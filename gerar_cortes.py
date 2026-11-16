#!/usr/bin/env python3
"""
Gerador de cortes virais para TikTok/YouTube Shorts.
Baixa vídeo, extrai áudio, transcreve com legendas do YouTube,
identifica melhores trechos (30-55s) e exporta formato vertical 9:16 com legendas.
"""
import subprocess
import json
import sys
import os
import re
import math
import shutil
import textwrap
from pathlib import Path
from datetime import timedelta


# ========== CONFIGURAÇÕES ==========
OUTPUT_DIR = Path("cortes_prontos")
TEMP_DIR = Path("temp_downloads")
BIN_DIR = Path("bin")
FFMPEG = BIN_DIR / "ffmpeg.exe"
MODEL_SIZE = "small"  # não usado com yt-dlp, mas mantido para compatibilidade
LANGUAGE = "pt"
TARGET_DURATION_MIN = 30
TARGET_DURATION_MAX = 55
NUM_CLIPS = 3
VERTICAL_WIDTH = 1080
VERTICAL_HEIGHT = 1920
FONT_SIZE = 70
# ====================================


def run_cmd(cmd, cwd=None):
    """Executa comando (lista de argumentos, sem shell) e retorna (sucesso, stdout, stderr).

    Sem shell=True, a URL e os nomes de arquivo nunca são interpretados pelo cmd.exe
    (evita injeção de comando e quebra com '&', espaços etc.).
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                cwd=cwd, encoding='utf-8', errors='replace')
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def resolve_ffmpeg():
    """Define qual ffmpeg usar: bin/ffmpeg.exe se existir, senão o do PATH.

    Retorna True se encontrou um ffmpeg (e atualiza a variável global FFMPEG).
    """
    global FFMPEG
    if FFMPEG.exists():
        return True
    found = shutil.which("ffmpeg")
    if found:
        FFMPEG = Path(found)
        return True
    return False


def ensure_dirs():
    """Cria diretórios necessários."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)


def clean_filename(name):
    """Limpa nome de arquivo para uso no sistema."""
    return re.sub(r'[\\/*?:"<>|]', "_", name)[:100]


def download_video(url, output_prefix):
    """Baixa vídeo (1080p max) e áudio usando yt-dlp."""
    print(f"\n📥 Baixando vídeo: {url}")
    # Onde o yt-dlp encontra o ffmpeg (necessário para juntar vídeo + áudio)
    ffmpeg_loc = ["--ffmpeg-location", str(FFMPEG.parent)]

    # Baixar vídeo (melhor qualidade até 1080p)
    video_path = TEMP_DIR / f"{output_prefix}_video.mp4"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        *ffmpeg_loc,
        "--no-playlist",
        "--merge-output-format", "mp4",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]",
        "-o", str(video_path),
        url
    ]
    ok, out, err = run_cmd(cmd)
    if not ok or not video_path.exists():
        print(f"❌ Falha ao baixar vídeo: {err}")
        return None, None

    # Baixar áudio separado (para transcrição via legendas do YouTube)
    audio_path = TEMP_DIR / f"{output_prefix}_audio.m4a"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        *ffmpeg_loc,
        "--no-playlist",
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "-o", str(audio_path),
        url
    ]
    ok, out, err = run_cmd(cmd)
    if not ok:
        print(f"⚠️  Falha ao baixar áudio separado: {err}")

    # Baixar legendas do YouTube (com timestamps)
    sub_path = TEMP_DIR / f"{output_prefix}_subs.json3"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        *ffmpeg_loc,
        "--no-playlist",
        "--write-auto-sub",
        "--sub-lang", "pt,pt-BR,pt-PT",
        "--sub-format", "json3",
        "--skip-download",
        "-o", str(TEMP_DIR / f"{output_prefix}_subs.%(ext)s"),
        url
    ]
    ok, out, err = run_cmd(cmd)

    # Procurar arquivo de legenda baixado
    sub_files = list(TEMP_DIR.glob(f"{output_prefix}_subs*.json3"))
    if not sub_files:
        sub_files = list(TEMP_DIR.glob(f"{output_prefix}_subs*.vtt"))
    if not sub_files:
        sub_files = list(TEMP_DIR.glob(f"{output_prefix}_subs*.srt"))

    subtitle_path = sub_files[0] if sub_files else None

    print(f"✅ Vídeo: {video_path}")
    print(f"✅ Legendas: {subtitle_path if subtitle_path else 'NÃO ENCONTRADAS'}")

    return video_path, subtitle_path


