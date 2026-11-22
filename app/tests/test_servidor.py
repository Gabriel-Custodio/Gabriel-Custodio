"""Testes do servidor da interface (sem rede externa, sem ffmpeg, sem Groq).

Rodar na pasta do projeto:   python -m unittest discover -s app/tests -v
Cada teste sobe um servidor de verdade numa porta livre, com um gerar_cortes.py falso.
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import servidor  # noqa: E402

GERAR_FALSO = r'''
import json, os, pathlib, sys, time
print("ARGS:", sys.argv[1:], flush=True)
print("ENV:", os.environ.get("CORTES_LAYOUT"), os.environ.get("CORTES_FUNDO"), flush=True)
print("emoji 🎬 ação coração ✓", flush=True)
if "lento" in sys.argv[1]:
    time.sleep(60)
saida = pathlib.Path("cortes_prontos"); saida.mkdir(exist_ok=True)
(saida / "corte_gameplay_1_TESTE.mp4").write_bytes(bytes(range(256)) * 40)
(saida / "corte_gameplay_1_TESTE.json").write_text(json.dumps(
    {"titulo_gancho": "TÍTULO <b>X</b>", "pergunta_cta": "E aí?", "transcricao": "fala real do corte",
     "nota": 90, "fonte_titulo": "ia_trecho_real", "clip_start": 10.0, "clip_end": 50.0}, ensure_ascii=False), encoding="utf-8")
print("✅ CONCLUÍDO: 1/1 cortes gerados com sucesso", flush=True)
sys.exit(3 if "falha" in sys.argv[1] else 0)
'''

URL_OK = "https://www.youtube.com/watch?v=abc123"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self._tmp.name)
        (self.raiz / "gerar_cortes.py").write_text(GERAR_FALSO, encoding="utf-8")
        self.saida = self.raiz / "cortes_prontos"
        self.saida.mkdir()
        self.srv = servidor.criar_servidor(self.raiz, 0)
        self.porta = self.srv.server_address[1]
        self.token = self.srv.estado.token
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.pastas_abertas = []
        self._orig_abrir = servidor.abrir_pasta
        servidor.abrir_pasta = lambda p: self.pastas_abertas.append(str(p))
        self._logs_antes = set((servidor.APP_DIR / "logs").glob("*.log")) if (servidor.APP_DIR / "logs").is_dir() else set()

    def tearDown(self):
        servidor.abrir_pasta = self._orig_abrir
        self.srv.estado.parar()
        job = self.srv.estado.job
        if job and job.proc:
            try:
                job.proc.wait(timeout=10)
            except Exception:
                job.proc.kill()
        self.srv.shutdown()
        self.srv.server_close()
        self._tmp.cleanup()
        logs = servidor.APP_DIR / "logs"
        if logs.is_dir():                                   # não deixa lixo dos testes
            for f in set(logs.glob("*.log")) - self._logs_antes:
                f.unlink()

    # ---- helpers
    def req(self, metodo, caminho, corpo=None, cookie=True, host=None, extras=None, csrf=True):
        con = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=15)
        cab = {"Host": host or f"127.0.0.1:{self.porta}"}
        if cookie:
            cab["Cookie"] = f"{servidor.COOKIE}={self.token}"
        if metodo == "POST":
            cab["Content-Type"] = "application/json"
            if csrf:
                cab["X-Requested-With"] = "fabrica"
        cab.update(extras or {})
        con.request(metodo, caminho, body=json.dumps(corpo) if corpo is not None else None, headers=cab)
        resp = con.getresponse()
        dados = resp.read()
        resp.dados = dados
        con.close()
        return resp

    def json(self, metodo, caminho, corpo=None, **k):
        resp = self.req(metodo, caminho, corpo, **k)
        return resp.status, json.loads(resp.dados.decode("utf-8") or "{}")

    def criar_corte(self, nome, tamanho=1000, meta=None, mtime=None):
        arq = self.saida / nome
        arq.write_bytes(bytes(i % 251 for i in range(tamanho)))
        if meta is not None:
            arq.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        if mtime:
            os.utime(arq, (mtime, mtime))
        return arq

    def esperar_fim(self, limite=30):
        fim = time.time() + limite
        while time.time() < fim:
            _, dados = self.json("GET", "/api/log?desde=0")
            if dados["job"] and dados["job"]["estado"] != "rodando":
                return dados
            time.sleep(0.2)
        self.fail("a execução não terminou a tempo")


class TestSeguranca(Base):
    def test_pagina_inicial_define_cookie_estrito(self):
        resp = self.req("GET", "/", cookie=False)
        self.assertEqual(resp.status, 200)
        cookie = resp.getheader("Set-Cookie")
        self.assertIn(self.token, cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Fábrica de Cortes".encode("utf-8"), resp.dados)

    def test_api_exige_cookie(self):
        for caminho in ("/api/estado", "/api/cortes", "/api/log", "/video?nome=a.mp4"):
            self.assertEqual(self.req("GET", caminho, cookie=False).status, 403, caminho)

    def test_cookie_errado_negado(self):
        resp = self.req("GET", "/api/cortes", cookie=False, extras={"Cookie": f"{servidor.COOKIE}=errado"})
        self.assertEqual(resp.status, 403)

    def test_host_estranho_negado_mesmo_com_cookie(self):          # proteção contra DNS rebinding
        for host in ("evil.com", f"evil.com:{self.porta}", "192.168.0.10"):
            self.assertEqual(self.req("GET", "/api/cortes", host=host).status, 403, host)
            self.assertEqual(self.req("GET", "/", host=host).status, 403, host)

    def test_post_exige_cabecalho_e_origem_propria(self):
        corpo = {"url": URL_OK, "modo": "ia"}
        self.assertEqual(self.req("POST", "/api/executar", corpo, csrf=False).status, 403)
        resp = self.req("POST", "/api/executar", corpo, extras={"Origin": "http://evil.com"})
        self.assertEqual(resp.status, 403)
        self.assertEqual(self.req("POST", "/api/executar", corpo, cookie=False).status, 403)

    def test_static_sem_saida_da_pasta(self):
        self.assertEqual(self.req("GET", "/static/estilo.css", cookie=False).status, 200)
        self.assertEqual(self.req("GET", "/static/app.js", cookie=False).status, 200)
        for caminho in ("/static/../servidor.py", "/static/..%2Fservidor.py", "/static/.env", "/static/sub/x"):
            self.assertEqual(self.req("GET", caminho, cookie=False).status, 404, caminho)

    def test_corpo_invalido(self):
        con = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=10)
        con.request("POST", "/api/executar", body="nao e json", headers={
            "Host": f"127.0.0.1:{self.porta}", "Cookie": f"{servidor.COOKIE}={self.token}",
            "X-Requested-With": "fabrica", "Content-Type": "application/json"})
        resp = con.getresponse()
        resp.read()
        con.close()
        self.assertEqual(resp.status, 400)

    def test_chave_groq_nunca_vaza(self):
        (self.raiz / ".env").write_text("GROQ_API_KEY=gsk_segredo_super_secreto_123\n", encoding="utf-8")
        resp = self.req("GET", "/api/estado")
        self.assertNotIn(b"segredo_super", resp.dados)
        self.assertTrue(json.loads(resp.dados)["prerequisitos"]["chave_groq"])
        (self.raiz / ".env").write_text("GROQ_API_KEY=cole_sua_chave_aqui\n", encoding="utf-8")
        self.assertFalse(self.json("GET", "/api/estado")[1]["prerequisitos"]["chave_groq"])


class TestCortes(Base):
    def test_listagem(self):
        agora = time.time()
        self.criar_corte("antigo.mp4", meta={"titulo_gancho": "VELHO"}, mtime=agora - 500)
        self.criar_corte("novo.mp4", 2048, meta={"titulo_gancho": "A PONTE ME CHAMOU", "pergunta_cta": "Você faria isso?",
                                                  "transcricao": "fala real", "nota": 92, "score_selecao": 9.5,
                                                  "clip_start": 5431.8, "clip_end": 5466.8, "fonte_titulo": "ia_trecho_real"}, mtime=agora)
        self.criar_corte("sem_json.mp4", mtime=agora - 100)
        (self.saida / "_status.json").write_text("{}", encoding="utf-8")
        (self.saida / "_descartados").mkdir()
        self.criar_corte("_oculto.mp4")
        (self.saida / "texto.txt").write_text("x")
        status, dados = self.json("GET", "/api/cortes")
        nomes = [c["nome"] for c in dados["cortes"]]
        self.assertEqual(status, 200)
        self.assertEqual(nomes, ["novo.mp4", "sem_json.mp4", "antigo.mp4"])      # mais novo primeiro
        novo = dados["cortes"][0]
        self.assertEqual((novo["titulo"], novo["pergunta"], novo["nota"], novo["estado"]),
                         ("A PONTE ME CHAMOU", "Você faria isso?", 92, "novo"))
        self.assertTrue(novo["tem_json"])
        self.assertFalse(dados["cortes"][1]["tem_json"])
        self.assertEqual(novo["inicio_s"], 5431.8)

    def test_json_quebrado_nao_derruba(self):
        arq = self.criar_corte("x.mp4")
        arq.with_suffix(".json").write_text("{quebrado", encoding="utf-8")
        status, dados = self.json("GET", "/api/cortes")
        self.assertEqual((status, len(dados["cortes"])), (200, 1))

    def test_pasta_inexistente(self):
        (self.saida).rmdir()
        self.assertEqual(self.json("GET", "/api/cortes"), (200, {"cortes": []}))

    def test_video_inteiro_e_faixas(self):
        arq = self.criar_corte("v.mp4", 10000)
        tudo = arq.read_bytes()
        resp = self.req("GET", "/video?nome=v.mp4")
        self.assertEqual((resp.status, resp.getheader("Content-Type"), resp.getheader("Accept-Ranges")),
                         (200, "video/mp4", "bytes"))
        self.assertEqual(resp.dados, tudo)
        r = self.req("GET", "/video?nome=v.mp4", extras={"Range": "bytes=0-99"})
        self.assertEqual((r.status, r.getheader("Content-Range"), r.dados), (206, "bytes 0-99/10000", tudo[:100]))
        r = self.req("GET", "/video?nome=v.mp4", extras={"Range": "bytes=9000-"})
        self.assertEqual((r.status, r.dados), (206, tudo[9000:]))
        r = self.req("GET", "/video?nome=v.mp4", extras={"Range": "bytes=-50"})
        self.assertEqual((r.status, r.dados), (206, tudo[-50:]))
        r = self.req("GET", "/video?nome=v.mp4", extras={"Range": "bytes=500-999999"})
        self.assertEqual((r.status, r.getheader("Content-Range")), (206, "bytes 500-9999/10000"))
        for ruim in ("bytes=20000-", "bytes=50-10", "bytes=abc", "bytes=-"):
            self.assertEqual(self.req("GET", "/video?nome=v.mp4", extras={"Range": ruim}).status, 416, ruim)
        r = self.req("HEAD", "/video?nome=v.mp4")
        self.assertEqual((r.status, r.getheader("Content-Length"), r.dados), (200, "10000", b""))

    def test_video_nao_sai_da_pasta(self):
        (self.raiz / "segredo.mp4").write_bytes(b"x" * 10)
        self.criar_corte("_status.mp4")
        ruins = ["../segredo.mp4", "..%2Fsegredo.mp4", "%2e%2e%2fsegredo.mp4", "..\\segredo.mp4", "/etc/passwd",
                 "a.mp4%00.txt", "_status.mp4", ".oculto.mp4", "nao_existe.mp4", "", "x.txt", "sub/../v.mp4"]
        for nome in ruins:
            self.assertEqual(self.req("GET", f"/video?nome={nome}").status, 404, nome)
        self.assertEqual(self.req("GET", "/video").status, 404)

    def test_acoes_aprovar_desfazer_descartar(self):
        self.criar_corte("a.mp4", meta={"titulo_gancho": "A"})
        acao = lambda nome, a: self.json("POST", "/api/cortes/acao", {"nome": nome, "acao": a})
        self.assertEqual(acao("a.mp4", "aprovar")[0], 200)
        self.assertEqual(self.json("GET", "/api/cortes")[1]["cortes"][0]["estado"], "aprovado")
        self.assertIn("aprovado", (self.saida / "_status.json").read_text(encoding="utf-8"))
        acao("a.mp4", "desfazer")
        self.assertEqual(self.json("GET", "/api/cortes")[1]["cortes"][0]["estado"], "novo")
        self.assertEqual(acao("a.mp4", "descartar")[0], 200)
        self.assertEqual(self.json("GET", "/api/cortes")[1]["cortes"], [])
        self.assertTrue((self.saida / "_descartados" / "a.mp4").is_file())        # nada é apagado
        self.assertTrue((self.saida / "_descartados" / "a.json").is_file())
        # descartar outro com o mesmo nome: não sobrescreve o anterior
        self.criar_corte("a.mp4", meta={"titulo_gancho": "A2"})
        acao("a.mp4", "descartar")
        self.assertEqual(len(list((self.saida / "_descartados").glob("a*.mp4"))), 2)

    def test_acoes_invalidas(self):
        self.criar_corte("a.mp4")
        for corpo in ({"nome": "a.mp4", "acao": "apagar_tudo"}, {"nome": "../x.mp4", "acao": "descartar"},
                      {"nome": "nao_existe.mp4", "acao": "aprovar"}, {"acao": "aprovar"}, {"nome": 5, "acao": "aprovar"}):
            self.assertEqual(self.json("POST", "/api/cortes/acao", corpo)[0], 400, corpo)
        self.assertTrue((self.saida / "a.mp4").is_file())

    def test_abrir_pasta(self):
        self.assertEqual(self.json("POST", "/api/pasta", {})[0], 200)
        self.assertEqual(self.pastas_abertas, [str(self.srv.estado.saida)])


class TestExecucao(Base):
    def test_validacao_do_pedido(self):
        ruins = [
            {"url": "file:///etc/passwd", "modo": "ia"}, {"url": "javascript:alert(1)", "modo": "ia"},
            {"url": "--help", "modo": "ia"}, {"url": "", "modo": "ia"}, {"url": "https://x.com/a b", "modo": "ia"},
            {"url": URL_OK, "modo": "hack"}, {"url": URL_OK},
            {"url": URL_OK, "modo": "gameplay", "cortes": 0}, {"url": URL_OK, "modo": "gameplay", "cortes": 11},
            {"url": URL_OK, "modo": "gameplay", "cortes": "abc"}, {"url": URL_OK, "modo": "gameplay", "webcam": "9"},
            {"url": URL_OK, "modo": "ia", "layout": "xyz"}, {"url": URL_OK, "modo": "ia", "fundo": "rosa"},
            {"url": "https://x.com/" + "a" * 2100, "modo": "ia"},
            {"url": URL_OK, "modo": "ia", "repetir": "sim"}, {"url": URL_OK, "modo": "ia", "importar_existentes": 1},
        ]
        for corpo in ruins:
            status, dados = self.json("POST", "/api/executar", corpo)
            self.assertEqual(status, 400, corpo)
            self.assertIn("erro", dados)
        self.assertIsNone(self.srv.estado.job)                       # nada foi iniciado

    def test_execucao_completa_com_log_e_corte_novo(self):
        status, dados = self.json("POST", "/api/executar", {
            "url": URL_OK, "modo": "gameplay", "cortes": 3, "webcam": "5", "layout": "fit", "fundo": "preto"})
        self.assertEqual(status, 201)
        self.assertEqual(dados["job"]["estado"], "rodando")
        fim = self.esperar_fim()
        texto = "\n".join(fim["linhas"])
        self.assertEqual(fim["job"]["estado"], "concluido")
        self.assertIn(f"ARGS: ['{URL_OK}', '--audio', 'gameplay', '--cortes', '3', '--webcam', '5']", texto)
        self.assertIn("ENV: fit black", texto)                      # layout/fundo chegam ao processo
        self.assertIn("emoji 🎬 ação coração ✓", texto)              # acentos e emoji sem quebrar
        self.assertIn("✅ CONCLUÍDO: 1/1", texto)
        cortes = self.json("GET", "/api/cortes")[1]["cortes"]
        self.assertEqual(len(cortes), 1)
        self.assertEqual((cortes[0]["titulo"], cortes[0]["transcricao"], cortes[0]["fonte_titulo"]),
                         ("TÍTULO <b>X</b>", "fala real do corte", "ia_trecho_real"))   # texto puro, a tela usa textContent

    def test_log_incremental(self):
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "ia"})
        fim = self.esperar_fim()
        total = fim["proximo"]
        self.assertGreaterEqual(total, 5)
        _, parte = self.json("GET", f"/api/log?desde={total - 2}")
        self.assertEqual(len(parte["linhas"]), 2)
        self.assertEqual(self.json("GET", f"/api/log?desde={total}")[1]["linhas"], [])
        self.assertEqual(self.json("GET", "/api/log?desde=lixo")[0], 200)

    def test_modos_sem_flags_de_gameplay(self):
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "original", "cortes": 7, "webcam": "3"})
        texto = "\n".join(self.esperar_fim()["linhas"])
        self.assertIn(f"ARGS: ['{URL_OK}', '--audio', 'original']", texto)        # flags só no gameplay

    def test_opcoes_de_repeticao_viram_flags(self):
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 2,
                                            "repetir": True, "importar_existentes": True})
        texto = "\n".join(self.esperar_fim()["linhas"])
        self.assertIn("'--cortes', '2', '--repetir', '--importar-existentes']", texto)
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "ia", "repetir": False})
        texto = "\n".join(self.esperar_fim()["linhas"])
        self.assertNotIn("--repetir", texto.split("ARGS:")[-1])       # desmarcado: sem a flag

    def test_resumo_no_inicio_so_vira_flag_ao_desligar(self):
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 1})
        self.assertNotIn("--sem-resumo", "\n".join(self.esperar_fim()["linhas"]).split("ARGS:")[-1])  # padrão: ligado
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 1, "resumo": True})
        self.assertNotIn("--sem-resumo", "\n".join(self.esperar_fim()["linhas"]).split("ARGS:")[-1])
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 1, "resumo": False})
        self.assertIn("--sem-resumo", "\n".join(self.esperar_fim()["linhas"]))
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "ia", "resumo": False})
        self.assertNotIn("--sem-resumo", "\n".join(self.esperar_fim()["linhas"]).split("ARGS:")[-1])  # só gameplay
        for ruim in (0, 1, "nao"):
            status, _ = self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "resumo": ruim})
            self.assertEqual(status, 400, ruim)

    def test_nome_do_streamer_vira_flag_com_igual(self):
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 1, "streamer": "  Alan  zoka "})
        self.assertIn("'--streamer=Alan zoka'", "\n".join(self.esperar_fim()["linhas"]))
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 1, "streamer": ""})
        self.assertNotIn("--streamer", "\n".join(self.esperar_fim()["linhas"]).split("ARGS:")[-1])
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 1, "streamer": "--audio=ia"})
        texto = "\n".join(self.esperar_fim()["linhas"])
        self.assertIn("'--streamer=--audio=ia'", texto)            # um argumento só: não vira outra opção
        for ruim in (5, ["x"], "a" * 61, "ab\x00cd"):
            status, _ = self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "streamer": ruim})
            self.assertEqual(status, 400, repr(ruim))

    def test_codigo_de_erro(self):
        self.json("POST", "/api/executar", {"url": "https://x.com/falha", "modo": "ia"})
        fim = self.esperar_fim()
        self.assertEqual((fim["job"]["estado"], fim["job"]["codigo"]), ("erro", 3))

    def test_uma_execucao_por_vez_e_parar(self):
        self.assertEqual(self.json("POST", "/api/executar", {"url": "https://x.com/lento", "modo": "ia"})[0], 201)
        status, dados = self.json("POST", "/api/executar", {"url": URL_OK, "modo": "ia"})
        self.assertEqual(status, 409)
        self.assertIn("andamento", dados["erro"])
        self.assertEqual(self.json("POST", "/api/parar", {})[1], {"parando": True})
        fim = self.esperar_fim(20)
        self.assertEqual(fim["job"]["estado"], "cancelado")
        self.assertIn("cancelado", fim["linhas"][-1])
        self.assertEqual(self.json("POST", "/api/parar", {})[1], {"parando": False})   # nada a parar
        self.assertEqual(self.json("POST", "/api/executar", {"url": URL_OK, "modo": "ia"})[0], 201)  # liberou
        self.esperar_fim()

    def test_sem_execucao(self):
        self.assertEqual(self.json("GET", "/api/log")[1], {"linhas": [], "proximo": 0, "job": None})
        self.assertIsNone(self.json("GET", "/api/estado")[1]["job"])

    def test_script_ausente(self):
        (self.raiz / "gerar_cortes.py").unlink()
        status, dados = self.json("POST", "/api/executar", {"url": URL_OK, "modo": "ia"})
        self.assertEqual(status, 400)
        self.assertIn("gerar_cortes.py", dados["erro"])
        self.assertFalse(self.json("GET", "/api/estado")[1]["prerequisitos"]["script"])

    def test_log_gravado_em_arquivo(self):
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "ia"})
        self.esperar_fim()
        novos = set((servidor.APP_DIR / "logs").glob("*.log")) - self._logs_antes
        self.assertEqual(len(novos), 1)
        self.assertIn("CONCLUÍDO", next(iter(novos)).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
