"""Testes da biblioteca "Vídeos longos" do servidor: lista, vídeo, ações, edição, títulos e onda do áudio.

Sem rede, sem ffmpeg real, sem Groq. Rodar na pasta do projeto:  python -m unittest discover -s app/tests -v
"""
import json
import os
import stat
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_servidor import Base, servidor  # noqa: E402

NOME = "VideoLongo_20261125_100000.mp4"
MOMENTOS = [
    {"n": 1, "inicio_video": 100.0, "fim_video": 140.0, "inicio": 0.0, "fim": 40.0, "duracao": 40.0, "nota": 9.0,
     "motivo": "susto", "titulo": "SUSTO <b>NO</b> BAU"},
    {"n": 2, "inicio_video": 500.0, "fim_video": 540.0, "inicio": 40.0, "fim": 80.0, "duracao": 40.0, "nota": 8.0,
     "motivo": "risada", "titulo": "RISADA"},
    {"lixo": True}, "texto solto",
]

GERAR_LONGOS = textwrap.dedent(r'''
    import json, os, sys, time
    print("ARGS:", sys.argv[1:], flush=True)
    if "--sugerir-titulos" in sys.argv:
        modo = open("modo.txt", encoding="utf-8").read().strip() if os.path.exists("modo.txt") else "ok"
        if modo == "erro":
            print("❌ A IA não devolveu sugestões agora.", flush=True)
            sys.exit(1)
        print("TITULOS_JSON=" + json.dumps([{"titulo": "Os melhores sustos da live", "pergunta": ""},
                                            {"titulo": "x" * 300, "pergunta": ""}, {"titulo": " "}], ensure_ascii=False), flush=True)
        sys.exit(0)
    if any("lento" in a for a in sys.argv[1:]):
        time.sleep(60)
''')

FFMPEG_ONDA = textwrap.dedent('''\
    #!/usr/bin/env python3
    import os, struct, sys
    contador = sys.argv[0] + ".contador"
    open(contador, "a").write("x")
    if os.path.exists(sys.argv[0] + ".falha"):
        sys.stderr.write("erro")
        sys.exit(1)
    amostras = [int(32000 * (i % 100) / 100) * (1 if i % 2 else -1) for i in range(2000 * 120)]      # 120 s, mais forte no fim de cada bloco
    sys.stdout.buffer.write(struct.pack("<%dh" % len(amostras), *amostras))
''')


class BaseLongos(Base):
    def setUp(self):
        super().setUp()
        (self.raiz / "gerar_cortes.py").write_text(GERAR_LONGOS, encoding="utf-8")
        (self.raiz / ".env").write_text("GROQ_API_KEY=gsk_teste\n", encoding="utf-8")
        antiga = os.environ.pop("GROQ_API_KEY", None)
        self.addCleanup(lambda: os.environ.__setitem__("GROQ_API_KEY", antiga) if antiga else os.environ.pop("GROQ_API_KEY", None))
        self.pasta = self.raiz / "videos_longos"

    def criar_longo(self, nome=NOME, meta=None, bruto=True, visivel=False, tamanho=3000, mtime=None):
        if bruto:
            (self.pasta / "_brutos").mkdir(parents=True, exist_ok=True)
            (self.pasta / "_brutos" / nome).write_bytes(bytes(i % 251 for i in range(tamanho)))
        if visivel:
            self.pasta.mkdir(parents=True, exist_ok=True)
            (self.pasta / nome).write_bytes(b"EDITADO" * 100)
        if meta is not False:
            self.pasta.mkdir(parents=True, exist_ok=True)
            dados = {"origem": "longo", "bruto": True, "titulo": "Melhores momentos de Fulano", "streamer": "Fulano",
                     "duracao_final_s": 80.0, "clip_duration": 80.0, "alvo_s": 480, "momentos": MOMENTOS,
                     "video_url": "https://www.youtube.com/watch?v=abc", "transcricao": "fala " * 10,
                     "selecao": {"curto": True, "motivo": "só havia momentos bons para 1,3 min"},
                     "edicao": {"estado": "bruto"}}
            dados.update(meta or {})
            (self.pasta / nome).with_suffix(".json").write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        if mtime:
            for arq in (self.pasta / "_brutos" / nome, self.pasta / nome, self.pasta / nome.replace(".mp4", ".json")):
                if arq.is_file():
                    os.utime(arq, (mtime, mtime))


