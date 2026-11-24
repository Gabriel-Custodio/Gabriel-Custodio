"""Ingestão contínua: UM ffmpeg gravando segmentos curtos (sem os buracos do esquema antigo de 1 processo por segmento).

- Resolve a URL da live com o yt-dlp (e reresolve quando o stream cai ou expira).
- Reinicia sozinho com espera crescente; live offline = tenta de novo a cada `espera_offline` s.
- Cada segmento terminado vira um Segmento com tempo do monitor (linha única, ancorada no primeiro segmento da sessão).
- Modo replay: o mesmo caminho, mas a entrada é um arquivo tocado em tempo real (-re), como se fosse uma live.
"""
import csv
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .anel import Segmento

SEGUNDOS_POR_SEGMENTO = 4
ESPERAS_REINICIO = (2, 4, 8, 15, 30, 60)
RE_OFFLINE = re.compile(r"(is offline|not currently live|no longer live|does not have a live|live event will begin|"
                        r"will begin in|premieres in|no live stream|offline)", re.I)


class LiveOffline(Exception):
    """A live não está no ar agora."""


class FalhaStream(Exception):
    """Não foi possível obter o stream (rede, yt-dlp, bloqueio)."""


def achar_ffmpeg(raiz):
    local = Path(raiz) / "bin" / "ffmpeg.exe"
    if local.is_file():
        return str(local)
    return shutil.which("ffmpeg")


def args_js_runtime():
    """Runtime JavaScript para o yt-dlp (o YouTube pode exigir)."""
    for nome in ("deno", "node", "bun"):
        if shutil.which(nome):
            return [] if nome == "deno" else ["--js-runtimes", nome]
    return []


def resolver_stream(url_live, python=None, executar=None, timeout=45):
    """URL direta (HLS) da live ATUAL de uma página de canal/live. Levanta LiveOffline ou FalhaStream."""
    executar = executar or (lambda cmd, t: subprocess.run(cmd, capture_output=True, text=True, timeout=t,
                                                          encoding="utf-8", errors="replace"))
    cmd = [python or sys.executable, "-m", "yt_dlp", "-g", "-f", "best[height<=1080]/best", "--no-warnings",
           "--no-playlist"] + args_js_runtime() + [url_live]
    try:
        r = executar(cmd, timeout)
    except subprocess.TimeoutExpired:
        raise FalhaStream(f"yt-dlp demorou mais de {timeout}s para responder")
    except OSError as e:
        raise FalhaStream(f"não foi possível rodar o yt-dlp: {e}")
    if r.returncode != 0:
        erro = (r.stderr or "").strip()
        if RE_OFFLINE.search(erro):
            raise LiveOffline(erro.splitlines()[-1][:200] if erro else "live offline")
        raise FalhaStream(f"yt-dlp falhou: {erro[-300:] or 'sem mensagem'}")
    linhas = [l.strip() for l in (r.stdout or "").splitlines() if l.strip().startswith("http")]
    if not linhas:
        raise FalhaStream("o yt-dlp não devolveu nenhum endereço")
    return next((l for l in linhas if ".m3u8" in l), linhas[0])


def comando_gravacao(ffmpeg, entrada, pasta, segundos=SEGUNDOS_POR_SEGMENTO, replay=False):
    """ffmpeg contínuo: copia o vídeo (sem recodificar) em segmentos .ts e escreve a lista em lista.csv."""
    pasta = Path(pasta)
    pre = ["-re"] if replay else ["-fflags", "+genpts", "-reconnect", "1", "-reconnect_streamed", "1",
                                  "-reconnect_delay_max", "5", "-rw_timeout", "15000000"]
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", *pre, "-i", str(entrada),
            "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-f", "segment", "-segment_time", str(segundos),
            "-segment_format", "mpegts", "-reset_timestamps", "1",
            "-segment_list", str(pasta / "lista.csv"), "-segment_list_type", "csv", "-segment_list_flags", "+live",
            str(pasta / "seg_%06d.ts")]


def ler_lista(caminho):
    """Linhas (nome, inicio, fim) do lista.csv, ignorando a última se estiver pela metade."""
    try:
        texto = Path(caminho).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    saida = []
    for linha in csv.reader(texto.splitlines()):
        if len(linha) >= 3:
            try:
                saida.append((linha[0], float(linha[1]), float(linha[2])))
            except ValueError:
                continue
    return saida