def parse_json3_subtitle(sub_path):
    """Parseia arquivo JSON3 do yt-dlp para lista de segmentos com timestamps."""
    try:
        with open(sub_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        segments = []
        for event in data.get('events', []):
            if 'segs' in event:
                start_ms = event.get('tStartMs', 0)
                dur_ms = event.get('dDurationMs', 0)
                text = ''.join(seg.get('utf8', '') for seg in event['segs'])
                text = text.replace('\n', ' ').strip()
                if text:
                    segments.append({
                        'start': start_ms / 1000.0,
                        'end': (start_ms + dur_ms) / 1000.0,
                        'text': text
                    })
        return segments
    except Exception as e:
        print(f"❌ Erro ao parsear JSON3: {e}")
        return []


def parse_vtt_subtitle(sub_path):
    """Parseia arquivo VTT para lista de segmentos."""
    segments = []
    try:
        with open(sub_path, 'r', encoding='utf-8') as f:
            content = f.read()

        blocks = re.split(r'\n\s*\n', content)
        for block in blocks:
            lines = block.strip().split('\n')
            # No VTT do YouTube a linha de tempo é a primeira do bloco (sem número do cue)
            idx = next((i for i, ln in enumerate(lines) if '-->' in ln), None)
            if idx is not None:
                time_line = lines[idx]
                # Remove tags inline do YouTube (<00:00:01.000>, <c>...</c>)
                text = re.sub(r'<[^>]+>', '', ' '.join(lines[idx + 1:])).strip()
                # Parse timestamp: 00:00:00.000 --> 00:00:05.000
                times = re.findall(r'(\d+):(\d+):(\d+[\.,]\d+)', time_line)
                if len(times) == 2:
                    start = float(times[0][0]) * 3600 + float(times[0][1]) * 60 + float(times[0][2].replace(',', '.'))
                    end = float(times[1][0]) * 3600 + float(times[1][1]) * 60 + float(times[1][2].replace(',', '.'))
                    if text:
                        segments.append({'start': start, 'end': end, 'text': text})
    except Exception as e:
        print(f"❌ Erro ao parsear VTT: {e}")
    return segments


def parse_srt_subtitle(sub_path):
    """Parseia arquivo SRT para lista de segmentos."""
    segments = []
    try:
        with open(sub_path, 'r', encoding='utf-8') as f:
            content = f.read()

        blocks = re.split(r'\n\s*\n', content.strip())
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3 and '-->' in lines[1]:
                time_line = lines[1]
                text = ' '.join(lines[2:]).strip()
                times = re.findall(r'(\d+):(\d+):(\d+[\.,]\d+)', time_line)
                if len(times) == 2:
                    start = float(times[0][0]) * 3600 + float(times[0][1]) * 60 + float(times[0][2].replace(',', '.'))
                    end = float(times[1][0]) * 3600 + float(times[1][1]) * 60 + float(times[1][2].replace(',', '.'))
                    if text:
                        segments.append({'start': start, 'end': end, 'text': text})
    except Exception as e:
        print(f"❌ Erro ao parsear SRT: {e}")
    return segments


def dedupe_segments(segments):
    """Remove repetições das legendas automáticas do YouTube.

    As legendas automáticas são "rolantes": o mesmo texto aparece em vários
    eventos que se sobrepõem no tempo. Aqui ordenamos por tempo, tiramos do
    início de cada segmento as palavras que já terminaram o segmento anterior
    e encurtamos o anterior para não sobrepor, evitando legenda duplicada.
    """
    result = []
    prev_raw = []  # texto original (sem cortes) do evento anterior
    for seg in sorted(segments, key=lambda s: (s['start'], s['end'])):
        words = seg['text'].split()
        if not words:
            continue
        raw = list(words)
        if result:
            prev = result[-1]
            prev_words = prev_raw
            # Maior sufixo do anterior que também é o prefixo do atual = repetição.
            # (1 palavra só conta se o segmento inteiro for essa palavra.)
            for k in range(min(len(prev_words), len(words)), 0, -1):
                if prev_words[-k:] == words[:k] and (k >= 2 or k == len(words)):
                    words = words[k:]
                    break
            if not words:  # era só repetição: apenas estende o tempo do anterior
                prev['end'] = max(prev['end'], seg['end'])
                prev_raw = raw
                continue
            if prev['start'] < seg['start'] < prev['end']:
                prev['end'] = seg['start']
        prev_raw = raw
        result.append({'start': seg['start'], 'end': seg['end'], 'text': ' '.join(words)})
    return result


def load_subtitles(sub_path):
    """Carrega legendas baseado na extensão e remove repetições."""
    if not sub_path or not sub_path.exists():
        return []

    ext = sub_path.suffix.lower()
    if ext == '.json3':
        segments = parse_json3_subtitle(sub_path)
    elif ext == '.vtt':
        segments = parse_vtt_subtitle(sub_path)
    elif ext == '.srt':
        segments = parse_srt_subtitle(sub_path)
    else:
        return []
    return dedupe_segments(segments)


def merge_subtitle_segments(segments, max_chars=500):
    """Agrupa segmentos de legenda em blocos maiores para análise."""
    if not segments:
        return []

    merged = []
    current_text = ""
    current_start = None

    for seg in segments:
        if current_start is None:
            current_start = seg['start']
        current_text += " " + seg['text']
        current_end = seg['end']

        # Se texto ficou grande ou pausa longa, finaliza bloco
        if len(current_text) >= max_chars:
            merged.append({
                'start': current_start,
                'end': current_end,
                'text': current_text.strip()
            })
            current_text = ""
            current_start = None

    # Último bloco
    if current_text:
        merged.append({
            'start': current_start,
            'end': segments[-1]['end'],
            'text': current_text.strip()
        })

    return merged


def score_segment(text, start, end, duration, total_duration):
    """
    Pontua um segmento para potencial viral.
    Critérios: gancho forte (início), conclusão clara (fim), duração ideal, posição.
    """
    score = 0
    text_lower = text.lower()

    # 1. Duração ideal (30-55s)
    if TARGET_DURATION_MIN <= duration <= TARGET_DURATION_MAX:
        score += 30
    elif 25 <= duration <= 60:
        score += 20
    elif 20 <= duration <= 65:
        score += 10

    # 2. Gancho forte no início (primeiras 3-5 palavras)
    hook_words = text_lower.split()[:5]
    hook_text = ' '.join(hook_words)

    # Palavras de gancho
    hook_patterns = [
        r'\b(nunca|jamais|impossível|incrível|surpreendente|chocante|segredo|truque|dica|como)\b',
        r'\b(você\s+(não\s+)?precisa|você\s+(não\s+)?deve|pare\s+de|comece\s+a)\b',
        r'\b(o\s+maior|a\s+maior|top\s+\d+|melhor|pior|errado|certo)\b',
        r'\b(grátis|gratuito|dinheiro|rico|milionário|sucesso|falha|erro)\b',
        r'[?!]{1,3}$',
        r'\b(olha\s+só|veja\s+só|preste\s+atenção|atenção)\b',
    ]
    for pattern in hook_patterns:
        if re.search(pattern, hook_text):
            score += 15
            break

    # 3. Conclusão clara no fim
    end_words = text_lower.split()[-8:]
    end_text = ' '.join(end_words)
    conclusion_patterns = [
        r'\b(por\s+isso|então|logo|conclusão|resumo|resumindo|finalment)\b',
        r'\b(agora\s+você\s+sabe|fica\s+a\s+dica|anote\s+aí|compartilhe)\b',
        r'\b(inscreva|like|comente|siga|baixe|link\s+na\s+bio)\b',
        r'[.]{3}$',
        r'(?!.*\b(mas|porém|entretanto)\b).*[.!?]$',  # finaliza sem continuação
    ]
    for pattern in conclusion_patterns:
        if re.search(pattern, end_text):
            score += 15
            break

    # 4. Densidade de informação (palavras-chave de valor)
    value_keywords = ['passo', 'passos', 'etapa', 'etapas', 'como', 'porque', 'porquê',
                      'resultado', 'resultados', 'antes', 'depois', 'transformou',
                      'mudei', 'aprendi', 'descobri', 'testei', 'funciona', 'prova',
                      'provas', 'números', 'dados', 'estudo', 'pesquisa', 'ciência',
                      'técnica', 'método', 'estratégia', 'segredo', 'truque']
    value_count = sum(1 for kw in value_keywords if kw in text_lower)
    score += min(value_count * 3, 20)

    # 5. Posição no vídeo (início costuma ter gancho, meio desenvolvimento, fim conclusão)
    position_ratio = start / total_duration if total_duration > 0 else 0
    if position_ratio < 0.15:  # Primeiros 15% - gancho
        score += 10
    elif 0.15 <= position_ratio < 0.4:  # Desenvolvimento
        score += 5
    elif position_ratio >= 0.8:  # Final - conclusão
        score += 10

    # 6. Engajamento visual sugerido (menções a visual)
    visual_keywords = ['olha', 'veja', 'mostro', 'mostra', 'aqui', 'tela', 'gráfico',
                       'imagem', 'foto', 'vídeo', 'demo', 'demonstração']
    visual_count = sum(1 for kw in visual_keywords if kw in text_lower)
    score += min(visual_count * 2, 10)

    # 7. Fala direta ao público
    direct_patterns = [r'\bvocê\b', r'\bseu\b', r'\bsua\b', r'\bte\b', r'\btu\b']
    direct_count = sum(1 for p in direct_patterns if re.search(p, text_lower))
    score += min(direct_count * 2, 10)

    return score


def find_best_clips(segments, video_duration, num_clips=NUM_CLIPS):
    """Identifica os melhores trechos para cortes virais."""
    if not segments:
        return []

    # Merge segmentos para análise
    merged = merge_subtitle_segments(segments)
    total_duration = video_duration

    # Gerar janelas deslizantes de 30-55s
    candidates = []
    window_sizes = list(range(TARGET_DURATION_MIN, TARGET_DURATION_MAX + 1, 5))

    for window in window_sizes:
        step = window // 3  # sobreposição
        start = 0
        while start + window <= total_duration:
            end = start + window
            # Coletar texto dessa janela
            window_text = ""
            for seg in merged:
                if seg['end'] <= start:
                    continue
                if seg['start'] >= end:
                    break
                # Calcular sobreposição
                overlap_start = max(seg['start'], start)
                overlap_end = min(seg['end'], end)
                if overlap_end > overlap_start:
                    # Proporção do segmento na janela
                    prop = (overlap_end - overlap_start) / (seg['end'] - seg['start'])
                    words = seg['text'].split()
                    n_words = max(1, int(len(words) * prop))
                    window_text += " " + ' '.join(words[:n_words])

            if len(window_text.strip()) > 100:  # mínimo de conteúdo
                score = score_segment(window_text.strip(), start, end, window, total_duration)
                candidates.append({
                    'start': start,
                    'end': end,
                    'duration': window,
                    'text': window_text.strip(),
                    'score': score
                })
            start += step

    # Ordenar por score
    candidates.sort(key=lambda x: x['score'], reverse=True)

    # Selecionar melhores não sobrepostos
    selected = []
    for c in candidates:
        # Verificar sobreposição com já selecionados
        overlap = False
        for s in selected:
            if not (c['end'] <= s['start'] or c['start'] >= s['end']):
                overlap = True
                break
        if not overlap:
            selected.append(c)
            if len(selected) >= num_clips:
                break

    # Se não encontrou suficientes, pegar os de maior score mesmo com sobreposição
    if len(selected) < num_clips:
        for c in candidates:
            if c not in selected:
                selected.append(c)
                if len(selected) >= num_clips:
                    break

    return selected[:num_clips]


def create_ass_subtitle(segments, clip_start, clip_end, output_path):
    """Cria arquivo ASS com legendas dinamicas para o corte."""
    # Filtrar segmentos dentro do corte
    clip_segments = []
    for seg in segments:
        if seg['end'] <= clip_start:
            continue
        if seg['start'] >= clip_end:
            break
        # Ajustar tempos relativos ao corte
        adj_start = max(0, seg['start'] - clip_start)
        adj_end = min(clip_end - clip_start, seg['end'] - clip_start)
        if adj_end > adj_start:
            clip_segments.append({
                'start': adj_start,
                'end': adj_end,
                'text': seg['text']
            })

    if not clip_segments:
        return False

    # Quebrar texto em linhas por tempo (máx ~2 linhas por vez)
    # Estilo ASS
    ass_header = f"""[Script Info]
Title: Cortes Virais
ScriptType: v4.00+
PlayResX: {VERTICAL_WIDTH}
PlayResY: {VERTICAL_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,62,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,350,1
Style: Highlight,Arial,{FONT_SIZE},&H00FFFF00,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,5,50,50,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def format_ass_time(seconds):
        """Formata tempo para ASS: H:MM:SS.cc"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        centiseconds = int((secs - int(secs)) * 100)
        return f"{hours}:{minutes:02d}:{int(secs):02d}.{centiseconds:02d}"

    events = []
    for i, seg in enumerate(clip_segments):
        start_str = format_ass_time(seg['start'])
        end_str = format_ass_time(seg['end'])
        text = seg['text'].strip()

        # Quebrar em linhas se muito longo
        max_chars_per_line = 35
        if len(text) > max_chars_per_line:
            words = text.split()
            lines = []
            current = ""
            for w in words:
                if len(current + " " + w) <= max_chars_per_line:
                    current += (" " + w) if current else w
                else:
                    lines.append(current)
                    current = w
            if current:
                lines.append(current)
            text = r"\N".join(lines)

        # A cada 3º segmento, usar estilo highlight (linha atual)
        style = "Highlight" if i % 3 == 0 else "Default"
        events.append(f"Dialogue: 0,{start_str},{end_str},{style},,0,0,0,,{text}")

    ass_content = ass_header + "\n".join(events)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass_content)

    return True


