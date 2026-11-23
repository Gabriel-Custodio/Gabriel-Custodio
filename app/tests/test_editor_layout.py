"""Testes do editor de layout: layouts salvos, quadros (imagem do vídeo) e o layout personalizado do gerar_cortes.

Tudo com mocks: nenhum ffmpeg, yt-dlp ou rede. Rodar na pasta do projeto:
    python -m unittest discover -s app/tests -v
"""
import json
import os
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

LAYOUT = {"nome": "Alan Zoka", "cam": {"x": 0.76, "y": 0.35, "w": 0.22, "h": 0.2},
          "game": {"x": 0.2, "y": 0.0, "w": 0.55, "h": 1.0}, "cam_altura": 640, "ordem": "cam_primeiro"}


SALVO = {"nome": "Alan Zoka", "cam": {"x": 0.76, "y": 0.35, "w": 0.22, "h": 0.2},
         "game": {"x": 0.2, "y": 0.0, "w": 0.55, "h": 1.0}, "cam_altura": 640, "ordem": "cam_primeiro"}


class BaseEditor(Base):
    pass


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



class TestUrlDuplicada(BaseEditor):
    def test_executar_aceita_esquema_duplicado(self):
        self.json("POST", "/api/executar", {"url": "https://https://twitch.tv/videos/123", "modo": "ia"})
        texto = "\n".join(self.esperar_fim()["linhas"])
        self.assertIn("'https://twitch.tv/videos/123'", texto)
        self.assertNotIn("https://https://", texto)




# ------------------------------------------------------------------ servidor: menu Editar dos cortes
OPCOES_EMPILHADO = {"layout": "empilhado", "tarja": True, "legenda": True, "resumo": False, "cta": True,
                    "cam": {"x": 0.76, "y": 0.35, "w": 0.22, "h": 0.2}, "game": {"x": 0.2, "y": 0.0, "w": 0.55, "h": 1.0},
                    "cam_altura": 640, "ordem": "cam_primeiro"}
OPCOES_INTEIRO = {"layout": "inteiro", "tarja": False, "legenda": True, "resumo": True, "cta": False}


