"""Testes do editor de layout: layouts salvos, quadros (imagem do vídeo) e o layout personalizado do gerar_cortes.

Tudo com mocks: nenhum ffmpeg, yt-dlp ou rede. Rodar na pasta do projeto:
    python -m unittest discover -s app/tests -v
"""
import http.client
import json
import math
import os
import subprocess
import sys
import tempfile
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
from test_servidor import Base, URL_OK  # noqa: E402

JPG = b"\xff\xd8\xff\xe0" + b"0" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 200
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"0" * 100
LAYOUT = {"nome": "Alan Zoka", "cam": {"x": 0.76, "y": 0.35, "w": 0.22, "h": 0.2},
          "game": {"x": 0.2, "y": 0.0, "w": 0.55, "h": 1.0}, "cam_altura": 640, "ordem": "cam_primeiro"}


class BaseEditor(Base):
    def setUp(self):
        super().setUp()
        self.srv.estado.quadros = self.raiz / "quadros"          # não suja app/quadros

    def enviar_bytes(self, caminho, dados, tipo="image/jpeg", cookie=True, csrf=True):
        con = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=15)
        cab = {"Host": f"127.0.0.1:{self.porta}", "Content-Type": tipo}
        if cookie:
            cab["Cookie"] = f"{servidor.COOKIE}={self.token}"
        if csrf:
            cab["X-Requested-With"] = "fabrica"
        con.request("POST", caminho, body=dados, headers=cab)
        resp = con.getresponse()
        corpo = resp.read()
        con.close()
        return resp.status, json.loads(corpo.decode("utf-8") or "{}")


class TestLayoutsSalvos(BaseEditor):
    def test_lista_vazia(self):
        self.assertEqual(self.json("GET", "/api/layouts"), (200, {"layouts": []}))

    def test_salvar_listar_e_apagar(self):
        status, dados = self.json("POST", "/api/layouts", LAYOUT)
        self.assertEqual(status, 201)
        self.assertEqual(dados["layout"]["slug"], "alan-zoka")
        arquivo = self.raiz / "layouts_salvos" / "alan-zoka.json"
        self.assertTrue(arquivo.is_file())
        _, lista = self.json("GET", "/api/layouts")
        self.assertEqual([l["slug"] for l in lista["layouts"]], ["alan-zoka"])
        self.assertEqual(lista["layouts"][0]["cam_altura"], 640)
        self.assertEqual(self.json("POST", "/api/layouts/apagar", {"slug": "alan-zoka"})[0], 200)
        self.assertFalse(arquivo.exists())
        self.assertEqual(self.json("POST", "/api/layouts/apagar", {"slug": "alan-zoka"})[0], 404)

    def test_nome_com_acento_e_simbolos_vira_slug_seguro(self):
        _, dados = self.json("POST", "/api/layouts", {**LAYOUT, "nome": "  Gabriel Çustódio!! #1  "})
        self.assertEqual(dados["layout"]["slug"], "gabriel-custodio-1")

    def test_salvar_com_o_mesmo_nome_substitui(self):
        self.json("POST", "/api/layouts", LAYOUT)
        self.json("POST", "/api/layouts", {**LAYOUT, "cam_altura": 500})
        _, lista = self.json("GET", "/api/layouts")
        self.assertEqual(len(lista["layouts"]), 1)
        self.assertEqual(lista["layouts"][0]["cam_altura"], 500)

    def test_invalidos_dao_400(self):
        ruins = [
            {**LAYOUT, "nome": ""}, {**LAYOUT, "nome": "!!!"}, {**LAYOUT, "nome": "a" * 61}, {**LAYOUT, "nome": "ab\x00c"},
            {**LAYOUT, "cam_altura": 100}, {**LAYOUT, "cam_altura": 5000}, {**LAYOUT, "cam_altura": "x"},
            {**LAYOUT, "ordem": "meio"},
            {**LAYOUT, "cam": {"x": 0.9, "y": 0, "w": 0.3, "h": 0.2}},          # sai do quadro
            {**LAYOUT, "cam": {"x": 0, "y": 0, "w": 0, "h": 0.2}},
            {**LAYOUT, "game": {"x": -0.1, "y": 0, "w": 0.5, "h": 0.5}},
            {**LAYOUT, "game": {"x": 0, "y": 0, "w": float("nan"), "h": 0.5}},
            {**LAYOUT, "cam": "texto"}, {**LAYOUT, "game": None},
            {k: v for k, v in LAYOUT.items() if k != "cam"},
        ]
        for ruim in ruins:
            self.assertEqual(self.json("POST", "/api/layouts", ruim)[0], 400, repr(ruim)[:90])
        self.assertEqual(self.json("GET", "/api/layouts")[1]["layouts"], [])

    def test_o_arquivo_salvo_serve_para_o_gerar_cortes(self):
        self.json("POST", "/api/layouts", LAYOUT)
        dados = json.loads((self.raiz / "layouts_salvos" / "alan-zoka.json").read_text(encoding="utf-8"))
        limpo = g.normalize_saved_layout(dados)
        self.assertIsNotNone(limpo)
        self.assertEqual((limpo["cam_altura"], limpo["ordem"]), (640, "cam_primeiro"))

    def test_slug_igual_nos_dois_lados(self):
        for nome in ("Alan Zoka", "Gabriel Çustódio!! #1", "  a  b  ", "ÁÉÍ óú", "x" * 80):
            self.assertEqual(servidor.slug_do_nome(nome), g.layout_slug(nome), nome)

    def test_apagar_com_slug_perigoso(self):
        for ruim in ("../x", "a/b", "", None, 5, "A" * 3):
            self.assertEqual(self.json("POST", "/api/layouts/apagar", {"slug": ruim})[0], 404, repr(ruim))

    def test_seguranca_das_rotas(self):
        self.assertEqual(self.json("GET", "/api/layouts", cookie=False)[0], 403)
        self.assertEqual(self.json("POST", "/api/layouts", LAYOUT, csrf=False)[0], 403)
        self.assertEqual(self.json("POST", "/api/layouts", LAYOUT, cookie=False)[0], 403)

    def test_layout_salvo_vira_flag_com_igual(self):
        self.json("POST", "/api/layouts", LAYOUT)
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 1, "layout_salvo": "alan-zoka"})
        self.assertIn("'--layout-salvo=alan-zoka'", "\n".join(self.esperar_fim()["linhas"]))

    def test_layout_salvo_inexistente_ou_perigoso_da_400(self):
        for ruim in ("nao-existe", "../x", "A B", 5, ["x"], "a" * 50):
            status, _ = self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "layout_salvo": ruim})
            self.assertEqual(status, 400, repr(ruim))

    def test_layout_salvo_ignorado_fora_do_gameplay(self):
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "ia", "layout_salvo": "qualquer"})
        self.assertNotIn("--layout-salvo", "\n".join(self.esperar_fim()["linhas"]).split("ARGS:")[-1])