def cut_and_export_vertical(video_path, clip, segments, output_path, clip_num):
    """Corta vídeo, converte para vertical 9:16, adiciona legendas e normaliza áudio."""
    print(f"\n🎬 Processando corte {clip_num}: {clip['start']:.1f}s - {clip['end']:.1f}s ({clip['duration']:.1f}s)")

    # Criar arquivo ASS temporário
    ass_path = TEMP_DIR / f"clip_{clip_num}_subs.ass"
    if not create_ass_subtitle(segments, clip['start'], clip['end'], ass_path):
        print("⚠️  Falha ao criar legendas, exportando sem...")
        ass_path = None

    # Construir comando ffmpeg complexo
    # 1. Cortar segmento
    # 2. Escala para vertical com fundo desfocado (blur)
    # 3. Centralizar vídeo
    # 4. Queimar legendas (se houver)
    # 5. Normalizar áudio (loudnorm)

    vf_filters = [
        f"trim=start={clip['start']}:end={clip['end']},setpts=PTS-STARTPTS",
        f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=decrease",
        f"pad={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black@0",  # placeholder para blur
    ]

    # Fundo desfocado: duplicar vídeo, escalar para cobrir tela inteira, blur, overlay
    # Mais eficiente: usar split + gblur
    bg_filter = (
        f"[0:v]split=2[v1][v2];"
        f"[v1]scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VERTICAL_WIDTH}:{VERTICAL_HEIGHT},"
        f"gblur=sigma=30[bg];"
        f"[v2]scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[video]"
    )

    # Filtro completo com legenda e normalização de áudio
    if ass_path and ass_path.exists():
        filter_complex = (
            f"{bg_filter};"
            f"[video]subtitles='{ass_path.as_posix()}'[vout]"
        )
        map_v = "[vout]"
    else:
        filter_complex = f"{bg_filter}"
        map_v = "[video]"

    # Filtro de áudio: loudnorm para -16 LUFS (padrão broadcast/streaming)
    af_filter = "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary"

    ffmpeg_cmd = FFMPEG.as_posix()
    cmd = [
        ffmpeg_cmd, "-y",
        "-ss", str(clip['start']),
        "-t", str(clip['duration']),
        "-i", str(video_path),
        "-filter_complex", filter_complex,
        "-map", map_v,
        "-map", "0:a?",
        "-af", af_filter,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path)
    ]

    print(f"⚙️  Executando ffmpeg...")
    ok, out, err = run_cmd(cmd)

    if not ok:
        print(f"❌ Erro no ffmpeg: {err[:500]}")
        # Tentar versão simplificada sem blur
        return cut_simple_vertical(video_path, clip, segments, ass_path, output_path, clip_num)

    print(f"✅ Corte salvo: {output_path}")
    return True