class TestEdicaoNoServidor(BaseEditor):
    NOME = "corte_gameplay_1_TESTE.mp4"

    def criar_bruto(self, nome=None, com_bruto=True, meta=None):
        nome = nome or self.NOME
        self.criar_corte(nome, 600, meta={"titulo_gancho": "T", "bruto": True, "edicao": {"estado": "bruto"}, **(meta or {})})
        if com_bruto:
            (self.saida / "_brutos").mkdir(exist_ok=True)
            (self.saida / "_brutos" / nome).write_bytes(b"BRUTO" * 300)
        return nome

    def editar(self, opcoes, nome=None):
        return self.json("POST", "/api/cortes/editar", {"nome": nome or self.NOME, "opcoes": opcoes})

    def test_listagem_mostra_se_e_editavel_e_bruto(self):
        self.criar_bruto()
        self.criar_bruto("corte_gameplay_2_SEM.mp4", com_bruto=False)
        _, dados = self.json("GET", "/api/cortes")
        por_nome = {c["nome"]: c for c in dados["cortes"]}
        c = por_nome[self.NOME]
        self.assertEqual((c["editavel"], c["bruto"], c["edicao"]), (True, True, {"estado": "bruto"}))
        self.assertFalse(por_nome["corte_gameplay_2_SEM.mp4"]["editavel"])

    def test_video_bruto_e_video_atual_sao_arquivos_diferentes(self):
        self.criar_bruto()
        atual = self.req("GET", f"/video?nome={self.NOME}")
        bruto = self.req("GET", f"/video?nome={self.NOME}&bruto=1")
        self.assertEqual(bruto.status, 200)
        self.assertEqual(bruto.dados, b"BRUTO" * 300)
        self.assertNotEqual(atual.dados, bruto.dados)
        self.assertEqual(self.req("GET", "/video?nome=nao_existe.mp4&bruto=1").status, 404)
        for ruim in ("../x.mp4", "_x.mp4", "a/b.mp4", ".mp4"):
            self.assertEqual(self.req("GET", f"/video?nome={ruim}&bruto=1").status, 404, ruim)
        self.assertEqual(self.req("GET", f"/video?nome={self.NOME}&bruto=1", cookie=False).status, 403)

    def test_editar_grava_opcoes_e_inicia_o_processo(self):
        self.criar_bruto()
        status, r = self.editar(OPCOES_EMPILHADO)
        self.assertEqual(status, 201)
        self.assertEqual(r["job"]["parametros"]["modo"], "edicao")
        texto = "\n".join(self.esperar_fim()["linhas"])
        self.assertIn(f"'--renderizar', '{self.NOME}'", texto)
        salvo = json.loads((self.saida / "_edicoes" / "corte_gameplay_1_TESTE.json").read_text(encoding="utf-8"))
        self.assertEqual((salvo["layout"], salvo["cam_altura"], salvo["resumo"]), ("empilhado", 640, False))
        self.assertTrue(g.normalize_edit_options(salvo))              # o script aceita o que o servidor gravou

    def test_editar_tela_inteira(self):
        self.criar_bruto()
        self.assertEqual(self.editar(OPCOES_INTEIRO)[0], 201)
        self.esperar_fim()
        salvo = json.loads((self.saida / "_edicoes" / "corte_gameplay_1_TESTE.json").read_text(encoding="utf-8"))
        self.assertEqual(salvo, {"layout": "inteiro", "tarja": False, "legenda": True, "resumo": True, "cta": False})
        self.assertTrue(g.normalize_edit_options(salvo))

    def test_editar_pedidos_invalidos(self):
        self.criar_bruto()
        self.criar_bruto("corte_gameplay_2_SEM.mp4", com_bruto=False)
        casos = [
            ({"nome": "nao_existe.mp4", "opcoes": OPCOES_INTEIRO}, "corte inexistente"),
            ({"nome": "corte_gameplay_2_SEM.mp4", "opcoes": OPCOES_INTEIRO}, "sem bruto"),
            ({"nome": "../x.mp4", "opcoes": OPCOES_INTEIRO}, "caminho"),
            ({"nome": self.NOME}, "sem opcoes"),
            ({"nome": self.NOME, "opcoes": "texto"}, "opcoes texto"),
            ({"nome": self.NOME, "opcoes": {**OPCOES_INTEIRO, "layout": "outro"}}, "layout invalido"),
            ({"nome": self.NOME, "opcoes": {**OPCOES_INTEIRO, "tarja": 1}}, "bool"),
            ({"nome": self.NOME, "opcoes": {**OPCOES_INTEIRO, "legenda": "sim"}}, "bool 2"),
            ({"nome": self.NOME, "opcoes": {**OPCOES_EMPILHADO, "cam_altura": 50}}, "altura"),
            ({"nome": self.NOME, "opcoes": {**OPCOES_EMPILHADO, "cam": {"x": 0.9, "y": 0, "w": 0.3, "h": 0.2}}}, "caixa fora"),
            ({"nome": self.NOME, "opcoes": {k: v for k, v in OPCOES_EMPILHADO.items() if k != "game"}}, "sem game"),
            ({"nome": self.NOME, "opcoes": {**OPCOES_EMPILHADO, "ordem": "x"}}, "ordem"),
        ]
        for corpo, rotulo in casos:
            self.assertEqual(self.json("POST", "/api/cortes/editar", corpo)[0], 400, rotulo)
        self.assertFalse((self.saida / "_edicoes").exists())            # nada foi gravado nem executado

    def test_nao_edita_com_outra_execucao_rodando(self):
        self.criar_bruto()
        self.assertEqual(self.json("POST", "/api/executar", {"url": "https://x.com/lento", "modo": "ia"})[0], 201)
        status, _ = self.editar(OPCOES_INTEIRO)
        self.assertEqual(status, 409)
        self.srv.estado.parar()

    def test_seguranca(self):
        self.criar_bruto()
        corpo = {"nome": self.NOME, "opcoes": OPCOES_INTEIRO}
        self.assertEqual(self.json("POST", "/api/cortes/editar", corpo, cookie=False)[0], 403)
        self.assertEqual(self.json("POST", "/api/cortes/editar", corpo, csrf=False)[0], 403)

    def test_descartar_leva_o_bruto_junto(self):
        self.criar_bruto()
        self.assertEqual(self.json("POST", "/api/cortes/acao", {"nome": self.NOME, "acao": "descartar"})[0], 200)
        self.assertFalse((self.saida / "_brutos" / self.NOME).exists())
        self.assertTrue((self.saida / "_descartados" / "_brutos" / self.NOME).is_file())
        self.assertTrue((self.saida / "_descartados" / self.NOME).is_file())

    def test_o_gerar_cortes_falso_nao_recebe_opcoes_de_geracao_antigas(self):
        self.json("POST", "/api/executar", {"url": URL_OK, "modo": "gameplay", "cortes": 1})
        texto = "\n".join(self.esperar_fim()["linhas"]).split("ARGS:")[-1].split("\n")[0]
        self.assertNotIn("--layout-salvo", texto)
        self.assertNotIn("--esperar-layout", texto)


