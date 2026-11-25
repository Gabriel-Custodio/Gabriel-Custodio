"""Testes da edição do vídeo longo (opções, capítulos, legenda ASS, progresso do ffmpeg e render_edit_longo), sem ffmpeg real.

Rodar na pasta do projeto:   python -m unittest discover -s app/tests -v
"""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
import gerar_cortes as g  # noqa: E402
import longos  # noqa: E402
from longos import edicao, legenda  # noqa: E402

MOMENTOS = [
    {"n": 1, "inicio_video": 100.0, "fim_video": 140.0, "inicio": 0.0, "fim": 40.0, "duracao": 40.0, "nota": 9.0,
     "motivo": "susto", "titulo": "SUSTO NO BAU"},
    {"n": 2, "inicio_video": 500.0, "fim_video": 540.0, "inicio": 40.0, "fim": 80.0, "duracao": 40.0, "nota": 8.0,
     "motivo": "risada", "titulo": "RISADA GERAL"},
    {"n": 3, "inicio_video": 900.0, "fim_video": 940.0, "inicio": 80.0, "fim": 120.0, "duracao": 40.0, "nota": 7.0,
     "motivo": "fim", "titulo": "FINAL INESPERADO"},
]


def trechos_ok(valor):
    """Cópia simples do normalize_trechos (devolve (lista|None, ok))."""
    return g.normalize_trechos(valor)


class TestOpcoes(unittest.TestCase):
    def test_padrao_tudo_ligado_e_sem_trechos(self):
        self.assertEqual(longos.normalizar_opcoes({}, trechos_ok),
                         {"legenda": True, "resumo": True, "cta": True, "capitulos": True, "espelhar": False})
        self.assertIs(longos.normalizar_opcoes({"espelhar": True}, trechos_ok)["espelhar"], True)
        self.assertIsNone(longos.normalizar_opcoes({"espelhar": 1}, trechos_ok))

    def test_recorte_validado(self):
        caixa = {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4}
        self.assertEqual(longos.normalizar_opcoes({"recorte": caixa}, trechos_ok)["recorte"], caixa)
        self.assertNotIn("recorte", longos.normalizar_opcoes({}, trechos_ok))
        for ruim in ({}, [], "x", {"x": 0.8, "y": 0, "w": 0.5, "h": 0.5}, {"x": 0, "y": 0, "w": 0.01, "h": 0.5},
                     {"x": 0, "y": 0, "w": 0.5, "h": 1.5}, {"x": "a", "y": 0, "w": 0.5, "h": 0.5},
                     {"x": 0, "y": 0, "w": float("nan"), "h": 0.5}):
            self.assertIsNone(longos.normalizar_opcoes({"recorte": ruim}, trechos_ok), ruim)

    def test_precisa_encaixe_em_16_9(self):
        self.assertFalse(longos.precisa_encaixe(1920, 1080))
        self.assertFalse(longos.precisa_encaixe(960, 545))               # quase 16:9
        self.assertTrue(longos.precisa_encaixe(800, 800))                # quadrado
        self.assertTrue(longos.precisa_encaixe(1920, 500))               # faixa larga
        self.assertFalse(longos.precisa_encaixe(0, 10))

    def test_comando_final_com_recorte_16_9_so_preenche(self):
        caixa = {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.5}
        cmd = longos.comando_final("ffmpeg", "in.mp4", "t.ass", 60.0, "out.mp4", recorte=caixa, origem=(1920, 1080))
        grafo = cmd[cmd.index("-filter_complex") + 1]
        self.assertTrue(grafo.startswith("[0:v]crop=trunc(iw*0.50000/2)*2:trunc(ih*0.50000/2)*2:trunc(iw*0.10000/2)*2:trunc(ih*0.20000/2)*2,scale=1920:1080:force_original_aspect_ratio=increase"))
        self.assertNotIn("gblur", grafo)
        self.assertLess(grafo.index("crop="), grafo.index("subtitles="))   # a legenda vem depois do recorte
        self.assertTrue(grafo.endswith("[vout]"))
        self.assertEqual(cmd[cmd.index("-map") + 1], "[vout]")
        self.assertIn("0:a?", cmd)
        self.assertNotIn("-vf", cmd)

    def test_comando_final_com_recorte_de_outro_formato_fica_inteiro_sobre_fundo_desfocado(self):
        caixa = {"x": 0.7, "y": 0.6, "w": 0.28, "h": 0.38}               # 538x410: bem diferente de 16:9
        cmd = longos.comando_final("ffmpeg", "in.mp4", None, 60.0, "out.mp4", recorte=caixa, origem=(1920, 1080), espelhar=True)
        grafo = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn(",hflip,split=2[a][b]", grafo)
        self.assertIn("gblur=sigma=30[fundo]", grafo)
        self.assertIn("force_original_aspect_ratio=decrease", grafo)
        self.assertIn("[fundo][frente]overlay=(W-w)/2:(H-h)/2,setsar=1", grafo)
        self.assertLess(grafo.index("hflip"), grafo.index("fade="))

    def test_comando_final_sem_recorte_continua_igual(self):
        cmd = longos.comando_final("ffmpeg", "in.mp4", "t.ass", 60.0, "out.mp4")
        self.assertIn("-vf", cmd)
        self.assertNotIn("-filter_complex", cmd)

    def test_comando_final_espelha_antes_da_legenda(self):
        normal = longos.comando_final("ffmpeg", "in.mp4", "t.ass", 60.0, "out.mp4")
        espelhado = longos.comando_final("ffmpeg", "in.mp4", "t.ass", 60.0, "out.mp4", espelhar=True)
        self.assertNotIn("hflip", normal[normal.index("-vf") + 1])
        filtro = espelhado[espelhado.index("-vf") + 1]
        self.assertTrue(filtro.startswith("hflip,subtitles="))
        sem_legenda = longos.comando_final("ffmpeg", "in.mp4", None, 1.0, "out.mp4", espelhar=True)
        self.assertEqual(sem_legenda[sem_legenda.index("-vf") + 1], "hflip")

    def test_valores_validos(self):
        o = longos.normalizar_opcoes({"legenda": False, "resumo": False, "cta": True, "capitulos": False,
                                      "trechos": [[0, 30], [40, 100]], "titulo": "  Meu   vídeo\n incrível "}, trechos_ok)
        self.assertEqual(o["trechos"], [[0.0, 30.0], [40.0, 100.0]])
        self.assertEqual(o["titulo"], "Meu vídeo incrível")
        self.assertFalse(o["legenda"] or o["resumo"] or o["capitulos"])

    def test_invalidos(self):
        for ruim in (None, "x", [], {"legenda": "sim"}, {"cta": 1}, {"trechos": [[5, 1]]}, {"trechos": "x"},
                     {"titulo": 5}, {"trechos": [[0, 0.1]]}):
            self.assertIsNone(longos.normalizar_opcoes(ruim, trechos_ok), ruim)

    def test_titulo_vazio_some_e_o_longo_e_cortado(self):
        self.assertNotIn("titulo", longos.normalizar_opcoes({"titulo": "   "}, trechos_ok))
        self.assertEqual(len(longos.normalizar_opcoes({"titulo": "a" * 300}, trechos_ok)["titulo"]), longos.TITULO_MAX)
        self.assertNotIn("\x00", longos.limpar_titulo("a\x00b"))


