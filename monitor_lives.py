#!/usr/bin/env python3
"""
Monitor de Lives em Tempo Real - Fábrica de Cortes
==================================================
Monitora lives ao vivo (YouTube, Twitch, Kick) e gera clips automáticos
baseados em picos de áudio e explosões de chat.

Fluxo:
1. Grava live em segmentos de 10s (ring buffer 2 min)
2. Monitora volume de áudio (FFmpeg volumedetect/ebur128) + chat velocity
3. Detecta gatilhos: picos de áudio/risada ou palavras-chave no chat
4. Junta últimos 45s do buffer -> clipe bruto
5. Pós-processa: split-screen 9:16 + transcrição Groq Whisper + legendas .ass dinâmicas
6. Salva em cortes_prontos/live_clipe_YYYYMMDD_HHMMSS.mp4

Autor: Fábrica de Cortes
"""

import subprocess
import sys
import os
import re
import time
import json
import threading
import queue
import signal
import argparse
from pathlib import Path
from datetime import datetime
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

# Adiciona o diretório do projeto ao path para importar constantes do gerar_cortes
sys.path.insert(0, str(Path(__file__).parent))

# ========== IMPORTS DO PROJETO ==========
try:
    from gerar_cortes import (
        FFMPEG, VERTICAL_WIDTH, VERTICAL_HEIGHT, TEMP_DIR, OUTPUT_DIR,
        GROQ_API_URL, GROQ_MODEL, GROQ_CHAT_URL,
        DEFAULT_GROQ_VISION_MODEL, DEFAULT_GROQ_TEXT_MODEL,
        MAX_IMAGES_PER_REQUEST, MIN_CLIMAX_SCORE,
        GAMEPLAY_SUBTITLE_WORDS_PER_CHUNK,
        GAMEPLAY_WATERMARK_PATH,
        clean_youtube_url,
        run_cmd,
        get_video_duration,
        transcribe_audio_groq,
        create_ass_subtitle_gameplay,
        detect_webcam_position_ai,
        load_env_file,
        get_vision_model,
        groq_chat,
        extract_json,
    )
except ImportError as e:
    print(f"❌ Erro ao importar gerar_cortes: {e}")
    sys.exit(1)

# ========== CONSTANTES DO MONITOR ==========
BUFFER_SEGMENT_DURATION = 10          # Duração de cada segmento do buffer (segundos)
BUFFER_MAX_DURATION = 120             # Ring buffer: mantém últimos 120s (2 min)
CLIP_DURATION = 45                    # Duração do clipe final ao detectar gatilho (segundos)
AUDIO_PEAK_THRESHOLD_DB = -20         # Limite de pico de áudio em dB (ajustar conforme teste)
CHAT_VELOCITY_THRESHOLD = 30          # Mensagens por minuto para considerar "explosão"
CHAT_KEYWORDS = ["clipa", "kkkk", "clipe", "corta", "salva", "top", "insano", "monstro", "god", "nerf", "buff", "bug", "fail", "win", "clutch", "ace", "pentakill", "quadrakill", "triplekill", "doublekill", "firstblood"]
RING_BUFFER_MAX_SEGMENTS = BUFFER_MAX_DURATION // BUFFER_SEGMENT_DURATION  # 12 segmentos
COOLDOWN_BETWEEN_CLIPS = 60           # Cooldown mínimo entre clips (segundos)

# Diretórios
LIVE_BUFFER_DIR = TEMP_DIR / "live_buffer"
LIVE_CLIPS_DIR = OUTPUT_DIR

# ========== DATACLASSES ==========

@dataclass
class BufferSegment:
    """Representa um segmento de 10s do ring buffer"""
    path: Path
    start_time: float      # Timestamp absoluto (epoch) do início do segmento
    duration: float = BUFFER_SEGMENT_DURATION
    audio_peak_db: float = -60.0
    chat_message_count: int = 0

@dataclass
class LiveConfig:
    """Configuração da live a monitorar"""
    url: str
    platform: str          # "youtube", "twitch", "kick"
    webcam_position: Dict  # Dict com x,y,w,h normalizados ou None
    streamer_name: str = ""

@dataclass
class TriggerEvent:
    """Evento que disparou a criação do clip"""
    timestamp: float
    trigger_type: str      # "audio_peak", "chat_velocity", "chat_keyword"
    details: str
    audio_peak_db: float = -60.0
    chat_msg_count: int = 0

# ========== VARIÁVEIS GLOBAIS ==========
stop_event = threading.Event()
buffer_queue = queue.Queue()
ring_buffer: deque = deque(maxlen=RING_BUFFER_MAX_SEGMENTS)
last_clip_time = 0.0
clip_counter = 0
current_live_config: Optional[LiveConfig] = None
groq_api_key: Optional[str] = None

# ========== FUNÇÕES AUXILIARES ==========

def log(msg: str, level: str = "INFO"):
    """Log com timestamp"""
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌", "OK": "✅", "CLIP": "🎬"}.get(level, "📝")
    print(f"[{ts}] {prefix} {msg}")

