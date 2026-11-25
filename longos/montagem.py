"""Montagem do bruto do vídeo longo: corta cada momento do vídeo baixado, padroniza e junta (ffmpeg).

Nada aqui importa o gerar_cortes.py: quem chama entrega `executar` (como o run_cmd: devolve (ok, out, err)) e
`medir_duracao`, o que deixa tudo testável com um ffmpeg falso.

O bruto é horizontal 1920x1080 (o editor faz o resto): cada momento sai do vídeo base com a MESMA codificação
(tamanho, fps, áudio), então a junção final é uma cópia de fluxo (sem recodificar). Se a cópia falhar, recodifica.
"""
import os
from datetime import datetime
from pathlib import Path

PASTA = "videos_longos"          # biblioteca dos vídeos longos (fica ao lado de cortes_prontos/)
PASTA_BRUTOS = "_brutos"         # o bruto nunca é alterado; a edição renderiza a partir dele
LARGURA, ALTURA, FPS = 1920, 1080, 30
CRF = 18                         # qualidade do bruto (12 minutos em 1080p ficam em centenas de MB, não em GB)
PRESET = "veryfast"
FADE_AUDIO_S = 0.12              # fade curtíssimo na entrada e na saída de cada momento (sem estalo na emenda)
TIMEOUT_MOMENTO_S = 600
FORMATO = 1                      # versão do formato do .json (o editor confere)


def nome_do_video(agora=None):
    """Nome do arquivo: VideoLongo_AAAAMMDD_HHMMSS.mp4"""
    return f"VideoLongo_{(agora or datetime.now()).strftime('%Y%m%d_%H%M%S')}.mp4"


def comando_momento(ffmpeg, origem, inicio, duracao, saida):
    """Corta [inicio, inicio+duracao] do vídeo base e padroniza (1920x1080, 30 fps, AAC 48 kHz estéreo)."""
    video = (f"scale={LARGURA}:{ALTURA}:force_original_aspect_ratio=decrease,"
             f"pad={LARGURA}:{ALTURA}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={FPS},format=yuv420p")
    fade = min(FADE_AUDIO_S, max(duracao / 4, 0.01))
    audio = f"aresample=48000,afade=t=in:st=0:d={fade:.3f},afade=t=out:st={max(duracao - fade, 0):.3f}:d={fade:.3f}"
    return [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{inicio:.3f}", "-t", f"{duracao:.3f}", "-i", str(origem),
            "-vf", video, "-af", audio,
            "-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(saida)]


def texto_da_lista(caminhos):
    """Conteúdo do arquivo de lista do concat do ffmpeg (aspas simples dos nomes são escapadas)."""
    linhas = []
    for caminho in caminhos:
        texto = Path(caminho).resolve().as_posix().replace("'", "'\\''")
        linhas.append(f"file '{texto}'")
    return "\n".join(linhas) + "\n"


def _apagar(*caminhos):
    for caminho in caminhos:
        try:
            Path(caminho).unlink()
        except OSError:
            pass


def montar_bruto(origem, escolhidos, destino, pasta_temp, ffmpeg, executar, medir_duracao, log=print):
    """Corta os `escolhidos` de `origem` e junta em `destino` (o bruto). Devolve um dict ou None se falhar.

    Um escolhido com `arquivo` (cortes de uma live) vem inteiro desse arquivo, não de `origem`: o `start`/`end` dele são
    só a posição na live (usada na escolha), e a legenda dele (`segmentos`) já é relativa ao começo do arquivo.

    O dict traz `momentos` (cada um com seu início/fim no vídeo base E na linha do tempo do vídeo longo,
    medidos nos arquivos de verdade) e `duracao_s`. Um momento que falhar é pulado; com menos de 2 sobrando, falha.
    O destino só aparece quando o arquivo está completo.
    """
    destino, pasta_temp = Path(destino), Path(pasta_temp)
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        pasta_temp.mkdir(parents=True, exist_ok=True)
    except OSError as erro:
        log(f"   ❌ Não foi possível criar as pastas do vídeo longo: {erro}")
        return None

    pedacos, momentos, cursor = [], [], 0.0
    total = len(escolhidos)
    for numero, cand in enumerate(escolhidos, 1):
        fonte = cand.get("arquivo")
        if fonte:
            inicio, duracao = 0.0, float(cand.get("duration") or (float(cand["end"]) - float(cand["start"])))
        else:
            inicio = float(cand["start"])
            duracao = float(cand["end"]) - inicio
        fim = inicio + duracao
        arquivo = pasta_temp / f"longo_momento_{numero:03d}.mp4"
        log(f"   ✂️  Momento {numero}/{total}: {inicio:.1f}s - {fim:.1f}s ({duracao:.1f}s)"
            + (f" de {Path(fonte).name}" if fonte else ""))
        ok, _, erro = executar(comando_momento(ffmpeg, fonte or origem, inicio, duracao, arquivo), timeout=TIMEOUT_MOMENTO_S)
        if not ok or not arquivo.is_file() or arquivo.stat().st_size < 1024:
            log(f"   ⚠️  Momento {numero} pulado (falha ao cortar): {str(erro)[:200]}")
            _apagar(arquivo)
            continue
        real = medir_duracao(arquivo) or duracao
        pedacos.append(arquivo)
        momentos.append({
            "n": len(momentos) + 1,
            "inicio_video": round(inicio, 3), "fim_video": round(fim, 3),
            "inicio": round(cursor, 3), "fim": round(cursor + real, 3), "duracao": round(real, 3),
            "nota": cand.get("score"), "motivo": cand.get("reason") or "",
            "titulo": cand.get("hook_title") or "", "faixa_do_video": cand.get("band"),
            "fonte_nota": cand.get("fonte"),
        })
        if fonte:                                                # corte de live: guarda de onde veio (e a legenda dele, tirada depois)
            momentos[-1].update({"arquivo": Path(fonte).name, "detectado_em": cand.get("detectado_em"),
                                 "segmentos": cand.get("segmentos") or []})
        cursor += real

    lista = pasta_temp / "longo_lista.txt"
    temporario = destino.with_name(destino.stem + ".tmp.mp4")
    try:
        if len(pedacos) < 2:
            log(f"   ❌ Só {len(pedacos)} momento(s) foram cortados: não dá para montar o vídeo longo.")
            return None
        try:
            lista.write_text(texto_da_lista(pedacos), encoding="utf-8")
        except OSError as erro:
            log(f"   ❌ Não foi possível escrever a lista de junção: {erro}")
            return None
        log(f"   🎞️  Juntando {len(pedacos)} momentos ({cursor / 60:.1f} min)...")
        base = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lista)]
        ok, _, erro = executar(base + ["-c", "copy", "-movflags", "+faststart", str(temporario)],
                               timeout=max(600, int(cursor * 2)))
        if not ok or not temporario.is_file() or temporario.stat().st_size < 1024:
            log("   ⚠️  Junção sem recodificar falhou, recodificando (mais lento)...")
            _apagar(temporario)
            ok, _, erro = executar(base + ["-c:v", "libx264", "-preset", PRESET, "-crf", str(CRF), "-pix_fmt", "yuv420p",
                                           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temporario)],
                                   timeout=max(1800, int(cursor * 6)))
            if not ok or not temporario.is_file() or temporario.stat().st_size < 1024:
                log(f"   ❌ Falha ao juntar os momentos: {str(erro)[:300]}")
                return None
        try:
            os.replace(str(temporario), str(destino))
        except OSError as erro:
            log(f"   ❌ Não foi possível guardar o vídeo longo: {erro}")
            return None
        return {"momentos": momentos, "duracao_s": round(cursor, 3)}
    finally:
        _apagar(temporario, lista, *pedacos)


