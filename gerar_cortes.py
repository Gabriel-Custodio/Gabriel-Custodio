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
import time
import asyncio
import argparse
import base64
import hashlib
from pathlib import Path
from datetime import datetime


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
VISUAL_FRAME_INTERVAL = 30  # Extrair frame a cada 30 segundos
MAX_VISUAL_FRAMES = 20

# Configurações do Modo Edição Completa por Visão IA (constantes do Gabriel; o modo ainda não
# foi implementado, nenhuma função usa estes valores por enquanto)
VISION_EDIT_FRAME_INTERVAL = 4  # 1 frame a cada 4 segundos
VISION_EDIT_FRAME_WIDTH = 480   # Largura dos frames para análise
VISION_EDIT_MAX_FRAMES = 180    # Máximo de frames (para vídeos longos)
VISION_EDIT_VIDEOS_MIN = 1      # Mínimo de vídeos curtos a gerar
VISION_EDIT_VIDEOS_MAX = 3      # Máximo de vídeos curtos a gerar
VISION_EDIT_SCENES_MIN = 5      # Mínimo de cenas por vídeo
VISION_EDIT_SCENES_MAX = 8      # Máximo de cenas por vídeo
VISION_EDIT_TARGET_DURATION_MIN = 30  # Duração mínima por vídeo (segundos)
VISION_EDIT_TARGET_DURATION_MAX = 45  # Duração máxima por vídeo (segundos)

# Groq: chat (visão e roteiros). Os nomes dos modelos mudam a cada poucos meses; troque no
# .env (GROQ_VISION_MODEL / GROQ_TEXT_MODEL) sem mexer no código. Veja .env.example.
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
DEFAULT_GROQ_VISION_MODEL = "qwen/qwen3.8-27b"
DEFAULT_GROQ_TEXT_MODEL = "openai/gpt-oss-120b"

# Modelos descontinuados/que retornam 404 (removidos da lista de fallback)
DEPRECATED_GROQ_MODELS = {
    "llama-3.1-8b-instant",
    "llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "qwen/qwen3-32b",
}

# Fallbacks estáticos ordenados por preferência (apenas modelos conhecidos como estáveis)
STATIC_TEXT_MODEL_FALLBACKS = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "gemma2-9b-it",
]
STATIC_VISION_MODEL_FALLBACKS = [
    "qwen/qwen3.8-27b",
    "llama-3.2-90b-vision-preview",
    "llama-3.2-11b-vision-preview",
]

MAX_IMAGES_PER_REQUEST = 3  # limite de imagens por chamada do modelo de visão
MIN_CLIMAX_SCORE = 5  # nota mínima (0-10) para um frame virar corte
SCRIPT_WORDS_PER_SECOND = 2.5  # ~150 palavras por minuto em português
# Narração curta demais vira corte de poucos segundos (o vídeo é esticado/acelerado para durar
# o mesmo que o áudio). Abaixo disso o corte é recusado.
MIN_NARRATION_SECONDS = 15
MAX_VIDEO_SPEEDUP = 2.0  # o trecho original nunca é acelerado mais que isso para caber na narração
MIN_SCRIPT_FILL = 0.6  # a IA deve escrever pelo menos 60% do máximo de palavras do trecho
FADE_OUT_SECONDS = 0.5  # fade de áudio e vídeo no final, para o corte não terminar seco
ENV_PATH = Path(__file__).parent / ".env"

# ========== CONFIGURAÇÕES GAMEPLAY ==========
GAMEPLAY_TIME_BANDS = 6  # Dividir vídeo em 6 faixas temporais
GAMEPLAY_CLIPS_PER_BAND = 2  # Clipes máximos por faixa
GAMEPLAY_MIN_GAP_SECONDS = 300  # 5 minutos mínimo entre cortes
GAMEPLAY_DEFAULT_CLIPS = 5  # Padrão de cortes a gerar
GAMEPLAY_MIN_CLIPS = 1
GAMEPLAY_MAX_CLIPS = 10

# Otimizações de retenção/gancho (NOVO 2025)
GAMEPLAY_CLIP_DURATION_MIN = 32  # Duração ideal mínima (era 30)
GAMEPLAY_CLIP_DURATION_MAX = 45  # Duração ideal máxima (era 55)
GAMEPLAY_FADE_OUT_SECONDS = 0.2  # Fade rápido apenas áudio (era 1.5s vídeo+áudio)
GAMEPLAY_HOOK_BANNER_Y = 140  # Posição Y da faixa superior (terço superior)
GAMEPLAY_HOOK_BANNER_HEIGHT = 120  # Altura da faixa
GAMEPLAY_HOOK_BANNER_DURATION = 4  # Duração da faixa em segundos
GAMEPLAY_SUBTITLE_WORDS_PER_CHUNK = 3  # Palavras por bloco de legenda (era 4)
GAMEPLAY_WATERMARK_PATH = "assets/logo.png"  # Logo/watermark opcional

# Seleção de momentos do modo gameplay: o CÓDIGO monta os candidatos a partir da transcrição real
# (início, fim e texto saem dela); a IA só escolhe entre candidatos numerados. Nunca calcula
# tempo nem escreve o texto do corte.
GAMEPLAY_CANDIDATES_PER_BAND = 8    # candidatos reais que a IA recebe por faixa
GAMEPLAY_CANDIDATE_STEP = 8         # segundos mínimos entre inícios de candidatos
GAMEPLAY_CANDIDATE_TEXT_CHARS = 600 # texto de cada candidato enviado à IA
GAMEPLAY_MIN_CLIP_WORDS = 30        # menos palavras que isso = trecho sem fala suficiente
GAMEPLAY_MIN_WORDS_PER_SECOND = 0.8 # densidade mínima de fala no trecho (palavras por segundo)
GAMEPLAY_PAD_BEFORE = 0.3           # folga antes da primeira fala (s)
GAMEPLAY_PAD_AFTER = 0.5            # folga depois da última fala (s)

# ========== CONFIGURAÇÕES CLIPFLOW 3 CAMADAS (NOVO 2025 - igual monitor_lives.py) ==========
CLIPFLOW_WEBAM_HEIGHT = 730                    # Altura do bloco webcam (topo)
CLIPFLOW_HOOK_BAR_HEIGHT = 180                 # Altura da faixa divisória de gancho (meio)
CLIPFLOW_GAMEPLAY_HEIGHT = 1010                # Altura do bloco gameplay (base)
CLIPFLOW_TOTAL_HEIGHT = CLIPFLOW_WEBAM_HEIGHT + CLIPFLOW_HOOK_BAR_HEIGHT + CLIPFLOW_GAMEPLAY_HEIGHT  # 1920
CLIPFLOW_HOOK_MODEL = DEFAULT_GROQ_TEXT_MODEL  # Modelo para gerar gancho/pergunta
CLIPFLOW_HOOK_MIN_SCORE = 65                   # Nota mínima para aceitar o gancho
CLIPFLOW_KARAOKE_WORDS_PER_CHUNK = 5           # Palavras por bloco karaokê (3-5)
CLIPFLOW_KARAOKE_FONT_SIZE = 68                # Tamanho da fonte karaokê
CLIPFLOW_KARAOKE_MARGIN_V = 220                # MarginV para área do gameplay (base)

# Layout "fit" (PADRÃO): o vídeo original inteiro, em escala 1:1, dentro da tela 9:16 (1080x1920).
# Nada é recortado nem ampliado além do necessário para caber na largura; o espaço que sobra em
# cima e embaixo recebe um fundo (cópia desfocada do próprio vídeo, ou preto). O layout antigo
# de 3 camadas (webcam recortada + gameplay com zoom) fica disponível com CORTES_LAYOUT=clipflow.
DEFAULT_LAYOUT = "cam"          # "cam", "fit" ou "clipflow"; troque no .env com CORTES_LAYOUT
# Layout "cam" (PADRÃO no modo gameplay): de cima para baixo, a CÂMERA do streamer (largura toda), uma faixa
# vermelha com a legenda e a linha fixa "curta, comente e compartilhe" e o GAMEPLAY preenchendo o resto
# (recorte central, sem barras). A câmera é recortada na proporção do bloco, com uma folga em volta da caixa
# indicada (IA, --webcam ou --camera). Sem câmera encontrada (ou nos modos IA/áudio original), sai no layout fit.
CAM_TOP_HEIGHT = 600            # altura do bloco da câmera
CAM_BAND_HEIGHT = 220           # altura da faixa vermelha (legenda em cima, linha fixa embaixo)
CAM_GAME_HEIGHT = VERTICAL_HEIGHT - CAM_TOP_HEIGHT - CAM_BAND_HEIGHT   # 1100
CAM_CROP_MARGIN = 0.06          # folga em volta da caixa da câmera (fração do tamanho)
CAM_MIN_CROP_FRAC = 0.18        # o recorte nunca é menor que isso da largura do vídeo (caixa só do rosto => amplia demais)
CAM_WARN_UPSCALE = 2.2          # acima disso a câmera pode ficar borrada: avisa e anota no .json
# Layout PERSONALIZADO ("custom"): o usuário escolhe, no editor da interface, a caixa da câmera, a caixa do
# gameplay e a altura do bloco da câmera; vira um arquivo em layouts_salvos/ e é usado com --layout-salvo NOME.
LAYOUTS_DIR = Path("layouts_salvos")
CUSTOM_CAM_MIN_HEIGHT = 300
CUSTOM_CAM_MAX_HEIGHT = 1100
CUSTOM_ORDERS = ("cam_primeiro", "game_primeiro")
CAM_SUBTITLE_CENTER_Y = 86      # centro da legenda (px, dentro da faixa)
CAM_BAND_CTA_SIZE = 34          # fonte da linha fixa dentro da faixa
CAM_BAND_CTA_Y = 160            # posição vertical (px dentro da faixa) da linha fixa
FIT_BACKGROUND = "blur"         # "blur" (fundo desfocado) ou "black" (preto); .env: CORTES_FUNDO
FIT_SHOW_HOOK = True            # faixa do gancho acima do vídeo, se sobrar espaço
FIT_HOOK_GAP = 24               # distância (px) entre a faixa do gancho e o vídeo
FIT_SUBTITLE_MARGIN_V = 420     # altura da legenda (px a partir da base), na área abaixo do vídeo
QUALITY_CRF = 17                # qualidade da saída (menor = melhor; 17 é quase sem perdas visíveis)
QUALITY_PRESET = "medium"
INTERMEDIATE_CRF = 12           # pré-corte (arquivo temporário): quase sem perdas para não somar perda

# Resumo narrado no início (modo gameplay): o vídeo "para" no primeiro quadro, uma voz resume o
# assunto do trecho (só com o que foi dito nele) e então o corte começa. LIGADO por padrão;
# desligue com --sem-resumo, com CORTES_RESUMO=0 no .env ou pela caixa na interface.
SUMMARY_INTRO_DEFAULT = True
SUMMARY_MIN_WORDS = 6           # resumo mais curto que isso não ajuda ninguém
SUMMARY_MAX_WORDS = 20          # 1 a 2 frases (~5 a 8 s de fala)
SUMMARY_MAX_SPOKEN_SECONDS = 9.0  # fala mais longa que isso: tenta de novo mais rápida, senão desiste
SUMMARY_FAST_RATE = "+15%"
SUMMARY_PAUSE_AFTER = 0.35      # respiro (s) entre o fim da fala e o início do corte
SUMMARY_MAX_TOTAL_SECONDS = 60  # intro + corte nunca passam disso
SUMMARY_MIN_GROUNDING = 0.3     # fração mínima das palavras "de conteúdo" do resumo que existe na fala real
SUMMARY_SUBTITLE_FONT_SIZE = 64
# Tarja vermelha do layout fit: título do gancho em cima + uma linha FIXA pedindo curtir/comentar/compartilhar.
# Troque o texto com CORTES_BANNER_CTA no .env; CORTES_BANNER_CTA=0 tira a linha.
FIT_BANNER_CTA_TEXT = "CURTA · COMENTE · COMPARTILHE"
FIT_BANNER_TITLE_SIZE = 50      # tamanho do título quando a linha fixa está ligada (o layout antigo usa 28)
FIT_BANNER_CTA_SIZE = 34
FIT_BANNER_MAX_TEXT_WIDTH = 1000  # px; o texto encolhe até caber

# Chamada final (CTA): depois do corte, o último quadro fica parado e a voz pede like, compartilhamento
# e inscrição (com a mesma frase na legenda). LIGADA por padrão; desligue com --sem-cta, CORTES_CTA=0
# ou pela caixa da interface. A frase pode ser trocada no .env (CORTES_CTA_TEXT).
CTA_DEFAULT = True
CTA_TEXT = "Gostou? Deixe o like, comente, compartilhe com um amigo e se inscreva no canal para ver mais cortes!"
CTA_MAX_WORDS = 25
CTA_MAX_SPOKEN_SECONDS = 8.0
CTA_PAUSE_AFTER = 0.6
CTA_FRAME_BEFORE_END = 1.6      # o corte termina com fade para preto; o quadro parado vem de antes do fade
CTA_FADE_SECONDS = 0.4
# Canais que são de CORTES/compilações: o nome do canal não é o do streamer, então não é usado sozinho
# (o resumo diz "o streamer"; informe o nome com --streamer ou na interface).
SUMMARY_CUT_CHANNEL_HINTS = ("corte", "cortes", "clip", "clips", "highlight", "highlights", "melhores momentos",
                             "compila", "best of", "momentos", "shorts", "viral")
# ====================================


def clean_youtube_url(url):
    """
    Limpa a URL do YouTube mantendo apenas o ID principal do vídeo.
    Remove parâmetros de tracking, playlist, timestamp, etc.
    Para URLs não-YouTube (Twitch, Kick, etc.), retorna a URL original inalterada.
    """
    youtube_patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
    ]

    for pattern in youtube_patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            return f"https://www.youtube.com/watch?v={video_id}"

    if re.match(r'https://www\.youtube\.com/watch\?v=[a-zA-Z0-9_-]{11}$', url):
        return url

    return url


def is_youtube_url(url):
    """Verifica se a URL é do YouTube (inclui youtu.be, shorts, etc.)."""
    youtube_domains = ['youtube.com', 'youtu.be']
    return any(domain in url.lower() for domain in youtube_domains)


def extract_audio_for_transcription(video_path, output_dir, chunk_duration=AUDIO_CHUNK_DURATION):
    """
    Extrai áudio do vídeo em MP3 mono 64kbps.
    Se vídeo > 20 min, divide em chunks de 15 min.
    Retorna lista de tuplas (caminho_do_audio, deslocamento_em_segundos).
    """
    print(f"🎵 Extraindo áudio para transcrição...")

    video_duration = get_video_duration(video_path)
    print(f"   Duração do vídeo: {video_duration/60:.1f} min")

    audio_files = []

    if video_duration <= MAX_VIDEO_DURATION_FOR_SINGLE_CHUNK:
        audio_path = output_dir / "audio_full.mp3"
        cmd = [
            FFMPEG.as_posix(), "-y",
            "-i", str(video_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-b:a", "64k",
            "-f", "mp3",
            str(audio_path)
        ]
        ok, _, err = run_cmd(cmd)
        if ok and audio_path.exists():
            audio_files.append((audio_path, 0.0))
            print(f"   ✅ Áudio extraído: {audio_path}")
        else:
            print(f"   ❌ Falha ao extrair áudio: {err[:200]}")
    else:
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
            ok, _, err = run_cmd(cmd)
            if ok and audio_path.exists():
                audio_files.append((audio_path, float(start_time)))
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
                'timestamp_granularities[]': 'word',
                'timestamp_granularities[]': 'segment',
                'language': 'pt',
                'prompt': 'Transcrição apenas da fala em português do streamer brasileiro. Ignore falas do jogo em inglês e efeitos sonoros.'
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
            if not text:
                continue

            words = text.split()
            if len(words) <= 2:
                english_like = sum(1 for w in words if len(w) > 2 and w[0].isupper() and w.isalpha())
                if english_like >= len(words) * 0.5:
                    continue

            segment_data = {
                'start': seg.get('start', 0),
                'end': seg.get('end', 0),
                'text': text
            }

            if 'words' in seg and seg['words']:
                segment_data['words'] = seg['words']

            segments.append(segment_data)

        filtered_segments = []
        for seg in segments:
            text = seg['text'].strip()
            words = text.split()
            english_words = sum(1 for w in words if w and w[0].isupper() and w.isalpha() and len(w) > 2)
            if words and english_words / len(words) > 0.8:
                if seg['end'] - seg['start'] > 3:
                    continue
            filtered_segments.append(seg)

        print(f"   ✅ Transcrição concluída: {len(filtered_segments)} segmentos (filtrados {len(segments) - len(filtered_segments)} ruído/inglês)")
        return filtered_segments

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
            "-q:v", "2",
            "-vf", "scale=1280:720",
            str(frame_path)
        ]
        ok, _, _ = run_cmd(cmd)
        if ok and frame_path.exists():
            frame_paths.append({
                'path': frame_path,
                'timestamp': timestamp
            })
        else:
            print(f"   ⚠️  Falha ao extrair frame em {timestamp:.0f}s")

    print(f"   ✅ {len(frame_paths)} frames extraídos")
    return frame_paths


def load_env_file(env_path=ENV_PATH):
    """Lê o .env (linhas CHAVE=valor) para os.environ, sem sobrescrever o que já existe."""
    env_path = Path(env_path)
    if not env_path.exists():
        return
    try:
        with open(env_path, 'r', encoding='utf-8-sig') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                if key.startswith('export '):
                    key = key[len('export '):].strip()
                value = value.strip().strip('"\'')
                if key and value:
                    os.environ.setdefault(key, value)
    except Exception as e:
        print(f"⚠️  Não foi possível ler o .env: {e}")


def get_groq_key():
    """Chave da API do Groq (vem do .env ou do ambiente) ou None."""
    return os.environ.get("GROQ_API_KEY") or None


def get_vision_model():
    """Modelo do Groq para analisar imagens (troque no .env: GROQ_VISION_MODEL)."""
    return os.environ.get("GROQ_VISION_MODEL") or DEFAULT_GROQ_VISION_MODEL


def get_text_model():
    """Modelo do Groq para escrever roteiros (troque no .env: GROQ_TEXT_MODEL)."""
    return os.environ.get("GROQ_TEXT_MODEL") or DEFAULT_GROQ_TEXT_MODEL


_ACTIVE_GROQ_TEXT_MODELS: list[str] = []
_ACTIVE_GROQ_VISION_MODELS: list[str] = []
_MODELS_FETCHED = False


def fetch_active_groq_models(api_key: str) -> tuple[list[str], list[str]]:
    """
    Consulta a API da Groq para listar modelos realmente ativos na conta.
    """
    global _ACTIVE_GROQ_TEXT_MODELS, _ACTIVE_GROQ_VISION_MODELS, _MODELS_FETCHED

    if _MODELS_FETCHED:
        return _ACTIVE_GROQ_TEXT_MODELS, _ACTIVE_GROQ_VISION_MODELS

    try:
        import requests
        headers = {'Authorization': f'Bearer {api_key}'}
        response = requests.get(GROQ_MODELS_URL, headers=headers, timeout=15)

        if response.status_code != 200:
            print(f"   ⚠️  Groq models API retornou {response.status_code}: {response.text[:200]}")
            _MODELS_FETCHED = True
            return [], []

        data = response.json()
        models = data.get('data', [])
        exclude_keywords = ['whisper', 'guard', 'tts', 'orpheus', 'audio', 'speech', 'translation']

        text_models = []
        vision_models = []

        for model in models:
            model_id = model.get('id', '').lower()
            if any(kw in model_id for kw in exclude_keywords):
                continue
            if model.get('id') in DEPRECATED_GROQ_MODELS:
                continue

            is_vision = 'vision' in model_id or 'qwen' in model_id
            if is_vision:
                vision_models.append(model.get('id'))
            else:
                text_models.append(model.get('id'))

        _ACTIVE_GROQ_TEXT_MODELS = text_models
        _ACTIVE_GROQ_VISION_MODELS = vision_models
        _MODELS_FETCHED = True

        if text_models or vision_models:
            print(f"   ✅ Modelos Groq ativos: {len(text_models)} texto, {len(vision_models)} visão")

        return text_models, vision_models

    except Exception as e:
        print(f"   ⚠️  Falha ao buscar modelos Groq (usando fallbacks estáticos): {e}")
        _MODELS_FETCHED = True
        return [], []


def get_text_models_to_try(api_key: str | None = None) -> list[str]:
    """Retorna lista de modelos de texto para tentar."""
    models = []

    env_model = os.environ.get("GROQ_TEXT_MODEL")
    if env_model and env_model not in DEPRECATED_GROQ_MODELS:
        models.append(env_model)

    preferred = "qwen/qwen3.8-27b"
    if preferred not in models and preferred not in DEPRECATED_GROQ_MODELS:
        models.append(preferred)

    if api_key:
        text_models, _ = fetch_active_groq_models(api_key)
        for m in text_models:
            if m not in models and m not in DEPRECATED_GROQ_MODELS:
                models.append(m)

    for fb in STATIC_TEXT_MODEL_FALLBACKS:
        if fb not in models and fb not in DEPRECATED_GROQ_MODELS:
            models.append(fb)

    return models


def get_vision_models_to_try(api_key: str | None = None) -> list[str]:
    """Retorna lista de modelos de visão para tentar."""
    models = []

    env_model = os.environ.get("GROQ_VISION_MODEL")
    if env_model and env_model not in DEPRECATED_GROQ_MODELS:
        models.append(env_model)

    preferred = "qwen/qwen3.8-27b"
    if preferred not in models and preferred not in DEPRECATED_GROQ_MODELS:
        models.append(preferred)

    if api_key:
        _, vision_models = fetch_active_groq_models(api_key)
        for m in vision_models:
            if m not in models and m not in DEPRECATED_GROQ_MODELS:
                models.append(m)

    for fb in STATIC_VISION_MODEL_FALLBACKS:
        if fb not in models and fb not in DEPRECATED_GROQ_MODELS:
            models.append(fb)

    return models


def groq_chat_with_fallback(messages, api_key, model_getter, max_tokens=4000, temperature=0.7, timeout=120, retries=3):
    """Chama o chat/completions do Groq com fallback automático entre modelos."""
    models_to_try = model_getter(api_key) if callable(model_getter) else model_getter(api_key)

    for model in models_to_try:
        result = groq_chat(messages, api_key, model, max_tokens, temperature, timeout, retries=1)
        if result is not None:
            if model != models_to_try[0]:
                print(f"   ✅ Fallback funcionou com modelo: {model}")
            return result
        print(f"   ⚠️  Modelo {model} indisponível, tentando próximo...")

    print(f"   ❌ Todos os modelos falharam")
    return None