def ensure_dirs():
    """Cria diretórios necessários"""
    LIVE_BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

def get_segment_filename() -> Path:
    """Gera nome de arquivo para segmento do buffer"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return LIVE_BUFFER_DIR / f"seg_{ts}.ts"

def get_clip_filename() -> Path:
    """Gera nome de arquivo para clip final"""
    global clip_counter
    clip_counter += 1
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LIVE_CLIPS_DIR / f"live_clipe_{ts}_{clip_counter:03d}.mp4"

# ========== GRAVAÇÃO DE BUFFER (THREAD 1) ==========

def record_buffer_segment(segment_path: Path, stream_url: str, duration: int = BUFFER_SEGMENT_DURATION) -> bool:
    """
    Grava um segmento da live via yt-dlp + FFmpeg.
    Usa -f "bestvideo+bestaudio" para YouTube, streamlink para Twitch/Kick se disponível.
    """
    # Para YouTube, usa yt-dlp direto com formato de live
    # Para Twitch/Kick, tenta streamlink primeiro
    cmd = [
        FFMPEG.as_posix(), "-y",
        "-hide_banner", "-loglevel", "error",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-timeout", "10000000",
        "-i", stream_url,
        "-t", str(duration),
        "-c", "copy",           # Copy codec para velocidade (segmento TS)
        "-f", "mpegts",         # MPEG-TS container para concatenação fácil
        str(segment_path)
    ]

    ok, _, err = run_cmd(cmd, timeout=duration + 10)
    if not ok:
        log(f"Falha ao gravar segmento: {err[:200]}", "ERROR")
        return False
    return True

def buffer_recorder_thread(stream_url: str):
    """Thread que grava continuamente segmentos de 10s no ring buffer"""
    log("Thread de gravação iniciada", "OK")

    while not stop_event.is_set():
        segment_path = get_segment_filename()
        start_time = time.time()

        # Grava segmento
        success = record_buffer_segment(segment_path, stream_url)

        if success and segment_path.exists():
            # Analisa pico de áudio do segmento recém-gravado
            peak_db = analyze_audio_peak(segment_path)

            segment = BufferSegment(
                path=segment_path,
                start_time=start_time,
                audio_peak_db=peak_db
            )

            ring_buffer.append(segment)
            log(f"Segmento gravado: {segment_path.name} | Pico: {peak_db:.1f} dB")

            # Verifica gatilho de áudio
            if peak_db > AUDIO_PEAK_THRESHOLD_DB:
                trigger = TriggerEvent(
                    timestamp=start_time,
                    trigger_type="audio_peak",
                    details=f"Pico de áudio detectado: {peak_db:.1f} dB (limite: {AUDIO_PEAK_THRESHOLD_DB} dB)",
                    audio_peak_db=peak_db
                )
                buffer_queue.put(("trigger", trigger))
        else:
            log("Falha ao gravar segmento, tentando novamente...", "WARN")
            time.sleep(2)

        # Pequena pausa para não saturar CPU
        time.sleep(0.5)

    log("Thread de gravação finalizada")

def analyze_audio_peak(video_path: Path) -> float:
    """
    Analisa pico de volume do arquivo usando FFmpeg volumedetect.
    Retorna o peak level em dB.
    """
    cmd = [
        FFMPEG.as_posix(), "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-af", "volumedetect",
        "-f", "null", "-"
    ]

    ok, out, err = run_cmd(cmd, timeout=15)

    # Parse output do volumedetect
    # Exemplo: "mean_volume: -24.5 dB\nmax_volume: -5.2 dB"
    peak_db = -60.0
    for line in (out + err).split('\n'):
        if 'max_volume:' in line:
            try:
                peak_db = float(line.split(':')[1].strip().replace(' dB', ''))
            except:
                pass
        elif 'mean_volume:' in line:
            try:
                mean_db = float(line.split(':')[1].strip().replace(' dB', ''))
                # Usa o maior entre mean e max para detecção
                peak_db = max(peak_db, mean_db)
            except:
                pass

    return peak_db

# ========== MONITORAMENTO DE CHAT (THREAD 2) ==========

def chat_monitor_thread(platform: str, url: str):
    """Thread que monitora chat da live para detectar explosões de mensagens"""
    log(f"Thread de chat iniciada ({platform})", "OK")

    try:
        import chat_downloader
    except ImportError:
        log("chat-downloader não instalado. Monitoramento de chat desabilitado.", "WARN")
        log("Instale com: pip install chat-downloader", "WARN")
        return

    last_check = time.time()
    message_timestamps = deque(maxlen=300)  # Últimos 5 minutos

    # Configura downloader baseado na plataforma
    if platform == "youtube":
        chat = chat_downloader.ChatDownloader().get_chat(url, message_types=["text_message"])
    elif platform == "twitch":
        chat = chat_downloader.ChatDownloader().get_chat(url, message_types=["text_message"])
    elif platform == "kick":
        chat = chat_downloader.ChatDownloader().get_chat(url, message_types=["text_message"])
    else:
        log(f"Plataforma {platform} não suportada para chat", "ERROR")
        return

    for message in chat:
        if stop_event.is_set():
            break

        try:
            msg_text = message.get("message", "").lower()
            msg_time = time.time()
            message_timestamps.append(msg_time)

            # Verifica palavras-chave
            for keyword in CHAT_KEYWORDS:
                if keyword in msg_text:
                    trigger = TriggerEvent(
                        timestamp=msg_time,
                        trigger_type="chat_keyword",
                        details=f"Palavra-chave detectada: '{keyword}' em '{message.get('message', '')[:100]}'",
                        chat_msg_count=len(message_timestamps)
                    )
                    buffer_queue.put(("trigger", trigger))
                    break

            # Verifica velocidade do chat (a cada 10s)
            if msg_time - last_check >= 10:
                recent_msgs = [t for t in message_timestamps if msg_time - t <= 60]
                msg_per_min = len(recent_msgs)

                if msg_per_min >= CHAT_VELOCITY_THRESHOLD:
                    trigger = TriggerEvent(
                        timestamp=msg_time,
                        trigger_type="chat_velocity",
                        details=f"Explosão de chat: {msg_per_min} msg/min (limite: {CHAT_VELOCITY_THRESHOLD})",
                        chat_msg_count=msg_per_min
                    )
                    buffer_queue.put(("trigger", trigger))

                last_check = msg_time

        except Exception as e:
            log(f"Erro processando mensagem de chat: {e}", "ERROR")
            continue

    log("Thread de chat finalizada")

# ========== PROCESSAMENTO DE CLIPS (THREAD 3) ==========

def extract_last_n_seconds_from_buffer(n_seconds: float, output_path: Path) -> bool:
    """
    Extrai os últimos N segundos do ring buffer concatenando segmentos TS.
    Retorna True se sucesso.
    """
    if not ring_buffer:
        log("Buffer vazio, não há o que extrair", "ERROR")
        return False

    # Calcula qual segmento iniciar (precisa cobrir n_seconds + margem)
    current_time = time.time()
    needed_duration = n_seconds + 5  # Margem de 5s

    # Seleciona segmentos do mais antigo para o mais novo que cobrem o período
    selected_segments = []
    accumulated = 0.0

    for seg in reversed(ring_buffer):
        selected_segments.insert(0, seg)
        accumulated += seg.duration
        if accumulated >= needed_duration:
            break

    if not selected_segments:
        log("Nenhum segmento disponível no buffer", "ERROR")
        return False

    log(f"Extraindo clip: {len(selected_segments)} segmentos ({accumulated:.1f}s)")

    # Cria arquivo de lista para concatenação
    list_file = LIVE_BUFFER_DIR / "concat_list.txt"
    with open(list_file, 'w') as f:
        for seg in selected_segments:
            f.write(f"file '{seg.path.as_posix()}'\n")

    # Concatena segmentos e corta exatamente os últimos n_seconds
    # Primeiro concatena tudo, depois corta o final
    temp_concat = LIVE_BUFFER_DIR / "temp_concat.ts"

    cmd_concat = [
        FFMPEG.as_posix(), "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(temp_concat)
    ]

    ok, _, err = run_cmd(cmd_concat, timeout=30)
    if not ok:
        log(f"Falha ao concatenar: {err[:200]}", "ERROR")
        return False

    # Agora corta os últimos n_seconds
    concat_duration = get_video_duration(temp_concat)
    start_cut = max(0, concat_duration - n_seconds)

    cmd_cut = [
        FFMPEG.as_posix(), "-y",
        "-hide_banner", "-loglevel", "error",
        "-ss", str(start_cut),
        "-t", str(n_seconds),
        "-i", str(temp_concat),
        "-c", "copy",
        str(output_path)
    ]

    ok, _, err = run_cmd(cmd_cut, timeout=30)

    # Limpa temp
    try:
        temp_concat.unlink(missing_ok=True)
        list_file.unlink(missing_ok=True)
    except:
        pass

    if not ok:
        log(f"Falha ao cortar clip: {err[:200]}", "ERROR")
        return False

    return True

def transcribe_clip_groq(clip_path: Path, api_key: str) -> List[Dict]:
    """
    Transcreve o clip usando Groq Whisper com word timestamps.
    Retorna lista de segmentos com palavras e timestamps.
    """
    log("Transcrevendo clip com Groq Whisper (word timestamps)...")

    try:
        import requests
    except ImportError:
        log("requests não instalado", "ERROR")
        return []

    try:
        with open(clip_path, 'rb') as f:
            files = {'file': (clip_path.name, f, 'audio/mpeg')}
            data = {
                'model': GROQ_MODEL,
                'response_format': 'verbose_json',
                'timestamp_granularities[]': 'word',  # Word-level timestamps!
                'language': 'pt'
            }
            headers = {'Authorization': f'Bearer {api_key}'}

            response = requests.post(GROQ_API_URL, files=files, data=data, headers=headers, timeout=120)

        if response.status_code != 200:
            log(f"Erro API Groq: {response.status_code} - {response.text[:200]}", "ERROR")
            return []

        result = response.json()

        # Converte word timestamps para segmentos estilo projeto
        segments = []
        if 'words' in result:
            words = result['words']
            if words:
                # Agrupa palavras em segmentos de ~3s ou por pausa
                current_seg = {'start': words[0]['start'], 'end': words[0]['end'], 'text': words[0]['word'], 'words': [words[0]]}

                for w in words[1:]:
                    # Se pausa > 1s ou segmento > 3s, fecha segmento atual
                    if (w['start'] - current_seg['end'] > 1.0) or (w['end'] - current_seg['start'] > 3.0):
                        segments.append({
                            'start': current_seg['start'],
                            'end': current_seg['end'],
                            'text': current_seg['text']
                        })
                        current_seg = {'start': w['start'], 'end': w['end'], 'text': w['word'], 'words': [w]}
                    else:
                        current_seg['end'] = w['end']
                        current_seg['text'] += ' ' + w['word']
                        current_seg['words'].append(w)

                if current_seg['text']:
                    segments.append({
                        'start': current_seg['start'],
                        'end': current_seg['end'],
                        'text': current_seg['text']
                    })

        log(f"Transcrição OK: {len(segments)} segmentos")
        return segments

    except Exception as e:
        log(f"Erro na transcrição: {e}", "ERROR")
        return []

def create_dynamic_ass_subtitle(segments: List[Dict], clip_duration: float, output_path: Path) -> bool:
    """
    Cria legenda .ass dinâmica: destaca palavra por palavra conforme é falada.
    Estilo: centro da tela, negrito, contorno preto, amarelo vibrante.
    """
    if not segments:
        return False

    # Header ASS otimizado para word-by-word highlighting
    ass_header = f"""[Script Info]