class TestLista(BaseLongos):
    def test_vazia_sem_pasta_e_com_pasta_vazia(self):
        self.assertEqual(self.json("GET", "/api/longos"), (200, {"longos": []}))
        self.pasta.mkdir()
        self.assertEqual(self.json("GET", "/api/longos")[1], {"longos": []})

    def test_campos_do_card(self):
        self.criar_longo()
        status, dados = self.json("GET", "/api/longos")
        self.assertEqual(status, 200)
        (v,) = dados["longos"]
        self.assertEqual((v["nome"], v["titulo"], v["estado"], v["bruto"], v["editado"]),
                         (NOME, "Melhores momentos de Fulano", "novo", True, False))
        self.assertEqual((v["duracao_s"], v["streamer"], v["tem_video_editado"]), (80.0, "Fulano", False))
        self.assertEqual(len(v["momentos"]), 2)                                  # o lixo não entra
        self.assertEqual(v["momentos"][0]["titulo"], "SUSTO <b>NO</b> BAU")      # texto puro: a tela usa textContent
        self.assertEqual(v["selecao"]["curto"], True)
        self.assertEqual(v["tamanho_mb"], round(3000 / 1_048_576, 1))
        self.assertNotIn("ordem", v)

    def test_so_aparece_quem_tem_bruto_json_e_origem_longo(self):
        self.criar_longo("A.mp4")
        self.criar_longo("B.mp4", bruto=False)                                   # sem bruto
        self.criar_longo("C.mp4", meta=False)                                    # sem json
        self.criar_longo("D.mp4", meta={"origem": "live"})
        self.criar_longo("_E.mp4")
        self.criar_longo("F.mp4", meta={"titulo": 5, "momentos": "x", "edicao": "x"})
        nomes = sorted(v["nome"] for v in self.json("GET", "/api/longos")[1]["longos"])
        self.assertEqual(nomes, ["A.mp4", "F.mp4"])

    def test_editado_e_ordem(self):
        self.criar_longo("velho.mp4", mtime=1000000)
        self.criar_longo("novo.mp4", visivel=True, meta={"bruto": False, "edicao": {"estado": "renderizado", "opcoes": {"legenda": True}},
                                                          "resumo_intro": {"texto": "Abertura."}, "cta_final": {"duracao_s": 6},
                                                          "resumo_motivo": "", "cta_motivo": ""})
        itens = self.json("GET", "/api/longos")[1]["longos"]
        self.assertEqual([v["nome"] for v in itens], ["novo.mp4", "velho.mp4"])
        novo = itens[0]
        self.assertTrue(novo["editado"] and novo["tem_video_editado"] and novo["cta"])
        self.assertEqual(novo["resumo"], "Abertura.")
        self.assertEqual(novo["edicao"]["opcoes"], {"legenda": True})

    def test_os_cortes_curtos_nao_veem_os_longos(self):
        self.criar_longo()
        self.assertEqual(self.json("GET", "/api/cortes")[1], {"cortes": []})


