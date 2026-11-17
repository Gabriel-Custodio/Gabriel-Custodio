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

Sistema de Fallback de Legendas (3 passos):
  Passo A: Legendas automáticas do YouTube (.vtt/.json3)
  Passo B: Transcrição de áudio via Groq Whisper API (whisper-large-v3-turbo)
  Passo C: Modo Visual Gameplay - análise de frames + narração IA
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
import base64
import hashlib
from pathlib import Path
from datetime import timedelta
from urllib.parse import urlparse, parse_qs


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

# Configurações de Fallback de Legendas
GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"
AUDIO_CHUNK_DURATION = 15 * 60  # 15 minutos em segundos
MAX_VIDEO_DURATION_FOR_SINGLE_CHUNK = 20 * 60  # 20 minutos
MIN_WORDS_FOR_AUDIO_TRANSCRIPTION = 30
VISUAL_FRAME_INTERVAL = 30  # Extrair frame a cada 30 segundos (fallback gameplay)
MAX_VISUAL_FRAMES = 20

# Configurações do Modo Edição Completa por Visão IA
VISION_EDIT_FRAME_INTERVAL = 4  # 1 frame a cada 4 segundos
VISION_EDIT_FRAME_WIDTH = 480   # Largura dos frames para análise
VISION_EDIT_MAX_FRAMES = 180    # Máximo de frames (para vídeos longos)
VISION_EDIT_VIDEOS_MIN = 1      # Mínimo de vídeos curtos a gerar
VISION_EDIT_VIDEOS_MAX = 3      # Máximo de vídeos curtos a gerar
VISION_EDIT_SCENES_MIN = 5      # Mínimo de cenas por vídeo
VISION_EDIT_SCENES_MAX = 8      # Máximo de cenas por vídeo
VISION_EDIT_TARGET_DURATION_MIN = 30  # Duração mínima por vídeo (segundos)
VISION_EDIT_TARGET_DURATION_MAX = 45  # Duração máxima por vídeo (segundos)

# ====================================


def clean_youtube_url(url):
    """
    Limpa a URL do YouTube mantendo apenas o ID principal do vídeo.
    Remove parâmetros de tracking, playlist, timestamp, etc.
    """
    # Padrões de URL do YouTube
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            return f"https://www.youtube.com/watch?v={video_id}"

    # Se já for uma URL limpa, retorna como está
    if re.match(r'https://www\.youtube\.com/watch\?v=[a-zA-Z0-9_-]{11}$', url):
        return url

    # Fallback: retorna original se não conseguir parsear
    return url


def extract_audio_for_transcription(video_path, output_dir, chunk_duration=AUDIO_CHUNK_DURATION):
    """
    Extrai áudio do vídeo em MP3 mono 64kbps.
    Se vídeo > 20 min, divide em chunks de 15 min.
    Retorna lista de caminhos dos arquivos de áudio.
    """
    print(f"🎵 Extraindo áudio para transcrição...")

    # Primeiro, obtém duração do vídeo
    video_duration = get_video_duration(video_path)
    print(f"   Duração do vídeo: {video_duration/60:.1f} min")

    audio_files = []

    if video_duration <= MAX_VIDEO_DURATION_FOR_SINGLE_CHUNK:
        # Vídeo curto: extrai áudio completo
        audio_path = output_dir / "audio_full.mp3"
        cmd = [
            FFMPEG.as_posix(), "-y",
            "-i", str(video_path),
            "-vn",  # Sem vídeo
            "-ac", "1",  # Mono
            "-ar", "16000",  # 16kHz (ideal para Whisper)
            "-b:a", "64k",  # 64kbps
            "-f", "mp3",
            str(audio_path)
        ]
        ok, out, err = run_cmd(cmd)
        if ok and audio_path.exists():
            audio_files.append(audio_path)
            print(f"   ✅ Áudio extraído: {audio_path}")
        else:
            print(f"   ❌ Falha ao extrair áudio: {err[:200]}")
    else:
        # Vídeo longo: divide em chunks de 15 min
        num_chunks = math.ceil(video_duration / chunk_duration)
        print(f"   Vídeo longo detectado. Dividindo em {num_chunks} chunks de {chunk_duration/60:.0f} min...")

        for i in range(num_chunks):
            start_time = i * chunk_duration
            chunk_duration_actual = min(chunk_duration, video_duration - start_time)

            audio_path = output_dir / f"audio_chunk_{i:03d}.mp3"
            cmd = [
                FFMPEG.as_posix(), "-y",
                "-ss", str(start_time),
                "-t", str(chunk_duration_actual),
                "-i", str(video_path),
                "-vn",
                "-ac", "1",
                "-ar", "16000",
                "-b:a", "64k",
                "-f", "mp3",
                str(audio_path)
            ]
            ok, out, err = run_cmd(cmd)
            if ok and audio_path.exists():
                audio_files.append(audio_path)
                print(f"   ✅ Chunk {i+1}/{num_chunks}: {audio_path} ({chunk_duration_actual/60:.1f} min)")
            else:
                print(f"   ❌ Falha no chunk {i+1}: {err[:200]}")

    return audio_files


