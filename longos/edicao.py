"""Edição do vídeo longo horizontal: opções, linha do tempo aparada e comandos do ffmpeg (sem rodar nada).

O bruto (`videos_longos/_brutos/NOME.mp4`) nunca é alterado: cada renderização parte dele. Aqui ficam só as partes
puras (validação, contas de tempo, listas de argumentos); quem roda o ffmpeg é o gerar_cortes.py.
"""
import math
from pathlib import Path

TITULO_MAX = 100                 # caracteres do título do vídeo
PRESET_FINAL = "fast"            # a renderização de 12 minutos precisa ser viável em PC comum
CRF_FINAL = 18
FADE_FINAL_S = 0.5               # fade de vídeo e de áudio no fim
LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"
OPCOES_BOOL = ("legenda", "resumo", "cta", "capitulos")
CAPITULO_DURACAO_S = 4.5         # quanto tempo o título do momento fica na tela


def limpar_titulo(valor, maximo=TITULO_MAX):
    """Texto do título: sem caracteres de controle, espaços simples, no máximo `maximo` caracteres."""
    texto = "".join(c for c in str(valor) if ord(c) >= 32 and ord(c) != 127)
    return " ".join(texto.split())[:maximo].strip()


def normalizar_opcoes(dados, normalizar_trechos):
    """Valida as opções da edição. None se algo estiver errado (nunca levanta erro).

    `normalizar_trechos` é a função do gerar_cortes (devolve (lista|None, ok)): a mesma regra dos cortes curtos.
    Tudo vem ligado por padrão; `trechos` ausente = o vídeo inteiro.
    """
    if not isinstance(dados, dict):
        return None
    opcoes = {}
    for chave in OPCOES_BOOL:
        valor = dados.get(chave, True)
        if not isinstance(valor, bool):
            return None
        opcoes[chave] = valor
    trechos, ok = normalizar_trechos(dados.get("trechos"))
    if not ok:
        return None
    if trechos:
        opcoes["trechos"] = trechos
    titulo = dados.get("titulo")
    if titulo is not None:
        if not isinstance(titulo, str):
            return None
        titulo = limpar_titulo(titulo)
        if titulo:
            opcoes["titulo"] = titulo
    return opcoes


def duracao_dos_trechos(trechos, total):
    """Duração do vídeo depois de aparado (sem trechos = o vídeo inteiro)."""
    return sum(b - a for a, b in trechos) if trechos else float(total)


def mapear_tempo(t, trechos):
    """Posição do instante `t` (no bruto) na linha do tempo aparada, ou None se esse instante foi cortado.

    Sem trechos a linha do tempo é a do bruto (devolve `t`).
    """
    if not trechos:
        return float(t)
    deslocamento = 0.0
    for a, b in trechos:
        if a - 1e-6 <= t <= b + 1e-6:
            return deslocamento + max(0.0, t - a)
        deslocamento += b - a
    return None


def capitulos(momentos, trechos, duracao):
    """Um título por momento, na linha do tempo aparada: [{n, inicio, fim, titulo}].

    Momento cortado por inteiro não vira capítulo; se só o começo foi cortado, o capítulo aparece onde o momento passa
    a existir. `fim` é o fim do capítulo na tela (CAPITULO_DURACAO_S ou até o próximo capítulo/fim do vídeo).
    """
    itens = []
    for m in momentos or []:
        try:
            a, b = float(m["inicio"]), float(m["fim"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(a) and math.isfinite(b)) or b <= a:
            continue
        inicio = None
        if not trechos:
            inicio = a
        else:
            for ta, tb in trechos:                              # o primeiro pedaço mantido que toca o momento
                if tb > a + 0.5 and ta < b - 0.5:
                    inicio = mapear_tempo(max(a, ta), trechos)
                    break
        if inicio is None or inicio >= duracao - 1.0:
            continue
        texto = limpar_titulo(m.get("titulo") or "", 60) or f"Momento {m.get('n') or len(itens) + 1}"
        itens.append({"n": int(m.get("n") or len(itens) + 1), "inicio": round(inicio, 3), "titulo": texto})
    itens.sort(key=lambda c: c["inicio"])
    for i, c in enumerate(itens):
        proximo = itens[i + 1]["inicio"] if i + 1 < len(itens) else duracao
        c["fim"] = round(min(c["inicio"] + CAPITULO_DURACAO_S, proximo - 0.1, duracao), 3)
    return [c for c in itens if c["fim"] - c["inicio"] >= 0.8]


def comando_aparar(ffmpeg, bruto, trechos, saida, crf=CRF_FINAL):
    """Junta só os trechos mantidos do bruto num arquivo intermediário (recodifica; o resultado final é codificado de novo)."""
    partes, rotulos = [], []
    for i, (a, b) in enumerate(trechos):
        partes.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}];"
                      f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
        rotulos.append(f"[v{i}][a{i}]")
    grafo = ";".join(partes) + f";{''.join(rotulos)}concat=n={len(trechos)}:v=1:a=1[v][a]"
    return [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(bruto),
            "-filter_complex", grafo, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-fps_mode", "cfr", str(saida)]


def _caminho_filtro(caminho):
    return Path(caminho).as_posix().replace(":", "\\:").replace("'", "\\'")


def comando_final(ffmpeg, fonte, ass, duracao, saida, preset=PRESET_FINAL, crf=CRF_FINAL):
    """Renderização final: legenda/títulos queimados (se houver ASS), volume uniforme e fade no fim. Sempre 1920x1080."""
    video = []
    if ass:
        video.append(f"subtitles='{_caminho_filtro(ass)}'")
    audio = [LOUDNORM]
    if duracao and duracao >= 2 * FADE_FINAL_S + 1:
        inicio = duracao - FADE_FINAL_S
        video.append(f"fade=t=out:st={inicio:.2f}:d={FADE_FINAL_S}")
        audio.append(f"afade=t=out:st={inicio:.2f}:d={FADE_FINAL_S}")
    cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(fonte)]
    if video:
        cmd += ["-vf", ",".join(video)]
    cmd += ["-af", ",".join(audio),
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(saida)]
    return cmd


def texto_para_resumo(caps, segmentos, duracao, maximo=2400):
    """Texto para a IA escrever a abertura narrada: "Momento N (TÍTULO): o que foi dito", na linha do tempo final.

    Só entram títulos e falas reais do vídeo (a abertura só pode usar o que está aqui). Sem capítulos, devolve a fala toda.
    """
    segmentos = [s for s in segmentos or [] if s.get("text")]
    if not caps:
        return " ".join(s["text"].strip() for s in segmentos)[:maximo]
    por_momento = min(300, max(60, maximo // len(caps) - 50))
    linhas = []
    for i, c in enumerate(caps):
        fim = caps[i + 1]["inicio"] if i + 1 < len(caps) else duracao
        fala = " ".join(s["text"].strip() for s in segmentos if s["end"] > c["inicio"] and s["start"] < fim)
        linhas.append(f"Momento {c['n']} ({c['titulo']}): {fala[:por_momento]}")
    return "\n".join(linhas)[:maximo]
