"""Testes do núcleo do monitor de lives (pacote monitor/): detectores, agregador, anel, ingestão, chat e o processo.

Sem ffmpeg, yt-dlp ou rede: o ffmpeg é substituído por funções falsas (cross-platform, nada de executáveis falsos).
Rodar na pasta do projeto:  python -m unittest discover -s app/tests -v
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "app"))
from monitor import analise, anel, chat, ingestao, processo, sinais  # noqa: E402
from monitor.__main__ import main as main_cli  # noqa: E402
from monitor.anel import Anel, Segmento  # noqa: E402
from monitor.sinais import Agregador, DetectorAudio, DetectorChat, Sinal  # noqa: E402
import canais  # noqa: E402


# ----------------------------------------------------------------------------------------------- detectores
def aquecer_audio(det, n=15, base=-30.0):
    for i in range(n):
        assert det.processar(i * 4.0, base + (0.4 if i % 2 else -0.4)) is None


class TestDetectorAudio(unittest.TestCase):
    def test_pico_contra_o_normal_da_live(self):
        d = DetectorAudio("media")
        aquecer_audio(d)
        s = d.processar(80.0, -20.0)                                        # +10 dB sobre a mediana (-30)
        self.assertEqual(s.tipo, "audio")
        self.assertAlmostEqual(s.forca, 4.0 + (10.0 - 6.0) * 0.9, delta=0.7)          # ~+10 dB (a mediana oscila um pouco)
        s2 = d.processar(84.0, -20.0)                                        # sustentado: um pouco mais forte
        self.assertGreater(s2.forca, s.forca)
        self.assertIsNone(d.processar(88.0, -29.0))                          # voltou ao normal

    def test_nao_dispara_no_aquecimento_nem_no_silencio(self):
        d = DetectorAudio("media")
        for i in range(5):
            d.processar(i, -30.0)
        self.assertIsNone(d.processar(10.0, -10.0))                           # ainda aprendendo o que é normal
        d = DetectorAudio("media")
        aquecer_audio(d)
        self.assertIsNone(d.processar(90.0, -60.0))                           # silêncio
        self.assertIsNone(d.processar(94.0, None))                            # sem medição

    def test_o_normal_da_live_e_relativo(self):
        calma, alta = DetectorAudio("media"), DetectorAudio("media")
        aquecer_audio(calma, base=-40.0)
        aquecer_audio(alta, base=-20.0)
        self.assertIsNotNone(calma.processar(100.0, -32.0))                   # +8 dB sobre uma live baixinha: dispara
        self.assertIsNone(alta.processar(100.0, -32.0))                       # o mesmo volume, numa live alta, é abaixo do normal
        self.assertIsNotNone(alta.processar(104.0, -13.0))                    # +7 dB sobre o normal dela: dispara

    def test_sensibilidade(self):
        for nome, dispara in (("alta", True), ("media", False), ("baixa", False)):
            d = DetectorAudio(nome)
            aquecer_audio(d)
            self.assertEqual(d.processar(100.0, -25.0) is not None, dispara, nome)        # +5 dB
        d = DetectorAudio("baixa")
        aquecer_audio(d)
        self.assertIsNone(d.processar(100.0, -23.0))                           # +7 dB: abaixo dos 8 da baixa
        self.assertIsNotNone(d.processar(104.0, -21.0))                        # +9 dB


class TestDetectorChat(unittest.TestCase):
    def normal(self, d, ate=300):
        """1 mensagem a cada 10 s (6 msg/min), sem palavras de reação."""
        for t in range(0, ate, 10):
            self.assertEqual(d.mensagem(float(t), "oi galera"), [])

    def test_explosao_de_chat(self):
        d = DetectorChat("media")
        self.normal(d)
        achados = []
        for i in range(25):
            achados += d.mensagem(300.0 + i * 0.4, "chat vivo")
        chat_s = [s for s in achados if s.tipo == "chat"]
        self.assertEqual(len(chat_s), 1)
        self.assertGreaterEqual(chat_s[0].forca, 3.0)

    def test_sem_aquecimento_nao_dispara(self):
        d = DetectorChat("media")
        achados = []
        for i in range(60):
            achados += d.mensagem(10.0 + i * 0.2, "texto")
        self.assertEqual([s for s in achados if s.tipo == "chat"], [])

    def test_ritmo_baixo_nunca_e_explosao(self):
        d = DetectorChat("alta")
        self.normal(d)
        for i in range(4):                                                    # 3 da janela normal + 4 = 14 msg/min: abaixo do mínimo (15)
            self.assertEqual([s for s in d.mensagem(300.0 + i * 0.5, "texto") if s.tipo == "chat"], [])

    def test_palavras_e_risada(self):
        d = DetectorChat()
        self.assertEqual(d.mensagem(1.0, "MEU DEUS!!")[0].tipo, "palavra")
        self.assertIn("risada", d.mensagem(2.0, "kkkkkkkk")[0].detalhe)
        self.assertEqual(d.mensagem(3.0, "kkk"), [])                            # risada curta não conta
        self.assertEqual(d.mensagem(4.0, "pogue"), [])                          # "pog" dentro de outra palavra não conta
        self.assertEqual(d.mensagem(5.0, "isso foi um clipe"), [])
        self.assertEqual(d.mensagem(6.0, "CLIPA ISSO")[0].tipo, "palavra")

    def test_tick_sem_mensagens_nao_quebra(self):
        d = DetectorChat()
        self.assertEqual(d.tick(5.0), [])
        d.mensagem(1.0, "oi")
        self.assertEqual(d.tick(500.0), [])


# ----------------------------------------------------------------------------------------------- agregador
def audio(t, f=7.0):
    return Sinal(t, "audio", f, "teste")


class TestAgregador(unittest.TestCase):
    def test_sinal_forte_abre_e_fecha_depois_do_pos_roll(self):
        a = Agregador("media")
        a.adicionar(audio(100.0, 7.0))
        self.assertEqual(a.avancar(105.0), [])                                   # ainda esperando o desfecho
        m = a.avancar(110.0)
        self.assertEqual(len(m), 1)
        self.assertEqual((m[0].pico_t, m[0].t1, m[0].t0), (100.0, 110.0, 65.0))   # 35 s antes do pico, 10 s depois
        self.assertAlmostEqual(m[0].t1 - m[0].t0, 45.0)
        self.assertEqual(a.avancar(130.0), [])                                   # entrega uma vez só

    def test_sinal_fraco_nao_abre(self):
        a = Agregador("media")
        a.adicionar(Sinal(10.0, "palavra", 3.0, "x"))
        a.adicionar(audio(20.0, 3.5))                                              # 3.5 + 0.4*3 = 4.7: ainda abaixo de 5
        self.assertIsNone(a.aberto)
        self.assertEqual(a.avancar(100.0), [])

    def test_audio_mais_chat_somam(self):
        a = Agregador("media")
        a.adicionar(audio(100.0, 4.0))
        self.assertIsNone(a.aberto)                                               # 4.0 sozinho não chega em 5
        a.adicionar(Sinal(104.0, "chat", 3.0, "chat"))
        self.assertIsNotNone(a.aberto)
        self.assertAlmostEqual(a.aberto["nota"], 4.0 + 0.4 * 3.0 + 2.0, places=2)

    def test_sinais_proximos_viram_um_momento_e_o_pico_acompanha_o_mais_forte(self):
        a = Agregador("media")
        a.adicionar(audio(100.0, 6.0))
        a.adicionar(audio(104.0, 8.0))
        a.adicionar(audio(106.0, 5.5))
        self.assertEqual(a.avancar(112.0), [])                                    # o último sinal foi em 106: espera até 116
        m = a.avancar(116.0)
        self.assertEqual(len(m), 1)
        self.assertEqual((m[0].pico_t, m[0].primeiro_t, m[0].ultimo_t), (104.0, 100.0, 106.0))
        self.assertEqual(len(m[0].sinais), 3)

    def test_momento_perto_do_anterior_e_descartado(self):
        a = Agregador("media")
        a.adicionar(audio(100.0)); a.avancar(110.0)
        a.adicionar(audio(120.0))                                                  # 20 s do anterior (< 30)
        self.assertIsNone(a.aberto)
        self.assertEqual(a.descartes["muito_perto_do_anterior"], 1)
        a.adicionar(audio(140.0))                                                  # 40 s: pode
        self.assertIsNotNone(a.aberto)

    def test_limite_por_hora(self):
        a = Agregador("baixa")
        a.max_hora = 4                                                              # 4 por hora (o padrão é maior)
        for i in range(5):
            t = 100.0 + i * 600.0
            a.adicionar(audio(t, 9.0))
            a.avancar(t + 15.0)
        self.assertEqual(len(a.fechados), 4)
        self.assertEqual(a.descartes["limite_por_hora"], 1)
        a.adicionar(audio(100.0 + 5 * 600.0 + 700.0, 9.0))                           # passou a hora: libera de novo
        self.assertIsNotNone(a.aberto)

    def test_limites_por_hora_padrao(self):
        self.assertEqual({k: v["max_hora"] for k, v in sinais.SENSIBILIDADES.items()}, {"baixa": 12, "media": 20, "alta": 30})

    def test_momentos_fracos_nao_gastam_a_cota_por_hora(self):
        a = Agregador("media")
        a.max_hora = 3
        for i in range(10):                                                         # 10 momentos fracos (nota 5,5: abaixo de 7,6)
            t = 100.0 + i * 100.0
            a.adicionar(audio(t, 5.5))
            self.assertEqual(len(a.avancar(t + 15.0)), 1)
        self.assertEqual((len(a.fechados), len(a.contados), a.descartes["limite_por_hora"]), (10, 0, 0))
        for i in range(3):                                                          # 3 fortes cabem
            t = 2000.0 + i * 100.0
            a.adicionar(audio(t, 9.0))
            self.assertEqual(len(a.avancar(t + 15.0)), 1)
        a.adicionar(audio(2400.0, 9.0))                                              # o 4º forte na mesma hora: limite
        self.assertIsNone(a.aberto)
        self.assertEqual(a.descartes["limite_por_hora"], 1)

    def test_nota_exata_do_descarte_conta(self):
        a = Agregador("media")
        a.max_hora = 1
        a.adicionar(audio(100.0, 7.6)); a.avancar(115.0)
        self.assertEqual(len(a.contados), 1)
        a.adicionar(audio(300.0, 9.0))
        self.assertEqual(a.descartes["limite_por_hora"], 1)

    def test_momento_continuo_fecha_no_limite_de_duracao(self):
        a = Agregador("media")
        t = 100.0
        fechados = []
        while t < 400 and not fechados:
            a.adicionar(audio(t, 7.0))
            fechados += a.avancar(t)
            t += 5.0
        self.assertEqual(len(fechados), 1)
        self.assertLessEqual(fechados[0].ultimo_t - fechados[0].primeiro_t, 50.0)

    def test_encerrar_entrega_o_momento_aberto_com_janela_limitada(self):
        a = Agregador("media")
        a.adicionar(audio(100.0, 8.0))
        a.avancar(103.0)
        m = a.encerrar()
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].t1, 103.0)                                           # só existe vídeo até 103
        self.assertEqual(a.encerrar(), [])

    def test_nota_de(self):
        self.assertEqual(Agregador.nota_de([]), 0.0)
        self.assertEqual(Agregador.nota_de([Sinal(0, "audio", 9.5, ""), Sinal(0, "chat", 9.5, "")]), 10.0)         # nunca passa de 10
        self.assertEqual(Agregador.nota_de([audio(0, 6.0), audio(1, 3.0)]), 6.0)                                  # mesmo tipo: só o maior


# ----------------------------------------------------------------------------------------------- anel
class TestAnel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def seg(self, i, ini, fim):
        p = self.dir / f"seg_{i:06d}.ts"
        p.write_bytes(b"x" * 100)
        return Segmento(p, ini, fim)

    def montar(self, n=10, dur=4.0, inicio=1000.0):
        a = Anel(duracao_max=20.0)
        for i in range(n):
            a.registrar(self.seg(i, inicio + i * dur, inicio + (i + 1) * dur))
        return a

    def test_janela_e_cobertura(self):
        a = self.montar()
        self.assertEqual(len(a.janela(1005.0, 1013.0)), 3)                          # 1004-1008, 1008-1012, 1012-1016
        self.assertEqual(a.cobertura(1000.0, 1040.0), 1.0)
        self.assertAlmostEqual(a.cobertura(990.0, 1010.0), 0.5)                       # metade antes do começo
        self.assertEqual(a.cobertura(2000.0, 2010.0), 0.0)
        self.assertEqual(a.segundos(), 40.0)

    def test_limpar_apaga_so_o_velho_e_respeita_retidos(self):
        a = self.montar()
        reter = a.janela(1000.0, 1004.0)
        a.reter(reter, True)
        self.assertEqual(a.limpar(1040.0), 3)                                         # terminaram antes de 1020: 4, menos o retido
        self.assertTrue(reter[0].path.exists())                                       # o retido sobrevive
        a.reter(reter, False)
        self.assertEqual(a.limpar(1040.0), 1)
        self.assertFalse(reter[0].path.exists())

    def test_extrair_janela_monta_o_comando_por_horario(self):
        a = self.montar()
        chamados = []

        def executar(cmd, timeout):
            chamados.append(cmd)
            lista = Path(cmd[cmd.index("-i") + 1])
            self.assertTrue(lista.exists())
            linhas = lista.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(linhas), 3)
            self.assertTrue(all(l.startswith("file '") for l in linhas))
            Path(cmd[-1]).write_bytes(b"v" * 4096)
            return SimpleNamespace(returncode=0, stderr="")
        saida = self.dir / "saida" / "clip.mp4"
        ok, msg, cob = anel.extrair_janela("ffmpeg", a, 1005.0, 1013.0, saida, executar=executar)
        self.assertTrue(ok, msg)
        self.assertEqual(cob, 1.0)
        cmd = chamados[0]
        self.assertAlmostEqual(float(cmd[cmd.index("-ss") + 1]), 1.0, places=2)        # 1005 - início do 1º segmento (1004)
        self.assertAlmostEqual(float(cmd[cmd.index("-t") + 1]), 8.0, places=2)
        self.assertFalse((saida.parent / "clip_lista.txt").exists())                   # sem lixo
        self.assertEqual(a.retidos, {})                                                # soltou os segmentos

    def test_extrair_janela_falhas(self):
        a = self.montar()
        ok, msg, _ = anel.extrair_janela("ffmpeg", a, 5000.0, 5045.0, self.dir / "x.mp4", executar=lambda *x: None)
        self.assertFalse(ok)
        ok, msg, _ = anel.extrair_janela("ffmpeg", a, 1000.0, 1010.0, self.dir / "x.mp4",
                                         executar=lambda c, t: SimpleNamespace(returncode=1, stderr="boom"))
        self.assertFalse(ok)
        self.assertIn("boom", msg)
        ok, msg, _ = anel.extrair_janela("ffmpeg", a, 1000.0, 1010.0, self.dir / "x.mp4",
                                         executar=lambda c, t: SimpleNamespace(returncode=0, stderr=""))
        self.assertFalse(ok)                                                          # saiu vazio
        self.assertEqual(a.retidos, {})

        def estoura(c, t):
            raise subprocess.TimeoutExpired(c, t)
        self.assertFalse(anel.extrair_janela("ffmpeg", a, 1000.0, 1010.0, self.dir / "x.mp4", executar=estoura)[0])


# ----------------------------------------------------------------------------------------------- análise de áudio
EBUR = """
[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -23.4 LUFS
    Threshold: -33.5 LUFS

  Loudness range:
    LRA:         3.2 LU

  True peak:
    Peak:       -1.8 dBFS
"""


class TestAnaliseAudio(unittest.TestCase):
    def test_interpretar(self):
        self.assertEqual(analise.interpretar_ebur128(EBUR), (-23.4, -1.8))
        self.assertEqual(analise.interpretar_ebur128("nada"), (None, None))
        self.assertEqual(analise.interpretar_ebur128(None), (None, None))
        self.assertEqual(analise.interpretar_ebur128("  I:         -inf LUFS\n")[0], -70.0)

    def test_medir(self):
        r = analise.medir_audio("ffmpeg", "seg.ts", executar=lambda cmd, t: SimpleNamespace(stderr=EBUR, stdout=""))
        self.assertEqual(r, (-23.4, -1.8))

        def sem(cmd, t):
            raise OSError("nao achei")
        self.assertEqual(analise.medir_audio("ffmpeg", "seg.ts", executar=sem), (None, None))


# ----------------------------------------------------------------------------------------------- ingestão
class FakeProc:
    """Faz de conta de um ffmpeg que grava segmentos (e a lista.csv) numa pasta, com pausa curta entre eles."""

    def __init__(self, cmd, n=3, intervalo=0.01, dur=4.0, conteudo=b"LUFS=-30.0", travar=False, loudness=None):
        self.cmd = cmd
        self.pasta = Path(cmd[cmd.index("-segment_list") + 1]).parent
        self.n, self.intervalo, self.dur, self.conteudo, self.loudness = n, intervalo, dur, conteudo, loudness
        self.fim = threading.Event()
        self.terminado = False
        self.thread = threading.Thread(target=self._rodar, args=(travar,), daemon=True)
        self.thread.start()

    def _rodar(self, travar):
        if travar:
            while not self.terminado:
                time.sleep(0.02)
            return
        for i in range(self.n):
            if self.terminado:
                break
            time.sleep(self.intervalo)
            lufs = self.loudness(i) if self.loudness else None
            (self.pasta / f"seg_{i:06d}.ts").write_bytes(f"LUFS={lufs}".encode() if lufs is not None else self.conteudo)
            with open(self.pasta / "lista.csv", "a", encoding="utf-8") as f:
                f.write(f"seg_{i:06d}.ts,{i * self.dur:.6f},{(i + 1) * self.dur:.6f}\n")
        self.fim.set()

    def poll(self):
        return 0 if (self.fim.is_set() or self.terminado) else None

    def terminate(self):
        self.terminado = True

    kill = terminate

    def wait(self, timeout=None):
        self.thread.join(timeout)
        return 0


class TestIngestao(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_comando_de_gravacao(self):
        live = ingestao.comando_gravacao("ffmpeg", "https://x/a.m3u8", self.dir, 4, replay=False)
        self.assertIn("-reconnect", live)
        self.assertNotIn("-re", live)
        self.assertEqual(live[live.index("-segment_time") + 1], "4")
        self.assertEqual(live[live.index("-c") + 1], "copy")
        self.assertTrue(live[-1].endswith("seg_%06d.ts"))
        replay = ingestao.comando_gravacao("ffmpeg", "a.mp4", self.dir, 4, replay=True)
        self.assertIn("-re", replay)
        self.assertNotIn("-reconnect", replay)

    def test_ler_lista(self):
        arq = self.dir / "lista.csv"
        arq.write_text("seg_000000.ts,0.000000,4.000000\nlixo\nseg_000001.ts,4.0,8.0\nx,a,b\n", encoding="utf-8")
        self.assertEqual(ingestao.ler_lista(arq), [("seg_000000.ts", 0.0, 4.0), ("seg_000001.ts", 4.0, 8.0)])
        self.assertEqual(ingestao.ler_lista(self.dir / "nao_existe.csv"), [])

    def test_resolver_stream(self):
        def ok(cmd, t):
            self.assertIn("yt_dlp", cmd)
            self.assertEqual(cmd[-1], "https://www.twitch.tv/alanzoka")
            return SimpleNamespace(returncode=0, stdout="https://a/video.mp4\nhttps://b/live.m3u8\n", stderr="")
        self.assertEqual(ingestao.resolver_stream("https://www.twitch.tv/alanzoka", executar=ok), "https://b/live.m3u8")

        def offline(cmd, t):
            return SimpleNamespace(returncode=1, stdout="", stderr="ERROR: [twitch:stream] alanzoka is offline")
        with self.assertRaises(ingestao.LiveOffline):
            ingestao.resolver_stream("u", executar=offline)

        def offline_yt(cmd, t):
            return SimpleNamespace(returncode=1, stdout="", stderr="ERROR: The channel is not currently live")
        with self.assertRaises(ingestao.LiveOffline):
            ingestao.resolver_stream("u", executar=offline_yt)

        def falha(cmd, t):
            return SimpleNamespace(returncode=1, stdout="", stderr="ERROR: unable to download webpage: HTTP Error 403")
        with self.assertRaises(ingestao.FalhaStream):
            ingestao.resolver_stream("u", executar=falha)

        def vazio(cmd, t):
            return SimpleNamespace(returncode=0, stdout="\n", stderr="")
        with self.assertRaises(ingestao.FalhaStream):
            ingestao.resolver_stream("u", executar=vazio)

        def estoura(cmd, t):
            raise subprocess.TimeoutExpired(cmd, t)
        with self.assertRaises(ingestao.FalhaStream):
            ingestao.resolver_stream("u", executar=estoura)

    def rodar(self, lancar, entrada_fn=None, replay=True, esperar=lambda i, seg, est: False, limite=5.0, **kw):
        segs, estados = [], []
        ing = ingestao.Ingestor(self.dir, "ffmpeg", entrada_fn or (lambda: "arquivo.mp4"), segs.append,
                                lambda e, d: estados.append((e, d)), replay=replay, lancar=lancar, **kw)
        ing.start()
        fim = time.time() + limite
        while time.time() < fim and not esperar(ing, segs, estados):
            time.sleep(0.02)
        ing.parar()
        ing.join(5)
        return ing, segs, estados

    def test_entrega_segmentos_com_tempo_continuo_e_termina_no_replay(self):
        ing, segs, estados = self.rodar(lambda cmd: FakeProc(cmd, n=4), esperar=lambda i, s, e: i.estado_final)
        self.assertEqual(len(segs), 4)
        for a, b in zip(segs, segs[1:]):
            self.assertAlmostEqual(b.inicio, a.fim, places=3)                        # sem buracos nem sobreposição
            self.assertAlmostEqual(a.duracao, 4.0, places=3)
        self.assertTrue(ing.estado_final)
        self.assertEqual([e for e, _ in estados if e in ("gravando", "fim")], ["gravando", "fim"])
        self.assertIsNotNone(ing.ref)

    def test_reinicia_quando_o_stream_cai_e_o_tempo_continua(self):
        sessoes = []

        def lancar(cmd):
            sessoes.append(cmd)
            return FakeProc(cmd, n=2)
        ing, segs, estados = self.rodar(lancar, replay=False, esperar=lambda i, s, e: len(s) >= 6, esperas=(0.05,))
        self.assertGreaterEqual(len(sessoes), 3)
        self.assertGreaterEqual(ing.reinicios, 2)
        self.assertGreaterEqual(len(segs), 6)
        for a, b in zip(segs, segs[1:]):
            self.assertGreaterEqual(b.inicio, a.fim - 1e-6)                            # a linha do tempo só anda para a frente
        self.assertTrue(any(e == "erro" and "reconectando" in d for e, d in estados))
        self.assertEqual(len({s.path for s in segs}), len(segs))                       # sessões em pastas diferentes: sem colisão de nomes

    def test_live_offline_espera_e_tenta_de_novo(self):
        chamadas = []

        def entrada():
            chamadas.append(1)
            if len(chamadas) == 1:
                raise ingestao.LiveOffline("alanzoka is offline")
            return "https://x/a.m3u8"
        ing, segs, estados = self.rodar(lambda cmd: FakeProc(cmd, n=2), entrada_fn=entrada, replay=False,
                                        espera_offline=0.05, esperar=lambda i, s, e: len(s) >= 2)
        nomes = [e for e, _ in estados]
        self.assertLess(nomes.index("offline"), nomes.index("gravando"))
        self.assertEqual(len(segs), 2)

    def test_falha_do_yt_dlp_reconecta_com_espera(self):
        chamadas = []

        def entrada():
            chamadas.append(1)
            if len(chamadas) < 3:
                raise ingestao.FalhaStream("HTTP 403")
            return "https://x/a.m3u8"
        ing, segs, estados = self.rodar(lambda cmd: FakeProc(cmd, n=1), entrada_fn=entrada, replay=False, esperas=(0.03, 0.03),
                                        esperar=lambda i, s, e: len(s) >= 1)
        self.assertGreaterEqual(len(chamadas), 3)
        self.assertEqual(len([1 for e, d in estados if e == "erro" and "403" in d]), 2)

    def test_gravacao_travada_e_reiniciada(self):
        sessoes = []

        def lancar(cmd):
            sessoes.append(cmd)
            return FakeProc(cmd, n=2, travar=len(sessoes) == 1)
        ing, segs, estados = self.rodar(lancar, replay=False, limite_sem_segmento=0.3, esperas=(0.02,),
                                        esperar=lambda i, s, e: len(s) >= 2)
        self.assertGreaterEqual(len(sessoes), 2)
        self.assertTrue(any("sem segmentos novos" in d for _, d in estados))

    def test_ffmpeg_que_nao_abre(self):
        def lancar(cmd):
            raise OSError("sem ffmpeg")
        ing, segs, estados = self.rodar(lancar, replay=False, esperas=(0.02,), esperar=lambda i, s, e: len(e) >= 3)
        self.assertTrue(any("ffmpeg" in d for _, d in estados))
        self.assertEqual(segs, [])

    def test_erro_do_consumidor_nao_derruba_a_gravacao(self):
        recebidos = []

        def ao_segmento(seg):
            recebidos.append(seg)
            raise RuntimeError("falhou no consumidor")
        ing = ingestao.Ingestor(self.dir, "ffmpeg", lambda: "a.mp4", ao_segmento, lambda e, d: None, replay=True,
                                lancar=lambda cmd: FakeProc(cmd, n=3))
        ing.start()
        ing.join(5)
        self.assertEqual(len(recebidos), 3)


# ----------------------------------------------------------------------------------------------- chat
class TestChat(unittest.TestCase):
    def test_arquivo_toca_em_ordem_e_ancorado_na_referencia(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        arq = Path(tmp.name) / "chat.jsonl"
        arq.write_text('{"t": 0.1, "texto": "b"}\nlixo\n{"t": 0.0, "texto": "a"}\n\n{"t": "x"}\n', encoding="utf-8")
        self.assertEqual(chat.ChatArquivo.carregar(arq), [(0.0, "a"), (0.1, "b")])
        recebidos = []
        ref = time.time() + 0.05
        c = chat.ChatArquivo(arq, lambda: ref, lambda t, txt: recebidos.append((t, txt)))
        c.start()
        c.join(5)
        self.assertEqual([x[1] for x in recebidos], ["a", "b"])
        self.assertAlmostEqual(recebidos[0][0], ref, places=3)
        self.assertAlmostEqual(recebidos[1][0], ref + 0.1, places=3)

    def test_arquivo_espera_a_referencia_e_para_quando_pedido(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        arq = Path(tmp.name) / "chat.jsonl"
        arq.write_text('{"t": 0.0, "texto": "a"}\n', encoding="utf-8")
        c = chat.ChatArquivo(arq, lambda: None, lambda t, txt: None)
        c.start()
        time.sleep(0.3)
        self.assertTrue(c.is_alive())
        c.parar()
        c.join(3)
        self.assertFalse(c.is_alive())

    def test_chat_real_recebe_mensagens(self):
        recebidos = []
        c = chat.ChatReal("https://x", lambda t, txt: recebidos.append(txt), lambda m: None,
                          abrir=lambda url: [{"message": "oi"}, {"message": "kkkkk"}, None])
        c.start()
        time.sleep(0.3)
        c.parar()
        c.join(7)
        self.assertEqual(recebidos[:3], ["oi", "kkkkk", ""])

    def test_chat_real_sem_a_biblioteca_desliga_sem_barulho(self):
        avisos = []

        def abrir(url):
            raise ImportError("chat_downloader")
        c = chat.ChatReal("https://x", lambda t, txt: None, avisos.append, abrir=abrir)
        c.start()
        c.join(3)
        self.assertFalse(c.is_alive())
        self.assertIn("pip install chat-downloader", avisos[0])

    def test_chat_real_desiste_depois_de_falhas_seguidas(self):
        avisos, tentativas = [], []

        def abrir(url):
            tentativas.append(1)
            raise RuntimeError("fora do ar")
        c = chat.ChatReal("https://x", lambda t, txt: None, avisos.append, abrir=abrir, esperas=(0.01,), max_falhas=3)
        c.start()
        c.join(5)
        self.assertEqual(len(tentativas), 3)
        self.assertIn("desligado", avisos[-1])


# ----------------------------------------------------------------------------------------------- o processo (de ponta a ponta)
def lufs_do_arquivo(cmd):
    """Lê do conteúdo do segmento falso o volume que o 'ffmpeg' deve 'medir'."""
    alvo = Path(cmd[cmd.index("-i") + 1]).read_text(encoding="utf-8", errors="replace")
    return float(alvo.split("=")[1])


def ebur_texto(lufs):
    return f"  Integrated loudness:\n    I:         {lufs:.1f} LUFS\n  True peak:\n    Peak:       -2.0 dBFS\n"


class BaseProcesso(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.video = self.raiz / "replay.mp4"
        self.video.write_bytes(b"v" * 2048)
        self.extracoes = []

    def executar(self, cmd, timeout):
        """ffmpeg falso: mede o áudio (ebur128) ou monta o corte (concat)."""
        if "-f" in cmd and "concat" in cmd:
            self.extracoes.append(cmd)
            Path(cmd[-1]).write_bytes(b"clip" * 1024)
            return SimpleNamespace(returncode=0, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr=ebur_texto(lufs_do_arquivo(cmd)))

    def fabrica(self, loudness, n=60, intervalo=0.003):
        def criar(*a, **k):
            return ingestao.Ingestor(*a, **k, lancar=lambda cmd: FakeProc(cmd, n=n, intervalo=intervalo, loudness=loudness))
        return criar

    def monitor(self, loudness, **kw):
        return processo.MonitorCanal(raiz=self.raiz, replay=self.video, ffmpeg="ffmpeg-falso", executar=self.executar,
                                     fabrica_ingestor=self.fabrica(loudness, kw.pop("n", 60)), **kw)

    def status(self, m):
        return json.loads((m.pasta_estado / "status.json").read_text(encoding="utf-8"))

    def momentos(self, m):
        arq = m.pasta_estado / "momentos.jsonl"
        return [json.loads(l) for l in arq.read_text(encoding="utf-8").splitlines()] if arq.exists() else []


def volume(i):
    """-30 LUFS de normal (com uma oscilaçãozinha), picos nos segmentos 30-31 e 50."""
    if i in (30, 31):
        return -18.0
    if i == 50:
        return -17.0
    return -30.0 + (0.4 if i % 2 else -0.4)


class TestProcessoReplay(BaseProcesso):
    def test_detecta_os_picos_e_extrai_os_cortes_candidatos(self):
        recebidos = []
        m = self.monitor(volume, ao_momento=lambda mom, caminho: recebidos.append((mom, caminho)))
        self.assertEqual(m.executar_ate_parar(), 0)
        ms = self.momentos(m)
        self.assertEqual(len(ms), 2)
        self.assertTrue(all(abs(x["t1"] - x["t0"] - 45.0) < 1.0 for x in ms))             # janelas de 45 s
        self.assertGreater(ms[1]["pico_t"] - ms[0]["pico_t"], 60.0)
        self.assertEqual(ms[0]["sinais"].get("audio"), 2)                                  # o pico sustentado (2 segmentos)
        self.assertEqual(len(self.extracoes), 2)
        self.assertEqual(len(recebidos), 2)
        self.assertTrue(all(c is not None and c.exists() for _, c in recebidos))
        st = self.status(m)
        self.assertEqual((st["estado"], st["segmentos"], st["momentos"], st["momentos_salvos"]), ("parado", 60, 2, 2))
        self.assertEqual(st["sinais_audio"], 3)
        self.assertEqual(st["replay"], str(self.video))
        self.assertFalse(list(m.pasta_anel.glob("s*/*")))                                  # o anel é limpo ao sair

    def test_janela_ancorada_no_horario_do_pico(self):
        m = self.monitor(volume)
        m.executar_ate_parar()
        ms = self.momentos(m)
        # o pico do 1º momento é o fim do segmento 30 (índice) e a janela termina 10 s depois dele
        self.assertAlmostEqual(ms[0]["t1"] - ms[0]["pico_t"], 10.0, places=1)
        # o corte pedido ao ffmpeg corresponde à janela (e cabe no que foi gravado)
        self.assertGreaterEqual(ms[0]["cobertura"], 0.99)

    def test_sem_picos_nao_gera_nada(self):
        m = self.monitor(lambda i: -30.0 + (0.4 if i % 2 else -0.4))
        self.assertEqual(m.executar_ate_parar(), 0)
        self.assertEqual(self.momentos(m), [])
        self.assertEqual(self.extracoes, [])
        self.assertEqual(self.status(m)["momentos"], 0)

    def test_so_detectar_sem_extrair(self):
        m = self.monitor(volume, salvar_momentos=False)
        m.executar_ate_parar()
        self.assertEqual(len(self.momentos(m)), 2)
        self.assertEqual(self.extracoes, [])
        self.assertIsNone(self.momentos(m)[0]["arquivo"])

    def test_momento_no_fim_da_gravacao_ainda_e_entregue(self):
        m = self.monitor(lambda i: -18.0 if i == 40 else -30.0 + (0.4 if i % 2 else -0.4), n=42)    # o replay acaba logo depois do pico
        m.executar_ate_parar()
        ms = self.momentos(m)
        self.assertEqual(len(ms), 1)
        self.assertLess(ms[0]["t1"] - ms[0]["pico_t"], 10.0)

    def test_pausado_aprende_mas_nao_gera_momentos(self):
        m = self.monitor(volume)
        m.pasta_estado.mkdir(parents=True, exist_ok=True)
        (m.pasta_estado / "pausar.flag").write_text("1")
        m.executar_ate_parar()
        self.assertEqual(self.momentos(m), [])
        self.assertTrue(self.status(m)["pausado"])
        self.assertEqual(self.status(m)["segmentos"], 60)                                    # continuou gravando

    def test_pedido_de_parada(self):
        m = self.monitor(volume, n=100000)

        def parar_depois():
            time.sleep(0.5)
            (m.pasta_estado / "parar.flag").write_text("1")
        threading.Thread(target=parar_depois, daemon=True).start()
        t0 = time.time()
        self.assertEqual(m.executar_ate_parar(), 0)
        self.assertLess(time.time() - t0, 15.0)
        self.assertEqual(self.status(m)["estado"], "parado")

    def test_sem_ffmpeg(self):
        m = processo.MonitorCanal(raiz=self.raiz, replay=self.video, ffmpeg=None, fabrica_ingestor=self.fabrica(volume))
        with mock.patch.object(processo, "achar_ffmpeg", lambda *_: None):
            m.ffmpeg = None
            self.assertEqual(m.executar_ate_parar(), 4)
        self.assertEqual(self.status(m)["estado"], "erro")

    def test_erro_ao_analisar_nao_derruba(self):
        m = self.monitor(volume)
        original = self.executar
        chamadas = []

        def executar(cmd, t):
            chamadas.append(1)
            if len(chamadas) == 3:
                raise RuntimeError("estourou")
            return original(cmd, t)
        m.executar = executar
        self.assertEqual(m.executar_ate_parar(), 0)
        self.assertEqual(self.status(m)["segmentos"], 60)


def registrar(raiz, canal="https://www.twitch.tv/alanzoka"):
    reg = canais.RegistroCanais(Path(raiz) / "canais_permitidos.json")
    reg.salvar({"canal": canal, "pode_cortar": True, "pode_postar": True})
    return reg


class TestProcessoCanal(BaseProcesso):
    def canal(self, url="https://www.twitch.tv/alanzoka", **kw):
        return processo.MonitorCanal(raiz=self.raiz, url=url, ffmpeg="ffmpeg-falso", executar=self.executar,
                                     fabrica_ingestor=self.fabrica(volume, 10), chat_real=False, **kw)

    def parar_depois(self, m, segundos):
        def parar():
            time.sleep(segundos)
            m.pasta_estado.mkdir(parents=True, exist_ok=True)
            (m.pasta_estado / "parar.flag").write_text("1")
        threading.Thread(target=parar, daemon=True).start()

    def test_canal_fora_da_lista_e_bloqueado(self):
        m = self.canal()
        self.assertEqual(m.executar_ate_parar(), 3)
        self.assertEqual(self.status(m)["estado"], "bloqueado")
        self.assertIn("não está na lista", self.status(m)["erro"])
        self.assertEqual(m.ingestor, None)                                                  # nem chegou a gravar

    def test_canal_suspenso_e_bloqueado_e_ativo_passa(self):
        reg = registrar(self.raiz)
        reg.definir_ativo("twitch:alanzoka", False)
        self.assertEqual(self.canal().executar_ate_parar(), 3)
        reg.definir_ativo("twitch:alanzoka", True)
        m = self.canal()
        self.parar_depois(m, 1.0)
        self.assertEqual(m.executar_ate_parar(), 0)
        self.assertEqual(self.status(m)["canal"], "alanzoka")
        self.assertEqual(m.id, "twitch-alanzoka")
        self.assertEqual(m.url_live, "https://www.twitch.tv/alanzoka")

    def test_permissao_removida_no_meio_para_o_monitor(self):
        reg = registrar(self.raiz)
        m = processo.MonitorCanal(raiz=self.raiz, url="https://www.twitch.tv/alanzoka", ffmpeg="ffmpeg-falso", executar=self.executar,
                                  fabrica_ingestor=self.fabrica(volume, 100000), chat_real=False)

        def suspender():
            time.sleep(0.6)
            reg.definir_ativo("twitch:alanzoka", False)
        threading.Thread(target=suspender, daemon=True).start()
        with mock.patch.object(processo, "CHECAGEM_PERMISSAO_S", 0.2):
            self.assertEqual(m.executar_ate_parar(), 3)
        self.assertEqual(self.status(m)["estado"], "bloqueado")
        self.assertIn("suspenso", self.status(m)["erro"])

    def test_link_invalido_nem_cria_o_monitor(self):
        for url in ("https://kick.com/x", "https://www.youtube.com/watch?v=abc", "lixo"):
            with self.assertRaises(ValueError, msg=url):
                processo.MonitorCanal(raiz=self.raiz, url=url)
        with self.assertRaises(ValueError):
            processo.MonitorCanal(raiz=self.raiz)                                          # sem url nem replay
        with self.assertRaises(ValueError):
            processo.MonitorCanal(raiz=self.raiz, replay=self.video, sensibilidade="maxima")

    def test_chat_arquivo_so_no_replay_e_cli(self):
        with self.assertRaises(SystemExit):
            main_cli(["--url", "https://www.twitch.tv/alanzoka", "--chat-arquivo", "x.jsonl", "--raiz", str(self.raiz)])
        with self.assertRaises(SystemExit):
            main_cli(["--replay", str(self.raiz / "nao_existe.mp4"), "--raiz", str(self.raiz)])
        with self.assertRaises(SystemExit):
            main_cli(["--url", "https://kick.com/x", "--raiz", str(self.raiz)])
        with self.assertRaises(SystemExit):
            main_cli(["--raiz", str(self.raiz)])

    def test_slug_e_ids(self):
        self.assertEqual(processo.slug("@Gaules!"), "gaules")
        self.assertEqual(processo.slug("!!!"), "live")
        m = processo.MonitorCanal(raiz=self.raiz, url="https://www.youtube.com/@Gaules")
        self.assertEqual(m.id, "youtube-gaules")
        self.assertEqual(m.url_live, "https://www.youtube.com/@gaules/live")
        self.assertEqual(processo.MonitorCanal(raiz=self.raiz, replay=self.video).id, "replay-replay")


if __name__ == "__main__":
    unittest.main()