def transcribe_audio_groq(audio_path, api_key):
    """
    Transcreve áudio usando Groq API (whisper-large-v3-turbo).
    Retorna lista de segmentos com timestamps no formato padrão do projeto.
    """
    try:
        import requests
    except ImportError:
        print("❌ requests não instalado. Rode: pip install requests")
        return []

    print(f"🔄 Transcrevendo via Groq Whisper API: {audio_path.name}")

    try:
        with open(audio_path, 'rb') as f:
            files = {'file': (audio_path.name, f, 'audio/mpeg')}
            data = {
                'model': GROQ_MODEL,
                'response_format': 'verbose_json',
                'timestamp_granularities[]': 'segment',
                'language': 'pt'
            }
            headers = {'Authorization': f'Bearer {api_key}'}

            response = requests.post(GROQ_API_URL, files=files, data=data, headers=headers, timeout=120)

        if response.status_code != 200:
            print(f"   ❌ Erro na API Groq: {response.status_code} - {response.text[:200]}")
            return []

        result = response.json()
        segments = []

        for seg in result.get('segments', []):
            text = seg.get('text', '').strip()
            if text:
                segments.append({
                    'start': seg.get('start', 0),
                    'end': seg.get('end', 0),
                    'text': text
                })

        print(f"   ✅ Transcrição concluída: {len(segments)} segmentos")
        return segments

    except Exception as e:
        print(f"   ❌ Erro na transcrição: {e}")
        return []


def extract_key_frames(video_path, output_dir, interval=VISUAL_FRAME_INTERVAL, max_frames=MAX_VISUAL_FRAMES):
    """
    Extrai frames-chave do vídeo para análise visual (modo gameplay).
    Retorna lista de caminhos dos frames extraídos.
    """
    print(f"🖼️  Extraindo frames-chave para análise visual...")

    video_duration = get_video_duration(video_path)
    frame_paths = []

    # Calcula intervalo real para não exceder max_frames
    actual_interval = max(interval, video_duration / max_frames)
    num_frames = min(max_frames, int(video_duration / actual_interval) + 1)

    for i in range(num_frames):
        timestamp = i * actual_interval
        if timestamp >= video_duration:
            break

        frame_path = output_dir / f"frame_{i:03d}_{timestamp:.0f}s.jpg"
        cmd = [
            FFMPEG.as_posix(), "-y",
            "-ss", str(timestamp),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",  # Alta qualidade
            "-vf", "scale=1280:720",  # Reduzir para economia
            str(frame_path)
        ]
        ok, out, err = run_cmd(cmd)
        if ok and frame_path.exists():
            frame_paths.append({
                'path': frame_path,
                'timestamp': timestamp
            })
        else:
            print(f"   ⚠️  Falha ao extrair frame em {timestamp:.0f}s")

    print(f"   ✅ {len(frame_paths)} frames extraídos")
    return frame_paths


def analyze_frames_with_ai(frame_paths, api_key):
    """
    Analisa frames com IA (via API) para identificar clímax e momentos virais.
    Retorna lista de dicionários com timestamps de clímax selecionados.
    Cada item: {'timestamp': float, 'reason': str, 'script': str}
    """
    try:
        import requests
    except ImportError:
        return []

    if not frame_paths or not api_key:
        # Fallback sem API: retorna frames distribuídos como clímax candidatos
        return _fallback_visual_analysis(frame_paths)

    print(f"🤖 Analisando {len(frame_paths)} frames com IA para identificar clímax...")

    # Prepara imagens em base64 (máximo 8 para análise)
    images_b64 = []
    for frame_info in frame_paths[:8]:
        try:
            with open(frame_info['path'], 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('utf-8')
                images_b64.append({
                    'timestamp': frame_info['timestamp'],
                    'data': b64
                })
        except Exception as e:
            print(f"   ⚠️  Erro ao ler frame: {e}")

    if not images_b64:
        return _fallback_visual_analysis(frame_paths)

    try:
        # Chama API de visão (Groq suporta modelos multimodais)
        # Se não houver modelo de visão disponível, usa fallback
        climax_moments = _call_vision_api(images_b64, api_key)
        if climax_moments:
            return climax_moments

        # Fallback se API falhar
        return _fallback_visual_analysis(frame_paths)

    except Exception as e:
        print(f"   ⚠️  Erro na análise visual via API: {e}")
        return _fallback_visual_analysis(frame_paths)


def _call_vision_api(images_b64, api_key):
    """
    Chama API de visão (Groq) para identificar momentos de clímax.
    Retorna lista de {'timestamp': float, 'reason': str, 'script': str}
    """
    try:
        import requests
    except ImportError:
        return None

    # Prepara payload para modelo de visão
    # Groq tem suporte a modelos multimodais como llama-3.2-90b-vision-preview
    content = [
        {
            "type": "text",
            "text": (
                "Analise estes frames de um vídeo de gameplay. "
                "Identifique os 1 a 3 melhores momentos de CLÍMAX VISUAL que dariam bons cortes virais (30-45s cada). "
                "Critérios: vitória, descoberta, reação forte, conclusão de desafio, momento 'wow', fail engraçado, "
                "easter egg, boss defeat, achievement unlock, final de speedrun.\n\n"
                "Para CADA clímax identificado, retorne JSON com:\n"
                "- timestamp: segundo exato do frame (número)\n"
                "- reason: por que é viral (ex: 'vitória contra boss final', 'descoberta secreta', 'reação de susto')\n"
                "- script: roteiro narrado viral de 35-45s em pt-BR com gancho, desenvolvimento e CTA, "
                "com pontuação rigorosa (vírgulas, pontos, exclamações) para TTS natural.\n\n"
                "Responda APENAS com array JSON válido. Exemplo:\n"
                "[{\"timestamp\": 342.5, \"reason\": \"vitória contra boss final\", "
                "\"script\": \"Você não vai acreditar! Após 50 tentativas, finalmente derrotei o boss final! "
                "O segredo era atacar na fase 2. Fica a dica: persistência vence!\"}]"
            )
        }
    ]

    for img in images_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img['data']}"}
        })

    payload = {
        "model": "llama-3.2-90b-vision-preview",
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.7,
        "max_tokens": 2000
    }

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }

    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=60
    )

    if response.status_code != 200:
        print(f"   ⚠️  API visão falhou: {response.status_code}")
        return None

    result = response.json()
    content_text = result['choices'][0]['message']['content'].strip()

    # Extrai JSON da resposta
    import json
    try:
        # Tenta parsear JSON direto
        climax_moments = json.loads(content_text)
    except json.JSONDecodeError:
        # Tenta extrair JSON de dentro de markdown ou texto
        import re
        json_match = re.search(r'\[.*\]', content_text, re.DOTALL)
        if json_match:
            climax_moments = json.loads(json_match.group())
        else:
            print(f"   ⚠️  Resposta da API não contém JSON válido")
            return None

    # Valida e filtra resultados
    valid_moments = []
    for moment in climax_moments:
        if isinstance(moment, dict) and 'timestamp' in moment and 'script' in moment:
            ts = float(moment['timestamp'])
            # Garante que timestamp está dentro da duração do vídeo (aproximado)
            if ts >= 0:
                valid_moments.append({
                    'timestamp': ts,
                    'reason': moment.get('reason', 'clímax visual'),
                    'script': moment['script']
                })

    # Ordena por timestamp e pega top 3
    valid_moments.sort(key=lambda x: x['timestamp'])
    return valid_moments[:3]


