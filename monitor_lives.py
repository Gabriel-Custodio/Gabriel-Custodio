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
# Palavras-chave fortes de reação do chat (apenas reações genuínas de clipe)
CHAT_KEYWORDS = [
    "clipa", "clipa isso", "clipa aí", "clipa", "clipe", "corta isso", "corta aí",
    "kkkkkk", "kkkkk", "kkkkkkk", "caralho", "nem fudendo", "mds", "olha isso",
    "meu deus", "nossa", "absurdo", "inacreditavel", "insano", "monstro", "god",
    "clutch", "ace", "pentakill", "quadrakill", "triplekill", "doublekill"
]
RING_BUFFER_MAX_SEGMENTS = BUFFER_MAX_DURATION // BUFFER_SEGMENT_DURATION  # 12 segmentos
COOLDOWN_BETWEEN_CLIPS = 60           # Cooldown mínimo entre clips (segundos)

# Novo layout 3 camadas (1080x1920)
WEBAM_HEIGHT = 730                    # Altura do bloco webcam (topo)
HOOK_BAR_HEIGHT = 180                 # Altura da faixa divisória de gancho (meio)
GAMEPLAY_HEIGHT = 1010                # Altura do bloco gameplay (base)
TOTAL_HEIGHT = WEBAM_HEIGHT + HOOK_BAR_HEIGHT + GAMEPLAY_HEIGHT  # 1920

# Modelo Groq para geração de gancho/pergunta
GROQ_HOOK_MODEL = "llama-3.3-70b-versatile"
HOOK_MIN_SCORE = 70                   # Nota mínima para aceitar o clip

# Palavrões para censura simples nas legendas
PROFANITY_WORDS = [
    "caralho", "porra", "puta", "merda", "foda", "cacete", "buceta", "pau", "rola",
    "cu", "bosta", "arrombado", "otario", "otária", "filho da puta", "filha da puta",
    "vai se foder", "vao se foder", "fodase", "foda-se", "carai", "krl", "cr", "putz"
]

# Estilos de legenda karaokê
KARAOKE_FONT_SIZE = 68
KARAOKE_MARGIN_V = 220
KARAOKE_WORDS_PER_CHUNK = 3  # 2-3 palavras por bloco

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

# Cache para URL do stream resolvida (HLS .m3u8)
# Formato: { "url": "<página_original>", "resolved": "<url_m3u8>", "timestamp": <epoch>, "valid": True }
stream_url_cache: Dict[str, Dict] = {}
STREAM_CACHE_TTL_SECONDS = 300  # 5 minutos

# ========== FUNÇÕES AUXILIARES ==========

def log(msg: str, level: str = "INFO"):
    """Log com timestamp"""
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌", "OK": "✅", "CLIP": "🎬"}.get(level, "📝")
    print(f"[{ts}] {prefix} {msg}")

