#!/usr/bin/env python3
"""
Interface gráfica local da Fábrica de Cortes (protótipo).

Abre uma página no navegador para:
  1. ver os cortes prontos (preview do vídeo, título, texto falado) e aprovar/descartar;
  2. gerar novos cortes (URL, modo, opções) acompanhando o log ao vivo.

Só usa a biblioteca padrão do Python (nada para instalar). O servidor escuta apenas em
127.0.0.1 (este computador). O código de corte continua sendo o gerar_cortes.py, que roda
como um processo separado: esta interface não altera o que ele faz.

Uso:  python app/servidor.py [--porta 8765] [--sem-navegador]
"""
import argparse
import atexit
import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import canais as modulo_canais
from canais import PLATAFORMAS, RegistroCanais
from lives import GerenteLives

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
PORTA_PADRAO = 8765

MODOS = {"ia": "Narração por IA", "gameplay": "Gameplay / Streamer", "original": "Áudio original"}
LAYOUTS = {"cam", "fit", "clipflow"}
FUNDOS = {"blur", "black"}
RECURSOS = ["edicao", "linha_do_tempo", "canais", "monitor_lives", "titulo_editavel", "video_longo", "videos_longos"]      # o que este servidor sabe fazer; a página confere (servidor antigo + página nova = aviso)
RE_SLUG = re.compile(r"^[a-z0-9_-]{1,40}$")
CAM_ALTURA_MIN, CAM_ALTURA_MAX, CAM_ALTURA_PADRAO = 300, 1100, 600
ORDENS = ("cam_primeiro", "game_primeiro")
TRECHOS_MAX = 60           # pedaços mantidos na linha do tempo (igual ao gerar_cortes.py)
TRECHO_MIN_S = 0.2
TITULO_MAX = 60            # caracteres do título/pergunta da tarja (igual ao gerar_cortes.py)
SUGERIR_TIMEOUT_S = 75     # tempo máximo esperando as sugestões de título
TRECHOS_TOTAL_MIN_S = 1.0
LONGO_MIN_MIN, LONGO_MAX_MIN = 4, 12     # duração do vídeo longo, em minutos (igual ao pacote longos/)
LONGO_TITULO_MAX = 100                   # caracteres do título do vídeo longo (igual ao pacote longos/)
LONGO_RECORTE_MIN = 0.05                 # menor lado do recorte, em fração do quadro (igual ao pacote longos/)
ONDA_PONTOS = 2400                       # picos da onda do áudio que a linha do tempo desenha
ONDA_TIMEOUT_S = 600
MAX_TRANSCRICAO = 8000                   # texto falado de um vídeo longo mostrado no card
MAX_LINHAS_LOG = 20000
MAX_CORPO = 64 * 1024
RE_URL = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
COOKIE = "fc_token"


# ---------------------------------------------------------------- utilidades
def agora():
    return datetime.now().isoformat(timespec="seconds")


def so_nome_de_arquivo(nome, extensoes=(".mp4",)):
    """Nome simples de arquivo (sem pastas), com extensão permitida e sem '_' no começo."""
    if not isinstance(nome, str) or not nome or len(nome) > 255:
        return None
    if Path(nome).name != nome or nome.startswith((".", "_")) or "\x00" in nome:
        return None
    if "/" in nome or "\\" in nome or not nome.lower().endswith(extensoes):
        return None
    return nome


def slug_do_nome(nome):
    """Nome de arquivo seguro (minúsculas, sem acento, só a-z 0-9 _ -); igual ao do gerar_cortes.py."""
    plano = unicodedata.normalize("NFKD", str(nome or "").lower())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9_-]+", "-", plano).strip("-_")[:40]


