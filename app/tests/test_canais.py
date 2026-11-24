"""Testes do registro de canais permitidos do monitor de lives (módulo canais + rotas /api/canais).

Rodar na pasta do projeto:  python -m unittest discover -s app/tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import canais  # noqa: E402
from test_servidor import Base  # noqa: E402

def pedido(**extra):
    base = {"plataforma": "twitch", "canal": "AlanZoka", "nome": "Alan", "pode_cortar": True, "pode_postar": True}
    base.update(extra)
    return base


class TestIdentificarCanal(unittest.TestCase):
    def test_links_da_twitch(self):
        for texto in ("https://www.twitch.tv/AlanZoka", "twitch.tv/alanzoka", "https://m.twitch.tv/alanzoka/videos",
                      "https://www.twitch.tv/alanzoka?sr=a", "https://player.twitch.tv/?channel=AlanZoka&parent=x"):
            self.assertEqual(canais.canal_de_url(texto), ("twitch", "alanzoka"), texto)

    def test_links_do_youtube(self):
        self.assertEqual(canais.canal_de_url("https://www.youtube.com/@Gaules"), ("youtube", "@gaules"))
        self.assertEqual(canais.canal_de_url("youtube.com/@gaules/live"), ("youtube", "@gaules"))
        uc = "UC" + "a" * 22
        self.assertEqual(canais.canal_de_url(f"https://www.youtube.com/channel/{uc}"), ("youtube", uc))

    def test_links_que_nao_identificam_o_canal(self):
        for texto in ("https://www.youtube.com/watch?v=abc123", "https://youtu.be/abc123", "https://www.youtube.com/live/abc",
                      "https://www.youtube.com/c/Nome", "https://www.twitch.tv/", "https://www.twitch.tv/directory",
                      "https://kick.com/alguem", "https://exemplo.com/@x", "", "   ", "https://www.twitch.tv/ab",
                      "https://www.twitch.tv/nome com espaco"):
            with self.assertRaises(canais.CanalInvalido, msg=texto):
                canais.canal_de_url(texto)

    def test_normalizar(self):
        self.assertEqual(canais.normalizar_canal("twitch", "@AlanZoka"), "alanzoka")
        self.assertEqual(canais.normalizar_canal("youtube", "Gaules"), "@gaules")
        self.assertEqual(canais.normalizar_canal("youtube", "@gaules"), "@gaules")
        for plat, ruim in (("twitch", "a"), ("twitch", "x" * 30), ("twitch", "a b"), ("twitch", "videos"),
                           ("youtube", "@a"), ("youtube", "@a/b"), ("kick", "alguem")):
            with self.assertRaises(canais.CanalInvalido, msg=ruim):
                canais.normalizar_canal(plat, ruim)

    def test_identificar_aceita_link_ou_nome_com_plataforma(self):
        self.assertEqual(canais.identificar("https://twitch.tv/alanzoka"), ("twitch", "alanzoka"))
        self.assertEqual(canais.identificar("AlanZoka", "twitch"), ("twitch", "alanzoka"))
        self.assertEqual(canais.identificar("@gaules", "youtube"), ("youtube", "@gaules"))
        with self.assertRaises(canais.CanalInvalido):
            canais.identificar("alanzoka")                      # sem plataforma e sem link


class TestRegistro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.arq = Path(self.tmp.name) / "canais_permitidos.json"
        antigo, canais.MAX_CANAIS = canais.MAX_CANAIS, 10              # o limite real (2) tem teste próprio
        self.addCleanup(setattr, canais, "MAX_CANAIS", antigo)
        self.reg = canais.RegistroCanais(self.arq)

    def test_cadastra_e_lista(self):
        c = self.reg.salvar(pedido())
        self.assertEqual((c["id"], c["canal"], c["situacao"]), ("twitch:alanzoka", "alanzoka", "ativo"))
        self.assertEqual(c["url_live"], "https://www.twitch.tv/alanzoka")
        dados = json.loads(self.arq.read_text(encoding="utf-8"))
        self.assertEqual(dados["versao"], 1)
        self.assertTrue(dados["canais"][0]["pode_cortar"] and dados["canais"][0]["pode_postar"])
        self.assertNotIn("origem", dados["canais"][0])                      # sem origem nem prazo: a permissão é vitalícia
        self.assertNotIn("revisar_ate", dados["canais"][0])
        self.assertEqual([x["id"] for x in self.reg.listar()], ["twitch:alanzoka"])

    def test_cadastra_so_com_o_link_do_canal(self):
        a = self.reg.salvar({"canal": "https://www.twitch.tv/AlanZoka", "pode_cortar": True, "pode_postar": True})
        self.assertEqual((a["plataforma"], a["canal"], a["nome"]), ("twitch", "alanzoka", "alanzoka"))
        b = self.reg.salvar({"canal": "https://www.youtube.com/@Gaules/live", "pode_cortar": True, "pode_postar": True})
        self.assertEqual((b["plataforma"], b["canal"], b["url_live"]), ("youtube", "@gaules", "https://www.youtube.com/@gaules/live"))
        uc = "UC" + "b" * 22
        c = self.reg.salvar({"canal": f"https://www.youtube.com/channel/{uc}", "pode_cortar": True, "pode_postar": True})
        self.assertEqual(c["url_live"], f"https://www.youtube.com/channel/{uc}/live")

    def test_nao_pede_confirmacao_de_permissao(self):
        c = self.reg.salvar({"canal": "https://www.twitch.tv/alanzoka"})              # sem campos de permissão
        self.assertTrue(c["pode_cortar"] and c["pode_postar"])                           # estar na lista É a permissão
        self.assertTrue(self.reg.verificar("twitch.tv/alanzoka")["permitido"])
        self.reg.salvar(pedido(pode_postar=False))                                       # o que a tela mandar a mais é ignorado
        self.assertTrue(self.reg.listar()[0]["pode_postar"])

    def test_validacoes(self):
        ruins = [pedido(plataforma="kick"), pedido(canal=""), pedido(canal="a b c"), pedido(canal=None),
                 pedido(ativo="sim"), pedido(layout="../x"),
                 pedido(canal="https://www.youtube.com/@gaules"),                  # link de outra plataforma
                 {"canal": "alanzoka", "pode_cortar": True, "pode_postar": True},  # nome sem plataforma
                 {"canal": "https://kick.com/x", "pode_cortar": True, "pode_postar": True},
                 {"canal": "https://www.youtube.com/watch?v=abc", "pode_cortar": True, "pode_postar": True}]
        for ruim in ruins:
            with self.assertRaises(ValueError, msg=repr(ruim)[:80]):
                self.reg.salvar(ruim)
        with self.assertRaises(ValueError):
            self.reg.salvar("texto")
        self.assertFalse(self.arq.exists())

    def test_atualizar_mantem_criado_em_e_nao_duplica(self):
        a = self.reg.salvar(pedido())
        b = self.reg.salvar(pedido(canal="alanzoka", nome="Alan Zoka"))
        self.assertEqual(len(self.reg.listar()), 1)
        self.assertEqual((b["nome"], b["criado_em"]), ("Alan Zoka", a["criado_em"]))

    def test_mesmo_nome_em_plataformas_diferentes_sao_canais_diferentes(self):
        self.reg.salvar(pedido())
        self.reg.salvar(pedido(plataforma="youtube", canal="alanzoka"))
        self.assertEqual({c["id"] for c in self.reg.listar()}, {"twitch:alanzoka", "youtube:@alanzoka"})

    def test_suspender_reativar_apagar(self):
        self.reg.salvar(pedido())
        self.assertEqual(self.reg.definir_ativo("twitch:alanzoka", False)["situacao"], "suspenso")
        self.assertFalse(self.reg.verificar("twitch.tv/alanzoka")["permitido"])
        self.assertEqual(self.reg.definir_ativo("twitch:alanzoka", True)["situacao"], "ativo")
        self.assertTrue(self.reg.verificar("twitch.tv/alanzoka")["permitido"])
        self.assertIsNone(self.reg.definir_ativo("twitch:nao_existe", True))
        with self.assertRaises(ValueError):
            self.reg.definir_ativo("twitch:alanzoka", "sim")
        self.assertTrue(self.reg.apagar("twitch:alanzoka"))
        self.assertFalse(self.reg.apagar("twitch:alanzoka"))
        self.assertEqual(self.reg.listar(), [])

    def test_verificar(self):
        self.reg.salvar(pedido())
        ok = self.reg.verificar("https://www.twitch.tv/AlanZoka")
        self.assertTrue(ok["permitido"])
        self.assertEqual((ok["plataforma"], ok["canal"]), ("twitch", "alanzoka"))
        fora = self.reg.verificar("https://www.twitch.tv/outro_canal")
        self.assertFalse(fora["permitido"])
        self.assertIn("não está na lista", fora["motivo"])
        self.assertFalse(self.reg.verificar("https://www.youtube.com/watch?v=x")["permitido"])    # canal desconhecido
        self.assertFalse(self.reg.verificar("lixo")["permitido"])
        self.assertFalse(self.reg.verificar(None)["permitido"])
        self.assertFalse(self.reg.verificar("https://www.youtube.com/@alanzoka")["permitido"])    # só a Twitch foi cadastrada

    def test_arquivo_ilegivel_nao_libera_ninguem(self):
        self.arq.write_text("{ isto não é json", encoding="utf-8")
        self.assertEqual(self.reg.listar(), [])
        self.assertFalse(self.reg.verificar("twitch.tv/alanzoka")["permitido"])
        self.reg.salvar(pedido())                                           # cadastrar de novo guarda o arquivo ruim
        self.assertTrue((Path(self.tmp.name) / "canais_permitidos.corrompido.json").is_file())
        self.assertTrue(self.reg.verificar("twitch.tv/alanzoka")["permitido"])

    def test_entrada_adulterada_sem_as_permissoes_nao_vale(self):
        self.reg.salvar(pedido())
        dados = json.loads(self.arq.read_text(encoding="utf-8"))
        dados["canais"][0]["pode_postar"] = False
        self.arq.write_text(json.dumps(dados), encoding="utf-8")
        self.assertFalse(self.reg.verificar("twitch.tv/alanzoka")["permitido"])

    def test_cadastro_antigo_com_origem_e_prazo_continua_valendo(self):
        self.reg.salvar(pedido())
        dados = json.loads(self.arq.read_text(encoding="utf-8"))
        dados["canais"][0].update({"origem": "antigo", "revisar_ate": "2020-01-01", "data_permissao": "2019-01-01"})
        self.arq.write_text(json.dumps(dados), encoding="utf-8")
        r = self.reg.verificar("twitch.tv/alanzoka")
        self.assertTrue(r["permitido"])                                      # o prazo não existe mais
        self.assertNotIn("revisar_ate", r["canal_info"])

    def test_limite_de_canais(self):
        canais.MAX_CANAIS = 2
        self.reg.salvar(pedido(canal="canal_um"))
        self.reg.salvar(pedido(canal="canal_dois"))
        with self.assertRaises(ValueError) as cx:
            self.reg.salvar(pedido(canal="canal_tres"))
        self.assertIn("no máximo 2 canais", str(cx.exception))
        self.reg.salvar(pedido(canal="canal_um", nome="editado"))           # atualizar um existente continua valendo

    def test_ordem_da_lista(self):
        self.reg.salvar(pedido(canal="zeta_canal"))
        self.reg.salvar(pedido(canal="alfa_canal"))
        self.reg.definir_ativo("twitch:alfa_canal", False)
        self.assertEqual([c["canal"] for c in self.reg.listar()], ["zeta_canal", "alfa_canal"])    # ativos primeiro


class TestLimiteDeDoisCanais(unittest.TestCase):
    def test_o_limite_padrao_e_2(self):
        self.assertEqual(canais.MAX_CANAIS, 2)
        with tempfile.TemporaryDirectory() as d:
            reg = canais.RegistroCanais(Path(d) / "c.json")
            reg.salvar({"canal": "https://www.twitch.tv/um_canal"})
            reg.salvar({"canal": "https://www.youtube.com/@outro"})
            with self.assertRaises(ValueError):
                reg.salvar({"canal": "https://www.twitch.tv/terceiro"})
            reg.apagar("twitch:um_canal")                                                  # apagando um, cabe de novo
            reg.salvar({"canal": "https://www.twitch.tv/terceiro"})
            reg.salvar({"canal": "https://www.twitch.tv/terceiro", "nome": "Editado"})     # editar não conta como novo
            self.assertEqual(len(reg.listar()), 2)


class TestRotasDeCanais(Base):
    def corpo(self, **extra):
        return pedido(**extra)

    def test_estado_informa_o_recurso(self):
        self.assertIn("canais", self.json("GET", "/api/estado")[1]["recursos"])

    def test_fluxo_completo(self):
        self.assertEqual(self.json("GET", "/api/canais")[1]["canais"], [])
        status, r = self.json("POST", "/api/canais", self.corpo())
        self.assertEqual((status, r["canal"]["id"]), (201, "twitch:alanzoka"))
        _, lista = self.json("GET", "/api/canais")
        self.assertEqual([c["situacao"] for c in lista["canais"]], ["ativo"])
        self.assertEqual(lista["canais"][0]["url_live"], "https://www.twitch.tv/alanzoka")
        self.assertEqual(lista["plataformas"], ["twitch", "youtube"])
        _, v = self.json("POST", "/api/canais/verificar", {"url": "https://www.twitch.tv/alanzoka"})
        self.assertTrue(v["permitido"])
        self.assertEqual(self.json("POST", "/api/canais/ativo", {"id": "twitch:alanzoka", "ativo": False})[1]["canal"]["situacao"], "suspenso")
        self.assertFalse(self.json("POST", "/api/canais/verificar", {"url": "twitch.tv/alanzoka"})[1]["permitido"])
        self.assertEqual(self.json("POST", "/api/canais/apagar", {"id": "twitch:alanzoka"})[0], 200)
        self.assertEqual(self.json("POST", "/api/canais/apagar", {"id": "twitch:alanzoka"})[0], 404)
        self.assertEqual(self.json("POST", "/api/canais/ativo", {"id": "twitch:nao", "ativo": True})[0], 404)

    def test_pedidos_invalidos(self):
        for ruim in (self.corpo(plataforma="kick"), self.corpo(canal=""),
                     self.corpo(layout="nao_existe")):
            self.assertEqual(self.json("POST", "/api/canais", ruim)[0], 400, repr(ruim)[:70])
        self.assertEqual(self.json("POST", "/api/canais/ativo", {"id": "twitch:x", "ativo": "sim"})[0], 400)
        self.assertEqual(self.json("POST", "/api/canais/verificar", {})[1]["permitido"], False)
        self.assertFalse((self.raiz / "canais_permitidos.json").exists())

    def test_terceiro_canal_e_recusado(self):
        self.assertEqual(self.json("GET", "/api/canais")[1]["maximo"], 2)
        self.assertEqual(self.json("POST", "/api/canais", {"canal": "https://www.twitch.tv/um_canal"})[0], 201)
        self.assertEqual(self.json("POST", "/api/canais", {"canal": "https://www.youtube.com/@outro"})[0], 201)
        status, r = self.json("POST", "/api/canais", {"canal": "https://www.twitch.tv/terceiro"})
        self.assertEqual(status, 400)
        self.assertIn("no máximo 2 canais", r["erro"])
        self.assertEqual(self.json("POST", "/api/canais", {"canal": "https://www.twitch.tv/um_canal", "nome": "Novo nome"})[0], 201)

    def test_layout_existente_e_aceito(self):
        layout = {"nome": "Alan Zoka", "cam": {"x": 0.76, "y": 0.35, "w": 0.22, "h": 0.2},
                  "game": {"x": 0.2, "y": 0.0, "w": 0.55, "h": 1.0}, "cam_altura": 640, "ordem": "cam_primeiro"}
        slug = self.json("POST", "/api/layouts", layout)[1]["layout"]["slug"]
        status, r = self.json("POST", "/api/canais", self.corpo(layout=slug))
        self.assertEqual((status, r["canal"]["layout"]), (201, slug))
        self.assertEqual(self.json("GET", "/api/canais")[1]["layouts"][0]["slug"], slug)

    def test_seguranca(self):
        for metodo, caminho, corpo in (("GET", "/api/canais", None), ("POST", "/api/canais", self.corpo()),
                                       ("POST", "/api/canais/verificar", {"url": "x"}),
                                       ("POST", "/api/canais/ativo", {"id": "a", "ativo": True}),
                                       ("POST", "/api/canais/apagar", {"id": "a"})):
            self.assertEqual(self.json(metodo, caminho, corpo, cookie=False)[0], 403, caminho)
            if metodo == "POST":
                self.assertEqual(self.json(metodo, caminho, corpo, csrf=False)[0], 403, caminho)
        self.assertEqual(self.json("GET", "/api/canais", host="evil.com")[0], 403)


if __name__ == "__main__":
    unittest.main()
