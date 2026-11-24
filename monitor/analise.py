"""Medição do áudio de um segmento (volume em LUFS, pelo filtro ebur128 do ffmpeg)."""
import re
import subprocess

_RE_I = re.compile(r"^\s*I:\s+(-?\d+(?:\.\d+)?|-inf)\s+LUFS", re.M)
_RE_PICO = re.compile(r"^\s*Peak:\s+(-?\d+(?:\.\d+)?|-inf)\s+dBFS", re.M)


def _num(txt):
    return float("-inf") if txt == "-inf" else float(txt)


def interpretar_ebur128(saida):
    """(lufs, pico_db) do resumo final que o ebur128 imprime. (None, None) se não achar (sem áudio, erro)."""
    achados = _RE_I.findall(saida or "")
    if not achados:
        return None, None
    lufs = _num(achados[-1])
    picos = _RE_PICO.findall(saida or "")
    pico = _num(picos[-1]) if picos else None
    if lufs == float("-inf"):
        lufs = -70.0
    return lufs, pico


def medir_audio(ffmpeg, caminho, executar=None, timeout=30):
    """-> (lufs, pico_db) do segmento; (None, None) se não deu para medir."""
    executar = executar or (lambda cmd, t: subprocess.run(cmd, capture_output=True, text=True, timeout=t,
                                                          encoding="utf-8", errors="replace"))
    cmd = [str(ffmpeg), "-hide_banner", "-nostats", "-i", str(caminho), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"]
    try:
        r = executar(cmd, timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    return interpretar_ebur128((getattr(r, "stderr", "") or "") + "\n" + (getattr(r, "stdout", "") or ""))