def generate_hook_and_question(transcription_text: str, api_key: str) -> Optional[Dict]:
    """
    Envia a transcrição para o Groq LLM (llama-3.3-70b-versatile) para gerar:
    - titulo_gancho: frase curta de impacto em caixa alta (máx 5 palavras)
    - pergunta_cta: pergunta curta para gerar comentários (máx 4 palavras)
    - nota: score 0-100

    Retorna dict com as chaves ou None se falhar/nota < 70.
    """
    if not transcription_text.strip():
        log("Transcrição vazia, não é possível gerar gancho", "WARN")
        return None

    prompt = (
        "Você é um especialista em criar clips virais para Shorts/Reels/TikTok. "
        "Analise a transcrição abaixo de um momento de uma live de gameplay/streamer (45 segundos).\n\n"
        "RETORNE APENAS JSON VÁLIDO com exatamente estas chaves:\n"
        '{"titulo_gancho": "FRASE CURTA DE IMPACTO EM CAIXA ALTA (MÁX 5 PALAVRAS)", '
        '"pergunta_cta": "PERGUNTA CURTA PARA GERAR COMENTÁRIOS (MÁX 4 PALAVRAS)", '
        '"nota": 85}\n\n'
        "REGRAS OBRIGATÓRIAS:\n"
        "- titulo_gancho: MAIÚSCULAS, impacto imediato, máximo 5 palavras, sem pontuação final\n"
        "- pergunta_cta: pergunta direta, máximo 4 palavras, termina com ?\n"
        "- nota: inteiro 0-100, baseada no potencial viral do momento (emoção, tensão, humor, skill, fail épico)\n"
        "- Se o conteúdo for genérico/sem graça, dê nota baixa (< 70)\n\n"
        f"TRANSCRIÇÃO:\n{transcription_text[:2000]}"
    )

    try:
        result = groq_chat(
            [{"role": "user", "content": prompt}],
            api_key,
            GROQ_HOOK_MODEL,
            temperature=0.7,
            max_tokens=200
        )

        if not result:
            log("Groq não retornou resposta para gancho", "WARN")
            return None

        hook_data = extract_json(result)
        if not hook_data or not isinstance(hook_data, dict):
            log(f"Resposta inválida do Groq para gancho: {result[:200]}", "WARN")
            return None

        # Valida campos obrigatórios
        required_keys = ["titulo_gancho", "pergunta_cta", "nota"]
        for key in required_keys:
            if key not in hook_data:
                log(f"Campo '{key}' ausente na resposta do Groq", "WARN")
                return None

        titulo = str(hook_data["titulo_gancho"]).strip()
        pergunta = str(hook_data["pergunta_cta"]).strip()
        nota = int(hook_data["nota"])

        # Validações
        if nota < HOOK_MIN_SCORE:
            log(f"Nota do gancho ({nota}) abaixo do mínimo ({HOOK_MIN_SCORE}). Clip descartado.", "WARN")
            return None

        # Limita palavras
        titulo_words = titulo.split()
        if len(titulo_words) > 5:
            titulo = " ".join(titulo_words[:5])
            log(f"Título truncado para 5 palavras: {titulo}", "INFO")

        pergunta_words = pergunta.split()
        if len(pergunta_words) > 4:
            pergunta = " ".join(pergunta_words[:4])
            log(f"Pergunta truncada para 4 palavras: {pergunta}", "INFO")

        # Garante que pergunta termina com ?
        if not pergunta.endswith("?"):
            pergunta += "?"

        # Garante caixa alta no título
        titulo = titulo.upper()

        log(f"✅ Gancho gerado: '{titulo}' | Pergunta: '{pergunta}' | Nota: {nota}", "OK")
        return {
            "titulo_gancho": titulo,
            "pergunta_cta": pergunta,
            "nota": nota
        }

    except Exception as e:
        log(f"Erro ao gerar gancho/pergunta: {e}", "ERROR")
        return None

def censor_profanity(text: str) -> str:
    """Aplica censura simples a palavrões comuns no texto da legenda."""
    import re
    censored = text
    for word in PROFANITY_WORDS:
        # Substitui mantendo primeira letra + *** (case insensitive)
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        def replace_func(match):
            w = match.group()
            if len(w) <= 2:
                return "*" * len(w)
            return w[0] + "*" * (len(w) - 1)
        censored = pattern.sub(replace_func, censored)
    return censored

def ensure_dirs():
    """Cria diretórios necessários"""
    LIVE_BUFFER_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)


