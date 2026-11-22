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
        FFMPEG, VERTICAL_WIDTH, VERTICAL_HEIGHT, TEMP_DIR,
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
AUDIO_PEAK_THRESHOLD_DB = -4.5        # Limite de pico de áudio em dB (-4.5 = apenas gritos/sustos/risadas altas)
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
# Usa o mesmo modelo de texto que já funciona no gerar_cortes.py (openai/gpt-oss-120b)
# Lista de fallback automático caso algum modelo retorne 404/400
# Será preenchido dinamicamente na inicialização com modelos reais da API Groq
HOOK_MODEL_PRIMARY = DEFAULT_GROQ_TEXT_MODEL  # "openai/gpt-oss-120b"
HOOK_MODEL_FALLBACKS = [
    "llama-3.1-8b-instant",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b"
]
HOOK_MIN_SCORE = 65                   # Nota mínima para aceitar o clip (abaixo = descarta)

# Modelos ativos descobertos dinamicamente (preenchido em fetch_active_groq_models)
ACTIVE_GROQ_MODELS: List[str] = []


def fetch_active_groq_models(api_key: str) -> List[str]:
    """
    Consulta a API da Groq para listar modelos realmente ativos na conta.
    Filtra fora modelos de áudio/whisper/guard/tts/orpheus.
    Retorna lista de IDs de modelos de chat/texto ativos.
    Fallback para lista estática se a chamada falhar.
    """
    try:
        import requests
        headers = {'Authorization': f'Bearer {api_key}'}
        response = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers=headers,
            timeout=15
        )
        if response.status_code != 200:
            log(f"Groq models API retornou {response.status_code}: {response.text[:200]}", "WARN")
            return []

        data = response.json()
        models = data.get('data', [])

        # Palavras-chave para filtrar modelos NÃO de chat/texto
        exclude_keywords = ['whisper', 'guard', 'tts', 'orpheus', 'audio', 'speech', 'translation']

        active_models = []
        for model in models:
            model_id = model.get('id', '').lower()
            # Filtra modelos de áudio/guard/tts
            if any(kw in model_id for kw in exclude_keywords):
                continue
            # Prioriza modelos conhecidos bons
            if any(good in model_id for good in ['gpt-oss', 'llama', 'qwen', 'gemma', 'mixtral']):
                active_models.append(model.get('id'))

        if active_models:
            log(f"✅ Modelos Groq ativos descobertos: {', '.join(active_models[:5])}{'...' if len(active_models) > 5 else ''}", "OK")
            return active_models
        else:
            log("Nenhum modelo de chat ativo encontrado na API, usando fallbacks estáticos", "WARN")
            return []

    except Exception as e:
        log(f"Falha ao buscar modelos Groq (usando fallbacks estáticos): {e}", "WARN")
        return []


def get_hook_models_to_try() -> List[str]:
    """
    Retorna lista de modelos para tentar (descobertos dinamicamente + fallbacks estáticos).
    Garante que o modelo principal esteja no topo se estiver ativo.
    """
    global ACTIVE_GROQ_MODELS
    if not ACTIVE_GROQ_MODELS:
        # Fallback estático se descoberta não rodou ou falhou
        return [HOOK_MODEL_PRIMARY] + HOOK_MODEL_FALLBACKS

    # Coloca o modelo principal no topo se ele estiver na lista ativa
    models = []
    if HOOK_MODEL_PRIMARY in ACTIVE_GROQ_MODELS:
        models.append(HOOK_MODEL_PRIMARY)
        # Adiciona o resto dos ativos (exceto o principal que já foi adicionado)
        for m in ACTIVE_GROQ_MODELS:
            if m != HOOK_MODEL_PRIMARY:
                models.append(m)
    else:
        # Principal não está ativo, usa a lista ativa como está
        models = ACTIVE_GROQ_MODELS[:]

    # Adiciona fallbacks estáticos que não estão já na lista
    for fb in HOOK_MODEL_FALLBACKS:
        if fb not in models:
            models.append(fb)

    return models

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

# Diretórios - Caminho absoluto garantido para cortes_prontos
OUTPUT_DIR = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cortes_prontos"))
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

# Throttling de logs repetitivos (evita spam no console)
_last_cooldown_log = 0.0
_last_buffer_log = 0.0
LOG_THROTTLE_SECONDS = 5.0