def _fallback_visual_analysis(frame_paths):
    """
    Fallback sem API: seleciona frames bem distribuídos como clímax candidatos.
    Gera roteiros padrão para cada um.
    """
    print("   📋 Usando análise visual de fallback (sem API de visão)...")

    if not frame_paths:
        return []

    # Seleciona até 3 frames bem distribuídos (início, meio, fim)
    num_climax = min(3, len(frame_paths))
    indices = []
    if num_climax == 1:
        indices = [len(frame_paths) // 2]
    elif num_climax == 2:
        indices = [len(frame_paths) // 3, 2 * len(frame_paths) // 3]
    else:
        indices = [len(frame_paths) // 4, len(frame_paths) // 2, 3 * len(frame_paths) // 4]

    climax_moments = []
    for idx in indices:
        frame = frame_paths[idx]
        ts = frame['timestamp']
        mins = int(ts // 60)
        secs = int(ts % 60)

        # Gera roteiro padrão baseado na posição
        if idx < len(frame_paths) * 0.33:
            reason = "início promissor / gancho visual"
            script = (
                "Olha só esse início incrível! Nos primeiros minutos já acontece algo inesperado. "
                "Você vai querer ver como isso se desenvolve. Fica a dica: o começo define tudo!"
            )
        elif idx < len(frame_paths) * 0.66:
            reason = "meio do gameplay / desenvolvimento"
            script = (
                "No meio da partida, a estratégia muda completamente! "
                "Uma mecânica secreta é revelada e tudo vira de cabeça para baixo. "
                "Você precisa ver isso para acreditar. Inscreva-se para mais!"
            )
        else:
            reason = "final / clímax do gameplay"
            script = (
                "O final é de arrepiar! Após toda a jornada, o momento da vitória chega. "
                "A recompensa valeu cada segundo de esforço. "
                "Compartilhe se você também vibrou! Fica a dica!"
            )

        # Ajusta duração do script para ~40s (~100 palavras)
        max_words = 100
        script_words = script.split()
        if len(script_words) > max_words:
            script = ' '.join(script_words[:max_words])
            if not script.endswith(('.', '!', '?', '...')):
                script += "."

        climax_moments.append({
            'timestamp': ts,
            'reason': reason,
            'script': script
        })

    return climax_moments


def generate_climax_clips_from_visual(video_path, frame_paths, climax_moments, output_dir, clip_num_start=1):
    """
    Para cada momento de clímax identificado, gera o corte completo:
    - Cria roteiro narrado (já vem do climax_moments)
    - Gera áudio TTS com edge-tts
    - Corta vídeo no FFmpeg sincronizado com áudio
    - Salva em output_dir
    Retorna lista de caminhos dos cortes gerados.
    """
    if not climax_moments:
        print("   ⚠️  Nenhum clímax identificado para gerar cortes")
        return []

    video_duration = get_video_duration(video_path)
    generated_clips = []

    for i, climax in enumerate(climax_moments):
        clip_idx = clip_num_start + i
        timestamp = climax['timestamp']
        reason = climax['reason']
        script = climax['script']

        print(f"\n🎬 Gerando corte visual {clip_idx}: clímax em {timestamp:.1f}s ({reason})")
        print(f"   Roteiro ({len(script.split())} palavras): {script[:120]}...")

        # Define janela de corte centrada no timestamp (±20s = 40s total)
        # Ajusta para ficar dentro dos limites do vídeo
        window_half = 20  # segundos de cada lado
        clip_start = max(0, timestamp - window_half)
        clip_end = min(video_duration, timestamp + window_half)

        # Se o vídeo for curto, usa duração alvo padrão
        target_duration = clip_end - clip_start
        if target_duration < TARGET_DURATION_MIN:
            # Estende para duração mínima
            needed = TARGET_DURATION_MIN - target_duration
            clip_start = max(0, clip_start - needed / 2)
            clip_end = min(video_duration, clip_end + needed / 2)
            target_duration = clip_end - clip_start
        elif target_duration > TARGET_DURATION_MAX:
            # Reduz para duração máxima
            excess = target_duration - TARGET_DURATION_MAX
            clip_start += excess / 2
            clip_end -= excess / 2
            target_duration = clip_end - clip_start

        # Gera áudio TTS
        tts_audio_path = TEMP_DIR / f"visual_clip_{clip_idx}_tts.mp3"
        success, tts_duration, word_events = asyncio.run(
            generate_tts_audio(script, tts_audio_path)
        )

        if not success or not tts_audio_path.exists():
            print(f"   ❌ Falha no TTS para corte {clip_idx}")
            continue

        print(f"   🔊 Áudio TTS: {tts_duration:.1f}s ({len(word_events)} eventos WordBoundary)")

        # Cria legendas sincronizadas com WordBoundary
        ass_path = TEMP_DIR / f"visual_clip_{clip_idx}_subs.ass"
        if not create_ass_subtitle_from_tts_events(word_events, ass_path):
            print(f"   ⚠️  Falha ao criar legendas, continuando sem...")
            ass_path = None

        # Nome do arquivo de saída
        safe_reason = clean_filename(reason[:40])
        output_name = f"corte_visual_{clip_idx}_{safe_reason}.mp4"
        output_path = output_dir / output_name

        # Corta e exporta vídeo vertical sincronizado com TTS
        # Cria um clip object compatível com cut_and_export_vertical
        clip_obj = {
            'start': clip_start,
            'end': clip_end,
            'duration': clip_end - clip_start,
            'text': script,
            'score': 100  # Score máximo para clímax visual
        }

        # Usa duração real do TTS para sincronia exata
        final_duration = tts_duration
        speed_ratio = clip_obj['duration'] / final_duration if final_duration > 0 else 1.0

        # Constrói comando FFmpeg direto para modo visual (similar a cut_and_export_vertical modo IA)
        bg_filter = (
            f"[0:v]split=2[v1][v2];"
            f"[v1]scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{VERTICAL_HEIGHT},"
            f"gblur=sigma=30[bg];"
            f"[v2]scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[video]"
        )

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
            "-ss", str(clip_start),
            "-t", str(final_duration),  # Duração exata do áudio TTS
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
            "-shortest",
            str(output_path)
        ]

        print(f"   ⚙️  Executando FFmpeg (velocidade: {speed_ratio:.2f}x)...")
        ok, out, err = run_cmd(cmd)

        if not ok:
            print(f"   ❌ Erro no FFmpeg: {err[:300]}")
            # Tenta fallback simplificado
            ok = _try_simple_cut(video_path, clip_start, final_duration, tts_audio_path, ass_path, output_path, speed_ratio)
            if not ok:
                continue

        print(f"   ✅ Corte visual salvo: {output_path}")
        generated_clips.append(str(output_path))

    return generated_clips


def _try_simple_cut(video_path, start_time, duration, tts_audio_path, ass_path, output_path, speed_ratio):
    """Tenta corte simplificado sem fundo desfocado."""
    print(f"   🔄 Tentando versão simplificada...")

    vf = f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=decrease,pad={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"
    if ass_path and ass_path.exists():
        vf += f",subtitles='{ass_path.as_posix()}'"
    vf = f"setpts={1/speed_ratio:.6f}*PTS," + vf

    ffmpeg_cmd = FFMPEG.as_posix()
    cmd = [
        ffmpeg_cmd, "-y",
        "-ss", str(start_time),
        "-t", str(duration),
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

    ok, out, err = run_cmd(cmd)
    if ok:
        print(f"   ✅ Corte salvo (simplificado): {output_path}")
    return ok


def load_subtitles_with_fallback(subtitle_path, video_path, temp_dir, api_key=None):
    """
    Carrega legendas com cascata de fallback:
    Passo A: Legendas do YouTube (.vtt/.json3/.srt)
    Passo B: Transcrição de áudio via Groq Whisper API
    Passo C: Modo Visual Gameplay (frames + IA) - retorna dados especiais para fluxo dedicado

    Retorna tupla: (segments, source, extra_data)
    - Para youtube/groq_audio: segments = lista de segmentos, extra_data = None
    - Para visual_gameplay: segments = [], extra_data = {'frame_paths': [...], 'climax_moments': [...]}
    """
    print("\n📝 Carregando legendas (sistema de fallback em 3 passos)...")

    # === PASSO A: Legendas do YouTube ===
    print("   Passo A: Tentando legendas automáticas do YouTube...")
    segments = load_subtitles(subtitle_path) if subtitle_path else []

    if segments:
        total_words = sum(len(s['text'].split()) for s in segments)
        print(f"   ✅ Passo A OK: {len(segments)} segmentos, {total_words} palavras")
        return segments, "youtube", None

    # Tenta buscar outros arquivos de legenda na pasta temp
    for ext in ['.vtt', '.srt', '.json3']:
        sub_files = list(temp_dir.glob(f"*subs*{ext}"))
        if sub_files:
            segments = load_subtitles(sub_files[0])
            if segments:
                total_words = sum(len(s['text'].split()) for s in segments)
                print(f"   ✅ Passo A OK (fallback local): {len(segments)} segmentos, {total_words} palavras")
                return segments, "youtube", None

    print("   ⚠️  Passo A falhou: Nenhuma legenda do YouTube encontrada")

    # === PASSO B: Transcrição de Áudio via Groq ===
    if api_key:
        print("   Passo B: Tentando transcrição de áudio via Groq Whisper API...")

        # Extrai áudio (com chunking se necessário)
        audio_files = extract_audio_for_transcription(video_path, temp_dir)

        all_segments = []
        for audio_file in audio_files:
            segments = transcribe_audio_groq(audio_file, api_key)
            all_segments.extend(segments)

        if all_segments:
            total_words = sum(len(s['text'].split()) for s in all_segments)
            print(f"   ✅ Passo B OK: {len(all_segments)} segmentos, {total_words} palavras")

            # Se tem poucas palavras (< 30), pode ser gameplay sem fala
            if total_words < MIN_WORDS_FOR_AUDIO_TRANSCRIPTION:
                print(f"   ⚠️  Poucas palavras ({total_words} < {MIN_WORDS_FOR_AUDIO_TRANSCRIPTION}). Possível gameplay sem narração.")
                # Não retorna ainda - tenta Passo C
            else:
                return all_segments, "groq_audio", None
        else:
            print("   ⚠️  Passo B falhou: Transcrição não retornou segmentos")
    else:
        print("   ⚠️  Passo B pulado: GROQ_API_KEY não configurada no .env")

    # === PASSO C: Modo Visual Gameplay ===
    print("   Passo C: Modo Visual Gameplay - extraindo frames para análise...")

    frame_paths = extract_key_frames(video_path, temp_dir)
    if not frame_paths:
        print("   ❌ Falha ao extrair frames")
        return [], "failed", None

    # Analisa frames com IA para identificar clímax
    climax_moments = analyze_frames_with_ai(frame_paths, api_key)

    if not climax_moments:
        print("   ⚠️  Nenhum clímax identificado, usando fallback distribuído")
        climax_moments = _fallback_visual_analysis(frame_paths)

    if not climax_moments:
        print("   ❌ Falha total no Passo C")
        return [], "failed", None

    print(f"   ✅ Passo C OK: {len(climax_moments)} clímax identificados")
    for cm in climax_moments:
        mins = int(cm['timestamp'] // 60)
        secs = int(cm['timestamp'] % 60)
        print(f"      - {mins:02d}:{secs:02d} ({cm['reason']})")
    print(f"      Modo: Visual Gameplay (fluxo dedicado: IA → TTS → FFmpeg)")

    # Retorna dados especiais para o fluxo visual_gameplay
    extra_data = {
        'frame_paths': frame_paths,
        'climax_moments': climax_moments
    }
    return [], "visual_gameplay", extra_data


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
    # Limpa URL mantendo apenas o ID do vídeo
    clean_url = clean_youtube_url(url)
    if clean_url != url:
        print(f"\n🔗 URL original: {url}")
        print(f"🔗 URL limpa: {clean_url}")
    url = clean_url

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
        "--js-runtimes", "node",
        "--rm-cache-dir",
        "--extractor-args", "youtube:player_client=android,web",
        "-o", str(video_path),
        url
    ]
    ok, out, err = run_cmd(cmd)
    if not ok or not video_path.exists():
        print(f"⚠️  Falha no download principal, tentando fallback com player_client=web...")
        # Fallback: tentar apenas web client
        cmd_fallback = [
            sys.executable, "-m", "yt_dlp",
            *ffmpeg_loc,
            "--no-playlist",
            "--merge-output-format", "mp4",
            "-f", "bv*[height<=1080]+ba/b[height<=1080]",
            "--js-runtimes", "node",
            "--rm-cache-dir",
            "--extractor-args", "youtube:player_client=web",
            "-o", str(video_path),
            url
        ]
        ok, out, err = run_cmd(cmd_fallback)
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
        "--js-runtimes", "node",
        "--rm-cache-dir",
        "--extractor-args", "youtube:player_client=android,web",
        "-o", str(audio_path),
        url
    ]
    ok, out, err = run_cmd(cmd)
    if not ok:
        print(f"⚠️  Falha ao baixar áudio separado: {err}")

    # Baixar legendas do YouTube (com timestamps) - EXPANDIDO para incluir inglês
    sub_path = TEMP_DIR / f"{output_prefix}_subs.json3"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        *ffmpeg_loc,
        "--no-playlist",
        "--write-auto-sub",
        "--sub-lang", "pt,pt-BR,pt-PT,en",  # Inclui inglês como fallback
        "--sub-format", "json3",
        "--skip-download",
        "--js-runtimes", "node",
        "--rm-cache-dir",
        "--extractor-args", "youtube:player_client=android,web",
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
    print(f"✅ Legendas: {subtitle_path if subtitle_path else 'NÃO ENCONTRADAS (fallback será tentado)'}")

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
    GARANTE pontuação natural rigorosa (vírgulas, pontos, exclamações)
    para que o edge-tts gere respiração correta na narração.
    """
    # Limpar e preparar texto
    text = original_text.strip()
    words = text.split()

    if len(words) < 20:
        # Texto muito curto: garantir pontuação final
        result = text
        if not re.search(r'[.!?…]$', result):
            result += "."
        return result

    # Estratégia: pegar início (gancho), meio (pontos-chave) e fim (conclusão)
    # Proporção: 20% gancho, 60% desenvolvimento, 20% conclusão
    hook_end = max(1, len(words) // 5)
    conclusion_start = max(hook_end + 1, len(words) * 4 // 5)

    hook_part = ' '.join(words[:hook_end])
    middle_part = ' '.join(words[hook_end:conclusion_start])
    conclusion_part = ' '.join(words[conclusion_start:])

    # Identificar frases completas com palavras-chave de valor no meio
    value_keywords = ['passo', 'passos', 'etapa', 'etapas', 'como', 'porque', 'porquê',
                      'resultado', 'resultados', 'antes', 'depois', 'transformou',
                      'mudei', 'aprendi', 'descobri', 'testei', 'funciona', 'prova',
                      'provas', 'números', 'dados', 'estudo', 'pesquisa', 'ciência',
                      'técnica', 'método', 'estratégia', 'segredo', 'truque', 'dica']

    # Dividir middle_part em frases preservando pontuação
    sentences = re.split(r'(?<=[.!?…])\s+', middle_part)
    key_sentences = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        # Garantir que cada frase tenha pontuação final
        if not re.search(r'[.!?…]$', sentence):
            sentence += "."
        if any(kw in sentence.lower() for kw in value_keywords):
            key_sentences.append(sentence)

    # Construir roteiro viral com pontuação rigorosa
    # Gancho: primeiras palavras com tom de urgência/curiosidade + pontuação
    hook = hook_part.strip()
    if not re.search(r'[.!?…]$', hook):
        hook = hook.rstrip('.') + "!"

    # Desenvolvimento: frases-chave completas (máx 3-4), cada uma com pontuação
    if key_sentences:
        development = ' '.join(key_sentences[:4])
    else:
        # Fallback: pegar primeiras frases do middle_part garantindo pontuação
        fallback_sentences = re.split(r'(?<=[.!?…])\s+', middle_part[:300])
        fallback_sentences = [s.strip() for s in fallback_sentences if s.strip()]
        if fallback_sentences:
            development = ' '.join(fallback_sentences[:3])
            if not re.search(r'[.!?…]$', development):
                development += "."
        else:
            development = ""

    # Conclusão: última parte com call-to-action e pontuação
    conclusion = conclusion_part.strip()
    if not re.search(r'\b(inscreva|like|comente|siga|compartilhe|baixe|link)\b', conclusion.lower()):
        conclusion = conclusion.rstrip('.') + " — Fica a dica!"
    if not re.search(r'[.!?…]$', conclusion):
        conclusion += "."

    # Montar script completo
    parts = [hook]
    if development:
        parts.append(development)
    parts.append(conclusion)
    script = ' '.join(parts)

    # Limitar tamanho aproximado para duração alvo
    # Estimativa: ~150 palavras por minuto em português = 2.5 palavras/seg
    max_words = int(target_duration * 2.5)
    script_words = script.split()

    if len(script_words) > max_words:
        # Cortar no limite de palavras mas PRESERVAR pontuação da última frase completa
        truncated = ' '.join(script_words[:max_words])
        # Encontrar última pontuação final válida
        last_punct = max(
            truncated.rfind('.'),
            truncated.rfind('!'),
            truncated.rfind('?'),
            truncated.rfind('…')
        )
        if last_punct > max_words * 0.5:  # Se encontrou pontuação na segunda metade
            script = truncated[:last_punct + 1]
        else:
            # Sem pontuação boa: adicionar ponto final
            script = truncated.rstrip('.!?…') + "."

    # Garantia final: script deve terminar com pontuação
    if not re.search(r'[.!?…]$', script):
        script += "."

    return script


async def generate_tts_audio(text, output_path, voice=TTS_VOICE, rate=TTS_RATE, volume=TTS_VOLUME):
    """
    Gera áudio com edge-tts e retorna (sucesso, duração, eventos_word_boundary).
    Os eventos WordBoundary contêm timestamps exatos de cada palavra para legendas perfeitas.

    IMPORTANTE (edge-tts 7.x): .stream() só pode ser chamado UMA VEZ por instância.
    Fazemos um único loop que escreve o áudio no arquivo E captura WordBoundary/SentenceBoundary.
    """
    try:
        import edge_tts
    except ImportError:
        print("❌ edge-tts não instalado. Rode: pip install edge-tts")
        return False, 0, []

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)

        # Um único loop: escreve áudio no arquivo E captura eventos de fronteira
        # open() nativo não suporta async with; usa with sincronizado dentro do loop assíncrono
        word_boundary_events = []
        with open(output_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    word_boundary_events.append({
                        "text": chunk["text"],
                        "offset": chunk["offset"] / 10_000_000,  # ticks (100ns) → segundos
                        "duration": chunk["duration"] / 10_000_000,
                        "type": chunk["type"]
                    })

        if not output_path.exists() or output_path.stat().st_size == 0:
            return False, 0, []

        # Obter duração real do áudio gerado
        audio_duration = get_audio_duration(output_path)
        return True, audio_duration, word_boundary_events
    except Exception as e:
        print(f"❌ Erro ao gerar TTS: {e}")
        return False, 0, []


def create_ass_subtitle_from_tts_events(word_events, output_path):
    """
    Cria arquivo ASS usando eventos WordBoundary REAIS do edge-tts.
    - Um estilo único (Alignment=2, MarginV=320) para nunca subir ao meio da tela
    - Zero sobreposição: cada evento termina exatamente onde o próximo começa
    - Agrupa palavras em blocos de 3-5 palavras mantendo pontuação natural
    """
    if not word_events:
        return False

    # Agrupar eventos em blocos de 3-5 palavras (estilo TikTok/Shorts)
    WORDS_PER_CHUNK = 4
    chunks = []
    current_chunk_words = []
    current_chunk_start = None

    for event in word_events:
        word_text = event["text"].strip()
        if not word_text:
            continue

        if current_chunk_start is None:
            current_chunk_start = event["offset"]

        current_chunk_words.append(word_text)

        # Verificar se deve fechar o chunk: atingiu limite OU palavra tem pontuação final
        is_end_of_sentence = word_text.endswith(('.', '!', '?', '...'))
        if len(current_chunk_words) >= WORDS_PER_CHUNK or is_end_of_sentence:
            chunk_text = ' '.join(current_chunk_words)
            chunk_end = event["offset"] + event["duration"]
            chunks.append({
                'start': current_chunk_start,
                'end': chunk_end,
                'text': chunk_text
            })
            current_chunk_words = []
            current_chunk_start = None

    # Último chunk se houver palavras restantes
    if current_chunk_words:
        last_event = word_events[-1]
        chunk_end = last_event["offset"] + last_event["duration"]
        chunks.append({
            'start': current_chunk_start,
            'end': chunk_end,
            'text': ' '.join(current_chunk_words)
        })

    if not chunks:
        return False

    # Garantir zero sobreposição: end do atual = start do próximo
    for i in range(len(chunks) - 1):
        if chunks[i]['end'] > chunks[i + 1]['start']:
            chunks[i]['end'] = chunks[i + 1]['start']

    # Remover chunks muito curtos (< 0.3s)
    chunks = [c for c in chunks if c['end'] - c['start'] >= 0.3]

    # Estilo ASS - UM ÚNICO ESTILO: Alignment=2 (bottom-center), MarginV=320
    # Sem alternância de estilos - texto sempre na mesma posição, nunca sobe
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
    for chunk in chunks:
        start_str = format_ass_time(chunk['start'])
        end_str = format_ass_time(chunk['end'])
        text = chunk['text'].strip()
        # UM ÚNICO ESTILO - sem alternância, sem karaoke
        events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}")

    ass_content = ass_header + "\n".join(events)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass_content)

    return True


def create_ass_subtitle(segments, clip_start, clip_end, output_path):
    """
    Cria arquivo ASS para modo original (legendas do YouTube).
    Mesmo padrão: Alignment=2, MarginV=320, zero sobreposição.
    """
    # Filtrar segmentos que caem dentro do clip
    clip_segments = []
    for seg in segments:
        if seg['end'] <= clip_start:
            continue
        if seg['start'] >= clip_end:
            break
        # Ajustar tempos relativos ao início do clip
        adj_start = max(0, seg['start'] - clip_start)
        adj_end = min(clip_end - clip_start, seg['end'] - clip_start)
        if adj_end > adj_start and seg['text'].strip():
            clip_segments.append({
                'start': adj_start,
                'end': adj_end,
                'text': seg['text'].strip()
            })

    if not clip_segments:
        return False

    # Agrupar em blocos de 3-5 palavras
    WORDS_PER_CHUNK = 4
    chunks = []

    for seg in clip_segments:
        words = seg['text'].split()
        if not words:
            continue
        seg_duration = seg['end'] - seg['start']
        word_duration = seg_duration / len(words) if words else 0

        for i in range(0, len(words), WORDS_PER_CHUNK):
            chunk_words = words[i:i + WORDS_PER_CHUNK]
            chunk_start = seg['start'] + i * word_duration
            chunk_end = min(seg['start'] + (i + len(chunk_words)) * word_duration, seg['end'])
            chunks.append({
                'start': chunk_start,
                'end': chunk_end,
                'text': ' '.join(chunk_words)
            })

    if not chunks:
        return False

    # Zero sobreposição
    for i in range(len(chunks) - 1):
        if chunks[i]['end'] > chunks[i + 1]['start']:
            chunks[i]['end'] = chunks[i + 1]['start']

    chunks = [c for c in chunks if c['end'] - c['start'] >= 0.3]

    # Mesmo estilo único: Alignment=2, MarginV=320
    ass_header = f"""[Script Info]
Title: Cortes Virais - Áudio Original
ScriptType: v4.00+
PlayResX: {VERTICAL_WIDTH}
PlayResY: {VERTICAL_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,62,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,40,40,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def format_ass_time(seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        centiseconds = int((secs - int(secs)) * 100)
        return f"{hours}:{minutes:02d}:{int(secs):02d}.{centiseconds:02d}"

    events = []
    for chunk in chunks:
        start_str = format_ass_time(chunk['start'])
        end_str = format_ass_time(chunk['end'])
        text = chunk['text'].strip()
        events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}")

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


def cut_and_export_vertical(video_path, clip, segments, output_path, clip_num, audio_mode="original"):
    """Corta vídeo, converte para vertical 9:16, adiciona legendas e normaliza áudio."""
    print(f"\n🎬 Processando corte {clip_num}: {clip['start']:.1f}s - {clip['end']:.1f}s ({clip['duration']:.1f}s) [Modo: {audio_mode}]")

    ass_path = None
    tts_audio_path = None
    final_duration = clip['duration']
    video_trim_start = clip['start']
    tts_word_events = []

    if audio_mode == "ia":
        # Modo IA: gerar narração e sincronizar
        print(f"🤖 Gerando roteiro viral e narração com edge-tts ({TTS_VOICE})...")

        # Gerar roteiro viral baseado no texto do clip
        viral_script = generate_viral_script(clip['text'], clip['duration'])
        print(f"📝 Roteiro gerado ({len(viral_script.split())} palavras): {viral_script[:150]}...")

        # Gerar áudio TTS com eventos WordBoundary para sincronia exata
        tts_audio_path = TEMP_DIR / f"clip_{clip_num}_tts.mp3"
        success, tts_duration, word_events = asyncio.run(generate_tts_audio(viral_script, tts_audio_path))

        if not success or not tts_audio_path.exists():
            print("⚠️  Falha no TTS, usando áudio original...")
            audio_mode = "original"
        else:
            print(f"🔊 Áudio TTS gerado: {tts_duration:.1f}s ({len(word_events)} eventos WordBoundary)")
            final_duration = tts_duration
            video_trim_start = clip['start']
            tts_word_events = word_events

            # Criar legendas sincronizadas com eventos REAIS do edge-tts
            ass_path = TEMP_DIR / f"clip_{clip_num}_subs.ass"
            if not create_ass_subtitle_from_tts_events(tts_word_events, ass_path):
                print("⚠️  Falha ao criar legendas sincronizadas com WordBoundary")
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
        # O vídeo será esticado/acelerado para durar EXATAMENTE o mesmo que o áudio TTS
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
        # IMPORTANTE: -t usa a duração REAL do áudio TTS (final_duration), não a do clip original
        # Isso garante que o vídeo cortado tenha exatamente a duração do áudio narrado
        cmd = [
            ffmpeg_cmd, "-y",
            "-ss", str(video_trim_start),
            "-t", str(final_duration),  # Duração exata do áudio TTS
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
        return cut_simple_vertical(video_path, clip, segments, ass_path, output_path, clip_num, audio_mode, tts_audio_path, final_duration)

    print(f"✅ Corte salvo: {output_path}")
    return True


def cut_simple_vertical(video_path, clip, segments, ass_path, output_path, clip_num, audio_mode="original", tts_audio_path=None, tts_duration=None):
    """Versão simplificada sem fundo desfocado (fallback)."""
    print(f"🔄 Tentando versão simplificada...")

    vf = f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=decrease,pad={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"
    if ass_path and ass_path.exists():
        vf += f",subtitles='{ass_path.as_posix()}'"

    ffmpeg_cmd = FFMPEG.as_posix()

    if audio_mode == "ia" and tts_audio_path and tts_audio_path.exists():
        # Usar duração REAL do TTS para -t (sincronia exata)
        actual_tts_duration = tts_duration if tts_duration else get_audio_duration(tts_audio_path)
        if actual_tts_duration <= 0:
            actual_tts_duration = clip['duration']
        speed_ratio = clip['duration'] / actual_tts_duration if actual_tts_duration > 0 else 1.0
        vf = f"setpts={1/speed_ratio:.6f}*PTS," + vf
        cmd = [
            ffmpeg_cmd, "-y",
            "-ss", str(clip['start']),
            "-t", str(actual_tts_duration),  # Duração exata do áudio TTS
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

    # Carregar API key do Groq do .env
    groq_api_key = None
    env_path = Path(".env")
    if env_path.exists():
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('GROQ_API_KEY='):
                        groq_api_key = line.split('=', 1)[1].strip().strip('"\'')
                        break
        except Exception:
            pass

    if groq_api_key:
        print("🔑 GROQ_API_KEY carregada do .env")
    else:
        print("⚠️  GROQ_API_KEY não encontrada no .env (Passo B será pulado)")

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

    # 3. Carregar legendas com sistema de fallback em 3 passos
    segments, subtitle_source, extra_data = load_subtitles_with_fallback(subtitle_path, video_path, TEMP_DIR, groq_api_key)

    # === FLUXO ESPECIAL: VISUAL GAMEPLAY ===
    if subtitle_source == "visual_gameplay":
        print("\n" + "=" * 60)
        print("🎮 MODO VISUAL GAMEPLAY ATIVADO")
        print("   Fluxo: Frames → IA seleciona clímax → TTS → FFmpeg")
        print("=" * 60)

        # Força modo IA para gameplay visual
        audio_mode = "ia"

        # Gera cortes diretamente dos clímax identificados
        climax_moments = extra_data.get('climax_moments', [])
        generated_paths = generate_climax_clips_from_visual(
            video_path,
            extra_data.get('frame_paths', []),
            climax_moments,
            OUTPUT_DIR,
            clip_num_start=1
        )

        # Limpeza
        print("\n🧹 Limpando arquivos temporários...")
        import shutil
        if TEMP_DIR.exists():
            shutil.rmtree(TEMP_DIR)

        # Resumo final
        print("\n" + "=" * 60)
        print(f"✅ CONCLUÍDO (Visual Gameplay): {len(generated_paths)} cortes gerados")
        for p in generated_paths:
            print(f"   📹 {p}")
        print(f"📁 Pasta de saída: {OUTPUT_DIR.absolute()}")
        print("=" * 60)

        if generated_paths:
            print("\n📋 Próximos passos:")
            print("  1. Revise os cortes na pasta 'cortes_prontos/'")
            print("  2. Adicione hashtags e descrição para TikTok/Shorts")
            print("  3. Publique nos melhores horários (18-21h costuma funcionar bem)")
        return

    # === FLUXO NORMAL: TRANSCRIÇÃO/LEGENDAS ===
    if not segments:
        print("❌ Não foi possível obter transcrição/legendas do vídeo após todos os fallbacks.")
        print("   Dica: use vídeos com legendas automáticas do YouTube ativadas ou configure GROQ_API_KEY no .env")
        sys.exit(1)

    print(f"📝 Legendas carregadas: {len(segments)} segmentos (fonte: {subtitle_source})")

    # Se modo IA e fonte for groq_audio com poucas palavras, avisa
    if subtitle_source == "groq_audio" and audio_mode == "original":
        total_words = sum(len(s['text'].split()) for s in segments)
        if total_words < MIN_WORDS_FOR_AUDIO_TRANSCRIPTION * 2:
            print(f"⚠️  Poucas palavras detectadas ({total_words}). Considere usar --audio ia")

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
