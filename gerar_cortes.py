#!/usr/bin/env python3
"""
Gerador de cortes virais para TikTok/YouTube Shorts.
Baixa vídeo, extrai áudio, transcreve com legendas do YouTube,
identifica melhores trechos (30-55s) e exporta formato vertical 9:16 com legendas.

Modos de áudio:
  --audio original  : Mantém áudio original do vídeo (padrão)
  --audio ia        : Gera narração com IA (edge-tts, voz pt-BR-AntonioNeural),
                      cria roteiro viral resumido com gancho forte, remove áudio
                      original (-an), ajusta duração do vídeo ao MP3 gerado e
                      sincroniza legendas .ass com a narração.
"""
import subprocess
import json
import sys
import os
import re
import math
import shutil
import textwrap
import asyncio
import argparse
from pathlib import Path
from datetime import timedelta


# ========== CONFIGURAÇÕES ==========
OUTPUT_DIR = Path("cortes_prontos")
TEMP_DIR = Path("temp_downloads")
BIN_DIR = Path(__file__).parent / "bin"
FFMPEG = BIN_DIR / "ffmpeg.exe"
MODEL_SIZE = "small"  # não usado com yt-dlp, mas mantido para compatibilidade
LANGUAGE = "pt"
TARGET_DURATION_MIN = 30
TARGET_DURATION_MAX = 55
NUM_CLIPS = 3
VERTICAL_WIDTH = 1080
VERTICAL_HEIGHT = 1920
FONT_SIZE = 70
# Configurações de narração IA
TTS_VOICE = "pt-BR-AntonioNeural"
TTS_RATE = "+0%"
TTS_VOLUME = "+0%"
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


