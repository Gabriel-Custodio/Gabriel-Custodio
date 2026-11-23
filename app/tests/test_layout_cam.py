"""Testes do layout cam (gameplay em cima, faixa vermelha com legenda, câmera ampliada embaixo).

Tudo com mocks: nenhum ffmpeg é executado. Rodar na pasta do projeto:
    python -m unittest discover -s app/tests -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
import gerar_cortes as g  # noqa: E402

CAM_CANTO = {"x": 0.0, "y": 0.72, "w": 0.26, "h": 0.28, "name": "Canto inferior esquerdo"}
STYLE = ("Style: Default,Arial,62,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,320,1")


class TestEscolhaDeLayout(unittest.TestCase):
    def setUp(self):
        self.antigo = os.environ.pop("CORTES_LAYOUT", None)

    def tearDown(self):
        os.environ.pop("CORTES_LAYOUT", None)
        if self.antigo is not None:
            os.environ["CORTES_LAYOUT"] = self.antigo

    def test_padrao_e_cam(self):
        self.assertEqual(g.get_layout(), "cam")
        self.assertTrue(g.uses_fit_family())

    def test_valores_validos_e_invalidos(self):
        for valor, esperado in [("fit", "fit"), ("CLIPFLOW", "clipflow"), ("cam", "cam"), ("xyz", "cam")]:
            os.environ["CORTES_LAYOUT"] = valor
            self.assertEqual(g.get_layout(), esperado)

    def test_clipflow_nao_usa_o_plano_b_do_fit(self):
        os.environ["CORTES_LAYOUT"] = "clipflow"
        self.assertFalse(g.uses_fit_family())


class TestGeometria(unittest.TestCase):
    def test_camera_de_1080p_amplia_ate_o_limite(self):
        geo = g.cam_geometry(1920, 1080, CAM_CANTO)
        self.assertEqual((geo["crop_w"], geo["crop_h"]), (498, 302))
        self.assertLessEqual(geo["cam_scale"], g.CAM_MAX_UPSCALE + 0.01)
        self.assertGreater(geo["cam_scale"], 1.5)           # maior que o original
        self.assertEqual((geo["game_w"], geo["game_h"]), (1080, 608))

    def test_pecas_empilhadas_cabem_e_ficam_centralizadas(self):
        geo = g.cam_geometry(1920, 1080, CAM_CANTO)
        self.assertEqual(geo["y_band"], geo["y_game"] + geo["game_h"])
        self.assertEqual(geo["y_cam"], geo["y_band"] + g.CAM_BAND_HEIGHT)
        base = geo["y_cam"] + geo["cam_h"]
        self.assertLessEqual(base, g.VERTICAL_HEIGHT)
        self.assertLessEqual(abs(geo["y_game"] - (g.VERTICAL_HEIGHT - base)), 2)   # folga igual em cima e embaixo
        self.assertLessEqual(geo["x_cam"] + geo["cam_w"], g.VERTICAL_WIDTH)
        for chave in ("cam_w", "cam_h", "crop_w", "crop_h", "y_game", "y_cam", "x_cam"):
            self.assertEqual(geo[chave] % 2, 0, chave)       # medidas pares (exigência do libx264)

    def test_camera_grande_nunca_passa_da_largura_da_tela(self):
        geo = g.cam_geometry(1920, 1080, {"x": 0.0, "y": 0.0, "w": 0.6, "h": 0.4})
        self.assertLessEqual(geo["cam_w"], g.VERTICAL_WIDTH)
        self.assertLessEqual(geo["cam_scale"], 1.0 + 0.01)

    def test_recorte_e_limitado_ao_video(self):
        geo = g.cam_geometry(1920, 1080, {"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5})
        self.assertLessEqual(geo["crop_x"] + geo["crop_w"], 1920)
        self.assertLessEqual(geo["crop_y"] + geo["crop_h"], 1080)

    def test_casos_invalidos_devolvem_none(self):
        for pos in (None, {}, {"x": 0, "y": 0, "w": 0, "h": 0.2}, {"x": 1.2, "y": 0, "w": 0.2, "h": 0.2},
                    {"x": "a", "y": 0, "w": 0.2, "h": 0.2}, {"x": 0, "y": 0, "w": 0.01, "h": 0.01}):
            self.assertIsNone(g.cam_geometry(1920, 1080, pos), pos)
        self.assertIsNone(g.cam_geometry(0, 0, CAM_CANTO))

    def test_video_vertical_nao_tem_espaco_para_a_camera(self):
        self.assertIsNone(g.cam_geometry(1080, 1920, CAM_CANTO))


class TestAss(unittest.TestCase):
    def test_legenda_vai_para_o_topo_da_faixa(self):
        with tempfile.TemporaryDirectory() as d:
            origem, saida = Path(d) / "a.ass", Path(d) / "b.ass"
            origem.write_text("[V4+ Styles]\n" + STYLE + "\n", encoding="utf-8")
            g.retarget_ass_for_fit(origem, saida, alignment="8", margin_v=700)
            campos = [l for l in saida.read_text(encoding="utf-8").splitlines() if l.startswith("Style:")][0].split(",")
            self.assertEqual((campos[18], campos[21]), ("8", "700"))

    def test_fit_continua_igual(self):
        with tempfile.TemporaryDirectory() as d:
            origem, saida = Path(d) / "a.ass", Path(d) / "b.ass"
            origem.write_text(STYLE + "\n", encoding="utf-8")
            g.retarget_ass_for_fit(origem, saida)
            campos = saida.read_text(encoding="utf-8").strip().split(",")
            self.assertEqual((campos[18], campos[21]), ("2", str(g.FIT_SUBTITLE_MARGIN_V)))


class TestComando(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.geo = g.cam_geometry(1920, 1080, CAM_CANTO)
        for nome in ("CORTES_FUNDO",):
            self.addCleanup(os.environ.pop, nome, None)
        p = mock.patch.object(g, "TEMP_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(g, "GAMEPLAY_WATERMARK_PATH", str(self.dir / "nao_existe.png"))
        p.start()
        self.addCleanup(p.stop)

    def grafo(self, cmd):
        return cmd[cmd.index("-filter_complex") + 1]

    def test_grafo_com_fundo_desfocado(self):
        os.environ["CORTES_FUNDO"] = "blur"
        ass = self.dir / "s.ass"
        ass.write_text(STYLE + "\n", encoding="utf-8")
        cmd = g.build_cam_cmd(self.dir / "in.mp4", self.dir / "out.mp4", ass, self.dir / "faixa.png", self.geo, 30.0)
        grafo = self.grafo(cmd)
        self.assertIn("gblur", grafo)
        self.assertIn(f"scale={self.geo['game_w']}:{self.geo['game_h']}", grafo)
        self.assertIn(f"crop={self.geo['crop_w']}:{self.geo['crop_h']}:{self.geo['crop_x']}:{self.geo['crop_y']}", grafo)
        self.assertIn(f"overlay=0:{self.geo['y_band']}", grafo)
        self.assertIn(f"overlay={self.geo['x_cam']}:{self.geo['y_cam']}", grafo)
        self.assertIn("subtitles=", grafo)
        self.assertIn("fade=t=out", grafo)
        self.assertEqual(cmd[cmd.index("-map") + 1], "[vout]")
        self.assertIn("0:a?", cmd)

    def test_grafo_com_fundo_preto(self):
        os.environ["CORTES_FUNDO"] = "black"
        cmd = g.build_cam_cmd(self.dir / "in.mp4", self.dir / "out.mp4", None, self.dir / "faixa.png", self.geo, 30.0)
        grafo = self.grafo(cmd)
        self.assertNotIn("gblur", grafo)
        self.assertIn("pad=1080:1920", grafo)
        self.assertNotIn("subtitles=", grafo)

    def test_legenda_usa_o_topo_da_faixa(self):
        ass = self.dir / "s.ass"
        ass.write_text(STYLE + "\n", encoding="utf-8")
        g.build_cam_cmd(self.dir / "in.mp4", self.dir / "out.mp4", ass, self.dir / "faixa.png", self.geo, 30.0)
        gerado = (self.dir / "s_cam.ass").read_text(encoding="utf-8").strip().split(",")
        self.assertEqual(gerado[18], "8")
        self.assertEqual(int(gerado[21]), self.geo["y_band"] + g.CAM_SUBTITLE_TOP_PAD)


class TestFaixa(unittest.TestCase):
    def test_faixa_e_vermelha_com_a_linha_fixa(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            com, sem = Path(d) / "com.png", Path(d) / "sem.png"
            self.assertTrue(g.create_cam_band_image("CURTA · COMENTE · COMPARTILHE", com))
            self.assertTrue(g.create_cam_band_image(None, sem))
            a, b = Image.open(com).convert("RGB"), Image.open(sem).convert("RGB")
            self.assertEqual(a.size, (g.VERTICAL_WIDTH, g.CAM_BAND_HEIGHT))
            self.assertEqual(a.getpixel((5, 100)), (0xD9, 0x1E, 0x18))
            self.assertNotEqual(a.tobytes(), b.tobytes())       # a linha fixa foi desenhada
            # a parte de cima (onde vai a legenda) fica limpa nas duas
            self.assertEqual(a.crop((0, 8, 1080, 190)).tobytes(), b.crop((0, 8, 1080, 190)).tobytes())


class TestFluxo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.addCleanup(os.environ.pop, "CORTES_LAYOUT", None)
        os.environ.pop("CORTES_LAYOUT", None)
        for alvo, valor in (("TEMP_DIR", self.dir),):
            p = mock.patch.object(g, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        self.chamadas = []

        def falso(cmd, *a, **k):
            self.chamadas.append(cmd)
            return True, "", ""
        p = mock.patch.object(g, "run_cmd", falso)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(g, "get_video_dimensions", lambda *_: (1920, 1080))
        p.start()
        self.addCleanup(p.stop)

    def processa(self, webcam, audio_mode="original"):
        return g.process_clipflow_layout(
            self.dir / "in.mp4", self.dir / "out.mp4", webcam, None, {"titulo_gancho": "X"}, 30.0, None, "texto",
            audio_mode=audio_mode)

    def test_com_camera_usa_o_cam(self):
        self.assertTrue(self.processa(CAM_CANTO))
        self.assertEqual(g.LAYOUT_APLICADO, "cam")
        self.assertEqual(len(self.chamadas), 1)
        self.assertIn("-loop", self.chamadas[0])         # a faixa entra como segunda entrada
        self.assertTrue((self.dir / "faixa_cam.png").exists())

    def test_sem_camera_cai_no_fit(self):
        self.assertTrue(self.processa(None))
        self.assertEqual(g.LAYOUT_APLICADO, "fit")

    def test_modo_ia_cai_no_fit(self):
        self.assertTrue(self.processa(CAM_CANTO, audio_mode="ia"))
        self.assertEqual(g.LAYOUT_APLICADO, "fit")

    def test_recorte_invalido_cai_no_fit(self):
        self.assertTrue(self.processa({"x": 0, "y": 0, "w": 0.01, "h": 0.01}))
        self.assertEqual(g.LAYOUT_APLICADO, "fit")

    def test_falha_do_ffmpeg_devolve_false_para_acionar_o_fallback(self):
        with mock.patch.object(g, "run_cmd", lambda *a, **k: (False, "", "erro")):
            self.assertFalse(self.processa(CAM_CANTO))

    def test_fit_escolhido_nao_usa_o_cam(self):
        os.environ["CORTES_LAYOUT"] = "fit"
        self.assertTrue(self.processa(CAM_CANTO))
        self.assertEqual(g.LAYOUT_APLICADO, "fit")
        self.assertFalse((self.dir / "faixa_cam.png").exists())

    def test_json_traz_o_layout(self):
        fonte = (RAIZ / "gerar_cortes.py").read_text(encoding="utf-8")
        self.assertIn('"layout": LAYOUT_APLICADO', fonte)


if __name__ == "__main__":
    unittest.main()
