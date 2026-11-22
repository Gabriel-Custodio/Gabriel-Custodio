"""Testes do resumo narrado no início do corte (add_summary_intro e suas peças).

Tudo com mocks: nenhum ffmpeg, rede ou edge-tts é usado. Rodar na pasta do projeto:
    python -m unittest discover -s app/tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
import gerar_cortes as g  # noqa: E402

FALA = ("gente olha só o que aconteceu aqui eu tentei passar o chefe de primeira "
        "mas o jogo roda a trinta quadros por segundo e eu não consigo me acostumar com isso")
EVENTOS = [{"text": t, "offset": i * 0.4, "duration": 0.35, "type": "WordBoundary"}
           for i, t in enumerate("O streamer testou o jogo e não aguentou os trinta quadros".split())]


def resposta(texto):
    return json.dumps({"resumo": texto}, ensure_ascii=False)


class TestAtivacao(unittest.TestCase):
    def setUp(self):
        self.antigo = os.environ.pop("CORTES_RESUMO", None)

    def tearDown(self):
        os.environ.pop("CORTES_RESUMO", None)
        if self.antigo is not None:
            os.environ["CORTES_RESUMO"] = self.antigo

    def test_padrao_ligado(self):
        self.assertTrue(g.summary_intro_enabled())

    def test_desliga_com_env(self):
        for v in ("0", "false", "off", "NAO", "não"):
            os.environ["CORTES_RESUMO"] = v
            self.assertFalse(g.summary_intro_enabled(), v)
        for v in ("1", "true", "", "qualquer"):
            os.environ["CORTES_RESUMO"] = v
            self.assertTrue(g.summary_intro_enabled(), v)

    def test_flag_sem_resumo_existe(self):
        self.assertIn("--sem-resumo", (RAIZ / "gerar_cortes.py").read_text(encoding="utf-8"))


class TestTextoDoResumo(unittest.TestCase):
    def gerar(self, saida, chave="k"):
        with mock.patch.object(g, "get_text_models_to_try", return_value=["m1"]), \
             mock.patch.object(g, "groq_chat", return_value=saida) as chat:
            return g.generate_summary_intro_text(FALA, chave), chat

    def test_resumo_bom(self):
        texto, _ = self.gerar(resposta("O streamer tentou passar o chefe, mas o jogo a trinta quadros incomodou muito."))
        self.assertTrue(texto.startswith("O streamer"))

    def test_sem_chave_ou_sem_fala(self):
        self.assertIsNone(self.gerar(resposta("qualquer coisa aqui para teste"), chave=None)[0])
        with mock.patch.object(g, "groq_chat") as chat:
            self.assertIsNone(g.generate_summary_intro_text("   ", "k"))
            chat.assert_not_called()

    def test_curto_demais_e_recusado(self):
        self.assertIsNone(self.gerar(resposta("Ele jogou."))[0])

    def test_longo_fica_so_com_a_primeira_frase(self):
        longo = ("O streamer tentou passar o chefe de primeira. "
                 + " ".join(["depois reclamou dos quadros por segundo do jogo"] * 4) + ".")
        texto, _ = self.gerar(resposta(longo))
        self.assertEqual(texto, "O streamer tentou passar o chefe de primeira.")

    def test_invencao_e_descartada(self):
        texto, _ = self.gerar(resposta("Cristiano Ronaldo anunciou transferência bilionária para Marte ontem."))
        self.assertIsNone(texto)

    def test_limpa_emoji_hashtag_e_aspas(self):
        texto, _ = self.gerar(resposta('"O streamer tentou passar o chefe do jogo 🎮 #gta6 sem sucesso"'))
        self.assertNotIn("#", texto)
        self.assertNotIn("🎮", texto)
        self.assertNotIn('"', texto)

    def test_resposta_invalida_tenta_proximo_modelo(self):
        with mock.patch.object(g, "get_text_models_to_try", return_value=["m1", "m2"]), \
             mock.patch.object(g, "groq_chat", side_effect=["lixo", resposta("O streamer tentou passar o chefe do jogo sem sucesso.")]):
            self.assertIsNotNone(g.generate_summary_intro_text(FALA, "k"))


class TestPecas(unittest.TestCase):
    def test_fidelidade(self):
        self.assertTrue(g.summary_is_grounded("O streamer tentou passar o chefe e não consegue se acostumar com os quadros", FALA))
        self.assertFalse(g.summary_is_grounded("Cristiano Ronaldo anunciou transferência bilionária", FALA))

    def test_fps(self):
        self.assertEqual(g.normalize_fps("29.97"), "30000/1001")
        self.assertEqual(g.normalize_fps("59.94"), "60000/1001")
        self.assertEqual(g.normalize_fps("30"), "30")
        self.assertEqual(g.normalize_fps("lixo"), "30")

    def test_sonda_do_corte(self):
        err = ("Stream #0:0: Video: h264 (High), yuv420p(tv), 1080x1920 [SAR 1:1 DAR 9:16], 29.97 fps, 29.97 tbr\n"
               "Stream #0:1: Audio: aac (LC), 48000 Hz, stereo, fltp, 192 kb/s")
        with mock.patch.object(g, "run_cmd", return_value=(False, "", err)):
            info = g.probe_stream_info("x.mp4")
        self.assertEqual(info, {"fps": "30000/1001", "sample_rate": 48000, "channels": 2, "has_audio": True})
        with mock.patch.object(g, "run_cmd", return_value=(False, "", "Video: h264, 1080x1920, 30 fps")):
            self.assertFalse(g.probe_stream_info("x.mp4")["has_audio"])

    def test_tempos_das_palavras(self):
        frases = [{"text": "uma frase de quatro palavras", "offset": 1.0, "duration": 2.0, "type": "SentenceBoundary"}]
        w = g.intro_word_timings(frases)
        self.assertEqual([x[2] for x in w], ["uma", "frase", "de", "quatro", "palavras"])
        self.assertAlmostEqual(w[0][0], 1.0)
        self.assertAlmostEqual(w[-1][1], 3.0)
        misto = EVENTOS + [{"text": "frase inteira repetida", "offset": 0, "duration": 5, "type": "SentenceBoundary"}]
        self.assertEqual(len(g.intro_word_timings(misto)), len(EVENTOS))     # prefere WordBoundary

    def test_legenda_da_intro(self):
        with tempfile.TemporaryDirectory() as d:
            ass = Path(d) / "i.ass"
            self.assertTrue(g.create_intro_ass(EVENTOS, ass, 6.0))
            texto = ass.read_text(encoding="utf-8")
            self.assertIn("Style: Intro,", texto)
            self.assertIn("O STREAMER TESTOU", texto)
            self.assertEqual(texto.count("Dialogue:"), 4)                   # 11 palavras em blocos de 3
            self.assertFalse(g.create_intro_ass([], ass, 6.0))

    def test_comandos(self):
        info = {"fps": "30000/1001", "sample_rate": 48000, "channels": 2, "has_audio": True}
        intro = g.build_intro_cmd("f.png", "t.mp3", "C:/tmp/a.ass", "i.mp4", 6.5, info)
        self.assertIn("-loop", intro)
        self.assertEqual(intro[intro.index("-framerate") + 1], "30000/1001")
        self.assertEqual(intro[intro.index("-ar") + 1], "48000")
        self.assertIn("C\\:/tmp/a.ass", intro[intro.index("-filter_complex") + 1])    # ':' escapado no filtro
        self.assertEqual(intro[intro.index("-crf") + 1], str(g.QUALITY_CRF))
        copia = g.build_concat_copy_cmd("l.txt", "o.mp4")
        self.assertEqual(copia[copia.index("-c") + 1], "copy")
        self.assertIn("concat", copia)
        self.assertIn("concat=n=2:v=1:a=1", g.build_concat_reencode_cmd("a", "b", "c")[
            g.build_concat_reencode_cmd("a", "b", "c").index("-filter_complex") + 1])


class TestFluxoCompleto(unittest.TestCase):
    """add_summary_intro com ffmpeg falso: confere decisões e que o corte nunca é estragado."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.antigo_temp = g.TEMP_DIR
        g.TEMP_DIR = self.dir
        os.environ.pop("CORTES_RESUMO", None)
        self.saida = self.dir / "corte.mp4"
        self.saida.write_bytes(b"ORIGINAL")
        self.cmds = []
        self.durs = {"corte.mp4": 40.0, "resumo_corte_intro.mp4": 7.0, "resumo_corte_final.mp4": 47.0}
        self.falhar = set()

    def tearDown(self):
        g.TEMP_DIR = self.antigo_temp
        os.environ.pop("CORTES_RESUMO", None)
        self.tmp.cleanup()

    def fake_run(self, cmd, cwd=None, timeout=None):
        self.cmds.append(cmd)
        if "-i" in cmd and cmd[-1] == "-i":          # não ocorre; só por segurança
            return True, "", ""
        if cmd[:4] == [g.FFMPEG.as_posix(), "-hide_banner", "-i", str(self.saida)]:
            return False, "", ("Video: h264, 1080x1920, 30 fps, 30 tbr\nAudio: aac (LC), 44100 Hz, stereo")
        if "-frames:v" in cmd:
            Path(cmd[-1]).write_bytes(b"PNG")
            return True, "", ""
        tipo = "intro" if "-loop" in cmd else "copia" if "concat" in cmd and "-f" in cmd else "reencode"
        if tipo in self.falhar:
            return False, "", f"falha {tipo}"
        Path(cmd[-1]).write_bytes(tipo.encode())
        return True, "", ""

    def rodar(self, fala_ok=(True, 6.0, EVENTOS), texto="O streamer testou o jogo e não aguentou os quadros.", chave="k"):
        with mock.patch.object(g, "run_cmd", side_effect=self.fake_run), \
             mock.patch.object(g, "get_video_duration", side_effect=lambda p: self.durs.get(Path(p).name, 0)), \
             mock.patch.object(g, "generate_summary_intro_text", return_value=texto), \
             mock.patch.object(g, "_speak_summary", return_value=fala_ok):
            return g.add_summary_intro(self.saida, FALA, chave)

    def test_sucesso_junta_sem_recodificar(self):
        info = self.rodar()
        self.assertEqual(self.saida.read_bytes(), b"copia")
        self.assertEqual(info["juncao"], "copia")
        self.assertAlmostEqual(info["duracao_s"], 6.0 + g.SUMMARY_PAUSE_AFTER, places=2)
        self.assertEqual(info["texto"], "O streamer testou o jogo e não aguentou os quadros.")
        self.assertEqual(list(self.dir.glob("resumo_*")), [])          # sem lixo em temp

    def test_desligado_nao_faz_nada(self):
        os.environ["CORTES_RESUMO"] = "0"
        self.assertIsNone(self.rodar())
        self.assertEqual(self.cmds, [])
        self.assertEqual(self.saida.read_bytes(), b"ORIGINAL")

    def test_sem_chave(self):
        self.assertIsNone(self.rodar(chave=None))
        self.assertEqual(self.saida.read_bytes(), b"ORIGINAL")

    def test_sem_resumo_bom_o_corte_fica_igual(self):
        self.assertIsNone(self.rodar(texto=None))
        self.assertEqual(self.saida.read_bytes(), b"ORIGINAL")

    def test_juncao_copia_falha_usa_recodificacao(self):
        self.falhar.add("copia")
        info = self.rodar()
        self.assertEqual(info["juncao"], "reencode")
        self.assertEqual(self.saida.read_bytes(), b"reencode")

    def test_duracao_errada_na_copia_cai_para_recodificacao(self):
        self.durs["resumo_corte_final.mp4"] = 41.0                    # faltou a intro no resultado
        self.assertIsNone(self.rodar())                                 # as duas juncoes dariam 41: desiste
        self.assertEqual(self.saida.read_bytes(), b"ORIGINAL")

    def test_tudo_falha_preserva_o_corte(self):
        self.falhar.update({"copia", "reencode"})
        self.assertIsNone(self.rodar())
        self.assertEqual(self.saida.read_bytes(), b"ORIGINAL")
        self.assertEqual(list(self.dir.glob("resumo_*")), [])

    def test_falha_na_intro_preserva_o_corte(self):
        self.falhar.add("intro")
        self.assertIsNone(self.rodar())
        self.assertEqual(self.saida.read_bytes(), b"ORIGINAL")

    def test_voz_longa_demais_desiste(self):
        self.assertIsNone(self.rodar(fala_ok=(True, g.SUMMARY_MAX_SPOKEN_SECONDS + 2, EVENTOS)))
        self.assertEqual(self.saida.read_bytes(), b"ORIGINAL")

    def test_voz_longa_tenta_mais_rapido(self):
        voz = mock.Mock(side_effect=[(True, 11.0, EVENTOS), (True, 7.5, EVENTOS)])
        with mock.patch.object(g, "run_cmd", side_effect=self.fake_run), \
             mock.patch.object(g, "get_video_duration", side_effect=lambda p: self.durs.get(Path(p).name, 0)), \
             mock.patch.object(g, "generate_summary_intro_text", return_value="O streamer testou o jogo e não aguentou."), \
             mock.patch.object(g, "_speak_summary", voz):
            info = g.add_summary_intro(self.saida, FALA, "k")
        self.assertEqual(voz.call_args_list[1].args[2], g.SUMMARY_FAST_RATE)
        self.assertIsNotNone(info)

    def test_passaria_do_limite_total(self):
        self.durs["corte.mp4"] = g.SUMMARY_MAX_TOTAL_SECONDS - 2
        self.assertIsNone(self.rodar())
        self.assertEqual(self.saida.read_bytes(), b"ORIGINAL")

    def test_corte_sem_audio(self):
        def sem_audio(cmd, cwd=None, timeout=None):
            if cmd[:4] == [g.FFMPEG.as_posix(), "-hide_banner", "-i", str(self.saida)]:
                return False, "", "Video: h264, 1080x1920, 30 fps"
            return self.fake_run(cmd)
        with mock.patch.object(g, "run_cmd", side_effect=sem_audio), \
             mock.patch.object(g, "get_video_duration", side_effect=lambda p: self.durs.get(Path(p).name, 0)), \
             mock.patch.object(g, "generate_summary_intro_text", return_value="O streamer testou o jogo."), \
             mock.patch.object(g, "_speak_summary", return_value=(True, 6.0, EVENTOS)):
            self.assertIsNone(g.add_summary_intro(self.saida, FALA, "k"))
        self.assertEqual(self.saida.read_bytes(), b"ORIGINAL")

    def test_erro_inesperado_nao_estoura(self):
        with mock.patch.object(g, "generate_summary_intro_text", side_effect=RuntimeError("boom")):
            self.assertIsNone(g.add_summary_intro(self.saida, FALA, "k"))
        self.assertEqual(self.saida.read_bytes(), b"ORIGINAL")