class TestVideo(BaseLongos):
    def test_serve_o_bruto_e_depois_o_editado(self):
        self.criar_longo()
        r = self.req("GET", f"/video?nome={NOME}&lib=longos")
        self.assertEqual((r.status, len(r.dados)), (200, 3000))
        self.assertEqual(r.getheader("Content-Type"), "video/mp4")
        self.criar_longo(visivel=True)
        self.assertTrue(self.req("GET", f"/video?nome={NOME}&lib=longos").dados.startswith(b"EDITADO"))
        self.assertEqual(len(self.req("GET", f"/video?nome={NOME}&lib=longos&bruto=1").dados), 3000)

    def test_faixa_de_bytes(self):
        self.criar_longo()
        r = self.req("GET", f"/video?nome={NOME}&lib=longos&bruto=1", extras={"Range": "bytes=100-199"})
        self.assertEqual((r.status, len(r.dados), r.getheader("Content-Range")), (206, 100, "bytes 100-199/3000"))

    def test_nomes_perigosos_e_inexistentes(self):
        self.criar_longo()
        for nome in ("../x.mp4", "..%2Fx.mp4", "nao_existe.mp4", "_x.mp4", "a.txt", ""):
            self.assertEqual(self.req("GET", f"/video?nome={nome}&lib=longos").status, 404, nome)
        self.criar_corte("so_corte.mp4")                                         # um corte curto não é servido como longo
        self.assertEqual(self.req("GET", "/video?nome=so_corte.mp4&lib=longos").status, 404)
        self.assertEqual(self.req("GET", f"/video?nome={NOME}").status, 404)    # sem lib=longos é a biblioteca dos cortes

    def test_exige_o_cookie(self):
        self.criar_longo()
        self.assertEqual(self.req("GET", f"/video?nome={NOME}&lib=longos", cookie=False).status, 403)
        self.assertEqual(self.req("GET", "/api/longos", cookie=False).status, 403)


class TestAcoes(BaseLongos):
    def acao(self, nome, a):
        return self.json("POST", "/api/longos/acao", {"nome": nome, "acao": a})

    def estado(self):
        return self.json("GET", "/api/longos")[1]["longos"][0]["estado"]

    def test_aprovar_e_desfazer(self):
        self.criar_longo()
        self.assertEqual(self.acao(NOME, "aprovar")[0], 200)
        self.assertEqual(self.estado(), "aprovado")
        self.assertTrue((self.pasta / "_status.json").is_file())
        self.assertEqual(self.acao(NOME, "desfazer")[0], 200)
        self.assertEqual(self.estado(), "novo")

    def test_descartar_leva_tudo_junto_e_nada_e_apagado(self):
        self.criar_longo(visivel=True)
        self.assertEqual(self.acao(NOME, "descartar")[0], 200)
        self.assertEqual(self.json("GET", "/api/longos")[1], {"longos": []})
        d = self.pasta / "_descartados"
        self.assertTrue((d / NOME).is_file() and (d / NOME.replace(".mp4", ".json")).is_file() and (d / "_brutos" / NOME).is_file())
        self.criar_longo()                                                        # descartar de novo um de mesmo nome não sobrescreve
        self.acao(NOME, "descartar")
        self.assertEqual(len(list((d / "_brutos").glob("VideoLongo*"))), 2)

    def test_acoes_invalidas(self):
        self.criar_longo()
        for corpo in ({"nome": NOME, "acao": "apagar_tudo"}, {"nome": "../x.mp4", "acao": "descartar"},
                      {"nome": "nao_existe.mp4", "acao": "aprovar"}, {"acao": "aprovar"}, {"nome": 5, "acao": "aprovar"}):
            self.assertEqual(self.json("POST", "/api/longos/acao", corpo)[0], 400, corpo)
        self.assertTrue((self.pasta / "_brutos" / NOME).is_file())

    def test_a_aprovacao_dos_cortes_curtos_e_separada(self):
        self.criar_longo()
        self.criar_corte("a.mp4")
        self.acao(NOME, "aprovar")
        self.assertEqual(self.json("GET", "/api/cortes")[1]["cortes"][0]["estado"], "novo")


