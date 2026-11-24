"""Testes do gerenciador de lives (app/lives.py) e das rotas /api/lives.

O "monitor" aqui é um script falso (sem rede, sem ffmpeg): grava status.json como o de verdade e sai quando vê parar.flag.
Rodar na pasta do projeto:  python -m unittest discover -s app/tests -v
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lives  # noqa: E402
from canais import RegistroCanais  # noqa: E402
from test_servidor import Base  # noqa: E402

MONITOR_FALSO = r'''
import json, os, sys, time
from datetime import datetime
from pathlib import Path
raiz = Path(sys.argv[sys.argv.index("--raiz") + 1])
pid = sys.argv[sys.argv.index("--id") + 1]
modo = os.environ.get("MONITOR_FALSO_MODO", "normal")
pasta = raiz / "cortes_prontos" / "_lives" / pid
pasta.mkdir(parents=True, exist_ok=True)
print("[00:00:00] [INFO] monitor falso iniciado", flush=True)
if modo == "erro":
    print("[00:00:01] [ERROR] ffmpeg não encontrado", flush=True)
    sys.exit(4)
if modo == "ignora":                                   # não obedece ao parar.flag (para testar a parada à força)
    while True:
        time.sleep(0.2)
n = 0
while not (pasta / "parar.flag").exists():
    n += 1
    status = {"id": pid, "estado": "gravando", "detalhe": "", "erro": "", "pid": os.getpid(), "tempo_monitor_s": n * 0.2,
              "momentos": 1, "sinais_audio": 2, "sinais_chat": 0, "sinais_palavra": 0, "mensagens_chat": 5, "chat": "ligado",
              "atualizado_em": datetime.now().isoformat(timespec="seconds")}
    (pasta / "status.json.tmp").write_text(json.dumps(status), encoding="utf-8")
    (pasta / "status.json.tmp").replace(pasta / "status.json")
    if n == 1:
        (pasta / "momentos.jsonl").write_text(json.dumps({"nota": 7.5, "sinais": {"audio": 1, "chat": 1}, "em": "2026-11-24T20:00:00"}) + "\n", encoding="utf-8")
    time.sleep(0.2)
print("[00:00:05] [INFO] monitor falso parando", flush=True)
'''


def esperar(condicao, limite=10.0):
    fim = time.time() + limite
    while time.time() < fim:
        if condicao():
            return True
        time.sleep(0.05)
    return False


class BaseGerente(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name)
        self.script = self.raiz / "monitor_falso.py"
        self.script.write_text(MONITOR_FALSO, encoding="utf-8")
        self.registro = RegistroCanais(self.raiz / "canais_permitidos.json")
        self.registro.salvar({"canal": "https://www.twitch.tv/alanzoka"})
        self.registro.salvar({"canal": "https://www.twitch.tv/mrfalll"})
        self.gerente = lives.GerenteLives(self.raiz, self.registro,
                                          comando_monitor=lambda url, pid: [sys.executable, str(self.script), "--url", url, "--id", pid, "--raiz", str(self.raiz)])
        self.gerente.pasta_logs = self.raiz / "logs"
        self.addCleanup(self.gerente.parar_todas, 3.0)

    def estado(self, canal_id):
        return next(l for l in self.gerente.listar()["lives"] if l["canal"]["id"] == canal_id)


class TestGerente(BaseGerente):
    def test_pasta_id(self):
        self.assertEqual(lives.pasta_id("twitch:alanzoka"), "twitch-alanzoka")
        self.assertEqual(lives.pasta_id("youtube:@Fulano"), "youtube-fulano")

    def test_antes_de_iniciar_tudo_parado(self):
        d = self.gerente.listar()
        self.assertEqual(d["maximo"], 2)
        self.assertEqual(d["rodando"], 0)
        self.assertEqual({l["estado"] for l in d["lives"]}, {"parado"})
        self.assertFalse(any(l["rodando"] for l in d["lives"]))

    def test_inicia_mostra_status_e_para(self):
        self.gerente.iniciar("twitch:alanzoka")
        self.assertTrue(esperar(lambda: self.estado("twitch:alanzoka")["estado"] == "gravando"))
        e = self.estado("twitch:alanzoka")
        self.assertTrue(e["rodando"])
        self.assertEqual(e["total_momentos"], 1)
        self.assertEqual(e["momentos"][0]["nota"], 7.5)
        self.assertEqual(e["mensagens_chat"], 5)
        self.assertFalse(self.estado("twitch:mrfalll")["rodando"])                    # o outro canal continua parado
        self.assertIn("monitor falso iniciado", "\n".join(self.gerente.log("twitch:alanzoka")[0]))
        self.assertTrue(self.gerente.parar("twitch:alanzoka"))
        self.assertTrue(esperar(lambda: not self.estado("twitch:alanzoka")["rodando"]))
        e = self.estado("twitch:alanzoka")
        self.assertEqual((e["estado"], e["codigo"]), ("parado", 0))
        self.assertFalse(self.gerente.parar("twitch:alanzoka"))                       # já parou

    def test_comando_do_monitor_usa_o_link_do_canal_e_a_raiz(self):
        canal = self.registro.listar()[0]
        g = lives.GerenteLives(self.raiz, self.registro, python="PY")
        self.assertEqual(g._comando(canal), ["PY", "-m", "monitor", "--url", "https://www.twitch.tv/" + canal["canal"],
                                            "--id", "twitch-" + canal["canal"], "--raiz", str(self.raiz)])

    def test_nao_inicia_duas_vezes_o_mesmo_canal(self):
        self.gerente.iniciar("twitch:alanzoka")
        with self.assertRaisesRegex(ValueError, "já está assistindo"):
            self.gerente.iniciar("twitch:alanzoka")

    def test_limite_de_lives_simultaneas(self):
        pequeno = lives.MAX_SIMULTANEAS
        lives.MAX_SIMULTANEAS = 1
        self.addCleanup(setattr, lives, "MAX_SIMULTANEAS", pequeno)
        self.gerente.iniciar("twitch:alanzoka")
        with self.assertRaisesRegex(ValueError, "máximo"):
            self.gerente.iniciar("twitch:mrfalll")

    def test_duas_lives_ao_mesmo_tempo(self):
        self.gerente.iniciar("twitch:alanzoka")
        self.gerente.iniciar("twitch:mrfalll")
        self.assertTrue(esperar(lambda: all(self.estado(c)["estado"] == "gravando" for c in ("twitch:alanzoka", "twitch:mrfalll"))))
        self.assertEqual(self.gerente.listar()["rodando"], 2)
        self.gerente.parar_todas(5.0)
        self.assertEqual(self.gerente.listar()["rodando"], 0)

    def test_recusa_canal_fora_da_lista_ou_suspenso(self):
        with self.assertRaisesRegex(ValueError, "não está na lista"):
            self.gerente.iniciar("twitch:desconhecido")
        self.registro.definir_ativo("twitch:alanzoka", False)
        with self.assertRaisesRegex(ValueError, "suspenso"):
            self.gerente.iniciar("twitch:alanzoka")
        self.assertEqual(self.gerente.listar()["rodando"], 0)

    def test_falha_do_monitor_aparece_como_erro(self):
        import os
        os.environ["MONITOR_FALSO_MODO"] = "erro"
        self.addCleanup(os.environ.pop, "MONITOR_FALSO_MODO", None)
        self.gerente.iniciar("twitch:alanzoka")
        self.assertTrue(esperar(lambda: not self.estado("twitch:alanzoka")["rodando"]))
        e = self.estado("twitch:alanzoka")
        self.assertEqual(e["estado"], "erro")
        self.assertIn("ffmpeg", e["erro"])

    def test_pausar_e_retomar(self):
        with self.assertRaisesRegex(ValueError, "não está rodando"):
            self.gerente.pausar("twitch:alanzoka", True)
        self.gerente.iniciar("twitch:alanzoka")
        self.assertTrue(esperar(lambda: self.estado("twitch:alanzoka")["estado"] == "gravando"))
        self.gerente.pausar("twitch:alanzoka", True)
        self.assertTrue(self.estado("twitch:alanzoka")["pausado"])
        self.gerente.pausar("twitch:alanzoka", False)
        self.assertFalse(self.estado("twitch:alanzoka")["pausado"])

    def test_parada_a_forca_quando_o_monitor_nao_obedece(self):
        import os
        os.environ["MONITOR_FALSO_MODO"] = "ignora"
        self.addCleanup(os.environ.pop, "MONITOR_FALSO_MODO", None)
        self.gerente.iniciar("twitch:alanzoka")
        self.gerente.parar("twitch:alanzoka")
        exe = self.gerente.execucoes["twitch:alanzoka"]
        self.gerente._forcar_parada(exe, limite=0.5)
        self.assertTrue(esperar(lambda: not exe.rodando))
        self.assertIn("encerrando o processo", "\n".join(exe.desde(0)[0]))

    def test_status_de_execucao_anterior_nao_vale(self):
        pasta = self.raiz / "cortes_prontos" / "_lives" / "twitch-alanzoka"
        pasta.mkdir(parents=True)
        (pasta / "status.json").write_text(json.dumps({"estado": "gravando", "pid": 1}), encoding="utf-8")
        self.assertEqual(self.estado("twitch:alanzoka")["estado"], "parado")
        self.gerente.iniciar("twitch:alanzoka")                                       # começa limpo: apaga o status velho
        self.assertTrue(esperar(lambda: self.estado("twitch:alanzoka")["estado"] == "gravando"))
        self.assertNotEqual(json.loads((pasta / "status.json").read_text(encoding="utf-8"))["pid"], 1)


class TestRotasDeLives(Base):
    def setUp(self):
        super().setUp()
        script = self.raiz / "monitor_falso.py"
        script.write_text(MONITOR_FALSO, encoding="utf-8")
        g = self.srv.estado.lives
        g.pasta_logs = self.raiz / "logs"
        g.comando_monitor = lambda url, pid: [sys.executable, str(script), "--url", url, "--id", pid, "--raiz", str(self.raiz)]
        self.json("POST", "/api/canais", {"canal": "https://www.twitch.tv/alanzoka"})

    def tearDown(self):
        self.srv.estado.lives.parar_todas(3.0)
        super().tearDown()

    def estado_live(self):
        return self.json("GET", "/api/lives")[1]["lives"][0]

    def test_estado_informa_o_recurso(self):
        self.assertIn("monitor_lives", self.json("GET", "/api/estado")[1]["recursos"])

    def test_fluxo(self):
        s, d = self.json("GET", "/api/lives")
        self.assertEqual((s, d["maximo"], d["rodando"], d["lives"][0]["estado"]), (200, 2, 0, "parado"))
        s, r = self.json("POST", "/api/lives/iniciar", {"id": "twitch:alanzoka"})
        self.assertEqual(s, 201)
        self.assertTrue(esperar(lambda: self.estado_live()["estado"] == "gravando"))
        self.assertEqual(self.json("POST", "/api/lives/iniciar", {"id": "twitch:alanzoka"})[0], 409)   # já rodando
        self.assertEqual(self.json("POST", "/api/lives/pausar", {"id": "twitch:alanzoka", "pausar": True})[0], 200)
        self.assertTrue(self.estado_live()["pausado"])
        self.assertEqual(self.json("POST", "/api/lives/pausar", {"id": "twitch:alanzoka", "pausar": "sim"})[0], 400)
        linhas = self.json("GET", "/api/lives/log?id=twitch%3Aalanzoka&desde=0")[1]
        self.assertTrue(linhas["linhas"])
        self.assertEqual(self.json("POST", "/api/lives/parar", {"id": "twitch:alanzoka"})[0], 200)
        self.assertTrue(esperar(lambda: not self.estado_live()["rodando"]))
        self.assertEqual(self.json("POST", "/api/lives/parar", {"id": "twitch:alanzoka"})[0], 404)

    def test_iniciar_recusado(self):
        self.assertEqual(self.json("POST", "/api/lives/iniciar", {"id": "twitch:outro"})[0], 409)
        self.assertEqual(self.json("POST", "/api/lives/iniciar", {})[0], 409)
        self.assertEqual(self.json("POST", "/api/lives/pausar", {"id": "twitch:alanzoka", "pausar": True})[0], 409)

    def test_suspender_ou_apagar_o_canal_para_o_monitor(self):
        self.json("POST", "/api/lives/iniciar", {"id": "twitch:alanzoka"})
        self.assertTrue(esperar(lambda: self.estado_live()["estado"] == "gravando"))
        self.json("POST", "/api/canais/ativo", {"id": "twitch:alanzoka", "ativo": False})
        self.assertTrue(esperar(lambda: not self.estado_live()["rodando"]))
        self.json("POST", "/api/canais/ativo", {"id": "twitch:alanzoka", "ativo": True})
        self.json("POST", "/api/lives/iniciar", {"id": "twitch:alanzoka"})
        self.assertTrue(esperar(lambda: self.estado_live()["estado"] == "gravando"))
        self.json("POST", "/api/canais/apagar", {"id": "twitch:alanzoka"})
        exe = self.srv.estado.lives.execucoes["twitch:alanzoka"]
        self.assertTrue(esperar(lambda: not exe.rodando))

    def test_seguranca(self):
        for metodo, caminho, corpo in (("GET", "/api/lives", None), ("GET", "/api/lives/log?id=x", None),
                                       ("POST", "/api/lives/iniciar", {"id": "twitch:alanzoka"}),
                                       ("POST", "/api/lives/parar", {"id": "twitch:alanzoka"}),
                                       ("POST", "/api/lives/pausar", {"id": "twitch:alanzoka", "pausar": True})):
            self.assertEqual(self.json(metodo, caminho, corpo, cookie=False)[0], 403, caminho)
            if metodo == "POST":
                self.assertEqual(self.json(metodo, caminho, corpo, csrf=False)[0], 403, caminho)
        self.assertEqual(self.json("GET", "/api/lives", host="evil.com")[0], 403)


if __name__ == "__main__":
    unittest.main()