if __name__ == "__main__":
    unittest.main()


class TestNomeDoStreamer(unittest.TestCase):
    def test_nome_do_canal(self):
        self.assertEqual(g.resolve_streamer_name({"channel": "Alanzoka", "uploader": "x"}), "Alanzoka")
        self.assertEqual(g.resolve_streamer_name({"uploader": "@gaules"}), "gaules")
        self.assertIsNone(g.resolve_streamer_name({}))
        self.assertIsNone(g.resolve_streamer_name(None))

    def test_canal_de_cortes_nao_e_o_streamer(self):
        for canal in ("Volta Isso Cortes", "Melhores Momentos do Fulano", "Clips do Beltrano", "XYZ Highlights"):
            self.assertIsNone(g.resolve_streamer_name({"channel": canal}), canal)
        self.assertEqual(g.resolve_streamer_name({"channel": "Volta Isso Cortes"}, override="Alanzoka"), "Alanzoka")
        self.assertEqual(g.resolve_streamer_name({"channel": "Cortes X", "uploader": "Gaules"}), "Gaules")

    def test_limpeza_do_nome(self):
        self.assertEqual(g.clean_streamer_name("  @Alan 🎮 zoka  "), "Alan zoka")
        self.assertIsNone(g.clean_streamer_name("🎮"))
        self.assertIsNone(g.clean_streamer_name(None))
        self.assertEqual(len(g.clean_streamer_name("a" * 100)), 40)

    def test_troca_o_streamer_pelo_nome(self):
        f = g.apply_streamer_name
        self.assertEqual(f("O streamer tentou clonar a chave.", "Alanzoka"), "Alanzoka tentou clonar a chave.")
        self.assertEqual(f("A reação do streamer foi enorme.", "Alanzoka"), "A reação de Alanzoka foi enorme.")
        self.assertEqual(f("Alanzoka tentou de novo.", "Alanzoka"), "Alanzoka tentou de novo.")   # já tem
        self.assertEqual(f("Ele tentou de novo.", "Alanzoka"), "Ele tentou de novo.")             # nada a trocar
        self.assertEqual(f("O streamer tentou.", None), "O streamer tentou.")

    def test_prompt_pede_o_nome_e_texto_final_leva_o_nome(self):
        with mock.patch.object(g, "get_text_models_to_try", return_value=["m1"]), \
             mock.patch.object(g, "groq_chat", return_value=resposta("O streamer tentou passar o chefe do jogo sem sucesso.")) as chat:
            texto = g.generate_summary_intro_text(FALA, "k", "Alanzoka")
        pedido = chat.call_args.args[0][0]["content"]
        self.assertIn("se chama Alanzoka", pedido)
        self.assertTrue(texto.startswith("Alanzoka tentou"))          # a IA ignorou: o código troca
        with mock.patch.object(g, "get_text_models_to_try", return_value=["m1"]), \
             mock.patch.object(g, "groq_chat", return_value=resposta("O streamer tentou passar o chefe do jogo sem sucesso.")) as chat:
            g.generate_summary_intro_text(FALA, "k", None)
        self.assertIn("o streamer", chat.call_args.args[0][0]["content"])
        self.assertNotIn("se chama", chat.call_args.args[0][0]["content"])

    def test_o_nome_nao_derruba_a_checagem_de_fidelidade(self):
        # "Alanzoka" não está na fala, mas foi informado: não conta como invenção
        with mock.patch.object(g, "get_text_models_to_try", return_value=["m1"]), \
             mock.patch.object(g, "groq_chat", return_value=resposta("Alanzoka tentou passar o chefe do jogo sem sucesso.")):
            self.assertIsNotNone(g.generate_summary_intro_text(FALA, "k", "Alanzoka"))

    def test_info_do_video(self):
        saida = 'aviso\n{"channel": "Alanzoka", "title": "x"}'
        with mock.patch.object(g, "run_cmd", return_value=(True, saida, "")):
            self.assertEqual(g.fetch_video_info("https://twitch.tv/x")["channel"], "Alanzoka")
        with mock.patch.object(g, "run_cmd", return_value=(False, "", "erro")):
            self.assertEqual(g.fetch_video_info("https://twitch.tv/x"), {})
        with mock.patch.object(g, "run_cmd", return_value=(True, "{quebrado", "")):
            self.assertEqual(g.fetch_video_info("https://twitch.tv/x"), {})
