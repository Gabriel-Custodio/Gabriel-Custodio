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
CAM_DIREITA = {"x": 0.78, "y": 0.37, "w": 0.21, "h": 0.22, "name": "Meio-direita"}
STYLE = ("Style: Default,Arial,62,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,320,1")


class TestEscolhaDeLayout(unittest.TestCase):
    def setUp(self):
        self.antigo = os.environ.pop("CORTES_LAYOUT", None)

    def tearDown(self):
        os.environ.pop("CORTES_LAYOUT", None)
        if self.antigo is not None:
            os.environ["CORTES_LAYOUT"] = self.antigo

    def test_padrao_volta_a_ser_fit(self):
        self.assertEqual(g.get_layout(), "fit")
        self.assertTrue(g.uses_fit_family())

    def test_valores_validos_e_invalidos(self):
        for valor, esperado in [("fit", "fit"), ("CLIPFLOW", "clipflow"), ("cam", "cam"), ("xyz", "fit")]:
            os.environ["CORTES_LAYOUT"] = valor
            self.assertEqual(g.get_layout(), esperado)

    def test_clipflow_nao_usa_o_plano_b_do_fit(self):
        os.environ["CORTES_LAYOUT"] = "clipflow"
        self.assertFalse(g.uses_fit_family())


class TestGeometria(unittest.TestCase):
    def test_blocos_somam_a_tela_toda(self):
        self.assertEqual(g.CAM_TOP_HEIGHT + g.CAM_BAND_HEIGHT + g.CAM_GAME_HEIGHT, g.VERTICAL_HEIGHT)

    def test_recorte_tem_a_proporcao_do_bloco_e_contem_a_caixa(self):
        geo = g.cam_geometry(1920, 1080, CAM_DIREITA)
        aspecto = g.VERTICAL_WIDTH / g.CAM_TOP_HEIGHT
        self.assertAlmostEqual(geo["crop_w"] / geo["crop_h"], aspecto, delta=0.03)
        x0, y0 = CAM_DIREITA["x"] * 1920, CAM_DIREITA["y"] * 1080
        x1, y1 = x0 + CAM_DIREITA["w"] * 1920, y0 + CAM_DIREITA["h"] * 1080
        self.assertLessEqual(geo["crop_x"], x0)
        self.assertLessEqual(geo["crop_y"], y0)
        self.assertGreaterEqual(geo["crop_x"] + geo["crop_w"], x1)
        self.assertGreaterEqual(geo["crop_y"] + geo["crop_h"], y1)

    def test_recorte_fica_dentro_do_video_e_com_medidas_pares(self):
        for pos in (CAM_DIREITA, CAM_CANTO, {"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5},
                    {"x": 0.0, "y": 0.0, "w": 0.05, "h": 0.05}):
            geo = g.cam_geometry(1920, 1080, pos)
            self.assertGreaterEqual(geo["crop_x"], 0, pos)
            self.assertGreaterEqual(geo["crop_y"], 0, pos)
            self.assertLessEqual(geo["crop_x"] + geo["crop_w"], 1920, pos)
            self.assertLessEqual(geo["crop_y"] + geo["crop_h"], 1080, pos)
            for chave in ("crop_x", "crop_y", "crop_w", "crop_h"):
                self.assertEqual(geo[chave] % 2, 0, (pos, chave))

    def test_caixa_so_do_rosto_cresce_ate_o_minimo(self):
        geo = g.cam_geometry(1920, 1080, {"x": 0.85, "y": 0.4, "w": 0.04, "h": 0.07})
        self.assertGreaterEqual(geo["crop_w"], int(g.CAM_MIN_CROP_FRAC * 1920) - 2)
        self.assertLessEqual(geo["ampliacao"], g.VERTICAL_WIDTH / (g.CAM_MIN_CROP_FRAC * 1920) + 0.05)

    def test_ampliacao_e_calculada(self):
        geo = g.cam_geometry(1920, 1080, CAM_DIREITA)
        self.assertAlmostEqual(geo["ampliacao"], g.VERTICAL_WIDTH / geo["crop_w"], delta=0.01)

    def test_posicoes_dos_blocos(self):
        geo = g.cam_geometry(1920, 1080, CAM_DIREITA)
        self.assertEqual(geo["y_band"], g.CAM_TOP_HEIGHT)
        self.assertEqual(geo["y_game"], g.CAM_TOP_HEIGHT + g.CAM_BAND_HEIGHT)

    def test_casos_invalidos_devolvem_none(self):
        for pos in (None, {}, {"x": 0, "y": 0, "w": 0, "h": 0.2}, {"x": 1.2, "y": 0, "w": 0.2, "h": 0.2},
                    {"x": "a", "y": 0, "w": 0.2, "h": 0.2}):
            self.assertIsNone(g.cam_geometry(1920, 1080, pos), pos)
        self.assertIsNone(g.cam_geometry(0, 0, CAM_CANTO))


class TestCameraManual(unittest.TestCase):
    def test_valida(self):
        box = g.parse_camera_box("0.78, 0.37, 0.21, 0.22")
        self.assertEqual((box["x"], box["y"], box["w"], box["h"]), (0.78, 0.37, 0.21, 0.22))
        self.assertIsNotNone(g.parse_camera_box("0.1;0.1;0.3;0.3"))

    def test_invalida(self):
        for texto in ("", "a,b,c,d", "0.1,0.1,0.3", "0.9,0.1,0.3,0.3", "0.1,0.1,0,0.3", "-0.1,0,0.2,0.2", "1.5,0,0.2,0.2", None):
            self.assertIsNone(g.parse_camera_box(texto), texto)


class TestAss(unittest.TestCase):
    def test_pos_fixo_do_karaoke_vai_para_dentro_da_faixa(self):
        # a legenda karaokê usa \\pos(540,835) em cada fala: sem trocá-lo, alinhamento e margem não valem
        with tempfile.TemporaryDirectory() as d:
            origem, saida = Path(d) / "a.ass", Path(d) / "b.ass"
            origem.write_text(STYLE + "\nDialogue: 0,0:00:01.00,0:00:02.00,Karaoke,,0,0,0,karaoke,{\\pos(540,835)}OI\n",
                              encoding="utf-8")
            g.retarget_ass_for_fit(origem, saida, alignment="5", margin_v=0, pos_y=686)
            texto = saida.read_text(encoding="utf-8")
            self.assertIn("{\\pos(540,686)}", texto)
            self.assertNotIn("835", texto)

    def test_sem_pos_y_o_pos_continua_como_estava(self):
        with tempfile.TemporaryDirectory() as d:
            origem, saida = Path(d) / "a.ass", Path(d) / "b.ass"
            origem.write_text(STYLE + "\nDialogue: 0,0:00:01.00,0:00:02.00,Karaoke,,0,0,0,karaoke,{\\pos(540,835)}OI\n",
                              encoding="utf-8")
            g.retarget_ass_for_fit(origem, saida)
            self.assertIn("{\\pos(540,835)}", saida.read_text(encoding="utf-8"))

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
        self.geo = g.cam_geometry(1920, 1080, CAM_DIREITA)
        p = mock.patch.object(g, "TEMP_DIR", self.dir)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(g, "GAMEPLAY_WATERMARK_PATH", str(self.dir / "nao_existe.png"))
        p.start()
        self.addCleanup(p.stop)

    def grafo(self, cmd):
        return cmd[cmd.index("-filter_complex") + 1]

    def test_grafo_camera_faixa_gameplay(self):
        ass = self.dir / "s.ass"
        ass.write_text(STYLE + "\nDialogue: 0,0:00:01.00,0:00:02.00,Karaoke,,0,0,0,karaoke,{\\pos(540,835)}OI\n",
                       encoding="utf-8")
        cmd = g.build_cam_cmd(self.dir / "in.mp4", self.dir / "out.mp4", ass, self.dir / "faixa.png", self.geo, 30.0)
        grafo = self.grafo(cmd)
        self.assertIn(f"crop={self.geo['crop_w']}:{self.geo['crop_h']}:{self.geo['crop_x']}:{self.geo['crop_y']}", grafo)
        self.assertIn(f"scale=1080:{g.CAM_TOP_HEIGHT}", grafo)                       # câmera: largura toda
        self.assertIn(f"scale=1080:{g.CAM_GAME_HEIGHT}:force_original_aspect_ratio=increase", grafo)
        self.assertIn(f"crop=1080:{g.CAM_GAME_HEIGHT}", grafo)                       # gameplay preenche o bloco
        self.assertIn("[cam][1:v][game]vstack=inputs=3", grafo)                      # câmera, faixa, gameplay
        self.assertIn("subtitles=", grafo)
        self.assertIn("fade=t=out", grafo)
        self.assertEqual(cmd[cmd.index("-map") + 1], "[vout]")
        self.assertIn("0:a?", cmd)

    def test_legenda_cai_no_meio_da_faixa(self):
        ass = self.dir / "s.ass"
        ass.write_text(STYLE + "\nDialogue: 0,0:00:01.00,0:00:02.00,Karaoke,,0,0,0,karaoke,{\\pos(540,835)}OI\n",
                       encoding="utf-8")
        g.build_cam_cmd(self.dir / "in.mp4", self.dir / "out.mp4", ass, self.dir / "faixa.png", self.geo, 30.0)
        texto = (self.dir / "s_cam.ass").read_text(encoding="utf-8")
        y = g.CAM_TOP_HEIGHT + g.CAM_SUBTITLE_CENTER_Y
        self.assertIn(f"{{\\pos(540,{y})}}", texto)
        self.assertTrue(g.CAM_TOP_HEIGHT < y < g.CAM_TOP_HEIGHT + g.CAM_BAND_HEIGHT)   # dentro da faixa
        self.assertLess(y + 60, g.CAM_TOP_HEIGHT + g.CAM_BAND_CTA_Y)                  # sem bater na linha fixa

    def test_sem_legenda(self):
        cmd = g.build_cam_cmd(self.dir / "in.mp4", self.dir / "out.mp4", None, self.dir / "faixa.png", self.geo, 30.0)
        self.assertNotIn("subtitles=", self.grafo(cmd))


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
            self.assertEqual(a.crop((0, 8, 1080, 140)).tobytes(), b.crop((0, 8, 1080, 140)).tobytes())


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
        os.environ["CORTES_LAYOUT"] = "cam"                      # o cam deixou de ser o padrão: precisa ser pedido
        self.assertTrue(self.processa(CAM_DIREITA))
        self.assertEqual(g.LAYOUT_APLICADO, "cam")
        self.assertGreater(g.CAM_AMPLIACAO, 1.0)
        self.assertEqual(len(self.chamadas), 1)
        self.assertIn("-loop", self.chamadas[0])         # a faixa entra como segunda entrada
        self.assertTrue((self.dir / "faixa_cam.png").exists())

    def test_sem_camera_cai_no_fit(self):
        self.assertTrue(self.processa(None))
        self.assertEqual(g.LAYOUT_APLICADO, "fit")

    def test_modo_ia_cai_no_fit(self):
        self.assertTrue(self.processa(CAM_CANTO, audio_mode="ia"))
        self.assertEqual(g.LAYOUT_APLICADO, "fit")

    def test_caixa_invalida_cai_no_fit(self):
        self.assertTrue(self.processa({"x": 2, "y": 0, "w": 0.2, "h": 0.2}))
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
        self.assertIn('"camera_ampliacao": CAM_AMPLIACAO', fonte)


if __name__ == "__main__":
    unittest.main()