class TestTempoECapitulos(unittest.TestCase):
    def test_mapear_tempo(self):
        self.assertEqual(longos.mapear_tempo(50, None), 50.0)
        trechos = [[10, 30], [50, 80]]
        self.assertEqual(longos.mapear_tempo(10, trechos), 0.0)
        self.assertEqual(longos.mapear_tempo(25, trechos), 15.0)
        self.assertEqual(longos.mapear_tempo(50, trechos), 20.0)
        self.assertEqual(longos.mapear_tempo(70, trechos), 40.0)
        self.assertIsNone(longos.mapear_tempo(40, trechos))                        # estava no meio do trecho cortado
        self.assertIsNone(longos.mapear_tempo(5, trechos))
        self.assertEqual(longos.duracao_dos_trechos(trechos, 999), 50.0)
        self.assertEqual(longos.duracao_dos_trechos(None, 120), 120.0)

    def test_capitulos_sem_cortes(self):
        caps = longos.capitulos(MOMENTOS, None, 120.0)
        self.assertEqual([(c["n"], c["inicio"], c["titulo"]) for c in caps],
                         [(1, 0.0, "SUSTO NO BAU"), (2, 40.0, "RISADA GERAL"), (3, 80.0, "FINAL INESPERADO")])
        self.assertTrue(all(abs((c["fim"] - c["inicio"]) - longos.CAPITULO_DURACAO_S) < 1e-6 for c in caps))

    def test_capitulos_com_cortes(self):
        # mantém só [0, 40] e [60, 120]: o momento 2 (40-80) começa no corte, mas a parte 60-80 sobrevive
        caps = longos.capitulos(MOMENTOS, [[0, 40], [60, 120]], 100.0)
        self.assertEqual([(c["n"], c["inicio"]) for c in caps], [(1, 0.0), (2, 40.0), (3, 60.0)])
        # tudo do momento 2 cortado: não vira capítulo
        caps = longos.capitulos(MOMENTOS, [[0, 40], [80, 120]], 80.0)
        self.assertEqual([c["n"] for c in caps], [1, 3])
        self.assertEqual(caps[1]["inicio"], 40.0)

    def test_capitulo_nao_invade_o_proximo_nem_passa_do_fim(self):
        momentos = [dict(MOMENTOS[0], fim=40.0), dict(MOMENTOS[1], inicio=2.0, n=2)]
        caps = longos.capitulos(momentos, None, 100.0)
        self.assertTrue(all(c["fim"] <= 100.0 for c in caps))
        for a, b in zip(caps, caps[1:]):
            self.assertLessEqual(a["fim"], b["inicio"])

    def test_capitulos_com_dados_ruins_e_sem_titulo(self):
        ruins = [{}, {"inicio": "x", "fim": 5}, {"inicio": 10, "fim": 5}, {"inicio": float("nan"), "fim": 5}]
        self.assertEqual(longos.capitulos(ruins, None, 100.0), [])
        caps = longos.capitulos([{"n": 4, "inicio": 0, "fim": 30, "titulo": ""}], None, 100.0)
        self.assertEqual(caps[0]["titulo"], "Momento 4")
        self.assertEqual(longos.capitulos(None, None, 100.0), [])

    def test_texto_para_resumo_so_tem_titulos_e_falas_reais(self):
        caps = longos.capitulos(MOMENTOS, None, 120.0)
        segs = [{"start": 1, "end": 5, "text": "primeira fala"}, {"start": 45, "end": 50, "text": "segunda fala"},
                {"start": 85, "end": 90, "text": "terceira fala"}]
        texto = longos.texto_para_resumo(caps, segs, 120.0)
        linhas = texto.splitlines()
        self.assertEqual(len(linhas), 3)
        self.assertEqual(linhas[0], "Momento 1 (SUSTO NO BAU): primeira fala")
        self.assertIn("segunda fala", linhas[1])
        self.assertNotIn("primeira", linhas[1])
        self.assertEqual(longos.texto_para_resumo([], segs, 120.0), "primeira fala segunda fala terceira fala")
        self.assertLessEqual(len(longos.texto_para_resumo(caps * 30, segs * 50, 120.0)), 2400)


