"""Testes do histórico de cortes: um mesmo vídeo processado várias vezes não repete trechos.

Rodar na pasta do projeto:   python -m unittest discover -s app/tests -v
"""
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
import gerar_cortes as g  # noqa: E402

FILLER = "então a gente vai aqui pelo mapa e olha o que tem por aqui cara".split()


def transcricao(duracao, semente=7):
    """Fala contínua (≈2,6 palavras/s) ao longo de todo o vídeo, com alguns trechos animados."""
    random.seed(semente)
    segs, t = [], 5.0
    while t < duracao - 10:
        dur = random.uniform(2.0, 5.0)
        palavras = [random.choice(FILLER) for _ in range(int(dur * 2.6))]
        if random.random() < 0.08:
            palavras += ["kkkkk", "meu", "deus", "não", "acredito!"]
        segs.append({"start": t, "end": t + dur, "text": " ".join(palavras) + "."})
        t += dur + random.uniform(0.2, 2.0)
    return segs


def sobrepoe(a, b, margem=0.0):
    return min(a[1], b[1] + margem) - max(a[0], b[0] - margem) > 0


class TestChaveDoVideo(unittest.TestCase):
    def test_links_diferentes_do_mesmo_video(self):
        iguais = [
            "https://www.youtube.com/watch?v=oDVH4HnGByU",
            "https://www.youtube.com/watch?v=oDVH4HnGByU&t=1214s&pp=ugUHEgVwdC1CUg%3D%3D",
            "https://youtu.be/oDVH4HnGByU?si=abc",
            "https://www.youtube.com/shorts/oDVH4HnGByU",
            "https://www.youtube.com/live/oDVH4HnGByU",
        ]
        chaves = {g.video_key_from_url(u) for u in iguais}
        self.assertEqual(chaves, {"yt_oDVH4HnGByU"})

    def test_outros_sites_e_videos_diferentes(self):
        self.assertNotEqual(g.video_key_from_url("https://www.youtube.com/watch?v=AAAAAAAAAAA"),
                            g.video_key_from_url("https://www.youtube.com/watch?v=BBBBBBBBBBB"))
        a = g.video_key_from_url("https://www.twitch.tv/videos/123")
        self.assertTrue(a.startswith("url_"))
        self.assertEqual(a, g.video_key_from_url("HTTPS://www.twitch.tv/videos/123"))
        self.assertNotEqual(a, g.video_key_from_url("https://www.twitch.tv/videos/124"))