Title: Live Clip - Dynamic Subtitles
ScriptType: v4.00+
PlayResX: {VERTICAL_WIDTH}
PlayResY: {VERTICAL_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,72,&H00FFFF00,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,5,40,40,1100,1
Style: Highlight,Arial,72,&H0000FF00,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,5,40,40,1100,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def format_ass_time(seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centisecs = int((seconds - int(seconds)) * 100)
        return f"{hours}:{minutes:02d}:{secs:02d}.{centisecs:02d}"

    events = []

    for seg in segments:
        # Para cada segmento, cria eventos palavra por palavra
        if 'words' in seg and seg['words']:
            # Tem word timestamps - cria highlight por palavra
            for w in seg['words']:
                start = format_ass_time(w['start'])
                end = format_ass_time(w['end'])
                # Palavra atual em highlight (verde), resto em default (amarelo)
                # Usa inline style override para highlight da palavra atual
                text = f"{{\\rHighlight}}{w['word']}{{\\rDefault}}"
                events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
        else:
            # Fallback: segmento inteiro
            start = format_ass_time(seg['start'])
            end = format_ass_time(seg['end'])
            text = seg['text']
            events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(ass_header)
            f.write('\n'.join(events))
        log(f"Legenda dinâmica criada: {output_path}")
        return True
    except Exception as e:
        log(f"Erro ao criar legenda: {e}", "ERROR")
        return False

def process_gameplay_layout(input_path: Path, output_path: Path, webcam_pos: Optional[Dict], ass_path: Optional[Path]) -> bool:
    """
    Aplica layout tela dividida (split-screen) 9:16:
    - Topo: webcam crop 1080x620
    - Base: gameplay crop central 9:16 -> 1080x1300
    - vstack = 1080x1920
    - Áudio: loudnorm + fade rápido 0.2s
    - Watermark se existir
    """
    log("Aplicando layout tela dividida (split-screen)...")

    ffmpeg_cmd = FFMPEG.as_posix()
    watermark_path = Path(GAMEPLAY_WATERMARK_PATH)
    has_watermark = watermark_path.exists()

    if webcam_pos is None:
        # Modo sem webcam: apenas gameplay central 9:16
        vf = f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}"

        if ass_path and ass_path.exists():
            vf += f",subtitles='{ass_path.as_posix()}'"

        if has_watermark:
            vf += f",movie='{watermark_path.as_posix()}'[wm];[in][wm]overlay=20:20"

        # Fade rápido apenas áudio (0.2s)
        af = f"loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,afade=t=out:st={clip_duration-0.2:.2f}:d=0.2"

        cmd = [
            ffmpeg_cmd, "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-af", af,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path)
        ]
    else:
        # Tela dividida com webcam
        wc_x = f"iw*{webcam_pos['x']:.6f}"
        wc_y = f"ih*{webcam_pos['y']:.6f}"
        wc_w = f"iw*{webcam_pos['w']:.6f}"
        wc_h = f"ih*{webcam_pos['h']:.6f}"

        filter_complex = (
            f"[0:v]crop={wc_w}:{wc_h}:{wc_x}:{wc_y},"
            f"scale={VERTICAL_WIDTH}:620:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:620[cam];"
            f"[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:1300:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:1300[game];"
            f"[cam][game]vstack=inputs=2[vout]"
        )

        map_v = "[vout]"

        # Adiciona legendas
        if ass_path and ass_path.exists():
            filter_complex += f";[vout]subtitles='{ass_path.as_posix()}'[vsub]"
            map_v = "[vsub]"

        # Adiciona watermark
        if has_watermark:
            filter_complex += f";movie='{watermark_path.as_posix()}'[wm];[vsub][wm]overlay=20:20[vfinal]"
            map_v = "[vfinal]"

        # Fade rápido apenas áudio
        af = f"loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,afade=t=out:st={clip_duration-0.2:.2f}:d=0.2"

        cmd = [
            ffmpeg_cmd, "-y",
            "-i", str(input_path),
            "-filter_complex", filter_complex,
            "-map", map_v,
            "-map", "0:a?",
            "-af", af,
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path)
        ]

    ok, _, err = run_cmd(cmd, timeout=60)

    if not ok:
        log(f"Erro no FFmpeg layout: {err[:500]}", "ERROR")
        return False

    log(f"Layout aplicado com sucesso: {output_path}", "OK")
    return True

