"""Gerenciador das lives monitoradas: sobe, acompanha e para um processo `python -m monitor` por canal (só biblioteca padrão).

- No máximo MAX_SIMULTANEAS lives ao mesmo tempo (um processo por live).
- Só inicia canal que está ATIVO na lista de canais permitidos (o próprio monitor confere de novo ao subir e a cada minuto).
- O estado que a tela mostra vem de cortes_prontos/_lives/<id>/status.json (escrito pelo monitor) mais o estado do processo.
- Controle: parar = arquivo parar.flag (o monitor encerra com calma); se não sair a tempo, encerra a árvore de processos.
  Pausar = arquivo pausar.flag (continua gravando e aprendendo, mas não gera momentos).
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

MAX_SIMULTANEAS = 2
MAX_LINHAS_LOG = 400
TEMPO_PARADA_S = 25.0            # depois do pedido de parada, quanto esperar antes de encerrar o processo à força
STATUS_VELHO_S = 30.0            # status.json sem atualização há mais que isso: o monitor travou ou morreu
MOMENTOS_NA_TELA = 8


def pasta_id(identificador_canal):
    """'twitch:alanzoka' -> 'twitch-alanzoka' (nome da pasta de estado; o mesmo que o monitor usa por padrão)."""
    return re.sub(r"[^a-z0-9_-]+", "-", str(identificador_canal).lower()).strip("-")[:60] or "live"


def _ler_json(caminho):
    try:
        return json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class Execucao:
    """Um processo de monitor em andamento (ou terminado há pouco), com o log em memória e em arquivo."""

    def __init__(self, canal_id, comando):
        self.canal_id = canal_id
        self.comando = comando
        self.inicio = time.time()
        self.fim = None
        self.codigo = None
        self.pedido_parada = None          # instante em que a parada foi pedida
        self.proc = None
        self.linhas = []
        self.base = 0
        self.trava = threading.Lock()
        self.arquivo_log = None

    def adicionar(self, linha):
        with self.trava:
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
        with self.trava:
            indice = max(indice, self.base)
            return self.linhas[indice - self.base:], self.base + len(self.linhas)

    @property
    def rodando(self):
        return self.fim is None


class GerenteLives:
    def __init__(self, raiz, registro, python=None, matar_arvore=None, comando_monitor=None):
        self.raiz = Path(raiz)
        self.registro = registro
        self.python = python or sys.executable
        self.pasta_lives = self.raiz / "cortes_prontos" / "_lives"
        self.pasta_logs = Path(__file__).resolve().parent / "logs"
        self.matar_arvore = matar_arvore or _matar_arvore
        self.comando_monitor = comando_monitor           # troca do comando (testes); recebe (url_canal, pasta_id)
        self.execucoes = {}                              # canal_id -> Execucao (a mais recente)
        self.trava = threading.Lock()

    # ------------------------------------------------------------------ consultas
    def _canal(self, canal_id):
        return next((c for c in self.registro.listar() if c["id"] == canal_id), None)

    def rodando(self):
        with self.trava:
            return [i for i, e in self.execucoes.items() if e.rodando]

    def _pasta(self, canal_id):
        return self.pasta_lives / pasta_id(canal_id)

    def _momentos(self, pasta):
        """Últimos momentos detectados (momentos.jsonl), do mais novo para o mais antigo."""
        try:
            linhas = (pasta / "momentos.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return [], 0
        itens = []
        for l in linhas[-MOMENTOS_NA_TELA:][::-1]:
            try:
                itens.append(json.loads(l))
            except ValueError:
                continue
        return itens, len(linhas)

    def situacao(self, canal):
        """O que a tela mostra para um canal: situação do monitor, números e últimos momentos."""
        execucao = self.execucoes.get(canal["id"])
        pasta = self._pasta(canal["id"])
        rodando = bool(execucao and execucao.rodando)
        saida = {"id": canal["id"], "rodando": rodando, "pausado": (pasta / "pausar.flag").exists() if rodando else False,
                 "parando": bool(rodando and execucao.pedido_parada), "estado": "parado", "detalhe": "", "erro": "",
                 "momentos": [], "total_momentos": 0}
        status = _ler_json(pasta / "status.json") if execucao else None
        if execucao and status and status.get("pid") is not None and execucao.proc and status["pid"] != execucao.proc.pid:
            status = None                                              # status de uma execução anterior
        if status:
            saida.update({k: status.get(k) for k in ("estado", "detalhe", "erro", "buffer_s", "tempo_monitor_s", "chat", "sensibilidade",
                                                     "segmentos", "sinais_audio", "sinais_chat", "sinais_palavra", "momentos_salvos",
                                                     "mensagens_chat", "reinicios", "cortes_enviados", "cortes_descartados", "cortes_erro", "ultimo_corte", "ia", "enviar_cortes", "ultimo_sinal", "ultimo_momento", "iniciado_em",
                                                     "atualizado_em")})
            saida["total_momentos"] = status.get("momentos", 0)
            if rodando:
                try:
                    idade = time.time() - datetime.fromisoformat(status["atualizado_em"]).timestamp()
                except (KeyError, ValueError, TypeError):
                    idade = 0
                if idade > STATUS_VELHO_S:
                    saida["detalhe"] = f"sem resposta do monitor há {int(idade)} s"
                    saida["sem_resposta"] = True
        if rodando:
            saida["segundos"] = round(time.time() - execucao.inicio)
            if not status:
                saida.update({"estado": "iniciando", "detalhe": "subindo o monitor"})
            elif execucao.pedido_parada:
                saida["estado"], saida["detalhe"] = "parando", "encerrando"
        elif execucao:
            saida["codigo"] = execucao.codigo
            if execucao.codigo == 3 and status:
                saida["estado"] = "bloqueado"
            elif execucao.codigo not in (0, None) and saida["estado"] != "bloqueado":
                saida["estado"] = "erro"
                saida["erro"] = saida["erro"] or self._ultimo_erro(execucao) or f"o monitor saiu com o código {execucao.codigo}"
            else:
                saida["estado"] = "parado"
            saida["segundos"] = round((execucao.fim or time.time()) - execucao.inicio)
        if status or execucao:
            saida["momentos"], visto = self._momentos(pasta)
            saida["total_momentos"] = max(saida["total_momentos"], visto)
        return saida

    @staticmethod
    def _ultimo_erro(execucao):
        linhas, _ = execucao.desde(0)
        for l in reversed(linhas):
            if "[ERROR]" in l or "Traceback" in l or "Error" in l:
                return re.sub(r"^\[[\d:]+\]\s*\[ERROR\]\s*", "", l)[:300]
        return ""

    def listar(self):
        canais = self.registro.listar()
        return {"lives": [{"canal": c, **self.situacao(c)} for c in canais], "maximo": MAX_SIMULTANEAS,
                "rodando": len(self.rodando())}

    def log(self, canal_id, desde=0):
        execucao = self.execucoes.get(canal_id)
        if not execucao:
            return [], 0
        return execucao.desde(desde)

    # ------------------------------------------------------------------ ações
    def _comando(self, canal):
        pid = pasta_id(canal["id"])
        url = canal["url_live"][:-len("/live")] if canal["url_live"].endswith("/live") else canal["url_live"]
        if self.comando_monitor:
            return self.comando_monitor(url, pid)
        return [self.python, "-m", "monitor", "--url", url, "--id", pid, "--raiz", str(self.raiz)]

    def iniciar(self, canal_id):
        """Sobe o monitor do canal. Levanta ValueError com a mensagem para a tela."""
        canal = self._canal(str(canal_id or ""))
        if canal is None:
            raise ValueError("Esse canal não está na lista de canais permitidos.")
        if canal["situacao"] != "ativo":
            raise ValueError("Esse canal está suspenso. Reative-o na lista para o monitor assisti-lo.")
        with self.trava:
            atual = self.execucoes.get(canal["id"])
            if atual and atual.rodando:
                raise ValueError("O monitor já está assistindo a este canal.")
            if sum(1 for e in self.execucoes.values() if e.rodando) >= MAX_SIMULTANEAS:
                raise ValueError(f"Já há {MAX_SIMULTANEAS} lives sendo monitoradas ao mesmo tempo (o máximo). Pare uma para iniciar outra.")
            pasta = self._pasta(canal["id"])
            pasta.mkdir(parents=True, exist_ok=True)
            for nome in ("parar.flag", "pausar.flag", "status.json"):              # começa limpo
                try:
                    (pasta / nome).unlink()
                except OSError:
                    pass
            execucao = Execucao(canal["id"], self._comando(canal))
            env = os.environ.copy()
            env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
            bin_dir = self.raiz / "bin"
            if bin_dir.is_dir():
                env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
            kwargs = {}
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                kwargs["start_new_session"] = True
            try:
                execucao.proc = subprocess.Popen(execucao.comando, cwd=str(self.raiz), env=env, stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                                 encoding="utf-8", errors="replace", bufsize=1, **kwargs)
            except OSError as e:
                raise ValueError(f"Não foi possível iniciar o monitor: {e}")
            try:
                self.pasta_logs.mkdir(parents=True, exist_ok=True)
                execucao.arquivo_log = open(self.pasta_logs / f"live_{pasta_id(canal['id'])}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
                                            "w", encoding="utf-8")
            except OSError:
                execucao.arquivo_log = None
            self.execucoes[canal["id"]] = execucao
        execucao.adicionar(f"▶ Iniciando o monitor de {canal['nome']} ({canal['plataforma']})")
        threading.Thread(target=self._acompanhar, args=(execucao,), daemon=True).start()
        return self.situacao(canal)

    def _acompanhar(self, execucao):
        try:
            for linha in execucao.proc.stdout:
                execucao.adicionar(linha.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        execucao.codigo = execucao.proc.wait()
        execucao.fim = time.time()
        execucao.adicionar(f"■ Monitor encerrado (código {execucao.codigo}) após {round(execucao.fim - execucao.inicio)} s")
        try:
            execucao.proc.stdout.close()
        except OSError:
            pass
        if execucao.arquivo_log:
            try:
                execucao.arquivo_log.close()
            except OSError:
                pass

    def parar(self, canal_id):
        """Pede a parada (parar.flag) e, se o monitor não sair a tempo, encerra o processo. Devolve True se havia o que parar."""
        execucao = self.execucoes.get(str(canal_id or ""))
        if not execucao or not execucao.rodando:
            return False
        if execucao.pedido_parada is None:
            execucao.pedido_parada = time.time()
            try:
                (self._pasta(execucao.canal_id) / "parar.flag").write_text("parar", encoding="utf-8")
            except OSError:
                self.matar_arvore(execucao.proc)
                return True
            threading.Thread(target=self._forcar_parada, args=(execucao,), daemon=True).start()
        return True

    def _forcar_parada(self, execucao, limite=None):
        fim = time.time() + (TEMPO_PARADA_S if limite is None else limite)
        while time.time() < fim:
            if not execucao.rodando:
                return
            time.sleep(0.2)
        if execucao.rodando and execucao.proc is not None:
            execucao.adicionar("⚠ O monitor não saiu a tempo; encerrando o processo.")
            self.matar_arvore(execucao.proc)

    def pausar(self, canal_id, pausar):
        execucao = self.execucoes.get(str(canal_id or ""))
        if not execucao or not execucao.rodando:
            raise ValueError("O monitor deste canal não está rodando.")
        arq = self._pasta(execucao.canal_id) / "pausar.flag"
        try:
            if pausar:
                arq.write_text("pausar", encoding="utf-8")
            elif arq.exists():
                arq.unlink()
        except OSError as e:
            raise ValueError(f"Não foi possível alterar a pausa: {e}")
        return True

    def parar_todas(self, esperar=8.0):
        """Ao fechar a interface: nenhuma live fica sendo gravada em segundo plano."""
        ativas = [e for e in list(self.execucoes.values()) if e.rodando]
        for e in ativas:
            self.parar(e.canal_id)
        fim = time.time() + esperar
        while time.time() < fim and any(e.rodando for e in ativas):
            time.sleep(0.2)
        for e in ativas:
            if e.rodando and e.proc is not None:
                self.matar_arvore(e.proc)


def _matar_arvore(proc):
    """Encerra o processo e tudo que ele iniciou (ffmpeg, yt-dlp...)."""
    import signal
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass
