"""Testes do título editável do corte e das sugestões da IA (gerar_cortes.py + servidor).

Tudo com mocks: nenhum ffmpeg, Groq ou rede. Rodar na pasta do projeto:  python -m unittest discover -s app/tests -v
"""
import contextlib
import io
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

APP = Path(__file__).resolve().parents[1]
RAIZ = APP.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gerar_cortes as g  # noqa: E402
import servidor  # noqa: E402
import test_editor_layout as tel  # noqa: E402
from test_editor_layout import BaseScript, OPCOES_EMPILHADO, OPCOES_INTEIRO, BaseEditor  # noqa: E402


class TestOpcoesComTitulo(unittest.TestCase):
    def test_titulo_e_pergunta_sao_limpos(self):
        o = g.normalize_edit_options({**OPCOES_INTEIRO, "titulo": "  Que   loucura \n isso  ", "pergunta": " Viu?\t"})
        self.assertEqual((o["titulo"], o["pergunta"]), ("Que loucura isso", "Viu?"))

    def test_vazio_ou_ausente_mantem_o_do_corte(self):
        for dados in (OPCOES_INTEIRO, {**OPCOES_INTEIRO, "titulo": "", "pergunta": "   "}, {**OPCOES_INTEIRO, "titulo": None}):
            o = g.normalize_edit_options(dados)
            self.assertNotIn("titulo", o)
            self.assertNotIn("pergunta", o)

    def test_limite_de_caracteres_e_controle(self):
        o = g.normalize_edit_options({**OPCOES_INTEIRO, "titulo": "a\x00b" + "x" * 100})
        self.assertEqual(len(o["titulo"]), g.HOOK_TEXT_MAX_CHARS)
        self.assertNotIn("\x00", o["titulo"])

    def test_tipo_errado_e_invalido(self):
        for ruim in (123, ["x"], {"a": 1}, True):
            self.assertIsNone(g.normalize_edit_options({**OPCOES_INTEIRO, "titulo": ruim}), repr(ruim))
            self.assertIsNone(g.normalize_edit_options({**OPCOES_INTEIRO, "pergunta": ruim}), repr(ruim))


class TestRenderComTitulo(BaseScript):
    NOME = tel.TestRenderizarEdicao.NOME
    montar = tel.TestRenderizarEdicao.montar
    renderizar = tel.TestRenderizarEdicao.renderizar
    meta = tel.TestRenderizarEdicao.meta

    def renderizar_espiando(self):
        self.hooks = []
        original = g.process_clipflow_layout

        def espia(*a, **k):
            self.hooks.append(k.get("hook_data"))
            return original(*a, **k)
        with mock.patch.object(g, "process_clipflow_layout", espia):
            return self.renderizar()

    def test_a_tarja_usa_o_titulo_escolhido_em_caixa_alta(self):
        self.montar({**OPCOES_EMPILHADO, "titulo": "bomba no cofre", "pergunta": "Você abriria?"})
        self.assertTrue(self.renderizar_espiando())
        self.assertEqual(self.hooks[0]["titulo_gancho"], "BOMBA NO COFRE")
        self.assertEqual(self.hooks[0]["pergunta_cta"], "Você abriria?")
        m = self.meta()
        self.assertEqual((m["titulo_gancho"], m["pergunta_cta"], m["fonte_titulo"]), ("BOMBA NO COFRE", "Você abriria?", "manual"))
        self.assertEqual(m["titulo_original"], {"titulo_gancho": "TÍTULO", "pergunta_cta": "E AÍ?", "fonte_titulo": None})

    def test_so_o_titulo_muda_a_pergunta_continua(self):
        self.montar({**OPCOES_EMPILHADO, "titulo": "novo titulo"})
        self.assertTrue(self.renderizar_espiando())
        self.assertEqual((self.hooks[0]["titulo_gancho"], self.hooks[0]["pergunta_cta"]), ("NOVO TITULO", "E AÍ?"))
        self.assertEqual(self.meta()["pergunta_cta"], "E AÍ?")

    def test_o_titulo_original_guarda_o_primeiro_e_nao_o_ultimo(self):
        self.montar({**OPCOES_EMPILHADO, "titulo": "primeiro"})
        self.assertTrue(self.renderizar_espiando())
        (self.out / "_edicoes" / "corte_gameplay_1_X.json").write_text(json.dumps({**OPCOES_EMPILHADO, "titulo": "segundo"}), encoding="utf-8")
        self.assertTrue(self.renderizar_espiando())
        m = self.meta()
        self.assertEqual((m["titulo_gancho"], m["titulo_original"]["titulo_gancho"]), ("SEGUNDO", "TÍTULO"))

    def test_sem_titulo_novo_nada_muda_no_texto_do_corte(self):
        self.montar(OPCOES_EMPILHADO, meta_extra={"fonte_titulo": "ia_texto_real"})
        self.assertTrue(self.renderizar_espiando())
        self.assertEqual(self.hooks[0]["titulo_gancho"], "TÍTULO")
        m = self.meta()
        self.assertEqual((m["titulo_gancho"], m["fonte_titulo"]), ("TÍTULO", "ia_texto_real"))
        self.assertNotIn("titulo_original", m)

    def test_sem_tarja_o_titulo_nao_e_desenhado_mas_fica_salvo(self):
        self.montar({**OPCOES_EMPILHADO, "tarja": False, "titulo": "para copiar"})
        self.assertTrue(self.renderizar_espiando())
        self.assertIsNone(self.hooks[0])
        self.assertEqual(self.meta()["titulo_gancho"], "PARA COPIAR")

    def test_falha_do_ffmpeg_nao_troca_o_titulo(self):
        self.montar({**OPCOES_EMPILHADO, "titulo": "novo"})
        self.assertFalse(self.renderizar(ffmpeg_ok=False))
        self.assertEqual(self.meta()["titulo_gancho"], "TÍTULO")