def process_trigger(trigger: TriggerEvent):
    """Processa um gatilho e gera o clip final"""
    global last_clip_time, clip_duration

    # Cooldown
    now = time.time()
    if now - last_clip_time < COOLDOWN_BETWEEN_CLIPS:
        log(f"Cooldown ativo, ignorando gatilho ({COOLDOWN_BETWEEN_CLIPS - (now - last_clip_time):.0f}s restantes)", "WARN")
        return

    last_clip_time = now

    log(f"🎯 GATILHO DETECTADO: {trigger.trigger_type} - {trigger.details}", "CLIP")

    # 1. Extrai últimos 45s do buffer
    raw_clip_path = LIVE_BUFFER_DIR / f"raw_clip_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ts"
    clip_duration = CLIP_DURATION

    if not extract_last_n_seconds_from_buffer(clip_duration, raw_clip_path):
        return

    # 2. Transcreve com Groq Whisper (word timestamps)
    segments = transcribe_clip_groq(raw_clip_path, groq_api_key)
    if not segments:
        log("Falha na transcrição, pulando clip", "ERROR")
        return

    # 3. Cria legenda dinâmica .ass
    ass_path = LIVE_BUFFER_DIR / f"subs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ass"
    if not create_dynamic_ass_subtitle(segments, clip_duration, ass_path):
        ass_path = None

    # 4. Aplica layout tela dividida + pós-processamento
    final_clip_path = get_clip_filename()

    if not process_gameplay_layout(raw_clip_path, final_clip_path, current_live_config.webcam_position, ass_path):
        return

    # 5. Limpeza
    try:
        raw_clip_path.unlink(missing_ok=True)
        if ass_path:
            ass_path.unlink(missing_ok=True)
    except:
        pass

    log(f"🎉 CLIP PRONTO: {final_clip_path.name}", "OK")

