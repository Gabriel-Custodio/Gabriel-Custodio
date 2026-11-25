"""Testes do vídeo longo montado com os cortes de uma live monitorada (pacote longos + gerar_cortes + servidor).

Sem ffmpeg, rede ou Groq. Rodar na pasta do projeto:   python -m unittest discover -s app/tests -v
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gerar_cortes as g  # noqa: E402
import longos  # noqa: E402
from test_longos import FfmpegFalso  # noqa: E402
from test_servidor import Base  # noqa: E402

AGORA = datetime(2026, 11, 25, 20, 0, 0)


def cria_corte(pasta, nome, minutos_atras, nota=8.0, canal="alanzoka", plataforma="twitch", dur=45.0, origem="live",
               bruto=True, titulo="TITULO", fonte_titulo="ia_texto_real", segmentos=None, **extra):
    """Cria cortes_prontos/NOME.mp4 + .json (+ _brutos/NOME.mp4) como o monitor faz; detectado `minutos_atras`."""
    pasta = Path(pasta)
    (pasta / "_brutos").mkdir(parents=True, exist_ok=True)
    detectado = AGORA - timedelta(minutes=minutos_atras)
    meta = {"origem": origem, "live": {"plataforma": plataforma, "canal": canal, "nome": canal.title(),
                                       "url": f"https://www.twitch.tv/{canal}", "detectado_em": detectado.isoformat(timespec="seconds")},
            "score_selecao": nota, "clip_duration": dur, "titulo_gancho": titulo, "fonte_titulo": fonte_titulo,
            "motivo_selecao": "reacao forte", "transcricao": f"fala do corte {nome}", "streamer": canal.title(),
            "legenda_segmentos": segmentos if segmentos is not None else [{"start": 1.0, "end": 4.0, "text": f"fala {nome}"}]}
    meta.update(extra)
    (pasta / nome).write_bytes(b"x" * 100)
    (pasta / nome).with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
    if bruto:
        (pasta / "_brutos" / nome).write_bytes(b"x" * 100)


class TestListarCortes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pasta = Path(self._tmp.name) / "cortes_prontos"
        self.pasta.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def listar(self, **kw):
        return longos.listar_cortes_da_live(self.pasta, "twitch", "alanzoka", agora=AGORA, **kw)

    def test_campos_e_linha_do_tempo_real(self):
        cria_corte(self.pasta, "CorteLive_A.mp4", 10, nota=7.5)
        (c,) = self.listar()
        self.assertEqual(c["end"] - c["start"], 45.0)
        self.assertEqual(c["end"], (AGORA - timedelta(minutes=10)).timestamp())          # o fim do corte é quando o monitor detectou
        self.assertEqual((c["score"], c["reason"], c["hook_title"], c["nome"]), (7.5, "reacao forte", "TITULO", "CorteLive_A.mp4"))
        self.assertEqual(Path(c["arquivo"]).name, "CorteLive_A.mp4")
        self.assertEqual(Path(c["arquivo"]).parent.name, "_brutos")
        self.assertEqual(c["segmentos"], [{"start": 1.0, "end": 4.0, "text": "fala CorteLive_A.mp4"}])
        self.assertEqual(c["streamer"], "Alanzoka")

    def test_ordem_cronologica(self):
        for nome, atras in (("C.mp4", 5), ("A.mp4", 50), ("B.mp4", 20)):
            cria_corte(self.pasta, nome, atras)
        self.assertEqual([c["nome"] for c in self.listar()], ["A.mp4", "B.mp4", "C.mp4"])

    def test_filtros(self):
        cria_corte(self.pasta, "ok.mp4", 10)
        cria_corte(self.pasta, "outro_canal.mp4", 10, canal="gaules")
        cria_corte(self.pasta, "outra_plataforma.mp4", 10, plataforma="youtube")
        cria_corte(self.pasta, "do_vod.mp4", 10, origem="video")
        cria_corte(self.pasta, "sem_bruto.mp4", 10, bruto=False)
        cria_corte(self.pasta, "_interno.mp4", 10)
        cria_corte(self.pasta, "sem_nota.mp4", 10, score_selecao=None)
        cria_corte(self.pasta, "sem_duracao.mp4", 10, clip_duration=0, duracao_final_s=0)
        cria_corte(self.pasta, "sem_data.mp4", 10, live={"plataforma": "twitch", "canal": "alanzoka"}, timestamp=None)
        (self.pasta / "quebrado.json").write_text("{nao e json", encoding="utf-8")
        (self.pasta / "lista.json").write_text("[]", encoding="utf-8")
        self.assertEqual([c["nome"] for c in self.listar()], ["ok.mp4"])

    def test_periodo(self):
        cria_corte(self.pasta, "recente.mp4", 30)
        cria_corte(self.pasta, "antigo.mp4", 60 * 5)
        self.assertEqual(len(self.listar()), 2)
        self.assertEqual([c["nome"] for c in self.listar(horas=2)], ["recente.mp4"])
        self.assertEqual(len(self.listar(horas=6)), 2)

    def test_titulo_padrao_nao_vira_titulo_de_capitulo(self):
        cria_corte(self.pasta, "padrao.mp4", 10, titulo="MOMENTO INCRÍVEL NA LIVE", fonte_titulo="padrao")
        self.assertEqual(self.listar()[0]["hook_title"], "")

    def test_pasta_inexistente(self):
        self.assertEqual(longos.listar_cortes_da_live(self.pasta / "nao_existe", "twitch", "alanzoka"), [])

    def test_cortes_descartados_ficam_de_fora(self):
        cria_corte(self.pasta, "bom.mp4", 10)
        cria_corte(self.pasta / "_descartados", "ruim.mp4", 10)
        self.assertEqual([c["nome"] for c in self.listar()], ["bom.mp4"])


class TestEscolhaESolapamento(unittest.TestCase):
    def test_cortes_que_se_sobrepoem_na_live_nao_entram_juntos(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            cria_corte(pasta, "A.mp4", 60, nota=9.0)
            cria_corte(pasta, "B.mp4", 59.5, nota=8.0)       # detectado 30 s depois: os 45 s dele repetem 15 s de A
            cria_corte(pasta, "C.mp4", 50, nota=7.0)
            cands = longos.listar_cortes_da_live(pasta, "twitch", "alanzoka", agora=AGORA)
            escolhidos, resumo = longos.selecionar(cands, 480, distancia_min_s=0)
            self.assertEqual([c["nome"] for c in escolhidos], ["A.mp4", "C.mp4"])
            self.assertEqual(resumo["escolhidos"], 2)

    def test_so_nota_6_5_ou_mais(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            for i, nota in enumerate((6.4, 6.5, 9.0)):
                cria_corte(pasta, f"{i}.mp4", 100 - i * 10, nota=nota)
            cands = longos.listar_cortes_da_live(pasta, "twitch", "alanzoka", agora=AGORA)
            escolhidos, _ = longos.selecionar(cands, 480, distancia_min_s=0)
            self.assertEqual([c["nome"] for c in escolhidos], ["1.mp4", "2.mp4"])


class TestMontagemDeCortes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.cortes = self.dir / "cortes_prontos"
        for i, atras in enumerate((90, 60, 30)):
            cria_corte(self.cortes, f"C{i}.mp4", atras, nota=9 - i, dur=40.0 + i * 5, titulo=f"TITULO {i}",
                       segmentos=[{"start": 2.0, "end": 6.0, "text": f"fala {i}", "words": [
                           {"word": "fala", "start": 2.0, "end": 3.0}, {"word": str(i), "start": 3.0, "end": 4.0}]}])
        self.cands = longos.listar_cortes_da_live(self.cortes, "twitch", "alanzoka", agora=AGORA)

    def tearDown(self):
        self._tmp.cleanup()

    def montar(self, falso):
        destino = self.dir / "videos_longos" / "_brutos" / "V.mp4"
        res = longos.montar_bruto(None, self.cands, destino, self.dir / "temp", "ffmpeg", falso, falso.medir, log=lambda *_: None)
        return res, destino

    def test_cada_momento_vem_do_proprio_arquivo(self):
        falso = FfmpegFalso()
        res, destino = self.montar(falso)
        self.assertTrue(destino.is_file())
        cortes = [c for c in falso.chamadas if "concat" not in c]
        for i, cmd in enumerate(cortes):
            self.assertEqual(Path(cmd[cmd.index("-i") + 1]).name, f"C{i}.mp4")
            self.assertEqual(cmd[cmd.index("-ss") + 1], "0.000")                              # o corte inteiro, do começo
            self.assertEqual(cmd[cmd.index("-t") + 1], f"{40.0 + i * 5:.3f}")
        self.assertEqual([(m["inicio"], m["fim"]) for m in res["momentos"]], [(0, 40), (40, 85), (85, 135)])
        self.assertEqual([m["arquivo"] for m in res["momentos"]], ["C0.mp4", "C1.mp4", "C2.mp4"])
        self.assertEqual((res["momentos"][0]["inicio_video"], res["momentos"][0]["fim_video"]), (0.0, 40.0))
        self.assertEqual(res["momentos"][1]["titulo"], "TITULO 1")

    def test_legenda_e_texto_vem_de_cada_corte(self):
        res, _ = self.montar(FfmpegFalso())
        resumo = {"elegiveis": 3, "escolhidos": 3, "curto": True, "motivo": ""}
        meta = longos.metadados(res["momentos"], [], res["duracao_s"], 480, resumo, streamer="Alanzoka",
                                extra={"fonte": {"tipo": "live", "canal": "alanzoka"}})
        json.dumps(meta)
        textos = [s["text"] for s in meta["legenda_segmentos"]]
        self.assertEqual(textos, ["fala 0", "fala 1", "fala 2"])
        self.assertEqual([s["start"] for s in meta["legenda_segmentos"]], [2.0, 42.0, 87.0])        # deslocadas para a linha do tempo do longo
        self.assertEqual(meta["legenda_segmentos"][1]["words"][0], {"word": "fala", "start": 42.0, "end": 43.0})
        self.assertEqual(meta["transcricao"], "fala 0 fala 1 fala 2")
        self.assertTrue(all("segmentos" not in m for m in meta["momentos"]))                      # não duplica a legenda em cada momento
        self.assertEqual(meta["fonte"]["tipo"], "live")
        self.assertEqual((meta["origem"], meta["bruto"]), ("longo", True))


class TestBuildLongFromLive(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.dir)
        self.falso = FfmpegFalso()
        self.patches = [mock.patch.object(g, "run_cmd", lambda cmd, cwd=None, timeout=None: self.falso(cmd, timeout)),
                        mock.patch.object(g, "get_video_duration", self.falso.medir),
                        mock.patch.object(g, "TEMP_DIR", self.dir / "temp"), mock.patch.object(g, "OUTPUT_DIR", Path("cortes_prontos")),
                        mock.patch.object(g, "load_env_file", lambda: None)]
        for p in self.patches:
            p.start()
        agora = datetime.now()
        for i in range(8):                                     # 8 cortes de 45 s, detectados de 4 em 4 min, até 1 h atrás
            cria_corte(Path("cortes_prontos"), f"CorteLive_{i}.mp4", 5 + i * 4, nota=9.0 - i * 0.2)
        for arq in Path("cortes_prontos").glob("*.json"):       # as datas dos cortes de teste são relativas a AGORA fixo: reancora em agora
            dados = json.loads(arq.read_text(encoding="utf-8"))
            antes = datetime.fromisoformat(dados["live"]["detectado_em"])
            dados["live"]["detectado_em"] = (agora - (AGORA - antes)).isoformat(timespec="seconds")
            arq.write_text(json.dumps(dados), encoding="utf-8")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.chdir(self.cwd)
        self._tmp.cleanup()

    def rodar(self, ref="twitch:alanzoka", minutos=4, horas=None):
        with contextlib.redirect_stdout(io.StringIO()) as saida:
            r = g.build_long_from_live(ref, minutos, horas)
        self.saida = saida.getvalue()
        return r

    def test_gera_o_bruto_e_o_json_com_a_origem(self):
        r = self.rodar()
        self.assertIsNotNone(r, self.saida)
        meta = json.loads(r.read_text(encoding="utf-8"))
        self.assertEqual((meta["origem"], meta["bruto"], meta["streamer"]), ("longo", True, "Alanzoka"))
        self.assertEqual(meta["fonte"]["tipo"], "live")
        self.assertEqual(meta["fonte"]["canal"], "alanzoka")
        self.assertEqual(meta["fonte"]["cortes"], [m["arquivo"] for m in meta["momentos"]])
        self.assertEqual(meta["video_url"], "https://www.twitch.tv/alanzoka")
        self.assertGreaterEqual(meta["duracao_final_s"], 240)
        self.assertTrue((Path("videos_longos") / "_brutos" / (r.stem + ".mp4")).is_file())
        self.assertEqual(len(list(Path("cortes_prontos/_brutos").glob("*.mp4"))), 8)             # os cortes do monitor não são tocados
        self.assertTrue(meta["legenda_segmentos"])
        self.assertEqual([c for c in (self.dir / "temp").glob("*")], [])

    def test_periodo(self):
        meta = json.loads(self.rodar(minutos=4, horas=1).read_text(encoding="utf-8"))
        self.assertEqual(meta["fonte"]["horas"], 1)
        self.assertIn("nas últimas 1 h", self.saida)

    def test_sem_cortes_do_canal_nao_gera_e_explica(self):
        self.assertIsNone(self.rodar("twitch:outro"))
        self.assertIn("Não há cortes deste canal", self.saida)
        self.assertFalse(Path("videos_longos").exists())

    def test_referencia_invalida(self):
        for ruim in ("", "semdoispontos", ":canal", "twitch:"):
            self.assertIsNone(self.rodar(ruim), ruim)
            self.assertIn("PLATAFORMA:CANAL", self.saida)

    def test_poucos_cortes_bons_nao_geram(self):
        for arq in Path("cortes_prontos").glob("*.json"):
            dados = json.loads(arq.read_text(encoding="utf-8"))
            dados["score_selecao"] = 3.0
            arq.write_text(json.dumps(dados), encoding="utf-8")
        self.assertIsNone(self.rodar())
        self.assertIn("não dá um vídeo longo", self.saida)

    def test_erro_inesperado_nao_sobe(self):
        with mock.patch.object(longos, "montar_bruto", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.rodar())


class TestLinhaDeComando(unittest.TestCase):
    def rodar(self, *args):
        with mock.patch.object(sys, "argv", ["gerar_cortes.py", *args]), contextlib.redirect_stdout(io.StringIO()) as saida:
            try:
                g.main()
                codigo = 0
            except SystemExit as e:
                codigo = e.code or 0
        return codigo, saida.getvalue()

    def test_exige_a_duracao_e_valida_a_faixa(self):
        self.assertEqual(self.rodar("--longo-da-live", "twitch:x")[0], 1)
        self.assertIn("--video-longo", self.rodar("--longo-da-live", "twitch:x")[1])
        self.assertEqual(self.rodar("--longo-da-live", "twitch:x", "--video-longo", "3")[0], 1)

    def test_chama_a_montagem_com_os_argumentos(self):
        chamadas = []
        with mock.patch.object(g, "resolve_ffmpeg", lambda: True), mock.patch.object(g, "ensure_dirs", lambda: None), \
             mock.patch.object(g, "build_long_from_live", lambda *a: chamadas.append(a) or Path("x.json")):
            codigo, _ = self.rodar("--longo-da-live", "twitch:alanzoka", "--video-longo", "8", "--live-horas", "6")
            self.assertEqual((codigo, chamadas), (0, [("twitch:alanzoka", 8, 6)]))
        with mock.patch.object(g, "resolve_ffmpeg", lambda: True), mock.patch.object(g, "ensure_dirs", lambda: None), \
             mock.patch.object(g, "build_long_from_live", lambda *a: None):
            self.assertEqual(self.rodar("--longo-da-live", "twitch:alanzoka", "--video-longo", "8")[0], 1)

    def test_help(self):
        import subprocess
        saida = subprocess.run([sys.executable, str(RAIZ / "gerar_cortes.py"), "--help"], capture_output=True, text=True,
                               encoding="utf-8", cwd=str(RAIZ), timeout=60).stdout
        self.assertIn("--longo-da-live", saida)
        self.assertIn("--live-horas", saida)


GERAR_FALSO_LIVE = "import sys\nprint('ARGS:', sys.argv[1:], flush=True)\n"


class TestRotaDoServidor(Base):
    def setUp(self):
        super().setUp()
        (self.raiz / "gerar_cortes.py").write_text(GERAR_FALSO_LIVE, encoding="utf-8")
        self.canal = self.json("POST", "/api/canais", {"canal": "https://www.twitch.tv/alanzoka"})[1]["canal"]

    def pedir(self, **corpo):
        return self.json("POST", "/api/lives/longo", {"id": self.canal["id"], "minutos": 8, "horas": 6, **corpo})

    def test_monta_o_comando(self):
        status, dados = self.pedir()
        self.assertEqual(status, 201)
        self.assertEqual(dados["job"]["parametros"]["video_longo"], 8)
        texto = "\n".join(self.esperar_fim()["linhas"])
        self.assertIn("'--longo-da-live', 'twitch:alanzoka', '--video-longo', '8', '--live-horas', '6'", texto)

    def test_todos_os_cortes_nao_manda_o_periodo(self):
        self.assertEqual(self.pedir(horas=None)[0], 201)
        self.assertNotIn("--live-horas", "\n".join(self.esperar_fim()["linhas"]).split("ARGS:")[-1])

    def test_validacoes(self):
        for corpo in ({"minutos": 3}, {"minutos": 13}, {"minutos": "8"}, {"minutos": 8.5}, {"minutos": True}, {"minutos": None},
                      {"horas": 0}, {"horas": -1}, {"horas": 24 * 31}, {"horas": "6"}, {"horas": True}, {"id": "nao-existe"}, {"id": ""}):
            self.assertEqual(self.pedir(**corpo)[0], 400, corpo)
        self.assertIsNone(self.srv.estado.job)

    def test_canal_suspenso_nao_pode_ter_os_cortes_usados(self):
        self.json("POST", "/api/canais/ativo", {"id": self.canal["id"], "ativo": False})
        status, dados = self.pedir()
        self.assertEqual(status, 400)
        self.assertIn("suspenso", dados["erro"])

    def test_uma_execucao_por_vez(self):
        (self.raiz / "gerar_cortes.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        self.assertEqual(self.pedir()[0], 201)
        self.assertEqual(self.pedir()[0], 409)

    def test_exige_cookie_e_cabecalho(self):
        self.assertEqual(self.req("POST", "/api/lives/longo", {"id": "x"}, cookie=False).status, 403)
        self.assertEqual(self.req("POST", "/api/lives/longo", {"id": "x"}, csrf=False).status, 403)


if __name__ == "__main__":
    unittest.main()