# ------------------------------------------------------------------ gerar_cortes: corte bruto e --renderizar
class BaseScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.out = self.dir / "cortes_prontos"
        self.temp = self.dir / "temp"
        self.out.mkdir()
        self.temp.mkdir()
        for alvo, valor in (("OUTPUT_DIR", self.out), ("TEMP_DIR", self.temp), ("HISTORICO", None),
                            ("GAMEPLAY_WATERMARK_PATH", str(self.dir / "nao_existe.png"))):
            p = mock.patch.object(g, alvo, valor)
            p.start()
            self.addCleanup(p.stop)
        self.env_antigo = {k: os.environ.get(k) for k in ("CORTES_GAMEPLAY_BRUTO", "CORTES_LAYOUT", "CORTES_RESUMO", "CORTES_CTA")}
        for k in self.env_antigo:
            os.environ.pop(k, None)
        self.addCleanup(self._restaurar)
        g.CUSTOM_LAYOUT = None
        g.STREAMER_NOME = None

    def _restaurar(self):
        g.CUSTOM_LAYOUT = None
        g.STREAMER_NOME = None
        g.FIT_SUBTITLE_BELOW_VIDEO = False
        for k, v in self.env_antigo.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


SEGMENTOS = [
    {"start": 95.0, "end": 103.0, "text": "antes do corte", "words": [{"word": "antes", "start": 95.0, "end": 96.0}]},
    {"start": 100.0, "end": 108.0, "text": "olha so isso aqui", "words": [
        {"word": "olha", "start": 101.0, "end": 101.5}, {"word": "so", "start": 101.5, "end": 102.0},
        {"word": "isso", "start": 107.5, "end": 109.0}]},
    {"start": 120.0, "end": 125.0, "text": "depois do fim", "words": []},
    {"start": 200.0, "end": 210.0, "text": "bem depois"},
]