class TestHistorico(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.arq = self.dir / g.HISTORICO_NOME

    def tearDown(self):
        self._tmp.cleanup()

    def novo(self, key="yt_AAA", ativo=True):
        return g.HistoricoCortes(self.arq, key, "https://www.youtube.com/watch?v=AAA", ativo=ativo)

    def test_regras_de_sobreposicao(self):
        h = self.novo()
        self.assertFalse(h.repetido(100, 140))                      # histórico vazio
        h.registrar(1000, 1040, "gameplay", "a.mp4", "T")
        self.assertTrue(h.repetido(1000, 1040))                     # o mesmo corte
        self.assertTrue(h.repetido(1008, 1048))                     # deslocado 8 s (a grade dos candidatos)
        self.assertTrue(h.repetido(990, 1030))
        self.assertTrue(h.repetido(1041, 1081))                     # colado no fim: mesma conversa
        self.assertFalse(h.repetido(1056, 1096))                    # 16 s depois do fim: já é outro trecho
        self.assertFalse(h.repetido(1100, 1140))
        self.assertFalse(h.repetido(500, 540))

    def test_persistencia_e_isolamento_entre_videos(self):
        h = self.novo("yt_AAA")
        h.registrar(1000, 1040, "ia", "a.mp4")
        outro = self.novo("yt_BBB")
        self.assertFalse(outro.repetido(1000, 1040))                # outro vídeo: não interfere
        reaberto = self.novo("yt_AAA")                              # outra execução do programa
        self.assertEqual(reaberto.usados(), [(1000.0, 1040.0)])
        self.assertTrue(reaberto.repetido(1000, 1040))
        self.assertFalse((self.dir / (g.HISTORICO_NOME[:-5] + ".tmp")).exists())

    def test_repetir_nao_filtra_mas_continua_registrando(self):
        h = self.novo()
        h.registrar(1000, 1040, "ia", "a.mp4")
        livre = self.novo(ativo=False)
        self.assertFalse(livre.repetido(1000, 1040))
        novos, ignorados = livre.filtrar([{"start": 1000, "end": 1040}])
        self.assertEqual((len(novos), ignorados), (1, 0))
        livre.registrar(2000, 2040, "ia", "b.mp4")
        self.assertTrue(self.novo().repetido(2000, 2040))           # na próxima vez sem --repetir, vale

    def test_arquivo_corrompido_nao_derruba(self):
        self.arq.write_text("{isso nao e json", encoding="utf-8")
        h = self.novo()
        self.assertEqual(h.usados(), [])
        h.registrar(10, 50, "ia", "a.mp4")
        self.assertEqual(self.novo().usados(), [(10.0, 50.0)])
        self.arq.write_text(json.dumps({"videos": {"yt_AAA": {"cortes": [{"inicio": "x"}, 5, {"inicio": 1, "fim": 2}]}}}), encoding="utf-8")
        self.assertEqual(self.novo().usados(), [(1.0, 2.0)])      # entradas ruins são ignoradas

    def test_esquecer_video(self):
        h = self.novo()
        h.registrar(10, 50, "ia", "a.mp4")
        h.esquecer_video()
        self.assertFalse(self.novo().repetido(10, 50))

    def test_importar_cortes_existentes(self):
        pasta, descartados = self.dir / "cortes", self.dir / "cortes" / "_descartados"
        descartados.mkdir(parents=True)
        def meta(nome, **campos):
            (pasta / nome).write_text(json.dumps(campos), encoding="utf-8")
        meta("c1.json", clip_start=100.0, clip_end=140.0, modo="gameplay", titulo_gancho="UM")
        meta("c2.json", clip_start=500.0, clip_end=540.0, video_id="yt_AAA")           # já do vídeo certo
        meta("c3.json", clip_start=900.0, clip_end=940.0, video_id="yt_OUTRO")         # de outro vídeo: não importa
        meta("c4.json", titulo_gancho="sem tempos")                                   # sem tempos: ignora
        meta("_status.json", clip_start=1.0, clip_end=2.0)                             # arquivo interno: ignora
        (pasta / "ruim.json").write_text("{quebrado", encoding="utf-8")
        (descartados / "d1.json").write_text(json.dumps({"clip_start": 300.0, "clip_end": 340.0}), encoding="utf-8")
        h = self.novo()
        self.assertEqual(h.importar_existentes([pasta, descartados, self.dir / "nao_existe"]), 3)
        self.assertEqual(sorted(h.usados()), [(100.0, 140.0), (300.0, 340.0), (500.0, 540.0)])
        self.assertEqual(h.importar_existentes([pasta, descartados]), 0)               # rodar de novo não duplica
        self.assertTrue(h.repetido(300, 340))                                         # descartado também conta como usado


class TestSelecaoSemRepetir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.arq = Path(self._tmp.name) / g.HISTORICO_NOME
        self.antes = g.HISTORICO
        # a IA (simulada) sempre escolhe os 2 primeiros candidatos de cada faixa
        self._llm = g.groq_chat_with_fallback
        g.groq_chat_with_fallback = lambda *a, **k: json.dumps([
            {"id": 1, "score": 8, "reason": "r", "hook_title": "TITULO UM", "pergunta_cta": "E agora?"},
            {"id": 2, "score": 7, "reason": "r", "hook_title": "TITULO DOIS", "pergunta_cta": "Será?"}])

    def tearDown(self):
        g.HISTORICO = self.antes
        g.groq_chat_with_fallback = self._llm
        self._tmp.cleanup()

    def historico(self, ativo=True):
        return g.HistoricoCortes(self.arq, "yt_VIDEO", "https://www.youtube.com/watch?v=VIDEO", ativo=ativo)

    def test_varias_execucoes_do_mesmo_video_nao_repetem(self):
        duracao = 10863.68
        segs = transcricao(duracao)
        g.HISTORICO = self.historico()
        todos = []
        for rodada in range(1, 40):
            clips = g.find_best_clips_gameplay_ai(segs, duracao, "KEY", num_clips=6)
            if not clips:
                break
            for c in clips:
                faixa = (c["start"], c["end"])
                for anterior in todos:                                 # nenhum corte repete um anterior
                    self.assertFalse(sobrepoe(faixa, anterior), (rodada, faixa, anterior))
                todos.append(faixa)
                g.registrar_corte_no_historico(c["start"], c["end"], "gameplay", "x.mp4", c.get("hook_title", ""))
        else:
            self.fail("deveria esgotar os trechos do vídeo")
        self.assertGreater(rodada, 2)                                  # deu mais de uma rodada de cortes
        self.assertGreaterEqual(len(todos), 12)                        # ao menos 2 rodadas completas de 6
        print(f"\n   {len(todos)} cortes distintos em {rodada - 1} execuções antes de esgotar o vídeo")

    def test_primeira_execucao_nao_muda_e_repetir_volta_a_permitir(self):
        duracao = 3600.0
        segs = transcricao(duracao)
        g.HISTORICO = self.historico()
        primeira = g.find_best_clips_gameplay_ai(segs, duracao, "KEY", num_clips=6)
        self.assertEqual(len(primeira), 6)
        for c in primeira:
            g.registrar_corte_no_historico(c["start"], c["end"], "gameplay", "x.mp4")
        segunda = g.find_best_clips_gameplay_ai(segs, duracao, "KEY", num_clips=6)
        inicio_1 = {round(c["start"], 1) for c in primeira}
        self.assertTrue(segunda)
        self.assertFalse(inicio_1 & {round(c["start"], 1) for c in segunda})
        g.HISTORICO = self.historico(ativo=False)                      # --repetir
        terceira = g.find_best_clips_gameplay_ai(segs, duracao, "KEY", num_clips=6)
        self.assertEqual(inicio_1, {round(c["start"], 1) for c in terceira})   # volta a escolher os mesmos

    def test_sem_historico_o_comportamento_e_o_antigo(self):
        duracao = 3600.0
        segs = transcricao(duracao)
        g.HISTORICO = None
        a = g.find_best_clips_gameplay_ai(segs, duracao, "KEY", num_clips=6)
        b = g.find_best_clips_gameplay_ai(segs, duracao, "KEY", num_clips=6)
        self.assertEqual([c["start"] for c in a], [c["start"] for c in b])

    def test_metodo_tradicional_tambem_pula_repetidos(self):
        duracao = 1500.0
        segs = transcricao(duracao)
        g.HISTORICO = self.historico()
        primeira = g.find_best_clips(segs, duracao, 3)
        self.assertEqual(len(primeira), 3)
        for c in primeira:
            g.registrar_corte_no_historico(c["start"], c["end"], "ia", "x.mp4")
        segunda = g.find_best_clips(segs, duracao, 3)
        for c in segunda:
            for p in primeira:
                self.assertFalse(sobrepoe((c["start"], c["end"]), (p["start"], p["end"])))

    def test_passo_c_nao_escolhe_momento_ja_cortado(self):
        quadros = [{"timestamp": 100.0, "score": 9}, {"timestamp": 400.0, "score": 8}, {"timestamp": 800.0, "score": 7}]
        g.HISTORICO = self.historico()
        self.assertEqual([f["timestamp"] for f in g._pick_climax_frames(quadros, num_clips=2)], [100.0, 400.0])
        g.HISTORICO.registrar(80, 120, "visual", "x.mp4")
        self.assertEqual([f["timestamp"] for f in g._pick_climax_frames(quadros, num_clips=2)], [400.0, 800.0])

    def test_json_do_corte_guarda_de_qual_video_veio(self):
        g.HISTORICO = self.historico()
        mp4 = Path(self._tmp.name) / "corte.mp4"
        g.save_clip_metadata(mp4, {"text": "x", "start": 1.0, "end": 41.0, "duration": 40.0}, {}, "ia")
        meta = json.loads(mp4.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual((meta["video_id"], meta["video_url"]),
                         ("yt_VIDEO", "https://www.youtube.com/watch?v=VIDEO"))

    def test_registrar_nunca_levanta(self):
        g.HISTORICO = g.HistoricoCortes(Path("/pasta/inexistente/ou/sem/permissao/h.json"), "yt_X", "")
        g.registrar_corte_no_historico(1, 41, "ia", "x.mp4")           # só avisa


if __name__ == "__main__":
    unittest.main(verbosity=2)