class TestSugerirTitulos(BaseScript):
    RESPOSTA = json.dumps([
        {"titulo_gancho": "Bomba no cofre!", "pergunta_cta": "você abriria?"},
        {"titulo_gancho": "ISSO DEU ERRADO", "pergunta_cta": "quem erra"},
        {"titulo_gancho": "isso deu errado", "pergunta_cta": "repetido"},               # repetido (mesmo título)
        {"titulo_gancho": "TÍTULO ATUAL", "pergunta_cta": "igual ao atual"},             # igual ao atual
        {"titulo_gancho": "", "pergunta_cta": "sem título"},
        "lixo",
        {"titulo_gancho": "UM DOIS TRÊS QUATRO CINCO SEIS", "pergunta_cta": "a b c d e f"},
    ], ensure_ascii=False)

    def test_limpa_dedupe_e_limita(self):
        with mock.patch.object(g, "groq_chat_with_fallback", return_value=self.RESPOSTA) as chat:
            r = g.suggest_titles("olha só essa fala do corte", "Título atual", "chave")
        self.assertEqual([x["titulo"] for x in r], ["BOMBA NO COFRE", "ISSO DEU ERRADO", "UM DOIS TRÊS QUATRO CINCO"])
        self.assertEqual(r[0]["pergunta"], "você abriria?")                                   # a limpeza só acerta o "?" e o tamanho
        self.assertEqual(r[2]["pergunta"], "a b c d?")
        prompt = chat.call_args[0][0][0]["content"]
        self.assertIn("olha só essa fala do corte", prompt)
        self.assertIn("Título atual", prompt)
        self.assertIn("NÃO invente", prompt)

    def test_respeita_a_quantidade(self):
        with mock.patch.object(g, "groq_chat_with_fallback", return_value=self.RESPOSTA):
            self.assertEqual(len(g.suggest_titles("fala", "", "chave", count=2)), 2)

    def test_sem_texto_sem_chave_ou_resposta_ruim_devolve_lista_vazia(self):
        with mock.patch.object(g, "groq_chat_with_fallback", return_value=self.RESPOSTA):
            self.assertEqual(g.suggest_titles("", "x", "chave"), [])
            self.assertEqual(g.suggest_titles("fala", "x", None), [])
        for ruim in (None, "", "não sei", '{"titulo_gancho": "x"}'):
            with mock.patch.object(g, "groq_chat_with_fallback", return_value=ruim):
                self.assertEqual(g.suggest_titles("fala", "x", "chave"), [], repr(ruim))

    def preparar(self, meta=None):
        self.out.mkdir(exist_ok=True)
        (self.out / "corte_1.mp4").write_bytes(b"x" * 100)
        (self.out / "corte_1.json").write_text(json.dumps({"titulo_gancho": "TÍTULO ATUAL", "transcricao": "fala do corte aqui", **(meta or {})}),
                                               encoding="utf-8")

    def rodar(self, nome="corte_1.mp4", chave="k", resposta=None):
        saida = io.StringIO()
        with mock.patch.object(g, "load_env_file", lambda *a, **k: None), mock.patch.object(g, "get_groq_key", lambda: chave), \
                mock.patch.object(g, "groq_chat_with_fallback", return_value=self.RESPOSTA if resposta is None else resposta), \
                contextlib.redirect_stdout(saida):
            ok = g.suggest_titles_for_clip(nome)
        return ok, saida.getvalue()

    def test_modo_da_linha_de_comando_imprime_o_json(self):
        self.preparar()
        ok, texto = self.rodar()
        self.assertTrue(ok)
        linha = next(l for l in texto.splitlines() if l.startswith("TITULOS_JSON="))
        itens = json.loads(linha[len("TITULOS_JSON="):])
        self.assertEqual(itens[0], {"titulo": "BOMBA NO COFRE", "pergunta": "você abriria?"})

    def test_falhas_do_modo_da_linha_de_comando(self):
        self.preparar()
        self.assertFalse(self.rodar(chave=None)[0])
        self.assertIn("GROQ_API_KEY", self.rodar(chave=None)[1])
        self.assertFalse(self.rodar(resposta="não sei")[0])
        for ruim in ("../x.mp4", "_x.mp4", "x.txt", "nao_existe.mp4"):
            self.assertFalse(self.rodar(nome=ruim)[0], ruim)
        self.preparar({"transcricao": "  "})
        ok, texto = self.rodar()
        self.assertFalse(ok)
        self.assertIn("transcrição", texto)

    def test_flag_existe(self):
        self.assertIn("--sugerir-titulos", (RAIZ / "gerar_cortes.py").read_text(encoding="utf-8"))