class TestEdicao(BaseLongos):
    OPCOES = {"legenda": True, "capitulos": False, "resumo": True, "cta": False, "titulo": "  Meu  vídeo ", "trechos": [[0, 30], [40, 80]]}

    def test_validar_opcoes(self):
        ok = servidor.Estado.validar_opcoes_longo(self.OPCOES)
        self.assertEqual(ok, {"legenda": True, "capitulos": False, "resumo": True, "cta": False, "titulo": "Meu vídeo",
                              "espelhar": False, "trechos": [[0.0, 30.0], [40.0, 80.0]]})
        self.assertEqual(servidor.Estado.validar_opcoes_longo({}),
                         {"legenda": True, "capitulos": True, "resumo": True, "cta": True, "espelhar": False})
        self.assertIs(servidor.Estado.validar_opcoes_longo({"espelhar": True})["espelhar"], True)
        with self.assertRaises(ValueError):
            servidor.Estado.validar_opcoes_longo({"espelhar": "sim"})
        caixa = {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4}
        self.assertEqual(servidor.Estado.validar_opcoes_longo({"recorte": caixa})["recorte"], caixa)
        self.assertNotIn("recorte", servidor.Estado.validar_opcoes_longo({}))
        for ruim in ({}, "x", {"x": 0.9, "y": 0, "w": 0.5, "h": 0.5}, {"x": 0, "y": 0, "w": 0.01, "h": 0.5}):
            with self.assertRaises(ValueError):
                servidor.Estado.validar_opcoes_longo({"recorte": ruim})
        for ruim in (None, "x", {"legenda": "sim"}, {"cta": 1}, {"titulo": 5}, {"trechos": [[5, 1]]}, {"trechos": "x"},
                     {"trechos": [[0, 0.5]]}, {"trechos": [[0, 1], [0.5, 3]]}):
            with self.assertRaises(ValueError, msg=str(ruim)):
                servidor.Estado.validar_opcoes_longo(ruim)
        self.assertNotIn("titulo", servidor.Estado.validar_opcoes_longo({"titulo": "   "}))
        self.assertEqual(len(servidor.Estado.validar_opcoes_longo({"titulo": "a" * 500})["titulo"]), servidor.LONGO_TITULO_MAX)

    def test_edita_salva_as_opcoes_e_chama_o_renderizador_dos_longos(self):
        self.criar_longo()
        status, dados = self.json("POST", "/api/longos/editar", {"nome": NOME, "opcoes": self.OPCOES})
        self.assertEqual(status, 201)
        texto = "\n".join(self.esperar_fim()["linhas"])
        self.assertIn(f"ARGS: ['--renderizar', '{NOME}', '--biblioteca', 'longos']", texto)
        salvo = json.loads((self.pasta / "_edicoes" / NOME.replace(".mp4", ".json")).read_text(encoding="utf-8"))
        self.assertEqual(salvo["titulo"], "Meu vídeo")
        self.assertEqual(salvo["trechos"], [[0.0, 30.0], [40.0, 80.0]])
        self.assertFalse((self.saida / "_edicoes").exists())                         # nada vazou para a pasta dos cortes

    def test_recusas(self):
        self.criar_longo()
        for corpo in ({"nome": "nao_existe.mp4", "opcoes": {}}, {"nome": "../x.mp4", "opcoes": {}}, {"opcoes": {}},
                      {"nome": NOME, "opcoes": {"legenda": "x"}}, {"nome": NOME}):
            self.assertEqual(self.json("POST", "/api/longos/editar", corpo)[0], 400, corpo)
        self.criar_longo("B.mp4", meta={"origem": "live"})
        self.assertEqual(self.json("POST", "/api/longos/editar", {"nome": "B.mp4", "opcoes": {}})[0], 400)
        self.assertFalse((self.pasta / "_edicoes").exists())

    def test_so_uma_execucao_por_vez(self):
        self.criar_longo("lento.mp4")
        self.assertEqual(self.json("POST", "/api/longos/editar", {"nome": "lento.mp4", "opcoes": {}})[0], 201)
        self.assertEqual(self.json("POST", "/api/longos/editar", {"nome": "lento.mp4", "opcoes": {}})[0], 409)
        self.assertEqual(self.json("POST", "/api/cortes/editar", {"nome": "x.mp4", "opcoes": {}})[0], 400)


