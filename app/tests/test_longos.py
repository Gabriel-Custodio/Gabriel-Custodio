"""Testes do vídeo longo (pacote longos/ e a integração no gerar_cortes.py), sem ffmpeg nem rede.

Rodar na pasta do projeto:   python -m unittest discover -s app/tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gerar_cortes as g  # noqa: E402
import longos  # noqa: E402
from longos import montagem, selecao  # noqa: E402
from test_historico import transcricao  # noqa: E402  (fala contínua para um vídeo de teste)


def cand(inicio, dur=40.0, nota=8.0, **extra):
    return {"start": float(inicio), "end": float(inicio + dur), "duration": float(dur), "score": nota,
            "reason": "r", "hook_title": f"T{int(inicio)}", **extra}


class TestSelecao(unittest.TestCase):
    def test_nota_minima_e_regra_do_monitor(self):
        self.assertEqual(selecao.NOTA_MINIMA, 6.5)
        escolhidos, resumo = longos.selecionar([cand(0, nota=6.4), cand(100, nota=6.5), cand(200, nota=9.0)], 240)
        self.assertEqual([c["start"] for c in escolhidos], [100.0, 200.0])
        self.assertEqual(resumo["elegiveis"], 2)

    def test_fecha_o_alvo_com_os_melhores_em_ordem_cronologica(self):
        # 20 momentos de 40 s, a nota cai com o tempo: os primeiros são os melhores
        todos = [cand(i * 100, nota=9.9 - i * 0.1) for i in range(20)]
        escolhidos, resumo = longos.selecionar(todos, 240)
        total = sum(c["duration"] for c in escolhidos)
        self.assertEqual(total, 240.0)                                 # 6 x 40 s: parou ao fechar o alvo
        self.assertEqual([c["start"] for c in escolhidos], [i * 100.0 for i in range(6)])
        self.assertEqual([c["start"] for c in escolhidos], sorted(c["start"] for c in escolhidos))
        self.assertTrue(resumo["viavel"])
        self.assertFalse(resumo["curto"])
        self.assertEqual(resumo["total_s"], 240.0)

    def test_ordem_cronologica_mesmo_com_notas_embaralhadas(self):
        notas = [7.0, 9.0, 8.0, 9.5, 7.5, 8.5]
        todos = [cand(i * 100, nota=n) for i, n in enumerate(notas)]
        escolhidos, _ = longos.selecionar(todos, 480)
        self.assertEqual([c["start"] for c in escolhidos], [0, 100, 200, 300, 400, 500])

    def test_distancia_minima_entre_inicios(self):
        escolhidos, _ = longos.selecionar([cand(0, nota=8.0), cand(30, nota=9.0), cand(200, nota=7.0)], 480)
        self.assertEqual([c["start"] for c in escolhidos], [30.0, 200.0])        # 0 e 30 são vizinhos: fica o melhor
        escolhidos, _ = longos.selecionar([cand(0, nota=8.0), cand(60, nota=9.0)], 480)
        self.assertEqual([c["start"] for c in escolhidos], [0.0, 60.0])          # exatamente 60 s de distância vale

    def test_nunca_sobrepoe(self):
        escolhidos, _ = longos.selecionar([cand(0, dur=100, nota=9.0), cand(70, dur=100, nota=8.0)], 480,
                                          distancia_min_s=10)
        self.assertEqual(len(escolhidos), 1)

    def test_o_ultimo_pode_passar_um_pouco_mas_nao_demais(self):
        # alvo 100 s: um de 90 s não cabe (60 + 90 > 145), o de 45 s cabe (60 + 45 = 105)
        escolhidos, resumo = longos.selecionar(
            [cand(0, dur=60, nota=9.5), cand(200, dur=90, nota=9.0), cand(400, dur=45, nota=8.0)], 100)
        self.assertEqual([c["start"] for c in escolhidos], [0.0, 400.0])
        self.assertEqual(resumo["total_s"], 105.0)

    def test_nunca_fica_abaixo_do_alvo_por_causa_de_um_momento_que_nao_coube(self):
        # alvo 240: 6 x 39 s = 234 não fecha; o 7º (39 s) passa do alvo em 33 s, dentro da folga: o vídeo tem 273 s
        escolhidos, resumo = longos.selecionar([cand(i * 100, dur=39) for i in range(12)], 240)
        self.assertEqual(sum(c["duration"] for c in escolhidos), 273.0)
        self.assertFalse(resumo["curto"])

    def test_pouco_material_sai_mais_curto_e_avisa(self):
        escolhidos, resumo = longos.selecionar([cand(i * 100) for i in range(4)], 480)
        self.assertEqual(len(escolhidos), 4)
        self.assertTrue(resumo["curto"])
        self.assertTrue(resumo["viavel"])
        self.assertIn("mais curto", resumo["motivo"])

    def test_inviavel_com_um_momento_so_ou_nenhum(self):
        for candidatos in ([], [cand(0)], [cand(0, nota=3.0), cand(100, nota=2.0)]):
            escolhidos, resumo = longos.selecionar(candidatos, 480)
            self.assertFalse(resumo["viavel"], candidatos)
            self.assertIn("não dá um vídeo longo", resumo["motivo"])
        _, resumo = longos.selecionar([cand(0, dur=20), cand(100, dur=20)], 480)    # 2 momentos, mas só 40 s
        self.assertFalse(resumo["viavel"])

    def test_entradas_ruins_sao_ignoradas_e_nada_e_alterado(self):
        bons = [cand(0), cand(100)]
        ruins = [{}, {"start": "x", "end": 5, "score": 9}, {"start": 10, "end": 5, "score": 9},
                 {"start": 0, "end": 40, "score": float("nan")}, {"start": -5, "end": 40, "score": 9},
                 {"start": 0, "end": 40, "score": None}]
        copia = json.loads(json.dumps(bons))
        escolhidos, _ = longos.selecionar(ruins + bons, 480)
        self.assertEqual([c["start"] for c in escolhidos], [0.0, 100.0])
        self.assertEqual(json.loads(json.dumps(bons)), copia)                          # os dicts de entrada ficam como estavam
        escolhidos[0]["start"] = 999
        self.assertEqual(bons[0]["start"], 0.0)

    def test_duracao_valida(self):
        self.assertTrue(all(longos.duracao_valida(m) for m in (4, 8, 12)))
        self.assertFalse(any(longos.duracao_valida(m) for m in (3, 13, 0, -4, 8.5, "8", None, True)))


class FfmpegFalso:
    """Imita o run_cmd: cada comando 'corta' (escreve o arquivo de saída); registra o que foi chamado."""

    def __init__(self, falha_momento=(), falha_copia=False, falha_tudo_no_concat=False):
        self.chamadas, self.falha_momento = [], set(falha_momento)
        self.falha_copia, self.falha_tudo_no_concat = falha_copia, falha_tudo_no_concat
        self.duracoes = {}

    def __call__(self, cmd, timeout=None):
        self.chamadas.append(cmd)
        saida = Path(cmd[-1])
        if "concat" in cmd:
            if self.falha_tudo_no_concat or (self.falha_copia and "copy" in cmd):
                return False, "", "erro de junção"
            saida.write_bytes(b"x" * 4096)
            return True, "", ""
        indice = int(saida.stem.split("_")[-1])
        if indice in self.falha_momento:
            return False, "", "erro ao cortar"
        self.duracoes[saida] = float(cmd[cmd.index("-t") + 1])
        saida.write_bytes(b"x" * 4096)
        return True, "", ""

    def medir(self, caminho):
        return self.duracoes.get(Path(caminho), 0.0)


class TestMontagem(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.logs = []

    def tearDown(self):
        self._tmp.cleanup()

    def montar(self, falso, escolhidos=None, destino=None):
        escolhidos = escolhidos or [cand(10, 40), cand(300, 35), cand(900, 45)]
        destino = destino or self.dir / "videos_longos" / "_brutos" / "VideoLongo_X.mp4"
        return longos.montar_bruto(self.dir / "origem.mp4", escolhidos, destino, self.dir / "temp", "ffmpeg", falso,
                                   falso.medir, log=self.logs.append), destino

    def test_monta_e_mede_a_linha_do_tempo(self):
        falso = FfmpegFalso()
        res, destino = self.montar(falso)
        self.assertTrue(destino.is_file())
        self.assertEqual(res["duracao_s"], 120.0)
        self.assertEqual([(m["inicio"], m["fim"]) for m in res["momentos"]], [(0, 40), (40, 75), (75, 120)])
        self.assertEqual([(m["inicio_video"], m["fim_video"]) for m in res["momentos"]], [(10, 50), (300, 335), (900, 945)])
        self.assertEqual([m["n"] for m in res["momentos"]], [1, 2, 3])
        self.assertEqual(res["momentos"][0]["titulo"], "T10")
        self.assertEqual(sorted(p.name for p in (self.dir / "temp").iterdir()), [])        # temporários apagados
        self.assertEqual([p.name for p in destino.parent.iterdir()], [destino.name])        # nada de .tmp sobrando

    def test_cada_momento_sai_padronizado_horizontal(self):
        falso = FfmpegFalso()
        self.montar(falso)
        corte = falso.chamadas[0]
        self.assertEqual(corte[corte.index("-ss") + 1], "10.000")
        self.assertEqual(corte[corte.index("-t") + 1], "40.000")
        filtro = corte[corte.index("-vf") + 1]
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=decrease", filtro)
        self.assertIn("pad=1920:1080", filtro)
        self.assertIn("fps=30", filtro)
        self.assertIn("afade=t=in", corte[corte.index("-af") + 1])
        self.assertLess(corte.index("-ss"), corte.index("-i"))                              # corte rápido: -ss antes da entrada
        for chamada in falso.chamadas:
            self.assertNotIn("-an", chamada)

    def test_juncao_primeiro_copia_depois_recodifica(self):
        falso = FfmpegFalso()
        self.montar(falso)
        juncao = falso.chamadas[-1]
        self.assertEqual(juncao[juncao.index("-c") + 1], "copy")
        self.assertEqual(len([c for c in falso.chamadas if "concat" in c]), 1)
        falso = FfmpegFalso(falha_copia=True)
        res, destino = self.montar(falso)
        self.assertTrue(destino.is_file())
        self.assertIsNotNone(res)
        juncoes = [c for c in falso.chamadas if "concat" in c]
        self.assertEqual(len(juncoes), 2)
        self.assertIn("libx264", juncoes[1])
        self.assertTrue(any("recodificando" in linha for linha in self.logs))

    def test_momento_que_falha_e_pulado_e_os_tempos_continuam_certos(self):
        falso = FfmpegFalso(falha_momento={2})
        res, destino = self.montar(falso)
        self.assertEqual([m["n"] for m in res["momentos"]], [1, 2])
        self.assertEqual([(m["inicio_video"], m["inicio"]) for m in res["momentos"]], [(10, 0), (900, 40)])
        self.assertEqual(res["duracao_s"], 85.0)
        self.assertTrue(any("pulado" in linha for linha in self.logs))

    def test_menos_de_dois_momentos_nao_gera_nada(self):
        falso = FfmpegFalso(falha_momento={1, 3})
        res, destino = self.montar(falso)
        self.assertIsNone(res)
        self.assertFalse(destino.exists())
        self.assertEqual(sorted(p.name for p in (self.dir / "temp").iterdir()), [])

    def test_falha_na_juncao_nao_deixa_arquivo_nem_lixo(self):
        falso = FfmpegFalso(falha_tudo_no_concat=True)
        res, destino = self.montar(falso)
        self.assertIsNone(res)
        self.assertFalse(destino.exists())
        self.assertEqual(list(destino.parent.iterdir()), [])
        self.assertEqual(sorted(p.name for p in (self.dir / "temp").iterdir()), [])

    def test_lista_de_juncao_escapa_aspas(self):
        texto = montagem.texto_da_lista([self.dir / "it's.mp4", self.dir / "b.mp4"])
        linhas = texto.splitlines()
        self.assertTrue(linhas[0].startswith("file '") and "'\\''" in linhas[0])
        self.assertTrue(linhas[1].endswith("b.mp4'"))

    def test_nome_do_video(self):
        from datetime import datetime
        self.assertEqual(longos.nome_do_video(datetime(2026, 11, 25, 14, 30, 5)), "VideoLongo_20261125_143005.mp4")


class TestLegendaEMetadados(unittest.TestCase):
    MOMENTOS = [
        {"n": 1, "inicio_video": 100.0, "fim_video": 140.0, "inicio": 0.0, "fim": 40.0, "duracao": 40.0, "nota": 8.0,
         "motivo": "r", "titulo": "UM"},
        {"n": 2, "inicio_video": 500.0, "fim_video": 540.0, "inicio": 40.0, "fim": 80.0, "duracao": 40.0, "nota": 7.0,
         "motivo": "r", "titulo": "DOIS"},
    ]
    SEGS = [
        {"start": 90.0, "end": 105.0, "text": "antes e dentro", "words": [
            {"word": "antes", "start": 90.0, "end": 95.0}, {"word": "dentro", "start": 100.0, "end": 104.0}]},
        {"start": 110.0, "end": 120.0, "text": "meio"},
        {"start": 200.0, "end": 210.0, "text": "fora de tudo"},
        {"start": 535.0, "end": 550.0, "text": "sai pela borda", "words": [
            {"word": "sai", "start": 535.0, "end": 538.0}, {"word": "borda", "start": 541.0, "end": 545.0}]},
    ]

    def test_legenda_vai_para_a_linha_do_tempo_do_longo(self):
        saida = longos.legenda_dos_momentos(self.SEGS, self.MOMENTOS)
        self.assertEqual([s["text"] for s in saida], ["antes e dentro", "meio", "sai pela borda"])
        primeiro, meio, borda = saida
        self.assertEqual((primeiro["start"], primeiro["end"]), (0.0, 5.0))               # cortado na entrada do momento
        self.assertEqual([(w["word"], w["start"], w["end"]) for w in primeiro["words"]], [("dentro", 0.0, 4.0)])   # "antes" acaba antes do momento
        self.assertEqual((meio["start"], meio["end"]), (10.0, 20.0))
        self.assertEqual((borda["start"], borda["end"]), (40.0 + 35.0, 80.0))               # limitado ao fim do momento
        self.assertTrue(all(0 <= s["start"] < s["end"] <= 80.0 for s in saida))
        self.assertEqual([w["word"] for w in borda["words"]], ["sai"])                       # palavra que passa do fim some

    def test_palavras_ficam_dentro_do_momento(self):
        saida = longos.legenda_dos_momentos(self.SEGS, self.MOMENTOS)
        primeiro = saida[0]
        for w in primeiro["words"]:
            self.assertTrue(0.0 <= w["start"] < w["end"] <= 40.0)
        self.assertIn("dentro", [w["word"] for w in primeiro["words"]])

    def test_metadados(self):
        resumo = {"elegiveis": 9, "escolhidos": 2, "curto": True, "motivo": "aviso", "extra": "ignorado"}
        meta = longos.metadados(self.MOMENTOS, self.SEGS, 80.0, 480, resumo, url="https://x.com/v", video_id="yt_A",
                                streamer="Fulano", fonte_resolucao="1080p")
        json.dumps(meta)                                                                    # tem que virar JSON
        self.assertEqual((meta["origem"], meta["bruto"], meta["edicao"]), ("longo", True, {"estado": "bruto"}))
        self.assertEqual((meta["largura"], meta["altura"], meta["fps"]), (1920, 1080, 30))
        self.assertEqual((meta["duracao_final_s"], meta["clip_duration"], meta["alvo_s"]), (80.0, 80.0, 480.0))
        self.assertEqual(meta["titulo"], "Melhores momentos de Fulano")
        self.assertEqual(meta["selecao"], {"elegiveis": 9, "escolhidos": 2, "curto": True, "motivo": "aviso"})
        self.assertEqual(meta["video_id"], "yt_A")
        self.assertEqual(len(meta["momentos"]), 2)
        self.assertIn("antes e dentro meio", meta["transcricao"])
        self.assertNotIn("fora de tudo", meta["transcricao"])
        self.assertTrue(meta["legenda_segmentos"])
        sem_nome = longos.metadados(self.MOMENTOS, self.SEGS, 80.0, 480, resumo)
        self.assertEqual((sem_nome["titulo"], sem_nome["streamer"]), ("Melhores momentos", None))


def ia_falsa(*args, **kwargs):
    """A IA (simulada) dá nota a todos os candidatos que recebeu: 9,0 para o 1º, caindo 0,3 por posição."""
    prompt = args[0][0]["content"]
    itens = []
    for i in range(1, 13):
        if f"[{i}] " in prompt:
            itens.append({"id": i, "score": round(9.0 - 0.3 * (i - 1), 1), "reason": f"motivo {i}",
                          "hook_title": f"TITULO {i}", "pergunta_cta": "E agora?"})
    return json.dumps(itens)


class TestAnaliseDoVideo(unittest.TestCase):
    def setUp(self):
        self._llm = g.groq_chat_with_fallback
        g.groq_chat_with_fallback = ia_falsa
        self.antes = g.HISTORICO
        g.HISTORICO = None
        self.duracao = 3600.0
        self.segs = transcricao(self.duracao)

    def tearDown(self):
        g.groq_chat_with_fallback = self._llm
        g.HISTORICO = self.antes

    def test_sem_video_longo_e_o_caminho_de_sempre(self):
        antigo = g.find_best_clips_gameplay_ai(self.segs, self.duracao, "KEY", 5)
        clips, pool = g.analyze_gameplay_video(self.segs, self.duracao, "KEY", 5, False)
        self.assertIsNone(pool)
        self.assertEqual([(c["start"], c["end"]) for c in clips], [(c["start"], c["end"]) for c in antigo])

    def test_uma_analise_alimenta_cortes_e_video_longo(self):
        clips, pool = g.analyze_gameplay_video(self.segs, self.duracao, "KEY", 5, True)
        self.assertEqual(len(clips), 5)
        self.assertGreater(len(pool), 30)                                   # muito mais notas que os 12 dos cortes sozinhos
        antigo = g.find_best_clips_gameplay_ai(self.segs, self.duracao, "KEY", 5)
        self.assertGreater(len(pool), len(antigo))
        inicios_pool = {round(c["start"], 1) for c in pool}
        self.assertTrue({round(c["start"], 1) for c in clips} <= inicios_pool)   # os cortes saem do mesmo conjunto
        for c in pool:                                                      # o pool não foi mexido pelo ajuste de duração dos cortes
            self.assertTrue(g.GAMEPLAY_CLIP_DURATION_MIN - 0.01 <= c["duration"] <= g.GAMEPLAY_CLIP_DURATION_MAX + 0.01)
            self.assertAlmostEqual(c["duration"], c["end"] - c["start"], places=3)
            self.assertIn("score", c)

    def test_so_o_video_longo_com_zero_cortes(self):
        clips, pool = g.analyze_gameplay_video(self.segs, self.duracao, "KEY", 0, True)
        self.assertEqual(clips, [])
        escolhidos, resumo = longos.selecionar(pool, 480)
        self.assertTrue(resumo["viavel"])
        self.assertGreaterEqual(resumo["total_s"], 440)

    def test_o_historico_vale_para_os_cortes_mas_nao_para_o_video_longo(self):
        with tempfile.TemporaryDirectory() as tmp:
            g.HISTORICO = g.HistoricoCortes(Path(tmp) / g.HISTORICO_NOME, "yt_V", "https://www.youtube.com/watch?v=V")
            primeiros, pool1 = g.analyze_gameplay_video(self.segs, self.duracao, "KEY", 5, True)
            for c in primeiros:
                g.registrar_corte_no_historico(c["start"], c["end"], "gameplay", "x.mp4")
            segundos, pool2 = g.analyze_gameplay_video(self.segs, self.duracao, "KEY", 5, True)
            usados = {round(c["start"], 1) for c in primeiros}
            self.assertFalse(usados & {round(c["start"], 1) for c in segundos})        # cortes: nada repetido
            self.assertTrue(usados <= {round(c["start"], 1) for c in pool2})           # vídeo longo: pode usar de novo

    def test_sem_chave_o_longo_nao_sai_mas_os_cortes_sim(self):
        clips, pool = g.analyze_gameplay_video(self.segs, self.duracao, None, 3, True)
        self.assertIsNone(pool)
        self.assertEqual(len(clips), 3)
        self.assertEqual(g.analyze_gameplay_video(self.segs, self.duracao, None, 0, True), ([], None))

    def test_ia_fora_do_ar_usa_a_nota_local(self):
        g.groq_chat_with_fallback = lambda *a, **k: None
        clips, pool = g.analyze_gameplay_video(self.segs, self.duracao, "KEY", 3, True)
        self.assertTrue(pool)
        self.assertTrue(all(c["fonte"] == "local" for c in pool))
        self.assertEqual(len(clips), 3)


class TestBuildLongVideo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.dir)
        self.falso = FfmpegFalso()
        self.patches = [mock.patch.object(g, "run_cmd", self.falso), mock.patch.object(g, "get_video_duration", self.falso.medir),
                        mock.patch.object(g, "TEMP_DIR", self.dir / "temp"), mock.patch.object(g, "HISTORICO", None),
                        mock.patch.object(g, "STREAMER_NOME", "Fulano")]
        for p in self.patches:
            p.start()
        self.pool = [cand(i * 120, nota=9.0 - i * 0.1, text=f"fala {i}") for i in range(14)]
        self.segs = [{"start": i * 120 + 1.0, "end": i * 120 + 5.0, "text": f"fala {i}"} for i in range(14)]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.chdir(self.cwd)
        self._tmp.cleanup()

    def test_gera_o_bruto_e_o_json(self):
        saida = g.build_long_video(self.dir / "v.mp4", self.pool, self.segs, 4, "https://www.youtube.com/watch?v=A")
        self.assertEqual(saida.parent, Path("videos_longos"))
        meta = json.loads(saida.read_text(encoding="utf-8"))
        self.assertEqual((meta["origem"], meta["bruto"], meta["streamer"]), ("longo", True, "Fulano"))
        self.assertEqual(meta["video_url"], "https://www.youtube.com/watch?v=A")
        self.assertTrue((Path("videos_longos") / "_brutos" / (saida.stem + ".mp4")).is_file())
        self.assertGreaterEqual(meta["duracao_final_s"], 240)
        self.assertEqual(len(meta["momentos"]), 6)
        self.assertEqual([p.name for p in Path("videos_longos").iterdir() if p.is_file()], [saida.name])   # sem .tmp

    def test_sem_momentos_bons_nao_gera_e_nao_quebra(self):
        fracos = [cand(i * 120, nota=5.0) for i in range(10)]
        self.assertIsNone(g.build_long_video(self.dir / "v.mp4", fracos, self.segs, 8))
        self.assertFalse(Path("videos_longos").exists())

    def test_erro_inesperado_nunca_sobe(self):
        with mock.patch.object(longos, "montar_bruto", side_effect=RuntimeError("boom")):
            self.assertIsNone(g.build_long_video(self.dir / "v.mp4", self.pool, self.segs, 4))

    def test_json_que_nao_grava_remove_o_bruto(self):
        original = Path.write_text

        def falha(self_, *a, **k):
            if self_.name.endswith(".json.tmp"):
                raise OSError("disco cheio")
            return original(self_, *a, **k)
        with mock.patch.object(Path, "write_text", falha):
            self.assertIsNone(g.build_long_video(self.dir / "v.mp4", self.pool, self.segs, 4))
        self.assertEqual(list((Path("videos_longos") / "_brutos").iterdir()), [])


class TestFluxoPrincipal(unittest.TestCase):
    """g.main() inteiro com tudo de fora simulado (download, transcrição, IA, ffmpeg): confere as saídas e o código de saída."""
    URL = "https://www.youtube.com/watch?v=AAAAAAAAAAA"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.cwd = os.getcwd()
        os.chdir(self.dir)
        self.falso = FfmpegFalso()
        self.video = self.dir / "v.mp4"
        self.duracao = 3600.0
        self.segs = transcricao(3600.0)
        self.cortes_feitos = []
        self.chave = "KEY"

        def run_cmd(cmd, cwd=None, timeout=None):
            if "-version" in cmd or "--version" in cmd or "-c" in cmd and "import" in cmd[cmd.index("-c") + 1]:
                return True, "", ""
            return self.falso(cmd, timeout)

        def duracao(caminho):
            return self.duracao if Path(caminho) == self.video else self.falso.medir(caminho)

        def corte(video, clip, segments, saida, num, webcam, chave=None):
            self.cortes_feitos.append((clip["start"], clip["end"]))
            return True

        self.patches = [
            mock.patch.object(g, "resolve_ffmpeg", lambda: True), mock.patch.object(g, "run_cmd", run_cmd),
            mock.patch.object(g, "find_js_runtime", lambda: "node"), mock.patch.object(g, "load_env_file", lambda: None),
            mock.patch.object(g, "get_groq_key", lambda: self.chave), mock.patch.object(g, "download_video", lambda *a: (self.video, None)),
            mock.patch.object(g, "fetch_video_info", lambda *a, **k: {}), mock.patch.object(g, "get_video_duration", duracao),
            mock.patch.object(g, "load_subtitles_with_fallback", lambda *a, **k: (self.segs, "whisper", {})),
            mock.patch.object(g, "groq_chat_with_fallback", ia_falsa), mock.patch.object(g, "cut_and_export_gameplay", corte),
            mock.patch.object(g, "HISTORICO", None), mock.patch.object(g, "STREAMER_NOME", None),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        os.chdir(self.cwd)
        self._tmp.cleanup()

    def rodar(self, *args):
        """Roda o main(); devolve o código de saída (0 se terminou normalmente)."""
        with mock.patch.object(sys, "argv", ["gerar_cortes.py", self.URL, "--audio", "gameplay", *args]):
            try:
                g.main()
            except SystemExit as erro:
                return erro.code or 0
        return 0

    def jsons(self):
        pasta = Path("videos_longos")
        return sorted(pasta.glob("*.json")) if pasta.is_dir() else []

    def test_cortes_e_video_longo_na_mesma_execucao(self):
        self.assertEqual(self.rodar("--cortes", "3", "--video-longo", "4"), 0)
        self.assertEqual(len(self.cortes_feitos), 3)
        (arquivo,) = self.jsons()
        meta = json.loads(arquivo.read_text(encoding="utf-8"))
        self.assertEqual((meta["origem"], meta["bruto"], meta["video_url"]), ("longo", True, self.URL))
        self.assertGreaterEqual(meta["duracao_final_s"], 240)
        self.assertTrue((Path("videos_longos") / "_brutos" / (arquivo.stem + ".mp4")).is_file())
        self.assertFalse(Path("temp_downloads").exists())                      # a limpeza do final continua acontecendo

    def test_so_o_video_longo_com_zero_cortes(self):
        self.assertEqual(self.rodar("--cortes", "0", "--video-longo", "6"), 0)
        self.assertEqual(self.cortes_feitos, [])
        self.assertEqual(len(self.jsons()), 1)

    def test_so_o_longo_que_falha_termina_com_erro(self):
        with mock.patch.object(longos, "montar_bruto", return_value=None):
            self.assertEqual(self.rodar("--cortes", "0", "--video-longo", "4"), 1)
        self.assertEqual(self.jsons(), [])

    def test_longo_que_falha_nao_estraga_os_cortes(self):
        with mock.patch.object(longos, "montar_bruto", return_value=None):
            self.assertEqual(self.rodar("--cortes", "3", "--video-longo", "4"), 0)
        self.assertEqual(len(self.cortes_feitos), 3)                           # os cortes já tinham sido feitos
        self.assertEqual(self.jsons(), [])

    def test_sem_a_opcao_nada_muda_e_nao_cria_a_pasta(self):
        self.assertEqual(self.rodar("--cortes", "3"), 0)
        self.assertEqual(len(self.cortes_feitos), 3)
        self.assertFalse(Path("videos_longos").exists())

    def test_zero_cortes_sem_video_longo_vira_um_corte(self):
        self.assertEqual(self.rodar("--cortes", "0"), 0)                       # sem vídeo longo o mínimo continua 1
        self.assertEqual(len(self.cortes_feitos), 1)

    def test_sem_chave_da_ia_os_cortes_saem_e_o_longo_nao(self):
        self.chave = None
        self.assertEqual(self.rodar("--cortes", "2", "--video-longo", "4"), 0)
        self.assertEqual(len(self.cortes_feitos), 2)
        self.assertEqual(self.jsons(), [])
        self.assertEqual(self.rodar("--cortes", "0", "--video-longo", "4"), 1)    # nada a fazer

    def test_video_curto_nao_gera_longo(self):
        self.duracao = 60.0
        self.segs = [{"start": 1.0, "end": 55.0, "text": "palavra " * 60}]
        self.assertEqual(self.rodar("--cortes", "3", "--video-longo", "4"), 0)
        self.assertEqual(len(self.cortes_feitos), 1)                           # o corte único de sempre
        self.assertEqual(self.jsons(), [])


class TestLinhaDeComando(unittest.TestCase):
    def rodar(self, *args):
        return subprocess.run([sys.executable, str(RAIZ / "gerar_cortes.py"), *args], capture_output=True,
                              text=True, encoding="utf-8", cwd=str(RAIZ), timeout=60)

    def test_help_lista_a_opcao(self):
        self.assertIn("--video-longo", self.rodar("--help").stdout)

    def test_duracao_fora_da_faixa_e_recusada_antes_de_baixar_qualquer_coisa(self):
        for minutos in ("3", "13", "0"):
            r = self.rodar("https://www.youtube.com/watch?v=AAAAAAAAAAA", "--audio", "gameplay", "--video-longo", minutos)
            self.assertEqual(r.returncode, 1, minutos)
            self.assertIn("de 4 a 12", r.stdout)


if __name__ == "__main__":
    unittest.main()