def groq_chat(messages, api_key, model, max_tokens=4000, temperature=0.7, timeout=120, retries=3):
    """Chama o chat/completions do Groq e devolve o texto da resposta (ou None se falhar)."""
    try:
        import requests
    except ImportError:
        print("   ❌ requests não instalado. Rode: pip install -r requirements.txt")
        return None

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
    }
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}

    for attempt in range(1, retries + 1):
        wait = min(2 ** attempt * 2, 30)
        try:
            response = requests.post(GROQ_CHAT_URL, json=payload, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            print(f"   ⚠️  Falha de rede ao chamar o Groq ({model}): {e}")
            response = None

        if response is not None:
            if response.status_code == 200:
                try:
                    content = response.json()['choices'][0]['message']['content']
                except (KeyError, IndexError, ValueError):
                    print(f"   ❌ Resposta inesperada do Groq ({model})")
                    return None
                return re.sub(r'<think>.*?</think>', '', content or '', flags=re.DOTALL).strip()
            if response.status_code not in (429, 500, 502, 503, 504):
                print(f"   ❌ Groq {response.status_code} ({model}): {response.text[:300]}")
                return None
            try:
                wait = min(float(response.headers.get('retry-after', wait)), 30)
            except ValueError:
                pass
            print(f"   ⚠️  Groq {response.status_code}, tentativa {attempt}/{retries}...")

        if attempt < retries:
            time.sleep(wait)
    return None


def extract_json(text):
    """Extrai o primeiro JSON (lista ou objeto) de uma resposta."""
    if not text:
        return None
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    for opener, closer in (('[', ']'), ('{', '}')):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except ValueError:
                continue
    return None


def narration_too_short(tts_duration, clip_duration):
    """True se a narração é curta demais para o trecho."""
    if not tts_duration or tts_duration <= 0:
        return False
    minimum = max(MIN_NARRATION_SECONDS, clip_duration / MAX_VIDEO_SPEEDUP)
    return tts_duration < minimum


def fade_out_filters(total_duration):
    """Devolve (filtro_de_video, filtro_de_audio) de fade-out nos últimos FADE_OUT_SECONDS."""
    if not total_duration or total_duration < 2 * FADE_OUT_SECONDS + 1:
        return "", ""
    start = total_duration - FADE_OUT_SECONDS
    return (f"fade=t=out:st={start:.2f}:d={FADE_OUT_SECONDS}",
            f"afade=t=out:st={start:.2f}:d={FADE_OUT_SECONDS}")


def fade_out_filters_gameplay(total_duration):
    """Devolve (filtro_de_video, filtro_de_audio) para modo gameplay."""
    if not total_duration or total_duration < GAMEPLAY_FADE_OUT_SECONDS + 0.5:
        return "", ""
    start = total_duration - GAMEPLAY_FADE_OUT_SECONDS
    return ("", f"afade=t=out:st={start:.2f}:d={GAMEPLAY_FADE_OUT_SECONDS}")


def clean_script(text, max_words, min_words=15):
    """Limpa o roteiro devolvido pela IA e garante tamanho e pontuação final."""
    if not text:
        return None
    text = re.sub(r'[*_#`>]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip().strip('"“”\'')
    words = text.split()
    if len(words) < min_words:
        return None
    if len(words) > max_words:
        truncated = ' '.join(words[:max_words])
        last_punct = max(truncated.rfind(p) for p in '.!?…')
        text = truncated[:last_punct + 1] if last_punct > len(truncated) * 0.5 else truncated.rstrip(',;:') + '.'
    if not re.search(r'[.!?…]$', text):
        text += '.'
    return text


def generate_viral_script_ai(original_text, target_duration, api_key):
    """Reescreve o trecho do vídeo como narração viral usando um modelo de texto do Groq."""
    max_words = int(target_duration * SCRIPT_WORDS_PER_SECOND)
    min_words = max(15, int(max_words * MIN_SCRIPT_FILL))
    prompt = (
        f"O texto abaixo é a transcrição automática (sem pontuação, pode ter erros de reconhecimento) "
        f"de um trecho de cerca de {target_duration:.0f} segundos de um vídeo.\n\n"
        f"TRANSCRIÇÃO:\n{original_text[:6000]}\n\n"
        f"Escreva a narração de um corte vertical (TikTok/Shorts) a partir dela, em português do Brasil "
        f"(traduza se a transcrição estiver em outro idioma).\n"
        f"Regras:\n"
        f"1. Seja fiel ao conteúdo: não invente fatos, nomes, números ou resultados que não estejam no texto.\n"
        f"2. Estrutura: gancho forte na primeira frase, desenvolvimento curto e fechamento com uma chamada para ação breve.\n"
        f"3. Frases curtas, com pontuação (vírgulas, pontos, exclamações) para a voz respirar.\n"
        f"4. Entre {min_words} e {max_words} palavras (a narração precisa durar quase o trecho todo).\n"
        f"5. Sem emojis, markdown, aspas ou títulos.\n"
        f"Responda APENAS com o texto da narração."
    )
    for _ in range(2):
        answer = groq_chat_with_fallback(
            [{"role": "user", "content": prompt}],
            api_key,
            get_text_models_to_try,
            max_tokens=4000,
            temperature=0.7
        )
        script = clean_script(answer, max_words, min_words=min_words)
        if script:
            return script
        print(f"   ⚠️  Roteiro da IA curto demais (mínimo {min_words} palavras); tentando de novo...")
    return None


def _describe_frame_batch(batch, api_key, model):
    """Pede ao modelo de visão a descrição e a nota de cada frame de um lote."""
    content = [{
        "type": "text",
        "text": (
            "Você analisa frames de um vídeo (provavelmente gameplay) para achar momentos bons para cortes virais. "
            f"Vou enviar {len(batch)} imagem(ns), cada uma precedida pela sua numeração.\n"
            "Para CADA imagem responda:\n"
            "- frame: o número da imagem\n"
            "- description: o que está acontecendo, em 1 frase, descrevendo SÓ o que dá para ver\n"
            "- score: de 0 a 10, o potencial de corte viral (vitória, descoberta, reação forte, fim de desafio, "
            "momento engraçado ou surpreendente; menu, tela de carregamento ou cena parada = 0 a 2)\n"
            "- reason: por que essa nota, em poucas palavras\n"
            "Responda APENAS com uma lista JSON válida, por exemplo: "
            '[{"frame": 1, "description": "O jogador derrota o chefe final", "score": 9, "reason": "vitória épica"}]'
        )
    }]
    for i, frame in enumerate(batch, 1):
        try:
            with open(frame['path'], 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('utf-8')
        except OSError as e:
            print(f"   ⚠️  Erro ao ler frame: {e}")
            return []
        content.append({"type": "text", "text": f"Imagem {i}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    answer = groq_chat([{"role": "user", "content": content}], api_key, model, temperature=0.3)
    data = extract_json(answer)
    if not isinstance(data, list):
        print("   ⚠️  Resposta de visão sem JSON válido neste lote")
        return []

    described = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get('frame')) - 1
            score = max(0.0, min(10.0, float(item.get('score', 0))))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(batch):
            described.append({
                'timestamp': batch[idx]['timestamp'],
                'description': str(item.get('description', '')).strip(),
                'score': score,
                'reason': str(item.get('reason', '')).strip() or 'clímax visual',
            })
    return described


def _pick_climax_frames(described, num_clips=3, min_gap=TARGET_DURATION_MAX, min_score=MIN_CLIMAX_SCORE):
    """Escolhe os frames de maior nota, afastados entre si."""
    chosen = []
    for frame in sorted(described, key=lambda f: f['score'], reverse=True):
        if frame['score'] < min_score:
            break
        if HISTORICO is not None and HISTORICO.repetido(frame['timestamp'] - 20, frame['timestamp'] + 20):
            continue                      # esse momento já virou corte antes
        if all(abs(frame['timestamp'] - c['timestamp']) >= min_gap for c in chosen):
            chosen.append(frame)
            if len(chosen) >= num_clips:
                break
    return sorted(chosen, key=lambda f: f['timestamp'])


def _write_script_for_moment(moment, described, api_key):
    """Escreve a narração de um clímax usando SÓ as descrições reais dos frames do vídeo."""
    timeline = "\n".join(
        f"[{int(f['timestamp'] // 60):02d}:{int(f['timestamp'] % 60):02d}] {f['description']}"
        for f in sorted(described, key=lambda f: f['timestamp']) if f['description']
    )
    ts = moment['timestamp']
    max_words = int(TARGET_DURATION_MAX * SCRIPT_WORDS_PER_SECOND * 0.85)
    prompt = (
        f"Abaixo está a linha do tempo de um vídeo, descrita frame a frame:\n{timeline}\n\n"
        f"Escreva a narração de um corte vertical (TikTok/Shorts) de cerca de 40 segundos centrado em "
        f"[{int(ts // 60):02d}:{int(ts % 60):02d}] ({moment['reason']}), em português do Brasil.\n"
        f"Regras:\n"
        f"1. Baseie-se SÓ no que a linha do tempo mostra: não invente fatos, nomes, números ou resultados.\n"
        f"2. Estrutura: gancho forte na primeira frase, desenvolvimento e fechamento com uma chamada para ação breve.\n"
        f"3. Frases curtas, com pontuação (vírgulas, pontos, exclamações) para a voz respirar.\n"
        f"4. Entre 80 e {max_words} palavras.\n"
        f"5. Sem emojis, markdown, aspas ou títulos.\n"
        f"Responda APENAS com o texto da narração."
    )
    answer = groq_chat_with_fallback(
        [{"role": "user", "content": prompt}],
        api_key,
        get_text_models_to_try,
        max_tokens=4000,
        temperature=0.7
    )
    return clean_script(answer, max_words, min_words=int(TARGET_DURATION_MIN * SCRIPT_WORDS_PER_SECOND * 0.6))


def analyze_frames_with_ai(frame_paths, api_key):
    """Passo C: analisa os frames com IA e escolhe os clímax do vídeo."""
    if not api_key:
        print("   ❌ Passo C precisa da GROQ_API_KEY (configure no .env).")
        return []
    if not frame_paths:
        return []

    vision_models = get_vision_models_to_try(api_key)
    print(f"🤖 Analisando {len(frame_paths)} frames (modelos: {', '.join(vision_models[:3])}{'...' if len(vision_models) > 3 else ''})...")

    batches = [frame_paths[i:i + MAX_IMAGES_PER_REQUEST]
               for i in range(0, len(frame_paths), MAX_IMAGES_PER_REQUEST)]

    described = []
    for n, batch in enumerate(batches, 1):
        print(f"   Lote {n}/{len(batches)}...")
        batch_described = []
        for model in vision_models:
            batch_described = _describe_frame_batch(batch, api_key, model)
            if batch_described:
                if model != vision_models[0]:
                    print(f"      ✅ Fallback visão funcionou com: {model}")
                break
            print(f"      ⚠️  Modelo {model} falhou, tentando próximo...")
        described.extend(batch_described)

    if not described:
        print("   ❌ A IA não devolveu nenhuma descrição de frame.")
        return []

    chosen = _pick_climax_frames(described)
    if not chosen:
        best = max(f['score'] for f in described)
        print(f"   ⚠️  Nenhum frame passou da nota mínima ({MIN_CLIMAX_SCORE}); a melhor foi {best:.0f}.")
        return []

    climax_moments = []
    for moment in chosen:
        script = _write_script_for_moment(moment, described, api_key)
        if script:
            climax_moments.append({
                'timestamp': moment['timestamp'],
                'reason': moment['reason'],
                'script': script,
            })
        else:
            print(f"   ⚠️  Sem roteiro para o clímax em {moment['timestamp']:.0f}s; pulando.")
    return climax_moments


def generate_climax_clips_from_visual(video_path, _frame_paths, climax_moments, output_dir, clip_num_start=1):
    """Para cada momento de clímax identificado, gera o corte completo."""
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

        window_half = 20
        clip_start = max(0, timestamp - window_half)
        clip_end = min(video_duration, timestamp + window_half)

        target_duration = clip_end - clip_start
        if target_duration < TARGET_DURATION_MIN:
            needed = TARGET_DURATION_MIN - target_duration
            clip_start = max(0, clip_start - needed / 2)
            clip_end = min(video_duration, clip_end + needed / 2)
            target_duration = clip_end - clip_start
        elif target_duration > TARGET_DURATION_MAX:
            excess = target_duration - TARGET_DURATION_MAX
            clip_start += excess / 2
            clip_end -= excess / 2
            target_duration = clip_end - clip_start

        temp_segment_path = TEMP_DIR / f"segment_visual_{clip_idx}_{int(clip_start)}s_{int(target_duration)}s.mp4"

        if not extract_video_segment(video_path, clip_start, target_duration, temp_segment_path):
            print(f"   ⚠️  Falha no pré-corte, tentando usar vídeo original (pode causar timeout)...")
            input_video_path = video_path
            use_temp_segment = False
        else:
            input_video_path = temp_segment_path
            use_temp_segment = True

        tts_audio_path = TEMP_DIR / f"visual_clip_{clip_idx}_tts.mp3"
        success, tts_duration, word_events = asyncio.run(
            generate_tts_audio(script, tts_audio_path)
        )

        if not success or not tts_audio_path.exists():
            print(f"   ❌ Falha no TTS para corte {clip_idx}")
            if use_temp_segment and temp_segment_path.exists():
                try:
                    temp_segment_path.unlink()
                except:
                    pass
            continue

        print(f"   🔊 Áudio TTS: {tts_duration:.1f}s ({len(word_events)} eventos WordBoundary)")

        if narration_too_short(tts_duration, target_duration):
            print(f"   ❌ Narração curta demais ({tts_duration:.1f}s para um trecho de {target_duration:.0f}s); "
                  f"corte {clip_idx} descartado em vez de gerar um vídeo de poucos segundos.")
            if use_temp_segment and temp_segment_path.exists():
                try:
                    temp_segment_path.unlink()
                except:
                    pass
            continue

        ass_path = TEMP_DIR / f"visual_clip_{clip_idx}_subs.ass"
        if not create_karaoke_ass_subtitle_from_tts(word_events, ass_path, target_duration):
            print(f"   ⚠️  Falha ao criar legendas karaokê, continuando sem...")
            ass_path = None

        safe_reason = clean_filename(reason[:40])
        output_name = f"corte_visual_{clip_idx}_{safe_reason}.mp4"
        output_path = output_dir / output_name

        clip_obj = {
            'start': clip_start,
            'end': clip_end,
            'duration': clip_end - clip_start,
            'text': script,
            'score': 100
        }

        final_duration = tts_duration
        speed_ratio = clip_obj['duration'] / final_duration if final_duration > 0 else 1.0

        api_key = get_groq_key()
        if api_key:
            print("   🤖 Gerando gancho viral e CTA com IA...")
            hook_data = generate_hook_and_question(script, clip_obj['duration'], api_key)
        else:
            hook_data = {
                "titulo_gancho": "MOMENTO INCRÍVEL",
                "pergunta_cta": "O QUE ACHOU?",
                "nota": 75
            }

        success = process_clipflow_layout(
            input_path=input_video_path,
            output_path=output_path,
            webcam_pos=None,
            ass_path=ass_path,
            hook_data=hook_data,
            clip_duration=target_duration,
            groq_api_key=api_key,
            clip_text=script,
            audio_mode="ia",
            tts_audio_path=tts_audio_path,
            final_duration=final_duration,
            speed_ratio=speed_ratio
        )

        if use_temp_segment and temp_segment_path.exists():
            try:
                temp_segment_path.unlink()
            except:
                pass

        if not success:
            print(f"   ❌ Erro no FFmpeg layout ClipFlow")
            success = _try_simple_cut_clipflow(video_path if uses_fit_family() else (input_video_path if use_temp_segment else video_path), clip_obj, ass_path, output_path, speed_ratio, final_duration, hook_data, api_key, tts_audio_path)
            if not success:
                continue

        print(f"   ✅ Corte visual salvo: {output_path}")
        generated_clips.append(str(output_path))
        registrar_corte_no_historico(clip_obj['start'], clip_obj['end'], "visual", output_path, hook_data.get("titulo_gancho", ""))

    return generated_clips


def _try_simple_cut_clipflow(video_path, clip, ass_path, output_path, speed_ratio, final_duration, hook_data, api_key, tts_audio_path=None):
    """Tenta corte simplificado usando layout ClipFlow 3 camadas (1080x1920)."""
    if uses_fit_family():
        # video_path precisa ser o vídeo COMPLETO: o trecho é cortado aqui com -ss/-t
        return fit_fallback(video_path, clip['start'], clip['duration'], output_path, ass_path,
                            audio_mode="ia", tts_audio_path=tts_audio_path, tts_duration=final_duration)
    print(f"   🔄 Tentando versão simplificada (ClipFlow 3 camadas)...")

    ffmpeg_cmd = FFMPEG.as_posix()
    watermark_path = Path(GAMEPLAY_WATERMARK_PATH)
    has_watermark = watermark_path.exists()

    effective_duration = final_duration if final_duration else clip['duration']

    filter_complex = (
        f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
        f"scale={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}[top];"
        f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
        f"scale={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}[base]"
    )

    if ass_path and ass_path.exists():
        ass_path_escaped = ass_path.as_posix().replace(":", "\\:")
        filter_complex += f";[base]subtitles='{ass_path_escaped}'[base_sub]"
        base_label = "[base_sub]"
    else:
        base_label = "[base]"

    filter_complex += f";color=c=#D91E18:s={VERTICAL_WIDTH}x{CLIPFLOW_HOOK_BAR_HEIGHT}[hook_bar]"
    filter_complex += f";[top][hook_bar]{base_label}vstack=inputs=3[vout]"

    if has_watermark:
        wm_path_escaped = watermark_path.as_posix().replace(":", "\\:")
        filter_complex += f";movie='{wm_path_escaped}'[wm];[vout][wm]overlay=20:20[wmarked]"
        map_v = "[wmarked]"
    else:
        map_v = "[vout]"

    fade_start = max(0, effective_duration - 1.5)
    filter_complex += f";{map_v}fade=t=out:st={fade_start:.2f}:d=1.5[vout_fade]"
    map_v = "[vout_fade]"

    if speed_ratio > 0:
        filter_complex = filter_complex.replace(
            "[0:v]setpts=PTS-STARTPTS,crop=",
            f"[0:v]setpts=PTS-STARTPTS,setpts={1/speed_ratio:.6f}*PTS,crop="
        )

    af = f"asetpts=PTS-STARTPTS,loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,afade=t=out:st={fade_start:.2f}:d=1.5"

    cmd = [
        ffmpeg_cmd, "-y",
        "-hide_banner", "-loglevel", "error",
        "-fflags", "+genpts",
        "-i", str(video_path),
    ]

    if tts_audio_path and tts_audio_path.exists():
        cmd.extend(["-i", str(tts_audio_path)])

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", map_v,
        "-map", "1:a" if tts_audio_path and tts_audio_path.exists() else "0:a?",
        "-af", af,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        str(output_path)
    ])

    ok, _, err = run_cmd(cmd, timeout=600)
    if ok:
        print(f"   ✅ Corte salvo (fallback ClipFlow 3 camadas): {output_path}")
        return True
    else:
        print(f"   ❌ Falha no fallback ClipFlow: {err[:500]}")
        return False


def load_subtitles_with_fallback(subtitle_path, video_path, temp_dir, api_key=None):
    """Carrega legendas com cascata de fallback em 3 passos."""
    print("\n📝 Carregando legendas (sistema de fallback em 3 passos)...")

    print("   Passo A: Tentando legendas automáticas do YouTube...")
    segments = load_subtitles(subtitle_path) if subtitle_path else []

    if segments:
        total_words = sum(len(s['text'].split()) for s in segments)
        print(f"   ✅ Passo A OK: {len(segments)} segmentos, {total_words} palavras")
        return segments, "youtube", None

    for ext in ['.vtt', '.srt', '.json3']:
        sub_files = sorted(temp_dir.glob(f"*subs*{ext}"), key=_subtitle_lang_rank)
        if sub_files:
            segments = load_subtitles(sub_files[0])
            if segments:
                total_words = sum(len(s['text'].split()) for s in segments)
                print(f"   ✅ Passo A OK (fallback local): {len(segments)} segmentos, {total_words} palavras")
                return segments, "youtube", None

    print("   ⚠️  Passo A falhou: Nenhuma legenda do YouTube encontrada")

    if not api_key:
        print("   ❌ Sem legendas do YouTube e sem GROQ_API_KEY: não dá para transcrever o áudio (Passo B)")
        print("      nem analisar os frames (Passo C). Configure a chave no .env (veja .env.example)")
        print("      ou use um vídeo com legendas automáticas.")
        return [], "failed", None

    if api_key:
        print("   Passo B: Tentando transcrição de áudio via Groq Whisper API...")
        audio_files = extract_audio_for_transcription(video_path, temp_dir)

        all_segments = []
        for audio_file, offset in audio_files:
            segments = transcribe_audio_groq(audio_file, api_key)
            for seg in segments:
                seg['start'] += offset
                seg['end'] += offset
            all_segments.extend(segments)
        all_segments.sort(key=lambda s: s['start'])

        if all_segments:
            total_words = sum(len(s['text'].split()) for s in all_segments)
            print(f"   ✅ Passo B OK: {len(all_segments)} segmentos, {total_words} palavras")

            if total_words < MIN_WORDS_FOR_AUDIO_TRANSCRIPTION:
                print(f"   ⚠️  Poucas palavras ({total_words} < {MIN_WORDS_FOR_AUDIO_TRANSCRIPTION}). Possível gameplay sem narração.")
            else:
                return all_segments, "groq_audio", None
        else:
            print("   ⚠️  Passo B falhou: Transcrição não retornou segmentos")

    print("   Passo C: Modo Visual Gameplay - extraindo frames para análise...")
    frame_paths = extract_key_frames(video_path, temp_dir)
    if not frame_paths:
        print("   ❌ Falha ao extrair frames")
        return [], "failed", None

    climax_moments = analyze_frames_with_ai(frame_paths, api_key)
    if not climax_moments:
        print("   ❌ Passo C não encontrou clímax confiáveis. Nenhum corte será gerado.")
        return [], "failed", None

    print(f"   ✅ Passo C OK: {len(climax_moments)} clímax identificados")
    for cm in climax_moments:
        mins = int(cm['timestamp'] // 60)
        secs = int(cm['timestamp'] % 60)
        print(f"      - {mins:02d}:{secs:02d} ({cm['reason']})")
    print(f"      Modo: Visual Gameplay (fluxo dedicado: IA → TTS → FFmpeg)")

    extra_data = {
        'frame_paths': frame_paths,
        'climax_moments': climax_moments
    }
    return [], "visual_gameplay", extra_data


def run_cmd(cmd, cwd=None, timeout: int | None = None):
    """Executa comando (lista de argumentos, sem shell) e retorna (sucesso, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                cwd=cwd, encoding='utf-8', errors='replace', timeout=timeout)
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout.decode('utf-8', errors='replace') if e.stdout else ""
        stderr = e.stderr.decode('utf-8', errors='replace') if e.stderr else str(e)
        return False, stdout.strip(), stderr.strip()
    except Exception as e:
        return False, "", str(e)


def resolve_ffmpeg():
    """Define qual ffmpeg usar: bin/ffmpeg.exe se existir, senão o do PATH."""
    global FFMPEG
    if FFMPEG.exists():
        return True
    found = shutil.which("ffmpeg")
    if found:
        FFMPEG = Path(found)
        return True
    return False


def find_js_runtime():
    """Procura um runtime JavaScript que o yt-dlp usa para resolver os desafios do YouTube."""
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return name
    return None


def ytdlp_js_args():
    """Argumentos de runtime JavaScript para o yt-dlp."""
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return [] if name == "deno" else ["--js-runtimes", name]
    return []


def ensure_dirs():
    """Cria diretórios necessários."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)


SUB_LANG_PRIORITY = ["pt-BR", "pt", "pt-PT", "en"]


def _subtitle_lang_rank(path):
    """Prioridade do idioma pelo nome do arquivo."""
    lang = Path(path.stem).suffix.lstrip('.')
    if lang in SUB_LANG_PRIORITY:
        return SUB_LANG_PRIORITY.index(lang)
    return len(SUB_LANG_PRIORITY)


def pick_subtitle_file(directory, name_glob):
    """Escolhe o arquivo de legenda."""
    for ext in (".json3", ".vtt", ".srt"):
        files = sorted(directory.glob(f"{name_glob}{ext}"), key=_subtitle_lang_rank)
        if files:
            return files[0]
    return None


# ========== HISTÓRICO DE CORTES (não repetir trechos entre execuções) ==========
HISTORICO_NOME = "_historico_cortes.json"   # fica em cortes_prontos/ (a interface ignora arquivos com "_")
HISTORICO_MARGEM_S = 15                     # folga (s) em volta de cada corte já feito
HISTORICO_MAX_SOBREPOSICAO = 0.2            # candidato com mais que isso do tamanho sobreposto = repetido
HISTORICO = None                            # preenchido no main()


def video_key_from_url(url):
    """Identificador estável do vídeo: o mesmo para links com &t=..., &pp=..., youtu.be e shorts."""
    url = (url or "").strip()
    match = re.search(r'(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})', url)
    if match:
        return "yt_" + match.group(1)
    return "url_" + hashlib.md5(url.lower().encode("utf-8")).hexdigest()[:12]


class HistoricoCortes:
    """Lembra quais trechos de cada vídeo já viraram corte, para as próximas execuções pularem.

    Cortes descartados na interface também contam como usados: foram vistos e recusados.
    Com ativo=False (--repetir) nada é filtrado, mas os novos cortes continuam sendo registrados.
    """

    def __init__(self, caminho, video_key, video_url="", ativo=True):
        self.caminho = Path(caminho)
        self.video_key = video_key
        self.video_url = video_url
        self.ativo = ativo
        self.dados = self._ler()

    def _ler(self):
        try:
            dados = json.loads(self.caminho.read_text(encoding="utf-8"))
            if isinstance(dados, dict) and isinstance(dados.get("videos"), dict):
                return dados
        except (OSError, ValueError):
            pass
        return {"versao": 1, "videos": {}}

    def _gravar(self):
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.caminho.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.dados, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.caminho)

    def _cortes(self):
        video = self.dados["videos"].get(self.video_key) or {}
        return [c for c in video.get("cortes", []) if isinstance(c, dict)]

    def usados(self):
        """Intervalos (início, fim) em segundos já cortados deste vídeo."""
        faixas = []
        for c in self._cortes():
            try:
                faixas.append((float(c["inicio"]), float(c["fim"])))
            except (KeyError, TypeError, ValueError):
                continue
        return faixas

    def repetido(self, inicio, fim):
        """True se o intervalo cobre (quase) o mesmo trecho de um corte já feito."""
        if not self.ativo:
            return False
        duracao = max(fim - inicio, 0.001)
        for usado_ini, usado_fim in self.usados():
            sobreposto = min(fim, usado_fim + HISTORICO_MARGEM_S) - max(inicio, usado_ini - HISTORICO_MARGEM_S)
            if sobreposto > 0 and sobreposto / duracao > HISTORICO_MAX_SOBREPOSICAO:
                return True
        return False

    def filtrar(self, candidatos, chave_inicio="start", chave_fim="end"):
        """Devolve (candidatos_novos, quantos_foram_ignorados)."""
        if not self.ativo:
            return list(candidatos), 0
        novos = [c for c in candidatos if not self.repetido(c[chave_inicio], c[chave_fim])]
        return novos, len(candidatos) - len(novos)

    def registrar(self, inicio, fim, modo, arquivo="", titulo=""):
        video = self.dados["videos"].setdefault(self.video_key, {"url": self.video_url, "cortes": []})
        if self.video_url:
            video["url"] = self.video_url
        video.setdefault("cortes", []).append({
            "inicio": round(float(inicio), 2), "fim": round(float(fim), 2), "modo": modo,
            "arquivo": arquivo, "titulo": titulo, "quando": datetime.now().isoformat(timespec="seconds"),
        })
        self._gravar()

    def esquecer_video(self):
        self.dados["videos"].pop(self.video_key, None)
        self._gravar()

    def importar_existentes(self, pastas):
        """Marca como usados os cortes que já estão nas pastas (lê o .json ao lado de cada .mp4).

        Só use quando TODOS vieram deste vídeo: os cortes feitos antes do histórico existir não
        guardam de qual vídeo saíram. Retorna quantos foram marcados.
        """
        ja = {(round(a, 2), round(b, 2)) for a, b in self.usados()}
        novos = 0
        for pasta in pastas:
            pasta = Path(pasta)
            if not pasta.is_dir():
                continue
            for arq in sorted(pasta.glob("*.json")):
                if arq.name.startswith("_"):
                    continue
                try:
                    meta = json.loads(arq.read_text(encoding="utf-8"))
                    ini, fim = float(meta["clip_start"]), float(meta["clip_end"])
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                if meta.get("video_id") not in (None, self.video_key) or (round(ini, 2), round(fim, 2)) in ja:
                    continue
                self.registrar(ini, fim, str(meta.get("modo") or "importado"), arq.with_suffix(".mp4").name,
                               str(meta.get("titulo_gancho") or ""))
                ja.add((round(ini, 2), round(fim, 2)))
                novos += 1
        return novos


def filtrar_repetidos(candidatos, chave_inicio="start", chave_fim="end"):
    """Tira dos candidatos os trechos já cortados antes (sem histórico ativo, não tira nada)."""
    if HISTORICO is None:
        return list(candidatos), 0
    return HISTORICO.filtrar(candidatos, chave_inicio, chave_fim)


def registrar_corte_no_historico(inicio, fim, modo, arquivo, titulo=""):
    """Anota um corte gerado com sucesso. Nunca derruba a geração."""
    if HISTORICO is None:
        return
    try:
        HISTORICO.registrar(inicio, fim, modo, Path(arquivo).name, titulo)
    except Exception as e:
        print(f"   ⚠️  Não foi possível atualizar o histórico de cortes: {e}")


def dica_historico_esgotado():
    """Mensagem para quando o histórico deixou poucos ou nenhum trecho novo."""
    if HISTORICO is not None and HISTORICO.ativo and HISTORICO.usados():
        print(f"   📚 Este vídeo já tem {len(HISTORICO.usados())} trecho(s) cortado(s) antes, ignorados nesta execução.")
        print("      Para permitir repetir trechos, use --repetir (na interface: \"Permitir repetir trechos\").")


def clean_filename(name):
    """Limpa nome de arquivo para uso no sistema."""
    return re.sub(r'[\\/*?:"<>|]', "_", name)[:100]


def download_video(url, output_prefix):
    """Baixa vídeo e áudio usando yt-dlp."""
    clean_url = clean_youtube_url(url)
    if clean_url != url:
        print(f"\n🔗 URL original: {url}")
        print(f"🔗 URL limpa: {clean_url}")
    url = clean_url

    is_yt = is_youtube_url(url)
    print(f"\n📥 Baixando vídeo: {url} {'(YouTube)' if is_yt else '(outra plataforma)'}")

    ffmpeg_loc = ["--ffmpeg-location", str(FFMPEG.parent)]
    video_path = TEMP_DIR / f"{output_prefix}_video.mp4"

    if is_yt:
        video_format = "bv*[height<=1080]+ba/b[height<=1080]"
        extractor_args = ["--extractor-args", "youtube:player_client=android,web"]
        js_args = ytdlp_js_args()
    else:
        video_format = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        extractor_args = []
        js_args = []

    cmd = [
        sys.executable, "-m", "yt_dlp",
        *ffmpeg_loc,
        "--no-playlist",
        "--merge-output-format", "mp4",
        "-f", video_format,
        *js_args,
        "--rm-cache-dir",
        *extractor_args,
        "-o", str(video_path),
        url
    ]
    ok, _, err = run_cmd(cmd)
    if not ok or not video_path.exists():
        if is_yt:
            print(f"⚠️  Falha no download principal, tentando fallback com player_client=web...")
            cmd_fallback = [
                sys.executable, "-m", "yt_dlp",
                *ffmpeg_loc,
                "--no-playlist",
                "--merge-output-format", "mp4",
                "-f", video_format,
                *js_args,
                "--rm-cache-dir",
                "--extractor-args", "youtube:player_client=web",
                "-o", str(video_path),
                url
            ]
            ok, _, err = run_cmd(cmd_fallback)
        if not ok or not video_path.exists():
            print(f"❌ Falha ao baixar vídeo: {err}")
            return None, None

    audio_path = TEMP_DIR / f"{output_prefix}_audio.m4a"
    audio_format = "bestaudio[ext=m4a]/bestaudio"
    if is_yt:
        audio_extractor_args = ["--extractor-args", "youtube:player_client=android,web"]
        audio_js_args = ytdlp_js_args()
    else:
        audio_extractor_args = []
        audio_js_args = []

    cmd = [
        sys.executable, "-m", "yt_dlp",
        *ffmpeg_loc,
        "--no-playlist",
        "-f", audio_format,
        *audio_js_args,
        "--rm-cache-dir",
        *audio_extractor_args,
        "-o", str(audio_path),
        url
    ]
    ok, _, err = run_cmd(cmd)
    if not ok:
        print(f"⚠️  Falha ao baixar áudio separado: {err}")

    sub_langs = "pt,pt-BR,pt-PT,en"
    if is_yt:
        sub_extractor_args = ["--extractor-args", "youtube:player_client=android,web"]
        sub_js_args = ytdlp_js_args()
    else:
        sub_extractor_args = []
        sub_js_args = []

    cmd = [
        sys.executable, "-m", "yt_dlp",
        *ffmpeg_loc,
        "--no-playlist",
        "--write-auto-sub",
        "--sub-lang", sub_langs,
        "--sub-format", "json3",
        "--skip-download",
        *sub_js_args,
        "--rm-cache-dir",
        *sub_extractor_args,
        "-o", str(TEMP_DIR / f"{output_prefix}_subs.%(ext)s"),
        url
    ]
    ok, _, err = run_cmd(cmd)

    subtitle_path = pick_subtitle_file(TEMP_DIR, f"{output_prefix}_subs*")

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
            idx = next((i for i, ln in enumerate(lines) if '-->' in ln), None)
            if idx is not None:
                time_line = lines[idx]
                text = re.sub(r'<[^>]+>', '', ' '.join(lines[idx + 1:])).strip()
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
    """Remove repetições das legendas automáticas do YouTube."""
    result = []
    prev_raw = []
    for seg in sorted(segments, key=lambda s: (s['start'], s['end'])):
        words = seg['text'].split()
        if not words:
            continue
        raw = list(words)
        if result:
            prev = result[-1]
            prev_words = prev_raw
            for k in range(min(len(prev_words), len(words)), 0, -1):
                if prev_words[-k:] == words[:k] and (k >= 2 or k == len(words)):
                    words = words[k:]
                    break
            if not words:
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

        if len(current_text) >= max_chars:
            merged.append({
                'start': current_start,
                'end': current_end,
                'text': current_text.strip()
            })
            current_text = ""
            current_start = None

    if current_text:
        merged.append({
            'start': current_start,
            'end': segments[-1]['end'],
            'text': current_text.strip()
        })

    return merged


def score_segment(text, start, _end, duration, total_duration):
    """Pontua um segmento para potencial viral."""
    score = 0
    text_lower = text.lower()

    if TARGET_DURATION_MIN <= duration <= TARGET_DURATION_MAX:
        score += 30
    elif 25 <= duration <= 60:
        score += 20
    elif 20 <= duration <= 65:
        score += 10

    hook_words = text_lower.split()[:5]
    hook_text = ' '.join(hook_words)

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

    end_words = text_lower.split()[-8:]
    end_text = ' '.join(end_words)
    conclusion_patterns = [
        r'\b(por\s+isso|então|logo|conclusão|resumo|resumindo|finalment)\b',
        r'\b(agora\s+você\s+sabe|fica\s+a\s+dica|anote\s+aí|compartilhe)\b',
        r'\b(inscreva|like|comente|siga|baixe|link\s+na\s+bio)\b',
        r'[.]{3}$',
        r'(?!.*\b(mas|porém|entretanto)\b).*[.!?]$',
    ]
    for pattern in conclusion_patterns:
        if re.search(pattern, end_text):
            score += 15
            break

    value_keywords = ['passo', 'passos', 'etapa', 'etapas', 'como', 'porque', 'porquê',
                      'resultado', 'resultados', 'antes', 'depois', 'transformou',
                      'mudei', 'aprendi', 'descobri', 'testei', 'funciona', 'prova',
                      'provas', 'números', 'dados', 'estudo', 'pesquisa', 'ciência',
                      'técnica', 'método', 'estratégia', 'segredo', 'truque']
    value_count = sum(1 for kw in value_keywords if kw in text_lower)
    score += min(value_count * 3, 20)

    position_ratio = start / total_duration if total_duration > 0 else 0
    if position_ratio < 0.15:
        score += 10
    elif 0.15 <= position_ratio < 0.4:
        score += 5
    elif position_ratio >= 0.8:
        score += 10

    visual_keywords = ['olha', 'veja', 'mostro', 'mostra', 'aqui', 'tela', 'gráfico',
                       'imagem', 'foto', 'vídeo', 'demo', 'demonstração']
    visual_count = sum(1 for kw in visual_keywords if kw in text_lower)
    score += min(visual_count * 2, 10)

    direct_patterns = [r'\bvocê\b', r'\bseu\b', r'\bsua\b', r'\bte\b', r'\btu\b']
    direct_count = sum(1 for p in direct_patterns if re.search(p, text_lower))
    score += min(direct_count * 2, 10)

    return score


def find_best_clips(segments, video_duration, num_clips=NUM_CLIPS):
    """Identifica os melhores trechos para cortes virais."""
    if not segments:
        return []

    merged = merge_subtitle_segments(segments)
    total_duration = video_duration

    candidates = []
    window_sizes = list(range(TARGET_DURATION_MIN, TARGET_DURATION_MAX + 1, 5))

    for window in window_sizes:
        step = window // 3
        start = 0
        while start + window <= total_duration:
            end = start + window
            window_text = ""
            for seg in merged:
                if seg['end'] <= start:
                    continue
                if seg['start'] >= end:
                    break
                overlap_start = max(seg['start'], start)
                overlap_end = min(seg['end'], end)
                if overlap_end > overlap_start:
                    prop = (overlap_end - overlap_start) / (seg['end'] - seg['start'])
                    words = seg['text'].split()
                    n_words = max(1, int(len(words) * prop))
                    window_text += " " + ' '.join(words[:n_words])

            if len(window_text.strip()) > 100:
                score = score_segment(window_text.strip(), start, end, window, total_duration)
                candidates.append({
                    'start': start,
                    'end': end,
                    'duration': window,
                    'text': window_text.strip(),
                    'score': score
                })
            start += step

    candidates.sort(key=lambda x: x['score'], reverse=True)

    candidates, ignorados = filtrar_repetidos(candidates)
    if ignorados:
        print(f"   🚫 {ignorados} janela(s) ignorada(s) por repetirem cortes já feitos deste vídeo")

    selected = []
    for c in candidates:
        overlap = False
        for s in selected:
            if not (c['end'] <= s['start'] or c['start'] >= s['end']):
                overlap = True
                break
        if not overlap:
            selected.append(c)
            if len(selected) >= num_clips:
                break

    if len(selected) < num_clips:
        for c in candidates:
            if c not in selected:
                selected.append(c)
                if len(selected) >= num_clips:
                    break

    return selected[:num_clips]


def segments_in_range(segments, start, end):
    """Segmentos da transcrição que pertencem ao intervalo (pelo menos 50% dentro dele)."""
    found = []
    for seg in segments:
        seg_start, seg_end = seg['start'], seg['end']
        duration = seg_end - seg_start
        if duration <= 0 or seg_end <= start or seg_start >= end:
            continue
        overlap = min(seg_end, end) - max(seg_start, start)
        if overlap / duration >= 0.5:
            found.append(seg)
    return found


def real_text_for_range(segments, start, end):
    """Texto REAL (da transcrição) de um intervalo: retorna (texto, palavras, palavras_por_segundo)."""
    text = ' '.join(seg['text'].strip() for seg in segments_in_range(segments, start, end)).strip()
    words = len(text.split())
    return text, words, words / max(end - start, 0.001)


def has_enough_speech(words, words_per_second):
    """True se o trecho tem fala suficiente para legenda, título e texto fazerem sentido."""
    return words >= GAMEPLAY_MIN_CLIP_WORDS and words_per_second >= GAMEPLAY_MIN_WORDS_PER_SECOND


_GREETING_RE = re.compile(
    r"^\s*(salve|e a[ií],? galera|fala,? (galera|rapaziada|pessoal)|vamos l[aá]|ent[aã]o,? pessoal|boa (noite|tarde|dia)|bem[- ]vindos?)",
    re.I)
_LAUGH_RE = re.compile(r"\[(risos?|risadas?|gargalhadas?)\]|\b(k{3,}|(ha){2,}|(rs){2,})\b", re.I)
_INTENSE_WORDS = ("meu deus", "não acredito", "nao acredito", "caramba", "socorro", "que isso", "loucura",
                  "impossível", "impossivel", "mentira", "inacreditável", "inacreditavel", "olha isso", "nossa")


def local_moment_score(text, words_per_second):
    """Nota local (0 a ~9) de um trecho real: fala, risadas, exclamações, palavras de reação.

    Serve para escolher QUAIS candidatos a IA vai ver. Não tem pretensão de ser o julgamento final.
    """
    score = min(words_per_second, 2.5) / 2.5 * 3.0
    score += min(len(_LAUGH_RE.findall(text)), 3) * 0.8
    score += min(text.count('!'), 4) * 0.4
    score += min(text.count('?'), 3) * 0.2
    lower = text.lower()
    score += min(sum(1 for w in _INTENSE_WORDS if w in lower), 3) * 0.6
    if _GREETING_RE.search(text):
        score -= 2.0
    return round(max(score, 0.0), 2)


def build_gameplay_candidates(segments, video_duration):
    """Monta candidatos a corte a partir da transcrição REAL, em todo o vídeo.

    Início e fim ficam em limites de fala (segmentos); o texto é o que foi realmente dito no
    intervalo. Trechos sem fala suficiente não viram candidatos.
    """
    segs = sorted((s for s in segments if s.get('text', '').strip() and s['end'] > s['start']),
                  key=lambda s: s['start'])
    if not segs:
        return []
    target = (GAMEPLAY_CLIP_DURATION_MIN + GAMEPLAY_CLIP_DURATION_MAX) / 2
    candidates = []
    last_start = -1e9
    for i, first in enumerate(segs):
        if first['start'] - last_start < GAMEPLAY_CANDIDATE_STEP:
            continue
        start = max(0.0, first['start'] - GAMEPLAY_PAD_BEFORE)
        if start + GAMEPLAY_CLIP_DURATION_MIN > video_duration:
            break
        best_end, best_gap = None, None
        k = i
        while k < len(segs) and segs[k]['end'] - start <= GAMEPLAY_CLIP_DURATION_MAX:
            duration = segs[k]['end'] - start
            if duration >= GAMEPLAY_CLIP_DURATION_MIN:
                ends_sentence = bool(re.search(r'[.!?…]["”\']?$', segs[k]['text'].strip()))
                gap = abs(duration - target) - (3 if ends_sentence else 0)
                if best_gap is None or gap < best_gap:
                    best_gap, best_end = gap, segs[k]['end']
            k += 1
        if best_end is None:
            end = min(start + target, video_duration)
        else:
            end = min(best_end + GAMEPLAY_PAD_AFTER, start + GAMEPLAY_CLIP_DURATION_MAX, video_duration)
        if end - start < GAMEPLAY_CLIP_DURATION_MIN:
            continue
        text, words, wps = real_text_for_range(segs[i:k + 1], start, end)
        if not has_enough_speech(words, wps):
            continue
        candidates.append({
            'start': start, 'end': end, 'duration': end - start,
            'text': text, 'words': words, 'wps': round(wps, 2),
            'local_score': local_moment_score(text, wps),
        })
        last_start = first['start']
    return candidates


def select_band_candidates(candidates, band_start, band_end, limit):
    """Os melhores candidatos (nota local) de uma faixa, sem sobreposição entre si."""
    pool = sorted((c for c in candidates if band_start <= c['start'] < band_end),
                  key=lambda c: c['local_score'], reverse=True)
    chosen = []
    for cand in pool:
        if all(abs(cand['start'] - other['start']) >= GAMEPLAY_CLIP_DURATION_MAX for other in chosen):
            chosen.append(cand)
        if len(chosen) >= limit:
            break
    return sorted(chosen, key=lambda c: c['start'])


def _clean_hook_title(value):
    value = re.sub(r'["“”\'*#]+', '', str(value or '')).strip()
    value = re.sub(r'[.!?…,;:]+$', '', value)
    return ' '.join(value.split()[:5]).upper()


def _clean_hook_question(value):
    value = re.sub(r'["“”\'*#]+', '', str(value or '')).strip()
    words = value.split()[:4]
    return (' '.join(words).rstrip('?!.… ') + '?') if words else ''


def analyze_band_with_ai(candidates, api_key):
    """A IA ESCOLHE entre candidatos reais numerados; não calcula tempo nem escreve o texto.

    Retorna a lista dos candidatos escolhidos (com score, motivo e, se houver, o gancho), uma lista
    vazia se a IA não achou nada bom, ou None se a chamada falhou (quem chama usa a nota local).
    """
    if not candidates:
        return []
    limit = GAMEPLAY_CLIPS_PER_BAND
    listing = "\n".join(
        f"[{n}] {c['text'][:GAMEPLAY_CANDIDATE_TEXT_CHARS]}" for n, c in enumerate(candidates, 1))
    prompt = (
        "Você é um editor especialista em Shorts/TikTok/Reels de games. Abaixo estão TRECHOS REAIS de uma "
        "live, já recortados e numerados. O texto de cada trecho é a transcrição EXATA do que o streamer "
        f"disse nele. Escolha os {limit} melhores para cortes virais.\n\n"
        "ACEITE: reação explosiva, pergunta intrigante, história engraçada ou chocante, clutch/vitória, "
        "fail épico, descoberta, frase de impacto, risada.\n"
        "REJEITE: saudações, enrolação, explicação de contexto, trechos sem graça e textos sem sentido "
        "(transcrição ruim).\n\n"
        f"TRECHOS:\n{listing}\n\n"
        "Responda APENAS com JSON válido, uma lista de até "
        f"{limit} objetos:\n"
        '[{"id": 3, "score": 8.5, "reason": "motivo curto (máx 50 caracteres)", '
        '"hook_title": "TÍTULO EM CAIXA ALTA, MÁX 5 PALAVRAS", "pergunta_cta": "PERGUNTA CURTA?"}]\n\n'
        "Regras:\n"
        "- id: o número do trecho; use SÓ números da lista.\n"
        "- score: de 0 a 10.\n"
        "- hook_title e pergunta_cta: baseados SOMENTE no texto do trecho escolhido. NÃO invente fatos, "
        "nomes nem acontecimentos. Se o texto não fizer sentido, use \"\" nos dois.\n"
        "- Se nenhum trecho for bom, responda []."
    )
    answer = groq_chat_with_fallback([{"role": "user", "content": prompt}], api_key,
                                     get_text_models_to_try, max_tokens=1500, temperature=0.3)
    if not answer:
        return None
    data = extract_json(answer)
    if not isinstance(data, list):
        return None

    picked, used = [], set()
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get('id'))
            score = float(item.get('score', 0))
        except (TypeError, ValueError):
            continue
        if not (1 <= idx <= len(candidates)) or idx in used or score < 3.0:
            continue
        used.add(idx)
        cand = dict(candidates[idx - 1])
        title = _clean_hook_title(item.get('hook_title'))
        question = _clean_hook_question(item.get('pergunta_cta'))
        cand.update({
            'score': min(score, 10.0),
            'reason': str(item.get('reason', '')).strip()[:50],
            'fonte': 'ia',
        })
        if title and question:
            cand['hook_title'] = title
            cand['hook_data'] = {'titulo_gancho': title, 'pergunta_cta': question,
                                 'nota': int(min(score, 10.0) * 10)}
        picked.append(cand)
        if len(picked) >= limit:
            break
    return picked


def find_best_clips_gameplay_ai(segments, video_duration, api_key, num_clips=GAMEPLAY_DEFAULT_CLIPS):
    """Seleciona os melhores trechos do modo Gameplay: candidatos reais + escolha da IA."""
    if not segments or not api_key:
        print("   ⚠️  Sem segmentos ou API key, usando método tradicional...")
        return find_best_clips(segments, video_duration, num_clips)

    print(f"\n🤖 Modo Gameplay IA: montando candidatos a partir da transcrição real...")
    all_pool = build_gameplay_candidates(segments, video_duration)
    print(f"   🧩 {len(all_pool)} trechos candidatos com fala suficiente em todo o vídeo")
    all_pool, ignorados = filtrar_repetidos(all_pool)
    if ignorados:
        print(f"   🚫 {ignorados} candidato(s) ignorado(s) por repetirem cortes já feitos deste vídeo; restam {len(all_pool)}")
    if not all_pool:
        if ignorados:
            print("   ⚠️  Todos os trechos com fala deste vídeo já foram cortados antes.")
            dica_historico_esgotado()
            return []
        print("   ⚠️  Nenhum trecho com fala suficiente, usando método tradicional...")
        return find_best_clips(segments, video_duration, num_clips)

    band_duration = video_duration / GAMEPLAY_TIME_BANDS
    all_candidates = []

    for band_idx in range(GAMEPLAY_TIME_BANDS):
        band_start = band_idx * band_duration
        band_end = min((band_idx + 1) * band_duration, video_duration)
        print(f"   📍 Faixa {band_idx + 1}/{GAMEPLAY_TIME_BANDS}: {band_start/60:.1f}min - {band_end/60:.1f}min")

        pool = select_band_candidates(all_pool, band_start, band_end, GAMEPLAY_CANDIDATES_PER_BAND)
        if not pool:
            print("      ⚠️  Sem trechos com fala suficiente nesta faixa")
            continue

        picks = analyze_band_with_ai(pool, api_key)
        if picks is None:
            print("      ⚠️  IA indisponível nesta faixa: usando a nota local (fala, risadas, exclamações)")
            picks = []
            for cand in sorted(pool, key=lambda c: c['local_score'], reverse=True)[:GAMEPLAY_CLIPS_PER_BAND]:
                cand = dict(cand)
                cand.update({'score': min(cand['local_score'], 10.0), 'fonte': 'local',
                             'reason': 'trecho com mais fala e reação (sem IA)'})
                picks.append(cand)
        for cand in picks:
            cand['band'] = band_idx + 1
        all_candidates.extend(picks)

    if not all_candidates:
        print("   ⚠️  IA não retornou candidatos, usando método tradicional...")
        return find_best_clips(segments, video_duration, num_clips)

    all_candidates.sort(key=lambda x: x['score'], reverse=True)

    print(f"\n🔍 Aplicando filtro anti-repetição (mínimo {GAMEPLAY_MIN_GAP_SECONDS}s entre cortes)...")
    selected = []
    for c in all_candidates:
        too_close = False
        for s in selected:
            if abs(c['start'] - s['start']) < GAMEPLAY_MIN_GAP_SECONDS:
                too_close = True
                print(f"      ⏭️  Descartado: {c['start']/60:.1f}min (próximo de {s['start']/60:.1f}min)")
                break
        if not too_close:
            selected.append(c)
            print(f"      ✅ Selecionado: {c['start']/60:.1f}min - Score: {c['score']:.1f} - {c.get('reason', '')}")
            if len(selected) >= num_clips:
                break

    if len(selected) < num_clips:
        print(f"   ⚠️  Apenas {len(selected)} cortes com espaçamento de 5min. Relaxando filtro...")
        for gap in [180, 120, 60, 0]:
            selected = []
            for c in all_candidates:
                too_close = False
                for s in selected:
                    if abs(c['start'] - s['start']) < gap:
                        too_close = True
                        break
                if not too_close:
                    selected.append(c)
                    if len(selected) >= num_clips:
                        break
            if len(selected) >= num_clips:
                print(f"   ✅ Conseguiu {len(selected)} cortes com espaçamento de {gap}s")
                break

    for c in selected:
        if c['duration'] < GAMEPLAY_CLIP_DURATION_MIN:
            c['end'] = c['start'] + GAMEPLAY_CLIP_DURATION_MIN
            c['duration'] = GAMEPLAY_CLIP_DURATION_MIN
        elif c['duration'] > GAMEPLAY_CLIP_DURATION_MAX:
            c['end'] = c['start'] + GAMEPLAY_CLIP_DURATION_MAX
            c['duration'] = GAMEPLAY_CLIP_DURATION_MAX
        # o texto SEMPRE vem da transcrição do intervalo final (nunca do que a IA escreveu)
        c['text'], c['words'], c['wps'] = real_text_for_range(segments, c['start'], c['end'])
        c['wps'] = round(c['wps'], 2)

    selected = [c for c in selected if has_enough_speech(c['words'], c['wps'])]

    print(f"\n🎯 {len(selected)} cortes finais selecionados:")
    for i, c in enumerate(selected, 1):
        print(f"  {i}. {c['start']:.1f}s - {c['end']:.1f}s ({c['duration']:.1f}s) | Score: {c['score']:.1f}")
        print(f"     Motivo: {c.get('reason', 'N/A')}")
        print(f"     Gancho: {c.get('hook_title', 'N/A')}")
        print(f"     Fala real ({c['words']} palavras, {c['wps']}/s): {c['text'][:120]}...")

    return selected[:num_clips]


def clip_file_title(clip, max_chars=50):
    """Base do nome do arquivo: o título do gancho (se a IA fez um) ou o começo da fala REAL."""
    base = (clip.get('hook_data') or {}).get('titulo_gancho') or clip.get('text', '')
    base = clean_filename(base[:max_chars]).strip(' .')
    return base or "corte"


def generate_viral_script(original_text, target_duration):
    """Gera um roteiro viral resumido a partir do texto original."""
    text = original_text.strip()
    words = text.split()

    if len(words) < 20:
        result = text
        if not re.search(r'[.!?…]$', result):
            result += "."
        return result

    hook_end = max(1, len(words) // 5)
    conclusion_start = max(hook_end + 1, len(words) * 4 // 5)

    hook_part = ' '.join(words[:hook_end])
    middle_part = ' '.join(words[hook_end:conclusion_start])
    conclusion_part = ' '.join(words[conclusion_start:])

    value_keywords = ['passo', 'passos', 'etapa', 'etapas', 'como', 'porque', 'porquê',
                      'resultado', 'resultados', 'antes', 'depois', 'transformou',
                      'mudei', 'aprendi', 'descobri', 'testei', 'funciona', 'prova',
                      'provas', 'números', 'dados', 'estudo', 'pesquisa', 'ciência',
                      'técnica', 'método', 'estratégia', 'segredo', 'truque', 'dica']

    sentences = re.split(r'(?<=[.!?…])\s+', middle_part)
    key_sentences = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if not re.search(r'[.!?…]$', sentence):
            sentence += "."
        if any(kw in sentence.lower() for kw in value_keywords):
            key_sentences.append(sentence)

    hook = hook_part.strip()
    if not re.search(r'[.!?…]$', hook):
        hook = hook.rstrip('.') + "!"

    if key_sentences:
        development = ' '.join(key_sentences[:4])
    else:
        fallback_sentences = re.split(r'(?<=[.!?…])\s+', middle_part[:300])
        fallback_sentences = [s.strip() for s in fallback_sentences if s.strip()]
        if fallback_sentences:
            development = ' '.join(fallback_sentences[:3])
            if not re.search(r'[.!?…]$', development):
                development += "."
        else:
            development = ""

    conclusion = conclusion_part.strip()
    if not re.search(r'\b(inscreva|like|comente|siga|compartilhe|baixe|link)\b', conclusion.lower()):
        conclusion = conclusion.rstrip('.') + " — Fica a dica!"
    if not re.search(r'[.!?…]$', conclusion):
        conclusion += "."

    parts = [hook]
    if development:
        parts.append(development)
    parts.append(conclusion)
    script = ' '.join(parts)

    max_words = int(target_duration * 2.5)
    script_words = script.split()

    if len(script_words) > max_words:
        truncated = ' '.join(script_words[:max_words])
        last_punct = max(
            truncated.rfind('.'),
            truncated.rfind('!'),
            truncated.rfind('?'),
            truncated.rfind('…')
        )
        if last_punct > max_words * 0.5:
            script = truncated[:last_punct + 1]
        else:
            script = truncated.rstrip('.!?…') + "."

    if not re.search(r'[.!?…]$', script):
        script += "."

    return script


async def generate_tts_audio(text, output_path, voice=TTS_VOICE, rate=TTS_RATE, volume=TTS_VOLUME):
    """Gera áudio com edge-tts e retorna (sucesso, duração, eventos_word_boundary)."""
    try:
        import edge_tts
    except ImportError:
        print("❌ edge-tts não instalado. Rode: pip install edge-tts")
        return False, 0, []

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume,
                                           boundary="WordBoundary")

        word_boundary_events = []
        with open(output_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    word_boundary_events.append({
                        "text": chunk["text"],
                        "offset": chunk["offset"] / 10_000_000,
                        "duration": chunk["duration"] / 10_000_000,
                        "type": chunk["type"]
                    })

        if not output_path.exists() or output_path.stat().st_size == 0:
            return False, 0, []

        audio_duration = get_audio_duration(output_path)
        return True, audio_duration, word_boundary_events
    except Exception as e:
        print(f"❌ Erro ao gerar TTS: {e}")
        return False, 0, []


def create_ass_subtitle_from_tts_events(word_events, output_path):
    """Cria arquivo ASS usando eventos WordBoundary REAIS do edge-tts."""
    if not word_events:
        return False

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

    for i in range(len(chunks) - 1):
        if chunks[i]['end'] > chunks[i + 1]['start']:
            chunks[i]['end'] = chunks[i + 1]['start']

    MAX_CHUNK_DURATION = 3.0
    for chunk in chunks:
        if chunk['end'] - chunk['start'] > MAX_CHUNK_DURATION:
            chunk['end'] = chunk['start'] + MAX_CHUNK_DURATION

    chunks = [c for c in chunks if c['end'] - c['start'] >= 0.3]

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


def create_ass_subtitle(segments, clip_start, clip_end, output_path):
    """Cria arquivo ASS para modo original (legendas do YouTube)."""
    clip_segments = []
    for seg in segments:
        if seg['end'] <= clip_start:
            continue
        if seg['start'] >= clip_end:
            break
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

    for i in range(len(chunks) - 1):
        if chunks[i]['end'] > chunks[i + 1]['start']:
            chunks[i]['end'] = chunks[i + 1]['start']

    MAX_CHUNK_DURATION = 3.0
    for chunk in chunks:
        if chunk['end'] - chunk['start'] > MAX_CHUNK_DURATION:
            chunk['end'] = chunk['start'] + MAX_CHUNK_DURATION

    chunks = [c for c in chunks if c['end'] - c['start'] >= 0.3]

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


def extract_video_segment(video_path, start_time, duration, output_path):
    """Extrai um segmento curto do vídeo para evitar decodificação completa no FFmpeg."""
    print(f"   ✂️  Pré-cortando segmento: {start_time:.1f}s - {start_time+duration:.1f}s ({duration:.1f}s)")

    cmd = [
        FFMPEG.as_posix(), "-y",
        "-hide_banner", "-loglevel", "error",
        "-ss", str(start_time),
        "-t", str(duration),
        "-i", str(video_path),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(INTERMEDIATE_CRF),
        "-c:a", "aac", "-b:a", "192k",
        "-vf", "setpts=PTS-STARTPTS",
        "-af", "asetpts=PTS-STARTPTS",
        "-fps_mode", "cfr",
        "-avoid_negative_ts", "make_zero",
        "-fflags", "+genpts",
        str(output_path)
    ]

    ok, _, err = run_cmd(cmd, timeout=120)
    if not ok:
        print(f"   ❌ Falha no pré-corte: {err[:200]}")
        return False

    if not output_path.exists() or output_path.stat().st_size < 1024:
        print(f"   ❌ Arquivo de segmento inválido ou vazio")
        return False

    print(f"   ✅ Segmento extraído: {output_path} ({output_path.stat().st_size/1024/1024:.1f} MB)")
    return True


def save_clip_metadata(output_path, clip, hook_data, modo):
    """Grava o .json ao lado do corte (título, texto real, tempos). Nunca derruba a geração do corte."""
    try:
        meta = {
            "titulo_gancho": (hook_data or {}).get("titulo_gancho", ""),
            "pergunta_cta": (hook_data or {}).get("pergunta_cta", ""),
            "nota": (hook_data or {}).get("nota", 0),
            "transcricao": clip.get("text", ""),
            "transcricao_fonte": "transcrição real do intervalo do corte",
            "modo": modo,
            "video_id": HISTORICO.video_key if HISTORICO else None,
            "video_url": HISTORICO.video_url if HISTORICO else None,
            "score_selecao": clip.get("score"),
            "clip_start": clip.get("start"),
            "clip_end": clip.get("end"),
            "clip_duration": clip.get("duration"),
            "timestamp": datetime.now().isoformat(),
        }
        Path(output_path).with_suffix(".json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"   ⚠️  Não foi possível salvar o .json do corte: {e}")


def cut_and_export_vertical(video_path, clip, segments, output_path, clip_num, audio_mode="original", webcam_pos=None, groq_api_key=None):
    """Corta vídeo usando layout ClipFlow 3 camadas (1080x1920) para TODOS os modos."""
    print(f"\n🎬 Processando corte {clip_num}: {clip['start']:.1f}s - {clip['end']:.1f}s ({clip['duration']:.1f}s) [Modo: {audio_mode}]")

    clip_start = clip['start']
    clip_duration = clip['duration']
    temp_segment_path = TEMP_DIR / f"segment_clip_{clip_num}_{int(clip_start)}s_{int(clip_duration)}s.mp4"

    if not extract_video_segment(video_path, clip_start, clip_duration, temp_segment_path):
        print(f"   ⚠️  Falha no pré-corte, tentando usar vídeo original (pode causar timeout)...")
        input_video_path = video_path
        use_temp_segment = False
    else:
        input_video_path = temp_segment_path
        use_temp_segment = True

    ass_path = None
    tts_audio_path = None
    final_duration = clip['duration']
    tts_word_events = []
    viral_script = None
    speed_ratio = 1.0

    clip_text = clip.get('text', '')
    if groq_api_key:
        print("   🤖 Gerando gancho viral e CTA com IA...")
        hook_data = generate_hook_and_question(clip_text, clip['duration'], groq_api_key)
    else:
        print("   ⚠️  Sem GROQ_API_KEY, usando gancho padrão")
        hook_data = {
            "titulo_gancho": "MOMENTO INCRÍVEL",
            "pergunta_cta": "O QUE ACHOU?",
            "nota": 75
        }

    if audio_mode == "ia":
        print(f"🤖 Gerando roteiro viral e narração com edge-tts ({TTS_VOICE})...")

        api_key = get_groq_key()
        if api_key:
            print(f"🧠 Reescrevendo o roteiro com IA ({get_text_model()})...")
            viral_script = generate_viral_script_ai(clip['text'], clip['duration'], api_key)
            if not viral_script:
                print("⚠️  IA indisponível, usando o roteiro por regras...")
        if not viral_script:
            viral_script = generate_viral_script(clip['text'], clip['duration'])
        print(f"📝 Roteiro gerado ({len(viral_script.split())} palavras): {viral_script[:150]}...")

        tts_audio_path = TEMP_DIR / f"clip_{clip_num}_tts.mp3"
        success, tts_duration, word_events = asyncio.run(generate_tts_audio(viral_script, tts_audio_path))

        if not success or not tts_audio_path.exists():
            print("⚠️  Falha no TTS, usando áudio original...")
            audio_mode = "original"
        elif narration_too_short(tts_duration, clip['duration']):
            print(f"🔊 Áudio TTS gerado: {tts_duration:.1f}s")
            print(f"⚠️  Narração curta demais para um trecho de {clip['duration']:.0f}s "
                  f"(mínimo {max(MIN_NARRATION_SECONDS, clip['duration'] / MAX_VIDEO_SPEEDUP):.0f}s); "
                  f"usando o áudio original deste corte.")
            audio_mode = "original"
        else:
            print(f"🔊 Áudio TTS gerado: {tts_duration:.1f}s ({len(word_events)} eventos WordBoundary)")
            final_duration = tts_duration
            speed_ratio = clip['duration'] / final_duration if final_duration > 0 else 1.0
            tts_word_events = word_events

            ass_path = TEMP_DIR / f"clip_{clip_num}_subs.ass"
            if not create_ass_subtitle_from_tts_events(tts_word_events, ass_path):
                print("⚠️  Falha ao criar legendas sincronizadas com WordBoundary")
                ass_path = None

    if audio_mode == "original":
        ass_path = TEMP_DIR / f"clip_{clip_num}_subs.ass"
        if not create_karaoke_ass_subtitle(segments, clip['start'], clip['end'], clip['duration'], ass_path):
            print("⚠️  Falha ao criar legendas karaokê, exportando sem...")
            ass_path = None

    success = process_clipflow_layout(
        input_path=input_video_path,
        output_path=output_path,
        webcam_pos=webcam_pos,
        ass_path=ass_path,
        hook_data=hook_data,
        clip_duration=clip['duration'],
        groq_api_key=groq_api_key,
        clip_text=clip_text,
        audio_mode=audio_mode,
        tts_audio_path=tts_audio_path,
        final_duration=final_duration,
        speed_ratio=speed_ratio
    )

    if use_temp_segment and temp_segment_path.exists():
        try:
            temp_segment_path.unlink()
        except:
            pass

    if not success:
        print(f"   ❌ Falha no layout ClipFlow, tentando fallback simplificado...")
        ok = cut_simple_vertical(video_path, clip, segments, ass_path, output_path, clip_num, audio_mode, tts_audio_path, final_duration)
        if ok:
            save_clip_metadata(output_path, clip, hook_data, audio_mode)
        return ok

    save_clip_metadata(output_path, clip, hook_data, audio_mode)
    print(f"✅ Corte salvo: {output_path}")
    return True


def cut_simple_vertical(video_path, clip, segments, ass_path, output_path, clip_num, audio_mode="original", tts_audio_path=None, tts_duration=None):
    """Versão simplificada do fallback (fit: trecho certo do vídeo completo; clipflow: 3 camadas)."""
    if uses_fit_family():
        print("🔄 Tentando versão simplificada (layout fit)...")
        return fit_fallback(video_path, clip['start'], clip['duration'], output_path, ass_path,
                            audio_mode=audio_mode, tts_audio_path=tts_audio_path, tts_duration=tts_duration)
    print(f"🔄 Tentando versão simplificada (fallback ClipFlow 3 camadas vertical 1080x1920)...")

    ffmpeg_cmd = FFMPEG.as_posix()
    watermark_path = Path(GAMEPLAY_WATERMARK_PATH)
    has_watermark = watermark_path.exists()

    effective_duration = tts_duration if audio_mode == "ia" and tts_duration else clip['duration']

    cmd = [
        ffmpeg_cmd, "-y",
        "-hide_banner", "-loglevel", "error",
        "-fflags", "+genpts",
        "-i", str(video_path),
    ]
    if audio_mode == "ia" and tts_audio_path and tts_audio_path.exists():
        cmd.extend(["-i", str(tts_audio_path)])

    filter_complex = (
        f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
        f"scale={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}[top];"
        f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
        f"scale={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}[base]"
    )

    if ass_path and ass_path.exists():
        ass_path_escaped = ass_path.as_posix().replace(":", "\\:")
        filter_complex += f";[base]subtitles='{ass_path_escaped}'[base_sub]"
        base_label = "[base_sub]"
    else:
        base_label = "[base]"

    filter_complex += f";color=c=#D91E18:s={VERTICAL_WIDTH}x{CLIPFLOW_HOOK_BAR_HEIGHT}[hook_bar]"
    filter_complex += f";[top][hook_bar]{base_label}vstack=inputs=3[vout]"

    if has_watermark:
        wm_path_escaped = watermark_path.as_posix().replace(":", "\\:")
        filter_complex += f";movie='{wm_path_escaped}'[wm];[vout][wm]overlay=20:20[wmarked]"
        map_v = "[wmarked]"
    else:
        map_v = "[vout]"

    fade_start = max(0, effective_duration - 1.5)
    filter_complex += f";{map_v}fade=t=out:st={fade_start:.2f}:d=1.5[vout_fade]"
    map_v = "[vout_fade]"

    if audio_mode == "ia" and tts_audio_path and tts_audio_path.exists():
        actual_tts_duration = tts_duration if tts_duration else get_audio_duration(tts_audio_path)
        if actual_tts_duration <= 0:
            actual_tts_duration = clip['duration']
        speed_ratio = clip['duration'] / actual_tts_duration if actual_tts_duration > 0 else 1.0
        filter_complex = filter_complex.replace(
            "[0:v]setpts=PTS-STARTPTS,crop=",
            f"[0:v]setpts=PTS-STARTPTS,setpts={1/speed_ratio:.6f}*PTS, crop="
        )
        af = f"asetpts=PTS-STARTPTS,loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,afade=t=out:st={fade_start:.2f}:d=1.5"
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", map_v,
            "-map", "1:a",
            "-af", af,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            "-fps_mode", "cfr",
            "-movflags", "+faststart",
            str(output_path)
        ])
    else:
        af = f"asetpts=PTS-STARTPTS,loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,afade=t=out:st={fade_start:.2f}:d=1.5"
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", map_v,
            "-map", "0:a?",
            "-af", af,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            "-fps_mode", "cfr",
            "-movflags", "+faststart",
            str(output_path)
        ])

    ok, _, err = run_cmd(cmd, timeout=600)
    if ok:
        print(f"✅ Corte salvo (fallback ClipFlow 3 camadas vertical 1080x1920): {output_path}")
        return True
    else:
        print(f"❌ Falha total no fallback: {err[:500]}")
        return False