class TestCorteBruto(BaseScript):
    def test_padrao_ligado_e_desliga_com_env(self):
        self.assertTrue(g.gameplay_bruto_enabled())
        for v in ("0", "false", "off", "não"):
            os.environ["CORTES_GAMEPLAY_BRUTO"] = v
            self.assertFalse(g.gameplay_bruto_enabled(), v)
        os.environ["CORTES_GAMEPLAY_BRUTO"] = "1"
        self.assertTrue(g.gameplay_bruto_enabled())

    def test_segmentos_relativos(self):
        rel = g.relative_segments(SEGMENTOS, 100.0, 110.0)
        self.assertEqual([r["text"] for r in rel], ["antes do corte", "olha so isso aqui"])    # só os que tocam o trecho
        self.assertEqual((rel[0]["start"], rel[0]["end"]), (-5.0, 3.0))
        self.assertNotIn("words", rel[0])                                    # a palavra de antes do corte não entra
        palavras = rel[1]["words"]
        self.assertEqual([(w["word"], w["start"], w["end"]) for w in palavras],
                         [("olha", 1.0, 1.5), ("so", 1.5, 2.0), ("isso", 7.5, 9.0)])
        longa = [{"start": 100.0, "end": 120.0, "text": "x", "words": [{"word": "x", "start": 108.0, "end": 115.0}]}]
        self.assertEqual(g.relative_segments(longa, 100.0, 110.0)[0]["words"], [{"word": "x", "start": 8.0, "end": 10.0}])
        self.assertEqual(g.relative_segments(None, 0, 10), [])

    def test_export_raw_gameplay(self):
        seg = self.temp / "segmento.mp4"
        seg.write_bytes(b"VIDEO-BRUTO" * 100)
        saida = self.out / "corte_gameplay_1_X.mp4"
        clip = {"start": 100.0, "end": 110.0, "duration": 10.0, "score": 8.0, "reason": "r", "fonte": "f", "band": 2}
        g.STREAMER_NOME = "Alan"
        ok = g.export_raw_gameplay(clip, SEGMENTOS, saida, seg, {"titulo_gancho": "TÍTULO", "pergunta_cta": "E AÍ?", "nota": 88},
                                   "ia_trecho_real", "olha so isso aqui", 4, 0.4)
        self.assertTrue(ok)
        bruto = self.out / "_brutos" / saida.name
        self.assertEqual(bruto.read_bytes(), b"VIDEO-BRUTO" * 100)
        self.assertEqual(saida.read_bytes(), bruto.read_bytes())
        self.assertFalse(seg.exists())                                       # o pré-corte foi movido, não duplicado
        self.assertNotEqual(os.stat(saida).st_ino, os.stat(bruto).st_ino)      # cópias independentes (não links)
        meta = json.loads(saida.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual((meta["bruto"], meta["edicao"], meta["streamer"], meta["duracao_final_s"]),
                         (True, {"estado": "bruto"}, "Alan", 10.0))
        self.assertEqual((meta["titulo_gancho"], meta["clip_start"], meta["transcricao"]), ("TÍTULO", 100.0, "olha so isso aqui"))
        self.assertEqual(len(meta["legenda_segmentos"]), 2)
        self.assertNotIn("layout", meta)                                     # nenhum layout aplicado ainda

    def test_export_raw_sem_segmento_falha_sem_criar_nada(self):
        saida = self.out / "x.mp4"
        self.assertFalse(g.export_raw_gameplay({"start": 0, "end": 5, "duration": 5}, [], saida, None, {}, "p", "t", 1, 0.1))
        self.assertFalse(g.export_raw_gameplay({"start": 0, "end": 5, "duration": 5}, [], saida, self.temp / "nao.mp4", {}, "p", "t", 1, 0.1))
        self.assertEqual(list(self.out.iterdir()), [])

    def test_cut_and_export_gameplay_no_modo_bruto_nao_aplica_efeito_nenhum(self):
        def pre_corte(video, start, dur, saida):
            Path(saida).write_bytes(b"PRECORTE" * 50)
            return True

        def nao_pode(*a, **k):
            raise AssertionError("não deve renderizar layout/legenda/resumo/chamada no modo bruto")
        clip = {"start": 100.0, "end": 110.0, "duration": 10.0, "sem_checagem_fala": True,
                "hook_data": {"titulo_gancho": "T", "pergunta_cta": "P", "nota": 90}}
        saida = self.out / "corte_gameplay_1_Y.mp4"
        with mock.patch.object(g, "extract_video_segment", pre_corte), \
                mock.patch.object(g, "process_clipflow_layout", nao_pode), \
                mock.patch.object(g, "create_karaoke_ass_subtitle", nao_pode), \
                mock.patch.object(g, "add_summary_intro", nao_pode), mock.patch.object(g, "add_cta_outro", nao_pode):
            ok = g.cut_and_export_gameplay(self.dir / "video.mp4", clip, SEGMENTOS, saida, 1, None, None)
        self.assertTrue(ok)
        self.assertEqual((self.out / "_brutos" / saida.name).read_bytes(), b"PRECORTE" * 50)
        self.assertEqual(saida.read_bytes(), b"PRECORTE" * 50)

    def test_modo_antigo_continua_disponivel(self):
        os.environ["CORTES_GAMEPLAY_BRUTO"] = "0"
        chamadas = []

        def pre_corte(video, start, dur, saida):
            Path(saida).write_bytes(b"x" * 2000)
            return True

        def layout(**k):
            chamadas.append(k)
            Path(k["output_path"]).write_bytes(b"final")
            return True
        clip = {"start": 100.0, "end": 110.0, "duration": 10.0, "sem_checagem_fala": True,
                "hook_data": {"titulo_gancho": "T", "pergunta_cta": "P", "nota": 90}}
        saida = self.out / "corte_gameplay_1_Z.mp4"
        with mock.patch.object(g, "extract_video_segment", pre_corte), mock.patch.object(g, "process_clipflow_layout", layout), \
                mock.patch.object(g, "add_summary_intro", lambda *a, **k: None), mock.patch.object(g, "add_cta_outro", lambda *a, **k: None):
            self.assertTrue(g.cut_and_export_gameplay(self.dir / "video.mp4", clip, SEGMENTOS, saida, 1, None, None))
        self.assertEqual(len(chamadas), 1)
        self.assertFalse((self.out / "_brutos").exists())

    def test_main_pula_a_pergunta_da_webcam_no_modo_bruto(self):
        fonte = (RAIZ / "gerar_cortes.py").read_text(encoding="utf-8")
        self.assertIn("if gameplay_bruto_enabled():", fonte)
        self.assertIn("Modo bruto: o corte sai com a tela completa", fonte)
        self.assertEqual(g.DEFAULT_LAYOUT, "fit")


class TestOpcoesDeEdicao(unittest.TestCase):
    def test_validas(self):
        o = g.normalize_edit_options(OPCOES_EMPILHADO)
        self.assertEqual((o["layout"], o["cam_altura"], o["tarja"], o["resumo"]), ("empilhado", 640, True, False))
        self.assertEqual(g.normalize_edit_options(OPCOES_INTEIRO),
                         {"layout": "inteiro", "tarja": False, "legenda": True, "resumo": True, "cta": False})
        self.assertEqual(g.normalize_edit_options({"layout": "inteiro"})["tarja"], True)       # padrão: tudo ligado

    def test_invalidas(self):
        for ruim in (None, [], {}, {"layout": "x"}, {**OPCOES_INTEIRO, "tarja": "sim"}, {**OPCOES_INTEIRO, "cta": None},
                     {**OPCOES_EMPILHADO, "cam_altura": 5}, {**OPCOES_EMPILHADO, "ordem": "z"},
                     {k: v for k, v in OPCOES_EMPILHADO.items() if k != "cam"},
                     {**OPCOES_EMPILHADO, "game": {"x": 2, "y": 0, "w": 0.5, "h": 0.5}}):
            self.assertIsNone(g.normalize_edit_options(ruim), repr(ruim)[:80])


class TestRenderizarEdicao(BaseScript):
    NOME = "corte_gameplay_1_X.mp4"

    def montar(self, opcoes, meta_extra=None, com_bruto=True):
        (self.out / "_brutos").mkdir()
        (self.out / "_edicoes").mkdir()
        if com_bruto:
            (self.out / "_brutos" / self.NOME).write_bytes(b"BRUTO" * 100)
        (self.out / self.NOME).write_bytes(b"BRUTO" * 100)
        meta = {"titulo_gancho": "TÍTULO", "pergunta_cta": "E AÍ?", "nota": 90, "transcricao": "olha so isso aqui",
                "clip_duration": 10.0, "streamer": "Alan", "bruto": True, "edicao": {"estado": "bruto"},
                "legenda_segmentos": g.relative_segments(SEGMENTOS, 100.0, 110.0), **(meta_extra or {})}
        (self.out / self.NOME).with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
        (self.out / "_edicoes" / "corte_gameplay_1_X.json").write_text(json.dumps(opcoes), encoding="utf-8")

    def renderizar(self, ffmpeg_ok=True):
        self.cmds = []
        self.extras = {}

        def run(cmd, *a, **k):
            self.cmds.append(cmd)
            if ffmpeg_ok and "-filter_complex" in cmd:
                Path(cmd[-1]).write_bytes(b"EDITADO" * 100)
            return ffmpeg_ok, "", ""

        def intro(path, texto, chave, streamer=None):
            self.extras["intro"] = (texto, streamer, os.environ.get("CORTES_RESUMO"))
            return {"duracao_s": 7.0, "texto": "resumo"} if os.environ.get("CORTES_RESUMO") == "1" else None

        def cta(path):
            self.extras["cta"] = os.environ.get("CORTES_CTA")
            return {"duracao_s": 5.0} if os.environ.get("CORTES_CTA") == "1" else None
        with mock.patch.object(g, "run_cmd", run), mock.patch.object(g, "get_video_dimensions", lambda *_: (1920, 1080)), \
                mock.patch.object(g, "load_env_file", lambda *a, **k: None), mock.patch.object(g, "get_groq_key", lambda: "chave"), \
                mock.patch.object(g, "add_summary_intro", intro), mock.patch.object(g, "add_cta_outro", cta):
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                return g.render_edit(self.NOME)

    def meta(self):
        return json.loads((self.out / self.NOME).with_suffix(".json").read_text(encoding="utf-8"))

    def test_empilhado_com_tudo(self):
        self.montar({**OPCOES_EMPILHADO, "resumo": True, "cta": True})
        self.assertTrue(self.renderizar())
        self.assertEqual((self.out / self.NOME).read_bytes(), b"EDITADO" * 100)             # o visível foi trocado
        self.assertEqual((self.out / "_brutos" / self.NOME).read_bytes(), b"BRUTO" * 100)    # o bruto não foi tocado
        m = self.meta()
        self.assertEqual((m["bruto"], m["layout"], m["edicao"]["estado"]), (False, "custom", "renderizado"))
        self.assertEqual(m["edicao"]["opcoes"]["layout"], "empilhado")
        self.assertEqual(m["duracao_final_s"], 22.0)                                    # 10 + resumo 7 + chamada 5
        self.assertEqual(self.extras["intro"][:2], ("olha so isso aqui", "Alan"))
        grafo = [c for c in self.cmds if "-filter_complex" in c][0]
        grafo = grafo[grafo.index("-filter_complex") + 1]
        self.assertIn("vstack=inputs=3", grafo)
        self.assertIn("subtitles=", grafo)
        self.assertEqual(str(self.out / "_brutos" / self.NOME), [c for c in self.cmds if "-filter_complex" in c][0][self.cmds[0].index("-i") + 1])

    def test_opcoes_desligadas_nao_chamam_resumo_nem_chamada(self):
        self.montar({**OPCOES_EMPILHADO, "tarja": False, "legenda": False, "resumo": False, "cta": False})
        self.assertTrue(self.renderizar())
        self.assertEqual((self.extras["intro"][2], self.extras["cta"]), ("0", "0"))
        grafo = self.cmds[0][self.cmds[0].index("-filter_complex") + 1]
        self.assertIn("vstack=inputs=2", grafo)                       # sem tarja: só os dois blocos
        self.assertNotIn("subtitles=", grafo)                         # sem legenda
        self.assertNotIn("-loop", self.cmds[0])                       # e sem imagem da faixa
        m = self.meta()
        self.assertEqual((m["resumo_intro"], m["cta_final"], m["duracao_final_s"]), (None, None, 10.0))

    def test_tela_inteira_usa_o_fit_com_legenda_embaixo(self):
        self.montar({**OPCOES_INTEIRO, "tarja": True})
        self.assertTrue(self.renderizar())
        self.assertEqual(self.meta()["layout"], "fit")
        self.assertEqual(os.environ.get("CORTES_LAYOUT"), "fit")
        gerados = list(self.temp.glob("*_fit.ass"))
        self.assertTrue(gerados)
        texto = gerados[0].read_text(encoding="utf-8")
        import re
        y = int(re.search(r"\\pos\(540,(\d+)\)", texto).group(1))
        self.assertGreater(y, (1920 + 608) // 2)                       # abaixo da borda de baixo do vídeo, não por cima dele

    def test_tela_inteira_sem_tarja_nao_desenha_o_titulo(self):
        self.montar({**OPCOES_INTEIRO, "tarja": False})
        with mock.patch.object(g, "create_hook_banner_image", side_effect=AssertionError("sem tarja não há banner")):
            self.assertTrue(self.renderizar())

    def test_falha_do_ffmpeg_deixa_o_corte_como_estava(self):
        self.montar(OPCOES_EMPILHADO)
        self.assertFalse(self.renderizar(ffmpeg_ok=False))
        self.assertEqual((self.out / self.NOME).read_bytes(), b"BRUTO" * 100)
        self.assertTrue(self.meta()["bruto"])

    def test_sem_bruto_ou_opcoes_invalidas_ou_nome_ruim(self):
        self.montar(OPCOES_EMPILHADO, com_bruto=False)
        self.assertFalse(self.renderizar())
        self.montar_invalida = (self.out / "_edicoes" / "corte_gameplay_1_X.json")
        (self.out / "_brutos" / self.NOME).write_bytes(b"BRUTO")
        self.montar_invalida.write_text(json.dumps({"layout": "x"}), encoding="utf-8")
        self.assertFalse(self.renderizar())
        for ruim in ("../x.mp4", "_x.mp4", "x.txt", ".mp4"):
            self.assertFalse(g.render_edit(ruim), ruim)

    def test_sem_legenda_guardada_renderiza_sem_ela(self):
        self.montar(OPCOES_EMPILHADO, meta_extra={"legenda_segmentos": []})
        self.assertTrue(self.renderizar())
        grafo = self.cmds[0][self.cmds[0].index("-filter_complex") + 1]
        self.assertNotIn("subtitles=", grafo)

    def test_editar_de_novo_parte_sempre_do_bruto(self):
        self.montar(OPCOES_EMPILHADO)
        self.assertTrue(self.renderizar())
        (self.out / "_edicoes" / "corte_gameplay_1_X.json").write_text(json.dumps(OPCOES_INTEIRO), encoding="utf-8")
        self.assertTrue(self.renderizar())
        entrada = self.cmds[0][self.cmds[0].index("-i") + 1]
        self.assertEqual(entrada, str(self.out / "_brutos" / self.NOME))
        self.assertEqual(self.meta()["edicao"]["opcoes"]["layout"], "inteiro")

    def test_flag_renderizar_existe(self):
        fonte = (RAIZ / "gerar_cortes.py").read_text(encoding="utf-8")
        self.assertIn('"--renderizar"', fonte)
        self.assertIn("render_edit(args.renderizar)", fonte)


class TestLayoutSemTarja(BaseScript):
    def test_geometria_sem_faixa(self):
        lay = g.normalize_saved_layout({**SALVO, "tarja": False})
        geo = g.custom_geometry(1920, 1080, lay)
        self.assertEqual((geo["band_h"], geo["cam_h"], geo["game_h"]), (0, 640, 1920 - 640))
        self.assertEqual((geo["y_cam"], geo["y_band"], geo["y_game"]), (0, 640, 640))
        lay = g.normalize_saved_layout({**SALVO, "tarja": False, "ordem": "game_primeiro"})
        geo = g.custom_geometry(1920, 1080, lay)
        self.assertEqual((geo["y_game"], geo["y_band"], geo["y_cam"]), (0, 1280, 1280))

    def test_tarja_precisa_ser_bool(self):
        self.assertIsNone(g.normalize_saved_layout({**SALVO, "tarja": "sim"}))
        self.assertTrue(g.normalize_saved_layout(SALVO)["tarja"])                     # padrão: com tarja

    def test_comando_sem_faixa(self):
        geo = g.custom_geometry(1920, 1080, g.normalize_saved_layout({**SALVO, "tarja": False}))
        ass = self.temp / "s.ass"
        ass.write_text("Style: Default,Arial,62,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,320,1\n"
                       "Dialogue: 0,0:00:01.00,0:00:02.00,Karaoke,,0,0,0,karaoke,{\\pos(540,835)}OI\n", encoding="utf-8")
        cmd = g.build_cam_cmd(self.dir / "in.mp4", self.dir / "out.mp4", ass, self.dir / "faixa.png", geo, 30.0)
        grafo = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("[cam][game]vstack=inputs=2", grafo)
        self.assertNotIn("[1:v]", grafo)
        self.assertNotIn("-loop", cmd)
        self.assertIn("\\pos(540,640)", (self.temp / "s_cam.ass").read_text(encoding="utf-8"))    # legenda na junção dos blocos

    def test_com_faixa_continua_igual(self):
        geo = g.custom_geometry(1920, 1080, g.normalize_saved_layout(SALVO))
        cmd = g.build_cam_cmd(self.dir / "in.mp4", self.dir / "out.mp4", None, self.dir / "faixa.png", geo, 30.0)
        self.assertIn("[cam][1:v][game]vstack=inputs=3", cmd[cmd.index("-filter_complex") + 1])
        self.assertIn("-loop", cmd)

if __name__ == "__main__":
    unittest.main()