class TestQuadros(BaseEditor):
    def test_enviar_imagem_e_baixar(self):
        for dados, tipo in ((JPG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")):
            status, r = self.enviar_bytes("/api/quadro/enviar", dados, tipo)
            self.assertEqual(status, 201, tipo)
            resp = self.req("GET", f"/api/quadro?id={r['quadro']}")
            self.assertEqual((resp.status, resp.dados, resp.getheader("Content-Type")), (200, dados, tipo))

    def test_o_tipo_vem_dos_bytes_e_nao_do_cabecalho(self):
        status, r = self.enviar_bytes("/api/quadro/enviar", PNG, "image/jpeg")
        self.assertTrue(r["quadro"].endswith(".png"))

    def test_nao_imagem_vazio_e_grande_demais(self):
        self.assertEqual(self.enviar_bytes("/api/quadro/enviar", b"isto nao e imagem")[0], 400)
        self.assertEqual(self.enviar_bytes("/api/quadro/enviar", b"<svg onload=alert(1)>", "image/svg+xml")[0], 400)
        self.assertIn(self.enviar_bytes("/api/quadro/enviar", b"")[0], (400, 413))
        try:                                  # o servidor recusa sem ler o corpo: o cliente pode ver 413 ou a conexão fechada
            self.assertEqual(self.enviar_bytes("/api/quadro/enviar", JPG + b"0" * (servidor.MAX_IMAGEM + 1))[0], 413)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.assertFalse((self.raiz / "quadros").exists() and any((self.raiz / "quadros").iterdir()))

    def test_seguranca_do_envio(self):
        self.assertEqual(self.enviar_bytes("/api/quadro/enviar", JPG, cookie=False)[0], 403)
        self.assertEqual(self.enviar_bytes("/api/quadro/enviar", JPG, csrf=False)[0], 403)

    def test_id_de_quadro_invalido(self):
        for ruim in ("../x.jpg", "abc", "0123456789abcdef.exe", "0123456789abcdef.jpg", "", "0123456789abcdef.jpg/../a"):
            self.assertEqual(self.req("GET", f"/api/quadro?id={ruim}").status, 404, ruim)
        self.assertEqual(self.req("GET", "/api/quadro?id=0123456789abcdef.jpg", cookie=False).status, 403)

    def test_guarda_so_os_ultimos(self):
        for _ in range(servidor.MAX_QUADROS + 4):
            self.enviar_bytes("/api/quadro/enviar", JPG)
        self.assertEqual(len(list((self.raiz / "quadros").iterdir())), servidor.MAX_QUADROS)


class TestCapturaDeQuadro(BaseEditor):
    def setUp(self):
        super().setUp()
        self.srv.estado._ffmpeg = lambda: "/opt/ffmpeg/ffmpeg"
        self.chamadas = []

    def falso(self, falha_ytdlp=False, falha_ffmpeg=False):
        def run(cmd, **kw):
            self.chamadas.append((cmd, kw))
            if "yt_dlp" in cmd:
                if not falha_ytdlp:
                    destino = Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp4"))
                    destino.write_bytes(b"video")
                return subprocess.CompletedProcess(cmd, 1 if falha_ytdlp else 0, "", "erro")
            Path(cmd[-1]).write_bytes(JPG)
            return subprocess.CompletedProcess(cmd, 1 if falha_ffmpeg else 0, "", "")
        return run

    def test_baixa_trecho_curto_e_tira_o_quadro(self):
        with mock.patch.object(servidor.subprocess, "run", self.falso()):
            status, r = self.json("POST", "/api/quadro", {"url": URL_OK, "segundo": 90})
        self.assertEqual(status, 201)
        self.assertTrue(r["quadro"].endswith(".jpg"))
        ytdlp = self.chamadas[0][0]
        self.assertEqual(ytdlp[ytdlp.index("--download-sections") + 1], "*90-96")
        self.assertEqual(ytdlp[-2:], ["--", URL_OK])                    # a URL nunca vira opção
        self.assertNotIn("shell", self.chamadas[0][1])
        self.assertEqual(self.req("GET", f"/api/quadro?id={r['quadro']}").dados, JPG)

    def test_url_ou_segundo_invalidos(self):
        with mock.patch.object(servidor.subprocess, "run", self.falso()):
            for corpo in ({"url": "ftp://x"}, {"url": "-o x"}, {"url": ""}, {"url": URL_OK, "segundo": "a"},
                          {"url": URL_OK, "segundo": -1}, {"url": URL_OK, "segundo": 99999}, {}):
                self.assertEqual(self.json("POST", "/api/quadro", corpo)[0], 400, repr(corpo))
        self.assertEqual(self.chamadas, [])

    def test_falhas_viram_mensagem(self):
        with mock.patch.object(servidor.subprocess, "run", self.falso(falha_ytdlp=True)):
            status, r = self.json("POST", "/api/quadro", {"url": URL_OK})
        self.assertEqual(status, 400)
        self.assertIn("baixar", r["erro"])
        with mock.patch.object(servidor.subprocess, "run", self.falso(falha_ffmpeg=True)):
            self.assertEqual(self.json("POST", "/api/quadro", {"url": URL_OK})[0], 400)

    def test_timeout_vira_mensagem(self):
        def run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 1)
        with mock.patch.object(servidor.subprocess, "run", run):
            status, r = self.json("POST", "/api/quadro", {"url": URL_OK})
        self.assertEqual(status, 400)
        self.assertIn("demorou", r["erro"])

    def test_sem_ffmpeg(self):
        self.srv.estado._ffmpeg = lambda: None
        self.assertEqual(self.json("POST", "/api/quadro", {"url": URL_OK})[0], 400)


# ------------------------------------------------------------------ gerar_cortes: layout personalizado
SALVO = {"nome": "Alan Zoka", "cam": {"x": 0.76, "y": 0.35, "w": 0.22, "h": 0.2},
         "game": {"x": 0.2, "y": 0.0, "w": 0.55, "h": 1.0}, "cam_altura": 640, "ordem": "cam_primeiro"}


class TestLayoutPersonalizadoNoGerarCortes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "layouts").mkdir()
        for alvo, valor in (("LAYOUTS_DIR", self.dir / "layouts"), ("TEMP_DIR", self.dir),
                            ("GAMEPLAY_WATERMARK_PATH", str(self.dir / "nao_existe.png"))):
            p = mock.patch.object(g, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        self.antigo = os.environ.pop("CORTES_LAYOUT", None)
        self.addCleanup(self._restaurar)
        g.CUSTOM_LAYOUT = None

    def _restaurar(self):
        g.CUSTOM_LAYOUT = None
        os.environ.pop("CORTES_LAYOUT", None)
        if self.antigo is not None:
            os.environ["CORTES_LAYOUT"] = self.antigo

    def salvar(self, dados=SALVO, slug="alan-zoka"):
        (self.dir / "layouts" / f"{slug}.json").write_text(json.dumps(dados), encoding="utf-8")

    def test_validacao(self):
        self.assertIsNotNone(g.normalize_saved_layout(SALVO))
        for ruim in (None, [], {}, {**SALVO, "cam_altura": 10}, {**SALVO, "ordem": "x"}, {**SALVO, "cam": {"x": 2}},
                     {**SALVO, "game": {"x": 0.5, "y": 0.5, "w": 0.9, "h": 0.9}}, {**SALVO, "cam_altura": "a"}):
            self.assertIsNone(g.normalize_saved_layout(ruim), repr(ruim)[:80])

    def test_carregar_e_ativar(self):
        self.assertFalse(g.activate_saved_layout("nao-existe"))
        self.assertIsNone(g.CUSTOM_LAYOUT)
        self.salvar()
        self.assertFalse(g.activate_saved_layout("../alan-zoka-x"))
        self.assertTrue(g.activate_saved_layout("Alan Zoka"))                 # aceita o nome, vira o slug
        self.assertEqual(g.get_layout(), "custom")
        self.assertTrue(g.uses_fit_family())

    def test_arquivo_estragado_nao_ativa(self):
        (self.dir / "layouts" / "ruim.json").write_text("{nao e json", encoding="utf-8")
        self.assertFalse(g.activate_saved_layout("ruim"))
        self.salvar({**SALVO, "cam_altura": 5}, "ruim2")
        self.assertFalse(g.activate_saved_layout("ruim2"))

    def test_custom_sem_layout_carregado_volta_ao_padrao(self):
        os.environ["CORTES_LAYOUT"] = "custom"
        self.assertEqual(g.get_layout(), g.DEFAULT_LAYOUT)

    def test_geometria_cam_em_cima(self):
        geo = g.custom_geometry(1920, 1080, g.normalize_saved_layout(SALVO))
        self.assertEqual((geo["cam_h"], geo["game_h"]), (640, 1920 - 640 - g.CAM_BAND_HEIGHT))
        self.assertEqual((geo["y_cam"], geo["y_band"], geo["y_game"]), (0, 640, 640 + g.CAM_BAND_HEIGHT))
        self.assertEqual(geo["crop_x"], 1459 // 2 * 2)
        gx, gy, gw, gh = geo["game_crop"]
        self.assertEqual((gx, gy, gw, gh), (384, 0, 1056, 1080))
        for v in (geo["crop_x"], geo["crop_y"], geo["crop_w"], geo["crop_h"], gx, gy, gw, gh):
            self.assertEqual(v % 2, 0)

    def test_geometria_gameplay_em_cima(self):
        geo = g.custom_geometry(1920, 1080, g.normalize_saved_layout({**SALVO, "ordem": "game_primeiro"}))
        game_h = 1920 - 640 - g.CAM_BAND_HEIGHT
        self.assertEqual((geo["y_game"], geo["y_band"], geo["y_cam"]), (0, game_h, game_h + g.CAM_BAND_HEIGHT))

    def test_geometria_invalida(self):
        self.assertIsNone(g.custom_geometry(0, 0, g.normalize_saved_layout(SALVO)))
        self.assertIsNone(g.custom_geometry(1920, 1080, None))
        minusculo = {**SALVO, "cam": {"x": 0.1, "y": 0.1, "w": 0.001, "h": 0.001}}
        self.assertIsNone(g.custom_geometry(1920, 1080, g.normalize_saved_layout(minusculo)))

    def grafo(self, ordem):
        layout = g.normalize_saved_layout({**SALVO, "ordem": ordem})
        geo = g.custom_geometry(1920, 1080, layout)
        ass = self.dir / "s.ass"
        ass.write_text("Style: Default,Arial,62,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,320,1\n"
                       "Dialogue: 0,0:00:01.00,0:00:02.00,Karaoke,,0,0,0,karaoke,{\\pos(540,835)}OI\n", encoding="utf-8")
        cmd = g.build_cam_cmd(self.dir / "in.mp4", self.dir / "out.mp4", ass, self.dir / "faixa.png", geo, 30.0)
        return cmd[cmd.index("-filter_complex") + 1], geo

    def test_grafo_usa_a_caixa_do_gameplay_escolhida(self):
        grafo, geo = self.grafo("cam_primeiro")
        self.assertIn("[b]crop=1056:1080:384:0,scale=1080:", grafo)
        self.assertIn(f"[a]crop={geo['crop_w']}:{geo['crop_h']}:{geo['crop_x']}:{geo['crop_y']},scale=1080:640", grafo)
        self.assertIn("[cam][1:v][game]vstack=inputs=3", grafo)

    def test_grafo_ordem_invertida_e_legenda_na_faixa(self):
        grafo, geo = self.grafo("game_primeiro")
        self.assertIn("[game][1:v][cam]vstack=inputs=3", grafo)
        texto = (self.dir / "s_cam.ass").read_text(encoding="utf-8")
        self.assertIn(f"\\pos(540,{geo['y_band'] + g.CAM_SUBTITLE_CENTER_Y})", texto)

    def test_fluxo_completo_com_layout_salvo(self):
        self.salvar()
        self.assertTrue(g.activate_saved_layout("alan-zoka"))
        chamadas = []
        with mock.patch.object(g, "run_cmd", lambda cmd, *a, **k: (chamadas.append(cmd) or (True, "", ""))), \
                mock.patch.object(g, "get_video_dimensions", lambda *_: (1920, 1080)):
            ok = g.process_clipflow_layout(self.dir / "in.mp4", self.dir / "out.mp4", None, None, {"titulo_gancho": "X"},
                                           30.0, None, "texto", audio_mode="original")
        self.assertTrue(ok)
        self.assertEqual(g.LAYOUT_APLICADO, "custom")                     # sem webcam_pos: o layout salvo basta
        self.assertEqual(len(chamadas), 1)
        self.assertTrue((self.dir / "faixa_cam.png").exists())

    def test_modo_ia_com_layout_salvo_cai_no_fit(self):
        self.salvar()
        g.activate_saved_layout("alan-zoka")
        with mock.patch.object(g, "run_cmd", lambda *a, **k: (True, "", "")), \
                mock.patch.object(g, "get_video_dimensions", lambda *_: (1920, 1080)):
            self.assertTrue(g.process_clipflow_layout(self.dir / "in.mp4", self.dir / "out.mp4", None, None, {}, 30.0,
                                                      None, "t", audio_mode="ia"))
        self.assertEqual(g.LAYOUT_APLICADO, "fit")

    def test_json_e_cli_conhecem_o_layout_salvo(self):
        fonte = (RAIZ / "gerar_cortes.py").read_text(encoding="utf-8")
        self.assertIn('"--layout-salvo"', fonte)
        self.assertIn('"layout_salvo":', fonte)



# ------------------------------------------------------------------ pausa para escolher o layout durante a geração
ESPERA_FALSO = r'''
import json, pathlib, sys, time
d = pathlib.Path("temp_layout"); d.mkdir(exist_ok=True)
(d / "espera-1.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"1" * 50)
(d / "espera-2.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"2" * 50)
print("ARGS:", sys.argv[1:], flush=True)
print("@@LAYOUT_AGUARDANDO@@ espera-1.jpg espera-2.jpg ../x.jpg espera-99.jpg", flush=True)
for _ in range(300):
    if (d / "decisao.json").exists():
        break
    time.sleep(0.1)
print("DECISAO:", (d / "decisao.json").read_text(), flush=True)
print("@@LAYOUT_DECIDIDO@@", flush=True)
'''


class TestPausaParaEscolherLayout(BaseEditor):
    def setUp(self):
        super().setUp()
        (self.raiz / "gerar_cortes.py").write_text(ESPERA_FALSO, encoding="utf-8")

    def esperar_pausa(self, limite=15):
        import time
        fim = time.time() + limite
        while time.time() < fim:
            _, dados = self.json("GET", "/api/log?desde=0")
            if dados["job"] and dados["job"]["aguardando_layout"]:
                return dados
            time.sleep(0.1)
        self.fail("o job não ficou aguardando o layout")

    def iniciar(self, **extra):
        status, _ = self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 1, **extra})
        self.assertEqual(status, 201)

    def test_marca_vira_estado_e_texto_amigavel(self):
        self.iniciar(esperar_layout=True)
        dados = self.esperar_pausa()
        # "../x.jpg" é descartado; "espera-99.jpg" também (só espera-1 a espera-9 são nomes válidos)
        self.assertEqual(dados["job"]["aguardando_layout"], ["espera-1.jpg", "espera-2.jpg"])
        texto = "\n".join(dados["linhas"])
        self.assertNotIn("@@LAYOUT", texto)
        self.assertIn("Aguardando você escolher o layout", texto)
        self.assertIn("'--esperar-layout'", texto)
        self.srv.estado.parar()

    def test_quadros_da_espera_sao_servidos(self):
        self.iniciar(esperar_layout=True)
        self.esperar_pausa()
        resp = self.req("GET", "/api/quadro?id=espera-1.jpg")
        self.assertEqual((resp.status, resp.getheader("Content-Type")), (200, "image/jpeg"))
        self.assertTrue(resp.dados.startswith(b"\xff\xd8\xff"))
        for ruim in ("espera-0.jpg", "espera-10.jpg", "espera-1.png", "../espera-1.jpg", "espera-9.jpg"):
            self.assertEqual(self.req("GET", f"/api/quadro?id={ruim}").status, 404, ruim)
        self.assertEqual(self.req("GET", "/api/quadro?id=espera-1.jpg", cookie=False).status, 403)
        self.srv.estado.parar()

    def test_decidir_padrao_libera_o_script(self):
        self.iniciar(esperar_layout=True)
        self.esperar_pausa()
        self.assertEqual(self.json("POST", "/api/layout/decidir", {"acao": "padrao"})[0], 200)
        fim = self.esperar_fim()
        texto = "\n".join(fim["linhas"])
        self.assertIn('DECISAO: {"acao": "padrao"}', texto)
        self.assertIn("Layout decidido", texto)
        self.assertIsNone(fim["job"]["aguardando_layout"])
        self.assertFalse((self.raiz / "temp_layout" / "decisao.tmp").exists())

    def test_decidir_com_layout_salvo(self):
        self.json("POST", "/api/layouts", LAYOUT)
        self.iniciar(esperar_layout=True)
        self.esperar_pausa()
        self.assertEqual(self.json("POST", "/api/layout/decidir", {"acao": "salvo", "slug": "alan-zoka"})[0], 200)
        self.assertIn('"slug": "alan-zoka"', "\n".join(self.esperar_fim()["linhas"]))

    def test_decisoes_invalidas(self):
        self.iniciar(esperar_layout=True)
        self.esperar_pausa()
        for ruim in ({"acao": "salvo", "slug": "nao-existe"}, {"acao": "salvo", "slug": "../x"}, {"acao": "salvo"},
                     {"acao": "outra"}, {}, {"acao": "salvo", "slug": 5}):
            self.assertEqual(self.json("POST", "/api/layout/decidir", ruim)[0], 409, repr(ruim))
        self.assertFalse((self.raiz / "temp_layout" / "decisao.json").exists())      # nada foi entregue ao script
        self.assertEqual(self.json("POST", "/api/layout/decidir", {"acao": "padrao"})[0], 200)
        self.assertEqual(self.json("POST", "/api/layout/decidir", {"acao": "padrao"})[0], 409)   # já decidido
        self.esperar_fim()

    def test_sem_geracao_esperando(self):
        self.assertEqual(self.json("POST", "/api/layout/decidir", {"acao": "padrao"})[0], 409)

    def test_seguranca_da_decisao(self):
        self.assertEqual(self.json("POST", "/api/layout/decidir", {"acao": "padrao"}, cookie=False)[0], 403)
        self.assertEqual(self.json("POST", "/api/layout/decidir", {"acao": "padrao"}, csrf=False)[0], 403)

    def test_parar_enquanto_espera(self):
        self.iniciar(esperar_layout=True)
        self.esperar_pausa()
        self.assertTrue(self.srv.estado.parar())
        fim = self.esperar_fim()
        self.assertEqual(fim["job"]["estado"], "cancelado")
        self.assertIsNone(fim["job"]["aguardando_layout"])

    def test_parametro_esperar_layout(self):
        self.iniciar(esperar_layout=False)
        self.assertNotIn("--esperar-layout", "\n".join(self.esperar_pausa()["linhas"]).split("ARGS:")[-1].split("\n")[0])
        self.srv.estado.parar()
        self.esperar_fim()
        for ruim in (1, "sim", [True]):
            self.assertEqual(self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "esperar_layout": ruim})[0], 400)

    def test_layout_salvo_tem_prioridade_e_modo_ia_ignora(self):
        self.json("POST", "/api/layouts", LAYOUT)
        self.iniciar(esperar_layout=True, layout_salvo="alan-zoka")
        linha = "\n".join(self.esperar_pausa()["linhas"]).split("ARGS:")[-1].split("\n")[0]
        self.assertIn("--layout-salvo=alan-zoka", linha)
        self.assertNotIn("--esperar-layout", linha)
        self.srv.estado.parar()
        self.esperar_fim()
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "ia", "esperar_layout": True})
        self.assertNotIn("--esperar-layout", "\n".join(self.esperar_pausa()["linhas"]).split("ARGS:")[-1].split("\n")[0])
        self.srv.estado.parar()