def cut_simple_vertical(video_path, clip, segments, ass_path, output_path, clip_num):
    """Versão simplificada sem fundo desfocado (fallback)."""
    print(f"🔄 Tentando versão simplificada...")

    vf = f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=decrease,pad={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"
    if ass_path and ass_path.exists():
        vf += f",subtitles='{ass_path.as_posix()}'"

    ffmpeg_cmd = FFMPEG.as_posix()
    cmd = [
        ffmpeg_cmd, "-y",
        "-ss", str(clip['start']),
        "-t", str(clip['duration']),
        "-i", str(video_path),
        "-vf", vf,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path)
    ]

    ok, out, err = run_cmd(cmd)
    if ok:
        print(f"✅ Corte salvo (simplificado): {output_path}")
        return True
    else:
        print(f"❌ Falha total: {err[:500]}")
        return False


def get_video_duration(video_path):
    """Obtém duração do vídeo via ffmpeg (fallback sem ffprobe)."""
    # `ffmpeg -i` sem arquivo de saída retorna erro, mas imprime a duração no stderr.
    # Por isso não checamos o código de retorno, só procuramos a linha "Duration".
    _, out, err = run_cmd([FFMPEG.as_posix(), "-i", str(video_path)])
    match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', f"{out}\n{err}")
    if match:
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)
    return 0