GERAR_TITULOS = r'''
import json, sys, time
print("ARGS:", sys.argv[1:], flush=True)
modo = open("modo.txt", encoding="utf-8").read().strip() if __import__("os").path.exists("modo.txt") else "ok"
if modo == "lento":
    time.sleep(30)
if modo == "erro":
    print("❌ A IA não devolveu sugestões agora (limite ou instabilidade). Tente de novo em instantes.", flush=True)
    sys.exit(1)
if modo == "lixo":
    print("TITULOS_JSON=isso nao e json", flush=True)
    sys.exit(0)
print("TITULOS_JSON=" + json.dumps([{"titulo": "BOMBA NO COFRE", "pergunta": "Você abriria?"}, {"titulo": "<b>x</b>", "pergunta": ""},
                                    {"titulo": "  ", "pergunta": "sem titulo"}, "lixo"], ensure_ascii=False), flush=True)
'''


class TestRotaDeSugestoes(BaseEditor):
    NOME = "corte_gameplay_1_TESTE.mp4"

    def setUp(self):
        super().setUp()
        (self.raiz / "gerar_cortes.py").write_text(GERAR_TITULOS, encoding="utf-8")
        (self.raiz / ".env").write_text("GROQ_API_KEY=gsk_teste\n", encoding="utf-8")
        import os
        antiga = os.environ.pop("GROQ_API_KEY", None)
        self.addCleanup(lambda: os.environ.__setitem__("GROQ_API_KEY", antiga) if antiga else os.environ.pop("GROQ_API_KEY", None))
        self.criar_corte(self.NOME, 600, meta={"titulo_gancho": "T", "transcricao": "fala do corte"})

    def sugerir(self, nome=None):
        return self.json("POST", "/api/cortes/titulos", {"nome": nome or self.NOME})

    def test_devolve_as_sugestoes_limpas(self):
        status, d = self.sugerir()
        self.assertEqual(status, 200)
        self.assertEqual(d["titulos"], [{"titulo": "BOMBA NO COFRE", "pergunta": "Você abriria?"}, {"titulo": "<b>x</b>", "pergunta": ""}])

    def test_o_recurso_aparece_no_estado(self):
        self.assertIn("titulo_editavel", self.json("GET", "/api/estado")[1]["recursos"])

    def test_erros_viram_mensagem_para_a_tela(self):
        self.assertEqual(self.sugerir("nao_existe.mp4")[0], 400)
        self.assertEqual(self.sugerir("../x.mp4")[0], 400)
        self.criar_corte("sem_texto.mp4", 600, meta={"titulo_gancho": "T"})
        status, d = self.sugerir("sem_texto.mp4")
        self.assertEqual(status, 400)
        self.assertIn("transcrição", d["erro"])
        (self.raiz / ".env").write_text("GROQ_API_KEY=\n", encoding="utf-8")
        status, d = self.sugerir()
        self.assertEqual(status, 400)
        self.assertIn("GROQ_API_KEY", d["erro"])

    def test_falha_da_ia_mostra_o_motivo(self):
        (self.raiz / "modo.txt").write_text("erro", encoding="utf-8")
        status, d = self.sugerir()
        self.assertEqual(status, 400)
        self.assertIn("A IA não devolveu sugestões", d["erro"])
        (self.raiz / "modo.txt").write_text("lixo", encoding="utf-8")
        self.assertEqual(self.sugerir()[0], 400)

    def test_demora_demais(self):
        (self.raiz / "modo.txt").write_text("lento", encoding="utf-8")
        antigo = servidor.SUGERIR_TIMEOUT_S
        servidor.SUGERIR_TIMEOUT_S = 1
        self.addCleanup(setattr, servidor, "SUGERIR_TIMEOUT_S", antigo)
        t = time.time()
        status, d = self.sugerir()
        self.assertEqual(status, 400)
        self.assertIn("demorou demais", d["erro"])
        self.assertLess(time.time() - t, 10)

    def test_seguranca(self):
        self.assertEqual(self.json("POST", "/api/cortes/titulos", {"nome": self.NOME}, cookie=False)[0], 403)
        self.assertEqual(self.json("POST", "/api/cortes/titulos", {"nome": self.NOME}, csrf=False)[0], 403)