def trigger_processor_thread():
    """Thread que processa gatilhos da queue"""
    log("Thread de processamento de gatilhos iniciada", "OK")

    while not stop_event.is_set():
        try:
            item = buffer_queue.get(timeout=1)
            if item[0] == "trigger":
                process_trigger(item[1])
            buffer_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            log(f"Erro no processador de gatilhos: {e}", "ERROR")

    log("Thread de processamento finalizada")

# ========== RESOLUÇÃO DE URL DA LIVE ==========

def resolve_live_url(url: str) -> tuple[str, str]:
    """
    Resolve URL da live para stream direto.
    Retorna (stream_url, platform)
    """
    url = url.strip()

    # YouTube
    if "youtube.com" in url or "youtu.be" in url:
        # Para lives do YouTube, yt-dlp resolve direto
        # Extrai ID do vídeo
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/live/)([a-zA-Z0-9_-]{11})',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                video_id = match.group(1)
                return f"https://www.youtube.com/watch?v={video_id}", "youtube"
        return url, "youtube"

    # Twitch
    if "twitch.tv" in url:
        # Extrai nome do canal
        match = re.search(r'twitch\.tv/([a-zA-Z0-9_]+)', url)
        if match:
            channel = match.group(1)
            return f"https://twitch.tv/{channel}", "twitch"
        return url, "twitch"

    # Kick
    if "kick.com" in url:
        match = re.search(r'kick\.com/([a-zA-Z0-9_]+)', url)
        if match:
            channel = match.group(1)
            return f"https://kick.com/{channel}", "kick"
        return url, "kick"

    return url, "unknown"

