"""Testes das opções --webcam/--cortes do gerar_cortes.py (as que a interface usa) e do .json dos cortes.

Rodar na pasta do projeto:   python -m unittest discover -s app/tests -v
"""
import builtins
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
import gerar_cortes as g  # noqa: E402


class TestWebcam(unittest.TestCase):
    def test_escolha_direta_sem_perguntar(self):
        antigo = builtins.input
        builtins.input = lambda *a: self.fail("não deveria perguntar no terminal")
        try:
            pos = g.ask_webcam_position(choice="5")
            self.assertEqual((pos["x"], pos["y"], pos["w"], pos["h"]), (0.772, 0.45, 0.222, 0.242))
            self.assertEqual(g.ask_webcam_position(choice="1")["name"], "Canto inferior esquerdo")
            self.assertIsNone(g.ask_webcam_position(choice="7"))           # sem webcam = None (válido)
            self.assertIs(g.ask_webcam_position(choice="9"), False)         # inválido = False (cancela)
            self.assertIs(g.ask_webcam_position(choice="abc"), False)
        finally:
            builtins.input = antigo

    def test_opcao_6_sem_chave_cai_para_sem_webcam(self):
        self.assertIsNone(g.ask_webcam_position(video_path="x.mp4", api_key=None, choice="6"))

    def test_cancelar_no_terminal_e_diferente_de_sem_webcam(self):
        antigo = builtins.input

        def eof(*a):
            raise EOFError
        builtins.input = eof
        try:
            self.assertIs(g.ask_webcam_position(), False)                   # cancelado
        finally:
            builtins.input = antigo
        builtins.input = lambda *a: "7"
        try:
            self.assertIsNone(g.ask_webcam_position())                      # "sem webcam": segue em frente
        finally:
            builtins.input = antigo


class TestOpcoesDoPrograma(unittest.TestCase):
    def test_help_lista_as_opcoes(self):
        saida = subprocess.run([sys.executable, "-I", str(RAIZ / "gerar_cortes.py"), "--help"],
                               capture_output=True, text=True, encoding="utf-8", cwd=str(RAIZ), timeout=60).stdout
        for opcao in ("--webcam", "--cortes", "--audio", "--repair"):
            self.assertIn(opcao, saida)


class TestJsonDoCorte(unittest.TestCase):
    def test_save_clip_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp4 = Path(tmp) / "corte_1_X.mp4"
            clip = {"text": "fala real", "start": 10.0, "end": 50.0, "duration": 40.0, "score": 7.5}
            g.save_clip_metadata(mp4, clip, {"titulo_gancho": "TÍTULO", "pergunta_cta": "E AGORA?", "nota": 80}, "ia")
            meta = json.loads((Path(tmp) / "corte_1_X.json").read_text(encoding="utf-8"))
            self.assertEqual((meta["titulo_gancho"], meta["transcricao"], meta["modo"], meta["clip_start"]),
                             ("TÍTULO", "fala real", "ia", 10.0))

    def test_falha_ao_gravar_nao_levanta(self):
        g.save_clip_metadata(Path("/pasta/que/nao/existe/x.mp4"), {}, None, "original")      # só avisa


if __name__ == "__main__":
    unittest.main(verbosity=2)
