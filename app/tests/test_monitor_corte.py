"""Testes do corte bruto da live (monitor/corte.py): transcrição, juízo da IA e envio para "Cortes prontos".

Sem ffmpeg, Groq ou rede: transcrição, IA e ffmpeg são funções falsas.
Rodar na pasta do projeto:  python -m unittest discover -s app/tests -v
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from monitor import corte, processo  # noqa: E402
from monitor.sinais import Momento, Sinal  # noqa: E402
from test_servidor import Base  # noqa: E402

FALA = [{"start": 1.0, "end": 4.0, "text": "meu deus eu não acredito nisso",
         "words": [{"word": "meu", "start": 1.0, "end": 1.4}, {"word": "deus", "start": 1.4, "end": 1.9}]},
        {"start": 5.0, "end": 9.0, "text": "olha o que acabou de acontecer aqui gente"}]
CANAL = {"plataforma": "twitch", "canal": "alanzoka", "nome": "Alan", "layout": "alan-zoka", "url": "https://www.twitch.tv/alanzoka"}


def momento(nota=8.0):
    return Momento(pico_t=100.0, primeiro_t=98.0, ultimo_t=101.0, nota=nota, t0=65.0, t1=110.0,
                   sinais=[Sinal(100.0, "audio", 6.0, "+9 dB"), Sinal(100.5, "chat", 5.0, "ritmo 4x")])


class BaseCorte(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        self.candidato = self.raiz / "momento.mp4"
        self.candidato.write_bytes(b"corte" * 500)
        self.cmds, self.logs, self.julgamentos = [], [], []

    def executar(self, cmd, timeout):
        self.cmds.append(cmd)
        if cmd[-1].endswith(".mp3"):
            Path(cmd[-1]).write_bytes(b"mp3" * 100)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="Input #0, mpegts\n  Duration: 00:00:44.50, start: 0.0, bitrate: 1000 kb/s")

    def produtor(self, chave="chave-falsa", transcrever=None, julgar=None, **kw):
        def julgar_padrao(texto, resumo, dur):
            self.julgamentos.append((texto, resumo, dur))
            return {"aprovar": True, "score": 8.5, "reason": "reação forte", "titulo_gancho": "NÃO ACREDITO NISSO", "pergunta_cta": "VOCÊ VIRIA?"}
        return corte.ProdutorCortes(self.raiz, CANAL, "ffmpeg-falso", chave=chave, executar=self.executar,
                                    transcrever=transcrever or (lambda audio: FALA), julgar=julgar or julgar_padrao,
                                    agora=lambda: datetime(2026, 11, 24, 21, 30, 5), registrar=lambda m, n="INFO": self.logs.append((n, m)), **kw)

    def meta(self, nome):
        return json.loads((self.raiz / "cortes_prontos" / nome).with_suffix(".json").read_text(encoding="utf-8"))


class TestProdutorCortes(BaseCorte):
    def test_aprovado_gera_bruto_copia_visivel_e_json_completo(self):
        r = self.produtor()(momento(), self.candidato)
        self.assertEqual((r["resultado"], r["fonte"], r["score"]), ("enviado", "ia", 8.5))
        nome = r["arquivo"]
        self.assertEqual(nome, "live_alanzoka_20261124_213005.mp4")
        saida = self.raiz / "cortes_prontos"
        self.assertEqual((saida / nome).read_bytes(), self.candidato.read_bytes())
        self.assertEqual((saida / "_brutos" / nome).read_bytes(), self.candidato.read_bytes())
        self.assertTrue(self.candidato.exists())                                     # o candidato continua na pasta da live
        m = self.meta(nome)
        self.assertEqual((m["titulo_gancho"], m["pergunta_cta"], m["nota"]), ("NÃO ACREDITO NISSO", "VOCÊ VIRIA?", 85))
        self.assertTrue(m["bruto"])
        self.assertEqual(m["edicao"], {"estado": "bruto"})
        self.assertEqual((m["modo"], m["fonte_titulo"], m["fonte_selecao"]), ("gameplay", "ia_texto_real", "ia"))
        self.assertEqual((m["clip_start"], m["clip_end"], m["clip_duration"]), (0.0, 44.5, 44.5))
        self.assertEqual(m["streamer"], "Alan")
        self.assertEqual(m["transcricao"], "meu deus eu não acredito nisso olha o que acabou de acontecer aqui gente")
        self.assertEqual(m["legenda_segmentos"][0]["words"][0], {"word": "meu", "start": 1.0, "end": 1.4})
        self.assertEqual(m["origem"], "live")
        self.assertEqual((m["live"]["canal"], m["live"]["layout_sugerido"], m["live"]["pico_t"]), ("alanzoka", "alan-zoka", 100.0))
        self.assertEqual(m["live"]["sinais"], {"audio": 1, "chat": 1})
        self.assertEqual(m["motivo_selecao"], "reação forte")
        self.assertFalse(list(saida.glob("*.tmp")) or list((saida / "_brutos").glob("*.tmp")))
        self.assertFalse(list((self.raiz / "temp_downloads" / "lives").glob("*.mp3")))       # áudio temporário apagado

    def test_a_ia_recebe_a_transcricao_e_os_sinais(self):
        self.produtor()(momento(), self.candidato)
        texto, resumo, dur = self.julgamentos[0]
        self.assertIn("meu deus", texto)
        self.assertEqual(resumo["sinais"], {"audio": 1, "chat": 1})
        self.assertEqual(dur, 44.5)
        prompt = corte.montar_prompt(texto, resumo, dur, "Alan")
        self.assertIn("meu deus", prompt)
        self.assertIn("NÃO são fatos", prompt)
        self.assertIn("Alan", prompt)

    def test_ia_reprova_descarta_e_nao_escreve_nada(self):
        r = self.produtor(julgar=lambda t, s, d: {"aprovar": False, "score": 3.0, "reason": "só enrolação",
                                                  "titulo_gancho": "", "pergunta_cta": ""})(momento(), self.candidato)
        self.assertEqual((r["resultado"], r["motivo"]), ("descartado", "só enrolação"))
        self.assertFalse((self.raiz / "cortes_prontos").exists())

    def test_nota_da_ia_abaixo_do_minimo_descarta_mesmo_com_aprovar_true(self):
        r = self.produtor(julgar=lambda t, s, d: {"aprovar": True, "score": 5.0, "reason": "meh",
                                                  "titulo_gancho": "X", "pergunta_cta": "Y?"})(momento(), self.candidato)
        self.assertEqual(r["resultado"], "descartado")

    def test_sem_titulo_da_ia_usa_o_gancho_padrao(self):
        r = self.produtor(julgar=lambda t, s, d: {"aprovar": True, "score": 7.0, "reason": "ok",
                                                  "titulo_gancho": "", "pergunta_cta": ""})(momento(), self.candidato)
        m = self.meta(r["arquivo"])
        self.assertEqual((m["titulo_gancho"], m["fonte_titulo"]), ("MOMENTO INCRÍVEL NA LIVE", "padrao"))

    def test_ia_fora_do_ar_cai_na_nota_dos_sinais(self):
        alto = self.produtor(julgar=lambda t, s, d: None)(momento(8.0), self.candidato)
        self.assertEqual((alto["resultado"], alto["fonte"]), ("enviado", "sinais"))
        baixo = self.produtor(julgar=lambda t, s, d: None)(momento(5.0), self.candidato)
        self.assertEqual((baixo["resultado"], baixo["fonte"]), ("descartado", "sinais"))
        def quebra(t, s, d):
            raise RuntimeError("rede")
        self.assertEqual(self.produtor(julgar=quebra)(momento(5.0), self.candidato)["resultado"], "descartado")
        self.assertTrue(any(n == "WARN" and "IA falhou" in m for n, m in self.logs))

    def test_sem_chave_nao_transcreve_nem_chama_a_ia(self):
        chamadas = []
        p = self.produtor(chave=None, transcrever=lambda a: chamadas.append(a) or FALA, julgar=lambda t, s, d: chamadas.append("ia"))
        r = p(momento(8.0), self.candidato)
        self.assertEqual((r["resultado"], r["fonte"]), ("enviado", "sinais"))
        self.assertEqual(chamadas, [])
        m = self.meta(r["arquivo"])
        self.assertEqual((m["transcricao"], m["legenda_segmentos"], m["fonte_titulo"]), ("", [], "padrao"))
        self.assertIn("sem chave", r["motivo"])
        self.assertEqual(self.produtor(chave=None)(momento(6.5), self.candidato)["resultado"], "descartado")

    def test_quase_sem_fala_usa_so_os_sinais(self):
        chamadas = []
        p = self.produtor(transcrever=lambda a: [{"start": 1, "end": 2, "text": "ah"}], julgar=lambda t, s, d: chamadas.append(1))
        self.assertEqual(p(momento(8.0), self.candidato)["fonte"], "sinais")
        self.assertEqual(chamadas, [])

    def test_falha_na_transcricao_nao_derruba(self):
        def quebra(audio):
            raise RuntimeError("whisper fora")
        r = self.produtor(transcrever=quebra)(momento(8.0), self.candidato)
        self.assertEqual((r["resultado"], r["fonte"]), ("enviado", "sinais"))

    def test_sem_arquivo_candidato(self):
        self.assertEqual(self.produtor()(momento(), None)["resultado"], "sem_corte")
        self.assertEqual(self.produtor()(momento(), self.raiz / "nao_existe.mp4")["resultado"], "sem_corte")

    def test_nomes_nao_se_sobrepoem(self):
        p = self.produtor()
        a, b = p(momento(), self.candidato)["arquivo"], p(momento(), self.candidato)["arquivo"]
        self.assertNotEqual(a, b)
        self.assertTrue(b.endswith("_2.mp4"))

    def test_nome_de_canal_youtube(self):
        p = corte.ProdutorCortes(self.raiz, {**CANAL, "plataforma": "youtube", "canal": "@Fulano.Tal"}, "ffmpeg-falso", chave=None,
                                 executar=self.executar, agora=lambda: datetime(2026, 1, 2, 3, 4, 5))
        self.assertEqual(p(momento(9.0), self.candidato)["arquivo"], "live_fulano-tal_20260102_030405.mp4")

    def test_segmentos_do_corte_descarta_lixo(self):
        s = corte.segmentos_do_corte([{"start": "1", "end": 2, "text": " oi ", "words": [{"word": "oi", "start": 1, "end": 2}, {"x": 1}]},
                                      {"start": 1, "end": 2, "text": "  "}, {"text": "sem tempo"}, "lixo", None])
        self.assertEqual(s, [{"start": 1.0, "end": 2.0, "text": "oi", "words": [{"word": "oi", "start": 1.0, "end": 2.0}]}])

    def test_duracao_do_video(self):
        self.assertEqual(corte.duracao_do_video("ff", "x.mp4", lambda c, t: SimpleNamespace(stderr="  Duration: 00:01:02.50,", stdout="")), 62.5)
        self.assertIsNone(corte.duracao_do_video("ff", "x.mp4", lambda c, t: SimpleNamespace(stderr="nada", stdout="")))

    def test_ler_chave_do_env(self):
        import os
        antiga = os.environ.pop("GROQ_API_KEY", None)
        self.addCleanup(lambda: os.environ.__setitem__("GROQ_API_KEY", antiga) if antiga else os.environ.pop("GROQ_API_KEY", None))
        self.assertIsNone(corte.ler_chave_groq(self.raiz))
        (self.raiz / ".env").write_text("# x\nGROQ_API_KEY=cole_sua_chave_aqui\n", encoding="utf-8")
        self.assertIsNone(corte.ler_chave_groq(self.raiz))
        (self.raiz / ".env").write_text('GROQ_API_KEY="gsk_abc"\n', encoding="utf-8")
        self.assertEqual(corte.ler_chave_groq(self.raiz), "gsk_abc")


class TestLigacaoComOGerarCortes(BaseCorte):
    """Usa o gerar_cortes.py de verdade; só a chamada de rede ao Groq é trocada."""

    def test_julgar_groq_le_a_resposta_da_ia(self):
        from unittest import mock
        import gerar_cortes as g
        resposta = '```json\n{"aprovar": true, "score": 8.2, "reason": "reação forte", "titulo_gancho": "\\"Meu deus!\\" que isso aqui foi", "pergunta_cta": "você viu isso agora mesmo?"}\n```'
        with mock.patch.object(g, "groq_chat_with_fallback", return_value=resposta) as chat:
            r = corte.julgar_groq(RAIZ, "k", "meu deus que isso", {"sinais": {"audio": 1}, "detalhes": ["+9 dB"]}, 44.0, "Alan")
        self.assertEqual((r["aprovar"], r["score"], r["reason"]), (True, 8.2, "reação forte"))
        self.assertEqual(r["titulo_gancho"], "MEU DEUS! QUE ISSO AQUI")                 # no máx. 5 palavras, caixa alta, sem aspas
        self.assertEqual(r["pergunta_cta"], "você viu isso agora?")
        self.assertIn("meu deus que isso", chat.call_args[0][0][0]["content"])

    def test_julgar_groq_resposta_ruim_devolve_none(self):
        from unittest import mock
        import gerar_cortes as g
        for ruim in (None, "", "não sei", '{"aprovar": true}', '{"score": "alto"}', "[1, 2]"):
            with mock.patch.object(g, "groq_chat_with_fallback", return_value=ruim):
                self.assertIsNone(corte.julgar_groq(RAIZ, "k", "texto", {}, 40.0, "Alan"), repr(ruim))

    def test_aprovar_ausente_vale_pela_nota(self):
        from unittest import mock
        import gerar_cortes as g
        with mock.patch.object(g, "groq_chat_with_fallback", return_value='{"score": 7.5, "reason": "ok"}'):
            self.assertTrue(corte.julgar_groq(RAIZ, "k", "texto", {}, 40.0, "Alan")["aprovar"])
        with mock.patch.object(g, "groq_chat_with_fallback", return_value='{"score": 4, "reason": "ok"}'):
            self.assertFalse(corte.julgar_groq(RAIZ, "k", "texto", {}, 40.0, "Alan")["aprovar"])

    def test_transcrever_groq_chama_o_whisper_do_projeto(self):
        from unittest import mock
        import gerar_cortes as g
        with mock.patch.object(g, "transcribe_audio_groq", return_value=FALA) as t:
            self.assertEqual(corte.transcrever_groq(RAIZ, self.raiz / "a.mp3", "k"), FALA)
        self.assertEqual(t.call_args[0][1], "k")


class TestNoMonitor(unittest.TestCase):
    """O MonitorCanal chama o produtor, conta e registra o resultado no momentos.jsonl e no status."""

    def test_resultado_vai_para_a_contagem_e_o_registro(self):
        from test_monitor_nucleo import BaseProcesso, volume

        class T(BaseProcesso):
            def runTest(self):
                resultados = iter([{"resultado": "enviado", "arquivo": "live_x.mp4", "score": 8.0, "fonte": "ia", "motivo": "boa"},
                                   {"resultado": "descartado", "score": 3.0, "fonte": "ia", "motivo": "fraca"}])
                m = self.monitor(volume, enviar_cortes=True, produtor=lambda mom, caminho: next(resultados))
                self.assertEqual(m.executar_ate_parar(), 0)
                st = self.status(m)
                self.assertEqual((st["cortes_enviados"], st["cortes_descartados"], st["cortes_erro"]), (1, 1, 0))
                self.assertEqual(st["ultimo_corte"]["resultado"], "descartado")
                ms = self.momentos(m)
                self.assertEqual([x["corte"]["resultado"] for x in ms], ["enviado", "descartado"])
                self.assertEqual(ms[0]["corte"]["arquivo"], "live_x.mp4")

        t = T()
        t.setUp()
        try:
            t.runTest()
        finally:
            t.tearDown()
            t.doCleanups()

    def test_produtor_que_quebra_nao_derruba_o_monitor(self):
        from test_monitor_nucleo import BaseProcesso, volume

        def quebra(mom, caminho):
            raise RuntimeError("boom")

        class T(BaseProcesso):
            def runTest(self):
                m = self.monitor(volume, enviar_cortes=True, produtor=quebra)
                self.assertEqual(m.executar_ate_parar(), 0)
                st = self.status(m)
                self.assertEqual((st["momentos"], st["cortes_erro"]), (2, 2))
                self.assertEqual(len(self.momentos(m)), 2)

        t = T()
        t.setUp()
        try:
            t.runTest()
        finally:
            t.tearDown()
            t.doCleanups()

    def test_de_ponta_a_ponta_o_corte_aparece_em_cortes_prontos(self):
        """Replay + produtor de verdade (IA e ffmpeg falsos): o corte entra na lista de Cortes prontos."""
        from test_monitor_nucleo import BaseProcesso, volume

        teste = self

        class T(BaseProcesso):
            def runTest(self):
                def exe(cmd, timeout):
                    if cmd[-1].endswith(".mp3"):
                        Path(cmd[-1]).write_bytes(b"mp3" * 100)
                        return SimpleNamespace(returncode=0, stdout="", stderr="")
                    if "-f" in cmd and "concat" in cmd:
                        Path(cmd[-1]).write_bytes(b"clip" * 1024)
                        return SimpleNamespace(returncode=0, stderr="")
                    if "ebur128=peak=true" in cmd:
                        return self.executar(cmd, timeout)
                    return SimpleNamespace(returncode=1, stdout="", stderr="Duration: 00:00:45.00, start")
                self.executar_original = self.executar
                info = {"plataforma": "twitch", "canal": "alanzoka", "nome": "Alan", "layout": "", "url": "u"}
                p = corte.ProdutorCortes(self.raiz, info, "ffmpeg-falso", chave="k", executar=exe, transcrever=lambda a: FALA,
                                         julgar=lambda t, s, d: {"aprovar": True, "score": 9.0, "reason": "ótimo",
                                                                 "titulo_gancho": "OLHA ISSO", "pergunta_cta": "E AGORA?"},
                                         pasta_temp=self.raiz / "t")
                m = processo.MonitorCanal(raiz=self.raiz, replay=self.video, ffmpeg="ffmpeg-falso", executar=exe,
                                          fabrica_ingestor=self.fabrica(volume, 60), enviar_cortes=True, produtor=p)
                self.assertEqual(m.executar_ate_parar(), 0)
                self.assertEqual(self.status(m)["cortes_enviados"], 2)
                mp4 = sorted((self.raiz / "cortes_prontos").glob("live_*.mp4"))
                teste.assertEqual(len(mp4), 2)
                teste.assertEqual(len(list((self.raiz / "cortes_prontos" / "_brutos").glob("live_*.mp4"))), 2)

        t = T()
        t.setUp()
        try:
            t.runTest()
        finally:
            t.tearDown()
            t.doCleanups()


class TestCorteNaListaDeCortesProntos(Base):
    """O corte da live é um corte bruto como qualquer outro: aparece na lista, tem vídeo e pode ser editado."""

    def test_aparece_na_lista_e_e_editavel(self):
        def exe(cmd, t):
            if cmd[-1].endswith(".mp3"):
                Path(cmd[-1]).write_bytes(b"mp3" * 100)
            return SimpleNamespace(returncode=0, stdout="", stderr="Duration: 00:00:45.00,")
        c = corte.ProdutorCortes(self.raiz, CANAL, "ffmpeg-falso", chave="k", executar=exe, transcrever=lambda a: FALA,
            julgar=lambda t, s, d: {"aprovar": True, "score": 8.0, "reason": "boa", "titulo_gancho": "UAU", "pergunta_cta": "VIU?"})
        cand = self.raiz / "momento.mp4"
        cand.write_bytes(b"corte" * 500)
        nome = c(momento(), cand)["arquivo"]
        _, d = self.json("GET", "/api/cortes")
        item = next(x for x in d["cortes"] if x["nome"] == nome)
        self.assertEqual((item["titulo"], item["nota"], item["modo"], item["bruto"], item["editavel"]), ("UAU", 80, "gameplay", True, True))
        self.assertEqual((item["origem"], item["canal"]), ("live", "Alan"))
        self.assertEqual(self.json("POST", "/api/cortes/editar", {"nome": nome, "opcoes": {"layout": "inteiro"}})[0], 201)


if __name__ == "__main__":
    unittest.main()