def detect_webcam_position_ai_live(api_key: str) -> Optional[Dict]:
    """
    Extrai 1 frame do segmento mais recente do buffer e usa Groq Vision
    para detectar a posição da webcam do streamer.

    Retorna dict com x, y, w, h normalizados (0.0-1.0) ou None se não encontrar/falhar.
    """
    import tempfile
    import base64

    if not ring_buffer:
        log("Buffer vazio, não há frame para analisar", "WARN")
        return None

    # Usa o segmento mais recente do buffer
    latest_segment = ring_buffer[-1]
    video_path = latest_segment.path

    if not video_path.exists():
        log("Segmento mais recente não encontrado", "WARN")
        return None

    log("🔍 Detectando webcam automaticamente com IA...")

    try:
        # Obter duração do segmento
        video_duration = get_video_duration(video_path)
        if video_duration <= 0:
            return None

        # Extrair frame no meio do segmento (50%)
        timestamp = video_duration * 0.5

        # Criar arquivo temporário para o frame
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            frame_path = tmp.name

        # Extrair frame com ffmpeg
        cmd = [
            FFMPEG.as_posix(), "-y",
            "-ss", str(timestamp),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            "-vf", "scale=1280:720",
            frame_path
        ]
        ok, _, _ = run_cmd(cmd)
        if not ok:
            log("Falha ao extrair frame para detecção", "WARN")
            return None

        try:
            # Ler e codificar frame em base64
            with open(frame_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('utf-8')
        finally:
            # Limpar arquivo temporário
            try:
                os.unlink(frame_path)
            except:
                pass

        # Prompt para detecção de webcam
        prompt = (
            "Analise esta imagem de um vídeo de gameplay/streamer (16:9). "
            "Procure pela facecam/webcam do streamer - uma janela retangular menor "
            "sobreposta ao gameplay, geralmente mostrando o rosto do jogador.\n\n"
            "Se ENCONTRAR uma facecam/webcam, responda APENAS com JSON:\n"
            '{"found": true, "x": 0.XX, "y": 0.YY, "w": 0.WW, "h": 0.HH}\n'
            "Onde x,y são o canto superior esquerdo normalizado (0.0 a 1.0) e w,h são "
            "largura e altura normalizadas da facecam.\n\n"
            "Se NÃO ENCONTRAR facecam/webcam visível (apenas gameplay puro, sem câmera do streamer), "
            'responda APENAS: {"found": false}\n\n'
            "Não adicione texto extra, markdown ou explicações."
        )

        content = [{
            "type": "text",
            "text": prompt
        }, {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        }]

        model = get_vision_model()
        answer = groq_chat([{"role": "user", "content": content}], api_key, model, temperature=0.1, max_tokens=200)

        if not answer:
            log("IA não retornou resposta", "WARN")
            return None

        result = extract_json(answer)
        if not result or not isinstance(result, dict):
            log("Resposta da IA não é JSON válido", "WARN")
            return None

        if not result.get('found', False):
            log("IA não encontrou webcam na imagem", "WARN")
            return None

        # Validar coordenadas
        x = float(result.get('x', 0))
        y = float(result.get('y', 0))
        w = float(result.get('w', 0))
        h = float(result.get('h', 0))

        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
            log(f"Coordenadas inválidas: x={x}, y={y}, w={w}, h={h}", "WARN")
            return None

        # Garantir que o crop não saia dos limites
        if x + w > 1.0 or y + h > 1.0:
            log(f"Crop fora dos limites: x+w={x+w:.3f}, y+h={y+h:.3f}", "WARN")
            return None

        # Validação extra: webcam deve ter tamanho mínimo razoável
        if w < 0.05 or h < 0.05:
            log(f"Webcam muito pequena: w={w:.3f}, h={h:.3f}", "WARN")
            return None

        log(f"✅ Webcam detectada: x={x:.3f}, y={y:.3f}, w={w:.3f}, h={h:.3f}", "OK")
        return {'x': x, 'y': y, 'w': w, 'h': h, 'name': 'Detecção automática (IA)'}

    except Exception as e:
        log(f"Erro na detecção automática: {e}", "ERROR")
        return None


# ========== INTERFACE DE USUÁRIO ==========

def ask_webcam_position_live(api_key: Optional[str] = None) -> Optional[Dict]:
    """Pergunta posição da webcam para live (7 opções completas)"""
    print("\n🎮 Posicionamento da Webcam (Facecam do Streamer)")
    print("   O vídeo original é 16:9 (horizontal). Onde fica a câmera do streamer?")
    print()
    print("   [1] Canto inferior esquerdo (x=0.02*iw, y=0.70*ih, w=0.25*iw, h=0.28*ih)")
    print("   [2] Canto superior esquerdo (x=0.02*iw, y=0.02*ih, w=0.25*iw, h=0.28*ih)")
    print("   [3] Canto inferior direito (x=0.73*iw, y=0.70*ih, w=0.25*iw, h=0.28*ih)")
    print("   [4] Canto superior direito (x=0.73*iw, y=0.02*ih, w=0.25*iw, h=0.28*ih)")
    print("   [5] Meio-direita / acima do minimapa (x=0.772*iw, y=0.450*ih, w=0.222*iw, h=0.242*ih)")
    print("   [6] Detectar automaticamente com IA (Groq Vision)")
    print("   [7] Sem webcam (apenas zoom central 9:16)")
    print()

    predefined_positions = {
        '1': {'name': 'Canto inferior esquerdo', 'x': 0.02, 'y': 0.70, 'w': 0.25, 'h': 0.28},
        '2': {'name': 'Canto superior esquerdo', 'x': 0.02, 'y': 0.02, 'w': 0.25, 'h': 0.28},
        '3': {'name': 'Canto inferior direito', 'x': 0.73, 'y': 0.70, 'w': 0.25, 'h': 0.28},
        '4': {'name': 'Canto superior direito', 'x': 0.73, 'y': 0.02, 'w': 0.25, 'h': 0.28},
        '5': {'name': 'Meio-direita / acima do minimapa', 'x': 0.772, 'y': 0.450, 'w': 0.222, 'h': 0.242},
    }

    while True:
        try:
            choice = input("   Escolha uma opção [1-7]: ").strip()
            if choice in ('1', '2', '3', '4', '5', '6', '7'):
                break
            print("   ❌ Opção inválida. Digite 1, 2, 3, 4, 5, 6 ou 7.")
        except (EOFError, KeyboardInterrupt):
            print("\n   Cancelado pelo usuário.")
            return None

    if choice == '7':
        print("   ✅ Modo: Apenas gameplay (crop central 9:16)")
        return None

    if choice == '6':
        # Detecção automática com IA
        if not api_key:
            log("GROQ_API_KEY não configurada. Fallback para sem webcam (opção 7).", "WARN")
            return None

        pos = detect_webcam_position_ai_live(api_key)
        if pos is None:
            log("Detecção automática falhou. Fallback para sem webcam (opção 7).", "WARN")
            return None

        print(f"   ✅ Modo: Tela dividida com webcam detectada automaticamente")
        return pos

    pos = predefined_positions[choice]
    print(f"   ✅ Modo: Tela dividida com webcam no {pos['name']}")
    return pos

def parse_args():
    """Parse de argumentos da linha de comando"""
    parser = argparse.ArgumentParser(
        description="Monitor de Lives em Tempo Real - Gera clips automáticos de momentos épicos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python monitor_lives.py "https://youtube.com/watch?v=LIVE_ID" --webcam 1
  python monitor_lives.py "https://twitch.tv/streamer" --webcam 3
  python monitor_lives.py "https://kick.com/streamer" --webcam 6
  python monitor_lives.py "https://youtube.com/watch?v=LIVE_ID" --webcam 7
        """
    )
    parser.add_argument("url", nargs="?", help="URL da live (YouTube, Twitch ou Kick)")
    parser.add_argument("--webcam", type=int, choices=[1,2,3,4,5,6,7],
                       help="Posição da webcam (1-4=cantos, 5=meio-direita, 6=IA auto, 7=sem webcam)")
    parser.add_argument("--audio-threshold", type=float, default=AUDIO_PEAK_THRESHOLD_DB,
                       help=f"Limite de pico de áudio em dB (padrão: {AUDIO_PEAK_THRESHOLD_DB})")
    parser.add_argument("--chat-threshold", type=int, default=CHAT_VELOCITY_THRESHOLD,
                       help=f"Limite de mensagens/min para explosão de chat (padrão: {CHAT_VELOCITY_THRESHOLD})")
    parser.add_argument("--clip-duration", type=int, default=CLIP_DURATION,
                       help=f"Duração do clip em segundos (padrão: {CLIP_DURATION})")
    parser.add_argument("--buffer-duration", type=int, default=BUFFER_MAX_DURATION,
                       help=f"Duração do ring buffer em segundos (padrão: {BUFFER_MAX_DURATION})")
    parser.add_argument("--cooldown", type=int, default=COOLDOWN_BETWEEN_CLIPS,
                       help=f"Cooldown entre clips em segundos (padrão: {COOLDOWN_BETWEEN_CLIPS})")
    parser.add_argument("--no-chat", action="store_true", help="Desabilita monitoramento de chat")

    return parser.parse_args()

# ========== MAIN ==========

def signal_handler(sig, frame):
    """Handler para Ctrl+C"""
    log("Interrupção recebida, finalizando...", "WARN")
    stop_event.set()

def main():
    global current_live_config, groq_api_key, clip_duration
    global AUDIO_PEAK_THRESHOLD_DB, CHAT_VELOCITY_THRESHOLD, CLIP_DURATION, COOLDOWN_BETWEEN_CLIPS

    # Carrega .env
    load_env_file()
    groq_api_key = os.getenv("GROQ_API_KEY")

    if not groq_api_key:
        log("GROQ_API_KEY não encontrada no .env. Necessária para transcrição Whisper.", "ERROR")
        log("Copie .env.example para .env e configure sua chave.", "ERROR")
        return 1

    # Parse args
    args = parse_args()

    # Override constants from args
    AUDIO_PEAK_THRESHOLD_DB = args.audio_threshold
    CHAT_VELOCITY_THRESHOLD = args.chat_threshold
    CLIP_DURATION = args.clip_duration
    COOLDOWN_BETWEEN_CLIPS = args.cooldown
    clip_duration = CLIP_DURATION

    # URL da live
    if args.url:
        live_url = args.url
    else:
        live_url = input("🔗 Cole a URL da live (YouTube/Twitch/Kick): ").strip()

    if not live_url:
        log("URL não fornecida", "ERROR")
        return 1

    # Resolve URL e plataforma
    stream_url, platform = resolve_live_url(live_url)
    log(f"Plataforma detectada: {platform}")
    log(f"URL da stream: {stream_url}")

    # Posição da webcam
    if args.webcam:
        webcam_choice = str(args.webcam)
        predefined_positions = {
            '1': {'name': 'Canto inferior esquerdo', 'x': 0.02, 'y': 0.70, 'w': 0.25, 'h': 0.28},
            '2': {'name': 'Canto superior esquerdo', 'x': 0.02, 'y': 0.02, 'w': 0.25, 'h': 0.28},
            '3': {'name': 'Canto inferior direito', 'x': 0.73, 'y': 0.70, 'w': 0.25, 'h': 0.28},
            '4': {'name': 'Canto superior direito', 'x': 0.73, 'y': 0.02, 'w': 0.25, 'h': 0.28},
            '5': {'name': 'Meio-direita / acima do minimapa', 'x': 0.772, 'y': 0.450, 'w': 0.222, 'h': 0.242},
            '6': 'AI_DETECT',  # Special marker for AI detection
            '7': None,         # No webcam
        }

        if webcam_choice == '6':
            # AI auto-detection
            webcam_pos = detect_webcam_position_ai_live(groq_api_key)
            if webcam_pos is None:
                log("Detecção automática falhou. Fallback para sem webcam (opção 7).", "WARN")
                webcam_pos = None
        elif webcam_choice == '7':
            webcam_pos = None
        else:
            webcam_pos = predefined_positions.get(webcam_choice)
    else:
        webcam_pos = ask_webcam_position_live(groq_api_key)

    if webcam_pos is None and args.webcam not in (6, 7):
        return 1

    # Config da live
    current_live_config = LiveConfig(
        url=live_url,
        platform=platform,
        webcam_position=webcam_pos,
        streamer_name=platform  # TODO: extrair nome real
    )

    # Prepara diretórios
    ensure_dirs()

    # Limpa buffer antigo
    for f in LIVE_BUFFER_DIR.glob("*.ts"):
        try:
            f.unlink()
        except:
            pass

    log("=" * 60)
    log("MONITOR DE LIVE INICIADO")
    log(f"   Buffer: {BUFFER_MAX_DURATION}s (segmentos de {BUFFER_SEGMENT_DURATION}s)")
    log(f"   Clip duration: {CLIP_DURATION}s")
    log(f"   Audio threshold: {AUDIO_PEAK_THRESHOLD_DB} dB")
    log(f"   Chat velocity threshold: {CHAT_VELOCITY_THRESHOLD} msg/min")
    log(f"   Cooldown: {COOLDOWN_BETWEEN_CLIPS}s")
    log(f"   Webcam: {'Sem webcam' if webcam_pos is None else webcam_pos['name']}")
    log(f"   Chat monitor: {'Desabilitado' if args.no_chat else 'Habilitado'}")
    log("=" * 60)
    log("Pressione Ctrl+C para parar")

    # Registra signal handler
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Inicia threads
    threads = []

    # Thread 1: Gravação de buffer
    t1 = threading.Thread(target=buffer_recorder_thread, args=(stream_url,), daemon=True)
    t1.start()
    threads.append(t1)

    # Thread 2: Monitoramento de chat (se habilitado)
    if not args.no_chat:
        t2 = threading.Thread(target=chat_monitor_thread, args=(platform, stream_url), daemon=True)
        t2.start()
        threads.append(t2)

    # Thread 3: Processamento de gatilhos
    t3 = threading.Thread(target=trigger_processor_thread, daemon=True)
    t3.start()
    threads.append(t3)

    # Loop principal - aguarda stop_event
    try:
        while not stop_event.is_set():
            time.sleep(1)
            # Status periódico
            if len(ring_buffer) > 0:
                latest = ring_buffer[-1]
                log(f"Buffer: {len(ring_buffer)}/{RING_BUFFER_MAX_SEGMENTS} segmentos | "
                    f"Último pico: {latest.audio_peak_db:.1f} dB | "
                    f"Clips gerados: {clip_counter}", "INFO")
    except KeyboardInterrupt:
        pass

    # Finalização
    stop_event.set()
    log("Aguardando threads finalizarem...", "WARN")

    for t in threads:
        t.join(timeout=5)

    log("=" * 60)
    log(f"MONITOR FINALIZADO - {clip_counter} clips gerados", "OK")
    log(f"Clips salvos em: {LIVE_CLIPS_DIR}", "OK")
    log("=" * 60)

    return 0

if __name__ == "__main__":
    sys.exit(main())