def caixa_valida(caixa):
    """Caixa {x,y,w,h} em frações do vídeo (0 a 1), dentro do quadro. None se inválida."""
    if not isinstance(caixa, dict):
        return None
    try:
        x, y, w, h = (float(caixa[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if any(v != v or v in (float("inf"), float("-inf")) for v in (x, y, w, h)):
        return None
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.0001 and y + h <= 1.0001):
        return None
    return {"x": round(x, 5), "y": round(y, 5), "w": round(min(w, 1 - x), 5), "h": round(min(h, 1 - y), 5)}


def ler_json(caminho):
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8", errors="replace"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def abrir_pasta(caminho):
    """Abre a pasta no gerenciador de arquivos do sistema."""
    caminho = str(caminho)
    if sys.platform.startswith("win"):
        os.startfile(caminho)  # noqa: S606 (só abre uma pasta local)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", caminho])
    else:
        subprocess.Popen(["xdg-open", caminho], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def chave_groq_configurada(raiz):
    """True se o .env tem GROQ_API_KEY preenchida. Nunca devolve a chave."""
    if os.environ.get("GROQ_API_KEY", "").strip():
        return True
    try:
        for linha in (raiz / ".env").read_text(encoding="utf-8-sig", errors="replace").splitlines():
            linha = linha.strip()
            if linha.startswith("GROQ_API_KEY"):
                valor = linha.split("=", 1)[1].strip().strip('"').strip("'") if "=" in linha else ""
                return bool(valor) and valor != "cole_sua_chave_aqui"
    except OSError:
        pass
    return False


# ---------------------------------------------------------------- execução (job)
class Job:
    """Uma execução do gerar_cortes.py, com o log guardado em memória e em arquivo."""

    def __init__(self, parametros, comando):
        self.id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.parametros = parametros
        self.comando = comando
        self.inicio = time.time()
        self.fim = None
        self.codigo = None
        self.estado = "rodando"            # rodando | concluido | erro | cancelado
        self.cancelado = False
        self.linhas = []
        self.base = 0                      # quantas linhas antigas foram descartadas
        self.proc = None
        self.lock = threading.Lock()
        self.arquivo_log = None

    def adicionar(self, linha):
        with self.lock:
            self.linhas.append(linha)
            if len(self.linhas) > MAX_LINHAS_LOG:
                sobra = len(self.linhas) - MAX_LINHAS_LOG
                del self.linhas[:sobra]
                self.base += sobra
        if self.arquivo_log:
            try:
                self.arquivo_log.write(linha + "\n")
                self.arquivo_log.flush()
            except (OSError, ValueError):
                pass

    def desde(self, indice):
        """Linhas a partir do índice absoluto `indice` (a interface pede só o que falta)."""
        with self.lock:
            indice = max(indice, self.base)
            return self.linhas[indice - self.base:], self.base + len(self.linhas)

    def publico(self):
        fim = self.fim or time.time()
        return {
            "id": self.id,
            "estado": self.estado,
            "codigo": self.codigo,
            "parametros": self.parametros,
            "segundos": round(fim - self.inicio),
        }


class Estado:
    """Tudo que o servidor precisa saber: pastas, token, aprovações e a execução atual."""

    def __init__(self, raiz, python=None):
        self.raiz = Path(raiz).resolve()
        self.saida = self.raiz / "cortes_prontos"
        self.descartados = self.saida / "_descartados"
        self.saida_longos = self.raiz / "videos_longos"       # vídeos longos (horizontais) gerados com a opção "vídeo longo"
        self.brutos_longos = self.saida_longos / "_brutos"    # o bruto de cada vídeo longo (cada edição parte dele)
        self.edicoes_longos = self.saida_longos / "_edicoes"
        self.descartados_longos = self.saida_longos / "_descartados"
        self.arquivo_status_longos = self.saida_longos / "_status.json"
        self.cache_longos = self.saida_longos / "_cache"      # onda do áudio já calculada
        self.arquivo_status = self.saida / "_status.json"
        self.logs = APP_DIR / "logs"
        self.layouts = self.raiz / "layouts_salvos"
        self.brutos = self.saida / "_brutos"          # cortes brutos originais (cada edição parte deles)
        self.edicoes = self.saida / "_edicoes"        # opções da última edição de cada corte
        self.canais = RegistroCanais(self.raiz / "canais_permitidos.json")   # quem o monitor de lives pode assistir
        self.python = python or sys.executable
        self.lives = GerenteLives(self.raiz, self.canais, python=self.python)   # um processo de monitor por live
        self.token = secrets.token_urlsafe(32)
        self.job = None
        self.trava = threading.Lock()          # protege self.job e o arquivo de status

    # ---- cortes
    def _status(self, arquivo=None):
        return ler_json(arquivo or self.arquivo_status)

    def _gravar_status(self, dados, arquivo=None):
        arquivo = arquivo or self.arquivo_status
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        tmp = arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(arquivo)

    def listar_cortes(self):
        if not self.saida.is_dir():
            return []
        with self.trava:
            status = self._status()
        cortes = []
        for arq in self.saida.iterdir():
            if not arq.is_file() or so_nome_de_arquivo(arq.name) is None:
                continue
            meta = ler_json(arq.with_suffix(".json"))
            info = arq.stat()
            cortes.append({
                "nome": arq.name,
                "tamanho_mb": round(info.st_size / 1_048_576, 1),
                "modificado": datetime.fromtimestamp(info.st_mtime).isoformat(timespec="seconds"),
                "ordem": info.st_mtime,
                "estado": (status.get(arq.name) or {}).get("estado", "novo"),
                "titulo": str(meta.get("titulo_gancho") or ""),
                "pergunta": str(meta.get("pergunta_cta") or ""),
                "nota": meta.get("nota"),
                "transcricao": str(meta.get("transcricao") or ""),
                "fonte_titulo": meta.get("fonte_titulo"),
                "modo": meta.get("modo") or ("gameplay" if arq.name.startswith("corte_gameplay") else None),
                "score": meta.get("score_selecao"),
                "motivo": meta.get("motivo_selecao"),
                "inicio_s": meta.get("clip_start"),
                "cta": isinstance(meta.get("cta_final"), dict),
                "resumo": str((meta.get("resumo_intro") or {}).get("texto") or "") if isinstance(meta.get("resumo_intro"), dict) else "",
                "fim_s": meta.get("clip_end"),
                "duracao_s": meta.get("clip_duration"),
                "tem_json": bool(meta),
                "potencial": meta.get("potencial") if meta.get("potencial") in ("medio", "alto") else None,   # cortes de live
                "origem": meta.get("origem") if meta.get("origem") in ("live",) else None,      # corte vindo do monitor de lives
                "canal": str((meta.get("live") or {}).get("nome") or "")[:60] if isinstance(meta.get("live"), dict) else "",
                "bruto": bool(meta.get("bruto")),                                  # ainda sem edição (tela completa)
                "editavel": (self.brutos / arq.name).is_file(),
                "layout": meta.get("layout"),
                "edicao": meta.get("edicao") if isinstance(meta.get("edicao"), dict) else None,
            })
        cortes.sort(key=lambda c: c.pop("ordem"), reverse=True)
        return cortes

    def caminho_corte(self, nome):
        nome = so_nome_de_arquivo(nome)
        if nome is None:
            return None
        caminho = (self.saida / nome).resolve()
        if caminho.parent != self.saida.resolve() or not caminho.is_file():
            return None
        return caminho

    def acao_corte(self, nome, acao):
        """Aprovar, desfazer a aprovação ou descartar (move para _descartados; nada é apagado)."""
        caminho = self.caminho_corte(nome)
        if caminho is None:
            return False, "Corte não encontrado."
        with self.trava:
            status = self._status()
            if acao == "aprovar":
                status[caminho.name] = {"estado": "aprovado", "quando": agora()}
            elif acao == "desfazer":
                status.pop(caminho.name, None)
            elif acao == "descartar":
                self.descartados.mkdir(parents=True, exist_ok=True)
                for origem in (caminho, caminho.with_suffix(".json")):
                    if origem.is_file():
                        destino = self.descartados / origem.name
                        if destino.exists():
                            destino = self.descartados / f"{origem.stem}_{int(time.time())}{origem.suffix}"
                        shutil.move(str(origem), str(destino))
                bruto = self.brutos / caminho.name
                if bruto.is_file():                      # o bruto vai junto (para o corte poder ser recuperado)
                    (self.descartados / "_brutos").mkdir(parents=True, exist_ok=True)
                    destino = self.descartados / "_brutos" / bruto.name
                    if destino.exists():
                        destino = destino.with_name(f"{bruto.stem}_{int(time.time())}{bruto.suffix}")
                    shutil.move(str(bruto), str(destino))
                status.pop(caminho.name, None)
            else:
                return False, "Ação inválida."
            self._gravar_status(status)
        return True, "ok"

    def caminho_bruto(self, nome):
        """Caminho do corte bruto (cortes_prontos/_brutos/NOME), ou None."""
        nome = so_nome_de_arquivo(nome)
        if nome is None:
            return None
        caminho = (self.brutos / nome).resolve()
        if caminho.parent != self.brutos.resolve() or not caminho.is_file():
            return None
        return caminho

    @staticmethod
    def _trechos_limpos(valor):
        """Trechos mantidos da linha do tempo: [[ini, fim], ...] em segundos, em ordem. None = corte inteiro. Levanta ValueError."""
        if valor is None:
            return None
        erro = ValueError("Trechos inválidos: confira os cortes da linha do tempo.")
        if not isinstance(valor, list) or not 1 <= len(valor) <= TRECHOS_MAX:
            raise erro
        saida, ultimo = [], 0.0
        for item in valor:
            if not isinstance(item, list) or len(item) != 2:
                raise erro
            a, b = item
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in (a, b)):
                raise erro
            a, b = float(a), float(b)
            if not (math.isfinite(a) and math.isfinite(b)) or a < ultimo - 1e-6 or b - a < TRECHO_MIN_S:
                raise erro
            saida.append([round(a, 3), round(b, 3)])
            ultimo = b
        if sum(b - a for a, b in saida) < TRECHOS_TOTAL_MIN_S:
            raise ValueError("O corte aparado ficaria curto demais (mínimo de 1 segundo).")
        return saida

    @staticmethod
    def validar_opcoes_edicao(dados):
        """Opções de edição vindas da tela -> dict limpo (o gerar_cortes.py valida de novo). Levanta ValueError."""
        if not isinstance(dados, dict) or dados.get("layout") not in ("inteiro", "empilhado"):
            raise ValueError("Escolha o layout: tela inteira ou câmera + gameplay.")
        opcoes = {"layout": dados["layout"]}
        for chave in ("tarja", "legenda", "resumo", "cta"):
            valor = dados.get(chave, True)
            if not isinstance(valor, bool):
                raise ValueError("Opção inválida.")
            opcoes[chave] = valor
        espelhar = dados.get("espelhar", False)
        if not isinstance(espelhar, bool):
            raise ValueError("Opção inválida.")
        opcoes["espelhar"] = espelhar
        trechos = Estado._trechos_limpos(dados.get("trechos"))
        if trechos:
            opcoes["trechos"] = trechos
        for chave in ("titulo", "pergunta"):                    # texto da tarja escolhido pelo usuário (vazio = mantém o do corte)
            valor = dados.get(chave)
            if valor is None:
                continue
            if not isinstance(valor, str):
                raise ValueError("Título inválido.")
            valor = " ".join("".join(c for c in valor if ord(c) >= 32 and ord(c) != 127).split())[:TITULO_MAX].strip()
            if valor:
                opcoes[chave] = valor
        if opcoes["layout"] == "empilhado":
            limpo = Estado._layout_limpo({"nome": "", "cam": dados.get("cam"), "game": dados.get("game"),
                                          "cam_altura": dados.get("cam_altura", CAM_ALTURA_PADRAO),
                                          "ordem": dados.get("ordem", "cam_primeiro"), "tarja": opcoes["tarja"]})
            if limpo is None:
                raise ValueError("Caixas inválidas: confira a câmera, o gameplay e a altura dos blocos.")
            opcoes.update({"cam": limpo["cam"], "game": limpo["game"], "cam_altura": limpo["cam_altura"], "ordem": limpo["ordem"]})
        return opcoes

    def iniciar_edicao(self, dados):
        """Renderiza de novo um corte a partir do bruto (processo gerar_cortes.py --renderizar). (job, erro)."""
        nome = so_nome_de_arquivo(dados.get("nome"))
        if nome is None or self.caminho_corte(nome) is None:
            raise ValueError("Corte não encontrado.")
        if self.caminho_bruto(nome) is None:
            raise ValueError("Este corte não tem a versão bruta guardada (só cortes gerados no modo bruto podem ser editados).")
        opcoes = self.validar_opcoes_edicao(dados.get("opcoes"))
        if not (self.raiz / "gerar_cortes.py").is_file():
            raise ValueError("gerar_cortes.py não encontrado na pasta do projeto.")
        with self.trava:
            if self.job and self.job.estado == "rodando":
                return None, "Já existe uma execução em andamento."
        self.edicoes.mkdir(parents=True, exist_ok=True)
        arquivo = self.edicoes / f"{Path(nome).stem}.json"
        tmp = arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(opcoes, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(arquivo)
        comando = [self.python, str(self.raiz / "gerar_cortes.py"), "--renderizar", nome]
        params = {"modo": "edicao", "url": nome}
        return self._lancar(comando, {}, params, f"▶ Renderizando de novo: {nome}")

    def sugerir_titulos(self, nome, biblioteca="cortes"):
        """Pede à IA (gerar_cortes.py --sugerir-titulos) alternativas de título para o corte. Levanta ValueError com a mensagem.

        `biblioteca="longos"`: títulos para um vídeo longo (a IA lê os momentos dele).
        """
        longos = biblioteca == "longos"
        nome = so_nome_de_arquivo(nome)
        if nome is None or (self.caminho_longo(nome, bruto=True) if longos else self.caminho_corte(nome)) is None:
            raise ValueError("Vídeo longo não encontrado." if longos else "Corte não encontrado.")
        meta = ler_json(((self.saida_longos if longos else self.saida) / nome).with_suffix(".json"))
        if longos:
            if not meta.get("momentos"):
                raise ValueError("Este vídeo longo não tem os momentos guardados, então não dá para sugerir títulos.")
        elif not str(meta.get("transcricao") or "").strip():
            raise ValueError("Este corte não tem a transcrição guardada, então não dá para sugerir títulos.")
        if not chave_groq_configurada(self.raiz):
            raise ValueError("Falta a chave do Groq: coloque GROQ_API_KEY no arquivo .env para a IA sugerir títulos.")
        if not (self.raiz / "gerar_cortes.py").is_file():
            raise ValueError("gerar_cortes.py não encontrado na pasta do projeto.")
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform.startswith("win") else {}
        try:
            r = subprocess.run([self.python, str(self.raiz / "gerar_cortes.py"), "--sugerir-titulos", nome,
                                "--biblioteca", "longos" if longos else "cortes"], cwd=str(self.raiz),
                               env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=SUGERIR_TIMEOUT_S, **kwargs)
        except subprocess.TimeoutExpired:
            raise ValueError("A IA demorou demais para responder. Tente de novo.")
        except OSError as e:
            raise ValueError(f"Não foi possível consultar a IA: {e}")
        for linha in (r.stdout or "").splitlines():
            if linha.startswith("TITULOS_JSON="):
                try:
                    itens = json.loads(linha[len("TITULOS_JSON="):])
                except ValueError:
                    break
                limpos = []
                for i in itens if isinstance(itens, list) else []:
                    if isinstance(i, dict) and isinstance(i.get("titulo"), str) and i["titulo"].strip():
                        limpos.append({"titulo": i["titulo"].strip()[:LONGO_TITULO_MAX if longos else TITULO_MAX],
                                       "pergunta": str(i.get("pergunta") or "").strip()[:TITULO_MAX]})
                if limpos:
                    return limpos
        motivo = next((l.strip().lstrip("❌ ").strip() for l in reversed((r.stdout or "").splitlines()) if "❌" in l), "")
        raise ValueError(motivo or "A IA não devolveu sugestões agora. Tente de novo em instantes.")

    # ---- vídeos longos (biblioteca própria: videos_longos/, com o mesmo fluxo de edição dos cortes)
    def listar_longos(self):
        if not self.saida_longos.is_dir():
            return []
        with self.trava:
            status = self._status(self.arquivo_status_longos)
        itens = []
        for arq in self.saida_longos.glob("*.json"):
            nome = arq.stem + ".mp4"
            if arq.name.startswith((".", "_")) or so_nome_de_arquivo(nome) is None:
                continue
            bruto = self.brutos_longos / nome
            meta = ler_json(arq)
            if not bruto.is_file() or meta.get("origem") != "longo":
                continue
            visivel = self.saida_longos / nome
            video = visivel if visivel.is_file() else bruto
            info = video.stat()
            edicao = meta.get("edicao") if isinstance(meta.get("edicao"), dict) else {}
            momentos = []
            for m in meta.get("momentos") if isinstance(meta.get("momentos"), list) else []:
                if isinstance(m, dict) and isinstance(m.get("inicio"), (int, float)) and isinstance(m.get("fim"), (int, float)):
                    momentos.append({"n": m.get("n"), "inicio": m["inicio"], "fim": m["fim"],
                                     "inicio_video": m.get("inicio_video"), "titulo": str(m.get("titulo") or "")[:80],
                                     "nota": m.get("nota"), "motivo": str(m.get("motivo") or "")[:80]})
            resumo = meta.get("resumo_intro")
            itens.append({
                "nome": nome, "titulo": str(meta.get("titulo") or "")[:LONGO_TITULO_MAX],
                "estado": (status.get(nome) or {}).get("estado", "novo"),
                "bruto": bool(meta.get("bruto")), "editado": edicao.get("estado") == "renderizado",
                "tem_video_editado": visivel.is_file(),
                "tamanho_mb": round(info.st_size / 1_048_576, 1),
                "modificado": datetime.fromtimestamp(max(info.st_mtime, arq.stat().st_mtime)).isoformat(timespec="seconds"),
                "duracao_s": meta.get("duracao_final_s"), "duracao_bruto_s": meta.get("clip_duration"),
                "alvo_s": meta.get("alvo_s"), "momentos": momentos, "streamer": str(meta.get("streamer") or "")[:60],
                "video_url": str(meta.get("video_url") or "")[:300],
                "resumo": str(resumo.get("texto") or "") if isinstance(resumo, dict) else "",
                "resumo_motivo": str(meta.get("resumo_motivo") or ""),
                "cta": isinstance(meta.get("cta_final"), dict), "cta_motivo": str(meta.get("cta_motivo") or ""),
                "selecao": meta.get("selecao") if isinstance(meta.get("selecao"), dict) else None,
                "transcricao": str(meta.get("transcricao") or "")[:MAX_TRANSCRICAO],
                "edicao": edicao or None, "ordem": max(info.st_mtime, arq.stat().st_mtime),
            })
        itens.sort(key=lambda c: c.pop("ordem"), reverse=True)
        return itens

    def caminho_longo(self, nome, bruto=False):
        """Arquivo de vídeo de um vídeo longo: o editado (se já houve edição) ou o bruto; `bruto=True` só o bruto."""
        nome = so_nome_de_arquivo(nome)
        if nome is None:
            return None
        candidatos = [self.brutos_longos / nome] if bruto else [self.saida_longos / nome, self.brutos_longos / nome]
        for caminho in candidatos:
            caminho = caminho.resolve()
            if caminho.parent in (self.saida_longos.resolve(), self.brutos_longos.resolve()) and caminho.is_file():
                return caminho
        return None

    @staticmethod
    def _mover_sem_sobrescrever(origem, pasta):
        pasta.mkdir(parents=True, exist_ok=True)
        destino = pasta / origem.name
        if destino.exists():
            destino = pasta / f"{origem.stem}_{int(time.time())}{origem.suffix}"
        shutil.move(str(origem), str(destino))

    def acao_longo(self, nome, acao):
        """Aprovar, desfazer a aprovação ou descartar (move para _descartados; nada é apagado)."""
        nome = so_nome_de_arquivo(nome)
        if nome is None or self.caminho_longo(nome, bruto=True) is None:
            return False, "Vídeo longo não encontrado."
        with self.trava:
            status = self._status(self.arquivo_status_longos)
            if acao == "aprovar":
                status[nome] = {"estado": "aprovado", "quando": agora()}
            elif acao == "desfazer":
                status.pop(nome, None)
            elif acao == "descartar":
                for origem in (self.saida_longos / nome, (self.saida_longos / nome).with_suffix(".json")):
                    if origem.is_file():
                        self._mover_sem_sobrescrever(origem, self.descartados_longos)
                self._mover_sem_sobrescrever(self.brutos_longos / nome, self.descartados_longos / "_brutos")
                status.pop(nome, None)
            else:
                return False, "Ação inválida."
            self._gravar_status(status, self.arquivo_status_longos)
        return True, "ok"

    @staticmethod
    def validar_opcoes_longo(dados):
        """Opções de edição de um vídeo longo vindas da tela -> dict limpo (o gerar_cortes.py valida de novo)."""
        if not isinstance(dados, dict):
            raise ValueError("Opções inválidas.")
        opcoes = {}
        for chave in ("legenda", "capitulos", "resumo", "cta"):
            valor = dados.get(chave, True)
            if not isinstance(valor, bool):
                raise ValueError("Opção inválida.")
            opcoes[chave] = valor
        espelhar = dados.get("espelhar", False)
        if not isinstance(espelhar, bool):
            raise ValueError("Opção inválida.")
        opcoes["espelhar"] = espelhar
        if dados.get("recorte") is not None:                    # recorte livre do quadro (qualquer formato)
            recorte = caixa_valida(dados["recorte"])
            if recorte is None or recorte["w"] < LONGO_RECORTE_MIN or recorte["h"] < LONGO_RECORTE_MIN:
                raise ValueError("Recorte inválido: a caixa precisa ficar dentro do quadro e ter pelo menos 5% de cada lado.")
            opcoes["recorte"] = recorte
        trechos = Estado._trechos_limpos(dados.get("trechos"))
        if trechos:
            opcoes["trechos"] = trechos
        titulo = dados.get("titulo")
        if titulo is not None:
            if not isinstance(titulo, str):
                raise ValueError("Título inválido.")
            titulo = " ".join("".join(c for c in titulo if ord(c) >= 32 and ord(c) != 127).split())[:LONGO_TITULO_MAX].strip()
            if titulo:
                opcoes["titulo"] = titulo
        return opcoes

    def iniciar_edicao_longo(self, dados):
        """Renderiza de novo um vídeo longo a partir do bruto (gerar_cortes.py --renderizar --biblioteca longos). (job, erro)."""
        nome = so_nome_de_arquivo(dados.get("nome"))
        if nome is None or self.caminho_longo(nome, bruto=True) is None:
            raise ValueError("Vídeo longo não encontrado.")
        if ler_json((self.saida_longos / nome).with_suffix(".json")).get("origem") != "longo":
            raise ValueError("Os dados deste vídeo longo não foram encontrados (.json).")
        opcoes = self.validar_opcoes_longo(dados.get("opcoes"))
        if not (self.raiz / "gerar_cortes.py").is_file():
            raise ValueError("gerar_cortes.py não encontrado na pasta do projeto.")
        with self.trava:
            if self.job and self.job.estado == "rodando":
                return None, "Já existe uma execução em andamento."
        self.edicoes_longos.mkdir(parents=True, exist_ok=True)
        arquivo = self.edicoes_longos / f"{Path(nome).stem}.json"
        tmp = arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(opcoes, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(arquivo)
        comando = [self.python, str(self.raiz / "gerar_cortes.py"), "--renderizar", nome, "--biblioteca", "longos"]
        return self._lancar(comando, {}, {"modo": "edicao", "url": nome}, f"▶ Renderizando de novo o vídeo longo: {nome}")

    def iniciar_longo_da_live(self, dados):
        """Junta num vídeo longo os melhores cortes que o monitor já fez de um canal da lista. Levanta ValueError; devolve (job, erro)."""
        canal = next((c for c in self.canais.listar() if c["id"] == str(dados.get("id") or "")), None)
        if canal is None:
            raise ValueError("Esse canal não está na lista de canais permitidos.")
        if canal.get("situacao") != "ativo":
            raise ValueError("Esse canal está suspenso: a permissão para usar os cortes dele acabou. Reative-o na lista.")
        minutos = dados.get("minutos")
        if isinstance(minutos, bool) or not isinstance(minutos, int) or not LONGO_MIN_MIN <= minutos <= LONGO_MAX_MIN:
            raise ValueError(f"O vídeo longo deve ter de {LONGO_MIN_MIN} a {LONGO_MAX_MIN} minutos.")
        horas = dados.get("horas")
        if horas is not None and (isinstance(horas, bool) or not isinstance(horas, int) or not 1 <= horas <= 24 * 30):
            raise ValueError("Período inválido: use de 1 hora a 30 dias, ou todos os cortes.")
        if not (self.raiz / "gerar_cortes.py").is_file():
            raise ValueError("gerar_cortes.py não encontrado na pasta do projeto.")
        comando = [self.python, "-u", str(self.raiz / "gerar_cortes.py"), "--longo-da-live", f"{canal['plataforma']}:{canal['canal']}",
                   "--video-longo", str(minutos)]
        if horas:
            comando += ["--live-horas", str(horas)]
        params = {"modo": "longo_live", "url": canal.get("url_live") or canal["canal"], "video_longo": minutos}
        return self._lancar(comando, {}, params, f"▶ Montando o vídeo longo ({minutos} min) com os cortes de {canal.get('nome') or canal['canal']}")

    def caminho_ffmpeg(self):
        """ffmpeg para o que o servidor mesmo precisa fazer (a onda do áudio): bin/ffmpeg.exe ou o do PATH; None se não houver."""
        local = self.raiz / "bin" / "ffmpeg.exe"
        return str(local) if local.is_file() else shutil.which("ffmpeg")

    def onda_longo(self, nome):
        """Picos (0 a 1) do áudio do bruto, para a linha do tempo desenhar a onda sem baixar o arquivo inteiro no navegador."""
        import array
        bruto = self.caminho_longo(nome, bruto=True)
        if bruto is None:
            raise ValueError("Vídeo longo não encontrado.")
        estado = bruto.stat()
        cache = self.cache_longos / f"{bruto.stem}.onda.json"
        guardado = ler_json(cache)
        if guardado.get("tamanho") == estado.st_size and guardado.get("mtime") == int(estado.st_mtime) \
                and isinstance(guardado.get("picos"), list) and guardado["picos"]:
            return guardado["picos"]
        ffmpeg = self.caminho_ffmpeg()
        if not ffmpeg:
            raise ValueError("ffmpeg não encontrado: não dá para desenhar a onda do áudio (o vídeo continua editável).")
        kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform.startswith("win") else {}
        try:
            r = subprocess.run([ffmpeg, "-v", "error", "-i", str(bruto), "-vn", "-ac", "1", "-ar", "2000", "-f", "s16le", "-"],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=ONDA_TIMEOUT_S, **kwargs)
        except subprocess.TimeoutExpired:
            raise ValueError("Ler o áudio demorou demais.")
        except OSError as e:
            raise ValueError(f"Não foi possível ler o áudio: {e}")
        amostras = array.array("h")
        amostras.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
        if sys.byteorder == "big":
            amostras.byteswap()
        if r.returncode != 0 or not len(amostras):
            raise ValueError("Não foi possível ler o áudio deste vídeo.")
        por = max(1, len(amostras) // ONDA_PONTOS)
        brutos = []
        for i in range(ONDA_PONTOS):
            parte = amostras[i * por:(i + 1) * por]
            brutos.append(max(map(abs, parte)) if len(parte) else 0)
        maximo = max(brutos) or 1
        picos = [round(math.sqrt(p / maximo), 3) for p in brutos]              # raiz: mostra melhor o que é baixo
        try:
            self.cache_longos.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps({"tamanho": estado.st_size, "mtime": int(estado.st_mtime), "picos": picos}), encoding="utf-8")
            tmp.replace(cache)
        except OSError:
            pass                                                                # sem cache só recalcula na próxima
        return picos

    # ---- layouts salvos (editor de layout)
    def listar_layouts(self):
        itens = []
        if self.layouts.is_dir():
            for arq in sorted(self.layouts.glob("*.json")):
                if not RE_SLUG.match(arq.stem):
                    continue
                dados = ler_json(arq)
                limpo = self._layout_limpo(dados)
                if limpo:
                    limpo["slug"] = arq.stem
                    itens.append(limpo)
        return itens

    @staticmethod
    def _layout_limpo(dados):
        cam, game = caixa_valida(dados.get("cam")), caixa_valida(dados.get("game"))
        if cam is None or game is None:
            return None
        try:
            altura = int(dados.get("cam_altura", CAM_ALTURA_PADRAO))
        except (TypeError, ValueError):
            return None
        ordem = dados.get("ordem", "cam_primeiro")
        if not CAM_ALTURA_MIN <= altura <= CAM_ALTURA_MAX or ordem not in ORDENS:
            return None
        tarja = dados.get("tarja", True)
        if not isinstance(tarja, bool):
            return None
        nome = re.sub(r"\s+", " ", str(dados.get("nome") or "")).strip()
        return {"nome": nome[:60], "cam": cam, "game": game, "cam_altura": altura, "ordem": ordem, "tarja": tarja}

    def salvar_layout(self, dados):
        """Valida e grava layouts_salvos/<slug>.json. Devolve o layout salvo (com o slug). Levanta ValueError."""
        nome = re.sub(r"\s+", " ", str(dados.get("nome") or "")).strip()
        if not nome or len(nome) > 60 or any(ord(c) < 32 for c in nome):
            raise ValueError("Dê um nome ao layout (até 60 caracteres).")
        slug = slug_do_nome(nome)
        if not slug:
            raise ValueError("O nome precisa ter pelo menos uma letra ou número.")
        limpo = self._layout_limpo({**dados, "nome": nome})
        if limpo is None:
            raise ValueError("Layout inválido: confira as caixas da câmera e do gameplay e a altura.")
        self.layouts.mkdir(parents=True, exist_ok=True)
        tmp = self.layouts / f"{slug}.tmp"
        tmp.write_text(json.dumps(limpo, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.layouts / f"{slug}.json")
        limpo["slug"] = slug
        return limpo

    def apagar_layout(self, slug):
        if not isinstance(slug, str) or not RE_SLUG.match(slug):
            return False
        caminho = self.layouts / f"{slug}.json"
        if not caminho.is_file():
            return False
        caminho.unlink()
        return True

    # ---- canais permitidos (monitor de lives)
    def salvar_canal(self, dados):
        """Cadastra/atualiza um canal permitido. Levanta ValueError com a mensagem para a tela."""
        layout = str((dados or {}).get("layout") or "").strip() if isinstance(dados, dict) else ""
        if layout and not self.layout_existe(layout):
            raise ValueError("O layout escolhido não existe mais. Escolha outro ou deixe em branco.")
        return self.canais.salvar(dados)

    def layout_existe(self, slug):
        return isinstance(slug, str) and bool(RE_SLUG.match(slug)) and (self.layouts / f"{slug}.json").is_file()

    # ---- pré-requisitos (só informativo)
    def prerequisitos(self):
        ffmpeg_local = self.raiz / "bin" / "ffmpeg.exe"
        return {
            "script": (self.raiz / "gerar_cortes.py").is_file(),
            "ffmpeg": ffmpeg_local.is_file() or bool(shutil.which("ffmpeg")),
            "chave_groq": chave_groq_configurada(self.raiz),
            "js_runtime": any(shutil.which(n) for n in ("deno", "node", "bun")),
            "python": sys.version.split()[0],
        }

    # ---- execução
    def montar_comando(self, dados):
        """Valida o pedido da tela e monta a lista de argumentos (sem shell). Levanta ValueError."""
        url = re.sub(r"^(?:https?://)+(?=https?://)", "", str(dados.get("url", "")).strip(), flags=re.IGNORECASE)   # https://https://...
        if not RE_URL.match(url) or len(url) > 2000 or url.startswith("-"):
            raise ValueError("Cole um link que comece com http:// ou https://")
        modo = dados.get("modo")
        if modo not in MODOS:
            raise ValueError("Modo inválido.")
        comando = [self.python, "-u", str(self.raiz / "gerar_cortes.py"), url, "--audio", modo]
        params = {"url": url, "modo": modo}
        if modo == "gameplay":
            longo = dados.get("video_longo")                       # minutos do vídeo longo; ausente/None/False = sem vídeo longo
            if longo in (None, False, ""):
                longo = None
            else:
                try:
                    if longo is True or isinstance(longo, float) and longo != int(longo):
                        raise ValueError
                    longo = int(longo)
                except (TypeError, ValueError, OverflowError):
                    raise ValueError("Duração do vídeo longo inválida.")
                if not LONGO_MIN_MIN <= longo <= LONGO_MAX_MIN:
                    raise ValueError(f"O vídeo longo deve ter de {LONGO_MIN_MIN} a {LONGO_MAX_MIN} minutos.")
            bruto = dados.get("cortes")
            try:
                if isinstance(bruto, bool):
                    raise ValueError
                cortes = 5 if bruto in (None, "") else int(bruto)
            except (TypeError, ValueError):
                raise ValueError("Número de cortes inválido.")
            minimo = 0 if longo else 1                             # 0 cortes só vale quando há vídeo longo
            if not minimo <= cortes <= 10:
                raise ValueError(f"O número de cortes deve ser de {minimo} a 10.")
            comando += ["--cortes", str(cortes)]
            params["cortes"] = cortes
            if longo:
                comando += ["--video-longo", str(longo)]
                params["video_longo"] = longo
            webcam = str(dados.get("webcam") or "").strip()
            if webcam:
                if webcam not in {"1", "2", "3", "4", "5", "6", "7"}:
                    raise ValueError("Posição da webcam inválida.")
                comando += ["--webcam", webcam]
                params["webcam"] = webcam
        for chave, flag in (("repetir", "--repetir"), ("importar_existentes", "--importar-existentes")):
            valor = dados.get(chave)
            if valor is not None and not isinstance(valor, bool):      # 1 == True em Python: exige bool de verdade
                raise ValueError("Opção inválida.")
            if valor is True:
                comando.append(flag)
                params[chave] = True
        if modo == "gameplay":
            resumo = dados.get("resumo")                       # ligado por padrão; só False desliga
            if resumo is not None and not isinstance(resumo, bool):
                raise ValueError("Opção inválida.")
            if resumo is False:
                comando.append("--sem-resumo")
                params["resumo"] = False
            cta = dados.get("cta")                             # ligada por padrão; só False desliga
            if cta is not None and not isinstance(cta, bool):
                raise ValueError("Opção inválida.")
            if cta is False:
                comando.append("--sem-cta")
                params["cta"] = False
            camera = dados.get("camera")
            if camera is not None and not isinstance(camera, str):
                raise ValueError("Câmera manual inválida.")
            camera = re.sub(r"\s+", "", (camera or ""))
            if camera:
                partes = camera.replace(";", ",").split(",")
                try:
                    x, y, larg, alt = (float(p) for p in partes)
                    valida = (len(partes) == 4 and 0 <= x < 1 and 0 <= y < 1 and 0 < larg <= 1 and 0 < alt <= 1
                              and x + larg <= 1.0001 and y + alt <= 1.0001)
                except ValueError:
                    valida = False
                if not valida:
                    raise ValueError("Câmera manual inválida: use X,Y,LARGURA,ALTURA entre 0 e 1 (ex.: 0.78,0.37,0.21,0.22).")
                comando.append(f"--camera={','.join(partes)}")
                params["camera"] = ",".join(partes)
            streamer = dados.get("streamer")
            if streamer is not None and not isinstance(streamer, str):
                raise ValueError("Nome do streamer inválido.")
            streamer = re.sub(r"\s+", " ", (streamer or "")).strip()
            if streamer:
                if len(streamer) > 60 or any(ord(c) < 32 for c in streamer):
                    raise ValueError("Nome do streamer inválido (até 60 caracteres, só texto).")
                comando.append(f"--streamer={streamer}")        # com "=": nunca vira outra opção
                params["streamer"] = streamer
        env = {}
        layout = str(dados.get("layout") or "").strip().lower()
        if layout:
            if layout not in LAYOUTS:
                raise ValueError("Layout inválido.")
            env["CORTES_LAYOUT"] = layout
            params["layout"] = layout
        fundo = str(dados.get("fundo") or "").strip().lower()
        if fundo:
            fundo = {"preto": "black", "desfocado": "blur"}.get(fundo, fundo)
            if fundo not in FUNDOS:
                raise ValueError("Fundo inválido.")
            env["CORTES_FUNDO"] = fundo
            params["fundo"] = fundo
        return comando, env, params

    def iniciar(self, dados):
        """Inicia uma execução. Retorna (job, erro). Só uma por vez."""
        comando, extra_env, params = self.montar_comando(dados)
        if not (self.raiz / "gerar_cortes.py").is_file():
            raise ValueError("gerar_cortes.py não encontrado na pasta do projeto.")
        return self._lancar(comando, extra_env, params, f"▶ Iniciando: modo {MODOS[params['modo']]} | {params['url']}")

    def _lancar(self, comando, extra_env, params, linha_inicial):
        """Sobe o processo (um por vez) e começa a guardar o log. Retorna (job, erro)."""
        with self.trava:
            if self.job and self.job.estado == "rodando":
                return None, "Já existe uma execução em andamento."
            env = os.environ.copy()
            env.update(extra_env)
            env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
            bin_dir = self.raiz / "bin"
            if bin_dir.is_dir():
                env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
            job = Job(params, comando)
            kwargs = {}
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                           | getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                kwargs["start_new_session"] = True
            try:
                job.proc = subprocess.Popen(
                    comando, cwd=str(self.raiz), env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1, **kwargs)
            except OSError as e:
                raise ValueError(f"Não foi possível iniciar o processo: {e}")
            try:
                self.logs.mkdir(parents=True, exist_ok=True)
                job.arquivo_log = open(self.logs / f"execucao_{job.id}.log", "w", encoding="utf-8")
            except OSError:
                job.arquivo_log = None
            self.job = job
        job.adicionar(linha_inicial)
        threading.Thread(target=self._acompanhar, args=(job,), daemon=True).start()
        return job, None

    def _acompanhar(self, job):
        try:
            for linha in job.proc.stdout:
                job.adicionar(linha.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        job.codigo = job.proc.wait()
        job.fim = time.time()
        job.estado = "cancelado" if job.cancelado else ("concluido" if job.codigo == 0 else "erro")
        job.adicionar(f"■ Execução {job.estado} (código {job.codigo}) em {round(job.fim - job.inicio)} s")
        try:
            job.proc.stdout.close()
        except OSError:
            pass
        if job.arquivo_log:
            try:
                job.arquivo_log.close()
            except OSError:
                pass

    def parar(self):
        job = self.job
        if not job or job.estado != "rodando" or job.proc is None:
            return False
        job.cancelado = True
        matar_arvore(job.proc)
        return True


def matar_arvore(proc):
    """Encerra o processo e tudo que ele iniciou (ffmpeg, yt-dlp...)."""
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            threading.Timer(5.0, lambda: _kill_se_vivo(proc)).start()
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def _kill_se_vivo(proc):
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass


# ---------------------------------------------------------------- servidor HTTP
class Manipulador(BaseHTTPRequestHandler):
    server_version = "FabricaCortes/0.1"
    protocol_version = "HTTP/1.1"

    @property
    def estado(self):
        return self.server.estado

    def log_message(self, formato, *args):           # silencia o log padrão do http.server
        pass

    # ---- respostas
    def _enviar(self, codigo, corpo=b"", tipo="application/json; charset=utf-8", extras=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for chave, valor in (extras or {}).items():
            self.send_header(chave, valor)
        self.end_headers()
        if self.command != "HEAD" and corpo:
            self.wfile.write(corpo)

    def _json(self, dados, codigo=200):
        self._enviar(codigo, json.dumps(dados, ensure_ascii=False).encode("utf-8"))

    def _erro(self, codigo, mensagem):
        self._json({"erro": mensagem}, codigo)

    # ---- segurança: só este computador, e só quem abriu a página (cookie com token)
    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _autorizado(self):
        if not self._host_ok():
            return False
        cookies = {}
        for parte in (self.headers.get("Cookie") or "").split(";"):
            if "=" in parte:
                chave, valor = parte.strip().split("=", 1)
                cookies[chave] = valor
        return hmac.compare_digest(cookies.get(COOKIE, ""), self.estado.token)

    def _origem_ok(self):
        """Em POST, se o navegador mandou Origin, ele tem que ser esta própria página."""
        origem = self.headers.get("Origin")
        if not origem:
            return True
        return urlparse(origem).netloc.lower() == (self.headers.get("Host") or "").lower()

    def _corpo_json(self):
        try:
            tamanho = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if tamanho < 0 or tamanho > MAX_CORPO:
            return None
        try:
            dados = json.loads(self.rfile.read(tamanho).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return None
        return dados if isinstance(dados, dict) else None

    # ---- roteamento
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        rota = urlparse(self.path)
        caminho, consulta = rota.path, parse_qs(rota.query)
        if not self._host_ok():
            return self._erro(403, "Acesso negado.")
        if caminho in ("/", "/index.html"):
            return self._pagina_inicial()
        if caminho.startswith("/static/"):
            return self._estatico(caminho[len("/static/"):])
        if not self._autorizado():
            return self._erro(403, "Abra a interface pelo endereço mostrado no terminal.")
        if caminho == "/api/estado":
            return self._json(self._estado_publico())
        if caminho == "/api/cortes":
            return self._json({"cortes": self.estado.listar_cortes()})
        if caminho == "/api/layouts":
            return self._json({"layouts": self.estado.listar_layouts()})
        if caminho == "/api/longos":
            return self._json({"longos": self.estado.listar_longos()})
        if caminho == "/api/longos/onda":
            try:
                return self._json({"picos": self.estado.onda_longo((consulta.get("nome") or [""])[0])})
            except ValueError as e:
                return self._erro(400, str(e))
        if caminho == "/api/canais":
            return self._json({"canais": self.estado.canais.listar(), "plataformas": list(PLATAFORMAS), "maximo": modulo_canais.MAX_CANAIS,
                               "layouts": [{"slug": l["slug"], "nome": l["nome"]} for l in self.estado.listar_layouts()]})
        if caminho == "/api/lives":
            return self._json(self.estado.lives.listar())
        if caminho == "/api/lives/log":
            try:
                indice = int((consulta.get("desde") or ["0"])[0])
            except ValueError:
                indice = 0
            linhas, proximo = self.estado.lives.log((consulta.get("id") or [""])[0], indice)
            return self._json({"linhas": linhas, "proximo": proximo})
        if caminho == "/api/log":
            return self._log(consulta)
        if caminho == "/video":
            return self._video((consulta.get("nome") or [""])[0], bruto=(consulta.get("bruto") or [""])[0] == "1",
                               longo=(consulta.get("lib") or [""])[0] == "longos")
        return self._erro(404, "Não encontrado.")

    def do_POST(self):
        caminho = urlparse(self.path).path
        if not self._host_ok() or not self._autorizado() or not self._origem_ok():
            return self._erro(403, "Acesso negado.")
        if self.headers.get("X-Requested-With") != "fabrica":
            return self._erro(403, "Cabeçalho obrigatório ausente.")
        dados = self._corpo_json()
        if dados is None:
            return self._erro(400, "Pedido inválido.")
        if caminho == "/api/layouts":
            try:
                return self._json({"layout": self.estado.salvar_layout(dados)}, 201)
            except ValueError as e:
                return self._erro(400, str(e))
        if caminho == "/api/canais":
            try:
                return self._json({"canal": self.estado.salvar_canal(dados)}, 201)
            except ValueError as e:
                return self._erro(400, str(e))
        if caminho in ("/api/cortes/titulos", "/api/longos/titulos"):
            try:
                return self._json({"titulos": self.estado.sugerir_titulos(
                    dados.get("nome"), "longos" if caminho.startswith("/api/longos") else "cortes")})
            except ValueError as e:
                return self._erro(400, str(e))
        if caminho == "/api/canais/ativo":
            try:
                canal = self.estado.canais.definir_ativo(str(dados.get("id") or ""), dados.get("ativo"))
            except ValueError as e:
                return self._erro(400, str(e))
            if canal and dados.get("ativo") is False:
                self.estado.lives.parar(canal["id"])              # canal suspenso: o monitor para de assistir já
            return self._json({"canal": canal}) if canal else self._erro(404, "Canal não encontrado.")
        if caminho == "/api/canais/apagar":
            apagou = self.estado.canais.apagar(str(dados.get("id") or ""))
            if apagou:
                self.estado.lives.parar(str(dados.get("id") or ""))   # canal apagado: idem
            return self._json({"ok": True}) if apagou else self._erro(404, "Canal não encontrado.")
        if caminho == "/api/canais/verificar":
            return self._json(self.estado.canais.verificar(dados.get("url"), dados.get("plataforma")))
        if caminho == "/api/lives/iniciar":
            try:
                return self._json({"live": self.estado.lives.iniciar(dados.get("id"))}, 201)
            except ValueError as e:
                return self._erro(409, str(e))
        if caminho == "/api/lives/longo":
            try:
                job, erro = self.estado.iniciar_longo_da_live(dados)
            except ValueError as e:
                return self._erro(400, str(e))
            if erro:
                return self._erro(409, erro)
            return self._json({"job": job.publico()}, 201)
        if caminho == "/api/lives/parar":
            return self._json({"parando": True}) if self.estado.lives.parar(dados.get("id")) else self._erro(404, "Esse canal não está sendo monitorado agora.")
        if caminho == "/api/lives/pausar":
            if not isinstance(dados.get("pausar"), bool):
                return self._erro(400, "Opção inválida.")
            try:
                return self._json({"ok": self.estado.lives.pausar(dados.get("id"), dados["pausar"])})
            except ValueError as e:
                return self._erro(409, str(e))
        if caminho == "/api/layouts/apagar":
            return self._json({"ok": True}) if self.estado.apagar_layout(dados.get("slug")) else self._erro(404, "Layout não encontrado.")
        if caminho in ("/api/cortes/editar", "/api/longos/editar"):
            try:
                job, erro = (self.estado.iniciar_edicao_longo if caminho.startswith("/api/longos") else self.estado.iniciar_edicao)(dados)
            except ValueError as e:
                return self._erro(400, str(e))
            if erro:
                return self._erro(409, erro)
            return self._json({"job": job.publico()}, 201)
        if caminho == "/api/executar":
            try:
                job, erro = self.estado.iniciar(dados)
            except ValueError as e:
                return self._erro(400, str(e))
            if erro:
                return self._erro(409, erro)
            return self._json({"job": job.publico()}, 201)
        if caminho == "/api/parar":
            return self._json({"parando": self.estado.parar()})
        if caminho == "/api/longos/acao":
            ok, msg = self.estado.acao_longo(dados.get("nome"), dados.get("acao"))
            return self._json({"ok": True}) if ok else self._erro(400, msg)
        if caminho == "/api/cortes/acao":
            ok, msg = self.estado.acao_corte(dados.get("nome"), dados.get("acao"))
            return self._json({"ok": True}) if ok else self._erro(400, msg)
        if caminho == "/api/pasta":
            pasta = self.estado.saida_longos if dados.get("pasta") == "longos" else self.estado.saida
            pasta.mkdir(parents=True, exist_ok=True)
            try:
                abrir_pasta(pasta)
            except OSError as e:
                return self._erro(500, f"Não foi possível abrir a pasta: {e}")
            return self._json({"ok": True})
        return self._erro(404, "Não encontrado.")

    # ---- páginas e arquivos
    def _pagina_inicial(self):
        arquivo = STATIC_DIR / "index.html"
        try:
            corpo = arquivo.read_bytes()
        except OSError:
            return self._erro(500, "index.html não encontrado.")
        cookie = f"{COOKIE}={self.estado.token}; HttpOnly; SameSite=Strict; Path=/"
        self._enviar(200, corpo, "text/html; charset=utf-8", {"Set-Cookie": cookie})

    def _estatico(self, nome):
        if "/" in nome or "\\" in nome or nome.startswith("."):
            return self._erro(404, "Não encontrado.")
        arquivo = STATIC_DIR / nome
        if not arquivo.is_file():
            return self._erro(404, "Não encontrado.")
        tipo = mimetypes.guess_type(nome)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo in ("application/javascript", "application/json"):
            tipo += "; charset=utf-8"
        self._enviar(200, arquivo.read_bytes(), tipo)

    def _estado_publico(self):
        job = self.estado.job
        return {
            "raiz": str(self.estado.raiz),
            "saida": str(self.estado.saida),
            "modos": MODOS,
            "prerequisitos": self.estado.prerequisitos(),
            "recursos": RECURSOS,
            "job": job.publico() if job else None,
        }

    def _log(self, consulta):
        try:
            indice = int((consulta.get("desde") or ["0"])[0])
        except ValueError:
            indice = 0
        job = self.estado.job
        if not job:
            return self._json({"linhas": [], "proximo": 0, "job": None})
        linhas, proximo = job.desde(indice)
        self._json({"linhas": linhas, "proximo": proximo, "job": job.publico()})

    def _video(self, nome, bruto=False, longo=False):
        if longo:
            caminho = self.estado.caminho_longo(nome, bruto=bruto)
        else:
            caminho = self.estado.caminho_bruto(nome) if bruto else self.estado.caminho_corte(nome)
        if caminho is None:
            return self._erro(404, "Vídeo não encontrado.")
        tamanho = caminho.stat().st_size
        inicio, fim, codigo = 0, tamanho - 1, 200
        faixa = self.headers.get("Range")
        if faixa:
            m = re.match(r"^bytes=(\d*)-(\d*)$", faixa.strip())
            if not m or (not m.group(1) and not m.group(2)):
                return self._enviar(416, b"", "text/plain", {"Content-Range": f"bytes */{tamanho}"})
            if m.group(1):
                inicio = int(m.group(1))
                fim = int(m.group(2)) if m.group(2) else tamanho - 1
            else:                                   # "bytes=-N": os últimos N bytes
                inicio = max(tamanho - int(m.group(2)), 0)
            fim = min(fim, tamanho - 1)
            if inicio > fim or inicio >= tamanho:
                return self._enviar(416, b"", "text/plain", {"Content-Range": f"bytes */{tamanho}"})
            codigo = 206
        restante = fim - inicio + 1
        self.send_response(codigo)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(restante))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if codigo == 206:
            self.send_header("Content-Range", f"bytes {inicio}-{fim}/{tamanho}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(caminho, "rb") as f:
                f.seek(inicio)
                while restante > 0:
                    bloco = f.read(min(262144, restante))
                    if not bloco:
                        break
                    self.wfile.write(bloco)
                    restante -= len(bloco)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                      # o navegador fechou a conexão (comum ao pular no vídeo)


class Servidor(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, endereco, estado):
        super().__init__(endereco, Manipulador)
        self.estado = estado

    def handle_error(self, request, client_address):
        # O navegador fecha conexões o tempo todo (ex.: ao pular no vídeo); isso não é erro.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def criar_servidor(raiz, porta=PORTA_PADRAO, python=None):
    """Cria o servidor (porta 0 = qualquer porta livre). Usado pelo main e pelos testes."""
    estado = Estado(raiz, python=python)
    return Servidor(("127.0.0.1", porta), estado)


def main():
    parser = argparse.ArgumentParser(description="Interface local da Fábrica de Cortes")
    parser.add_argument("--porta", type=int, default=PORTA_PADRAO)
    parser.add_argument("--sem-navegador", action="store_true", help="não abre o navegador sozinho")
    parser.add_argument("--raiz", default=str(APP_DIR.parent), help="pasta do projeto (onde está o gerar_cortes.py)")
    args = parser.parse_args()

    try:
        servidor = criar_servidor(args.raiz, args.porta)
    except OSError as e:
        print(f"❌ Não foi possível abrir a porta {args.porta}: {e}")
        print("   Se a interface já estiver aberta, use o endereço dela. Ou tente: --porta 8766")
        sys.exit(1)

    estado = servidor.estado
    atexit.register(estado.parar)
    atexit.register(estado.lives.parar_todas)
    url = f"http://127.0.0.1:{servidor.server_address[1]}/"
    print("=" * 60)
    print("  FÁBRICA DE CORTES - INTERFACE LOCAL")
    print(f"  Endereço: {url}")
    print(f"  Projeto : {estado.raiz}")
    print("  Deixe esta janela aberta. Para encerrar: Ctrl+C.")
    print("=" * 60)
    if not args.sem_navegador:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando...")
    finally:
        estado.parar()
        estado.lives.parar_todas()
        servidor.server_close()


if __name__ == "__main__":
    main()