def generate_viral_script(original_text, target_duration):
    """
    Gera um roteiro viral resumido a partir do texto original.
    Cria gancho forte, desenvolvimento conciso e conclusão clara.
    """
    # Limpar e preparar texto
    text = original_text.strip()
    words = text.split()

    if len(words) < 20:
        return text  # Texto muito curto, usar original

    # Estratégia: pegar início (gancho), meio (pontos-chave) e fim (conclusão)
    # Proporção: 20% gancho, 60% desenvolvimento, 20% conclusão
    hook_end = max(1, len(words) // 5)
    conclusion_start = max(hook_end + 1, len(words) * 4 // 5)

    hook_part = ' '.join(words[:hook_end])
    middle_part = ' '.join(words[hook_end:conclusion_start])
    conclusion_part = ' '.join(words[conclusion_start:])

    # Identificar palavras-chave de valor no meio
    value_keywords = ['passo', 'passos', 'etapa', 'etapas', 'como', 'porque', 'porquê',
                      'resultado', 'resultados', 'antes', 'depois', 'transformou',
                      'mudei', 'aprendi', 'descobri', 'testei', 'funciona', 'prova',
                      'provas', 'números', 'dados', 'estudo', 'pesquisa', 'ciência',
                      'técnica', 'método', 'estratégia', 'segredo', 'truque', 'dica']

    middle_words = middle_part.split()
    key_sentences = []
    current_sentence = []

    for word in middle_words:
        current_sentence.append(word)
        if word.endswith('.') or word.endswith('!') or word.endswith('?'):
            sentence = ' '.join(current_sentence)
            if any(kw in sentence.lower() for kw in value_keywords):
                key_sentences.append(sentence)
            current_sentence = []

    if current_sentence:
        sentence = ' '.join(current_sentence)
        if any(kw in sentence.lower() for kw in value_keywords):
            key_sentences.append(sentence)

    # Construir roteiro viral
    # Gancho: primeiras palavras com tom de urgência/curiosidade
    hook = hook_part
    if not re.search(r'[?!]', hook):
        hook = hook.rstrip('.') + "!"

    # Desenvolvimento: frases-chave (máx 3-4)
    development = ' '.join(key_sentences[:4]) if key_sentences else middle_part[:300]

    # Conclusão: última parte com call-to-action
    conclusion = conclusion_part
    if not re.search(r'\b(inscreva|like|comente|siga|compartilhe|baixe|link)\b', conclusion.lower()):
        conclusion = conclusion.rstrip('.') + " — Fica a dica!"

    script = f"{hook} {development} {conclusion}".strip()

    # Limitar tamanho aproximado para duração alvo
    # Estimativa: ~150 palavras por minuto em português = 2.5 palavras/seg
    max_words = int(target_duration * 2.5)
    script_words = script.split()
    if len(script_words) > max_words:
        script = ' '.join(script_words[:max_words])
        # Garantir que termina com pontuação
        if not script.endswith(('.', '!', '?', '...')):
            script += "."

    return script


async def generate_tts_audio(text, output_path, voice=TTS_VOICE, rate=TTS_RATE, volume=TTS_VOLUME):
    """Gera áudio com edge-tts."""
    try:
        import edge_tts
    except ImportError:
        print("❌ edge-tts não instalado. Rode: pip install edge-tts")
        return False

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
        await communicate.save(str(output_path))
        return output_path.exists()
    except Exception as e:
        print(f"❌ Erro ao gerar TTS: {e}")
        return False


def create_ass_subtitle_from_script(script_text, audio_duration, output_path):
    """
    Cria arquivo ASS sincronizado com o áudio da narração.
    Divide o texto em blocos de 3-5 palavras distribuídos ao longo da duração do áudio.
    """
    words = script_text.strip().split()
    if not words:
        return False

    # Configuração: 3-5 palavras por bloco
    WORDS_PER_CHUNK = 4
    chunks = []

    for i in range(0, len(words), WORDS_PER_CHUNK):
        chunk_words = words[i:i + WORDS_PER_CHUNK]
        chunks.append(' '.join(chunk_words))

    if not chunks:
        return False

    # Distribuir tempo proporcionalmente (cada chunk tem duração similar)
    chunk_duration = audio_duration / len(chunks)

    word_chunks = []
    for i, chunk_text in enumerate(chunks):
        start = i * chunk_duration
        end = min((i + 1) * chunk_duration, audio_duration)
        word_chunks.append({
            'start': start,
            'end': end,
            'text': chunk_text
        })

    # Garantir sem sobreposição
    for i in range(len(word_chunks) - 1):
        if word_chunks[i]['end'] > word_chunks[i + 1]['start']:
            word_chunks[i]['end'] = word_chunks[i + 1]['start']

    # Remover chunks muito curtos
    word_chunks = [c for c in word_chunks if c['end'] - c['start'] >= 0.4]

    # Estilo ASS - Positionado mais para baixo (Alignment=2 = bottom-center, MarginV=320)
    ass_header = f"""[Script Info]
Title: Cortes Virais - Narrado por IA
ScriptType: v4.00+
PlayResX: {VERTICAL_WIDTH}
PlayResY: {VERTICAL_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,62,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,320,1
Style: Highlight,Arial,{FONT_SIZE},&H00FFFF00,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,5,50,50,320,1

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
    for i, chunk in enumerate(word_chunks):
        start_str = format_ass_time(chunk['start'])
        end_str = format_ass_time(chunk['end'])
        text = chunk['text'].strip()

        # Alternar estilo para efeito de "karaoke"
        style = "Highlight" if i % 2 == 0 else "Default"
        events.append(f"Dialogue: 0,{start_str},{end_str},{style},,0,0,0,,{text}")

    ass_content = ass_header + "\n".join(events)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass_content)

    return True


def get_audio_duration(audio_path):
    """Obtém duração do áudio via ffmpeg."""
    _, out, err = run_cmd([FFMPEG.as_posix(), "-i", str(audio_path)])
    match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', f"{out}\n{err}")
    if match:
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)
    return 0


def cut_and_export_vertical(video_path, clip, segments, output_path, clip_num, audio_mode="original", script_text=None):
    """Corta vídeo, converte para vertical 9:16, adiciona legendas e normaliza áudio."""
    print(f"\n🎬 Processando corte {clip_num}: {clip['start']:.1f}s - {clip['end']:.1f}s ({clip['duration']:.1f}s) [Modo: {audio_mode}]")

    ass_path = None
    tts_audio_path = None
    final_duration = clip['duration']
    video_trim_start = clip['start']

    if audio_mode == "ia":
        # Modo IA: gerar narração e sincronizar
        print(f"🤖 Gerando roteiro viral e narração com edge-tts ({TTS_VOICE})...")

        # Gerar roteiro viral baseado no texto do clip
        viral_script = generate_viral_script(clip['text'], clip['duration'])
        print(f"📝 Roteiro gerado ({len(viral_script.split())} palavras): {viral_script[:150]}...")

        # Gerar áudio TTS
        tts_audio_path = TEMP_DIR / f"clip_{clip_num}_tts.mp3"
        success = asyncio.run(generate_tts_audio(viral_script, tts_audio_path))

        if not success or not tts_audio_path.exists():
            print("⚠️  Falha no TTS, usando áudio original...")
            audio_mode = "original"
        else:
            # Obter duração real do áudio gerado
            tts_duration = get_audio_duration(tts_audio_path)
            print(f"🔊 Áudio TTS gerado: {tts_duration:.1f}s")
            final_duration = tts_duration
            video_trim_start = clip['start']  # Começar do início do clip original

            # Criar legendas sincronizadas com a narração
            ass_path = TEMP_DIR / f"clip_{clip_num}_subs.ass"
            if not create_ass_subtitle_from_script(viral_script, tts_duration, ass_path):
                print("⚠️  Falha ao criar legendas sincronizadas")
                ass_path = None

    if audio_mode == "original":
        # Modo original: usar legendas baseadas nas legendas do YouTube
        ass_path = TEMP_DIR / f"clip_{clip_num}_subs.ass"
        if not create_ass_subtitle(segments, clip['start'], clip['end'], ass_path):
            print("⚠️  Falha ao criar legendas, exportando sem...")
            ass_path = None

    # Construir comando ffmpeg
    # Fundo desfocado: duplicar vídeo, escalar para cobrir tela inteira, blur, overlay
    bg_filter = (
        f"[0:v]split=2[v1][v2];"
        f"[v1]scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VERTICAL_WIDTH}:{VERTICAL_HEIGHT},"
        f"gblur=sigma=30[bg];"
        f"[v2]scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[video]"
    )

    if audio_mode == "ia" and tts_audio_path and tts_audio_path.exists():
        # Usar áudio TTS externo, remover áudio original do vídeo (-an na entrada de vídeo)
        # O vídeo será esticado/acelerado para durar o mesmo que o áudio TTS
        speed_ratio = clip['duration'] / final_duration if final_duration > 0 else 1.0

        if ass_path and ass_path.exists():
            filter_complex = (
                f"{bg_filter};"
                f"[video]setpts={1/speed_ratio:.6f}*PTS[vspeed];"
                f"[vspeed]subtitles='{ass_path.as_posix()}'[vout]"
            )
            map_v = "[vout]"
        else:
            filter_complex = (
                f"{bg_filter};"
                f"[video]setpts={1/speed_ratio:.6f}*PTS[vout]"
            )
            map_v = "[vout]"

        ffmpeg_cmd = FFMPEG.as_posix()
        cmd = [
            ffmpeg_cmd, "-y",
            "-ss", str(video_trim_start),
            "-t", str(clip['duration']),
            "-i", str(video_path),
            "-i", str(tts_audio_path),
            "-filter_complex", filter_complex,
            "-map", map_v,
            "-map", "1:a",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-shortest",  # Terminar quando o mais curto acabar (áudio TTS)
            str(output_path)
        ]
    else:
        # Modo original: usar áudio do vídeo
        if ass_path and ass_path.exists():
            filter_complex = (
                f"{bg_filter};"
                f"[video]subtitles='{ass_path.as_posix()}'[vout]"
            )
            map_v = "[vout]"
        else:
            filter_complex = f"{bg_filter}"
            map_v = "[video]"

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
        return cut_simple_vertical(video_path, clip, segments, ass_path, output_path, clip_num, audio_mode, tts_audio_path)

    print(f"✅ Corte salvo: {output_path}")
    return True


def cut_simple_vertical(video_path, clip, segments, ass_path, output_path, clip_num, audio_mode="original", tts_audio_path=None):
    """Versão simplificada sem fundo desfocado (fallback)."""
    print(f"🔄 Tentando versão simplificada...")

    vf = f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=decrease,pad={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"
    if ass_path and ass_path.exists():
        vf += f",subtitles='{ass_path.as_posix()}'"

    ffmpeg_cmd = FFMPEG.as_posix()

    if audio_mode == "ia" and tts_audio_path and tts_audio_path.exists():
        speed_ratio = clip['duration'] / get_audio_duration(tts_audio_path) if get_audio_duration(tts_audio_path) > 0 else 1.0
        vf = f"setpts={1/speed_ratio:.6f}*PTS," + vf
        cmd = [
            ffmpeg_cmd, "-y",
            "-ss", str(clip['start']),
            "-t", str(clip['duration']),
            "-i", str(video_path),
            "-i", str(tts_audio_path),
            "-vf", vf,
            "-map", "0:v",
            "-map", "1:a",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-shortest",
            str(output_path)
        ]
    else:
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
    parser = argparse.ArgumentParser(description="Gerador de cortes virais para TikTok/YouTube Shorts")
    parser.add_argument("url", help="URL do vídeo do YouTube")
    parser.add_argument("--audio", choices=["original", "ia"], default="original",
                        help="Modo de áudio: original (padrão) ou ia (narração com edge-tts)")
    args = parser.parse_args()

    url = args.url
    audio_mode = args.audio

    print("=" * 60)
    print("✂️  GERADOR DE CORTES VIRAIS - TikTok / YouTube Shorts")
    print(f"🔊 Modo de áudio: {'Narração IA (edge-tts)' if audio_mode == 'ia' else 'Áudio original'}")
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

    if audio_mode == "ia":
        ok, _, _ = run_cmd([sys.executable, "-c", "import edge_tts"])
        if not ok:
            print("❌ edge-tts não encontrado. Rode: pip install edge-tts")
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
        suffix = "_ia" if audio_mode == "ia" else ""
        output_name = f"corte_{i}{suffix}_{safe_text}.mp4"
        output_path = OUTPUT_DIR / output_name

        if cut_and_export_vertical(video_path, clip, segments, output_path, i, audio_mode):
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