class TestComandos(unittest.TestCase):
    def test_aparar(self):
        cmd = longos.comando_aparar("ffmpeg", "bruto.mp4", [[0, 10], [20.5, 30]], "saida.mp4")
        grafo = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[0:v]trim=start=0.000:end=10.000", grafo)
        self.assertIn("atrim=start=20.500:end=30.000", grafo)
        self.assertIn("concat=n=2:v=1:a=1[v][a]", grafo)
        self.assertEqual(cmd[-1], "saida.mp4")
        self.assertNotEqual(cmd[cmd.index("-crf") + 1], str(g.INTERMEDIATE_CRF))          # nada de CRF 12 num vídeo de 12 min

    def test_final_com_e_sem_legenda(self):
        cmd = longos.comando_final("ffmpeg", "fonte.mp4", Path("temp/x y.ass"), 600.0, "saida.mp4")
        vf = cmd[cmd.index("-vf") + 1]
        self.assertTrue(vf.startswith("subtitles='temp/x y.ass'"))
        self.assertIn("fade=t=out:st=599.50", vf)
        af = cmd[cmd.index("-af") + 1]
        self.assertIn("loudnorm", af)
        self.assertIn("afade=t=out:st=599.50", af)
        self.assertEqual(cmd[cmd.index("-preset") + 1], longos.edicao.PRESET_FINAL)
        sem = longos.comando_final("ffmpeg", "fonte.mp4", None, 600.0, "saida.mp4")
        self.assertNotIn("subtitles", sem[sem.index("-vf") + 1])
        curto = longos.comando_final("ffmpeg", "fonte.mp4", None, 1.5, "saida.mp4")
        self.assertNotIn("-vf", curto)                                                        # sem nada a aplicar no vídeo
        self.assertNotIn("afade", curto[curto.index("-af") + 1])

    def test_caminho_de_legenda_no_windows(self):
        cmd = longos.comando_final("ffmpeg", "f.mp4", "C:\\proj\\temp_downloads\\longo.ass", 100.0, "s.mp4")
        self.assertIn("subtitles='C\\:", cmd[cmd.index("-vf") + 1])