# ========== FUNÇÕES AUXILIARES ==========

def log(msg: str, level: str = "INFO"):
    """Log com timestamp"""
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌", "OK": "✅", "CLIP": "🎬"}.get(level, "📝")
    print(f"[{ts}] {prefix} {msg}")

def generate_hook_and_question(transcription_text: str, api_key: str) -> Optional[Dict]:
    """
    Envia a transcrição para o Groq LLM para gerar gancho/pergunta/nota.
    Usa modelo principal (DEFAULT_GROQ_TEXT_MODEL) com fallback automático.
    Se TODOS os modelos falharem, retorna valores padrão seguros (NÃO aborta o clipe).
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
        "- Se o conteúdo for genérico/sem graça, dê nota baixa\n\n"
        f"TRANSCRIÇÃO:\n{transcription_text[:2000]}"
    )

    # Lista de modelos a tentar (descobertos dinamicamente + fallbacks estáticos)
    models_to_try = get_hook_models_to_try()

    for model in models_to_try:
        try:
            result = groq_chat(
                [{"role": "user", "content": prompt}],
                api_key,
                model,
                temperature=0.7,
                max_tokens=200
            )

            if not result:
                log(f"Groq ({model}) não retornou resposta, tentando próximo...", "WARN")
                continue

            hook_data = extract_json(result)
            if not hook_data or not isinstance(hook_data, dict):
                log(f"Resposta inválida do Groq ({model}): {result[:200]}", "WARN")
                continue

            # Valida campos obrigatórios
            required_keys = ["titulo_gancho", "pergunta_cta", "nota"]
            for key in required_keys:
                if key not in hook_data:
                    log(f"Campo '{key}' ausente na resposta do Groq ({model})", "WARN")
                    continue

            titulo = str(hook_data["titulo_gancho"]).strip()
            pergunta = str(hook_data["pergunta_cta"]).strip()
            nota = int(hook_data["nota"])

            # SEMPRE imprime nota e título para acompanhamento em tempo real
            log(f"[IA] Análise ({model}): Nota {nota}/100 | Título: '{titulo}' | Pergunta: '{pergunta}'", "INFO")

            # Validações
            if nota < HOOK_MIN_SCORE:
                log(f"[IA] Clipe descartado - Nota {nota}/100 (mínimo {HOOK_MIN_SCORE})", "WARN")
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

            log(f"✅ Gancho APROVADO ({model}): '{titulo}' | Pergunta: '{pergunta}' | Nota: {nota}", "OK")
            return {
                "titulo_gancho": titulo,
                "pergunta_cta": pergunta,
                "nota": nota
            }

        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "400" in error_str or "does not exist" in error_str:
                log(f"Modelo {model} indisponível (404/400), tentando fallback...", "WARN")
                continue
            log(f"Erro ao gerar gancho/pergunta com {model}: {e}", "ERROR")
            continue

    # FALLBACK SEGURO: todos os modelos falharam → usa valores padrão e segue com o clipe
    log("[FALLBACK] Todos os modelos LLM falharam. Usando gancho padrão para não perder o clipe.", "WARN")
    return {
        "titulo_gancho": "MOMENTO INCRÍVEL NA LIVE",
        "pergunta_cta": "O QUE VOCÊ ACHOU?",
        "nota": 75
    }

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
    # Garante que OUTPUT_DIR (cortes_prontos) existe com caminho absoluto
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


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
    """Gera nome de arquivo para clip final (NÃO incrementa contador - isso é feito após validação)"""
    global clip_counter
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Usa clip_counter + 1 para o nome, mas só incrementa após validação real
    return LIVE_CLIPS_DIR / f"live_clipe_{ts}_{clip_counter + 1:03d}.mp4"

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
    # FIX sincronia: -fflags +genpts para gerar PTS contínuos
    # Opções de saída (-fps_mode, -c, -f, -t) DEVEM vir DEPOIS do -i
    cmd = [
        FFMPEG.as_posix(), "-y",
        "-hide_banner", "-loglevel", "error",
        # Input options (before -i)
        "-fflags", "+genpts",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-timeout", "10000000",
        "-i", stream_url,  # URL direta .m3u8 resolvida
        # Output options (after -i)
        "-fps_mode", "cfr",
        "-t", str(duration),
        "-c", "copy",           # Copy codec para velocidade (segmento TS)
        "-f", "mpegts",         # MPEG-TS container para concatenação fácil
        str(segment_path)
    ]

    ok, _, err = run_cmd(cmd, timeout=duration + 10)
    if not ok:
        # Mostra últimas 1500 chars do stderr (erro real, não banner FFmpeg)
        err_tail = err[-1500:] if len(err) > 1500 else err
        log(f"Falha ao gravar segmento: {err_tail}", "ERROR")
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
    Retorna o peak level em dB (usa o maior entre max_volume e mean_volume).
    """
    cmd = [
        FFMPEG.as_posix(), "-y",
        "-hide_banner", "-loglevel", "info",  # info para capturar output do volumedetect
        "-i", str(video_path),
        "-af", "volumedetect",
        "-f", "null", "-"
    ]

    ok, out, err = run_cmd(cmd, timeout=15)

    # Parse output do volumedetect (vem no stderr)
    # Exemplo: "[Parsed_volumedetect_0 @ 0x...] mean_volume: -24.5 dB"
    #          "[Parsed_volumedetect_0 @ 0x...] max_volume: -5.2 dB"
    peak_db = -60.0
    output_text = err + out
    for line in output_text.split('\n'):
        if 'max_volume:' in line:
            try:
                # Formato: "... max_volume: -5.2 dB"
                value_str = line.split('max_volume:')[1].strip().split(' ')[0]
                peak_db = max(peak_db, float(value_str))
            except:
                pass
        elif 'mean_volume:' in line:
            try:
                value_str = line.split('mean_volume:')[1].strip().split(' ')[0]
                mean_db = float(value_str)
                # Usa o maior entre mean e max para detecção (mais sensível a gritos/risadas)
                peak_db = max(peak_db, mean_db)
            except:
                pass

    # Log para debug (apenas se não for silêncio total)
    if peak_db > -50.0:
        log(f"Audio peak detectado: {peak_db:.1f} dB ({video_path.name})", "INFO")

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

    FIX sincronia: Usa -fflags +genpts, -fps_mode cfr, aresample=async=1:first_pts=0,
    setpts=PTS-STARTPTS, asetpts=PTS-STARTPTS para garantir timestamps normalizados
    desde zero e evitar travamento/congelamento nos primeiros segundos.
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

    # Concatena segmentos com correção de timestamps
    # FIX: -fflags +genpts, -fps_mode cfr, setpts=PTS-STARTPTS, asetpts=PTS-STARTPTS
    # NÃO usa -c copy para garantir timestamps contínuos
    # IMPORTANTE: Opções de saída (-fps_mode, -c:v, -c:a, -af) DEPOIS do -i
    temp_concat = LIVE_BUFFER_DIR / "temp_concat.ts"

    cmd_concat = [
        FFMPEG.as_posix(), "-y",
        "-hide_banner", "-loglevel", "error",
        # Input options
        "-fflags", "+genpts",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        # Output options
        "-fps_mode", "cfr",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-vf", "setpts=PTS-STARTPTS",
        "-af", "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0",
        str(temp_concat)
    ]

    ok, _, err = run_cmd(cmd_concat, timeout=60)
    if not ok:
        # Mostra últimas 1500 chars do stderr
        err_tail = err[-1500:] if len(err) > 1500 else err
        log(f"Falha ao concatenar: {err_tail}", "ERROR")
        return False

    # Agora corta os últimos n_seconds (re-encode para garantir timestamps corretos)
    concat_duration = get_video_duration(temp_concat)
    start_cut = max(0, concat_duration - n_seconds)

    cmd_cut = [
        FFMPEG.as_posix(), "-y",
        "-hide_banner", "-loglevel", "error",
        # Input options (before -i): -ss for input seeking is faster
        "-fflags", "+genpts",
        "-ss", str(start_cut),
        "-i", str(temp_concat),
        # Output options (after -i)
        "-fps_mode", "cfr",
        "-t", str(n_seconds),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-vf", "setpts=PTS-STARTPTS",
        "-af", "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0",
        str(output_path)
    ]

    ok, _, err = run_cmd(cmd_cut, timeout=60)

    # Limpa temp
    try:
        temp_concat.unlink(missing_ok=True)
        list_file.unlink(missing_ok=True)
    except:
        pass

    if not ok:
        # Mostra últimas 1500 chars do stderr
        err_tail = err[-1500:] if len(err) > 1500 else err
        log(f"Falha ao cortar clip: {err_tail}", "ERROR")
        return False

    return True