class TestTitulos(BaseLongos):
    def sugerir(self, nome=NOME):
        return self.json("POST", "/api/longos/titulos", {"nome": nome})

    def test_devolve_sugestoes_limpas_e_chama_com_a_biblioteca(self):
        self.criar_longo()
        status, dados = self.sugerir()
        self.assertEqual(status, 200)
        self.assertEqual(dados["titulos"][0], {"titulo": "Os melhores sustos da live", "pergunta": ""})
        self.assertEqual(len(dados["titulos"]), 2)                                  # o vazio some
        self.assertEqual(len(dados["titulos"][1]["titulo"]), servidor.LONGO_TITULO_MAX)

    def test_erros_viram_mensagem(self):
        self.criar_longo()
        self.assertEqual(self.sugerir("nao_existe.mp4")[0], 400)
        self.assertEqual(self.sugerir("../x.mp4")[0], 400)
        self.criar_longo("B.mp4", meta={"momentos": []})
        status, dados = self.sugerir("B.mp4")
        self.assertEqual(status, 400)
        self.assertIn("momentos", dados["erro"])
        (self.raiz / "modo.txt").write_text("erro", encoding="utf-8")
        status, dados = self.sugerir()
        self.assertEqual(status, 400)
        self.assertIn("IA", dados["erro"])
        (self.raiz / ".env").write_text("", encoding="utf-8")
        self.assertIn("GROQ_API_KEY", self.sugerir()[1]["erro"])

    def test_a_rota_dos_cortes_continua_igual(self):
        self.assertEqual(self.json("POST", "/api/cortes/titulos", {"nome": "nao_existe.mp4"})[0], 400)


class TestOnda(BaseLongos):
    def setUp(self):
        super().setUp()
        self.ffmpeg = self.raiz / "ffmpeg_falso"
        self.ffmpeg.write_text(FFMPEG_ONDA, encoding="utf-8")
        self.ffmpeg.chmod(self.ffmpeg.stat().st_mode | stat.S_IXUSR)
        self.srv.estado.caminho_ffmpeg = lambda: str(self.ffmpeg)

    def chamadas(self):
        arq = Path(str(self.ffmpeg) + ".contador")
        return len(arq.read_text()) if arq.is_file() else 0

    def test_picos_normalizados_e_com_cache(self):
        self.criar_longo()
        status, dados = self.json("GET", f"/api/longos/onda?nome={NOME}")
        self.assertEqual(status, 200)
        picos = dados["picos"]
        self.assertEqual(len(picos), servidor.ONDA_PONTOS)
        self.assertTrue(all(0 <= p <= 1 for p in picos))
        self.assertEqual(max(picos), 1.0)
        self.assertEqual(self.chamadas(), 1)
        self.assertEqual(self.json("GET", f"/api/longos/onda?nome={NOME}")[1]["picos"], picos)
        self.assertEqual(self.chamadas(), 1)                                          # segunda vez vem do cache
        self.assertTrue(list((self.pasta / "_cache").glob("*.onda.json")))

    def test_cache_vale_so_para_o_mesmo_arquivo(self):
        self.criar_longo()
        self.json("GET", f"/api/longos/onda?nome={NOME}")
        self.criar_longo(tamanho=4000)                                                # o bruto mudou
        self.json("GET", f"/api/longos/onda?nome={NOME}")
        self.assertEqual(self.chamadas(), 2)

    def test_erros(self):
        self.criar_longo()
        self.assertEqual(self.json("GET", "/api/longos/onda?nome=nao_existe.mp4")[0], 400)
        self.assertEqual(self.json("GET", "/api/longos/onda?nome=../x.mp4")[0], 400)
        self.assertEqual(self.json("GET", "/api/longos/onda")[0], 400)
        Path(str(self.ffmpeg) + ".falha").write_text("x")
        status, dados = self.json("GET", f"/api/longos/onda?nome={NOME}")
        self.assertEqual(status, 400)
        self.assertIn("áudio", dados["erro"])
        self.srv.estado.caminho_ffmpeg = lambda: None
        self.assertIn("ffmpeg não encontrado", self.json("GET", f"/api/longos/onda?nome={NOME}")[1]["erro"])


class TestEstado(BaseLongos):
    def test_recurso_novo_e_pasta_dos_longos(self):
        self.assertIn("videos_longos", self.json("GET", "/api/estado")[1]["recursos"])
        self.assertEqual(self.json("POST", "/api/pasta", {"pasta": "longos"})[0], 200)
        self.assertEqual(self.pastas_abertas, [str(self.pasta)])


if __name__ == "__main__":
    unittest.main()