class TestUrlDuplicada(BaseEditor):
    def test_limpar_url(self):
        casos = {
            "https://https://youtu.be/sCRi_1a6cOk": "https://www.youtube.com/watch?v=sCRi_1a6cOk",
            "https://www.youtube.com/watch?v=sCRi_1a6cOk&t=30s&list=abc": "https://www.youtube.com/watch?v=sCRi_1a6cOk",
            "https://www.youtube.com/shorts/sCRi_1a6cOk?feature=share": "https://www.youtube.com/watch?v=sCRi_1a6cOk",
            "https://twitch.tv/videos/123": "https://twitch.tv/videos/123",
            "http://https://twitch.tv/videos/123": "https://twitch.tv/videos/123",
            "  https://x.com/a  ": "https://x.com/a",
        }
        for entrada, esperado in casos.items():
            self.assertEqual(servidor.limpar_url(entrada), esperado, entrada)

    def test_executar_aceita_esquema_duplicado(self):
        self.json("POST", "/api/executar", {"url": "https://https://twitch.tv/videos/123", "modo": "ia"})
        texto = "\n".join(self.esperar_fim()["linhas"])
        self.assertIn("'https://twitch.tv/videos/123'", texto)
        self.assertNotIn("https://https://", texto)


class TestCapturaComRepeticao(BaseEditor):
    def setUp(self):
        super().setUp()
        self.srv.estado._ffmpeg = lambda: "/opt/ffmpeg/ffmpeg"

    def falso(self, falha_se=lambda cmd: False, mensagem="ERROR: Requested section is out of range"):
        self.chamadas = []

        def run(cmd, **kw):
            self.chamadas.append(cmd)
            if "yt_dlp" in cmd:
                if falha_se(cmd):
                    return subprocess.CompletedProcess(cmd, 1, "", "linha qualquer\n" + mensagem + "\n")
                Path(cmd[cmd.index("-o") + 1].replace("%(ext)s", "mp4")).write_bytes(b"video")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            Path(cmd[-1]).write_bytes(JPG)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return run

    def test_segundo_alem_do_fim_tenta_o_segundo_10_e_avisa(self):
        alem = lambda cmd: "*44500-44506" in cmd
        with mock.patch.object(servidor.subprocess, "run", self.falso(alem)):
            status, r = self.json("POST", "/api/quadro", {"url": "https://https://youtu.be/sCRi_1a6cOk", "segundo": 44500})
        self.assertEqual(status, 201)
        self.assertIn("44500", r["aviso"])
        self.assertIn("segundo 10", r["aviso"])
        usadas = [c[c.index("--download-sections") + 1] for c in self.chamadas if "yt_dlp" in c]
        self.assertIn("*10-16", usadas)
        self.assertEqual(self.chamadas[0][-1], "https://www.youtube.com/watch?v=sCRi_1a6cOk")     # URL limpa

    def test_tenta_os_dois_clientes_do_youtube(self):
        so_web = lambda cmd: "youtube:player_client=android,web" in cmd
        with mock.patch.object(servidor.subprocess, "run", self.falso(so_web)):
            status, r = self.json("POST", "/api/quadro", {"url": "https://youtu.be/sCRi_1a6cOk", "segundo": 5})
        self.assertEqual(status, 201)
        self.assertIsNone(r["aviso"])
        clientes = [c[c.index("--extractor-args") + 1] for c in self.chamadas if "yt_dlp" in c]
        self.assertEqual(clientes, ["youtube:player_client=android,web", "youtube:player_client=web"])

    def test_erro_mostra_o_motivo_do_ytdlp(self):
        with mock.patch.object(servidor.subprocess, "run", self.falso(lambda cmd: True, "ERROR: Sign in to confirm you are not a bot")):
            status, r = self.json("POST", "/api/quadro", {"url": URL_OK, "segundo": 5})
        self.assertEqual(status, 400)
        self.assertIn("Sign in to confirm", r["erro"])

    def test_fora_do_youtube_nao_leva_opcoes_do_youtube(self):
        with mock.patch.object(servidor.subprocess, "run", self.falso()):
            self.json("POST", "/api/quadro", {"url": "https://www.twitch.tv/videos/123", "segundo": 5})
        self.assertNotIn("--extractor-args", self.chamadas[0])