def transcribe_clip_groq(clip_path: Path, api_key: str) -> List[Dict]:
    """
    Transcreve o clip usando Groq Whisper com word timestamps.
    Para evitar erro 413 (Request Entity Too Large), extrai primeiro o áudio
    leve em MP3 mono 16kHz 64kbps antes de enviar para a API.
    Retorna lista de segmentos com palavras e timestamps.
    """
    log("Transcrevendo clip com Groq Whisper (word timestamps)...")

    try:
        import requests
    except ImportError:
        log("requests não instalado", "ERROR")
        return []

    # Extrai áudio leve para Whisper: MP3 mono 16kHz 64kbps
    audio_temp = clip_path.with_suffix('.whisper.mp3')
    cmd_extract = [
        FFMPEG.as_posix(), "-y",
        "-hide_banner", "-loglevel", "error",
        "-i", str(clip_path),
        "-vn",                    # Sem vídeo
        "-ac", "1",               # Mono
        "-ar", "16000",           # 16kHz (otimizado para Whisper)
        "-b:a", "64k",            # 64kbps (arquivo leve < 500KB para 45s)
        str(audio_temp)
    ]
    ok, _, err = run_cmd(cmd_extract, timeout=30)
    if not ok or not audio_temp.exists():
        # Mostra últimas 1500 chars do stderr
        err_tail = err[-1500:] if len(err) > 1500 else err
        log(f"Falha ao extrair áudio para Whisper: {err_tail}", "ERROR")
        return []

    try:
        with open(audio_temp, 'rb') as f:
            files = {'file': (audio_temp.name, f, 'audio/mpeg')}
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
    finally:
        # Remove arquivo temporário de áudio
        try:
            audio_temp.unlink(missing_ok=True)
        except:
            pass

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