def resolver_url_stream(page_url: str) -> str:
    """
    Resolve a URL da página da live (ex: https://twitch.tv/alanzoka) para a URL direta
    do manifest HLS (.m3u8) usando yt-dlp.

    Implementa cache em memória com TTL de 5 minutos. Invalida o cache automaticamente
    se o FFmpeg retornar erro ao abrir o segmento.

    Args:
        page_url: URL da página da live (YouTube, Twitch, Kick)

    Returns:
        URL direta do stream HLS (.m3u8)

    Raises:
        RuntimeError: Se não conseguir resolver a URL
    """
    import subprocess

    now = time.time()

    # Verifica cache válido
    if page_url in stream_url_cache:
        cached = stream_url_cache[page_url]
        if cached.get("valid", True) and (now - cached["timestamp"]) < STREAM_CACHE_TTL_SECONDS:
            log(f"Usando URL de stream em cache: {cached['resolved'][:80]}...", "INFO")
            return cached["resolved"]
        else:
            # Cache expirado ou inválido, remove para forçar nova resolução
            log("Cache de URL expirado/invalidado, resolvendo novamente...", "INFO")
            stream_url_cache.pop(page_url, None)

    # Resolve com yt-dlp
    log(f"Resolvendo URL do stream via yt-dlp: {page_url}", "INFO")

    # Comando yt-dlp para extrair URL direta do melhor formato até 1080p
    # Usa python -m yt_dlp conforme padrão do projeto
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-g",                          # Get URL only
        "-f", "best[height<=1080]/best",  # Melhor qualidade até 1080p
        "--no-warnings",               # Suprime warnings
        "--no-check-certificates",     # Para alguns sites
        page_url
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding='utf-8',
            errors='replace'
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Timeout ao resolver URL com yt-dlp (30s): {page_url}")
    except Exception as e:
        raise RuntimeError(f"Erro ao executar yt-dlp: {e}")

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else "sem erro"
        raise RuntimeError(f"yt-dlp falhou (código {result.returncode}): {stderr[:500]}")

    # yt-dlp pode retornar múltiplas linhas (video + audio), pega a primeira que seja .m3u8 ou a primeira válida
    output_lines = result.stdout.strip().split('\n')
    resolved_url = None

    for line in output_lines:
        line = line.strip()
        if line and (line.startswith('http') or line.startswith('https')):
            # Prefere URLs .m3u8 (HLS)
            if '.m3u8' in line:
                resolved_url = line
                break
            # Fallback: primeira URL válida
            if resolved_url is None:
                resolved_url = line

    if not resolved_url:
        raise RuntimeError(f"yt-dlp não retornou URL válida. Saída: {result.stdout[:500]}")

    # Armazena no cache
    stream_url_cache[page_url] = {
        "resolved": resolved_url,
        "timestamp": now,
        "valid": True
    }

    log(f"✅ URL do stream resolvida e cacheada: {resolved_url[:80]}...", "OK")
    return resolved_url


def invalidate_stream_cache(page_url: str):
    """Invalida o cache de URL do stream para forçar nova resolução na próxima tentativa."""
    if page_url in stream_url_cache:
        stream_url_cache[page_url]["valid"] = False
        log(f"Cache de URL invalidado para: {page_url}", "WARN")


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

def record_buffer_segment(segment_path: Path, page_url: str, duration: int = BUFFER_SEGMENT_DURATION) -> bool:
    """
    Grava um segmento da live via FFmpeg usando URL direta HLS (.m3u8) resolvida pelo yt-dlp.

    Args:
        segment_path: Caminho do arquivo de saída do segmento
        page_url: URL da página da live (ex: https://twitch.tv/alanzoka) - NÃO a URL direta do stream
        duration: Duração do segmento em segundos

    Returns:
        True se sucesso, False caso contrário
    """
    # Resolve a URL da página para URL direta do stream HLS (.m3u8) com cache
    try:
        stream_url = resolver_url_stream(page_url)
    except RuntimeError as e:
        log(f"Falha ao resolver URL do stream: {e}", "ERROR")
        return False

    # Flags de reconexão DEVEM vir ANTES do -i para streams ao vivo (HLS)
    cmd = [
        FFMPEG.as_posix(), "-y",
        "-hide_banner", "-loglevel", "error",
        # Flags de reconexão para streams HLS ao vivo
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-timeout", "10000000",
        "-i", stream_url,  # URL direta .m3u8 resolvida
        "-t", str(duration),
        "-c", "copy",           # Copy codec para velocidade (segmento TS)
        "-f", "mpegts",         # MPEG-TS container para concatenação fácil
        str(segment_path)
    ]

    ok, _, err = run_cmd(cmd, timeout=duration + 10)
    if not ok:
        log(f"Falha ao gravar segmento: {err[:200]}", "ERROR")
        # Invalida cache se erro de I/O / abertura de arquivo (indica URL expirada)
        if "I/O error" in err or "Error opening input" in err or "404" in err or "403" in err:
            invalidate_stream_cache(page_url)
            log("Cache de URL invalidado devido a erro de stream, nova resolução na próxima tentativa", "WARN")
        return False
    return True

