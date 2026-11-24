"""Anel de segmentos gravados: guarda os últimos minutos da live e extrai uma janela POR HORÁRIO (não "os últimos N s")."""
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class Segmento:
    path: Path
    inicio: float              # tempo do monitor (s) em que o segmento começa
    fim: float                 # e em que termina
    lufs: Optional[float] = None

    @property
    def duracao(self):
        return self.fim - self.inicio


class Anel:
    """Segmentos em ordem de tempo, com limpeza do que ficou velho (e do arquivo no disco)."""

    def __init__(self, duracao_max=240.0):
        self.duracao_max = duracao_max
        self.segmentos: List[Segmento] = []
        self.trava = threading.Lock()
        self.retidos = {}                 # caminho -> contagem: segmentos que uma extração em andamento ainda precisa

    def registrar(self, seg: Segmento):
        with self.trava:
            self.segmentos.append(seg)
            self.segmentos.sort(key=lambda s: s.inicio)

    def limpar(self, agora):
        """Apaga do anel (e do disco) o que terminou antes de agora - duracao_max, exceto o que está retido."""
        corte = agora - self.duracao_max
        apagar = []
        with self.trava:
            manter = []
            for s in self.segmentos:
                if s.fim < corte and not self.retidos.get(str(s.path)):
                    apagar.append(s)
                else:
                    manter.append(s)
            self.segmentos = manter
        for s in apagar:
            try:
                Path(s.path).unlink()
            except OSError:
                pass
        return len(apagar)

    def segundos(self):
        with self.trava:
            return round(sum(s.duracao for s in self.segmentos), 1)

    def janela(self, t0, t1) -> List[Segmento]:
        with self.trava:
            return [s for s in self.segmentos if s.fim > t0 and s.inicio < t1]

    def cobertura(self, t0, t1):
        """Fração (0-1) de [t0, t1] que os segmentos guardados cobrem."""
        if t1 <= t0:
            return 0.0
        total = 0.0
        for s in self.janela(t0, t1):
            total += max(0.0, min(s.fim, t1) - max(s.inicio, t0))
        return min(1.0, total / (t1 - t0))

    def reter(self, segs, sinal):
        with self.trava:
            for s in segs:
                k = str(s.path)
                self.retidos[k] = max(0, self.retidos.get(k, 0) + (1 if sinal else -1))
                if not self.retidos[k]:
                    self.retidos.pop(k, None)


def extrair_janela(ffmpeg, anel: Anel, t0, t1, saida, crf=12, executar=None, pasta_temp=None) -> Tuple[bool, str, float]:
    """Corta [t0, t1] (tempo do monitor) dos segmentos do anel para `saida` (mp4). -> (ok, mensagem, cobertura 0-1).

    Re-codifica com CRF baixo (como o pré-corte do gerar_cortes) para o corte cair exatamente nos tempos pedidos.
    """
    executar = executar or (lambda cmd, timeout: subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                                                encoding="utf-8", errors="replace"))
    segs = anel.janela(t0, t1)
    if not segs:
        return False, "nenhum segmento cobre essa janela (já foi apagado?)", 0.0
    cobertura = anel.cobertura(t0, t1)
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    lista = Path(pasta_temp or saida.parent) / f"{saida.stem}_lista.txt"
    anel.reter(segs, True)
    try:
        linhas = []
        for s in segs:
            caminho = str(Path(s.path).resolve()).replace("\\", "/").replace("'", "'\\''")
            linhas.append(f"file '{caminho}'")
        lista.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        corte_inicial = max(0.0, t0 - segs[0].inicio)
        duracao = min(t1, segs[-1].fim) - max(t0, segs[0].inicio)
        cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lista),
               "-ss", f"{corte_inicial:.3f}", "-t", f"{duracao:.3f}",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-c:a", "aac", "-b:a", "192k",
               "-fps_mode", "cfr", "-fflags", "+genpts", "-movflags", "+faststart", str(saida)]
        try:
            r = executar(cmd, 180)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"ffmpeg não rodou: {e}", cobertura
        if getattr(r, "returncode", 1) != 0:
            return False, f"ffmpeg falhou: {(getattr(r, 'stderr', '') or '')[-300:]}", cobertura
        if not saida.exists() or saida.stat().st_size < 1024:
            return False, "o corte saiu vazio", cobertura
        return True, "ok", cobertura
    finally:
        anel.reter(segs, False)
        try:
            lista.unlink()
        except OSError:
            pass