class TestEdicaoComTituloNoServidor(BaseEditor):
    NOME = "corte_gameplay_1_TESTE.mp4"

    def setUp(self):
        super().setUp()
        self.criar_corte(self.NOME, 600, meta={"titulo_gancho": "T", "bruto": True, "edicao": {"estado": "bruto"}})
        (self.saida / "_brutos").mkdir(exist_ok=True)
        (self.saida / "_brutos" / self.NOME).write_bytes(b"BRUTO" * 300)

    def opcoes_gravadas(self):
        return json.loads((self.saida / "_edicoes" / f"{Path(self.NOME).stem}.json").read_text(encoding="utf-8"))

    def test_titulo_e_pergunta_vao_para_as_opcoes_limpos(self):
        status, _ = self.json("POST", "/api/cortes/editar", {"nome": self.NOME, "opcoes": {**OPCOES_INTEIRO, "titulo": "  Bomba   no cofre\n", "pergunta": " Viu? "}})
        self.assertEqual(status, 201)
        o = self.opcoes_gravadas()
        self.assertEqual((o["titulo"], o["pergunta"]), ("Bomba no cofre", "Viu?"))

    def test_sem_titulo_nao_grava_a_chave(self):
        self.assertEqual(self.json("POST", "/api/cortes/editar", {"nome": self.NOME, "opcoes": {**OPCOES_INTEIRO, "titulo": "", "pergunta": " "}})[0], 201)
        self.assertNotIn("titulo", self.opcoes_gravadas())
        self.assertNotIn("pergunta", self.opcoes_gravadas())

    def test_titulo_longo_e_cortado_e_tipo_errado_recusado(self):
        self.assertEqual(self.json("POST", "/api/cortes/editar", {"nome": self.NOME, "opcoes": {**OPCOES_INTEIRO, "titulo": "x" * 200}})[0], 201)
        self.assertEqual(len(self.opcoes_gravadas()["titulo"]), servidor.TITULO_MAX)
        for ruim in (5, ["a"], True):
            self.assertEqual(self.json("POST", "/api/cortes/editar", {"nome": self.NOME, "opcoes": {**OPCOES_INTEIRO, "titulo": ruim}})[0], 400, repr(ruim))

    def test_as_duas_validacoes_concordam_no_limite(self):
        self.assertEqual(servidor.TITULO_MAX, g.HOOK_TEXT_MAX_CHARS)


if __name__ == "__main__":
    unittest.main()