def create_hook_banner_image(titulo_gancho: str, pergunta_cta: str, output_path: Path) -> bool:
    """
    Gera uma imagem PNG 1080x180 com o banner do gancho usando Pillow.
    Elimina problemas de fonte/sintaxe do drawtext no FFmpeg no Windows.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log("Pillow não instalado. Instale com: pip install pillow", "ERROR")
        return False

    # Dimensões do banner
    WIDTH, HEIGHT = 1080, 180
    BG_COLOR = (0xD9, 0x1E, 0x18)  # #D91E18 - vermelho sólido

    # Cria imagem com fundo vermelho
    img = Image.new('RGB', (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Tenta carregar fonte TrueType do sistema
    font_title = None
    font_question = None
    font_paths = [
        # Windows
        "C:/Windows/Fonts/arialbd.ttf",      # Arial Bold
        "C:/Windows/Fonts/impact.ttf",       # Impact
        "C:/Windows/Fonts/ariblk.ttf",       # Arial Black
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]

    for fp in font_paths:
        try:
            font_title = ImageFont.truetype(fp, 44)
            font_question = ImageFont.truetype(fp, 26)
            log(f"Fonte carregada: {fp}", "INFO")
            break
        except OSError:
            continue

    # Fallback: fonte padrão do Pillow
    if font_title is None:
        log("Nenhuma fonte TrueType encontrada, usando fallback", "WARN")
        font_title = ImageFont.load_default()
        font_question = ImageFont.load_default()

    # Desenha titulo_gancho (linha superior, y ~ 35)
    if titulo_gancho:
        # Calcula largura do texto para centralizar
        bbox = draw.textbbox((0, 0), titulo_gancho, font=font_title)
        text_width = bbox[2] - bbox[0]
        x = (WIDTH - text_width) // 2
        y = 35

        # Desenha contorno preto (desenha 4 vezes deslocado)
        outline_offset = 3
        for dx, dy in [(-outline_offset, 0), (outline_offset, 0), (0, -outline_offset), (0, outline_offset),
                       (-outline_offset, -outline_offset), (outline_offset, -outline_offset),
                       (-outline_offset, outline_offset), (outline_offset, outline_offset)]:
            draw.text((x + dx, y + dy), titulo_gancho, font=font_title, fill=(0, 0, 0))
        # Desenha texto principal amarelo/dourado
        draw.text((x, y), titulo_gancho, font=font_title, fill=(0xFF, 0xD7, 0x00))  # #FFD700

    # Desenha pergunta_cta (linha inferior, y ~ 115)
    if pergunta_cta:
        bbox = draw.textbbox((0, 0), pergunta_cta, font=font_question)
        text_width = bbox[2] - bbox[0]
        x = (WIDTH - text_width) // 2
        y = 115

        # Contorno preto
        outline_offset = 2
        for dx, dy in [(-outline_offset, 0), (outline_offset, 0), (0, -outline_offset), (0, outline_offset),
                       (-outline_offset, -outline_offset), (outline_offset, -outline_offset),
                       (-outline_offset, outline_offset), (outline_offset, outline_offset)]:
            draw.text((x + dx, y + dy), pergunta_cta, font=font_question, fill=(0, 0, 0))
        # Texto principal branco
        draw.text((x, y), pergunta_cta, font=font_question, fill=(0xFF, 0xFF, 0xFF))  # #FFFFFF

    # Salva PNG
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, 'PNG')
        log(f"Banner do gancho gerado: {output_path}", "OK")
        return True
    except Exception as e:
        log(f"Erro ao salvar banner PNG: {e}", "ERROR")
        return False


def process_gameplay_layout(input_path: Path, output_path: Path, webcam_pos: Optional[Dict],
                            ass_path: Optional[Path], hook_data: Optional[Dict],
                            clip_dur: float) -> bool:
    """
    Aplica layout ClipFlow 3 camadas (1080x1920) para clips de live - igual ao gerar_cortes.py:

    1. BLOCO 1 - TOPO (Webcam): 1080x730
       - Crop da webcam selecionada → scale=1080:730:force_original_aspect_ratio=increase,crop=1080:730
       - Se sem webcam: gameplay com crop superior (zoom)

    2. BLOCO 2 - MEIO (Faixa Divisória de Gancho): 1080x180
       - Imagem PNG gerada via Pillow (banner_gancho.png, fundo #D91E18) como entrada de imagem estática
         (-loop 1 -framerate 30 -t <duracao> -i <banner_png>) → scale=1080:180:force_original_aspect_ratio=increase,crop=1080:180
       - NÃO usa o filtro "loop=loop=-1:size=1"

    3. BLOCO 3 - BASE (Gameplay): 1080x1010
       - Crop central 9:16 → scale=1080:1010:force_original_aspect_ratio=increase,crop=1080:1010
       - Legendas aplicadas APENAS UMA VEZ sobre o vídeo final empilhado, nunca duplicado

    - Empilha [top][mid][base] com vstack=inputs=3 = 1080x1920 exatos
    - Áudio: loudnorm + fade-out 1.5s
    - FIX sincronia: -fflags +genpts, aresample=async=1:first_pts=0, -fps_mode cfr
    """
    log("Aplicando layout ClipFlow 3 camadas (1080x1920)...")

    ffmpeg_cmd = FFMPEG.as_posix()
    watermark_path = Path(GAMEPLAY_WATERMARK_PATH)
    has_watermark = watermark_path.exists()

    # Constantes ClipFlow (iguais ao gerar_cortes.py)
    CLIPFLOW_WEBAM_HEIGHT = 730
    CLIPFLOW_HOOK_BAR_HEIGHT = 180
    CLIPFLOW_GAMEPLAY_HEIGHT = 1010

    # Prepara textos do gancho
    titulo_gancho = hook_data.get("titulo_gancho", "") if hook_data else ""
    pergunta_cta = hook_data.get("pergunta_cta", "") if hook_data else ""

    # 1. Gera banner PNG do gancho (1080x180) usando Pillow
    banner_path = LIVE_BUFFER_DIR / "banner_gancho.png"
    if not create_hook_banner_image(titulo_gancho, pergunta_cta, banner_path):
        log("Falha ao gerar banner do gancho, prosseguindo sem banner", "WARN")
        banner_path = None

    if webcam_pos is None:
        # SEM WEBCAM: Bloco 1 = gameplay com crop superior (zoom)
        filter_complex = (
            # Bloco 1 (Topo - 1080x730): Gameplay com crop superior (zoom)
            f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}[top];"
            # Bloco 3 (Base - 1080x1010): Gameplay central 9:16
            f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}[base]"
        )
    else:
        # COM WEBCAM: Bloco 1 = Webcam crop (usa iw*w:ih*h:iw*x:ih*y, nunca "w*" sem "i")
        wc_x = f"iw*{webcam_pos['x']:.6f}"
        wc_y = f"ih*{webcam_pos['y']:.6f}"
        wc_w = f"iw*{webcam_pos['w']:.6f}"
        wc_h = f"ih*{webcam_pos['h']:.6f}"

        filter_complex = (
            # Bloco 1 (Topo - 1080x730): Webcam crop
            f"[0:v]setpts=PTS-STARTPTS,crop={wc_w}:{wc_h}:{wc_x}:{wc_y},"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}[top];"
            # Bloco 3 (Base - 1080x1010): Gameplay central 9:16
            f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}[base]"
        )

    # Bloco 2 (Meio - 1080x180): Banner do gancho (PNG) OU faixa vermelha sólida como fallback
    # IMPORTANTE: O banner PNG é imagem estática - usa entrada separada com -loop 1 -framerate 30 -t <duracao>
    # e redimensiona com scale=1080:180:force_original_aspect_ratio=increase,crop=1080:180
    # NÃO usa o filtro "loop=loop=-1:size=1"
    if banner_path and banner_path.exists():
        filter_complex += f";[1:v]scale={VERTICAL_WIDTH}:{CLIPFLOW_HOOK_BAR_HEIGHT}:force_original_aspect_ratio=increase,crop={VERTICAL_WIDTH}:{CLIPFLOW_HOOK_BAR_HEIGHT}[hook_bar]"
        bar_label = "[hook_bar]"
        banner_input = ["-loop", "1", "-framerate", "30", "-t", str(clip_dur), "-i", str(banner_path)]
    else:
        filter_complex += f";color=c=#D91E18:s={VERTICAL_WIDTH}x{CLIPFLOW_HOOK_BAR_HEIGHT}[hook_bar]"
        bar_label = "[hook_bar]"
        banner_input = []

    # vstack final: 3 inputs (topo, meio/gancho, base) = 1080x1920
    filter_complex += f";[top]{bar_label}[base]vstack=inputs=3[stacked]"

    # Adiciona legendas APÓS o empilhamento, sobre o vídeo completo 1080x1920 (antes do fade)
    if ass_path and ass_path.exists():
        ass_path_escaped = ass_path.as_posix().replace(":", "\\:")
        filter_complex += f";[stacked]subtitles='{ass_path_escaped}'[vout]"
    else:
        filter_complex += ";[stacked]copy[vout]"

    # Adiciona fade de vídeo 1.5s no final
    fade_start = max(0, clip_dur - 1.5)
    filter_complex += f";[vout]fade=t=out:st={fade_start:.2f}:d=1.5[vout_fade]"
    map_v = "[vout_fade]"

    # Adiciona watermark se existir (aplica sobre o vídeo final com legendas)
    if has_watermark:
        # Escapa dois-pontos no caminho (para Windows com letra de drive)
        wm_path_escaped = watermark_path.as_posix().replace(":", "\\:")
        filter_complex = filter_complex.replace(
            "[vout]fade=t=out:st=",
            f"movie='{wm_path_escaped}'[wm];[vout][wm]overlay=20:20[wmarked];[wmarked]fade=t=out:st="
        )
        map_v = "[vout_fade]"  # already correct since fade is last

    # Áudio: asetpts=PTS-STARTPTS + loudnorm + fade-out 1.5s + FIX sincronia (aresample async=1)
    # asetpts=PTS-STARTPTS garante que timestamps de áudio comecem do zero
    af = f"asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,afade=t=out:st={fade_start:.2f}:d=1.5"

    # Monta comando FFmpeg com -hide_banner -loglevel error
    # FIX sincronia: -fflags +genpts, -fps_mode cfr
    # IMPORTANTE: Opções de saída (-fps_mode, -c:v, -c:a, -af) DEPOIS do -i
    cmd = [
        ffmpeg_cmd, "-y",
        "-hide_banner", "-loglevel", "error",
        # Input options
        "-fflags", "+genpts",
        "-i", str(input_path),
    ]
    # Adiciona banner como segundo input se existir
    cmd.extend(banner_input)
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", map_v,
        "-map", "0:a?",
        # Output options
        "-fps_mode", "cfr",
        "-af", af,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path)
    ])

    ok, _, err = run_cmd(cmd, timeout=300)

    if not ok:
        # Mostra as ÚLTIMAS 800 chars do stderr (erro real, sem banner do FFmpeg)
        err_tail = err[-800:] if len(err) > 800 else err
        log(f"Erro no FFmpeg layout 3 camadas: {err_tail}", "ERROR")
        return False

    log(f"Layout ClipFlow 3 camadas aplicado com sucesso: {output_path}", "OK")
    return True

def process_trigger(trigger: TriggerEvent):
    """Processa um gatilho e gera o clip final.

    Cooldown de 60s só é aplicado APÓS clip salvo com sucesso.
    Se falhar/descartar: micro-intervalo de 10s antes de permitir nova avaliação.
    """
    global last_clip_time, clip_duration

    # Cooldown: 60s após clip salvo; 10s após falha/descarte
    now = time.time()
    time_since_last = now - last_clip_time

    # Verifica se estamos em cooldown pós-sucesso (60s) ou micro-intervalo pós-falha (10s)
    # Usamos uma flag implícita: se last_clip_time foi setado após sucesso, cooldown = 60s
    # Para distinguir, podemos verificar se o último clip foi salvo com sucesso
    # Simplificação: cooldown normal = 60s; após falha = 10s
    # Vamos usar uma variável para rastrear se o último foi sucesso

    # Cooldown padrão (após sucesso)
    if now - last_clip_time < COOLDOWN_BETWEEN_CLIPS:
        global _last_cooldown_log
        if now - _last_cooldown_log >= LOG_THROTTLE_SECONDS:
            log(f"Cooldown ativo, ignorando gatilho ({COOLDOWN_BETWEEN_CLIPS - (now - last_clip_time):.0f}s restantes)", "WARN")
            _last_cooldown_log = now
        return

    # Trava mínima: exige pelo menos 3 segmentos (30s) no buffer antes de aceitar gatilhos
    MIN_BUFFER_SEGMENTS = 3  # 3 segmentos * 10s = 30s mínimo
    if len(ring_buffer) < MIN_BUFFER_SEGMENTS:
        global _last_buffer_log
        if now - _last_buffer_log >= LOG_THROTTLE_SECONDS:
            log(f"Buffer insuficiente ({len(ring_buffer)}/{MIN_BUFFER_SEGMENTS} segmentos), ignorando gatilho inicial", "WARN")
            _last_buffer_log = now
        return

    log(f"🎯 GATILHO DETECTADO: {trigger.trigger_type} - {trigger.details}", "CLIP")

    # 1. Extrai últimos 45s do buffer
    raw_clip_path = LIVE_BUFFER_DIR / f"raw_clip_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ts"
    clip_duration = CLIP_DURATION

    if not extract_last_n_seconds_from_buffer(clip_duration, raw_clip_path):
        # Falha na extração: micro-intervalo de 10s
        last_clip_time = time.time() - (COOLDOWN_BETWEEN_CLIPS - 10)  # Define para 10s atrás
        return

    # 2. Transcreve com Groq Whisper (word timestamps)
    segments = transcribe_clip_groq(raw_clip_path, groq_api_key)
    if not segments:
        log("Falha na transcrição, pulando clip", "ERROR")
        # Falha: micro-intervalo de 10s
        last_clip_time = time.time() - (COOLDOWN_BETWEEN_CLIPS - 10)
        return

    # 3. Gera texto completo da transcrição para o gancho
    full_transcription = " ".join([seg.get('text', '') for seg in segments if seg.get('text')])

    # 4. Gera gancho + pergunta via Groq LLM (llama-3.3-70b-versatile)
    hook_data = generate_hook_and_question(full_transcription, groq_api_key)
    if not hook_data:
        # generate_hook_and_question já loga o motivo (nota baixa)
        # Limpa arquivos temporários
        try:
            raw_clip_path.unlink(missing_ok=True)
        except:
            pass
        # Descarte por IA: micro-intervalo de 10s
        last_clip_time = time.time() - (COOLDOWN_BETWEEN_CLIPS - 10)
        return

    # 5. Cria legenda karaokê .ass (estilo destaque palavra por palavra)
    ass_path = LIVE_BUFFER_DIR / f"subs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ass"
    if not create_karaoke_ass_subtitle(segments, clip_duration, ass_path):
        ass_path = None

    # 6. Aplica layout 3 camadas + pós-processamento
    final_clip_path = get_clip_filename()

    if not process_gameplay_layout(raw_clip_path, final_clip_path, current_live_config.webcam_position, ass_path, hook_data, clip_duration):
        # Falha no layout: micro-intervalo de 10s
        last_clip_time = time.time() - (COOLDOWN_BETWEEN_CLIPS - 10)
        return

    # VALIDAÇÃO REAL: Verifica se o arquivo foi criado e tem tamanho > 50KB
    if not final_clip_path.exists() or final_clip_path.stat().st_size < 50_000:
        log(f"❌ Clip gerado mas inválido (não existe ou < 50KB): {final_clip_path}", "ERROR")
        # Remove arquivo inválido se existir
        try:
            final_clip_path.unlink(missing_ok=True)
        except:
            pass
        # Falha: micro-intervalo de 10s
        last_clip_time = time.time() - (COOLDOWN_BETWEEN_CLIPS - 10)
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

    # 8. Limpeza de temporários (NÃO apaga cortes_prontos/)
    try:
        raw_clip_path.unlink(missing_ok=True)
        if ass_path:
            ass_path.unlink(missing_ok=True)
    except:
        pass

    # SUCESSO: incrementa contador AGORA (após validação) e aplica cooldown completo de 60s
    global clip_counter
    clip_counter += 1
    last_clip_time = time.time()

    log(f"🎉 CLIP PRONTO: {final_clip_path.name} | Gancho: {hook_data['titulo_gancho']} | Nota: {hook_data['nota']} | Tamanho: {final_clip_path.stat().st_size/1024:.0f} KB", "OK")

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

    # Descoberta automática de modelos ativos na Groq
    log("🔍 Descobrindo modelos ativos na Groq...", "INFO")
    global ACTIVE_GROQ_MODELS
    ACTIVE_GROQ_MODELS = fetch_active_groq_models(groq_api_key)

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

    # Thread 4: Manual trigger (ENTER no terminal)
    def manual_trigger_listener():
        """Escuta ENTER no terminal para forçar gatilho manual ('manual_trigger')."""
        import sys
        import msvcrt  # Windows
        log("🔘 Pressione ENTER a qualquer momento para forçar um clip manual ('manual_trigger')", "INFO")
        while not stop_event.is_set():
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key == b'\r' or key == b'\n':  # ENTER
                    trigger = TriggerEvent(
                        timestamp=time.time(),
                        trigger_type="manual_trigger",
                        details="Gatilho manual via ENTER no terminal (ignora thresholds)"
                    )
                    buffer_queue.put(("trigger", trigger))
                    log("⌨️ Gatilho manual ativado via ENTER!", "CLIP")
            time.sleep(0.1)

    t4 = threading.Thread(target=manual_trigger_listener, daemon=True)
    t4.start()
    threads.append(t4)

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

    # Limpeza segura: apaga APENAS arquivos temporários em temp_downloads/live_buffer/
    # NÃO apaga arquivos de cortes_prontos/
    try:
        for f in LIVE_BUFFER_DIR.glob("*.ts"):
            f.unlink(missing_ok=True)
        for f in LIVE_BUFFER_DIR.glob("*.ass"):
            f.unlink(missing_ok=True)
        for f in LIVE_BUFFER_DIR.glob("concat_list.txt"):
            f.unlink(missing_ok=True)
        for f in LIVE_BUFFER_DIR.glob("temp_concat.ts"):
            f.unlink(missing_ok=True)
        log("Arquivos temporários do buffer limpos", "OK")
    except Exception as e:
        log(f"Aviso ao limpar temporários: {e}", "WARN")

    log("=" * 60)
    log(f"MONITOR FINALIZADO - {clip_counter} clips gerados", "OK")
    log(f"Clips salvos em: {LIVE_CLIPS_DIR}", "OK")
    log("=" * 60)

    return 0

if __name__ == "__main__":
    sys.exit(main())