def buffer_recorder_thread(page_url: str):
    """Thread que grava continuamente segmentos de 10s no ring buffer"""
    log("Thread de gravação iniciada", "OK")

    while not stop_event.is_set():
        segment_path = get_segment_filename()
        start_time = time.time()

        # Grava segmento (page_url é a URL da página, ex: https://twitch.tv/alanzoka)
        success = record_buffer_segment(segment_path, page_url)

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
    # Usa caminhos absolutos normalizados com barras normais para evitar duplicação de caminho
    # no FFmpeg concat demuxer (que resolve caminhos relativos ao diretório do .txt)
    list_file = LIVE_BUFFER_DIR / "concat_list.txt"
    with open(list_file, 'w', encoding='utf-8') as f:
        for seg in selected_segments:
            abs_path = seg.path.resolve().as_posix()  # Caminho absoluto com barras normais
            f.write(f"file '{abs_path}'\n")

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

def create_karaoke_ass_subtitle(segments: List[Dict], clip_duration: float, output_path: Path) -> bool:
    """
    Cria legenda .ass estilo KARAOKÊ/DESTAQUE para a área do gameplay (base):
    - Blocos de 2-3 palavras por vez (caixa alta)
    - Palavra exata sendo pronunciada em AMARELO (#FFD700), resto em BRANCO
    - Fonte Impact/Arial Black tamanho 68, borda preta grossa
    - MarginV=220 (área inferior do gameplay)
    - Censura simples de palavrões (F***, P***)
    - Zero sobreposição: end do bloco atual = start do próximo
    """
    if not segments:
        return False

    # Coleta todas as palavras com timestamps em ordem cronológica
    all_words = []
    for seg in segments:
        if 'words' in seg and seg['words']:
            for w in seg['words']:
                word_text = censor_profanity(w['word']).upper()
                all_words.append({
                    'word': word_text,
                    'start': w['start'],
                    'end': w['end']
                })
        elif 'text' in seg:
            # Fallback: divide o texto em palavras e estima timestamps
            words = seg['text'].split()
            if words:
                duration_per_word = (seg['end'] - seg['start']) / len(words)
                for i, word in enumerate(words):
                    word_text = censor_profanity(word).upper()
                    w_start = seg['start'] + i * duration_per_word
                    w_end = w_start + duration_per_word
                    all_words.append({
                        'word': word_text,
                        'start': w_start,
                        'end': w_end
                    })

    if not all_words:
        log("Nenhuma palavra para legendar", "WARN")
        return False

    # Header ASS para estilo karaokê
    ass_header = f"""[Script Info]
Title: Live Clip - Karaoke Subtitles
ScriptType: v4.00+
PlayResX: {VERTICAL_WIDTH}
PlayResY: {VERTICAL_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Impact,68,&H00FFFFFF,&H00FFFF00,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,8,3,2,20,20,{KARAOKE_MARGIN_V},1

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

    # Agrupa palavras em chunks de KARAOKE_WORDS_PER_CHUNK (2-3)
    i = 0
    while i < len(all_words):
        chunk_words = all_words[i:i + KARAOKE_WORDS_PER_CHUNK]
        chunk_start = chunk_words[0]['start']
        chunk_end = chunk_words[-1]['end']

        # Constrói o texto com highlight karaokê (\k timing)
        # Cada palavra tem seu tempo em centisegundos para o efeito karaokê
        karaoke_parts = []
        for j, w in enumerate(chunk_words):
            word_dur_cs = int((w['end'] - w['start']) * 100)
            if word_dur_cs < 1:
                word_dur_cs = 1
            # Palavra atual em amarelo, resto em branco
            # Usa {\k timing} para karaokê + {\t} para troca de cor
            karaoke_parts.append(f"{{\\k{word_dur_cs}\\t(\\c&H00D7FF&)}}{w['word']}{{\\t(\\c&HFFFFFF&)}}")

        # Adiciona espaço entre palavras
        karaoke_text = " ".join(karaoke_parts)

        start_fmt = format_ass_time(chunk_start)
        end_fmt = format_ass_time(chunk_end)

        events.append(f"Dialogue: 0,{start_fmt},{end_fmt},Karaoke,,0,0,0,karaoke,{karaoke_text}")

        i += KARAOKE_WORDS_PER_CHUNK

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(ass_header)
            f.write('\n'.join(events))
        log(f"Legenda karaokê criada: {output_path} ({len(events)} blocos)")
        return True
    except Exception as e:
        log(f"Erro ao criar legenda karaokê: {e}", "ERROR")
        return False

def process_gameplay_layout(input_path: Path, output_path: Path, webcam_pos: Optional[Dict],
                            ass_path: Optional[Path], hook_data: Optional[Dict],
                            clip_dur: float) -> bool:
    """
    Aplica novo layout 3 camadas (1080x1920) para clips de live:

    1. BLOCO 1 - TOPO (Webcam): 1080x730
       - Crop da webcam selecionada → scale=1080:730:force_original_aspect_ratio=increase,crop=1080:730

    2. BLOCO 2 - MEIO (Faixa Divisória de Gancho): 1080x180
       - Faixa sólida vermelha (#D91E18) com drawtext:
         * Linha 1 (y=48): titulo_gancho - negrito, branco/amarelo, tamanho 44, borda preta
         * Linha 2 (y=115): pergunta_cta - negrito, branco, tamanho 26

    3. BLOCO 3 - BASE (Gameplay): 1080x1010
       - Crop central 9:16 → scale=1080:1010:force_original_aspect_ratio=increase,crop=1080:1010
       - Legendas karaokê aplicadas aqui (MarginV=220)

    - Empilha com vstack=inputs=3 = 1080x1920 exatos
    - Áudio: loudnorm + fade-out 1.5s
    """
    log("Aplicando layout 3 camadas (1080x1920)...")

    ffmpeg_cmd = FFMPEG.as_posix()
    watermark_path = Path(GAMEPLAY_WATERMARK_PATH)
    has_watermark = watermark_path.exists()

    # Prepara textos do gancho (escapados para FFmpeg drawtext)
    titulo_gancho = ""
    pergunta_cta = ""
    if hook_data:
        titulo_gancho = hook_data.get("titulo_gancho", "").replace("'", "\\'").replace(":", "\\:")
        pergunta_cta = hook_data.get("pergunta_cta", "").replace("'", "\\'").replace(":", "\\:")

    # Cores para drawtext (formato AABBGGRR para FFmpeg)
    # Vermelho da faixa: #D91E18
    # Amarelo/dourado para título: #FFD700 -> 0x00D7FF
    # Branco para pergunta: #FFFFFF -> 0xFFFFFF
    # Preto para borda: #000000 -> 0x000000

    if webcam_pos is None:
        # SEM WEBCAM: Bloco 1 = placeholder preto/gradiente ou gameplay estendido?
        # Conforme spec: mesmo sem webcam, mantém 3 blocos. Bloco 1 pode ser gameplay estendido ou cor sólida.
        # Vamos usar gameplay para o bloco 1 também (zoom diferente) ou uma cor neutra.
        # Melhor: usa o mesmo gameplay mas com crop diferente para preencher o topo.
        filter_complex = (
            # Bloco 1 (Topo - 1080x730): Gameplay com crop superior (zoom)
            f"[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{WEBAM_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{WEBAM_HEIGHT}[top];"
            # Bloco 2 (Meio - 1080x180): Faixa vermelha com textos do gancho
            f"color=c=#D91E18:s={VERTICAL_WIDTH}x{HOOK_BAR_HEIGHT}[bar];"
            # Bloco 3 (Base - 1080x1010): Gameplay central 9:16
            f"[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{GAMEPLAY_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{GAMEPLAY_HEIGHT}[base]"
        )

        # Adiciona drawtext na faixa se houver gancho
        if titulo_gancho and pergunta_cta:
            filter_complex += (
                f";[bar]drawtext=text='{titulo_gancho}':fontfile='C\\\\:/Windows/Fonts/impact.ttf':"
                f"fontcolor=0x00D7FF:fontsize=44:bold=1:borderw=3:bordercolor=0x000000:"
                f"x=(w-text_w)/2:y=48,"
                f"drawtext=text='{pergunta_cta}':fontfile='C\\\\:/Windows/Fonts/impact.ttf':"
                f"fontcolor=0xFFFFFF:fontsize=26:bold=1:borderw=2:bordercolor=0x000000:"
                f"x=(w-text_w)/2:y=115[hook_bar]"
            )
            bar_label = "[hook_bar]"
        else:
            bar_label = "[bar]"

        # Adiciona legendas karaokê no gameplay (base)
        if ass_path and ass_path.exists():
            ass_path_escaped = ass_path.as_posix().replace("'", "\\'").replace(":", "\\:")
            filter_complex += f";[base]subtitles='{ass_path_escaped}'[base_sub]"
            base_label = "[base_sub]"
        else:
            base_label = "[base]"

        # Adiciona watermark se existir
        if has_watermark:
            filter_complex += f";movie='{watermark_path.as_posix()}'[wm];{base_label}[wm]overlay=20:20[base_final]"
            base_label = "[base_final]"

        # vstack final: 3 inputs
        filter_complex += f";[top]{bar_label}{base_label}vstack=inputs=3[vout]"
        map_v = "[vout]"

    else:
        # COM WEBCAM: Bloco 1 = Webcam crop
        wc_x = f"iw*{webcam_pos['x']:.6f}"
        wc_y = f"ih*{webcam_pos['y']:.6f}"
        wc_w = f"iw*{webcam_pos['w']:.6f}"
        wc_h = f"ih*{webcam_pos['h']:.6f}"

        filter_complex = (
            # Bloco 1 (Topo - 1080x730): Webcam crop
            f"[0:v]crop={wc_w}:{wc_h}:{wc_x}:{wc_y},"
            f"scale={VERTICAL_WIDTH}:{WEBAM_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{WEBAM_HEIGHT}[top];"
            # Bloco 2 (Meio - 1080x180): Faixa vermelha com textos do gancho
            f"color=c=#D91E18:s={VERTICAL_WIDTH}x{HOOK_BAR_HEIGHT}[bar];"
            # Bloco 3 (Base - 1080x1010): Gameplay central 9:16
            f"[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{GAMEPLAY_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{GAMEPLAY_HEIGHT}[base]"
        )

        # Adiciona drawtext na faixa se houver gancho
        if titulo_gancho and pergunta_cta:
            filter_complex += (
                f";[bar]drawtext=text='{titulo_gancho}':fontfile='C\\\\:/Windows/Fonts/impact.ttf':"
                f"fontcolor=0x00D7FF:fontsize=44:bold=1:borderw=3:bordercolor=0x000000:"
                f"x=(w-text_w)/2:y=48,"
                f"drawtext=text='{pergunta_cta}':fontfile='C\\\\:/Windows/Fonts/impact.ttf':"
                f"fontcolor=0xFFFFFF:fontsize=26:bold=1:borderw=2:bordercolor=0x000000:"
                f"x=(w-text_w)/2:y=115[hook_bar]"
            )
            bar_label = "[hook_bar]"
        else:
            bar_label = "[bar]"

        # Adiciona legendas karaokê no gameplay (base)
        if ass_path and ass_path.exists():
            ass_path_escaped = ass_path.as_posix().replace("'", "\\'").replace(":", "\\:")
            filter_complex += f";[base]subtitles='{ass_path_escaped}'[base_sub]"
            base_label = "[base_sub]"
        else:
            base_label = "[base]"

        # Adiciona watermark se existir
        if has_watermark:
            filter_complex += f";movie='{watermark_path.as_posix()}'[wm];{base_label}[wm]overlay=20:20[base_final]"
            base_label = "[base_final]"

        # vstack final: 3 inputs
        filter_complex += f";[top]{bar_label}{base_label}vstack=inputs=3[vout]"
        map_v = "[vout]"

    # Áudio: loudnorm + fade-out 1.5s
    fade_start = max(0, clip_dur - 1.5)
    af = f"loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,afade=t=out:st={fade_start:.2f}:d=1.5"

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

    ok, _, err = run_cmd(cmd, timeout=90)

    if not ok:
        log(f"Erro no FFmpeg layout 3 camadas: {err[:500]}", "ERROR")
        return False

    log(f"Layout 3 camadas aplicado com sucesso: {output_path}", "OK")
    return True

def process_trigger(trigger: TriggerEvent):
    """Processa um gatilho e gera o clip final"""
    global last_clip_time, clip_duration

    # Cooldown
    now = time.time()
    if now - last_clip_time < COOLDOWN_BETWEEN_CLIPS:
        log(f"Cooldown ativo, ignorando gatilho ({COOLDOWN_BETWEEN_CLIPS - (now - last_clip_time):.0f}s restantes)", "WARN")
        return

    # Trava mínima: exige pelo menos 3 segmentos (30s) no buffer antes de aceitar gatilhos
    MIN_BUFFER_SEGMENTS = 3  # 3 segmentos * 10s = 30s mínimo
    if len(ring_buffer) < MIN_BUFFER_SEGMENTS:
        log(f"Buffer insuficiente ({len(ring_buffer)}/{MIN_BUFFER_SEGMENTS} segmentos), ignorando gatilho inicial", "WARN")
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

    # 3. Gera texto completo da transcrição para o gancho
    full_transcription = " ".join([seg.get('text', '') for seg in segments if seg.get('text')])

    # 4. Gera gancho + pergunta via Groq LLM (llama-3.3-70b-versatile)
    hook_data = generate_hook_and_question(full_transcription, groq_api_key)
    if not hook_data:
        log("Gancho rejeitado (nota < 70 ou falha na geração). Clip descartado.", "WARN")
        # Limpa arquivos temporários
        try:
            raw_clip_path.unlink(missing_ok=True)
        except:
            pass
        return

    # 5. Cria legenda karaokê .ass (estilo destaque palavra por palavra)
    ass_path = LIVE_BUFFER_DIR / f"subs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ass"
    if not create_karaoke_ass_subtitle(segments, clip_duration, ass_path):
        ass_path = None

    # 6. Aplica layout 3 camadas + pós-processamento
    final_clip_path = get_clip_filename()

    if not process_gameplay_layout(raw_clip_path, final_clip_path, current_live_config.webcam_position, ass_path, hook_data, clip_duration):
        return

    # 7. Salva metadados JSON ao lado do vídeo
    json_path = final_clip_path.with_suffix('.json')
    metadata = {
        "titulo_gancho": hook_data["titulo_gancho"],
        "pergunta_cta": hook_data["pergunta_cta"],
        "nota": hook_data["nota"],
        "transcricao": full_transcription,
        "trigger_type": trigger.trigger_type,
        "trigger_details": trigger.details,
        "timestamp": datetime.now().isoformat(),
        "clip_duration": clip_duration,
        "webcam_position": current_live_config.webcam_position
    }
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        log(f"Metadados salvos: {json_path.name}", "OK")
    except Exception as e:
        log(f"Erro ao salvar JSON: {e}", "ERROR")

    # 8. Limpeza
    try:
        raw_clip_path.unlink(missing_ok=True)
        if ass_path:
            ass_path.unlink(missing_ok=True)
    except:
        pass

    log(f"🎉 CLIP PRONTO: {final_clip_path.name} | Gancho: {hook_data['titulo_gancho']} | Nota: {hook_data['nota']}", "OK")

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