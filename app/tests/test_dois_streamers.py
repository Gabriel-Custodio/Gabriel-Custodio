"""Testes das três novidades pedidas pelo QA: dois streamers no vídeo longo, descrição sugerida pela IA e reedição.

Sem ffmpeg, sem rede, sem Groq. Rodar na pasta do projeto:   python -m unittest discover -s app/tests -v
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1]
RAIZ = APP.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gerar_cortes as g  # noqa: E402
import longos  # noqa: E402
import servidor  # noqa: E402
from longos import montagem  # noqa: E402
from test_historico import transcricao  # noqa: E402
from test_longos import FfmpegFalso, cand, ia_falsa  # noqa: E402
from test_servidor import Base, URL_OK  # noqa: E402
from test_servidor_longos import NOME as NOME_LONGO, BaseLongos  # noqa: E402

ESTATICO = APP / "static"
URL_B = "https://www.youtube.com/watch?v=BBBBBBBBBBB"


def duas(a, b):
    """Candidatos das fontes 1 e 2 (a e b são listas de (início, nota))."""
    return ([cand(i, nota=n, fonte_n=1) for i, n in a] + [cand(i, nota=n, fonte_n=2) for i, n in b])


# ============================================================================================ 1. dois streamers
class TestSelecaoAlternada(unittest.TestCase):
    def test_intercala_um_de_cada_streamer(self):
        todos = duas([(i * 100, 9.0 - i * 0.1) for i in range(10)], [(i * 100, 8.0 - i * 0.1) for i in range(10)])
        escolhidos, resumo = longos.selecionar(todos, 240, alternar=True)
        self.assertEqual([c["fonte_n"] for c in escolhidos], [1, 2, 1, 2, 1, 2])
        self.assertEqual(sum(c["duration"] for c in escolhidos), 240.0)
        self.assertTrue(resumo["viavel"])

    def test_sem_alternar_a_melhor_nota_vence_e_um_streamer_pode_dominar(self):
        todos = duas([(i * 100, 9.5 - i * 0.1) for i in range(10)], [(i * 100, 7.0) for i in range(10)])
        escolhidos, _ = longos.selecionar(todos, 240)
        self.assertEqual({c["fonte_n"] for c in escolhidos}, {1})

    def test_dentro_de_cada_fonte_a_ordem_e_cronologica(self):
        todos = duas([(500, 9.0), (100, 8.5), (900, 8.0)], [(700, 9.0), (50, 8.5), (300, 8.0)])
        escolhidos, _ = longos.selecionar(todos, 240, alternar=True)
        self.assertEqual([(c["fonte_n"], c["start"]) for c in escolhidos],
                         [(1, 100.0), (2, 50.0), (1, 500.0), (2, 300.0), (1, 900.0), (2, 700.0)])

    def test_tempos_iguais_em_videos_diferentes_nao_se_atrapalham(self):
        # o mesmo início nos dois vídeos: são vídeos diferentes, então os dois entram
        escolhidos, _ = longos.selecionar(duas([(100, 9.0), (400, 8.0)], [(100, 9.0), (400, 8.0)]), 160, alternar=True)
        self.assertEqual(sorted((c["fonte_n"], c["start"]) for c in escolhidos), [(1, 100.0), (1, 400.0), (2, 100.0), (2, 400.0)])

    def test_a_distancia_minima_continua_valendo_dentro_da_mesma_fonte(self):
        escolhidos, _ = longos.selecionar(duas([(100, 9.0), (130, 8.9), (500, 8.0)], [(100, 9.0), (500, 8.0)]), 160, alternar=True)
        fonte1 = [c["start"] for c in escolhidos if c["fonte_n"] == 1]
        self.assertNotIn(130.0, fonte1)                                       # a 30 s do 100 s: barrado
        self.assertEqual(fonte1, [100.0, 500.0])

    def test_se_um_streamer_tem_poucos_momentos_o_outro_completa(self):
        todos = duas([(i * 100, 9.0 - i * 0.1) for i in range(10)], [(100, 8.0), (400, 5.0)])        # o 2º tem 1 momento bom (nota >= 6,5)
        escolhidos, resumo = longos.selecionar(todos, 240, alternar=True)
        self.assertEqual(sum(1 for c in escolhidos if c["fonte_n"] == 2), 1)
        self.assertEqual(sum(1 for c in escolhidos if c["fonte_n"] == 1), 5)
        self.assertEqual(resumo["total_s"], 240.0)

    def test_so_uma_fonte_com_alternar_funciona_como_antes(self):
        so_a = [cand(i * 100, nota=9.0 - i * 0.1, fonte_n=1) for i in range(10)]
        com, _ = longos.selecionar(so_a, 240, alternar=True)
        sem, _ = longos.selecionar([dict(c) for c in so_a], 240)
        self.assertEqual([c["start"] for c in com], [c["start"] for c in sem])

    def test_a_nota_minima_vale_para_os_dois(self):
        escolhidos, resumo = longos.selecionar(duas([(0, 6.4), (100, 9.0)], [(0, 6.0), (100, 6.5)]), 240, alternar=True)
        self.assertEqual(sorted((c["fonte_n"], c["start"]) for c in escolhidos), [(1, 100.0), (2, 100.0)])
        self.assertEqual(resumo["elegiveis"], 2)

    def test_candidatos_de_entrada_nao_sao_alterados(self):
        todos = duas([(0, 9.0), (100, 8.0)], [(0, 9.0), (100, 8.0)])
        copia = json.loads(json.dumps(todos))
        longos.selecionar(todos, 160, alternar=True)
        self.assertEqual(todos, copia)


class TestMontagemDuasFontes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.video1, self.video2 = self.dir / "um.mp4", self.dir / "dois.mp4"
        self.logs = []

    def tearDown(self):
        self._tmp.cleanup()

    def escolhidos(self):
        seg1 = [{"start": 12.0, "end": 20.0, "text": "fala do primeiro"}]
        seg2 = [{"start": 12.0, "end": 20.0, "text": "fala do segundo"}]
        return [cand(10, 40, fonte_n=1, video=self.video1, segmentos=seg1, streamer="Ana", url="https://a"),
                cand(10, 40, fonte_n=2, video=self.video2, segmentos=seg2, streamer="Beto", url="https://b"),
                cand(300, 35, fonte_n=1, video=self.video1, segmentos=seg1, streamer="Ana", url="https://a")]

    def montar(self, falso):
        destino = self.dir / "videos_longos" / "_brutos" / "V.mp4"
        return longos.montar_bruto(self.video1, self.escolhidos(), destino, self.dir / "temp", "ffmpeg", falso,
                                   falso.medir, log=self.logs.append)

    def test_cada_momento_e_cortado_do_video_da_sua_fonte(self):
        falso = FfmpegFalso()
        res = self.montar(falso)
        entradas = [c[c.index("-i") + 1] for c in falso.chamadas if "-ss" in c]
        self.assertEqual(entradas, [str(self.video1), str(self.video2), str(self.video1)])
        self.assertEqual([m["fonte_n"] for m in res["momentos"]], [1, 2, 1])
        self.assertEqual([m["streamer"] for m in res["momentos"]], ["Ana", "Beto", "Ana"])
        self.assertEqual([m["video_url"] for m in res["momentos"]], ["https://a", "https://b", "https://a"])
        self.assertEqual([(m["inicio"], m["fim"]) for m in res["momentos"]], [(0, 40), (40, 80), (80, 115)])
        self.assertTrue(any("fonte 2 (Beto)" in linha for linha in self.logs))

    def test_a_legenda_de_cada_momento_vem_da_transcricao_do_seu_video(self):
        res = self.montar(FfmpegFalso())
        meta = longos.metadados(res["momentos"], [{"start": 0.0, "end": 500.0, "text": "transcrição do vídeo errado"}],
                                res["duracao_s"], 480, {"elegiveis": 3, "escolhidos": 3}, streamer="Ana e Beto")
        textos = [s["text"] for s in meta["legenda_segmentos"]]
        self.assertEqual(textos, ["fala do primeiro", "fala do segundo"])            # o 3º momento (300-335 s) não tem fala
        self.assertIn("fala do segundo", textos)
        self.assertNotIn("transcrição do vídeo errado", textos)
        segundo = [s for s in meta["legenda_segmentos"] if s["text"] == "fala do segundo"][0]
        self.assertTrue(40.0 <= segundo["start"] < segundo["end"] <= 80.0)            # na janela do momento 2 do vídeo longo
        json.dumps(meta)                                                              # tem que virar JSON (nada de Path)
        self.assertTrue(all("segmentos" not in m and "video" not in m for m in meta["momentos"]))
        self.assertEqual(meta["titulo"], "Melhores momentos de Ana e Beto")
        self.assertEqual([m["streamer"] for m in meta["momentos"]], ["Ana", "Beto", "Ana"])

    def test_sem_video_no_candidato_nada_muda(self):
        falso = FfmpegFalso()
        res = longos.montar_bruto(self.video1, [cand(10, 40), cand(300, 35)], self.dir / "d" / "V.mp4", self.dir / "temp", "ffmpeg",
                                  falso, falso.medir, log=self.logs.append)
        self.assertTrue(all("fonte_n" not in m and "streamer" not in m for m in res["momentos"]))
        self.assertTrue(all(c[c.index("-i") + 1] == str(self.video1) for c in falso.chamadas if "-ss" in c))


class Cenario:
    """g.main() inteiro com DOIS vídeos, tudo de fora simulado (download, transcrição, IA, ffmpeg)."""
    URL = "https://www.youtube.com/watch?v=AAAAAAAAAAA"

    def preparar(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.dir)
        self.falso = FfmpegFalso()
        self.video1, self.video2 = self.dir / "um.mp4", self.dir / "dois.mp4"
        self.segs1, self.segs2 = transcricao(3600.0), transcricao(3000.0, semente=11)
        self.cortes_de = []
        self.baixados = []
        self.baixar_segundo = True
        self.duracao2 = 3000.0
        self.canais = {"AAAAAAAAAAA": "Canal A", "BBBBBBBBBBB": "Canal B"}

        def run_cmd(cmd, cwd=None, timeout=None):
            if "-version" in cmd or "--version" in cmd or "-c" in cmd and "import" in cmd[cmd.index("-c") + 1]:
                return True, "", ""
            return self.falso(cmd, timeout)

        def duracao(caminho):
            return {self.video1: 3600.0, self.video2: self.duracao2}.get(Path(caminho)) or self.falso.medir(caminho)

        def baixar(url, prefixo):
            self.baixados.append(url)
            if "BBBB" in url:
                return (self.video2, None) if self.baixar_segundo else (None, None)
            return self.video1, None

        def info(url, *a, **k):
            return {"channel": self.canais["B" * 11 if "BBBB" in url else "A" * 11]}

        def legendas(sub, video, pasta, chave=None):
            return (self.segs2 if Path(video) == self.video2 else self.segs1), "whisper", {}

        def corte(video, clip, segments, saida, num, webcam, chave=None):
            self.cortes_de.append(Path(video))
            return True

        self.patches = [
            mock.patch.object(g, "resolve_ffmpeg", lambda: True), mock.patch.object(g, "run_cmd", run_cmd),
            mock.patch.object(g, "find_js_runtime", lambda: "node"), mock.patch.object(g, "load_env_file", lambda: None),
            mock.patch.object(g, "get_groq_key", lambda: "KEY"), mock.patch.object(g, "download_video", baixar),
            mock.patch.object(g, "fetch_video_info", info), mock.patch.object(g, "get_video_duration", duracao),
            mock.patch.object(g, "load_subtitles_with_fallback", legendas),
            mock.patch.object(g, "groq_chat_with_fallback", ia_falsa), mock.patch.object(g, "cut_and_export_gameplay", corte),
            mock.patch.object(g, "HISTORICO", None), mock.patch.object(g, "STREAMER_NOME", None),
        ]
        for patch in self.patches:
            patch.start()

    def limpar(self):
        for patch in self.patches:
            patch.stop()
        os.chdir(self.cwd)
        self._tmp.cleanup()

    def rodar(self, *args, url=None):
        saida = io.StringIO()
        with mock.patch.object(sys, "argv", ["gerar_cortes.py", url or self.URL, "--audio", "gameplay", *args]), \
                contextlib.redirect_stdout(saida):
            try:
                g.main()
                codigo = 0
            except SystemExit as erro:
                codigo = erro.code or 0
        self.saida = saida.getvalue()
        return codigo

    def meta(self):
        (arquivo,) = sorted(Path("videos_longos").glob("*.json"))
        return json.loads(arquivo.read_text(encoding="utf-8"))


class TestSegundoLinkNoFluxo(Cenario, unittest.TestCase):
    def setUp(self):
        self.preparar()

    def tearDown(self):
        self.limpar()

    def test_os_momentos_dos_dois_streamers_se_intercalam(self):
        self.assertEqual(self.rodar("--cortes", "2", "--video-longo", "6", "--segundo-link", URL_B), 0)
        meta = self.meta()
        self.assertEqual([m["fonte_n"] for m in meta["momentos"]][:4], [1, 2, 1, 2])
        self.assertEqual({m["streamer"] for m in meta["momentos"]}, {"Canal A", "Canal B"})
        self.assertEqual(meta["streamer"], "Canal A e Canal B")
        self.assertEqual(meta["titulo"], "Melhores momentos de Canal A e Canal B")
        self.assertEqual([(f["n"], f["streamer"], f["url"]) for f in meta["fontes"]],
                         [(1, "Canal A", self.URL), (2, "Canal B", URL_B)])
        self.assertEqual(sum(f["momentos"] for f in meta["fontes"]), len(meta["momentos"]))
        self.assertGreaterEqual(meta["duracao_final_s"], 360)
        entradas = {c[c.index("-i") + 1] for c in self.falso.chamadas if "-ss" in c}
        self.assertEqual(entradas, {str(self.video1), str(self.video2)})
        self.assertIn("intercalados", self.saida)

    def test_os_cortes_curtos_saem_so_do_primeiro_video(self):
        self.rodar("--cortes", "3", "--video-longo", "4", "--segundo-link", URL_B)
        self.assertEqual(len(self.cortes_de), 3)
        self.assertEqual(set(self.cortes_de), {self.video1})

    def test_o_nome_do_segundo_streamer_informado_vence_o_do_canal(self):
        self.rodar("--cortes", "0", "--video-longo", "6", "--segundo-link", URL_B, "--streamer2", "Beto Gamer")
        meta = self.meta()
        self.assertEqual({m["streamer"] for m in meta["momentos"] if m["fonte_n"] == 2}, {"Beto Gamer"})

    def test_so_o_video_longo_com_zero_cortes_e_dois_links(self):
        self.assertEqual(self.rodar("--cortes", "0", "--video-longo", "6", "--segundo-link", URL_B), 0)
        self.assertEqual(self.cortes_de, [])
        self.assertEqual(len(list(Path("videos_longos").glob("*.json"))), 1)

    def test_segundo_video_que_nao_baixa_cai_para_so_o_primeiro(self):
        self.baixar_segundo = False
        self.assertEqual(self.rodar("--cortes", "2", "--video-longo", "4", "--segundo-link", URL_B), 0)
        self.assertEqual(len(self.cortes_de), 2)                                   # os cortes já estavam salvos
        meta = self.meta()
        self.assertTrue(all("fonte_n" not in m for m in meta["momentos"]))
        self.assertNotIn("fontes", meta)
        self.assertIn("usa só o primeiro", self.saida)

    def test_segundo_video_curto_demais_e_ignorado(self):
        self.duracao2 = 60.0
        self.rodar("--cortes", "2", "--video-longo", "4", "--segundo-link", URL_B)
        self.assertNotIn("fontes", self.meta())
        self.assertIn("mais de 90s", self.saida)

    def test_erro_inesperado_no_segundo_video_nao_derruba_nada(self):
        original = g.build_gameplay_candidates
        chamadas = []

        def quebra(segs, dur):
            chamadas.append(1)
            if len(chamadas) > 1:                                                  # a 1ª chamada é a análise do primeiro vídeo
                raise RuntimeError("boom")
            return original(segs, dur)
        with mock.patch.object(g, "build_gameplay_candidates", quebra):
            self.assertEqual(self.rodar("--cortes", "2", "--video-longo", "4", "--segundo-link", URL_B), 0)
        self.assertEqual(len(self.cortes_de), 2)
        self.assertNotIn("fontes", self.meta())
        self.assertIn("Erro no segundo vídeo", self.saida)

    def test_segundo_link_igual_ao_primeiro_e_ignorado(self):
        self.rodar("--cortes", "2", "--video-longo", "4", "--segundo-link", self.URL + "&t=30s")
        self.assertEqual(self.baixados, [self.URL])                                # só baixou o primeiro
        self.assertIn("mesmo vídeo", self.saida)
        self.assertNotIn("fontes", self.meta())

    def test_segundo_link_sem_video_longo_e_ignorado(self):
        self.assertEqual(self.rodar("--cortes", "2", "--segundo-link", URL_B), 0)
        self.assertEqual(self.baixados, [self.URL])
        self.assertIn("só vale com --video-longo", self.saida)
        self.assertFalse(Path("videos_longos").exists())

    def test_segundo_link_invalido_e_recusado_antes_de_baixar(self):
        for ruim in ("ftp://x.com/v", "nao e link", "javascript:alert(1)"):
            self.baixados.clear()
            self.assertEqual(self.rodar("--cortes", "2", "--video-longo", "4", "--segundo-link", ruim), 1, ruim)
            self.assertEqual(self.baixados, [])

    def test_sem_segundo_link_tudo_continua_como_antes(self):
        self.rodar("--cortes", "2", "--video-longo", "4")
        meta = self.meta()
        self.assertEqual(self.baixados, [self.URL])
        self.assertTrue(all("fonte_n" not in m for m in meta["momentos"]))
        self.assertNotIn("fontes", meta)

    def test_a_resolucao_do_primeiro_video_nao_e_trocada_pelo_segundo(self):
        def baixar(url, prefixo):
            g.FONTE_RESOLUCAO = "1920x1080" if "AAAA" in url else "640x360"      # o download real grava a resolução aqui
            return (self.video2 if "BBBB" in url else self.video1), None
        with mock.patch.object(g, "download_video", baixar):
            self.rodar("--cortes", "0", "--video-longo", "6", "--segundo-link", URL_B)
        self.assertEqual(self.meta()["fonte_resolucao"], "1920x1080")

    def test_o_help_lista_as_opcoes(self):
        saida = io.StringIO()
        with mock.patch.object(sys, "argv", ["gerar_cortes.py", "--help"]), contextlib.redirect_stdout(saida), \
                self.assertRaises(SystemExit):
            g.main()
        self.assertIn("--segundo-link", saida.getvalue())
        self.assertIn("--streamer2", saida.getvalue())


class TestSegundoLinkNoServidor(Base):
    def args(self, **extra):
        status, _ = self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 3, "video_longo": 8, **extra})
        texto = "\n".join(self.esperar_fim()["linhas"]).split("ARGS:")[-1] if status == 201 else ""
        return status, texto

    def test_vira_flag_com_igual_e_o_nome_do_segundo_streamer(self):
        status, texto = self.args(segundo_link=URL_B, streamer2="  Beto   Gamer ")
        self.assertEqual(status, 201)
        self.assertIn(f"'--segundo-link={URL_B}'", texto)
        self.assertIn("'--streamer2=Beto Gamer'", texto)

    def test_sem_segundo_link_nada_e_enviado(self):
        for vazio in (None, "", "   "):
            status, texto = self.args(segundo_link=vazio, streamer2="Beto")
            self.assertEqual(status, 201)
            self.assertNotIn("--segundo-link", texto)
            self.assertNotIn("--streamer2", texto)

    def test_exige_o_video_longo(self):
        status, d = self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 3, "segundo_link": URL_B})
        self.assertEqual(status, 400)
        self.assertIn("vídeo longo", d["erro"])

    def test_recusa_link_ruim_igual_ao_primeiro_e_tipos_errados(self):
        ruins = ["ftp://x.com/v", "texto solto", "-oops", URL_OK, URL_OK + "/", 12, ["https://x.com/v"], {"a": 1}, "https://x.com/" + "a" * 2000]
        for ruim in ruins:
            status, d = self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 3, "video_longo": 8, "segundo_link": ruim})
            self.assertEqual(status, 400, ruim)
        for ruim in (12, ["x"], "a" * 61, "nome\x00ruim"):
            status, _ = self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 3, "video_longo": 8,
                                                            "segundo_link": URL_B, "streamer2": ruim})
            self.assertEqual(status, 400, ruim)

    def test_fora_do_gameplay_o_segundo_link_e_ignorado(self):
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "original", "segundo_link": URL_B})
        self.assertNotIn("--segundo-link", "\n".join(self.esperar_fim()["linhas"]).split("ARGS:")[-1])

    def test_recursos_novos_aparecem_no_estado(self):
        recursos = self.json("GET", "/api/estado")[1]["recursos"]
        for r in ("segundo_link", "descricao", "reedicao"):
            self.assertIn(r, recursos)


class TestListaComDoisStreamers(BaseLongos):
    def test_card_mostra_as_fontes_e_o_streamer_de_cada_momento(self):
        momentos = [{"n": 1, "inicio": 0.0, "fim": 40.0, "titulo": "UM", "streamer": "Ana", "fonte_n": 1},
                    {"n": 2, "inicio": 40.0, "fim": 80.0, "titulo": "DOIS", "streamer": "Beto", "fonte_n": 2}]
        fontes = [{"n": 1, "streamer": "Ana", "url": "https://a", "momentos": 1}, {"n": 2, "streamer": "Beto", "url": "https://b", "momentos": 1},
                  {"n": 3, "streamer": "Extra"}, "lixo"]
        self.criar_longo(meta={"momentos": momentos, "fontes": fontes, "streamer": "Ana e Beto"})
        (v,) = self.json("GET", "/api/longos")[1]["longos"]
        self.assertEqual([m["streamer"] for m in v["momentos"]], ["Ana", "Beto"])
        self.assertEqual([(f["n"], f["streamer"], f["momentos"]) for f in v["fontes"]], [(1, "Ana", 1), (2, "Beto", 1)])   # no máximo 2

    def test_vídeo_de_uma_fonte_so_nao_tem_fontes(self):
        self.criar_longo()
        (v,) = self.json("GET", "/api/longos")[1]["longos"]
        self.assertEqual(v["fontes"], [])
        self.assertEqual(v["momentos"][0]["streamer"], "")


# ============================================================================================ 2. descrições
def resposta(*textos):
    return json.dumps([{"descricao": t} for t in textos], ensure_ascii=False)


class TestMotorDeDescricoes(unittest.TestCase):
    def test_clean_description(self):
        self.assertEqual(g.clean_description("Linha 1\n\n\n\nLinha   2 \x00 #tag\r\n"), "Linha 1\n\nLinha 2 #tag")
        self.assertEqual(g.clean_description("  \n\n  abc "), "abc")
        self.assertEqual(g.clean_description(None), "")
        self.assertEqual(len(g.clean_description("x" * 5000)), g.DESCRICAO_MAX_CORTE)
        self.assertEqual(len(g.clean_description("x" * 5000, 100)), 100)
        self.assertIn("#games", g.clean_description("fala #games"))                # hashtags ficam

    def test_sugestoes_do_corte_limpas_sem_repetir_e_na_quantidade(self):
        texto = "Texto bem legal sobre o susto no baú #live #susto"
        with mock.patch.object(g, "groq_chat_with_fallback",
                               return_value=resposta(texto, texto.upper(), "curta", "Outra descrição legal #a #b", 5, "Terceira opção ok #c #d", "Quarta opção boa #e #f")):
            opcoes = g.suggest_descriptions("fala real do corte", "SUSTO", "KEY")
        self.assertEqual([o["descricao"] for o in opcoes], [texto, "Outra descrição legal #a #b", "Terceira opção ok #c #d"])

    def test_nao_repete_a_descricao_atual(self):
        atual = "Descrição que já estava salva #live"
        with mock.patch.object(g, "groq_chat_with_fallback", return_value=resposta(atual, "Nova descrição boa #live #x")):
            opcoes = g.suggest_descriptions("fala", "T", "KEY", current=atual)
        self.assertEqual([o["descricao"] for o in opcoes], ["Nova descrição boa #live #x"])

    def test_o_prompt_do_corte_so_manda_o_que_foi_dito(self):
        enviados = []

        def fake(msgs, *a, **k):
            enviados.append(msgs[0]["content"])
            return resposta("Uma descrição suficientemente longa #x")
        with mock.patch.object(g, "groq_chat_with_fallback", fake):
            g.suggest_descriptions("o streamer abriu o baú e gritou", "BAÚ", "KEY", streamer="Ana")
        prompt = enviados[0]
        self.assertIn("o streamer abriu o baú e gritou", prompt)
        self.assertIn("NÃO invente", prompt)
        self.assertIn("Ana", prompt)

    def test_sem_texto_sem_chave_ou_resposta_ruim_devolve_vazio(self):
        with mock.patch.object(g, "groq_chat_with_fallback", return_value=resposta("Descrição boa o bastante #a")) as chamada:
            self.assertEqual(g.suggest_descriptions("", "T", "KEY"), [])
            self.assertEqual(g.suggest_descriptions("fala", "T", None), [])
            chamada.assert_not_called()
        for ruim in (None, "", "lixo", '{"descricao": "x"}', "[1, 2]"):
            with mock.patch.object(g, "groq_chat_with_fallback", return_value=ruim):
                self.assertEqual(g.suggest_descriptions("fala", "T", "KEY"), [], ruim)

    MOMENTOS = [{"n": 1, "titulo": "SUSTO NO BAÚ", "motivo": "grito", "streamer": "Ana"},
                {"n": 2, "titulo": "RISADA", "motivo": "piada", "streamer": "Beto"},
                {"n": 3, "titulo": "", "motivo": "sem título"}]

    def test_prompt_do_longo_lista_momentos_e_streamers_e_proibe_marcas_de_tempo(self):
        enviados = []

        def fake(msgs, *a, **k):
            enviados.append(msgs[0]["content"])
            return resposta("Os melhores momentos dos dois #live #games")
        with mock.patch.object(g, "groq_chat_with_fallback", fake):
            opcoes = g.suggest_long_descriptions(self.MOMENTOS, "Título atual", "KEY")
        prompt = enviados[0]
        self.assertIn("SUSTO NO BAÚ (grito) [Ana]", prompt)
        self.assertIn("RISADA (piada) [Beto]", prompt)
        self.assertIn("Streamers (pode citar estes nomes, nenhum outro): Ana, Beto", prompt)
        self.assertNotIn("sem título", prompt)                                    # momento sem título não entra
        self.assertIn("NÃO escreva marcas de tempo", prompt)
        self.assertEqual(len(opcoes), 1)

    def test_prompt_do_longo_de_um_streamer_so_nao_etiqueta_cada_momento(self):
        enviados = []
        with mock.patch.object(g, "groq_chat_with_fallback", lambda m, *a, **k: enviados.append(m[0]["content"]) or resposta("Descrição ok demais #x")):
            g.suggest_long_descriptions([{"n": 1, "titulo": "UM", "streamer": "Ana"}], "", "KEY")
        self.assertIn("1. UM\n", enviados[0])
        self.assertNotIn("[Ana]", enviados[0])

    def test_longo_sem_momentos_com_titulo_devolve_vazio(self):
        with mock.patch.object(g, "groq_chat_with_fallback") as chamada:
            self.assertEqual(g.suggest_long_descriptions([{"n": 1, "titulo": ""}], "T", "KEY"), [])
            self.assertEqual(g.suggest_long_descriptions([], "T", "KEY"), [])
            self.assertEqual(g.suggest_long_descriptions([{"n": 1, "titulo": "A"}], "T", None), [])
            chamada.assert_not_called()


class TestCapitulos(unittest.TestCase):
    MOMENTOS = [{"n": 1, "inicio": 0.0, "fim": 40.0, "titulo": "SUSTO NO BAÚ"},
                {"n": 2, "inicio": 40.0, "fim": 80.0, "titulo": "RISADA"},
                {"n": 3, "inicio": 80.0, "fim": 125.0, "titulo": "FINAL ÉPICO"}]

    def test_marcas_no_formato_do_youtube(self):
        caps = longos.capitulos(self.MOMENTOS, None, 125.0)
        self.assertEqual(longos.capitulos_para_descricao(caps), "0:00 SUSTO NO BAÚ\n0:40 RISADA\n1:20 FINAL ÉPICO")

    def test_o_resumo_do_inicio_empurra_os_tempos_e_ganha_uma_abertura(self):
        caps = longos.capitulos(self.MOMENTOS, None, 125.0)
        linhas = longos.capitulos_para_descricao(caps, 8.4).splitlines()
        self.assertEqual(linhas, ["0:00 Abertura", "0:08 SUSTO NO BAÚ", "0:48 RISADA", "1:28 FINAL ÉPICO"])

    def test_precisa_de_pelo_menos_tres_capitulos(self):
        caps = longos.capitulos(self.MOMENTOS[:2], None, 80.0)
        self.assertEqual(longos.capitulos_para_descricao(caps), "")
        self.assertEqual(longos.capitulos_para_descricao([]), "")
        self.assertEqual(longos.capitulos_para_descricao(None), "")

    def test_primeiro_capitulo_depois_de_zero_ganha_inicio(self):
        momentos = [{**m, "inicio": m["inicio"] + 30, "fim": m["fim"] + 30} for m in self.MOMENTOS]
        caps = longos.capitulos(momentos, None, 160.0)
        self.assertEqual(longos.capitulos_para_descricao(caps).splitlines()[0], "0:00 Início")

    def test_mais_de_uma_hora_usa_h_mm_ss(self):
        caps = [{"inicio": 0.0, "titulo": "A"}, {"inicio": 3599.0, "titulo": "B"}, {"inicio": 3725.0, "titulo": "C"}]
        self.assertEqual(longos.capitulos_para_descricao(caps).splitlines()[1:], ["59:59 B", "1:02:05 C"])

    def test_linhas_do_video_bruto_e_do_editado(self):
        bruto = {"momentos": self.MOMENTOS, "clip_duration": 125.0, "edicao": {"estado": "bruto"}}
        self.assertEqual(g.long_chapter_lines(bruto), "0:00 SUSTO NO BAÚ\n0:40 RISADA\n1:20 FINAL ÉPICO")
        editado = {"momentos": self.MOMENTOS, "clip_duration": 125.0, "duracao_final_s": 125.0 + 10.0 + 6.0,
                   "edicao": {"estado": "renderizado"}, "resumo_intro": {"duracao_s": 10.0}, "cta_final": {"duracao_s": 6.0}}
        self.assertEqual(g.long_chapter_lines(editado).splitlines(),
                         ["0:00 Abertura", "0:10 SUSTO NO BAÚ", "0:50 RISADA", "1:30 FINAL ÉPICO"])

    def test_linhas_com_trechos_cortados_seguem_a_linha_do_tempo_nova(self):
        # tirou os 40 s do meio (o momento 2 inteiro): sobram dois capítulos, então não há marcas suficientes
        meta = {"momentos": self.MOMENTOS, "clip_duration": 125.0, "duracao_final_s": 85.0, "edicao": {"estado": "renderizado"},
                "trechos_mantidos": [[0.0, 40.0], [80.0, 125.0]]}
        self.assertEqual(g.long_chapter_lines(meta), "")
        meta["momentos"] = self.MOMENTOS + [{"n": 4, "inicio": 125.0, "fim": 160.0, "titulo": "EXTRA"}]
        meta.update({"clip_duration": 160.0, "duracao_final_s": 120.0, "trechos_mantidos": [[0.0, 40.0], [80.0, 160.0]]})
        self.assertEqual(g.long_chapter_lines(meta).splitlines(), ["0:00 SUSTO NO BAÚ", "0:40 FINAL ÉPICO", "1:25 EXTRA"])

    def test_sem_duracao_ou_momentos_devolve_vazio(self):
        self.assertEqual(g.long_chapter_lines({}), "")
        self.assertEqual(g.long_chapter_lines({"momentos": "x", "clip_duration": 100}), "")


class TestModoLinhaDeComandoDescricoes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(os.chdir, self.cwd)
        for p in (mock.patch.object(g, "load_env_file", lambda: None), mock.patch.object(g, "get_groq_key", lambda: "KEY")):
            p.start()
            self.addCleanup(p.stop)
        Path("cortes_prontos").mkdir()
        Path("videos_longos").mkdir()

    def rodar(self, nome, biblioteca="cortes"):
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            ok = g.suggest_descriptions_for_clip(nome, biblioteca)
        return ok, saida.getvalue()

    def opcoes(self, texto):
        (linha,) = [l for l in texto.splitlines() if l.startswith("DESCRICOES_JSON=")]
        return json.loads(linha[len("DESCRICOES_JSON="):])

    def test_corte_imprime_o_json(self):
        Path("cortes_prontos/c.json").write_text(json.dumps({"transcricao": "fala do corte", "titulo_gancho": "T"}), encoding="utf-8")
        with mock.patch.object(g, "groq_chat_with_fallback", return_value=resposta("Descrição nova bem boa #live #susto")):
            ok, texto = self.rodar("c.mp4")
        self.assertTrue(ok)
        self.assertEqual(self.opcoes(texto), [{"descricao": "Descrição nova bem boa #live #susto"}])

    def test_longo_acrescenta_os_capitulos_reais_depois_do_texto_da_ia(self):
        meta = {"origem": "longo", "titulo": "Melhores", "momentos": TestCapitulos.MOMENTOS, "clip_duration": 125.0,
                "edicao": {"estado": "bruto"}}
        Path("videos_longos/v.json").write_text(json.dumps(meta), encoding="utf-8")
        with mock.patch.object(g, "groq_chat_with_fallback", return_value=resposta("Os melhores momentos da live #live #games")):
            ok, texto = self.rodar("v.mp4", "longos")
        self.assertTrue(ok)
        (opcao,) = self.opcoes(texto)
        self.assertEqual(opcao["descricao"], "Os melhores momentos da live #live #games\n\n0:00 SUSTO NO BAÚ\n0:40 RISADA\n1:20 FINAL ÉPICO")

    def test_o_tempo_nunca_vem_da_ia(self):
        meta = {"origem": "longo", "momentos": TestCapitulos.MOMENTOS, "clip_duration": 125.0}
        Path("videos_longos/v.json").write_text(json.dumps(meta), encoding="utf-8")
        with mock.patch.object(g, "groq_chat_with_fallback", return_value=resposta("Texto da IA com tempo falso 9:99 aqui #x")):
            _, texto = self.rodar("v.mp4", "longos")
        self.assertIn("9:99", self.opcoes(texto)[0]["descricao"])                 # o texto da IA passa como veio...
        self.assertIn("\n0:40 RISADA", self.opcoes(texto)[0]["descricao"])        # ...e as marcas reais vêm do código

    def test_falhas(self):
        self.assertFalse(self.rodar("../c.mp4")[0])
        self.assertFalse(self.rodar("_c.mp4")[0])
        self.assertFalse(self.rodar("c.txt")[0])
        self.assertIn("Não encontrei", self.rodar("c.mp4")[1])
        Path("cortes_prontos/sem.json").write_text(json.dumps({"titulo_gancho": "T"}), encoding="utf-8")
        self.assertIn("transcrição", self.rodar("sem.mp4")[1])
        Path("videos_longos/sem.json").write_text(json.dumps({"origem": "longo"}), encoding="utf-8")
        self.assertIn("momentos", self.rodar("sem.mp4", "longos")[1])
        Path("cortes_prontos/c.json").write_text(json.dumps({"transcricao": "fala"}), encoding="utf-8")
        with mock.patch.object(g, "groq_chat_with_fallback", return_value=None):
            ok, texto = self.rodar("c.mp4")
        self.assertFalse(ok)
        self.assertIn("não devolveu sugestões", texto)
        with mock.patch.object(g, "get_groq_key", lambda: None):
            self.assertIn("GROQ_API_KEY", self.rodar("c.mp4")[1])

    def test_flag_existe(self):
        saida = io.StringIO()
        with mock.patch.object(sys, "argv", ["gerar_cortes.py", "--help"]), contextlib.redirect_stdout(saida), self.assertRaises(SystemExit):
            g.main()
        self.assertIn("--sugerir-descricoes", saida.getvalue())


class TestRenderNaoApagaADescricao(unittest.TestCase):
    def test_descricao_salva_durante_a_renderizacao_e_mantida(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "c.json"
            caminho.write_text(json.dumps({"titulo_gancho": "T", "descricao": "salva enquanto renderizava"}), encoding="utf-8")
            meta = {"titulo_gancho": "T", "descricao": "texto antigo", "bruto": False}
            g._manter_descricao(caminho, meta)
            self.assertEqual(meta["descricao"], "salva enquanto renderizava")
            caminho.write_text(json.dumps({"titulo_gancho": "T"}), encoding="utf-8")          # apagada enquanto renderizava
            g._manter_descricao(caminho, meta)
            self.assertNotIn("descricao", meta)
            caminho.write_text("{quebrado", encoding="utf-8")                                 # ilegível: não mexe
            meta["descricao"] = "x"
            g._manter_descricao(caminho, meta)
            self.assertEqual(meta["descricao"], "x")
            g._manter_descricao(Path(tmp) / "nao_existe.json", meta)
            self.assertEqual(meta["descricao"], "x")


GERAR_DESCRICOES = textwrap.dedent(r'''
    import json, os, sys, time
    print("ARGS:", sys.argv[1:], flush=True)
    if "--sugerir-descricoes" in sys.argv:
        modo = open("modo.txt", encoding="utf-8").read().strip() if os.path.exists("modo.txt") else "ok"
        if modo == "erro":
            print("❌ A IA não devolveu sugestões agora.", flush=True)
            sys.exit(1)
        if modo == "lento":
            time.sleep(60)
        print("DESCRICOES_JSON=" + json.dumps([{"descricao": "Linha 1\n\n\n\nLinha 2   com  espaços #tag"}, {"descricao": ""},
                                               {"descricao": "y" * 6000}, {"x": 1}, "texto solto", {"descricao": 5}], ensure_ascii=False), flush=True)
        sys.exit(0)
''')


class BaseDescricao(BaseLongos):
    NOME = "corte_gameplay_1_TESTE.mp4"

    def setUp(self):
        super().setUp()
        (self.raiz / "gerar_cortes.py").write_text(GERAR_DESCRICOES, encoding="utf-8")
        self.criar_corte(self.NOME, 600, meta={"titulo_gancho": "T", "transcricao": "fala do corte", "nota": 80})
        self.criar_longo()

    def ler_meta(self, longo=False):
        arq = (self.pasta / NOME_LONGO) if longo else (self.saida / self.NOME)
        return json.loads(arq.with_suffix(".json").read_text(encoding="utf-8"))


class TestSugerirDescricoesNoServidor(BaseDescricao):
    def sugerir(self, nome=None, lib="cortes"):
        return self.json("POST", f"/api/{lib}/descricoes", {"nome": nome or (self.NOME if lib == "cortes" else NOME_LONGO)})

    def test_devolve_as_opcoes_limpas_para_corte_e_para_longo(self):
        status, d = self.sugerir()
        self.assertEqual(status, 200)
        textos = [o["descricao"] for o in d["descricoes"]]
        self.assertEqual(textos[0], "Linha 1\n\nLinha 2 com espaços #tag")
        self.assertEqual(len(textos), 2)                                              # vazios, tipos errados e lixo caem fora
        self.assertEqual(len(textos[1]), servidor.DESCRICAO_MAX_CORTE)
        status, d = self.sugerir(lib="longos")
        self.assertEqual(status, 200)
        self.assertEqual(len(d["descricoes"][1]["descricao"]), servidor.DESCRICAO_MAX_LONGO)

    def test_a_flag_e_a_biblioteca_chegam_ao_gerar_cortes(self):
        self.sugerir()
        self.sugerir(lib="longos")
        # o fake imprime os argumentos, mas a rota só devolve o JSON: confere pelo comando montado
        comandos = []
        original = servidor.subprocess.run

        def espia(cmd, *a, **k):
            comandos.append(cmd[2:])
            return original(cmd, *a, **k)
        with mock.patch.object(servidor.subprocess, "run", espia):
            self.sugerir()
            self.sugerir(lib="longos")
        self.assertEqual(comandos, [["--sugerir-descricoes", self.NOME, "--biblioteca", "cortes"],
                                    ["--sugerir-descricoes", NOME_LONGO, "--biblioteca", "longos"]])

    def test_erros_viram_mensagem_para_a_tela(self):
        self.assertEqual(self.sugerir("nao_existe.mp4")[0], 400)
        self.assertEqual(self.sugerir("../x.mp4")[0], 400)
        self.assertEqual(self.sugerir("nao_existe.mp4", "longos")[0], 400)
        self.criar_corte("sem_texto.mp4", 600, meta={"titulo_gancho": "T"})
        status, d = self.sugerir("sem_texto.mp4")
        self.assertEqual(status, 400)
        self.assertIn("transcrição", d["erro"])
        self.criar_longo("sem_momentos.mp4", meta={"momentos": []})
        status, d = self.sugerir("sem_momentos.mp4", "longos")
        self.assertEqual(status, 400)
        self.assertIn("momentos", d["erro"])
        (self.raiz / ".env").write_text("GROQ_API_KEY=\n", encoding="utf-8")
        status, d = self.sugerir()
        self.assertEqual(status, 400)
        self.assertIn("GROQ_API_KEY", d["erro"])
        self.assertIn("descrições", d["erro"])

    def test_falha_da_ia_e_demora_viram_mensagem(self):
        (self.raiz / "modo.txt").write_text("erro", encoding="utf-8")
        status, d = self.sugerir()
        self.assertEqual(status, 400)
        self.assertIn("A IA não devolveu sugestões", d["erro"])
        (self.raiz / "modo.txt").write_text("lento", encoding="utf-8")
        antigo = servidor.SUGERIR_TIMEOUT_S
        servidor.SUGERIR_TIMEOUT_S = 1
        self.addCleanup(setattr, servidor, "SUGERIR_TIMEOUT_S", antigo)
        status, d = self.sugerir()
        self.assertEqual(status, 400)
        self.assertIn("demorou demais", d["erro"])

    def test_seguranca(self):
        for rota in ("/api/cortes/descricoes", "/api/longos/descricoes", "/api/cortes/descricao", "/api/longos/descricao"):
            self.assertEqual(self.json("POST", rota, {"nome": self.NOME}, cookie=False)[0], 403, rota)
            self.assertEqual(self.json("POST", rota, {"nome": self.NOME}, csrf=False)[0], 403, rota)

    def test_os_titulos_continuam_funcionando_depois_da_refatoracao(self):
        (self.raiz / "gerar_cortes.py").write_text(GERAR_DESCRICOES + textwrap.dedent(r'''
            if "--sugerir-titulos" in sys.argv:
                print("TITULOS_JSON=" + json.dumps([{"titulo": "BOMBA", "pergunta": "Abriria?"}, {"titulo": " "}]), flush=True)
        '''), encoding="utf-8")
        self.assertEqual(self.json("POST", "/api/cortes/titulos", {"nome": self.NOME})[1]["titulos"], [{"titulo": "BOMBA", "pergunta": "Abriria?"}])
        self.assertEqual(self.json("POST", "/api/longos/titulos", {"nome": NOME_LONGO})[1]["titulos"], [{"titulo": "BOMBA", "pergunta": "Abriria?"}])


class TestSalvarDescricao(BaseDescricao):
    def salvar(self, texto, lib="cortes", nome="padrao"):
        if nome == "padrao":
            nome = self.NOME if lib == "cortes" else NOME_LONGO
        return self.json("POST", f"/api/{lib}/descricao", {"nome": nome, "descricao": texto})

    def test_salva_no_json_e_aparece_na_lista(self):
        status, d = self.salvar("  Minha   descrição\n\n\n\nsegunda linha #live  ")
        self.assertEqual((status, d["descricao"]), (200, "Minha descrição\n\nsegunda linha #live"))
        self.assertEqual(self.ler_meta()["descricao"], "Minha descrição\n\nsegunda linha #live")
        self.assertEqual(self.ler_meta()["transcricao"], "fala do corte")                 # o resto do .json fica como estava
        (c,) = self.json("GET", "/api/cortes")[1]["cortes"]
        self.assertEqual(c["descricao"], "Minha descrição\n\nsegunda linha #live")

    def test_longo_salva_e_lista(self):
        texto = "Os melhores momentos #live\n\n0:00 UM\n0:40 DOIS"
        self.assertEqual(self.salvar(texto, "longos")[1]["descricao"], texto)
        self.assertEqual(self.ler_meta(True)["descricao"], texto)
        self.assertEqual(self.ler_meta(True)["momentos"][0]["titulo"], "SUSTO <b>NO</b> BAU")
        (v,) = self.json("GET", "/api/longos")[1]["longos"]
        self.assertEqual(v["descricao"], texto)

    def test_texto_vazio_apaga(self):
        self.salvar("algo")
        self.assertEqual(self.salvar("   \n  ")[1]["descricao"], "")
        self.assertNotIn("descricao", self.ler_meta())
        self.assertEqual(self.json("GET", "/api/cortes")[1]["cortes"][0]["descricao"], "")

    def test_limite_de_tamanho_e_caracteres_de_controle(self):
        self.assertEqual(len(self.salvar("x" * 5000)[1]["descricao"]), servidor.DESCRICAO_MAX_CORTE)
        self.assertEqual(len(self.salvar("x" * 9000, "longos")[1]["descricao"]), servidor.DESCRICAO_MAX_LONGO)
        self.assertEqual(self.salvar("a\x00b\x07c\x7fd")[1]["descricao"], "abcd")

    def test_tipo_errado_e_nome_ruim(self):
        for ruim in (5, ["x"], {"a": 1}, True):
            self.assertEqual(self.salvar(ruim)[0], 400, ruim)
        self.assertEqual(self.json("POST", "/api/cortes/descricao", {"nome": self.NOME})[1]["descricao"], "")   # ausente = apagar
        for nome in ("nao_existe.mp4", "../x.mp4", "_x.mp4", "", None, 5):
            self.assertEqual(self.salvar("x", nome=nome)[0], 400, nome)
        self.assertEqual(self.salvar("x", "longos", nome="nao_existe.mp4")[0], 400)

    def test_corte_sem_json_nao_e_recriado_so_com_a_descricao(self):
        self.criar_corte("sem_json.mp4", 600)
        status, d = self.salvar("x", nome="sem_json.mp4")
        self.assertEqual(status, 400)
        self.assertIn(".json", d["erro"])
        self.assertFalse((self.saida / "sem_json.json").exists())

    def test_json_ilegivel_nao_e_sobrescrito(self):
        (self.saida / self.NOME).with_suffix(".json").write_text("{quebrado", encoding="utf-8")
        self.assertEqual(self.salvar("x")[0], 400)
        self.assertEqual((self.saida / self.NOME).with_suffix(".json").read_text(encoding="utf-8"), "{quebrado")

    def test_nao_deixa_arquivo_temporario(self):
        self.salvar("x")
        self.salvar("y", "longos")
        self.assertEqual([p.name for p in self.saida.iterdir() if p.suffix == ".tmp"], [])
        self.assertEqual([p.name for p in self.pasta.iterdir() if p.name.endswith(".tmp")], [])

    def test_o_card_da_edicao_nao_perde_a_descricao_ao_ser_listado_de_novo(self):
        self.salvar("minha descrição")
        self.json("GET", "/api/cortes")
        self.assertEqual(self.json("GET", "/api/cortes")[1]["cortes"][0]["descricao"], "minha descrição")


# ============================================================================================ 3. tela (sem navegador)
class TestTelaNova(unittest.TestCase):
    HTML = (ESTATICO / "index.html").read_text(encoding="utf-8")
    DESCRICAO = (ESTATICO / "descricao.js").read_text(encoding="utf-8")
    EDITOR = (ESTATICO / "editor.js").read_text(encoding="utf-8")
    APP = (ESTATICO / "app.js").read_text(encoding="utf-8")
    LONGOS = (ESTATICO / "longos.js").read_text(encoding="utf-8")

    def templates(self):
        return {nome: re.search(rf'<template id="{nome}">(.*?)</template>', self.HTML, re.S).group(1) for nome in ("tpl-corte", "tpl-longo")}

    def test_os_dois_cards_tem_o_bloco_de_descricao_com_o_que_o_script_usa(self):
        usadas = set(re.findall(r'querySelector\("\.([a-z\-]+)"\)', self.DESCRICAO))
        self.assertTrue({"descricao", "desc-texto", "desc-sugerir", "desc-salvar", "desc-copiar", "desc-estado", "desc-erro",
                         "desc-sugestoes"} <= usadas | {"descricao"})
        for nome, html in self.templates().items():
            for classe in usadas | {"descricao"}:
                self.assertRegex(html, rf'class="[^"]*\b{classe}\b', f"{nome} sem .{classe}")

    def test_cards_montam_a_descricao_e_trocam_o_rotulo_do_botao(self):
        self.assertIn('descricaoMontar(no, c, "cortes")', self.APP)
        self.assertIn('descricaoMontar(no, v, "longos")', self.LONGOS)
        self.assertIn('textContent = "Reeditar"', self.APP)
        self.assertIn('textContent = "Reeditar"', self.LONGOS)

    def test_o_script_da_descricao_usa_as_rotas_certas_e_nunca_html(self):
        self.assertIn("/api/${lib}/descricoes", self.DESCRICAO)
        self.assertIn("/api/${lib}/descricao`", self.DESCRICAO)
        self.assertNotIn("innerHTML", self.DESCRICAO)
        self.assertNotIn("insertAdjacentHTML", self.DESCRICAO)

    def test_formulario_do_segundo_link(self):
        for id_ in ("segundo-link", "streamer2", "campo-segundo-link", "campo-streamer2"):
            self.assertIn(f'id="{id_}"', self.HTML)
        self.assertRegex(self.HTML, r'id="campo-segundo-link"[^>]*hidden')
        self.assertRegex(self.HTML, r'name="segundo_link"')
        self.assertIn("corpo.segundo_link = segundo", self.APP)
        self.assertIn("corpo.streamer2", self.APP)
        self.assertIn('$("campo-segundo-link").hidden = !ligado', self.APP)

    def test_botao_recomecar_do_bruto(self):
        self.assertRegex(self.HTML, r'id="ed-recomecar"[^>]*hidden')
        self.assertIn('$("ed-recomecar").addEventListener("click"', self.EDITOR)
        self.assertEqual(self.EDITOR.count('$("ed-recomecar").hidden = !op'), 2)          # corte e vídeo longo
        self.assertEqual(self.EDITOR.count("if (edDlg.open) return;"), 2)                  # reabrir sem chamar showModal de novo
        self.assertIn('edicao: null', self.EDITOR)

    def test_titulo_da_janela_diz_reeditar_quando_ja_foi_editado(self):
        self.assertIn('${op ? "Reeditar" : "Editar"}: ', self.EDITOR)
        self.assertIn('${op ? "Reeditar" : "Editar"} vídeo longo: ', self.EDITOR)

    def test_a_pagina_exige_os_recursos_novos(self):
        for recurso in ("segundo_link", "descricao", "reedicao"):
            self.assertIn(f'estado.recursos.includes("{recurso}")', self.APP)

    def test_cards_do_longo_mostram_os_dois_streamers(self):
        self.assertIn("v.fontes.length === 2", self.LONGOS)
        self.assertIn("m.streamer", self.LONGOS)
        self.assertIn("m.streamer", self.EDITOR)


if __name__ == "__main__":
    unittest.main()
