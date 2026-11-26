"""Conferências estáticas da tela (sem navegador): todo id usado pelos scripts existe no HTML, os arquivos têm sintaxe válida,
os blocos do editor têm o tipo certo (data-so) e a aba "Vídeos longos" está ligada.

Rodar na pasta do projeto:   python -m unittest discover -s app/tests -v
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ESTATICO = Path(__file__).resolve().parents[1] / "static"
HTML = (ESTATICO / "index.html").read_text(encoding="utf-8")
SCRIPTS = ["descricao.js", "app.js", "timeline-logica.js", "timeline.js", "editor.js", "lives.js", "longos.js"]


def ids_do_html():
    return set(re.findall(r'\bid="([^"]+)"', HTML))


class TestTela(unittest.TestCase):
    def test_todo_id_usado_nos_scripts_existe_no_html(self):
        ids = ids_do_html()
        faltando = []
        for nome in SCRIPTS:
            codigo = (ESTATICO / nome).read_text(encoding="utf-8")
            usados = set(re.findall(r'\$\("([A-Za-z0-9_\-]+)"\)', codigo)) | set(re.findall(r'getElementById\("([^"]+)"\)', codigo))
            faltando += [(nome, i) for i in sorted(usados - ids)]
        self.assertEqual(faltando, [])

    def test_ids_do_html_nao_se_repetem(self):
        todos = re.findall(r'\bid="([^"]+)"', HTML)
        repetidos = sorted({i for i in todos if todos.count(i) > 1})
        self.assertEqual(repetidos, [])

    def test_scripts_carregados_na_ordem_certa(self):
        ordem = re.findall(r'<script src="/static/([^"]+)"', HTML)
        self.assertEqual(ordem, SCRIPTS)

    def test_aba_dos_videos_longos_esta_ligada(self):
        self.assertIn('id="aba-longos"', HTML)
        self.assertIn('aria-controls="painel-longos"', HTML)
        self.assertIn('id="painel-longos"', HTML)
        self.assertIn('id="tpl-longo"', HTML)
        app = (ESTATICO / "app.js").read_text(encoding="utf-8")
        self.assertIn('["gerar", "cortes", "longos", "lives"]', app)
        for nome in ("aba-longos", "painel-longos"):
            self.assertIn(f'id="{nome}"', HTML)

    def test_editor_tem_caixa_livre_e_espelhar(self):
        for nome in ("ed-espelhar", "ed-ajustar-bloco"):
            self.assertIn(f'id="{nome}"', HTML)
        js = (ESTATICO / "editor.js").read_text(encoding="utf-8")
        for lado in ("nw", "ne", "sw", "se", "n", "s", "e", "w"):
            self.assertIn(f'"{lado}"', js)
        css = (ESTATICO / "estilo.css").read_text(encoding="utf-8")
        for lado in ("n", "s", "e", "w"):
            self.assertIn(f".ed-alca.{lado} ", css)
        self.assertIn("espelhar", js)

    def test_editor_do_longo_tem_o_recorte_livre(self):
        for nome in ("ed-bloco-recorte", "ed-rec-usar", "ed-rec-quadro", "ed-rec-tudo", "ed-rec-quadro-area", "ed-rec-paisagem",
                     "ed-rec-img", "ed-rec", "ed-rec-previa", "ed-rec-nitidez"):
            self.assertIn(f'id="{nome}"', HTML)
        self.assertRegex(HTML, r'id="ed-bloco-recorte"[^>]*data-so="longo"|data-so="longo"[^>]*id="ed-bloco-recorte"')
        js = (ESTATICO / "editor.js").read_text(encoding="utf-8")
        self.assertIn("lo.recorte = ED.rec", js)
        self.assertIn('["cam", "game", "rec"]', js)
        self.assertIn(".ed-caixa.rec", (ESTATICO / "estilo.css").read_text(encoding="utf-8"))

    def test_o_template_do_longo_tem_o_que_o_script_procura(self):
        molde = HTML[HTML.index('<template id="tpl-longo">'):HTML.index("</template>", HTML.index('<template id="tpl-longo">'))]
        codigo = (ESTATICO / "longos.js").read_text(encoding="utf-8")
        for seletor in set(re.findall(r'querySelector\("([^"]+)"\)', codigo)):
            alvo = seletor.lstrip(".")
            self.assertTrue(alvo in molde or seletor == "video", seletor)

    def test_blocos_do_editor_tem_tipo(self):
        tipos = re.findall(r'data-so="([^"]+)"', HTML)
        self.assertTrue(tipos)
        self.assertTrue(set(tipos) <= {"curto", "longo"})
        # o que só vale para o vídeo vertical está marcado como curto
        for ident in ("ed-pegar-quadro", "ed-campo-pergunta", "ed-caixas"):
            self.assertRegex(HTML, rf'id="{ident}"[^>]*data-so="curto"|data-so="curto"[^>]*id="{ident}"')
        self.assertRegex(HTML, r'id="ed-bloco-momentos" data-so="longo"')
        self.assertRegex(HTML, r'data-so="longo" hidden><input type="checkbox" id="ed-capitulos"')

    def test_sintaxe_dos_scripts(self):
        gjs = shutil.which("gjs")
        if not gjs:
            self.skipTest("gjs não instalado")
        for nome in SCRIPTS:
            r = subprocess.run([gjs, "-c", f"new Function(imports.gi.GLib.file_get_contents('{ESTATICO / nome}')[1].toString())"],
                               capture_output=True, text=True, timeout=60)
            self.assertNotIn("SyntaxError", r.stderr, nome)
            self.assertEqual(r.returncode, 0, f"{nome}: {r.stderr[-300:]}")

    def test_o_servidor_manda_os_recursos_que_a_pagina_exige(self):
        app = (ESTATICO / "app.js").read_text(encoding="utf-8")
        exigidos = set(re.findall(r'estado\.recursos\.includes\("([a-z_]+)"\)', app))
        servidor = (ESTATICO.parent / "servidor.py").read_text(encoding="utf-8")
        recursos = set(re.findall(r'"([a-z_]+)"', re.search(r"RECURSOS = \[(.*?)\]", servidor).group(1)))
        self.assertTrue(exigidos <= recursos, exigidos - recursos)

    def test_cada_funcao_chamada_entre_scripts_existe(self):
        todos = "\n".join((ESTATICO / n).read_text(encoding="utf-8") for n in SCRIPTS)
        for funcao in ("carregarLongos", "atualizarContadorLongos", "edAbrir", "carregarCortes", "edAtualizarMomentos",
                       "tlMarcarIntervalo", "tlFracaoMantida", "selo", "formatarTempo"):
            self.assertRegex(todos, rf"function {funcao}\b|const {funcao}\b", funcao)


if __name__ == "__main__":
    unittest.main()