def _ask_webcam_choice():
    """Mostra o menu da webcam no terminal e devolve '1'..'7' (None se cancelado)."""
    print("\n🎮 Posicionamento da Webcam (Facecam do Streamer)")
    print("   O vídeo original é 16:9 (horizontal). Onde fica a câmera do streamer?")
    print()
    print("   [1] Canto inferior esquerdo (padrão)")
    print("   [2] Canto superior esquerdo")
    print("   [3] Canto inferior direito")
    print("   [4] Canto superior direito")
    print("   [5] Meio-direita / acima do minimapa (x=0.772, y=0.450, w=0.222, h=0.242)")
    print("   [6] Detectar automaticamente com IA (analisa 1 frame via Groq Vision)")
    print("   [7] Sem webcam (apenas zoom central 9:16 no gameplay)")
    print()

    while True:
        try:
            choice = input("   Escolha uma opção [1-7]: ").strip()
            if choice in ('1', '2', '3', '4', '5', '6', '7'):
                return choice
            print("   ❌ Opção inválida. Digite 1, 2, 3, 4, 5, 6 ou 7.")
        except (EOFError, KeyboardInterrupt):
            print("\n   Cancelado pelo usuário.")
            return None


def ask_webcam_position(video_path=None, api_key=None, choice=None):
    """Define onde fica a facecam do streamer no vídeo 16:9 original.

    choice ('1'..'7'): resposta já conhecida (opção --webcam), sem perguntar no terminal.
    Retorna o dict da posição, None para "sem webcam" (opção 7) ou False se foi cancelado/inválido.
    """
    if choice is not None:
        choice = str(choice).strip()
        if choice not in ('1', '2', '3', '4', '5', '6', '7'):
            print(f"   ❌ --webcam inválido: {choice}. Use de 1 a 7.")
            return False
        print(f"\n🎮 Posicionamento da Webcam: opção {choice} (definida por --webcam)")
    else:
        choice = _ask_webcam_choice()
        if choice is None:
            return False

    if choice == '7':
        print("   ✅ Modo: Apenas gameplay (crop central 9:16)")
        return None

    predefined_positions = {
        '1': {'name': 'Canto inferior esquerdo', 'x': 0.0, 'y': 0.72, 'w': 0.26, 'h': 0.28},
        '2': {'name': 'Canto superior esquerdo', 'x': 0.0, 'y': 0.0, 'w': 0.26, 'h': 0.28},
        '3': {'name': 'Canto inferior direito', 'x': 0.74, 'y': 0.72, 'w': 0.26, 'h': 0.28},
        '4': {'name': 'Canto superior direito', 'x': 0.74, 'y': 0.0, 'w': 0.26, 'h': 0.28},
        '5': {'name': 'Meio-direita / acima do minimapa', 'x': 0.772, 'y': 0.450, 'w': 0.222, 'h': 0.242},
    }

    if choice in predefined_positions:
        pos = predefined_positions[choice]
        print(f"   ✅ Modo: Tela dividida com webcam no {pos['name']}")
        return pos

    if choice == '6':
        if not api_key:
            print("   ⚠️  GROQ_API_KEY não configurada. Fallback para sem webcam.")
            return None
        if not video_path:
            print("   ⚠️  Caminho do vídeo não disponível. Fallback para sem webcam.")
            return None

        print("   🤖 Detectando posição da webcam com IA (Groq Vision)...")
        detected_pos = detect_webcam_position_ai(video_path, api_key)
        if detected_pos:
            print(f"   ✅ Webcam detectada: x={detected_pos['x']:.3f}, y={detected_pos['y']:.3f}, w={detected_pos['w']:.3f}, h={detected_pos['h']:.3f}")
            return detected_pos
        else:
            print("   ⚠️  IA não encontrou webcam. Fallback para sem webcam.")
            return None

    return None