# ------------------------------------------------------------------ gerar_cortes.wait_for_layout_choice
class TestEsperaNoScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "layouts").mkdir()
        self.espera = self.dir / "espera"
        for alvo, valor in (("LAYOUTS_DIR", self.dir / "layouts"), ("LAYOUT_WAIT_DIR", self.espera)):
            p = mock.patch.object(g, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        self.antigo = os.environ.pop("CORTES_LAYOUT", None)
        self.addCleanup(self._restaurar)
        g.CUSTOM_LAYOUT = None
        self.chamadas = []

        def run(cmd, *a, **k):
            self.chamadas.append(cmd)
            Path(cmd[-1]).write_bytes(JPG)
            return True, "", ""
        p = mock.patch.object(g, "run_cmd", run)
        p.start()
        self.addCleanup(p.stop)
        self.clips = [{"start": 100.0, "duration": 30.0}, {"start": 500.0, "duration": 40.0}]

    def _restaurar(self):
        g.CUSTOM_LAYOUT = None
        os.environ.pop("CORTES_LAYOUT", None)
        if self.antigo is not None:
            os.environ["CORTES_LAYOUT"] = self.antigo

    def decidir_depois(self, conteudo, atraso=0.3):
        def escrever():
            import time
            time.sleep(atraso)
            self.espera.mkdir(parents=True, exist_ok=True)
            (self.espera / "decisao.json").write_text(json.dumps(conteudo), encoding="utf-8")
        import threading
        t = threading.Thread(target=escrever, daemon=True)
        t.start()
        return t

    def rodar(self, **k):
        import contextlib
        import io
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            resultado = g.wait_for_layout_choice(self.dir / "video.mp4", self.clips, poll=0.05, **k)
        return resultado, saida.getvalue()

    def test_escolha_de_layout_salvo(self):
        (self.dir / "layouts" / "alan-zoka.json").write_text(json.dumps(SALVO), encoding="utf-8")
        t = self.decidir_depois({"acao": "salvo", "slug": "alan-zoka"})
        resultado, texto = self.rodar(timeout=10)
        t.join()
        self.assertEqual(resultado, "salvo")
        self.assertEqual(g.get_layout(), "custom")
        self.assertIn(f"{g.LAYOUT_MARK_WAITING} espera-1.jpg espera-2.jpg", texto)
        self.assertIn(g.LAYOUT_MARK_DONE, texto)
        self.assertFalse(self.espera.exists())                         # limpou a pasta

    def test_escolha_do_padrao(self):
        t = self.decidir_depois({"acao": "padrao"})
        resultado, _ = self.rodar(timeout=10)
        t.join()
        self.assertEqual(resultado, "padrao")
        self.assertIsNone(g.CUSTOM_LAYOUT)

    def test_layout_inexistente_cai_no_padrao(self):
        t = self.decidir_depois({"acao": "salvo", "slug": "fantasma"})
        resultado, texto = self.rodar(timeout=10)
        t.join()
        self.assertEqual(resultado, "padrao")
        self.assertIsNone(g.CUSTOM_LAYOUT)
        self.assertIn("não foi encontrado", texto)

    def test_sem_resposta_no_prazo(self):
        resultado, texto = self.rodar(timeout=0.3)
        self.assertEqual(resultado, "tempo")
        self.assertIn("no prazo", texto)
        self.assertIn(g.LAYOUT_MARK_DONE, texto)

    def test_decisao_velha_nao_vale(self):
        self.espera.mkdir()
        (self.espera / "decisao.json").write_text(json.dumps({"acao": "padrao"}), encoding="utf-8")
        resultado, _ = self.rodar(timeout=0.3)
        self.assertEqual(resultado, "tempo")                            # a pasta foi limpa no início

    def test_arquivo_estragado_e_ignorado_ate_ficar_valido(self):
        def escrever():
            import time
            time.sleep(0.2)
            self.espera.mkdir(parents=True, exist_ok=True)
            (self.espera / "decisao.json").write_text("{pela metade", encoding="utf-8")
            time.sleep(0.3)
            (self.espera / "decisao.json").write_text(json.dumps({"acao": "padrao"}), encoding="utf-8")
        import threading
        threading.Thread(target=escrever, daemon=True).start()
        resultado, _ = self.rodar(timeout=10)
        self.assertEqual(resultado, "padrao")

    def test_quadros_sao_do_meio_de_cada_trecho_e_no_maximo_quatro(self):
        self.clips = [{"start": 100.0 * i, "duration": 50.0} for i in range(1, 8)]
        self.rodar(timeout=0.2)
        self.assertEqual(len(self.chamadas), g.LAYOUT_WAIT_MAX_FRAMES)
        primeiro = self.chamadas[0]
        self.assertEqual(primeiro[primeiro.index("-ss") + 1], "120.00")           # 100 + 40% de 50

    def test_sem_quadros_nao_espera(self):
        with mock.patch.object(g, "run_cmd", lambda *a, **k: (False, "", "erro")):
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()) as saida:
                resultado = g.wait_for_layout_choice(self.dir / "video.mp4", self.clips, timeout=30, poll=0.05)
        self.assertEqual(resultado, "padrao")
        self.assertNotIn(g.LAYOUT_MARK_WAITING, saida.getvalue())

    def test_flag_existe_e_so_espera_sem_layout_salvo(self):
        fonte = (RAIZ / "gerar_cortes.py").read_text(encoding="utf-8")
        self.assertIn('"--esperar-layout"', fonte)
        self.assertIn("if args.esperar_layout and not CUSTOM_LAYOUT:", fonte)

if __name__ == "__main__":
    unittest.main()
