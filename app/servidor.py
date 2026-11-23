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
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
PORTA_PADRAO = 8765

MODOS = {"ia": "Narração por IA", "gameplay": "Gameplay / Streamer", "original": "Áudio original"}
LAYOUTS = {"cam", "fit", "clipflow"}
FUNDOS = {"blur", "black"}
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
        self.arquivo_status = self.saida / "_status.json"
        self.logs = APP_DIR / "logs"
        self.python = python or sys.executable
        self.token = secrets.token_urlsafe(32)
        self.job = None
        self.trava = threading.Lock()          # protege self.job e o arquivo de status

    # ---- cortes
    def _status(self):
        return ler_json(self.arquivo_status)

    def _gravar_status(self, dados):
        self.saida.mkdir(parents=True, exist_ok=True)
        tmp = self.arquivo_status.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.arquivo_status)

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
                status.pop(caminho.name, None)
            else:
                return False, "Ação inválida."
            self._gravar_status(status)
        return True, "ok"

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
        url = str(dados.get("url", "")).strip()
        if not RE_URL.match(url) or len(url) > 2000 or url.startswith("-"):
            raise ValueError("Cole um link que comece com http:// ou https://")
        modo = dados.get("modo")
        if modo not in MODOS:
            raise ValueError("Modo inválido.")
        comando = [self.python, "-u", str(self.raiz / "gerar_cortes.py"), url, "--audio", modo]
        params = {"url": url, "modo": modo}
        if modo == "gameplay":
            bruto = dados.get("cortes")
            try:
                if isinstance(bruto, bool):
                    raise ValueError
                cortes = 5 if bruto in (None, "") else int(bruto)
            except (TypeError, ValueError):
                raise ValueError("Número de cortes inválido.")
            if not 1 <= cortes <= 10:
                raise ValueError("O número de cortes deve ser de 1 a 10.")
            comando += ["--cortes", str(cortes)]
            params["cortes"] = cortes
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
        job.adicionar(f"▶ Iniciando: modo {MODOS[params['modo']]} | {params['url']}")
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
        if caminho == "/api/log":
            return self._log(consulta)
        if caminho == "/video":
            return self._video((consulta.get("nome") or [""])[0])
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
        if caminho == "/api/cortes/acao":
            ok, msg = self.estado.acao_corte(dados.get("nome"), dados.get("acao"))
            return self._json({"ok": True}) if ok else self._erro(400, msg)
        if caminho == "/api/pasta":
            self.estado.saida.mkdir(parents=True, exist_ok=True)
            try:
                abrir_pasta(self.estado.saida)
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

    def _video(self, nome):
        caminho = self.estado.caminho_corte(nome)
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
        servidor.server_close()


if __name__ == "__main__":
    main()