def detect_webcam_position_ai(video_path, api_key):
    """Extrai 1 frame do vídeo e usa Groq Vision para detectar a posição da webcam."""
    import tempfile

    try:
        video_duration = get_video_duration(video_path)
        if video_duration <= 0:
            return None

        timestamp = video_duration * 0.1

        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            frame_path = tmp.name

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
            return None

        try:
            with open(frame_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('utf-8')
        finally:
            try:
                os.unlink(frame_path)
            except:
                pass

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

        vision_models = get_vision_models_to_try(api_key)
        answer = None
        for model in vision_models:
            answer = groq_chat([{"role": "user", "content": content}], api_key, model, temperature=0.1, max_tokens=200, retries=1)
            if answer:
                if model != vision_models[0]:
                    print(f"      ✅ Fallback visão funcionou com: {model}")
                break
            print(f"      ⚠️  Modelo visão {model} falhou, tentando próximo...")

        if not answer:
            return None

        result = extract_json(answer)
        if not result or not isinstance(result, dict):
            return None

        if not result.get('found', False):
            return None

        x = float(result.get('x', 0))
        y = float(result.get('y', 0))
        w = float(result.get('w', 0))
        h = float(result.get('h', 0))

        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
            return None

        if x + w > 1.0 or y + h > 1.0:
            return None

        return {'x': x, 'y': y, 'w': w, 'h': h, 'name': 'Detecção automática (IA)'}

    except Exception as e:
        print(f"   ⚠️  Erro na detecção automática: {e}")
        return None


def censor_profanity(text: str) -> str:
    """Aplica censura simples a palavrões comuns no texto da legenda."""
    PROFANITY_WORDS = [
        "caralho", "porra", "puta", "merda", "foda", "cacete", "buceta", "pau", "rola",
        "cu", "bosta", "arrombado", "otario", "otária", "filho da puta", "filha da puta",
        "vai se foder", "vao se foder", "fodase", "foda-se", "carai", "krl", "cr", "putz"
    ]
    censored = text
    for word in PROFANITY_WORDS:
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        def replace_func(match):
            w = match.group()
            if len(w) <= 2:
                return "*" * len(w)
            return w[0] + "*" * (len(w) - 1)
        censored = pattern.sub(replace_func, censored)
    return censored


def banner_cta_line():
    """Linha fixa da tarja do layout fit (curta/comente/compartilhe). None = sem a linha (CORTES_BANNER_CTA=0)."""
    value = (os.environ.get("CORTES_BANNER_CTA") or "").strip()
    if value.lower() in ("0", "false", "nao", "não", "off", "no"):
        return None
    text = re.sub(r"\s+", " ", value)[:60] if value else ""
    return (text or FIT_BANNER_CTA_TEXT).upper()


def create_hook_banner_image(titulo_gancho: str, pergunta_cta: str, output_path: Path, cta_line=None) -> bool:
    """Gera uma imagem PNG 1080x180 com o banner do gancho usando Pillow.

    cta_line (layout fit): segunda linha fixa, menor, abaixo do título (o título fica maior). Sem ela, o banner
    é o de sempre (o layout antigo usa a faixa para a legenda karaokê no meio, então não ganha a linha).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("   ❌ Pillow não instalado. Instale com: pip install pillow")
        return False

    WIDTH, HEIGHT = VERTICAL_WIDTH, CLIPFLOW_HOOK_BAR_HEIGHT
    BG_COLOR = (0xD9, 0x1E, 0x18)
    BORDER_COLOR = (0xB0, 0x18, 0x14)

    img = Image.new('RGB', (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    draw.line([(0, 0), (WIDTH, 0)], fill=BORDER_COLOR, width=1)
    draw.line([(0, HEIGHT - 1), (WIDTH, HEIGHT - 1)], fill=BORDER_COLOR, width=1)

    font_title = None
    font_paths = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/ariblk.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]

    for fp in font_paths:
        try:
            font_title = ImageFont.truetype(fp, 28)
            print(f"   📝 Fonte carregada: {fp}")
            break
        except OSError:
            continue

    if font_title is None:
        print("   ⚠️  Nenhuma fonte TrueType encontrada, usando fallback")
        font_title = ImageFont.load_default()

    def fit_font(text, size):
        """Fonte do tamanho pedido, encolhida até o texto caber na largura máxima."""
        while size > 14:
            for fp in font_paths:
                try:
                    f = ImageFont.truetype(fp, size)
                except OSError:
                    continue
                box = draw.textbbox((0, 0), text, font=f)
                if box[2] - box[0] <= FIT_BANNER_MAX_TEXT_WIDTH:
                    return f
                break
            else:
                return None
            size -= 2
        return None

    def draw_centered(text, font, y, fill):
        box = draw.textbbox((0, 0), text, font=font)
        x = (WIDTH - (box[2] - box[0])) // 2
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
        draw.text((x, y), text, font=font, fill=fill)

    if cta_line:
        big = fit_font(titulo_gancho, FIT_BANNER_TITLE_SIZE) if titulo_gancho else None
        small = fit_font(cta_line, FIT_BANNER_CTA_SIZE)
        if (big or not titulo_gancho) and small:
            if big:
                draw_centered(titulo_gancho, big, 22, (0xFF, 0xD7, 0x00))
            draw_centered(cta_line, small, 112 if big else 70, (0xFF, 0xFF, 0xFF))
            titulo_gancho = ""          # já desenhado; o desenho simples abaixo não repete
        # sem fonte TrueType: segue com o desenho simples do título (sem a linha)

    if titulo_gancho:
        bbox = draw.textbbox((0, 0), titulo_gancho, font=font_title)
        text_width = bbox[2] - bbox[0]
        x = (WIDTH - text_width) // 2
        y = 12

        outline_offset = 1
        for dx, dy in [(-outline_offset, 0), (outline_offset, 0), (0, -outline_offset), (0, outline_offset)]:
            draw.text((x + dx, y + dy), titulo_gancho, font=font_title, fill=(0, 0, 0))
        draw.text((x, y), titulo_gancho, font=font_title, fill=(0xFF, 0xD7, 0x00))

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, 'PNG')
        print(f"   🖼️  Banner do gancho gerado (faixa limpa para karaokê): {output_path}")
        return True
    except Exception as e:
        print(f"   ❌ Erro ao salvar banner PNG: {e}")
        return False


ENGLISH_STOPWORDS = {
    'the', 'and', 'you', 'your', 'is', 'are', 'to', 'of', 'in', 'on', 'for',
    'with', 'this', 'that', 'police', 'suspect', 'driving', 'report', 'advised',
    'officer', 'unit', 'dispatch', 'code', 'respond', 'backup', 'emergency',
    'vehicle', 'pursuit', 'arrest', 'crime', 'scene', 'investigation', 'witness',
    'statement', 'evidence', 'detective', 'sergeant', 'lieutenant', 'captain',
    'call', 'calling', 'called', 'calls', 'go', 'going', 'went', 'gone',
    'come', 'coming', 'came', 'get', 'getting', 'got', 'gotten',
    'take', 'taking', 'took', 'taken', 'make', 'making', 'made',
    'do', 'doing', 'did', 'done', 'say', 'saying', 'said',
    'see', 'seeing', 'saw', 'seen', 'know', 'knowing', 'knew', 'known',
    'think', 'thinking', 'thought', 'want', 'wanting', 'wanted',
    'need', 'needing', 'needed', 'use', 'using', 'used',
    'find', 'finding', 'found', 'give', 'giving', 'gave', 'given',
    'tell', 'telling', 'told', 'ask', 'asking', 'asked',
    'work', 'working', 'worked', 'seem', 'seeming', 'seemed',
    'feel', 'feeling', 'felt', 'try', 'trying', 'tried',
    'leave', 'leaving', 'left'
}

GAMEPLAY_ALLOWED = {'gg', 'clip', 'fps', 'hp', 'mp', 'xp', 'lvl', 'lvlup',
                    'op', 'nerf', 'buff', 'meta', 'build', 'combo', 'kd',
                    'wp', 'gl', 'hf', 'ez', 'gr', 'ff', 'afk', 'dc', 'lag'}


def filter_portuguese_segments(segments, min_segment_duration=0.5, max_segment_duration=10.0):
    """Filtra segmentos para manter APENAS fala em português do streamer."""
    if not segments:
        return []

    filtered = []

    for seg in segments:
        text = seg['text'].strip()
        if not text:
            continue

        words = text.split()
        if not words:
            continue

        seg_duration = seg['end'] - seg['start']
        if seg_duration < min_segment_duration or seg_duration > max_segment_duration:
            continue

        filtered_words = []
        for w in words:
            w_lower = w.lower().strip('.,!?;:')
            if w_lower in GAMEPLAY_ALLOWED:
                filtered_words.append(w)
            elif w_lower in ENGLISH_STOPWORDS:
                continue
            elif len(w) > 2 and w[0].isupper() and w.isalpha() and w_lower in ENGLISH_STOPWORDS:
                continue
            else:
                filtered_words.append(w)

        if len(filtered_words) < max(1, len(words) * 0.3):
            continue

        filtered_text = ' '.join(filtered_words)
        pt_indicators = sum(1 for w in filtered_words if any(c in w.lower() for c in 'ãõâêôçáéíóúà'))
        common_pt_words = {'que', 'de', 'da', 'do', 'na', 'no', 'em', 'um', 'uma', 'para', 'com', 'não', 'é', 'eu', 'você', 'ele', 'ela', 'nós', 'vocês', 'eles', 'elas', 'meu', 'minha', 'seu', 'sua', 'nosso', 'nossa', 'o', 'a', 'os', 'as', 'e', 'ou', 'mas', 'por', 'como', 'quando', 'onde', 'porque', 'porquê', 'por que'}
        common_pt_count = sum(1 for w in filtered_words if w.lower() in common_pt_words)

        if pt_indicators == 0 and common_pt_count == 0 and len(filtered_words) > 3:
            english_like = sum(1 for w in filtered_words if len(w) > 2 and w[0].isupper() and w.isalpha())
            if english_like >= len(filtered_words) * 0.5:
                continue

        filtered.append({
            'start': seg['start'],
            'end': seg['end'],
            'text': filtered_text,
            'words': seg.get('words')
        })

    print(f"   🔍 Filtro anti-inglês: {len(segments)} -> {len(filtered)} segmentos")
    return filtered


def create_karaoke_ass_subtitle(segments, clip_start, clip_end, clip_duration, output_path):
    """Cria legenda .ass estilo KARAOKÊ centralizada na faixa vermelha."""
    if not segments:
        return False

    segments = filter_portuguese_segments(segments)
    if not segments:
        print("   ⚠️  Nenhum segmento em português válido após filtro")
        return False

    WORDS_PER_CHUNK = CLIPFLOW_KARAOKE_WORDS_PER_CHUNK
    chunks = []

    for seg in segments:
        if seg['end'] <= clip_start:
            continue
        if seg['start'] >= clip_end:
            break

        adj_start = max(0, seg['start'] - clip_start)
        adj_end = min(clip_duration, seg['end'] - clip_start)
        if adj_end <= adj_start:
            continue

        words = seg['text'].split()
        if not words:
            continue

        if 'words' in seg and seg['words']:
            seg_words = seg['words']
            clip_words = []
            for w in seg_words:
                w_start = w['start'] - clip_start
                w_end = w['end'] - clip_start
                if w_end > 0 and w_start < clip_duration:
                    clip_words.append({
                        'word': w['word'],
                        'start': max(0, w_start),
                        'end': min(clip_duration, w_end)
                    })
            if clip_words:
                for i in range(0, len(clip_words), WORDS_PER_CHUNK):
                    chunk_words = clip_words[i:i + WORDS_PER_CHUNK]
                    chunk_start = chunk_words[0]['start']
                    chunk_end = chunk_words[-1]['end']
                    censored_words = [censor_profanity(w['word']).upper() for w in chunk_words]
                    chunks.append({
                        'start': chunk_start,
                        'end': chunk_end,
                        'text': ' '.join(censored_words),
                        'words': censored_words
                    })
                continue

        seg_duration = adj_end - adj_start
        word_duration = seg_duration / len(words) if words else 0

        for i in range(0, len(words), WORDS_PER_CHUNK):
            chunk_words = words[i:i + WORDS_PER_CHUNK]
            chunk_start = adj_start + i * word_duration
            chunk_end = min(adj_start + (i + len(chunk_words)) * word_duration, adj_end)

            censored_words = [censor_profanity(w).upper() for w in chunk_words]
            chunk_text = ' '.join(censored_words)

            chunks.append({
                'start': chunk_start,
                'end': chunk_end,
                'text': chunk_text,
                'words': censored_words
            })

    if not chunks:
        return False

    for i in range(len(chunks) - 1):
        if chunks[i]['end'] > chunks[i + 1]['start']:
            chunks[i]['end'] = chunks[i + 1]['start']

    MAX_CHUNK_DURATION = 3.0
    for chunk in chunks:
        if chunk['end'] - chunk['start'] > MAX_CHUNK_DURATION:
            chunk['end'] = chunk['start'] + MAX_CHUNK_DURATION

    chunks = [c for c in chunks if c['end'] - c['start'] >= 0.3]

    KARAOKE_FONT_SIZE = 54
    KARAOKE_ALIGNMENT = 5
    KARAOKE_MARGIN_V = 0
    KARAOKE_OUTLINE = 3
    KARAOKE_SHADOW = 1
    KARAOKE_POS_X = 540
    KARAOKE_POS_Y = 835

    ass_header = f"""[Script Info]
Title: ClipFlow Gameplay - Karaoke Subtitles (Centered in Red Bar)
ScriptType: v4.00+
PlayResX: {VERTICAL_WIDTH}
PlayResY: {VERTICAL_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Impact,{KARAOKE_FONT_SIZE},&H00FFFFFF,&H00FFFF00,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{KARAOKE_OUTLINE},{KARAOKE_SHADOW},{KARAOKE_ALIGNMENT},20,20,{KARAOKE_MARGIN_V},1

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

    for chunk in chunks:
        chunk_start = chunk['start']
        chunk_end = chunk['end']
        chunk_words = chunk['words']

        karaoke_parts = []
        chunk_total_duration = chunk_end - chunk_start
        word_duration = chunk_total_duration / len(chunk_words) if chunk_words else 0

        for word in chunk_words:
            word_dur_cs = int(word_duration * 100)
            if word_dur_cs < 1:
                word_dur_cs = 1
            karaoke_parts.append(f"{{\\k{word_dur_cs}\\t(\\c&H00D7FF&)}}{word}{{\\t(\\c&HFFFFFF&)}}")

        karaoke_text = " ".join(karaoke_parts)
        start_fmt = format_ass_time(chunk_start)
        end_fmt = format_ass_time(chunk_end)

        events.append(f"Dialogue: 0,{start_fmt},{end_fmt},Karaoke,,0,0,0,karaoke,{{\\pos({KARAOKE_POS_X},{KARAOKE_POS_Y})}}{karaoke_text}")

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(ass_header)
            f.write('\n'.join(events))
        print(f"   📝 Legenda karaokê criada: {output_path} ({len(events)} blocos)")
        return True
    except Exception as e:
        print(f"   ❌ Erro ao criar legenda karaokê: {e}")
        return False


def create_karaoke_ass_subtitle_from_tts(word_events, output_path, clip_duration):
    """Cria legenda .ass estilo KARAOKÊ a partir de eventos WordBoundary do edge-tts."""
    if not word_events:
        return False

    WORDS_PER_CHUNK = CLIPFLOW_KARAOKE_WORDS_PER_CHUNK
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

        is_end_of_sentence = word_text.endswith(('.', '!', '?', '...'))
        if len(current_chunk_words) >= WORDS_PER_CHUNK or is_end_of_sentence:
            chunk_text = ' '.join(current_chunk_words)
            chunk_end = event["offset"] + event["duration"]
            censored_words = [censor_profanity(w).upper() for w in current_chunk_words]
            chunks.append({
                'start': current_chunk_start,
                'end': chunk_end,
                'text': chunk_text,
                'words': censored_words
            })
            current_chunk_words = []
            current_chunk_start = None

    if current_chunk_words:
        last_event = word_events[-1]
        chunk_end = last_event["offset"] + last_event["duration"]
        censored_words = [censor_profanity(w).upper() for w in current_chunk_words]
        chunks.append({
            'start': current_chunk_start,
            'end': chunk_end,
            'text': ' '.join(current_chunk_words),
            'words': censored_words
        })

    if not chunks:
        return False

    for i in range(len(chunks) - 1):
        if chunks[i]['end'] > chunks[i + 1]['start']:
            chunks[i]['end'] = chunks[i + 1]['start']

    MAX_CHUNK_DURATION = 3.0
    for chunk in chunks:
        if chunk['end'] - chunk['start'] > MAX_CHUNK_DURATION:
            chunk['end'] = chunk['start'] + MAX_CHUNK_DURATION

    chunks = [c for c in chunks if c['end'] - c['start'] >= 0.3]

    KARAOKE_FONT_SIZE = 54
    KARAOKE_ALIGNMENT = 5
    KARAOKE_MARGIN_V = 0
    KARAOKE_OUTLINE = 3
    KARAOKE_SHADOW = 1
    KARAOKE_POS_X = 540
    KARAOKE_POS_Y = 835

    ass_header = f"""[Script Info]
Title: ClipFlow - Karaoke Subtitles from TTS (Centered in Red Bar)
ScriptType: v4.00+
PlayResX: {VERTICAL_WIDTH}
PlayResY: {VERTICAL_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Impact,{KARAOKE_FONT_SIZE},&H00FFFFFF,&H00FFFF00,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{KARAOKE_OUTLINE},{KARAOKE_SHADOW},{KARAOKE_ALIGNMENT},20,20,{KARAOKE_MARGIN_V},1

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

    for chunk in chunks:
        chunk_start = chunk['start']
        chunk_end = chunk['end']
        chunk_words = chunk['words']

        karaoke_parts = []
        chunk_total_duration = chunk_end - chunk_start
        word_duration = chunk_total_duration / len(chunk_words) if chunk_words else 0

        for word in chunk_words:
            word_dur_cs = int(word_duration * 100)
            if word_dur_cs < 1:
                word_dur_cs = 1
            karaoke_parts.append(f"{{\\k{word_dur_cs}\\t(\\c&H00D7FF&)}}{word}{{\\t(\\c&HFFFFFF&)}}")

        karaoke_text = " ".join(karaoke_parts)
        start_fmt = format_ass_time(chunk_start)
        end_fmt = format_ass_time(chunk_end)

        events.append(f"Dialogue: 0,{start_fmt},{end_fmt},Karaoke,,0,0,0,karaoke,{{\\pos({KARAOKE_POS_X},{KARAOKE_POS_Y})}}{karaoke_text}")

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(ass_header)
            f.write('\n'.join(events))
        print(f"   📝 Legenda karaokê (TTS) criada: {output_path} ({len(events)} blocos)")
        return True
    except Exception as e:
        print(f"   ❌ Erro ao criar legenda karaokê (TTS): {e}")
        return False


def generate_hook_and_question(transcription_text: str, clip_duration: float, api_key: str) -> dict:
    """Envia a transcrição para o Groq LLM para gerar gancho/pergunta/nota."""
    if not transcription_text.strip():
        print("   ⚠️  Transcrição vazia, não é possível gerar gancho")
        return {
            "titulo_gancho": "MOMENTO INCRÍVEL NA LIVE",
            "pergunta_cta": "O QUE VOCÊ ACHOU?",
            "nota": 75
        }

    prompt = (
        "Você é um especialista em criar clips virais para Shorts/Reels/TikTok. "
        f"Analise a transcrição abaixo de um momento de gameplay/streamer ({clip_duration:.0f} segundos).\n\n"
        "RETORNE APENAS JSON VÁLIDO com exatamente estas chaves:\n"
        '{"titulo_gancho": "FRASE CURTA DE IMPACTO EM CAIXA ALTA (MÁX 5 PALAVRAS)", '
        '"pergunta_cta": "PERGUNTA CURTA PARA GERAR COMENTÁRIOS (MÁX 4 PALAVRAS)", '
        '"nota": 85}\n\n'
        "REGRAS OBRIGATÓRIAS:\n"
        "- titulo_gancho: MAIÚSCULAS, impacto imediato, máximo 5 palavras, sem pontuação final\n"
        "- pergunta_cta: pergunta direta, máximo 4 palavras, termina com ?\n"
        "- nota: inteiro 0-100, baseada no potencial viral do momento\n\n"
        f"TRANSCRIÇÃO:\n{transcription_text[:2000]}"
    )

    models_to_try = get_text_models_to_try(api_key)

    for model in models_to_try:
        try:
            result = groq_chat([{"role": "user", "content": prompt}], api_key, model, temperature=0.7, max_tokens=200)
            if not result:
                print(f"   ⚠️  Groq ({model}) não retornou resposta, tentando próximo...")
                continue

            hook_data = extract_json(result)
            if not hook_data or not isinstance(hook_data, dict):
                print(f"   ⚠️  Resposta inválida do Groq ({model}): {result[:200]}")
                continue

            required_keys = ["titulo_gancho", "pergunta_cta", "nota"]
            if any(key not in hook_data for key in required_keys):
                print(f"   ⚠️  Campos obrigatórios ausentes na resposta do Groq ({model})")
                continue

            titulo = str(hook_data["titulo_gancho"]).strip()
            pergunta = str(hook_data["pergunta_cta"]).strip()
            nota = int(hook_data["nota"])

            print(f"   [IA] Análise ({model}): Nota {nota}/100 | Título: '{titulo}' | Pergunta: '{pergunta}'")

            if nota < CLIPFLOW_HOOK_MIN_SCORE:
                print(f"   ⚠️  Nota {nota}/100 abaixo do mínimo ({CLIPFLOW_HOOK_MIN_SCORE}), tentando próximo modelo...")
                continue

            titulo_words = titulo.split()
            if len(titulo_words) > 5:
                titulo = " ".join(titulo_words[:5])

            pergunta_words = pergunta.split()
            if len(pergunta_words) > 4:
                pergunta = " ".join(pergunta_words[:4])

            if not pergunta.endswith("?"):
                pergunta += "?"

            titulo = titulo.upper()

            print(f"   ✅ Gancho APROVADO ({model}): '{titulo}' | Pergunta: '{pergunta}' | Nota: {nota}")
            return {
                "titulo_gancho": titulo,
                "pergunta_cta": pergunta,
                "nota": nota
            }

        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "400" in error_str or "does not exist" in error_str:
                print(f"   ⚠️  Modelo {model} indisponível (404/400), tentando fallback...")
                continue
            print(f"   ❌ Erro ao gerar gancho/pergunta com {model}: {e}")
            continue

    print("   ⚠️  [FALLBACK] Todos os modelos LLM falharam. Usando gancho padrão para não perder o clipe.")
    return {
        "titulo_gancho": "MOMENTO INCRÍVEL NA LIVE",
        "pergunta_cta": "O QUE VOCÊ ACHOU?",
        "nota": 75
    }


def repair_clip(input_clip_path, output_path=None, groq_api_key=None):
    """Repara/Reprocessa um clipe existente."""
    input_path = Path(input_clip_path)
    if not input_path.exists():
        print(f"❌ Arquivo não encontrado: {input_path}")
        return False

    if output_path is None:
        output_path = input_path.parent / f"{input_path.stem}_reparado{input_path.suffix}"
    else:
        output_path = Path(output_path)

    print(f"\n🔧 Reparando clipe:")
    print(f"   Origem:  {input_path.resolve()}")
    print(f"   Saída:   {output_path.resolve()}")

    clip_duration = get_video_duration(input_path)
    if clip_duration <= 0:
        print("❌ Não foi possível obter duração do clipe")
        return False
    print(f"   Duração: {clip_duration:.1f}s")

    width, height = get_video_dimensions(input_path)
    print(f"   Dimensões: {width}x{height}")

    has_vertical_layout = (width == VERTICAL_WIDTH and height == VERTICAL_HEIGHT)
    if has_vertical_layout:
        print("   ✅ Vídeo já possui layout vertical 1080x1920 (ClipFlow)")
        print("   🔧 Aplicando APENAS correção de sincronia A/V (sem re-empilhar camadas)")
    else:
        print(f"   🔄 Vídeo não é vertical 1080x1920 — aplicando layout ClipFlow 3 camadas completo")

    if groq_api_key is None:
        load_env_file()
        groq_api_key = get_groq_key()

    if groq_api_key:
        print("   🤖 Gerando gancho viral e CTA com IA...")
        hook_data = generate_hook_and_question("", clip_duration, groq_api_key)
    else:
        print("   ⚠️  Sem GROQ_API_KEY, usando gancho padrão")
        hook_data = {
            "titulo_gancho": "MOMENTO INCRÍVEL",
            "pergunta_cta": "O QUE ACHOU?",
            "nota": 75
        }

    ass_path = None

    if has_vertical_layout:
        success = repair_sync_only(input_path, output_path, clip_duration, hook_data, groq_api_key)
    else:
        success = process_clipflow_layout(
            input_path=input_path,
            output_path=output_path,
            webcam_pos=None,
            ass_path=ass_path,
            hook_data=hook_data,
            clip_duration=clip_duration,
            groq_api_key=groq_api_key,
            clip_text="",
            audio_mode="original",
            tts_audio_path=None,
            final_duration=clip_duration,
            speed_ratio=1.0
        )

    if success:
        print(f"✅ Clipe reparado com sucesso: {output_path}")
        return True
    else:
        print(f"❌ Falha ao reparar clipe")
        return False


def get_video_dimensions(video_path):
    """Obtém largura e altura do vídeo via ffmpeg (não depende de ffprobe no PATH)."""
    _, out, err = run_cmd([FFMPEG.as_posix(), "-hide_banner", "-i", str(video_path)])
    combined = f"{out}\n{err}"
    match = re.search(r'Video:.*?(\d{2,5})x(\d{2,5})', combined)
    if match:
        try:
            return int(match.group(1)), int(match.group(2))
        except ValueError:
            pass
    return 0, 0


def repair_sync_only(input_path, output_path, clip_duration, hook_data, groq_api_key):
    """Aplica APENAS correção de sincronia A/V em vídeo que já tem layout vertical 1080x1920."""
    print("   🔧 Modo reparo de sincronia (sem re-layout)...")

    ffmpeg_cmd = FFMPEG.as_posix()
    effective_duration = clip_duration
    fade_start = max(0, effective_duration - 1.5)

    banner_path = TEMP_DIR / "banner_gancho.png"
    titulo_gancho = hook_data.get("titulo_gancho", "") if hook_data else ""
    pergunta_cta = hook_data.get("pergunta_cta", "") if hook_data else ""

    if not create_hook_banner_image(titulo_gancho, pergunta_cta, banner_path):
        print("   ⚠️  Falha ao gerar banner do gancho, prosseguindo sem banner")
        banner_path = None

    if banner_path and banner_path.exists():
        filter_complex = (
            f"[1:v]scale={VERTICAL_WIDTH}:{CLIPFLOW_HOOK_BAR_HEIGHT}:force_original_aspect_ratio=increase,crop={VERTICAL_WIDTH}:{CLIPFLOW_HOOK_BAR_HEIGHT}[banner];"
            f"[0:v][banner]overlay=0:{CLIPFLOW_WEBAM_HEIGHT}:enable='between(t,0,{effective_duration})'[vout]"
        )
        banner_input = ["-loop", "1", "-framerate", "30", "-t", str(effective_duration), "-i", str(banner_path)]
    else:
        filter_complex = (
            f"color=c=#D91E18:s={VERTICAL_WIDTH}x{CLIPFLOW_HOOK_BAR_HEIGHT}[banner];"
            f"[0:v][banner]overlay=0:{CLIPFLOW_WEBAM_HEIGHT}:enable='between(t,0,{effective_duration})'[vout]"
        )
        banner_input = []

    filter_complex += f";[vout]fade=t=out:st={fade_start:.2f}:d=1.5[vout_fade]"
    af = f"asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,afade=t=out:st={fade_start:.2f}:d=1.5"

    cmd = [
        ffmpeg_cmd, "-y",
        "-hide_banner", "-loglevel", "error",
        "-fflags", "+genpts",
        "-i", str(input_path),
    ]
    cmd.extend(banner_input)
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout_fade]",
        "-map", "0:a?",
        "-af", af,
        "-fps_mode", "cfr",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path)
    ])

    print("   ⚙️  Executando ffmpeg (correção de sincronia + overlay banner)...")
    ok, _, err = run_cmd(cmd, timeout=600)

    if not ok:
        err_tail = err[-800:] if len(err) > 800 else err
        print(f"   ❌ Erro no FFmpeg (sync only): {err_tail}")
        return False

    print(f"   ✅ Correção de sincronia aplicada com sucesso")
    return True


def get_layout():
    """Layout escolhido: 'cam' (padrão), 'fit' ou 'clipflow'. Troca pelo .env (CORTES_LAYOUT)."""
    value = (os.environ.get("CORTES_LAYOUT") or DEFAULT_LAYOUT).strip().lower()
    if value == "custom":                     # só vale com um layout salvo carregado (--layout-salvo)
        return "custom" if CUSTOM_LAYOUT else DEFAULT_LAYOUT
    return value if value in ("cam", "fit", "clipflow") else DEFAULT_LAYOUT


def uses_fit_family():
    """True para 'cam', 'custom' e 'fit': mantêm o vídeo inteiro e usam o fit como plano B (fallbacks)."""
    return get_layout() in ("cam", "custom", "fit")


def fit_background_mode():
    """Fundo do layout fit: 'blur' ou 'black'. Troca pelo .env (CORTES_FUNDO)."""
    value = (os.environ.get("CORTES_FUNDO") or FIT_BACKGROUND).strip().lower()
    if value in ("black", "preto"):
        return "black"
    if value in ("blur", "desfocado"):
        return "blur"
    return FIT_BACKGROUND


def fit_geometry(src_w, src_h):
    """Tamanho (largura, altura), pares, do vídeo dentro de 1080x1920 sem cortar nada."""
    if src_w <= 0 or src_h <= 0:
        return 0, 0
    scale = min(VERTICAL_WIDTH / src_w, VERTICAL_HEIGHT / src_h)
    w = int(round(src_w * scale / 2)) * 2
    h = int(round(src_h * scale / 2)) * 2
    return min(w, VERTICAL_WIDTH), min(h, VERTICAL_HEIGHT)


def retarget_ass_for_fit(ass_path, out_path, alignment="2", margin_v=None, pos_y=None):
    """Copia o .ass trocando alinhamento e altura da legenda para a área abaixo do vídeo.

    Cada modo gera o .ass para o seu próprio layout; no layout fit a legenda fica sempre
    centralizada, FIT_SUBTITLE_MARGIN_V px acima da base. A legenda karaokê (gameplay) fixa a posição com
    \\pos(x,y) em cada fala, o que anula alinhamento e margem: com pos_y (layout cam) o y desse \\pos é trocado.
    Se algo falhar, usa o original.
    """
    if margin_v is None:
        margin_v = FIT_SUBTITLE_MARGIN_V
    try:
        text = Path(ass_path).read_text(encoding="utf-8-sig")
    except OSError:
        return Path(ass_path)

    def fix(match):
        fields = match.group(2).split(",")
        if len(fields) >= 23:
            fields[18] = str(alignment)               # Alignment (2 = baixo-centro, 8 = topo-centro)
            fields[21] = str(int(margin_v))           # MarginV
        return match.group(1) + ",".join(fields)

    try:
        text = re.sub(r"^(Style:\s*)(.*)$", fix, text, flags=re.M)
        if pos_y is not None:
            text = re.sub(r"\\pos\((-?\d+(?:\.\d+)?),\s*-?\d+(?:\.\d+)?\)", lambda m: f"\\pos({m.group(1)},{int(pos_y)})", text)
        Path(out_path).write_text(text, encoding="utf-8")
    except OSError:
        return Path(ass_path)
    return Path(out_path)


def export_fit_layout(input_path, output_path, ass_path, hook_data, effective_duration,
                      audio_mode="original", tts_audio_path=None, speed_ratio=1.0,
                      trim_start=None, trim_duration=None, simple=False):
    """Exporta o vídeo ORIGINAL inteiro em 1080x1920, sem recorte e sem zoom.

    - O vídeo é só reduzido para caber (lanczos); nunca é cortado nem esticado.
    - Fundo: cópia desfocada do vídeo (CORTES_FUNDO=blur) ou preto.
    - trim_start/trim_duration: cortam o trecho direto do vídeo completo (usado nos fallbacks;
      no fluxo normal o input já é o trecho pré-cortado).
    - simple=True: fundo preto, sem faixa de gancho nem marca d'água (fallback).
    Retorna (ok, stderr).
    """
    w, h = VERTICAL_WIDTH, VERTICAL_HEIGHT
    is_ia = bool(audio_mode == "ia" and tts_audio_path and Path(tts_audio_path).exists())

    video = "[0:v]setpts=PTS-STARTPTS"
    if is_ia and speed_ratio and speed_ratio > 0:
        video += f",setpts={1 / speed_ratio:.6f}*PTS"
    fit_scale = f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos"

    if not simple and fit_background_mode() == "blur":
        graph = (
            f"{video},split=2[a][b];"
            f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},gblur=sigma=30[bg];"
            f"[b]{fit_scale}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[canvas]"
        )
    else:
        graph = f"{video},{fit_scale},pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black[canvas]"
    label = "[canvas]"

    cmd = [FFMPEG.as_posix(), "-y", "-hide_banner", "-loglevel", "error", "-fflags", "+genpts"]
    if trim_start is not None:
        cmd += ["-ss", str(trim_start), "-t", str(trim_duration)]
    cmd += ["-i", str(input_path)]
    next_input = 1

    if not simple and FIT_SHOW_HOOK and hook_data:
        src_w, src_h = get_video_dimensions(input_path)
        _, fg_h = fit_geometry(src_w, src_h)
        free_top = (h - fg_h) // 2 if fg_h else 0
        if free_top >= CLIPFLOW_HOOK_BAR_HEIGHT + FIT_HOOK_GAP:
            banner_path = TEMP_DIR / "banner_gancho.png"
            if create_hook_banner_image(hook_data.get("titulo_gancho", ""), hook_data.get("pergunta_cta", ""), banner_path,
                                        cta_line=banner_cta_line()):
                y = free_top - CLIPFLOW_HOOK_BAR_HEIGHT - FIT_HOOK_GAP
                cmd += ["-loop", "1", "-framerate", "30", "-t", str(effective_duration), "-i", str(banner_path)]
                graph += f";{label}[{next_input}:v]overlay=0:{y}[withhook]"
                label = "[withhook]"
                next_input += 1

    if ass_path and Path(ass_path).exists():
        fit_ass = retarget_ass_for_fit(ass_path, TEMP_DIR / f"{Path(ass_path).stem}_fit.ass")
        graph += f";{label}subtitles='{Path(fit_ass).as_posix().replace(':', chr(92) + ':')}'[withsubs]"
        label = "[withsubs]"

    watermark_path = Path(GAMEPLAY_WATERMARK_PATH)
    if not simple and watermark_path.exists():
        graph += (f";movie='{watermark_path.as_posix().replace(':', chr(92) + ':')}'[wm];"
                  f"{label}[wm]overlay=20:20[wmarked]")
        label = "[wmarked]"

    fade_start = max(0, effective_duration - 1.5)
    graph += f";{label}fade=t=out:st={fade_start:.2f}:d=1.5[vout]"

    if is_ia:
        cmd += ["-i", str(tts_audio_path)]
        audio_map = f"{next_input}:a"
    else:
        audio_map = "0:a?"
    af = ("asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,"
          "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,"
          f"afade=t=out:st={fade_start:.2f}:d=1.5")

    cmd += [
        "-filter_complex", graph,
        "-map", "[vout]", "-map", audio_map,
        "-af", af,
        "-c:v", "libx264", "-preset", QUALITY_PRESET, "-crf", str(QUALITY_CRF),
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        str(output_path),
    ]
    ok, _, err = run_cmd(cmd, timeout=900)
    return ok, err


def process_fit_layout(input_path, output_path, webcam_pos, ass_path, hook_data, clip_duration, groq_api_key, clip_text,
                       audio_mode="original", tts_audio_path=None, final_duration=None, speed_ratio=1.0):
    """Layout fit no fluxo normal (mesma assinatura do process_clipflow_layout)."""
    print("   🎨 Layout fit: vídeo original inteiro em 1080x1920, sem recorte nem zoom...")
    if webcam_pos is not None:
        print("   ℹ️  A webcam já aparece dentro do vídeo; o layout fit não a recorta nem a amplia.")
    ok, err = export_fit_layout(
        input_path, output_path, ass_path, hook_data,
        final_duration if final_duration else clip_duration,
        audio_mode=audio_mode, tts_audio_path=tts_audio_path, speed_ratio=speed_ratio)
    if not ok:
        print(f"   ❌ Erro no FFmpeg (layout fit): {err[-800:]}")
        return False
    global LAYOUT_APLICADO
    LAYOUT_APLICADO = "fit"
    print(f"   ✅ Corte salvo: {output_path}")
    return True


LAYOUT_APLICADO = None   # layout realmente usado no corte atual ("cam", "fit" ou "clipflow"); vai para o .json
CAM_AMPLIACAO = None     # quantas vezes a câmera foi ampliada no corte atual (layout cam); vai para o .json


def _even(value):
    return int(value) // 2 * 2


def parse_camera_box(text):
    """'x,y,largura,altura' (frações de 0 a 1 do vídeo) -> dict como o da webcam, ou None se inválido."""
    try:
        x, y, w, h = (float(p) for p in str(text).replace(";", ",").split(","))
    except (TypeError, ValueError):
        return None
    if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0 and x + w <= 1.0001 and y + h <= 1.0001):
        return None
    return {"x": x, "y": y, "w": w, "h": h, "name": "Câmera manual (--camera)"}


CAMERA_PREVIEW_NAME = "_previa_camera.jpg"


def save_camera_preview(video_path, webcam_pos, out_path):
    """Salva um quadro do vídeo com a caixa da câmera (amarelo) e o que vai aparecer em cima (verde),
    para conferir a detecção. Falhar aqui nunca atrapalha os cortes."""
    try:
        from PIL import Image, ImageDraw
        duration = get_video_duration(video_path)
        frame = TEMP_DIR / "previa_camera_frame.jpg"
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        ok, _, _ = run_cmd([FFMPEG.as_posix(), "-y", "-hide_banner", "-loglevel", "error",
                            "-ss", str(max(0.0, duration * 0.1)), "-i", str(video_path),
                            "-vframes", "1", "-q:v", "3", str(frame)])
        if not ok or not frame.exists():
            return None
        img = Image.open(frame).convert("RGB")
        w, h = img.size
        geo = cam_geometry(w, h, webcam_pos)
        d = ImageDraw.Draw(img)
        d.rectangle([webcam_pos["x"] * w, webcam_pos["y"] * h,
                     (webcam_pos["x"] + webcam_pos["w"]) * w, (webcam_pos["y"] + webcam_pos["h"]) * h],
                    outline=(255, 215, 0), width=max(3, w // 300))
        if geo:
            d.rectangle([geo["crop_x"], geo["crop_y"], geo["crop_x"] + geo["crop_w"], geo["crop_y"] + geo["crop_h"]],
                        outline=(0, 230, 90), width=max(3, w // 300))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, quality=88)
        print(f"   🖼️  Prévia da câmera (amarelo = detectada, verde = o que aparece em cima): {out_path}")
        print("      Se errou, rode de novo com --camera X,Y,LARG,ALT (frações do vídeo) ou escolha a posição em --webcam.")
        return Path(out_path)
    except Exception as e:
        print(f"   ⚠️  Não foi possível salvar a prévia da câmera: {e}")
        return None


def cam_geometry(src_w, src_h, webcam_pos):
    """Medidas do layout cam, ou None se não der para montá-lo (sem câmera ou caixa inválida).

    O recorte da câmera tem a proporção do bloco (1080 x CAM_TOP_HEIGHT) e contém a caixa indicada, com uma
    folga (CAM_CROP_MARGIN); caixas pequenas demais (só o rosto) crescem até CAM_MIN_CROP_FRAC da largura do
    vídeo. O recorte sempre fica dentro do vídeo. 'ampliacao' = quantas vezes a câmera é esticada.
    """
    if src_w <= 0 or src_h <= 0 or not isinstance(webcam_pos, dict):
        return None
    try:
        fx, fy, fw, fh = (float(webcam_pos[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= fx < 1.0 and 0.0 <= fy < 1.0 and fw > 0 and fh > 0):
        return None
    aspect = VERTICAL_WIDTH / CAM_TOP_HEIGHT
    bw, bh = min(fw, 1.0 - fx) * src_w, min(fh, 1.0 - fy) * src_h
    if bw < 8 or bh < 8:
        return None
    bw, bh = bw * (1 + 2 * CAM_CROP_MARGIN), bh * (1 + 2 * CAM_CROP_MARGIN)
    crop_w = max(bw, bh * aspect, CAM_MIN_CROP_FRAC * src_w)
    crop_h = crop_w / aspect
    if crop_w > src_w or crop_h > src_h:                      # não cabe: encolhe mantendo a proporção
        k = min(src_w / crop_w, src_h / crop_h)
        crop_w, crop_h = crop_w * k, crop_h * k
    cx = (fx + fw / 2) * src_w
    cy = (fy + fh / 2) * src_h
    x0 = min(max(cx - crop_w / 2, 0), src_w - crop_w)
    y0 = min(max(cy - crop_h / 2, 0), src_h - crop_h)
    crop_w, crop_h, x0, y0 = _even(crop_w), _even(crop_h), _even(x0), _even(y0)
    if crop_w < 16 or crop_h < 16:
        return None
    return {
        "crop_x": x0, "crop_y": y0, "crop_w": crop_w, "crop_h": crop_h,
        "ampliacao": round(VERTICAL_WIDTH / crop_w, 2),
        "cam_h": CAM_TOP_HEIGHT, "game_h": CAM_GAME_HEIGHT, "game_crop": None,   # None = recorte central
        "y_cam": 0, "y_band": CAM_TOP_HEIGHT, "y_game": CAM_TOP_HEIGHT + CAM_BAND_HEIGHT,
    }


def layout_slug(name):
    """Nome de arquivo seguro para um layout salvo (minúsculas, sem acento, só a-z 0-9 _ -)."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", _plain(name or "")).strip("-_")
    return slug[:40]


def _valid_box(box):
    try:
        x, y, w, h = (float(box[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0
            and x + w <= 1.0001 and y + h <= 1.0001):
        return None
    return {"x": x, "y": y, "w": min(w, 1.0 - x), "h": min(h, 1.0 - y)}


def normalize_saved_layout(data):
    """Valida o conteúdo de um layout salvo e devolve a versão limpa, ou None se algo estiver errado."""
    if not isinstance(data, dict):
        return None
    cam, game = _valid_box(data.get("cam") or {}), _valid_box(data.get("game") or {})
    if cam is None or game is None:
        return None
    try:
        cam_altura = int(data.get("cam_altura", CAM_TOP_HEIGHT))
    except (TypeError, ValueError):
        return None
    if not CUSTOM_CAM_MIN_HEIGHT <= cam_altura <= CUSTOM_CAM_MAX_HEIGHT:
        return None
    ordem = data.get("ordem", "cam_primeiro")
    if ordem not in CUSTOM_ORDERS:
        return None
    return {"nome": str(data.get("nome") or "")[:60], "cam": cam, "game": game,
            "cam_altura": cam_altura, "ordem": ordem}


def load_saved_layout(name):
    """Lê layouts_salvos/<slug>.json. None se não existir ou for inválido."""
    slug = layout_slug(name)
    if not slug:
        return None
    try:
        data = json.loads((LAYOUTS_DIR / f"{slug}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    layout = normalize_saved_layout(data)
    if layout is not None:
        layout["slug"] = slug
    return layout


CUSTOM_LAYOUT = None     # layout salvo em uso nesta execução (preenchido por activate_saved_layout)


def activate_saved_layout(name):
    """Carrega o layout salvo e passa a usá-lo nos cortes do modo gameplay. False se não achar."""
    global CUSTOM_LAYOUT
    layout = load_saved_layout(name)
    if layout is None:
        return False
    CUSTOM_LAYOUT = layout
    os.environ["CORTES_LAYOUT"] = "custom"
    return True


def custom_geometry(src_w, src_h, layout):
    """Medidas do layout personalizado (mesmo formato do cam_geometry), ou None se não der."""
    if src_w <= 0 or src_h <= 0 or not layout:
        return None

    def crop(box):
        x0, y0 = _even(box["x"] * src_w), _even(box["y"] * src_h)
        w = _even(min(box["w"] * src_w, src_w - x0))
        h = _even(min(box["h"] * src_h, src_h - y0))
        return x0, y0, w, h

    cx, cy, cw, ch = crop(layout["cam"])
    gx, gy, gw, gh = crop(layout["game"])
    if min(cw, ch, gw, gh) < 16:
        return None
    cam_h = int(layout["cam_altura"])
    game_h = VERTICAL_HEIGHT - cam_h - CAM_BAND_HEIGHT
    if layout.get("ordem") == "game_primeiro":
        y_game, y_band, y_cam = 0, game_h, game_h + CAM_BAND_HEIGHT
    else:
        y_cam, y_band, y_game = 0, cam_h, cam_h + CAM_BAND_HEIGHT
    return {
        "crop_x": cx, "crop_y": cy, "crop_w": cw, "crop_h": ch,
        "ampliacao": round(VERTICAL_WIDTH / cw, 2),
        "cam_h": cam_h, "game_h": game_h, "game_crop": (gx, gy, gw, gh),
        "y_cam": y_cam, "y_band": y_band, "y_game": y_game,
    }


def create_cam_band_image(cta_line, output_path):
    """Faixa vermelha 1080 x CAM_BAND_HEIGHT do layout cam: a legenda é desenhada sobre ela pelo ffmpeg
    (parte de cima) e a linha fixa (curta/comente/compartilhe) fica na parte de baixo. cta_line=None: só a faixa."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("   ❌ Pillow não instalado. Instale com: pip install pillow")
        return False
    width, height = VERTICAL_WIDTH, CAM_BAND_HEIGHT
    img = Image.new("RGB", (width, height), (0xD9, 0x1E, 0x18))
    draw = ImageDraw.Draw(img)
    border = (0xB0, 0x18, 0x14)
    draw.line([(0, 0), (width, 0)], fill=border, width=2)
    draw.line([(0, height - 1), (width, height - 1)], fill=border, width=2)
    if cta_line:
        font_paths = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/ariblk.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                      "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
        font = None
        size = CAM_BAND_CTA_SIZE
        while font is None and size > 14:
            for fp in font_paths:
                try:
                    candidate = ImageFont.truetype(fp, size)
                except OSError:
                    continue
                box = draw.textbbox((0, 0), cta_line, font=candidate)
                if box[2] - box[0] <= FIT_BANNER_MAX_TEXT_WIDTH:
                    font = candidate
                break
            else:
                break            # nenhuma fonte TrueType disponível
            size -= 2
        if font is not None:
            box = draw.textbbox((0, 0), cta_line, font=font)
            x = (width - (box[2] - box[0])) // 2
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                draw.text((x + dx, CAM_BAND_CTA_Y + dy), cta_line, font=font, fill=(0, 0, 0))
            draw.text((x, CAM_BAND_CTA_Y), cta_line, font=font, fill=(0xFF, 0xFF, 0xFF))
    try:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, "PNG")
        return True
    except Exception as e:
        print(f"   ❌ Erro ao salvar a faixa PNG: {e}")
        return False


def build_cam_cmd(input_path, output_path, ass_path, band_path, geo, effective_duration):
    """Comando do ffmpeg do layout cam (câmera em cima, faixa vermelha com legenda, gameplay embaixo)."""
    w = VERTICAL_WIDTH
    if geo.get("game_crop"):                  # layout personalizado: o usuário escolheu a caixa do gameplay
        gx, gy, gw, gh = geo["game_crop"]
        game_src = f"crop={gw}:{gh}:{gx}:{gy},"
    else:                                     # layout cam: recorte central
        game_src = ""
    ordem = "[cam][1:v][game]" if geo["y_cam"] < geo["y_game"] else "[game][1:v][cam]"
    graph = (
        f"[0:v]setpts=PTS-STARTPTS,split=2[a][b];"
        f"[a]crop={geo['crop_w']}:{geo['crop_h']}:{geo['crop_x']}:{geo['crop_y']},"
        f"scale={w}:{geo['cam_h']}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{geo['cam_h']},setsar=1[cam];"
        f"[b]{game_src}scale={w}:{geo['game_h']}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={w}:{geo['game_h']},setsar=1[game];"
        f"{ordem}vstack=inputs=3[canvas]"
    )
    label = "[canvas]"

    if ass_path and Path(ass_path).exists():
        # a legenda karaokê usa \pos fixo: é ele que precisa ir para dentro da faixa
        cam_ass = retarget_ass_for_fit(ass_path, TEMP_DIR / f"{Path(ass_path).stem}_cam.ass",
                                       alignment="5", margin_v=0, pos_y=geo["y_band"] + CAM_SUBTITLE_CENTER_Y)
        graph += f";{label}subtitles='{Path(cam_ass).as_posix().replace(':', chr(92) + ':')}'[withsubs]"
        label = "[withsubs]"

    watermark_path = Path(GAMEPLAY_WATERMARK_PATH)
    if watermark_path.exists():
        graph += (f";movie='{watermark_path.as_posix().replace(':', chr(92) + ':')}'[wm];"
                  f"{label}[wm]overlay=20:20[wmarked]")
        label = "[wmarked]"

    fade_start = max(0, effective_duration - 1.5)
    graph += f";{label}fade=t=out:st={fade_start:.2f}:d=1.5[vout]"
    af = ("asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,"
          "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,"
          f"afade=t=out:st={fade_start:.2f}:d=1.5")
    return [
        FFMPEG.as_posix(), "-y", "-hide_banner", "-loglevel", "error", "-fflags", "+genpts",
        "-i", str(input_path),
        "-loop", "1", "-framerate", "30", "-t", str(effective_duration), "-i", str(band_path),
        "-filter_complex", graph,
        "-map", "[vout]", "-map", "0:a?",
        "-af", af,
        "-c:v", "libx264", "-preset", QUALITY_PRESET, "-crf", str(QUALITY_CRF),
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        str(output_path),
    ]


def process_cam_layout(input_path, output_path, webcam_pos, ass_path, hook_data, clip_duration, groq_api_key, clip_text,
                       audio_mode="original", tts_audio_path=None, final_duration=None, speed_ratio=1.0):
    """Layout cam no fluxo normal. Se não der para montá-lo (sem câmera, caixa inválida), usa o fit."""
    def to_fit(motivo):
        print(f"   ℹ️  Layout cam não aplicado ({motivo}); usando o layout fit.")
        return process_fit_layout(input_path, output_path, None, ass_path, hook_data, clip_duration, groq_api_key,
                                  clip_text, audio_mode=audio_mode, tts_audio_path=tts_audio_path,
                                  final_duration=final_duration, speed_ratio=speed_ratio)

    global LAYOUT_APLICADO, CAM_AMPLIACAO
    CAM_AMPLIACAO = None
    if audio_mode != "original":
        return to_fit("só vale para o modo gameplay")
    custom = CUSTOM_LAYOUT if get_layout() == "custom" else None
    if not custom and not webcam_pos:
        return to_fit("câmera do streamer não indicada/encontrada")
    src_w, src_h = get_video_dimensions(input_path)
    geo = custom_geometry(src_w, src_h, custom) if custom else cam_geometry(src_w, src_h, webcam_pos)
    if geo is None:
        return to_fit("caixa da câmera/gameplay inválida")
    band_path = TEMP_DIR / "faixa_cam.png"
    if not create_cam_band_image(banner_cta_line(), band_path):
        return to_fit("não foi possível desenhar a faixa vermelha")

    if custom:
        print(f"   🎨 Layout personalizado '{custom.get('nome') or custom.get('slug')}': câmera ampliada {geo['ampliacao']:.2f}x, "
              f"faixa vermelha com legenda, gameplay na caixa escolhida...")
    else:
        print(f"   🎨 Layout cam: câmera em cima (ampliada {geo['ampliacao']:.2f}x), faixa vermelha com legenda, gameplay embaixo...")
    if geo["ampliacao"] > CAM_WARN_UPSCALE:
        print(f"   ⚠️  A câmera é pequena no vídeo e foi ampliada {geo['ampliacao']:.1f}x: pode ficar borrada.")
    effective = final_duration if final_duration else clip_duration
    ok, _, err = run_cmd(build_cam_cmd(input_path, output_path, ass_path, band_path, geo, effective), timeout=900)
    if not ok:
        print(f"   ❌ Erro no FFmpeg (layout cam): {err[-800:]}")
        return False
    LAYOUT_APLICADO = "custom" if custom else "cam"
    CAM_AMPLIACAO = geo["ampliacao"]
    print(f"   ✅ Corte salvo: {output_path}")
    return True


def fit_fallback(video_path, start, duration, output_path, ass_path, audio_mode="original",
                 tts_audio_path=None, tts_duration=None):
    """Fallback do layout fit: corta o trecho certo (-ss/-t) direto do vídeo completo.

    Tenta fundo preto com legenda; se falhar, tenta sem legenda. Nunca codifica o vídeo inteiro.
    """
    is_ia = bool(audio_mode == "ia" and tts_audio_path and Path(tts_audio_path).exists())
    if is_ia:
        real = tts_duration if tts_duration else get_audio_duration(tts_audio_path)
        if not real or real <= 0:
            real = duration
        speed_ratio = duration / real
        effective = real
    else:
        speed_ratio, effective = 1.0, duration
    for subs in ([ass_path, None] if ass_path else [None]):
        ok, err = export_fit_layout(video_path, output_path, subs, None, effective,
                                    audio_mode=audio_mode, tts_audio_path=tts_audio_path,
                                    speed_ratio=speed_ratio, trim_start=start, trim_duration=duration,
                                    simple=True)
        if ok:
            print(f"   ✅ Corte salvo (fallback fit{'' if subs else ', sem legenda'}): {output_path}")
            return True
        print(f"   ❌ Falha no fallback fit: {err[-500:]}")
    return False


def process_clipflow_layout(input_path, output_path, webcam_pos, ass_path, hook_data, clip_duration, groq_api_key, clip_text,
                            audio_mode="original", tts_audio_path=None, final_duration=None, speed_ratio=1.0):
    """Aplica o layout dos cortes (todos os modos). Padrão: fit (vídeo inteiro, sem zoom);
    CORTES_LAYOUT=clipflow usa o layout de 3 camadas (webcam recortada + gameplay com zoom)."""
    layout = get_layout()
    if layout in ("cam", "custom"):
        return process_cam_layout(input_path, output_path, webcam_pos, ass_path, hook_data, clip_duration,
                                  groq_api_key, clip_text, audio_mode=audio_mode,
                                  tts_audio_path=tts_audio_path, final_duration=final_duration,
                                  speed_ratio=speed_ratio)
    if layout == "fit":
        return process_fit_layout(input_path, output_path, webcam_pos, ass_path, hook_data, clip_duration,
                                  groq_api_key, clip_text, audio_mode=audio_mode,
                                  tts_audio_path=tts_audio_path, final_duration=final_duration,
                                  speed_ratio=speed_ratio)
    global LAYOUT_APLICADO
    LAYOUT_APLICADO = "clipflow"
    print("   🎨 Aplicando layout ClipFlow 3 camadas (1080x1920)...")

    ffmpeg_cmd = FFMPEG.as_posix()
    watermark_path = Path(GAMEPLAY_WATERMARK_PATH)
    has_watermark = watermark_path.exists()

    titulo_gancho = hook_data.get("titulo_gancho", "") if hook_data else ""
    pergunta_cta = hook_data.get("pergunta_cta", "") if hook_data else ""

    effective_duration = final_duration if final_duration else clip_duration

    banner_path = TEMP_DIR / "banner_gancho.png"
    if not create_hook_banner_image(titulo_gancho, pergunta_cta, banner_path):
        print("   ⚠️  Falha ao gerar banner do gancho, prosseguindo sem banner")
        banner_path = None

    if webcam_pos is None:
        filter_complex = (
            f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}[top];"
            f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}[base]"
        )
    else:
        wc_x = f"iw*{webcam_pos['x']:.6f}"
        wc_y = f"ih*{webcam_pos['y']:.6f}"
        wc_w = f"iw*{webcam_pos['w']:.6f}"
        wc_h = f"ih*{webcam_pos['h']:.6f}"

        filter_complex = (
            f"[0:v]setpts=PTS-STARTPTS,crop={wc_w}:{wc_h}:{wc_x}:{wc_y},"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}[top];"
            f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}[base]"
        )

    if banner_path and banner_path.exists():
        filter_complex += f";[1:v]scale={VERTICAL_WIDTH}:{CLIPFLOW_HOOK_BAR_HEIGHT}:force_original_aspect_ratio=increase,crop={VERTICAL_WIDTH}:{CLIPFLOW_HOOK_BAR_HEIGHT}[hook_bar]"
        bar_label = "[hook_bar]"
        banner_input = ["-loop", "1", "-framerate", "30", "-t", str(effective_duration), "-i", str(banner_path)]
    else:
        filter_complex += f";color=c=#D91E18:s={VERTICAL_WIDTH}x{CLIPFLOW_HOOK_BAR_HEIGHT}[hook_bar]"
        bar_label = "[hook_bar]"
        banner_input = []

    filter_complex += f";[top]{bar_label}[base]vstack=inputs=3[stacked]"

    if ass_path and ass_path.exists():
        ass_path_escaped = ass_path.as_posix().replace(":", "\\:")
        filter_complex += f";[stacked]subtitles='{ass_path_escaped}'[vout]"
    else:
        filter_complex += ";[stacked]copy[vout]"

    fade_start = max(0, effective_duration - 1.5)
    filter_complex += f";[vout]fade=t=out:st={fade_start:.2f}:d=1.5[vout_fade]"
    map_v = "[vout_fade]"

    if has_watermark:
        wm_path_escaped = watermark_path.as_posix().replace(":", "\\:")
        filter_complex = filter_complex.replace(
            "[vout]fade=t=out:st=",
            f"movie='{wm_path_escaped}'[wm];[vout][wm]overlay=20:20[wmarked];[wmarked]fade=t=out:st="
        )
        map_v = "[vout_fade]"

    af = f"asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,afade=t=out:st={fade_start:.2f}:d=1.5"

    has_banner_input = len(banner_input) > 0
    tts_audio_index = 2 if has_banner_input else 1

    cmd = [
        ffmpeg_cmd, "-y",
        "-hide_banner", "-loglevel", "error",
        "-fflags", "+genpts",
        "-i", str(input_path),
    ]
    cmd.extend(banner_input)

    if audio_mode == "ia" and tts_audio_path and tts_audio_path.exists():
        cmd.extend(["-i", str(tts_audio_path)])
        if speed_ratio > 0:
            filter_complex = filter_complex.replace(
                "[0:v]setpts=PTS-STARTPTS,crop=",
                f"[0:v]setpts=PTS-STARTPTS,setpts={1/speed_ratio:.6f}*PTS,crop="
            )
        audio_map = f"{tts_audio_index}:a"
    else:
        audio_map = "0:a?"

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", map_v,
        "-map", audio_map,
        "-af", af,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        str(output_path)
    ])

    print("   ⚙️  Executando ffmpeg (ClipFlow 3 camadas)...")
    ok, _, err = run_cmd(cmd, timeout=600)

    if not ok:
        err_tail = err[-800:] if len(err) > 800 else err
        print(f"   ❌ Erro no FFmpeg layout 3 camadas: {err_tail}")
        return False

    print(f"   ✅ Layout ClipFlow 3 camadas aplicado com sucesso: {output_path}")
    return True


# ---------------------------------------------------------------------------------------------
# Resumo narrado no início do corte (vídeo parado no primeiro quadro + voz resumindo o assunto)
# ---------------------------------------------------------------------------------------------
# Por que um corte saiu SEM resumo/chamada final (para o relatório no fim da execução)
EXTRAS_MOTIVO = {}
EXTRAS_RELATORIO = []


def _skip(tipo, motivo):
    """Anota o motivo de um recurso opcional não ter sido aplicado e devolve None."""
    EXTRAS_MOTIVO[tipo] = motivo
    return None


def summary_intro_enabled():
    """Resumo no início: ligado por padrão. Desliga com CORTES_RESUMO=0 (ou --sem-resumo)."""
    value = os.environ.get("CORTES_RESUMO")
    if value is None or not value.strip():
        return SUMMARY_INTRO_DEFAULT
    return value.strip().lower() not in ("0", "false", "nao", "não", "off", "no")


def _content_stems(text):
    """Radicais (5 letras, sem acento) das palavras de conteúdo de um texto."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", str(text).lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return {w[:5] for w in re.findall(r"[a-z0-9]+", plain) if len(w) >= 5}


def summary_is_grounded(summary, source_text, minimum=SUMMARY_MIN_GROUNDING):
    """Confere se o resumo usa palavras que existem na fala real (barreira contra invenção)."""
    mine = _content_stems(summary)
    if len(mine) < 2:
        return True
    theirs = _content_stems(source_text)
    return len(mine & theirs) / len(mine) >= minimum


def _plain(text):
    """Minúsculas e sem acento, para comparar nomes."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in plain if not unicodedata.combining(c))


def clean_streamer_name(raw):
    """Nome do streamer em texto simples (sem @, emojis ou lixo); None se não sobrar nada útil."""
    text = re.sub(r"[^\w\s.\-À-ÿ]", " ", str(raw or ""))
    text = re.sub(r"\s+", " ", text).strip(" .-_")
    return text[:40] if len(text) >= 2 else None


def looks_like_cuts_channel(name):
    """Canal de cortes/compilações (o nome do canal não é o do streamer)."""
    plain = _plain(name)
    return any(re.search(rf"\b{re.escape(h)}", plain) for h in SUMMARY_CUT_CHANNEL_HINTS)


def fetch_video_info(url):
    """Metadados do vídeo (título, canal, ...) via yt-dlp, sem baixar nada. {} se falhar."""
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--skip-download", "--no-warnings", "-J"]
    if is_youtube_url(url):
        cmd += ytdlp_js_args()
    cmd.append(url)
    try:
        ok, out, _ = run_cmd(cmd, timeout=120)
        start = out.find("{") if out else -1
        data = json.loads(out[start:]) if ok and start >= 0 else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_streamer_name(info, override=None):
    """Nome do streamer para o resumo: o informado (--streamer) ou o do canal; None se não der para saber."""
    name = clean_streamer_name(override)
    if name:
        return name
    for key in ("channel", "uploader"):
        name = clean_streamer_name((info or {}).get(key))
        if name and not looks_like_cuts_channel(name):
            return name
    return None


def apply_streamer_name(summary, name):
    """Garante o nome no resumo: se a IA escreveu "o streamer", troca pelo nome."""
    if not name or _plain(name) in _plain(summary):
        return summary
    for pattern, repl in ((r"\bdo streamer\b", f"de {name}"), (r"\bao streamer\b", f"a {name}"),
                          (r"\bpelo streamer\b", f"por {name}"), (r"\bo streamer\b", name),
                          (r"\bda streamer\b", f"de {name}"), (r"\ba streamer\b", name)):
        new, n = re.subn(pattern, repl, summary, count=1, flags=re.I)
        if n:
            return new
    return summary


def _clean_summary(raw):
    """Tira aspas, emojis, hashtags e espaços repetidos do resumo devolvido pela IA."""
    text = str(raw or "")
    text = re.sub(r"#\w+", " ", text)
    text = re.sub(r"[^\w\s.,!?:;'\-%/()À-ÿ]", " ", text)   # emojis, aspas, símbolos
    return re.sub(r"\s+", " ", text).strip()


def generate_summary_intro_text(clip_text, api_key, streamer=None):
    """Pede à IA 1-2 frases que apresentam o trecho, usando SOMENTE o que foi dito nele.

    Devolve o texto ou None (sem fala, sem chave, resposta fora das regras ou inventada).
    """
    source = (clip_text or "").strip()
    if not source or not api_key:
        return None
    prompt = (
        "Você escreve a ABERTURA NARRADA de um Short a partir da transcrição de um trecho de live/gameplay. "
        "O vídeo fica parado no primeiro quadro enquanto um narrador resume o assunto; depois o trecho começa.\n\n"
        f"REGRAS OBRIGATÓRIAS:\n- 1 a 2 frases, de {SUMMARY_MIN_WORDS} a {SUMMARY_MAX_WORDS} palavras no total\n"
        "- use SOMENTE o que está dito na transcrição: não invente nomes, números, jogos, lugares ou fatos que não aparecem nela\n"
        + (f"- o streamer se chama {streamer}: use esse NOME na frase (nunca \"o streamer\"); não invente nenhum outro nome\n"
           if streamer else "- fale em terceira pessoa (\"o streamer...\"), sem inventar nome\n")
        + "- tom animado mas natural, em português do Brasil\n"
        "- apresente o assunto e crie curiosidade; NÃO conte o desfecho nem a piada final\n"
        "- sem emojis, hashtags, aspas ou palavrões\n\n"
        'RETORNE APENAS JSON VÁLIDO: {"resumo": "texto da abertura"}\n\n'
        f"TRANSCRIÇÃO:\n{source[:2500]}"
    )
    for model in get_text_models_to_try(api_key):
        try:
            result = groq_chat([{"role": "user", "content": prompt}], api_key, model, temperature=0.4, max_tokens=250)
        except Exception as e:
            print(f"   ⚠️  Resumo: erro no Groq ({model}): {e}")
            continue
        data = extract_json(result) if result else None
        if not isinstance(data, dict):
            continue
        summary = _clean_summary(data.get("resumo"))
        words = summary.split()
        if len(words) > SUMMARY_MAX_WORDS:
            # tenta ficar só com a primeira frase
            first = re.match(r"^(.+?[.!?])(\s|$)", summary)
            summary = first.group(1) if first else ""
            words = summary.split()
        if not (SUMMARY_MIN_WORDS <= len(words) <= SUMMARY_MAX_WORDS):
            print(f"   ⚠️  Resumo fora do tamanho ({len(words)} palavras) em {model}, tentando outro modelo...")
            continue
        summary = apply_streamer_name(summary, streamer)
        if not summary_is_grounded(summary, f"{source} {streamer or ''}"):
            print(f"   ⚠️  Resumo descartado: usa assuntos que não estão na fala do trecho ({model}).")
            continue
        return summary
    return None


def _speak_summary(text, tts_path, rate):
    """Gera a voz do resumo. Devolve (ok, duração_em_segundos, eventos_de_palavra)."""
    try:
        ok, duration, events = asyncio.run(generate_tts_audio(text, Path(tts_path), rate=rate))
    except Exception as e:
        print(f"   ⚠️  Resumo: falha ao gerar a voz: {e}")
        return False, 0, []
    return bool(ok), float(duration or 0), events or []


def intro_word_timings(events):
    """(início, fim, palavra) de cada palavra falada, a partir dos eventos do edge-tts."""
    kinds = {e.get("type") for e in events}
    use = [e for e in events if e.get("type") == "WordBoundary"] if "WordBoundary" in kinds else list(events)
    words = []
    for ev in use:
        parts = str(ev.get("text", "")).split()
        if not parts:
            continue
        start = float(ev.get("offset", 0))
        dur = max(float(ev.get("duration", 0)), 0.05)
        step = dur / len(parts)
        for i, part in enumerate(parts):
            words.append((start + i * step, start + (i + 1) * step, part))
    return words


def create_intro_ass(word_events, output_path, total_duration, per_chunk=3):
    """Legenda da narração do resumo: blocos de poucas palavras, na mesma altura da legenda do corte."""
    words = intro_word_timings(word_events)
    if not words:
        return False
    chunks = [words[i:i + per_chunk] for i in range(0, len(words), per_chunk)]

    def stamp(sec):
        sec = max(0.0, sec)
        return f"{int(sec // 3600)}:{int(sec % 3600 // 60):02d}:{int(sec % 60):02d}.{int((sec - int(sec)) * 100):02d}"

    lines = []
    for i, chunk in enumerate(chunks):
        start = chunk[0][0]
        end = chunks[i + 1][0][0] if i + 1 < len(chunks) else min(chunk[-1][1] + 0.25, total_duration)
        if end - start < 0.15:
            end = start + 0.15
        text = " ".join(w[2] for w in chunk).upper()
        text = re.sub(r"[{}\\]", "", text)
        lines.append(f"Dialogue: 0,{stamp(start)},{stamp(end)},Intro,,0,0,0,,{text}")
    header = (
        "[Script Info]\nTitle: Resumo narrado\nScriptType: v4.00+\n"
        f"PlayResX: {VERTICAL_WIDTH}\nPlayResY: {VERTICAL_HEIGHT}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Intro,Impact,{SUMMARY_SUBTITLE_FONT_SIZE},&H00FFFFFF,&H00FFFF00,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,4,1,2,40,40,{FIT_SUBTITLE_MARGIN_V},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    try:
        Path(output_path).write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"   ⚠️  Resumo: não consegui criar a legenda da narração: {e}")
        return False
    return True


_FPS_EXATOS = (("23.98", "24000/1001"), ("29.97", "30000/1001"), ("59.94", "60000/1001"))


def normalize_fps(raw):
    """Taxa de quadros em texto para o ffmpeg; 29.97 vira 30000/1001 (igual ao vídeo de origem)."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return "30"
    for approx, exact in _FPS_EXATOS:
        if abs(value - float(approx)) < 0.02:
            return exact
    return str(int(round(value))) if abs(value - round(value)) < 0.01 else str(raw)


def probe_stream_info(path):
    """fps, taxa de amostragem e canais do corte pronto (a intro precisa sair idêntica para juntar sem recodificar)."""
    _, out, err = run_cmd([FFMPEG.as_posix(), "-hide_banner", "-i", str(path)])
    text = f"{out}\n{err}"
    info = {"fps": "30", "sample_rate": 44100, "channels": 2, "has_audio": False}
    m = re.search(r"Video:.*?,\s*([\d.]+) fps", text)
    if m:
        info["fps"] = normalize_fps(m.group(1))
    m = re.search(r"Audio:[^\n]*?,\s*(\d{4,6}) Hz,\s*([^,\n]+)", text)
    if m:
        info["has_audio"] = True
        info["sample_rate"] = int(m.group(1))
        info["channels"] = 1 if m.group(2).strip().lower() == "mono" else 2
    elif "Audio:" in text:
        info["has_audio"] = True
    return info


def build_intro_cmd(frame_png, tts_path, ass_path, output_path, duration, info, pre_filter=""):
    """ffmpeg que cria a intro: primeiro quadro parado + legenda + voz, com os parâmetros do corte."""
    subs = Path(ass_path).as_posix().replace(":", "\\:")
    graph = (f"[0:v]{pre_filter + ',' if pre_filter else ''}subtitles='{subs}'[v];"
             f"[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample={info['sample_rate']},apad[a]")
    return [
        FFMPEG.as_posix(), "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-framerate", info["fps"], "-i", str(frame_png),
        "-i", str(tts_path),
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-t", f"{duration:.3f}", "-r", info["fps"],
        "-c:v", "libx264", "-preset", QUALITY_PRESET, "-crf", str(QUALITY_CRF),
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(info["sample_rate"]), "-ac", str(info["channels"]),
        "-movflags", "+faststart", str(output_path),
    ]


def build_concat_copy_cmd(list_path, output_path):
    """Junta intro + corte SEM recodificar (o corte mantém exatamente a qualidade que já tinha)."""
    return [FFMPEG.as_posix(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", "-movflags", "+faststart", str(output_path)]


def build_concat_reencode_cmd(intro_path, main_path, output_path):
    """Plano B da junção: recodifica (qualidade do corte um pouco menor, mas sempre funciona)."""
    return [FFMPEG.as_posix(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(intro_path), "-i", str(main_path),
            "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", QUALITY_PRESET, "-crf", str(QUALITY_CRF),
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(output_path)]


def _concat_with_fallback(first, second, joined_path, list_path, expected):
    """Junta first + second em joined_path. Devolve ("copia" | "reencode", "") ou (None, erro)."""
    def quoted(p):
        return "file '" + Path(p).resolve().as_posix().replace("'", "'\\''") + "'"
    Path(list_path).write_text(quoted(first) + "\n" + quoted(second) + "\n", encoding="utf-8")
    ok, _, err = run_cmd(build_concat_copy_cmd(list_path, joined_path), timeout=300)
    if ok and Path(joined_path).exists() and _duration_matches(joined_path, expected):
        return "copia", ""
    print("   ⚠️  Junção sem recodificar não ficou boa; refazendo recodificando (qualidade um pouco menor).")
    ok, _, err = run_cmd(build_concat_reencode_cmd(first, second, joined_path), timeout=900)
    if ok and Path(joined_path).exists() and _duration_matches(joined_path, expected):
        return "reencode", ""
    return None, err


def _duration_matches(path, expected, tolerance=0.6):
    real = get_video_duration(path)
    return real > 0 and abs(real - expected) <= tolerance


def add_summary_intro(output_path, clip_text, api_key, streamer=None):
    """Coloca o resumo narrado no começo de um corte já pronto (1080x1920).

    Devolve um dict com o que foi feito (para o .json) ou None se não houve intro. NUNCA levanta
    erro nem estraga o corte: o arquivo só é trocado quando a versão com intro está completa.
    """
    EXTRAS_MOTIVO.pop("resumo", None)
    if not summary_intro_enabled():
        return None
    output_path = Path(output_path)
    if not api_key:
        print("   ℹ️  Resumo no início: sem GROQ_API_KEY, o corte sai sem ele.")
        return _skip("resumo", "sem GROQ_API_KEY")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", output_path.stem)[:60]
    tts_path = TEMP_DIR / f"resumo_{stem}.mp3"
    ass_path = TEMP_DIR / f"resumo_{stem}.ass"
    frame_path = TEMP_DIR / f"resumo_{stem}.png"
    intro_path = TEMP_DIR / f"resumo_{stem}_intro.mp4"
    list_path = TEMP_DIR / f"resumo_{stem}_lista.txt"
    joined_path = TEMP_DIR / f"resumo_{stem}_final.mp4"
    temporarios = [tts_path, ass_path, frame_path, intro_path, list_path, joined_path]
    try:
        print("   🎙️  Resumo narrado no início do corte...")
        summary = generate_summary_intro_text(clip_text, api_key, streamer)
        if not summary:
            print("   ℹ️  Sem um resumo bom o bastante para este trecho: o corte sai sem intro.")
            return _skip("resumo", "a IA não deu um resumo fiel à fala do trecho")
        print(f"   📝 Resumo: \"{summary}\"")

        ok, spoken, events = _speak_summary(summary, tts_path, TTS_RATE)
        if ok and spoken > SUMMARY_MAX_SPOKEN_SECONDS:
            ok, spoken, events = _speak_summary(summary, tts_path, SUMMARY_FAST_RATE)
        if not ok or spoken <= 0 or spoken > SUMMARY_MAX_SPOKEN_SECONDS:
            print("   ℹ️  Não consegui uma narração curta o bastante: o corte sai sem intro.")
            return _skip("resumo", "não saiu uma narração curta o bastante")
        intro_seconds = spoken + SUMMARY_PAUSE_AFTER

        main_seconds = get_video_duration(output_path)
        if main_seconds <= 0:
            return _skip("resumo", "não consegui ler a duração do corte")
        if main_seconds + intro_seconds > SUMMARY_MAX_TOTAL_SECONDS:
            print(f"   ℹ️  Intro + corte passariam de {SUMMARY_MAX_TOTAL_SECONDS}s: o corte sai sem intro.")
            return _skip("resumo", "intro + corte passariam de 60 s")
        info = probe_stream_info(output_path)
        if not info["has_audio"]:
            print("   ℹ️  O corte não tem áudio para juntar com a intro: sai sem ela.")
            return _skip("resumo", "o corte não tem áudio")

        ok, _, err = run_cmd([FFMPEG.as_posix(), "-y", "-hide_banner", "-loglevel", "error",
                              "-i", str(output_path), "-frames:v", "1", str(frame_path)], timeout=120)
        if not ok or not frame_path.exists():
            print(f"   ⚠️  Resumo: não consegui pegar o primeiro quadro: {err[-300:]}")
            return _skip("resumo", "falha ao pegar o primeiro quadro")
        if not create_intro_ass(events, ass_path, intro_seconds):
            return _skip("resumo", "falha ao criar a legenda da narração")
        ok, _, err = run_cmd(build_intro_cmd(frame_path, tts_path, ass_path, intro_path, intro_seconds, info), timeout=300)
        if not ok or not intro_path.exists():
            print(f"   ⚠️  Resumo: erro ao montar a intro: {err[-400:]}")
            return _skip("resumo", "erro do ffmpeg ao montar a intro")
        intro_real = get_video_duration(intro_path)
        expected = (intro_real or intro_seconds) + main_seconds

        mode, err = _concat_with_fallback(intro_path, output_path, joined_path, list_path, expected)
        if not mode:
            print(f"   ⚠️  Resumo: a junção falhou ({err[-300:]}). O corte sai sem intro.")
            return _skip("resumo", "a junção da intro falhou")

        os.replace(joined_path, output_path)
        print(f"   ✅ Resumo narrado aplicado ({intro_seconds:.1f}s no início, junção: {mode}).")
        return {"texto": summary, "voz": TTS_VOICE, "streamer": streamer,
                "duracao_s": round(intro_seconds, 2), "juncao": mode}
    except Exception as e:
        print(f"   ⚠️  Resumo no início ignorado por erro: {e}")
        return _skip("resumo", "erro inesperado")
    finally:
        for p in temporarios:
            try:
                p.unlink()
            except OSError:
                pass


def cta_outro_enabled():
    """Chamada final (like, compartilhar, inscrever-se): ligada por padrão. Desliga com CORTES_CTA=0 (ou --sem-cta)."""
    value = os.environ.get("CORTES_CTA")
    if value is None or not value.strip():
        return CTA_DEFAULT
    return value.strip().lower() not in ("0", "false", "nao", "não", "off", "no")


def cta_text():
    """Frase da chamada final: a do .env (CORTES_CTA_TEXT), se razoável, senão a padrão."""
    custom = _clean_summary(os.environ.get("CORTES_CTA_TEXT"))
    if custom and 3 <= len(custom.split()) <= CTA_MAX_WORDS:
        return custom
    return CTA_TEXT


def add_cta_outro(output_path):
    """Acrescenta no FIM de um corte pronto uma chamada narrada (like, compartilhar, inscrever-se).

    O último quadro (de antes do fade final) fica parado, levemente escurecido, com a frase na legenda e na
    voz. Junção sem recodificar (plano B: recodificar). Devolve um dict para o .json ou None; nunca levanta
    erro nem estraga o corte (o arquivo só é trocado com o resultado completo).
    """
    EXTRAS_MOTIVO.pop("cta", None)
    if not cta_outro_enabled():
        return None
    output_path = Path(output_path)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", output_path.stem)[:60]
    tts_path = TEMP_DIR / f"cta_{stem}.mp3"
    ass_path = TEMP_DIR / f"cta_{stem}.ass"
    frame_path = TEMP_DIR / f"cta_{stem}.png"
    outro_path = TEMP_DIR / f"cta_{stem}_outro.mp4"
    list_path = TEMP_DIR / f"cta_{stem}_lista.txt"
    joined_path = TEMP_DIR / f"cta_{stem}_final.mp4"
    temporarios = [tts_path, ass_path, frame_path, outro_path, list_path, joined_path]
    try:
        text = cta_text()
        print("   📣 Chamada final (like, compartilhar, inscrever-se)...")
        ok, spoken, events = _speak_summary(text, tts_path, TTS_RATE)
        if ok and spoken > CTA_MAX_SPOKEN_SECONDS:
            ok, spoken, events = _speak_summary(text, tts_path, SUMMARY_FAST_RATE)
        if not ok or spoken <= 0 or spoken > CTA_MAX_SPOKEN_SECONDS:
            print("   ℹ️  Não consegui a narração da chamada final: o corte sai sem ela.")
            return _skip("cta", "não saiu a narração da chamada")
        outro_seconds = spoken + CTA_PAUSE_AFTER

        main_seconds = get_video_duration(output_path)
        if main_seconds <= 0:
            return _skip("cta", "não consegui ler a duração do corte")
        if main_seconds + outro_seconds > SUMMARY_MAX_TOTAL_SECONDS:
            print(f"   ℹ️  Corte + chamada final passariam de {SUMMARY_MAX_TOTAL_SECONDS}s: sai sem a chamada final.")
            return _skip("cta", "corte + chamada passariam de 60 s")
        info = probe_stream_info(output_path)
        if not info["has_audio"]:
            print("   ℹ️  O corte não tem áudio para juntar com a chamada final: sai sem ela.")
            return _skip("cta", "o corte não tem áudio")

        at = max(0.0, main_seconds - CTA_FRAME_BEFORE_END)
        ok, _, err = run_cmd([FFMPEG.as_posix(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at:.2f}",
                              "-i", str(output_path), "-frames:v", "1", str(frame_path)], timeout=120)
        if not ok or not frame_path.exists():
            print(f"   ⚠️  Chamada final: não consegui pegar o quadro: {err[-300:]}")
            return _skip("cta", "falha ao pegar o quadro")
        if not create_intro_ass(events, ass_path, outro_seconds):
            return _skip("cta", "falha ao criar a legenda da chamada")
        fade_out_at = max(0.0, outro_seconds - CTA_FADE_SECONDS)
        pre = (f"eq=brightness=-0.12,fade=t=in:st=0:d={CTA_FADE_SECONDS},"
               f"fade=t=out:st={fade_out_at:.2f}:d={CTA_FADE_SECONDS}")
        ok, _, err = run_cmd(build_intro_cmd(frame_path, tts_path, ass_path, outro_path, outro_seconds, info, pre),
                             timeout=300)
        if not ok or not outro_path.exists():
            print(f"   ⚠️  Chamada final: erro ao montar o final: {err[-400:]}")
            return _skip("cta", "erro do ffmpeg ao montar o final")
        expected = main_seconds + (get_video_duration(outro_path) or outro_seconds)
        mode, err = _concat_with_fallback(output_path, outro_path, joined_path, list_path, expected)
        if not mode:
            print(f"   ⚠️  Chamada final: a junção falhou ({err[-300:]}). O corte sai sem ela.")
            return _skip("cta", "a junção do final falhou")

        os.replace(joined_path, output_path)
        print(f"   ✅ Chamada final aplicada ({outro_seconds:.1f}s no fim, junção: {mode}).")
        return {"texto": text, "voz": TTS_VOICE, "duracao_s": round(outro_seconds, 2), "juncao": mode}
    except Exception as e:
        print(f"   ⚠️  Chamada final ignorada por erro: {e}")
        return _skip("cta", "erro inesperado")
    finally:
        for p in temporarios:
            try:
                p.unlink()
            except OSError:
                pass


def relatorio_extras(itens=None):
    """Linhas do relatório: quais cortes saíram sem resumo no início / chamada final, e por quê."""
    itens = EXTRAS_RELATORIO if itens is None else itens
    if not itens:
        return []
    linhas = []
    for chave, rotulo, ligado in (("resumo", "resumo no início", "resumo_ligado"),
                                  ("cta", "chamada final", "cta_ligado")):
        if not any(i[ligado] for i in itens):
            linhas.append(f"   {rotulo}: desligado")
            continue
        com = [i["corte"] for i in itens if i[chave]]
        linhas.append(f"   {rotulo}: {len(com)} de {len(itens)} cortes")
        for i in itens:
            if i[ligado] and not i[chave]:
                linhas.append(f"      ⚠️  corte {i['corte']} saiu SEM {rotulo}: {i['motivo_' + chave] or 'motivo não registrado'}")
    return linhas


def imprimir_relatorio_extras():
    linhas = relatorio_extras()
    if linhas:
        print("\n🎙️  Resumo no início e chamada final:")
        print("\n".join(linhas))


STREAMER_NOME = None   # nome do streamer do vídeo atual (preenchido no main; usado no resumo)


def cut_and_export_gameplay(video_path, clip, segments, output_path, clip_num, webcam_pos, groq_api_key=None):
    """Corta vídeo no modo Gameplay/Streamer (layout cam por padrão; fit e clipflow disponíveis), 1080x1920."""
    global LAYOUT_APLICADO
    LAYOUT_APLICADO = None
    webcam_name = "Sem webcam (gameplay only)" if webcam_pos is None else webcam_pos.get('name', 'Posição personalizada')
    print(f"\n🎬 Processando corte GAMEPLAY {clip_num}: {clip['start']:.1f}s - {clip['end']:.1f}s ({clip['duration']:.1f}s)")
    print(f"   Webcam: {webcam_name}")
    if webcam_pos and 'x' in webcam_pos:
        print(f"   Coordenadas: x={webcam_pos['x']:.3f}, y={webcam_pos['y']:.3f}, w={webcam_pos['w']:.3f}, h={webcam_pos['h']:.3f}")

    # O texto do corte é SEMPRE a fala real do intervalo (nunca um texto escrito pela IA).
    clip_words, clip_wps = None, None
    if segments:
        real_text, clip_words, clip_wps = real_text_for_range(segments, clip['start'], clip['end'])
        if not clip.get('sem_checagem_fala') and not has_enough_speech(clip_words, clip_wps):
            print(f"   ⏭️  Corte descartado: fala insuficiente neste trecho "
                  f"({clip_words} palavras, {clip_wps:.2f}/s; mínimo {GAMEPLAY_MIN_CLIP_WORDS} palavras e "
                  f"{GAMEPLAY_MIN_WORDS_PER_SECOND}/s). Sem fala, a legenda e o título não fariam sentido.")
            return False
        clip['text'] = real_text

    clip_start = clip['start']
    clip_duration = clip['duration']
    temp_segment_path = TEMP_DIR / f"segment_gameplay_{clip_num}_{int(clip_start)}s_{int(clip_duration)}s.mp4"

    if not extract_video_segment(video_path, clip_start, clip_duration, temp_segment_path):
        print(f"   ⚠️  Falha no pré-corte, tentando usar vídeo original (pode causar timeout)...")
        input_video_path = video_path
        use_temp_segment = False
    else:
        input_video_path = temp_segment_path
        use_temp_segment = True

    ass_path = TEMP_DIR / f"gameplay_clip_{clip_num}_subs.ass"
    if not create_karaoke_ass_subtitle(segments, clip['start'], clip['end'], clip['duration'], ass_path):
        print("   ⚠️  Falha ao criar legendas karaokê, exportando sem...")
        ass_path = None

    clip_text = clip.get('text', '')
    hook_source = "ia_trecho_real"
    if clip.get('hook_data'):
        hook_data = clip['hook_data']
        print(f"   🏷️  Título da IA (baseado só na fala real do trecho): '{hook_data['titulo_gancho']}'")
    elif groq_api_key:
        print("   🤖 Gerando gancho viral e CTA com IA (a partir da fala real do trecho)...")
        hook_data = generate_hook_and_question(clip_text, clip['duration'], groq_api_key)
        hook_source = "ia_texto_real"
    else:
        print("   ⚠️  Sem GROQ_API_KEY, usando gancho padrão")
        hook_data = {
            "titulo_gancho": "MOMENTO INCRÍVEL NA LIVE",
            "pergunta_cta": "O QUE VOCÊ ACHOU?",
            "nota": 75
        }
        hook_source = "padrao"

    success = process_clipflow_layout(
        input_path=input_video_path,
        output_path=output_path,
        webcam_pos=webcam_pos,
        ass_path=ass_path,
        hook_data=hook_data,
        clip_duration=clip['duration'],
        groq_api_key=groq_api_key,
        clip_text=clip_text,
        audio_mode="original"
    )

    if use_temp_segment and temp_segment_path.exists():
        try:
            temp_segment_path.unlink()
        except:
            pass

    if not success:
        print(f"   ❌ Falha no layout ClipFlow, tentando fallback simplificado...")
        return cut_simple_gameplay(video_path, clip, ass_path, output_path, clip_num, webcam_pos)

    # Resumo narrado no início (vídeo parado + voz). Se falhar por qualquer motivo, o corte fica como está.
    intro_info = add_summary_intro(output_path, clip_text, groq_api_key, STREAMER_NOME)
    # Chamada final narrada (like, compartilhar, inscrever-se) no fim do corte; mesma regra: falhou, fica como está.
    cta_info = add_cta_outro(output_path)
    EXTRAS_RELATORIO.append({
        "corte": clip_num,
        "resumo": intro_info is not None, "resumo_ligado": summary_intro_enabled(),
        "motivo_resumo": EXTRAS_MOTIVO.get("resumo"),
        "cta": cta_info is not None, "cta_ligado": cta_outro_enabled(),
        "motivo_cta": EXTRAS_MOTIVO.get("cta"),
    })

    json_path = output_path.with_suffix('.json')
    metadata = {
        "titulo_gancho": hook_data.get("titulo_gancho", ""),
        "pergunta_cta": hook_data.get("pergunta_cta", ""),
        "nota": hook_data.get("nota", 0),
        "transcricao": clip_text,
        "transcricao_fonte": "transcrição real do intervalo do corte",
        "video_id": HISTORICO.video_key if HISTORICO else None,
        "video_url": HISTORICO.video_url if HISTORICO else None,
        "palavras": clip_words,
        "palavras_por_segundo": round(clip_wps, 2) if clip_wps is not None else None,
        "fonte_titulo": hook_source,
        "score_selecao": clip.get('score'),
        "motivo_selecao": clip.get('reason'),
        "fonte_selecao": clip.get('fonte'),
        "faixa_do_video": clip.get('band'),
        "clip_start": clip['start'],
        "clip_end": clip['end'],
        "clip_duration": clip['duration'],
        "webcam_position": webcam_pos,
        "layout": LAYOUT_APLICADO,
        "layout_salvo": CUSTOM_LAYOUT.get("nome") if (CUSTOM_LAYOUT and LAYOUT_APLICADO == "custom") else None,
        "camera_ampliacao": CAM_AMPLIACAO,
        "streamer": STREAMER_NOME,
        "resumo_intro": intro_info,
        "cta_final": cta_info,
        "duracao_final_s": round(clip['duration'] + (intro_info["duracao_s"] if intro_info else 0)
                                 + (cta_info["duracao_s"] if cta_info else 0), 2),
        "timestamp": datetime.now().isoformat()
    }
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        print(f"   📄 Metadados salvos: {json_path.name}")
    except Exception as e:
        print(f"   ⚠️  Erro ao salvar JSON: {e}")

    print(f"✅ Corte ClipFlow salvo: {output_path}")
    return True


def cut_simple_gameplay(video_path, clip, ass_path, output_path, clip_num, webcam_pos):
    """Versão simplificada do modo gameplay - fallback que SEMPRE gera vertical 1080x1920."""
    if uses_fit_family():
        print("🔄 Tentando versão simplificada (layout fit)...")
        return fit_fallback(video_path, clip['start'], clip['duration'], output_path, ass_path)
    print(f"🔄 Tentando versão simplificada (gameplay - fallback vertical 1080x1920)...")

    ffmpeg_cmd = FFMPEG.as_posix()

    if webcam_pos is None:
        filter_complex = (
            f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}[top];"
            f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}[base]"
        )
    else:
        wc_x = f"iw*{webcam_pos['x']:.6f}"
        wc_y = f"ih*{webcam_pos['y']:.6f}"
        wc_w = f"iw*{webcam_pos['w']:.6f}"
        wc_h = f"ih*{webcam_pos['h']:.6f}"

        filter_complex = (
            f"[0:v]setpts=PTS-STARTPTS,crop={wc_w}:{wc_h}:{wc_x}:{wc_y},"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_WEBAM_HEIGHT}[top];"
            f"[0:v]setpts=PTS-STARTPTS,crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:{CLIPFLOW_GAMEPLAY_HEIGHT}[base]"
        )

    if ass_path and ass_path.exists():
        ass_path_escaped = ass_path.as_posix().replace(":", "\\:")
        filter_complex += f";[base]subtitles='{ass_path_escaped}'[base_sub]"
        base_label = "[base_sub]"
    else:
        base_label = "[base]"

    filter_complex += f";color=c=#D91E18:s={VERTICAL_WIDTH}x{CLIPFLOW_HOOK_BAR_HEIGHT}[hook_bar]"
    filter_complex += f";[top][hook_bar]{base_label}vstack=inputs=3[vout]"

    fade_start = max(0, clip['duration'] - 1.5)
    filter_complex += f";[vout]fade=t=out:st={fade_start:.2f}:d=1.5[vout_fade]"
    map_v = "[vout_fade]"

    af = f"asetpts=PTS-STARTPTS,loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary,afade=t=out:st={fade_start:.2f}:d=1.5"

    cmd = [
        ffmpeg_cmd, "-y",
        "-hide_banner", "-loglevel", "error",
        "-fflags", "+genpts",
        "-ss", str(clip['start']),
        "-t", str(clip['duration']),
        "-i", str(video_path),
        "-filter_complex", filter_complex,
        "-map", map_v,
        "-map", "0:a?",
        "-af", af,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        str(output_path)
    ]

    ok, _, err = run_cmd(cmd, timeout=600)
    if ok:
        print(f"✅ Corte salvo (fallback vertical 1080x1920): {output_path}")
        return True
    else:
        print(f"❌ Falha total no fallback: {err[:500]}")
        return False


def create_ass_subtitle_gameplay(segments, clip_start, clip_end, output_path):
    """Cria arquivo ASS para modo gameplay."""
    if not segments:
        return False

    segments = filter_portuguese_segments(segments)
    if not segments:
        print("   ⚠️  Nenhum segmento em português válido após filtro")
        return False

    clip_segments = []
    for seg in segments:
        if seg['end'] <= clip_start:
            continue
        if seg['start'] >= clip_end:
            break
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

    WORDS_PER_CHUNK = GAMEPLAY_SUBTITLE_WORDS_PER_CHUNK
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

    for i in range(len(chunks) - 1):
        if chunks[i]['end'] > chunks[i + 1]['start']:
            chunks[i]['end'] = chunks[i + 1]['start']

    chunks = [c for c in chunks if c['end'] - c['start'] >= 0.3]

    ass_header = f"""[Script Info]
Title: Cortes Gameplay - Áudio Original
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


def get_video_duration(video_path):
    """Obtém duração do vídeo via ffmpeg."""
    _, out, err = run_cmd([FFMPEG.as_posix(), "-i", str(video_path)])
    match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', f"{out}\n{err}")
    if match:
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)
    return 0


def show_interactive_menu():
    """Exibe menu interativo e retorna (url, audio_mode, repair_path) ou None para sair."""
    while True:
        print("\n" + "=" * 60)
        print("✂️  GERADOR DE CORTES VIRAIS - TikTok / YouTube Shorts")
        print("=" * 60)
        print("Layout: ClipFlow 3 camadas (Webcam 730px + Hook Bar 180px + Gameplay 1010px = 1080x1920)")
        print()
        print("[1] Gerar cortes com áudio original (legendas YouTube sincronizadas)")
        print("[2] Reparar/Reprocessar clipe existente (corrigir sincronia + layout ClipFlow)")
        print("[3] Gerar cortes com narração IA (edge-tts, voz pt-BR-AntonioNeural)")
        print("[4] Modo Gameplay / Streamer (Tela Dividida + Áudio Original)")
        print("[5] Sair")
        print()
        choice = input("Escolha uma opção [1-5]: ").strip()

        if choice == "1":
            url = input("Cole a URL do vídeo do YouTube: ").strip()
            if url:
                return url, "original", None
        elif choice == "2":
            clip_path = input("Caminho do clipe (ex: cortes_prontos/corte_1.mp4): ").strip()
            if clip_path:
                return None, "repair", clip_path
        elif choice == "3":
            url = input("Cole a URL do vídeo do YouTube: ").strip()
            if url:
                return url, "ia", None
        elif choice == "4":
            url = input("Cole a URL do vídeo do YouTube: ").strip()
            if url:
                return url, "gameplay", None
        elif choice == "5":
            return None, "exit", None
        else:
            print("Opção inválida. Tente novamente.")

    return None, "exit", None


def main():
    parser = argparse.ArgumentParser(description="Gerador de cortes virais para TikTok/YouTube Shorts")
    parser.add_argument("url", nargs="?", help="URL do vídeo do YouTube")
    parser.add_argument("--audio", choices=["original", "ia", "gameplay"], default="original",
                        help="Modo de áudio: original (padrão), ia (narração com edge-tts) ou gameplay (tela dividida streamer)")
    parser.add_argument("--webcam", choices=["1", "2", "3", "4", "5", "6", "7"],
                        help="Modo gameplay: posição da webcam (1-7), sem perguntar no terminal")
    parser.add_argument("--camera", metavar="X,Y,LARG,ALT",
                        help="Modo gameplay (layout cam): caixa da câmera do streamer em frações do vídeo (0 a 1), "
                             "ex.: 0.78,0.37,0.21,0.22. Tem prioridade sobre --webcam e a detecção por IA")
    parser.add_argument("--layout-salvo", metavar="NOME",
                        help="Modo gameplay: usa um layout criado no editor da interface (pasta layouts_salvos/)")
    parser.add_argument("--cortes", type=int, metavar="N",
                        help="Modo gameplay: quantidade de cortes (1-10), sem perguntar no terminal")
    parser.add_argument("--repetir", action="store_true",
                        help="Permite repetir trechos que já viraram corte em execuções anteriores deste vídeo")
    parser.add_argument("--importar-existentes", action="store_true",
                        help="Marca como já usados os cortes que estão em cortes_prontos/ (use só se todos vieram deste vídeo)")
    parser.add_argument("--sem-resumo", action="store_true",
                        help="Desliga o resumo narrado no início do corte (vem ligado por padrão no modo gameplay)")
    parser.add_argument("--sem-cta", action="store_true",
                        help="Desliga a chamada final narrada (like, compartilhar, inscrever-se); vem ligada no modo gameplay")
    parser.add_argument("--streamer", metavar="NOME",
                        help="Nome do streamer, usado no resumo narrado (padrão: nome do canal, se não for um canal de cortes)")
    parser.add_argument("--repair", metavar="CLIP_PATH", help="Reparar/Reprocessar clipe existente: corrige sincronia A/V e aplica layout ClipFlow 3 camadas")
    args = parser.parse_args()
    if args.sem_resumo:
        os.environ["CORTES_RESUMO"] = "0"
    if args.sem_cta:
        os.environ["CORTES_CTA"] = "0"
    if args.layout_salvo:
        if not activate_saved_layout(args.layout_salvo):
            print(f"❌ Layout salvo não encontrado ou inválido: {args.layout_salvo!r} (procurei em {LAYOUTS_DIR}{os.sep}).")
            sys.exit(1)
        print(f"🎨 Layout personalizado: {CUSTOM_LAYOUT.get('nome') or CUSTOM_LAYOUT['slug']}")

    if args.repair:
        print("=" * 60)
        print("🔧 MODO REPARO - Reprocessar clipe existente")
        print("   Corrige sincronia A/V + aplica layout ClipFlow 3 camadas (1080x1920)")
        print("=" * 60)

        if not resolve_ffmpeg():
            print(f"❌ ffmpeg não encontrado em {FFMPEG} nem no PATH")
            sys.exit(1)
        ok, _, _ = run_cmd([FFMPEG.as_posix(), "-version"])
        if not ok:
            print(f"❌ ffmpeg não funciona: {FFMPEG}")
            sys.exit(1)

        load_env_file()
        groq_api_key = get_groq_key()
        if groq_api_key:
            print(f"🔑 GROQ_API_KEY carregada (visão: {get_vision_model()} | texto: {get_text_model()})")
        else:
            print("⚠️  GROQ_API_KEY não encontrada no .env: usando gancho padrão")

        ensure_dirs()
        success = repair_clip(args.repair, groq_api_key=groq_api_key)
        sys.exit(0 if success else 1)

    url = args.url
    audio_mode = args.audio

    if not url:
        url, audio_mode, repair_path = show_interactive_menu()

        if audio_mode == "exit":
            print("Saindo...")
            sys.exit(0)

        if audio_mode == "repair":
            print("=" * 60)
            print("🔧 MODO REPARO - Reprocessar clipe existente")
            print("   Corrige sincronia A/V + aplica layout ClipFlow 3 camadas (1080x1920)")
            print("=" * 60)

            if not resolve_ffmpeg():
                print(f"❌ ffmpeg não encontrado em {FFMPEG} nem no PATH")
                sys.exit(1)
            ok, _, _ = run_cmd([FFMPEG.as_posix(), "-version"])
            if not ok:
                print(f"❌ ffmpeg não funciona: {FFMPEG}")
                sys.exit(1)

            load_env_file()
            groq_api_key = get_groq_key()
            if groq_api_key:
                print(f"🔑 GROQ_API_KEY carregada (visão: {get_vision_model()} | texto: {get_text_model()})")
            else:
                print("⚠️  GROQ_API_KEY não encontrada no .env: usando gancho padrão")

            ensure_dirs()
            success = repair_clip(repair_path, groq_api_key=groq_api_key)
            sys.exit(0 if success else 1)

        if not url:
            print("URL não informada. Saindo...")
            sys.exit(1)

    print("=" * 60)
    print("✂️  GERADOR DE CORTES VIRAIS - TikTok / YouTube Shorts")
    audio_mode_display = {
        'original': 'Áudio original',
        'ia': 'Narração IA (edge-tts)',
        'gameplay': 'Gameplay / Streamer (Tela Dividida + Áudio Original)'
    }
    print(f"🔊 Modo de áudio: {audio_mode_display.get(audio_mode, audio_mode)}")
    print("=" * 60)

    if not resolve_ffmpeg():
        print(f"❌ ffmpeg não encontrado em {FFMPEG} nem no PATH")
        print("   Rode 'python verificar_dependencias.py' ou copie o ffmpeg.exe para a pasta 'bin'.")
        sys.exit(1)
    ok, _, _ = run_cmd([FFMPEG.as_posix(), "-version"])
    if not ok:
        print(f"❌ ffmpeg não funciona: {FFMPEG}")
        sys.exit(1)

    ok, _, _ = run_cmd([sys.executable, "-m", "yt_dlp", "--version"])
    if not ok:
        print("❌ yt-dlp não encontrado. Rode: pip install -r requirements.txt")
        sys.exit(1)

    if not find_js_runtime():
        print("⚠️  Nenhum runtime JavaScript (Node.js, Deno ou Bun) encontrado: o download do YouTube pode falhar.")
        print("   Instale o Node.js LTS:  winget install OpenJS.NodeJS.LTS  (e reabra o terminal)")
    else:
        ok, _, _ = run_cmd([sys.executable, "-c", "import yt_dlp_ejs"])
        if not ok:
            print("⚠️  Pacote yt-dlp-ejs não instalado: o download do YouTube pode falhar.")
            print("   Rode: pip install -r requirements.txt")

    if audio_mode == "ia":
        ok, _, _ = run_cmd([sys.executable, "-c", "import edge_tts"])
        if not ok:
            print("❌ edge-tts não encontrado. Rode: pip install edge-tts")
            sys.exit(1)

    load_env_file()
    groq_api_key = get_groq_key()

    if groq_api_key:
        print(f"🔑 GROQ_API_KEY carregada (visão: {get_vision_model()} | texto: {get_text_model()})")
    else:
        print("⚠️  GROQ_API_KEY não encontrada no .env: sem ela não há roteiro por IA (Passo B/C) nem fallback sem legendas")

    ensure_dirs()

    # Histórico: lembra os trechos já cortados deste vídeo para não repeti-los entre execuções
    global HISTORICO
    url_limpa = clean_youtube_url(url)
    HISTORICO = HistoricoCortes(OUTPUT_DIR / HISTORICO_NOME, video_key_from_url(url_limpa), url_limpa,
                                ativo=not args.repetir)
    if args.importar_existentes:
        n = HISTORICO.importar_existentes([OUTPUT_DIR, OUTPUT_DIR / "_descartados"])
        print(f"📚 {n} corte(s) que já estavam na pasta foram marcados como usados deste vídeo.")
    if args.repetir:
        print("📚 Histórico: --repetir ativo, trechos já cortados antes PODEM se repetir.")
    elif HISTORICO.usados():
        print(f"📚 Histórico: {len(HISTORICO.usados())} trecho(s) deste vídeo já foram cortados antes e serão ignorados "
              f"(use --repetir para permitir).")

    prefix = hashlib.md5(url.encode()).hexdigest()[:12]

    video_path, subtitle_path = download_video(url, prefix)
    if not video_path:
        sys.exit(1)

    global STREAMER_NOME
    STREAMER_NOME = None
    if audio_mode == "gameplay" and summary_intro_enabled():
        STREAMER_NOME = resolve_streamer_name(fetch_video_info(url_limpa), args.streamer)
        if STREAMER_NOME:
            print(f"🎙️  Nome do streamer para o resumo: {STREAMER_NOME}")
        else:
            print("🎙️  Nome do streamer não identificado (canal de cortes ou sem dados): o resumo dirá \"o streamer\". "
                  "Informe com --streamer \"NOME\" ou no campo da interface.")

    video_duration = get_video_duration(video_path)
    if video_duration <= 0:
        print("❌ Não foi possível obter duração do vídeo")
        sys.exit(1)
    print(f"📹 Duração do vídeo: {video_duration:.1f}s ({video_duration/60:.1f} min)")

    segments, subtitle_source, extra_data = load_subtitles_with_fallback(subtitle_path, video_path, TEMP_DIR, groq_api_key)

    if subtitle_source == "visual_gameplay":
        print("\n" + "=" * 60)
        print("🎮 MODO VISUAL GAMEPLAY ATIVADO")
        print("   Fluxo: Frames → IA seleciona clímax → TTS → FFmpeg")
        print("=" * 60)

        audio_mode = "ia"
        climax_moments = extra_data.get('climax_moments', [])
        generated_paths = generate_climax_clips_from_visual(
            video_path,
            extra_data.get('frame_paths', []),
            climax_moments,
            OUTPUT_DIR,
            clip_num_start=1
        )

        print("\n🧹 Limpando arquivos temporários...")
        if TEMP_DIR.exists():
            shutil.rmtree(TEMP_DIR)

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

    if audio_mode == "gameplay":
        print("\n" + "=" * 60)
        print("🎮 MODO GAMEPLAY / STREAMER (TELA DIVIDIDA + ÁUDIO ORIGINAL)")
        print("   Fluxo: Transcrição → IA analisa faixas temporais → Seleção de clímax → Split-screen → FFmpeg")
        print("=" * 60)

        audio_mode = "original"

        webcam_choice = args.webcam
        camera_manual = None
        if get_layout() == "custom":
            print("\n🎮 Layout personalizado: a câmera e o gameplay vêm das caixas salvas no editor (pergunta da webcam dispensada).")
            args.camera = None
        if args.camera:
            camera_manual = parse_camera_box(args.camera)
            if camera_manual is None:
                print(f"❌ --camera inválido: {args.camera!r}. Use X,Y,LARGURA,ALTURA entre 0 e 1 (ex.: 0.78,0.37,0.21,0.22).")
                sys.exit(1)
        if camera_manual is not None:
            print(f"\n🎮 Câmera do streamer definida por --camera: x={camera_manual['x']:.3f}, y={camera_manual['y']:.3f}, "
                  f"w={camera_manual['w']:.3f}, h={camera_manual['h']:.3f}")
            webcam_pos = camera_manual
        elif get_layout() == "custom":
            webcam_pos = None
        else:
            if webcam_choice is None and get_layout() == "cam":
                # Layout cam: a câmera vai em cima. Sem --webcam, a IA procura a câmera num quadro do vídeo;
                # sem chave da Groq, pergunta no terminal (ou, sem terminal, cai no layout fit).
                if groq_api_key:
                    webcam_choice = "6"
                elif sys.stdin is not None and sys.stdin.isatty():
                    webcam_choice = None
                else:
                    print("\n🎮 Layout cam: sem --webcam/--camera e sem GROQ_API_KEY para detectar a câmera; usando o layout fit.")
                    webcam_choice = "7"
            if webcam_choice is None and get_layout() == "fit":
                # No layout fit o vídeo aparece inteiro (a webcam já está dentro dele): nada a recortar.
                print("\n🎮 Layout fit: o vídeo aparece inteiro, a webcam não é recortada (pergunta dispensada).")
                webcam_pos = None
            else:
                webcam_pos = ask_webcam_position(video_path, groq_api_key, choice=webcam_choice)
                if webcam_pos is False:
                    print("❌ Posição da webcam inválida ou cancelada. Cancelando.")
                    sys.exit(1)
        if get_layout() == "cam":
            if webcam_pos:
                save_camera_preview(video_path, webcam_pos, OUTPUT_DIR / CAMERA_PREVIEW_NAME)
            else:
                print("   ℹ️  Sem câmera definida: os cortes sairão no layout fit.")

        if video_duration <= 90:
            print(f"\n⚡ Vídeo curto detectado ({video_duration:.1f}s <= 90s): usando vídeo inteiro como único corte")
            num_clips = 1
            clips = [{
                'start': 0.0,
                'end': video_duration,
                'duration': video_duration,
                'text': ' '.join(s['text'] for s in segments) if segments else 'Gameplay curto',
                'score': 100,
                'reason': 'Vídeo curto completo',
                'hook_title': 'MOMENTO ÉPICO COMPLETO',
                'sem_checagem_fala': True
            }]
            print(f"   ✅ Clip único criado: 0s - {video_duration:.1f}s ({video_duration:.1f}s)")
        else:
            if args.cortes is not None:
                num_clips = max(GAMEPLAY_MIN_CLIPS, min(GAMEPLAY_MAX_CLIPS, args.cortes))
                print(f"\n📊 Quantidade de cortes: {num_clips} (definida por --cortes)")
            else:
                print(f"\n📊 Quantidade de cortes (padrão: {GAMEPLAY_DEFAULT_CLIPS}, min: {GAMEPLAY_MIN_CLIPS}, max: {GAMEPLAY_MAX_CLIPS}):")
            while args.cortes is None:
                try:
                    num_clips_input = input(f"   Quantos cortes deseja gerar? [{GAMEPLAY_DEFAULT_CLIPS}]: ").strip()
                    if not num_clips_input:
                        num_clips = GAMEPLAY_DEFAULT_CLIPS
                        break
                    num_clips = int(num_clips_input)
                    if GAMEPLAY_MIN_CLIPS <= num_clips <= GAMEPLAY_MAX_CLIPS:
                        break
                    print(f"   ❌ Digite um número entre {GAMEPLAY_MIN_CLIPS} e {GAMEPLAY_MAX_CLIPS}.")
                except (ValueError, EOFError, KeyboardInterrupt):
                    print("\n   Cancelado ou entrada inválida. Usando padrão.")
                    num_clips = GAMEPLAY_DEFAULT_CLIPS
                    break
            print(f"   ✅ Serão gerados {num_clips} cortes")

            if groq_api_key:
                clips = find_best_clips_gameplay_ai(segments, video_duration, groq_api_key, num_clips)
            else:
                print("   ⚠️  Sem GROQ_API_KEY, usando método tradicional de sliding window...")
                clips = find_best_clips(segments, video_duration, num_clips)

            if not clips:
                print("❌ Não foi possível identificar trechos viáveis.")
                dica_historico_esgotado()
                sys.exit(1)
            if len(clips) < num_clips:
                print(f"⚠️  Só {len(clips)} trecho(s) novo(s) (você pediu {num_clips}).")
                dica_historico_esgotado()

        print(f"\n🎯 {len(clips)} melhores trechos identificados:")
        for i, clip in enumerate(clips, 1):
            print(f"  {i}. {clip['start']:.1f}s - {clip['end']:.1f}s ({clip['duration']:.1f}s) | Score: {clip['score']:.1f}")
            print(f"     Motivo: {clip.get('reason', 'N/A')}")
            print(f"     Texto: {clip['text'][:120]}...")

        print("\n" + "=" * 60)
        print("🎬 INICIANDO EXPORTAÇÃO DOS CORTES (GAMEPLAY - ClipFlow 3 Camadas)")
        print("=" * 60)

        success_count = 0
        for i, clip in enumerate(clips, 1):
            safe_text = clip_file_title(clip)
            output_name = f"corte_gameplay_{i}_{safe_text}.mp4"
            output_path = OUTPUT_DIR / output_name

            if cut_and_export_gameplay(video_path, clip, segments, output_path, i, webcam_pos, groq_api_key):
                success_count += 1
                registrar_corte_no_historico(clip['start'], clip['end'], "gameplay", output_path,
                                             (clip.get('hook_data') or {}).get('titulo_gancho', ''))

        print("\n🧹 Limpando arquivos temporários...")
        if TEMP_DIR.exists():
            shutil.rmtree(TEMP_DIR)

        imprimir_relatorio_extras()

        print("\n" + "=" * 60)
        print(f"✅ CONCLUÍDO (Gameplay): {success_count}/{len(clips)} cortes gerados com sucesso")
        print(f"📁 Pasta de saída: {OUTPUT_DIR.absolute()}")
        print("=" * 60)

        if success_count > 0:
            print("\n📋 Próximos passos:")
            print("  1. Revise os cortes na pasta 'cortes_prontos/'")
            print("  2. Adicione hashtags e descrição para TikTok/Shorts")
            print("  3. Publique nos melhores horários (18-21h costuma funcionar bem)")
        return

    if not segments:
        print("❌ Não foi possível obter transcrição/legendas do vídeo após todos os fallbacks.")
        print("   Dica: use vídeos com legendas automáticas do YouTube ativadas ou configure GROQ_API_KEY no .env")
        sys.exit(1)

    print(f"📝 Legendas carregadas: {len(segments)} segmentos (fonte: {subtitle_source})")

    if subtitle_source == "groq_audio" and audio_mode == "original":
        total_words = sum(len(s['text'].split()) for s in segments)
        if total_words < MIN_WORDS_FOR_AUDIO_TRANSCRIPTION * 2:
            print(f"⚠️  Poucas palavras detectadas ({total_words}). Considere usar --audio ia")

    print(f"\n🧠 Analisando conteúdo para encontrar {NUM_CLIPS} melhores trechos ({TARGET_DURATION_MIN}-{TARGET_DURATION_MAX}s)...")
    clips = find_best_clips(segments, video_duration)

    if not clips:
        print("❌ Não foi possível identificar trechos viáveis.")
        dica_historico_esgotado()
        sys.exit(1)

    print(f"\n🎯 {len(clips)} melhores trechos identificados:")
    for i, clip in enumerate(clips, 1):
        print(f"  {i}. {clip['start']:.1f}s - {clip['end']:.1f}s ({clip['duration']:.1f}s) | Score: {clip['score']}")
        print(f"     Texto: {clip['text'][:120]}...")

    print("\n" + "=" * 60)
    print("🎬 INICIANDO EXPORTAÇÃO DOS CORTES")
    print("=" * 60)

    success_count = 0
    for i, clip in enumerate(clips, 1):
        safe_text = clean_filename(clip['text'][:50])
        suffix = "_ia" if audio_mode == "ia" else ""
        output_name = f"corte_{i}{suffix}_{safe_text}.mp4"
        output_path = OUTPUT_DIR / output_name

        if cut_and_export_vertical(video_path, clip, segments, output_path, i, audio_mode):
            success_count += 1
            registrar_corte_no_historico(clip['start'], clip['end'], audio_mode, output_path)

    print("\n🧹 Limpando arquivos temporários...")
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR)

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