class TestLegenda(unittest.TestCase):
    def test_tempo_ass(self):
        self.assertEqual(legenda.tempo_ass(0), "0:00:00.00")
        self.assertEqual(legenda.tempo_ass(3725.5), "1:02:05.50")
        self.assertEqual(legenda.tempo_ass(-4), "0:00:00.00")
        self.assertEqual(legenda.tempo_ass(59.999), "0:00:59.99")

    def test_blocos_de_palavras_com_tempo(self):
        seg = {"start": 0.0, "end": 4.0, "text": "a b c d e f",
               "words": [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate("a b c d e f".split())]}
        itens = legenda.blocos([seg], 100.0)
        self.assertEqual([b["words"] for b in itens], [["A", "B", "C", "D"], ["E", "F"]])
        self.assertEqual((itens[0]["start"], itens[0]["end"]), (0.0, 1.9))

    def test_blocos_sem_palavras_dividem_o_tempo_e_censuram(self):
        itens = legenda.blocos([{"start": 10.0, "end": 14.0, "text": "um dois tres quatro cinco seis seis sete"}], 100.0,
                               censurar=lambda p: "***" if p == "dois" else p)
        self.assertEqual(itens[0]["words"], ["UM", "***", "TRES", "QUATRO"])
        self.assertAlmostEqual(itens[0]["start"], 10.0)
        self.assertEqual(len(itens), 2)

    def test_blocos_respeitam_os_limites(self):
        segs = [{"start": 0.0, "end": 20.0, "text": "a b c d", "words": [
            {"word": "a", "start": 0.0, "end": 0.4}, {"word": "b", "start": 0.4, "end": 0.8},
            {"word": "c", "start": 0.8, "end": 1.2}, {"word": "d", "start": 1.2, "end": 9.0}]},
            {"start": 200.0, "end": 210.0, "text": "fora"}]
        itens = legenda.blocos(segs, 100.0)
        self.assertEqual(len(itens), 1)
        self.assertLessEqual(itens[0]["end"] - itens[0]["start"], legenda.DURACAO_MAX_BLOCO_S)
        self.assertEqual(legenda.blocos([{"start": 5, "end": 5, "text": "x"}, {"start": 1, "end": 2, "text": "  "}], 10), [])

    def test_ass_completo(self):
        segs = [{"start": 1.0, "end": 3.0, "text": "olá mundo {x}\\n"}]
        caps = [{"n": 2, "inicio": 0.0, "fim": 4.5, "titulo": "SUSTO {NO} BAU"}]
        texto = legenda.montar_ass(segs, 10.0, caps)
        self.assertIn("PlayResX: 1920", texto)
        self.assertIn("PlayResY: 1080", texto)
        self.assertIn("Style: Legenda,Impact,64", texto)
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:03.00,Legenda", texto)
        self.assertIn("Dialogue: 1,0:00:00.00,0:00:04.50,Capitulo,,0,0,0,,{\\fad(250,250)}2 · SUSTO NO BAU", texto)
        self.assertNotIn("{x}", texto)
        self.assertEqual(texto.count("Dialogue:"), 2)

    def test_sem_nada_para_mostrar(self):
        self.assertIsNone(legenda.montar_ass([], 10.0, []))
        self.assertIsNone(legenda.montar_ass(None, 10.0, None))
        self.assertIn("Capitulo", legenda.montar_ass([], 10.0, [{"n": 1, "inicio": 0, "fim": 3, "titulo": "X"}]))


SCRIPT_FFMPEG = textwrap.dedent('''\
    #!/usr/bin/env python3
    import sys, time
    args = sys.argv[1:]
    modo = open(sys.argv[0] + ".modo").read().strip()
    assert "-progress" in args and "pipe:1" in args
    if modo == "ok":
        for us in (1000000, 5000000, 10000000):
            print("out_time_us=%d" % us, flush=True)
            print("progress=continue", flush=True)
        print("out_time_us=N/A", flush=True)
        open(args[-1], "wb").write(b"x" * 2048)
        print("progress=end", flush=True)
    elif modo == "erro":
        sys.stderr.write("linha de erro do ffmpeg\\n")
        sys.exit(3)
    elif modo == "trava":
        print("out_time_us=1000000", flush=True)
        time.sleep(30)
''')