def legenda_dos_momentos(segmentos, momentos):
    """Transcrição de cada momento reposicionada na linha do tempo do vídeo longo (mesmo formato do corte curto).

    Cada segmento/palavra é limitado à janela do seu momento, então nada vaza para o vizinho.
    """
    saida = []
    for momento in momentos:
        a, b = momento["inicio_video"], momento["fim_video"]
        deslocamento = momento["inicio"] - a
        comprimento = momento["duracao"]
        for seg in momento["segmentos"] if momento.get("segmentos") is not None else (segmentos or []):
            if seg["end"] <= a or seg["start"] >= b:
                continue
            inicio = max(seg["start"], a) + deslocamento
            fim = min(seg["end"], b) + deslocamento
            if fim - inicio <= 0:
                continue
            item = {"start": round(inicio, 3), "end": round(min(fim, momento["inicio"] + comprimento), 3), "text": seg["text"]}
            palavras = []
            for p in seg.get("words") or []:
                ps, pe = max(p["start"], a) + deslocamento, min(p["end"], b) + deslocamento
                if pe > ps and p["end"] > a and p["start"] < b:
                    palavras.append({"word": p["word"], "start": round(ps, 3), "end": round(pe, 3)})
            if palavras:
                item["words"] = palavras
            saida.append(item)
    return saida


def metadados(momentos, segmentos, duracao_s, alvo_s, resumo, url=None, video_id=None, streamer=None,
              fonte_resolucao=None, agora=None, extra=None):
    """O .json do vídeo longo (ao lado do bruto, em videos_longos/NOME.json). O editor lê `bruto` e `legenda_segmentos`.

    `extra`: campos a mais (ex.: de onde vieram os momentos). Momentos com `segmentos` próprios (cortes de live) usam a legenda deles.
    """
    textos = []
    for momento in momentos:
        proprios = momento["segmentos"] if momento.get("segmentos") is not None else segmentos
        trecho = " ".join(s["text"].strip() for s in proprios or []
                          if s["end"] > momento["inicio_video"] and s["start"] < momento["fim_video"] and s.get("text"))
        textos.append(trecho)
    legenda = legenda_dos_momentos(segmentos, momentos)
    momentos = [{k: v for k, v in m.items() if k != "segmentos"} for m in momentos]
    nome = f"Melhores momentos de {streamer}" if streamer else "Melhores momentos"
    dados = {
        "origem": "longo", "formato": FORMATO, "bruto": True, "edicao": {"estado": "bruto"},
        "titulo": nome, "streamer": streamer or None,
        "largura": LARGURA, "altura": ALTURA, "fps": FPS,
        "video_id": video_id, "video_url": url, "fonte_resolucao": fonte_resolucao,
        "alvo_s": round(float(alvo_s), 2), "duracao_final_s": round(float(duracao_s), 2), "clip_duration": round(float(duracao_s), 2),
        "momentos": momentos,
        "selecao": {k: resumo.get(k) for k in ("elegiveis", "escolhidos", "curto", "motivo")},
        "transcricao": " ".join(t for t in textos if t),
        "transcricao_fonte": "transcrição real dos momentos escolhidos",
        "legenda_segmentos": legenda,
        "timestamp": (agora or datetime.now()).isoformat(),
    }
    dados.update(extra or {})
    return dados