class Ingestor(threading.Thread):
    """Mantém a gravação de pé e entrega cada segmento pronto em `ao_segmento(Segmento)`."""

    def __init__(self, pasta, ffmpeg, entrada_fn: Callable[[], str], ao_segmento: Callable[[Segmento], None],
                 ao_estado: Callable[[str, str], None], replay=False, segundos=SEGUNDOS_POR_SEGMENTO,
                 espera_offline=60.0, limite_sem_segmento=40.0, relogio=time.time, lancar=None, esperas=ESPERAS_REINICIO):
        super().__init__(daemon=True, name="ingestao")
        self.pasta, self.ffmpeg, self.entrada_fn = Path(pasta), ffmpeg, entrada_fn
        self.ao_segmento, self.ao_estado = ao_segmento, ao_estado
        self.replay, self.segundos = replay, segundos
        self.espera_offline, self.limite_sem_segmento = espera_offline, limite_sem_segmento
        self.relogio, self.esperas = relogio, esperas
        self.lancar = lancar or (lambda cmd: subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                                              stdin=subprocess.DEVNULL))
        self.parar_ev = threading.Event()
        self.proc = None
        self.reinicios = 0
        self.sessao = 0
        self.ref: Optional[float] = None         # tempo do monitor em que a 1ª sessão começou (o chat de replay se ancora aqui)
        self.fim_anterior = 0.0                   # fim do último segmento entregue (as sessões novas continuam dali)
        self.estado_final = False                 # True quando não há mais nada a gravar (fim do replay)

    def parar(self):
        self.parar_ev.set()
        self._matar()

    def _matar(self):
        p = self.proc
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def run(self):
        tentativa = 0
        while not self.parar_ev.is_set():
            self.ao_estado("conectando", "resolvendo o endereço da live" if not self.replay else "abrindo o arquivo")
            try:
                entrada = self.entrada_fn()
            except LiveOffline as e:
                self.ao_estado("offline", f"live fora do ar; nova checagem em {self.espera_offline:.0f}s ({e})")
                self.parar_ev.wait(self.espera_offline)
                continue
            except FalhaStream as e:
                espera = self.esperas[min(tentativa, len(self.esperas) - 1)]
                tentativa += 1
                self.ao_estado("erro", f"{e}; nova tentativa em {espera}s")
                self.parar_ev.wait(espera)
                continue
            gravou = self._gravar(entrada)
            if self.parar_ev.is_set():
                break
            if gravou:
                tentativa = 0
            if self.replay and gravou:                       # o arquivo acabou: fim do replay
                self.ao_estado("fim", "o arquivo de replay terminou")
                self.estado_final = True
                break
            self.reinicios += 1
            espera = self.esperas[min(tentativa, len(self.esperas) - 1)]
            tentativa += 1
            self.ao_estado("erro", f"a gravação parou; reconectando em {espera}s")
            self.parar_ev.wait(espera)

    def _gravar(self, entrada):
        """Roda um ffmpeg até ele acabar ou travar. True se chegou a entregar algum segmento."""
        self.sessao += 1
        pasta = self.pasta / f"s{self.sessao:03d}"
        pasta.mkdir(parents=True, exist_ok=True)
        try:
            self.proc = self.lancar(comando_gravacao(self.ffmpeg, entrada, pasta, self.segundos, self.replay))
        except OSError as e:
            self.ao_estado("erro", f"não foi possível rodar o ffmpeg: {e}")
            return False
        self.ao_estado("gravando", "gravando a live" if not self.replay else "tocando o arquivo como se fosse uma live")
        entregues = 0
        base_t: Optional[float] = None       # início (no relógio do ffmpeg) do primeiro segmento da sessão
        origem = 0.0
        ultimo_visto = self.relogio()
        while not self.parar_ev.is_set():
            novos = ler_lista(pasta / "lista.csv")[entregues:]
            for nome, ini, fim in novos:
                caminho = pasta / nome
                if not caminho.exists():
                    break
                if base_t is None:
                    # tempo do monitor em que esta sessão começa: agora menos a duração do 1º segmento, mas nunca antes
                    # do fim do último segmento já entregue (a linha do tempo só anda para a frente)
                    base_t = ini
                    origem = max(self.fim_anterior, self.relogio() - (fim - ini))
                    if self.ref is None:
                        self.ref = origem
                seg = Segmento(caminho, origem + (ini - base_t), origem + (fim - base_t))
                self.fim_anterior = seg.fim
                entregues += 1
                ultimo_visto = self.relogio()
                try:
                    self.ao_segmento(seg)
                except Exception as e:                       # um erro do consumidor não derruba a gravação
                    self.ao_estado("gravando", f"erro ao tratar um segmento: {e}")
            if self.proc.poll() is not None and not ler_lista(pasta / "lista.csv")[entregues:]:
                break
            if self.relogio() - ultimo_visto > self.limite_sem_segmento:
                self.ao_estado("erro", "sem segmentos novos há muito tempo; reiniciando a gravação")
                break
            self.parar_ev.wait(0.5)
        self._matar()
        return entregues > 0