def main():
    if len(sys.argv) < 2:
        print("Uso: python gerar_cortes.py <URL_DO_YOUTUBE>")
        sys.exit(1)

    url = sys.argv[1]
    print("=" * 60)
    print("✂️  GERADOR DE CORTES VIRAIS - TikTok / YouTube Shorts")
    print("=" * 60)

    # Verificar dependências
    if not resolve_ffmpeg():
        print(f"❌ ffmpeg não encontrado em {FFMPEG} nem no PATH")
        print("   Rode 'python verificar_dependencias.py' ou copie o ffmpeg.exe para a pasta 'bin'.")
        sys.exit(1)
    ok, _, _ = run_cmd([FFMPEG.as_posix(), "-version"])
    if not ok:
        print(f"❌ ffmpeg não funciona: {FFMPEG}")
        sys.exit(1)

    # Mesmo método usado nos downloads (python -m yt_dlp): não depende do PATH
    ok, _, _ = run_cmd([sys.executable, "-m", "yt_dlp", "--version"])
    if not ok:
        print("❌ yt-dlp não encontrado. Rode: pip install -r requirements.txt")
        sys.exit(1)

    ensure_dirs()

    # Prefixo para arquivos temporários
    import hashlib
    prefix = hashlib.md5(url.encode()).hexdigest()[:12]

    # 1. Baixar vídeo e legendas
    video_path, subtitle_path = download_video(url, prefix)
    if not video_path:
        sys.exit(1)

    # 2. Obter duração real do vídeo
    video_duration = get_video_duration(video_path)
    if video_duration <= 0:
        print("❌ Não foi possível obter duração do vídeo")
        sys.exit(1)
    print(f"📹 Duração do vídeo: {video_duration:.1f}s ({video_duration/60:.1f} min)")

    # 3. Carregar legendas
    segments = load_subtitles(subtitle_path) if subtitle_path else []
    if not segments:
        print("⚠️  Nenhuma legenda encontrada. Tentando extrair do vídeo...")
        # Fallback: yt-dlp pode ter baixado legenda embutida
        # Tentar buscar arquivo vtt/srt na temp
        for ext in ['.vtt', '.srt', '.json3']:
            sub_files = list(TEMP_DIR.glob(f"{prefix}_subs*{ext}"))
            if sub_files:
                segments = load_subtitles(sub_files[0])
                if segments:
                    subtitle_path = sub_files[0]
                    break

    if not segments:
        print("❌ Não foi possível obter transcrição/legendas do vídeo.")
        print("   Dica: use vídeos com legendas automáticas do YouTube ativadas.")
        sys.exit(1)

    print(f"📝 Legendas carregadas: {len(segments)} segmentos")

    # 4. Encontrar melhores clips
    print(f"\n🧠 Analisando conteúdo para encontrar {NUM_CLIPS} melhores trechos ({TARGET_DURATION_MIN}-{TARGET_DURATION_MAX}s)...")
    clips = find_best_clips(segments, video_duration)

    if not clips:
        print("❌ Não foi possível identificar trechos viáveis.")
        sys.exit(1)

    print(f"\n🎯 {len(clips)} melhores trechos identificados:")
    for i, clip in enumerate(clips, 1):
        print(f"  {i}. {clip['start']:.1f}s - {clip['end']:.1f}s ({clip['duration']:.1f}s) | Score: {clip['score']}")
        print(f"     Texto: {clip['text'][:120]}...")

    # 5. Processar cada corte
    print("\n" + "=" * 60)
    print("🎬 INICIANDO EXPORTAÇÃO DOS CORTES")
    print("=" * 60)

    success_count = 0
    for i, clip in enumerate(clips, 1):
        # Nome do arquivo final
        safe_text = clean_filename(clip['text'][:50])
        output_name = f"corte_{i}_{safe_text}.mp4"
        output_path = OUTPUT_DIR / output_name

        if cut_and_export_vertical(video_path, clip, segments, output_path, i):
            success_count += 1

    # 6. Limpeza
    print("\n🧹 Limpando arquivos temporários...")
    import shutil
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)

    # Resumo final
    print("\n" + "=" * 60)
    print(f"✅ CONCLUÍDO: {success_count}/{len(clips)} cortes gerados com sucesso")
    print(f"📁 Pasta de saída: {OUTPUT_DIR.absolute()}")
    print("=" * 60)

    if success_count > 0:
        print("\n📋 Próximos passos:")
        print("  1. Revise os cortes na pasta 'cortes_prontos/'")
        print("  2. Adicione hashtags e descrição para TikTok/Shorts")
        print("  3. Publique nos melhores horários (18-21h costuma funcionar bem)")


if __name__ == "__main__":
    main()