class TestProgresso(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.ffmpeg = self.dir / "ffmpeg_falso"
        self.ffmpeg.write_text(SCRIPT_FFMPEG, encoding="utf-8")
        self.ffmpeg.chmod(self.ffmpeg.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self._tmp.cleanup()

    def rodar(self, modo, **kw):
        Path(str(self.ffmpeg) + ".modo").write_text(modo)
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            res = g.run_ffmpeg_progress([str(self.ffmpeg), "-i", "x", str(self.dir / "saida.mp4")], 10.0, "Renderizando", **kw)
        return res, saida.getvalue()

    def test_sucesso_mostra_andamento_ate_100(self):
        (ok, erro), texto = self.rodar("ok")
        self.assertTrue(ok)
        self.assertTrue((self.dir / "saida.mp4").is_file())
        self.assertIn("⏳ Renderizando: 10% (0:01 de 0:10)", texto)
        self.assertIn("⏳ Renderizando: 50%", texto)
        self.assertIn("⏳ Renderizando: 100%", texto)

    def test_erro_devolve_o_texto_do_ffmpeg(self):
        (ok, erro), _ = self.rodar("erro")
        self.assertFalse(ok)
        self.assertIn("linha de erro do ffmpeg", erro)

    def test_trava_e_interrompido_sem_limite_fixo_de_tempo(self):
        import time
        inicio = time.monotonic()
        (ok, erro), _ = self.rodar("trava", stall_s=2)
        self.assertFalse(ok)
        self.assertIn("sem andar", erro)
        self.assertLess(time.monotonic() - inicio, 15)

    def test_ffmpeg_que_nao_existe(self):
        ok, erro = g.run_ffmpeg_progress([str(self.dir / "nao_existe"), "-i", "x"], 10.0)
        self.assertFalse(ok)
        self.assertTrue(erro)


class TestRenderLongo(unittest.TestCase):
    NOME = "VideoLongo_20261125_100000.mp4"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.dir)
        pasta = Path("videos_longos")
        for sub in ("_brutos", "_edicoes"):
            (pasta / sub).mkdir(parents=True)
        (pasta / "_brutos" / self.NOME).write_bytes(b"BRUTO")
        (pasta / self.NOME).write_bytes(b"BRUTO")
        segs = [{"start": 5.0, "end": 12.0, "text": "vamos abrir o bau agora", "words": [
            {"word": "vamos", "start": 5.0, "end": 5.5}, {"word": "abrir", "start": 5.6, "end": 6.0}]},
                {"start": 50.0, "end": 58.0, "text": "que risada gente"},
                {"start": 90.0, "end": 98.0, "text": "olha o final disso"}]
        self.meta = {"origem": "longo", "bruto": True, "titulo": "Melhores momentos", "streamer": "Fulano",
                     "momentos": MOMENTOS, "legenda_segmentos": segs, "clip_duration": 120.0, "edicao": {"estado": "bruto"}}
        self.escrever_meta()
        self.escrever_opcoes({})
        self.cmds, self.falhar = [], set()
        self.extras = {}
        self.durs = {self.NOME: 120.0}
        self.patches = [
            mock.patch.object(g, "run_ffmpeg_progress", self.fake_ffmpeg),
            mock.patch.object(g, "get_video_duration", lambda p: self.durs.get(Path(p).name, 0) or (
                120.0 if Path(p).name == self.NOME else 100.0)),
            mock.patch.object(g, "load_env_file", lambda: None), mock.patch.object(g, "get_groq_key", lambda: "KEY"),
            mock.patch.object(g, "add_summary_intro", self.fake_resumo), mock.patch.object(g, "add_cta_outro", self.fake_cta),
            mock.patch.object(g, "TEMP_DIR", self.dir / "temp"), mock.patch.object(g, "STREAMER_NOME", None),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        os.chdir(self.cwd)
        self._tmp.cleanup()

    # ---- fakes
    def fake_ffmpeg(self, cmd, total_s, rotulo="", **kw):
        self.cmds.append((rotulo, cmd, total_s))
        if rotulo in self.falhar:
            return False, "erro simulado do ffmpeg"
        Path(cmd[-1]).write_bytes(b"RENDER-" + rotulo.encode() + b"." * 2048)
        return True, ""

    def fake_resumo(self, caminho, texto, chave, streamer=None, formato=None):
        self.extras["resumo"] = (texto, chave, streamer, formato)
        if "resumo" in self.falhar:
            g.EXTRAS_MOTIVO["resumo"] = "a IA não deu um resumo fiel"
            return None
        return {"texto": "Abertura.", "duracao_s": 7.0, "juncao": "copia"}

    def fake_cta(self, caminho, formato=None):
        self.extras["cta"] = formato
        if "cta" in self.falhar:
            g.EXTRAS_MOTIVO["cta"] = "falha ao pegar o quadro"
            return None
        return {"texto": "Gostou?", "duracao_s": 6.0, "juncao": "copia"}

    def escrever_meta(self):
        Path("videos_longos", self.NOME).with_suffix(".json").write_text(json.dumps(self.meta), encoding="utf-8")

    def escrever_opcoes(self, opcoes):
        (Path("videos_longos") / "_edicoes" / (Path(self.NOME).stem + ".json")).write_text(json.dumps(opcoes), encoding="utf-8")

    def rodar(self):
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            ok = g.render_edit_longo(self.NOME)
        self.saida = saida.getvalue()
        return ok

    def meta_atual(self):
        return json.loads(Path("videos_longos", self.NOME).with_suffix(".json").read_text(encoding="utf-8"))

    # ---- testes
    def test_renderiza_com_tudo_ligado(self):
        self.assertTrue(self.rodar())
        self.assertEqual([c[0] for c in self.cmds], ["Renderizando"])                         # sem trechos: não apara
        _, cmd, total = self.cmds[0]
        self.assertEqual(total, 120.0)
        self.assertIn("subtitles=", cmd[cmd.index("-vf") + 1])
        self.assertTrue(Path("videos_longos", self.NOME).read_bytes().startswith(b"RENDER-Renderizando"))
        self.assertEqual((Path("videos_longos") / "_brutos" / self.NOME).read_bytes(), b"BRUTO")   # o bruto nunca muda
        meta = self.meta_atual()
        self.assertFalse(meta["bruto"])
        self.assertEqual(meta["edicao"]["estado"], "renderizado")
        self.assertEqual(len(meta["capitulos"]), 3)
        self.assertEqual(meta["resumo_intro"]["texto"], "Abertura.")
        self.assertEqual(meta["cta_final"]["duracao_s"], 6.0)
        self.assertEqual(self.extras["cta"], "longo")
        self.assertEqual(self.extras["resumo"][3], "longo")
        self.assertEqual(self.extras["resumo"][2], "Fulano")
        self.assertIn("Momento 1 (SUSTO NO BAU): vamos abrir o bau agora", self.extras["resumo"][0])
        self.assertEqual(list(Path("temp").glob("*")), [])

    def test_renderiza_com_recorte_e_espelhar(self):
        caixa = {"x": 0.6, "y": 0.5, "w": 0.35, "h": 0.45}
        self.escrever_opcoes({"recorte": caixa, "espelhar": True})
        with mock.patch.object(g, "get_video_dimensions", return_value=(1920, 1080)):
            self.assertTrue(self.rodar())
        cmd = self.cmds[0][1]
        grafo = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("hflip", grafo)
        self.assertIn("gblur", grafo)                               # 672x486 não é 16:9: aparece inteiro sobre fundo desfocado
        self.assertLess(grafo.index("hflip"), grafo.index("subtitles="))
        meta = self.meta_atual()
        self.assertEqual(meta["edicao"]["opcoes"]["recorte"], caixa)
        self.assertTrue(meta["edicao"]["opcoes"]["espelhar"])
        self.assertIn("recorte: sim", self.saida)

    def test_recorte_invalido_nao_renderiza(self):
        self.escrever_opcoes({"recorte": {"x": 0.9, "y": 0, "w": 0.5, "h": 0.5}})
        self.assertFalse(self.rodar())
        self.assertEqual(self.cmds, [])

    def test_ass_tem_legenda_e_capitulos(self):
        self.rodar()
        # o ASS é apagado no fim; confira refazendo o texto igual ao do render
        falas = g.filter_portuguese_segments(self.meta["legenda_segmentos"])
        texto = longos.montar_ass(falas, 120.0, longos.capitulos(MOMENTOS, None, 120.0), g.censor_profanity)
        self.assertIn("Capitulo", texto)
        self.assertIn("Legenda", texto)

    def test_tudo_desligado_so_normaliza_e_nao_chama_ia(self):
        self.escrever_opcoes({"legenda": False, "capitulos": False, "resumo": False, "cta": False})
        self.assertTrue(self.rodar())
        _, cmd, _ = self.cmds[0]
        self.assertNotIn("subtitles", " ".join(cmd))
        self.assertEqual(self.extras, {})
        meta = self.meta_atual()
        self.assertIsNone(meta["resumo_intro"])
        self.assertEqual(meta["capitulos"], [])

    def test_trechos_aparam_antes_e_ajustam_tudo(self):
        self.escrever_opcoes({"trechos": [[0, 40], [80, 120]]})
        self.durs["longo_VideoLongo_20261125_100000_aparado.mp4"] = 80.0
        self.assertTrue(self.rodar())
        self.assertEqual([c[0] for c in self.cmds], ["Aparando", "Renderizando"])
        self.assertEqual(self.cmds[0][2], 80.0)
        self.assertIn("trim=start=80.000:end=120.000", self.cmds[0][1][self.cmds[0][1].index("-filter_complex") + 1])
        self.assertEqual(Path(self.cmds[1][1][self.cmds[1][1].index("-i") + 1]).name, "longo_VideoLongo_20261125_100000_aparado.mp4")
        meta = self.meta_atual()
        self.assertEqual(meta["trechos_mantidos"], [[0.0, 40.0], [80.0, 120.0]])
        self.assertEqual([c["n"] for c in meta["capitulos"]], [1, 3])                       # o momento 2 foi cortado inteiro
        self.assertEqual(meta["capitulos"][1]["inicio"], 40.0)
        self.assertNotIn("que risada gente", self.extras["resumo"][0])                      # a abertura só fala do que sobrou
        self.assertEqual(list(Path("temp").glob("*")), [])

    def test_titulo_novo_e_guardado_e_o_original_tambem(self):
        self.escrever_opcoes({"titulo": "Os melhores sustos"})
        self.assertTrue(self.rodar())
        meta = self.meta_atual()
        self.assertEqual((meta["titulo"], meta["titulo_original"]), ("Os melhores sustos", "Melhores momentos"))

    def test_extras_que_falham_sao_registrados_sem_derrubar_o_video(self):
        self.falhar.update({"resumo", "cta"})
        self.assertTrue(self.rodar())
        meta = self.meta_atual()
        self.assertIsNone(meta["resumo_intro"])
        self.assertEqual(meta["resumo_motivo"], "a IA não deu um resumo fiel")
        self.assertEqual(meta["cta_motivo"], "falha ao pegar o quadro")
        self.assertIn("SEM resumo", self.saida)

    def test_falha_no_ffmpeg_deixa_tudo_como_estava(self):
        self.falhar.add("Renderizando")
        self.assertFalse(self.rodar())
        self.assertEqual(Path("videos_longos", self.NOME).read_bytes(), b"BRUTO")
        self.assertTrue(self.meta_atual()["bruto"])
        self.assertEqual(list(Path("temp").glob("*")), [])
        self.assertIn("continua como estava", self.saida)

    def test_falha_ao_aparar_nao_renderiza(self):
        self.escrever_opcoes({"trechos": [[0, 40]]})
        self.falhar.add("Aparando")
        self.assertFalse(self.rodar())
        self.assertEqual([c[0] for c in self.cmds], ["Aparando"])
        self.assertEqual(Path("videos_longos", self.NOME).read_bytes(), b"BRUTO")

    def test_entradas_invalidas(self):
        self.escrever_opcoes({"legenda": "sim"})
        self.assertFalse(self.rodar())
        self.escrever_opcoes({})
        (Path("videos_longos") / "_brutos" / self.NOME).unlink()
        self.assertFalse(self.rodar())
        self.assertFalse(g.render_edit_longo("../x.mp4"))
        self.assertFalse(g.render_edit_longo("_x.mp4"))
        self.assertEqual(self.cmds, [])

    def test_so_serve_para_videos_longos(self):
        self.meta["origem"] = "live"
        self.escrever_meta()
        self.assertFalse(self.rodar())
        self.assertEqual(self.cmds, [])


class TestTitulosDoLongo(unittest.TestCase):
    def setUp(self):
        self._llm = g.groq_chat_with_fallback

    def tearDown(self):
        g.groq_chat_with_fallback = self._llm

    def test_sugestoes_limpas_sem_repetir(self):
        prompts = []

        def ia(msgs, *a, **k):
            prompts.append(msgs[0]["content"])
            return json.dumps([{"titulo": "OS MELHORES SUSTOS 😱 DA LIVE"}, {"titulo": "Os melhores sustos da live"},
                               {"titulo": "Título atual"}, {"titulo": "x" * 200}, {"nada": 1}, "texto", {"titulo": 5}])
        g.groq_chat_with_fallback = ia
        itens = g.suggest_long_titles(MOMENTOS, "Título atual", "KEY")
        self.assertEqual(len(itens), 2)                                       # emoji sai; repetido e atual somem
        self.assertNotIn("😱", itens[0]["titulo"])
        self.assertTrue(all(len(i["titulo"]) <= 70 and i["pergunta"] == "" for i in itens))
        self.assertIn("SUSTO NO BAU (susto)", prompts[0])                        # a IA só vê os momentos reais
        self.assertIn("NÃO invente", prompts[0])

    def test_sem_chave_ou_sem_momentos_ou_ia_fora(self):
        g.groq_chat_with_fallback = lambda *a, **k: None
        self.assertEqual(g.suggest_long_titles(MOMENTOS, "", "KEY"), [])
        self.assertEqual(g.suggest_long_titles(MOMENTOS, "", None), [])
        self.assertEqual(g.suggest_long_titles([], "", "KEY"), [])
        g.groq_chat_with_fallback = lambda *a, **k: "isto não é json"
        self.assertEqual(g.suggest_long_titles(MOMENTOS, "", "KEY"), [])

    def test_comando_sugerir_titulos_do_longo(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                Path("videos_longos").mkdir()
                Path("videos_longos/V.json").write_text(json.dumps({"momentos": MOMENTOS, "titulo": "Atual"}), encoding="utf-8")
                g.groq_chat_with_fallback = lambda *a, **k: json.dumps([{"titulo": "Um título bom"}])
                with mock.patch.object(g, "load_env_file", lambda: None), mock.patch.object(g, "get_groq_key", lambda: "KEY"):
                    saida = io.StringIO()
                    with contextlib.redirect_stdout(saida):
                        self.assertTrue(g.suggest_titles_for_clip("V.mp4", "longos"))
                    self.assertIn('TITULOS_JSON=[{"titulo": "Um título bom", "pergunta": ""}]', saida.getvalue())
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertFalse(g.suggest_titles_for_clip("nao_existe.mp4", "longos"))
                with mock.patch.object(g, "load_env_file", lambda: None), mock.patch.object(g, "get_groq_key", lambda: None):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertFalse(g.suggest_titles_for_clip("V.mp4", "longos"))
            finally:
                os.chdir(cwd)


class TestIntroHorizontal(unittest.TestCase):
    EVENTOS = [{"text": t, "offset": i * 0.4, "duration": 0.35, "type": "WordBoundary"}
               for i, t in enumerate("o streamer testou o jogo e não aguentou os sustos".split())]

    def test_ass_da_narracao_horizontal(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "a.ass"
            self.assertTrue(g.create_intro_ass(self.EVENTOS, caminho, 5.0, formato="longo"))
            texto = caminho.read_text(encoding="utf-8")
            self.assertIn("PlayResX: 1920", texto)
            self.assertIn("PlayResY: 1080", texto)
            self.assertIn("Style: Intro,Impact,64,", texto)
            self.assertTrue(g.create_intro_ass(self.EVENTOS, caminho, 5.0))
            self.assertIn("PlayResX: 1080", caminho.read_text(encoding="utf-8"))               # o vertical continua igual

    def test_resumo_e_chamada_do_longo_nao_tem_limite_de_60s_e_ignoram_o_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir_ = Path(tmp)
            saida = dir_ / "v.mp4"
            saida.write_bytes(b"ORIGINAL")
            os.environ["CORTES_RESUMO"] = "0"
            os.environ["CORTES_CTA"] = "0"

            def run(cmd, cwd=None, timeout=None):
                if "-hide_banner" in cmd and "-i" in cmd and cmd[-1] == str(saida) and "-frames:v" not in cmd and "-y" not in cmd:
                    return False, "", "Video: h264, 1920x1080, 30 fps\nAudio: aac, 48000 Hz, stereo"
                Path(cmd[-1]).write_bytes(b"x" * 2048)
                return True, "", ""
            chamadas = {}

            def concat(primeiro, segundo, joined, lista, esperado, reencode_timeout=900):
                chamadas["timeout"] = reencode_timeout
                Path(joined).write_bytes(b"JUNTO")
                return "copia", ""
            try:
                with mock.patch.object(g, "TEMP_DIR", dir_), mock.patch.object(g, "run_cmd", side_effect=run), \
                     mock.patch.object(g, "get_video_duration", lambda p: 700.0), \
                     mock.patch.object(g, "generate_summary_intro_text", return_value="O streamer abre a live com sustos.") as gerar, \
                     mock.patch.object(g, "_speak_summary", return_value=(True, 5.0, self.EVENTOS)), \
                     mock.patch.object(g, "_concat_with_fallback", side_effect=concat):
                    info = g.add_summary_intro(saida, "texto dos momentos", "KEY", "Fulano", formato="longo")
                    self.assertIsNotNone(info)
                    self.assertTrue(gerar.call_args.kwargs["longo"])
                    self.assertEqual(chamadas["timeout"], 2800)                                  # 4 x 700 s: proporcional ao vídeo
                    saida.write_bytes(b"ORIGINAL")
                    self.assertIsNotNone(g.add_cta_outro(saida, formato="longo"))
                    saida.write_bytes(b"ORIGINAL")
                    self.assertIsNone(g.add_summary_intro(saida, "texto", "KEY", "Fulano"))      # curto: o env desligado vale
                    self.assertIsNone(g.add_cta_outro(saida))
            finally:
                os.environ.pop("CORTES_RESUMO", None)
                os.environ.pop("CORTES_CTA", None)

    def test_prompt_do_longo_fala_de_melhores_momentos(self):
        prompts = []

        def groq(msgs, key, model, **k):
            prompts.append(msgs[0]["content"])
            return json.dumps({"resumo": "O streamer enfrenta sustos e risadas nesta live."})
        with mock.patch.object(g, "groq_chat", side_effect=groq), mock.patch.object(g, "get_text_models_to_try", return_value=["m"]):
            texto = g.generate_summary_intro_text("Momento 1 (SUSTOS): o streamer teve sustos e risadas na live", "k", "o streamer", longo=True)
            curto = g.generate_summary_intro_text("o streamer teve sustos e risadas na live", "k", None)
        self.assertTrue(texto)
        self.assertIn("MELHORES MOMENTOS", prompts[0])
        self.assertIn("Short", prompts[1])
        self.assertTrue(curto)


class TestLinhaDeComandoDaEdicao(unittest.TestCase):
    def test_help_lista_a_biblioteca(self):
        import subprocess
        saida = subprocess.run([sys.executable, str(RAIZ / "gerar_cortes.py"), "--help"], capture_output=True, text=True,
                               encoding="utf-8", cwd=str(RAIZ), timeout=60).stdout
        self.assertIn("--biblioteca", saida)


if __name__ == "__main__":
    unittest.main()
