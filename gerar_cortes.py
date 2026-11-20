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
DEFAULT_GROQ_VISION_MODEL = "qwen/qwen3.8-27b"
DEFAULT_GROQ_TEXT_MODEL = "openai/gpt-oss-120b"
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
# ====================================


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
    Retorna lista de tuplas (caminho_do_audio, deslocamento_em_segundos): o deslocamento é
    o início do chunk no vídeo, somado depois aos tempos que a transcrição devolve (que
    sempre começam em 0 dentro de cada arquivo).
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
        ok, _, err = run_cmd(cmd)
        if ok and audio_path.exists():
            audio_files.append((audio_path, 0.0))
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
    """Lê o .env (linhas CHAVE=valor) para os.environ, sem sobrescrever o que já existe.

    Usa utf-8-sig porque o Bloco de Notas do Windows costuma salvar com BOM, o que
    faria a primeira chave (GROQ_API_KEY) não ser reconhecida.
    """
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


def groq_chat(messages, api_key, model, max_tokens=4000, temperature=0.7, timeout=120, retries=3):
    """Chama o chat/completions do Groq e devolve o texto da resposta (ou None se falhar).

    Tenta de novo em 429 (limite de uso) e erros 5xx. O max_tokens é folgado porque
    modelos com raciocínio gastam tokens "pensando" antes da resposta final.
    """
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
                # Alguns modelos devolvem o raciocínio entre <think>...</think>
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
    """Extrai o primeiro JSON (lista ou objeto) de uma resposta, mesmo dentro de ```json ... ```."""
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
    """True se a narração é curta demais para o trecho (o corte sairia com poucos segundos).

    Duração 0 ou negativa significa "não consegui medir": não bloqueia.
    """
    if not tts_duration or tts_duration <= 0:
        return False
    minimum = max(MIN_NARRATION_SECONDS, clip_duration / MAX_VIDEO_SPEEDUP)
    return tts_duration < minimum


def fade_out_filters(total_duration):
    """Devolve (filtro_de_video, filtro_de_audio) de fade-out nos últimos FADE_OUT_SECONDS.

    Vazios se o corte for curto demais para ter fade. A duração é a do ARQUIVO FINAL
    (a do áudio TTS no modo IA, a do trecho no modo original).
    """
    if not total_duration or total_duration < 2 * FADE_OUT_SECONDS + 1:
        return "", ""
    start = total_duration - FADE_OUT_SECONDS
    return (f"fade=t=out:st={start:.2f}:d={FADE_OUT_SECONDS}",
            f"afade=t=out:st={start:.2f}:d={FADE_OUT_SECONDS}")


def fade_out_filters_gameplay(total_duration):
    """Devolve (filtro_de_video, filtro_de_audio) para modo gameplay.

    NOVO 2025: Apenas fade rápido no ÁUDIO (0.2s), SEM fade no vídeo
    para incentivar loop contínuo no TikTok/Shorts/Reels.
    """
    if not total_duration or total_duration < GAMEPLAY_FADE_OUT_SECONDS + 0.5:
        return "", ""
    start = total_duration - GAMEPLAY_FADE_OUT_SECONDS
    return ("", f"afade=t=out:st={start:.2f}:d={GAMEPLAY_FADE_OUT_SECONDS}")


def clean_script(text, max_words, min_words=15):
    """Limpa o roteiro devolvido pela IA e garante tamanho e pontuação final.

    Remove markdown/aspas, junta linhas e, se passar de max_words, corta na última
    frase completa. Retorna None se sobrar menos de min_words (narração curta demais).
    """
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
    """Reescreve o trecho do vídeo como narração viral usando um modelo de texto do Groq.

    A transcrição automática do YouTube não tem pontuação e tem erros de reconhecimento,
    então o modelo recebe isso como "matéria-prima" e devolve frases curtas e pontuadas.
    Retorna None se a IA falhar (quem chama usa generate_viral_script, por regras).
    """
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
    # Uma segunda tentativa: modelos às vezes devolvem só uma frase.
    for _ in range(2):
        answer = groq_chat([{"role": "user", "content": prompt}], api_key, get_text_model())
        script = clean_script(answer, max_words, min_words=min_words)
        if script:
            return script
        print(f"   ⚠️  Roteiro da IA curto demais (mínimo {min_words} palavras); tentando de novo...")
    return None


def _describe_frame_batch(batch, api_key, model):
    """Pede ao modelo de visão a descrição e a nota de cada frame de um lote (até 3 imagens).

    Retorna lista de {'timestamp', 'description', 'score', 'reason'}. O timestamp vem do
    nosso próprio frame (a ordem das imagens), nunca de um número que o modelo invente.
    """
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
    """Escolhe os frames de maior nota, afastados entre si (os cortes não se sobrepõem)."""
    chosen = []
    for frame in sorted(described, key=lambda f: f['score'], reverse=True):
        if frame['score'] < min_score:
            break
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
    answer = groq_chat([{"role": "user", "content": prompt}], api_key, get_text_model())
    return clean_script(answer, max_words, min_words=int(TARGET_DURATION_MIN * SCRIPT_WORDS_PER_SECOND * 0.6))


def analyze_frames_with_ai(frame_paths, api_key):
    """
    Passo C: analisa os frames com IA e escolhe os clímax do vídeo.

    1. Manda os frames em lotes de até MAX_IMAGES_PER_REQUEST imagens (limite do Groq)
       e recebe descrição + nota de cada um.
    2. Escolhe os frames de maior nota (mínimo MIN_CLIMAX_SCORE), afastados entre si.
    3. Escreve o roteiro de cada clímax a partir das descrições reais.

    Retorna lista de {'timestamp', 'reason', 'script'}, ou [] se não der para gerar
    cortes confiáveis. Não existe roteiro "genérico" de reserva: sem IA não há corte.
    """
    if not api_key:
        print("   ❌ Passo C precisa da GROQ_API_KEY (configure no .env).")
        return []
    if not frame_paths:
        return []

    model = get_vision_model()
    batches = [frame_paths[i:i + MAX_IMAGES_PER_REQUEST]
               for i in range(0, len(frame_paths), MAX_IMAGES_PER_REQUEST)]
    print(f"🤖 Analisando {len(frame_paths)} frames com {model} ({len(batches)} lotes de até {MAX_IMAGES_PER_REQUEST})...")

    described = []
    for n, batch in enumerate(batches, 1):
        print(f"   Lote {n}/{len(batches)}...")
        described.extend(_describe_frame_batch(batch, api_key, model))

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

        if narration_too_short(tts_duration, target_duration):
            print(f"   ❌ Narração curta demais ({tts_duration:.1f}s para um trecho de {target_duration:.0f}s); "
                  f"corte {clip_idx} descartado em vez de gerar um vídeo de poucos segundos.")
            continue

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
        v_fade, a_fade = fade_out_filters(final_duration)
        v_fade = f",{v_fade}" if v_fade else ""
        af_chain = "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary" + (f",{a_fade}" if a_fade else "")

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
                f"[vspeed]subtitles='{ass_path.as_posix()}'{v_fade}[vout]"
            )
            map_v = "[vout]"
        else:
            filter_complex = (
                f"{bg_filter};"
                f"[video]setpts={1/speed_ratio:.6f}*PTS{v_fade}[vout]"
            )
            map_v = "[vout]"

        ffmpeg_cmd = FFMPEG.as_posix()
        cmd = [
            ffmpeg_cmd, "-y",
            "-ss", str(clip_start),
            "-t", str(clip_obj['duration']),  # Trecho original; setpts ajusta para a duração do TTS
            "-i", str(video_path),
            "-i", str(tts_audio_path),
            "-filter_complex", filter_complex,
            "-map", map_v,
            "-map", "1:a",
            "-af", af_chain,
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
        ok, _, err = run_cmd(cmd)

        if not ok:
            print(f"   ❌ Erro no FFmpeg: {err[:300]}")
            # Tenta fallback simplificado
            ok = _try_simple_cut(video_path, clip_start, clip_obj['duration'], tts_audio_path, ass_path, output_path, speed_ratio, final_duration)
            if not ok:
                continue

        print(f"   ✅ Corte visual salvo: {output_path}")
        generated_clips.append(str(output_path))

    return generated_clips


def _try_simple_cut(video_path, start_time, duration, tts_audio_path, ass_path, output_path, speed_ratio, final_duration=None):
    """Tenta corte simplificado sem fundo desfocado."""
    print(f"   🔄 Tentando versão simplificada...")

    vf = f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=decrease,pad={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"
    if ass_path and ass_path.exists():
        vf += f",subtitles='{ass_path.as_posix()}'"
    vf = f"setpts={1/speed_ratio:.6f}*PTS," + vf
    v_fade, a_fade = fade_out_filters(final_duration if final_duration else duration / speed_ratio)
    if v_fade:
        vf += f",{v_fade}"
    af_chain = "loudnorm=I=-16:TP=-1.5:LRA=11" + (f",{a_fade}" if a_fade else "")

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
        "-af", af_chain,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path)
    ]

    ok, _, _ = run_cmd(cmd)
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
        sub_files = sorted(temp_dir.glob(f"*subs*{ext}"), key=_subtitle_lang_rank)
        if sub_files:
            segments = load_subtitles(sub_files[0])
            if segments:
                total_words = sum(len(s['text'].split()) for s in segments)
                print(f"   ✅ Passo A OK (fallback local): {len(segments)} segmentos, {total_words} palavras")
                return segments, "youtube", None

    print("   ⚠️  Passo A falhou: Nenhuma legenda do YouTube encontrada")

    # Os passos B e C dependem da API do Groq. Sem chave não há como seguir: o código não
    # inventa roteiro de reserva (cortes sem relação com o vídeo).
    if not api_key:
        print("   ❌ Sem legendas do YouTube e sem GROQ_API_KEY: não dá para transcrever o áudio (Passo B)")
        print("      nem analisar os frames (Passo C). Configure a chave no .env (veja .env.example)")
        print("      ou use um vídeo com legendas automáticas.")
        return [], "failed", None

    # === PASSO B: Transcrição de Áudio via Groq ===
    if api_key:
        print("   Passo B: Tentando transcrição de áudio via Groq Whisper API...")

        # Extrai áudio (com chunking se necessário)
        audio_files = extract_audio_for_transcription(video_path, temp_dir)

        all_segments = []
        for audio_file, offset in audio_files:
            segments = transcribe_audio_groq(audio_file, api_key)
            # A API devolve tempos relativos ao chunk; soma o início do chunk no vídeo
            for seg in segments:
                seg['start'] += offset
                seg['end'] += offset
            all_segments.extend(segments)
        all_segments.sort(key=lambda s: s['start'])

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

    # === PASSO C: Modo Visual Gameplay ===
    print("   Passo C: Modo Visual Gameplay - extraindo frames para análise...")

    frame_paths = extract_key_frames(video_path, temp_dir)
    if not frame_paths:
        print("   ❌ Falha ao extrair frames")
        return [], "failed", None

    # Analisa frames com IA para identificar clímax
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

    # Retorna dados especiais para o fluxo visual_gameplay
    extra_data = {
        'frame_paths': frame_paths,
        'climax_moments': climax_moments
    }
    return [], "visual_gameplay", extra_data


def run_cmd(cmd, cwd=None, timeout: int | None = None):
    """Executa comando (lista de argumentos, sem shell) e retorna (sucesso, stdout, stderr).

    Sem shell=True, a URL e os nomes de arquivo nunca são interpretados pelo cmd.exe
    (evita injeção de comando e quebra com '&', espaços etc.).
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                cwd=cwd, encoding='utf-8', errors='replace', timeout=timeout)
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired as e:
        # Captura stdout/stderr parciais se disponíveis
        stdout = e.stdout.decode('utf-8', errors='replace') if e.stdout else ""
        stderr = e.stderr.decode('utf-8', errors='replace') if e.stderr else str(e)
        return False, stdout.strip(), stderr.strip()
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


def find_js_runtime():
    """Procura um runtime JavaScript que o yt-dlp usa para resolver os desafios do YouTube.

    O yt-dlp precisa de um deles (junto com o pacote yt-dlp-ejs) para baixar do YouTube sem
    erro 403 / formatos faltando. Retorna o nome do primeiro encontrado ou None.
    """
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return name
    return None


def ytdlp_js_args():
    """Argumentos de runtime JavaScript para o yt-dlp, conforme o que está instalado.

    O deno já vem habilitado por padrão no yt-dlp. Node e Bun precisam de --js-runtimes.
    Sem nenhum instalado não passamos nada (apontar para um programa que não existe só
    esconde o problema real, que main() já avisa).
    """
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return [] if name == "deno" else ["--js-runtimes", name]
    return []


def ensure_dirs():
    """Cria diretórios necessários."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)


# Ordem de preferência dos idiomas das legendas baixadas (português antes de inglês).
# Sem isso o glob devolve em ordem alfabética e "en" vem antes de "pt".
SUB_LANG_PRIORITY = ["pt-BR", "pt", "pt-PT", "en"]


def _subtitle_lang_rank(path):
    """Prioridade do idioma pelo nome do arquivo (video_subs.pt-BR.json3 -> 'pt-BR')."""
    lang = Path(path.stem).suffix.lstrip('.')
    if lang in SUB_LANG_PRIORITY:
        return SUB_LANG_PRIORITY.index(lang)
    return len(SUB_LANG_PRIORITY)


def pick_subtitle_file(directory, name_glob):
    """Escolhe o arquivo de legenda: formato json3 > vtt > srt e, dentro do formato, pt antes de en."""
    for ext in (".json3", ".vtt", ".srt"):
        files = sorted(directory.glob(f"{name_glob}{ext}"), key=_subtitle_lang_rank)
        if files:
            return files[0]
    return None


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
        *ytdlp_js_args(),
        "--rm-cache-dir",
        "--extractor-args", "youtube:player_client=android,web",
        "-o", str(video_path),
        url
    ]
    ok, _, err = run_cmd(cmd)
    if not ok or not video_path.exists():
        print(f"⚠️  Falha no download principal, tentando fallback com player_client=web...")
        # Fallback: tentar apenas web client
        cmd_fallback = [
            sys.executable, "-m", "yt_dlp",
            *ffmpeg_loc,
            "--no-playlist",
            "--merge-output-format", "mp4",
            "-f", "bv*[height<=1080]+ba/b[height<=1080]",
            *ytdlp_js_args(),
            "--rm-cache-dir",
            "--extractor-args", "youtube:player_client=web",
            "-o", str(video_path),
            url
        ]
        ok, _, err = run_cmd(cmd_fallback)
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
        *ytdlp_js_args(),
        "--rm-cache-dir",
        "--extractor-args", "youtube:player_client=android,web",
        "-o", str(audio_path),
        url
    ]
    ok, _, err = run_cmd(cmd)
    if not ok:
        print(f"⚠️  Falha ao baixar áudio separado: {err}")

    # Baixar legendas do YouTube (com timestamps) - EXPANDIDO para incluir inglês
    cmd = [
        sys.executable, "-m", "yt_dlp",
        *ffmpeg_loc,
        "--no-playlist",
        "--write-auto-sub",
        "--sub-lang", "pt,pt-BR,pt-PT,en",  # Inclui inglês como fallback
        "--sub-format", "json3",
        "--skip-download",
        *ytdlp_js_args(),
        "--rm-cache-dir",
        "--extractor-args", "youtube:player_client=android,web",
        "-o", str(TEMP_DIR / f"{output_prefix}_subs.%(ext)s"),
        url
    ]
    ok, _, err = run_cmd(cmd)

    # Procurar arquivo de legenda baixado
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


def score_segment(text, start, _end, duration, total_duration):
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


def find_best_clips_gameplay_ai(segments, video_duration, api_key, num_clips=GAMEPLAY_DEFAULT_CLIPS):
    """
    Seleciona os melhores trechos para cortes virais no modo Gameplay usando IA.

    Estratégia:
    1. Divide o vídeo em GAMEPLAY_TIME_BANDS faixas temporais iguais (do início ao fim)
    2. Para cada faixa, extrai a transcrição com timestamps [HH:MM:SS]
    3. Envia cada faixa para a IA (Groq) para identificar os melhores GAMEPLAY_CLIPS_PER_BAND clímax
    4. Coleta todos os candidatos, ordena por score e aplica filtro anti-repetição (min 300s gap)
    5. Retorna os top N clipes

    Retorna lista de dicts com: start, end, duration, text, score, reason
    """
    if not segments or not api_key:
        print("   ⚠️  Sem segmentos ou API key, usando método tradicional...")
        return find_best_clips(segments, video_duration, num_clips)

    print(f"\n🤖 Modo Gameplay IA: Analisando vídeo em {GAMEPLAY_TIME_BANDS} faixas temporais...")

    # Dividir vídeo em faixas temporais
    band_duration = video_duration / GAMEPLAY_TIME_BANDS
    all_candidates = []

    for band_idx in range(GAMEPLAY_TIME_BANDS):
        band_start = band_idx * band_duration
        band_end = min((band_idx + 1) * band_duration, video_duration)

        print(f"   📍 Faixa {band_idx + 1}/{GAMEPLAY_TIME_BANDS}: {band_start/60:.1f}min - {band_end/60:.1f}min")

        # Extrair segmentos desta faixa
        band_segments = []
        for seg in segments:
            if seg['end'] <= band_start:
                continue
            if seg['start'] >= band_end:
                break
            # Calcular sobreposição
            overlap_start = max(seg['start'], band_start)
            overlap_end = min(seg['end'], band_end)
            if overlap_end > overlap_start:
                prop = (overlap_end - overlap_start) / (seg['end'] - seg['start'])
                words = seg['text'].split()
                n_words = max(1, int(len(words) * prop))
                band_segments.append({
                    'start': overlap_start,
                    'end': overlap_end,
                    'text': ' '.join(words[:n_words])
                })

        if not band_segments:
            print(f"      ⚠️  Sem transcrição nesta faixa")
            continue

        # Compactar transcrição da faixa (limite ~3000 chars para não estourar tokens)
        band_text_parts = []
        total_chars = 0
        for seg in band_segments:
            timestamp = f"[{int(seg['start']//3600):02d}:{int((seg['start']%3600)//60):02d}:{int(seg['start']%60):02d}]"
            seg_text = f"{timestamp} {seg['text']}"
            if total_chars + len(seg_text) > 3000:
                break
            band_text_parts.append(seg_text)
            total_chars += len(seg_text)

        band_text = "\n".join(band_text_parts)

        # Chamar IA para analisar esta faixa
        candidates = analyze_band_with_ai(band_text, band_start, band_end, api_key)
        all_candidates.extend(candidates)

    if not all_candidates:
        print("   ⚠️  IA não retornou candidatos, usando método tradicional...")
        return find_best_clips(segments, video_duration, num_clips)

    # Ordenar por score (maior primeiro)
    all_candidates.sort(key=lambda x: x['score'], reverse=True)

    # Filtro anti-repetição: mínimo 300s (5 min) entre cortes
    print(f"\n🔍 Aplicando filtro anti-repetição (mínimo {GAMEPLAY_MIN_GAP_SECONDS}s entre cortes)...")
    selected = []
    for c in all_candidates:
        # Verificar se está muito próximo de algum já selecionado
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

    # Se não conseguiu selecionar o suficiente, relaxa o filtro progressivamente
    if len(selected) < num_clips:
        print(f"   ⚠️  Apenas {len(selected)} cortes com espaçamento de 5min. Relaxando filtro...")
        for gap in [180, 120, 60, 0]:  # 3min, 2min, 1min, sem filtro
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

    # Garantir duração alvo (32-45s para gameplay)
    for c in selected:
        if c['duration'] < GAMEPLAY_CLIP_DURATION_MIN:
            c['end'] = c['start'] + GAMEPLAY_CLIP_DURATION_MIN
            c['duration'] = GAMEPLAY_CLIP_DURATION_MIN
        elif c['duration'] > GAMEPLAY_CLIP_DURATION_MAX:
            c['end'] = c['start'] + GAMEPLAY_CLIP_DURATION_MAX
            c['duration'] = GAMEPLAY_CLIP_DURATION_MAX

    print(f"\n🎯 {len(selected)} cortes finais selecionados:")
    for i, c in enumerate(selected, 1):
        print(f"  {i}. {c['start']:.1f}s - {c['end']:.1f}s ({c['duration']:.1f}s) | Score: {c['score']:.1f}")
        print(f"     Motivo: {c.get('reason', 'N/A')}")
        print(f"     Gancho: {c.get('hook_title', 'N/A')}")
        print(f"     Texto: {c['text'][:120]}...")

    return selected[:num_clips]


def analyze_band_with_ai(band_text, band_start, band_end, api_key):
    """
    Analisa uma faixa temporal com a IA para encontrar clímax.
    Retorna lista de candidatos com start, end, duration, text, score, reason, hook_title.
    """
    band_duration = band_end - band_start

    prompt = (
        f"Você é um editor especialista em Shorts/TikTok/Reels de games. "
        f"Analise a transcrição de uma live/stream de gameplay (duração da faixa: {band_duration/60:.1f}min).\n"
        f"O texto abaixo tem timestamps [HH:MM:SS].\n\n"
        f"TRANSCRIÇÃO DA FAIXA:\n{band_text}\n\n"
        f"Identifique os melhores {GAMEPLAY_CLIPS_PER_BAND} momentos para CORTES VIRAIS de {GAMEPLAY_CLIP_DURATION_MIN} a {GAMEPLAY_CLIP_DURATION_MAX} segundos.\n\n"
        f"🎯 REGRA DE OURO DA RETENÇÃO:\n"
        f"- O corte DEVE COMEÇAR NO CLÍMAX IMEDIATO (primeiros 2s = gancho total)\n"
        f"- REJEITE: introduções lentas, saudações ('salve rapaziada', 'e aí galera', 'olha aí'), "
        f"explicações demoradas, setup de contexto, 'vamos lá', 'então pessoal'\n"
        f"- ACEITE APENAS: reação explosiva, pergunta intrigante aberta, clutch/vitória no exato momento, "
        f"fail épico, descoberta chocante, frase de impacto, risada incontrolável\n"
        f"- O corte DEVE TERMINAR LOGO APÓS O DESFECHO da frase/reação principal (sem silêncio morto)\n\n"
        f"Procure por: reações fortes, risadas, jogadas incríveis, vitórias, descobertas, momentos engraçados, "
        f"fails épicos, clutches, conclusões de desafios, interações com chat de alto impacto.\n\n"
        f"Responda APENAS com JSON válido, lista de objetos:\n"
        f'[\n'
        f'  {{"start_offset": 125.5, "duration": 38, "score": 9.0, '
        f'"reason": "clutch 1v3 no último segundo", "text": "trecho da transcrição", '
        f'"hook_title": "ELE VENCEU O IMPOSSÍVEL"}},\n'
        f'  {{"start_offset": 380.0, "duration": 35, "score": 8.5, '
        f'"reason": "reação de choque ao drop raro", "text": "trecho da transcrição", '
        f'"hook_title": "NÃO ACREDITEI NO QUE VI"}}\n'
        f']\n\n'
        f"Regras OBRIGATÓRIAS:\n"
        f"- start_offset: segundos desde o INÍCIO DA FAIXA (não do vídeo)\n"
        f"- duration: entre {GAMEPLAY_CLIP_DURATION_MIN} e {GAMEPLAY_CLIP_DURATION_MAX} segundos (ideal: 32-45s)\n"
        f"- score: 0 a 10 (potencial viral)\n"
        f"- reason: descrição curta do porquê (máx 50 chars)\n"
        f"- text: trecho relevante da transcrição (máx 200 chars)\n"
        f"- hook_title: TÍTULO DE IMPACTO em CAIXA ALTA, 5-6 palavras máx (ex: 'ELE NÃO ESPERAVA POR ISSO', 'O MELHOR CLUTCH DA HISTÓRIA')\n"
        f"- Se não houver bons momentos com gancho imediato, retorne lista vazia []"
    )

    answer = groq_chat([{"role": "user", "content": prompt}], api_key, get_text_model(), temperature=0.3, max_tokens=2000)

    if not answer:
        return []

    data = extract_json(answer)
    if not isinstance(data, list):
        return []

    candidates = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            start_offset = float(item.get('start_offset', 0))
            duration = float(item.get('duration', 40))
            score = float(item.get('score', 0))
            reason = str(item.get('reason', '')).strip()
            text = str(item.get('text', '')).strip()
            hook_title = str(item.get('hook_title', '')).strip().upper()
        except (TypeError, ValueError):
            continue

        # Validar
        if not (0 <= start_offset <= band_duration):
            continue
        if not (GAMEPLAY_CLIP_DURATION_MIN <= duration <= GAMEPLAY_CLIP_DURATION_MAX):
            duration = max(GAMEPLAY_CLIP_DURATION_MIN, min(GAMEPLAY_CLIP_DURATION_MAX, duration))
        if score < 3.0:  # Score mínimo para considerar
            continue

        abs_start = band_start + start_offset
        abs_end = min(abs_start + duration, band_end)
        actual_duration = abs_end - abs_start

        if actual_duration < GAMEPLAY_CLIP_DURATION_MIN:
            continue

        candidates.append({
            'start': abs_start,
            'end': abs_end,
            'duration': actual_duration,
            'text': text,
            'score': score,
            'reason': reason,
            'hook_title': hook_title
        })

    return candidates


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
        # edge-tts 7.x emite só SentenceBoundary por padrão; as legendas de 3-5 palavras
        # precisam de eventos por palavra, então pedimos WordBoundary explicitamente.
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume,
                                           boundary="WordBoundary")

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
        viral_script = None
        api_key = get_groq_key()
        if api_key:
            print(f"🧠 Reescrevendo o roteiro com IA ({get_text_model()})...")
            viral_script = generate_viral_script_ai(clip['text'], clip['duration'], api_key)
            if not viral_script:
                print("⚠️  IA indisponível, usando o roteiro por regras...")
        if not viral_script:
            viral_script = generate_viral_script(clip['text'], clip['duration'])
        print(f"📝 Roteiro gerado ({len(viral_script.split())} palavras): {viral_script[:150]}...")

        # Gerar áudio TTS com eventos WordBoundary para sincronia exata
        tts_audio_path = TEMP_DIR / f"clip_{clip_num}_tts.mp3"
        success, tts_duration, word_events = asyncio.run(generate_tts_audio(viral_script, tts_audio_path))

        if not success or not tts_audio_path.exists():
            print("⚠️  Falha no TTS, usando áudio original...")
            audio_mode = "original"
        elif narration_too_short(tts_duration, clip['duration']):
            # Sem esta trava o trecho de 30-55s seria espremido em poucos segundos.
            print(f"🔊 Áudio TTS gerado: {tts_duration:.1f}s")
            print(f"⚠️  Narração curta demais para um trecho de {clip['duration']:.0f}s "
                  f"(mínimo {max(MIN_NARRATION_SECONDS, clip['duration'] / MAX_VIDEO_SPEEDUP):.0f}s); "
                  f"usando o áudio original deste corte.")
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
        v_fade, a_fade = fade_out_filters(final_duration)
        v_fade = f",{v_fade}" if v_fade else ""
        af_chain = "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary" + (f",{a_fade}" if a_fade else "")

        if ass_path and ass_path.exists():
            filter_complex = (
                f"{bg_filter};"
                f"[video]setpts={1/speed_ratio:.6f}*PTS[vspeed];"
                f"[vspeed]subtitles='{ass_path.as_posix()}'{v_fade}[vout]"
            )
            map_v = "[vout]"
        else:
            filter_complex = (
                f"{bg_filter};"
                f"[video]setpts={1/speed_ratio:.6f}*PTS{v_fade}[vout]"
            )
            map_v = "[vout]"

        ffmpeg_cmd = FFMPEG.as_posix()
        # IMPORTANTE: o -t da ENTRADA é a duração do trecho original (clip['duration']).
        # O setpts (clip/final) é que encolhe/estica esse trecho para durar final_duration,
        # a duração real do áudio TTS. Usar final_duration aqui aplicaria a escala duas vezes
        # e o vídeo (com -shortest) cortaria a narração.
        cmd = [
            ffmpeg_cmd, "-y",
            "-ss", str(video_trim_start),
            "-t", str(clip['duration']),  # Trecho original; setpts ajusta para a duração do TTS
            "-i", str(video_path),
            "-i", str(tts_audio_path),
            "-filter_complex", filter_complex,
            "-map", map_v,
            "-map", "1:a",
            "-af", af_chain,
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
        v_fade, a_fade = fade_out_filters(clip['duration'])
        v_fade = f",{v_fade}" if v_fade else ""
        if ass_path and ass_path.exists():
            filter_complex = (
                f"{bg_filter};"
                f"[video]subtitles='{ass_path.as_posix()}'{v_fade}[vout]"
            )
            map_v = "[vout]"
        elif v_fade:
            filter_complex = f"{bg_filter};[video]null{v_fade}[vout]"
            map_v = "[vout]"
        else:
            filter_complex = f"{bg_filter}"
            map_v = "[video]"

        af_filter = "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary" + (f",{a_fade}" if a_fade else "")

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
    ok, _, err = run_cmd(cmd)

    if not ok:
        print(f"❌ Erro no ffmpeg: {err[:500]}")
        return cut_simple_vertical(video_path, clip, segments, ass_path, output_path, clip_num, audio_mode, tts_audio_path, final_duration)

    print(f"✅ Corte salvo: {output_path}")
    return True


def cut_simple_vertical(video_path, clip, _segments, ass_path, output_path, _clip_num, audio_mode="original", tts_audio_path=None, tts_duration=None):
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
        v_fade, a_fade = fade_out_filters(actual_tts_duration)
        if v_fade:
            vf += f",{v_fade}"
        af_chain = "loudnorm=I=-16:TP=-1.5:LRA=11" + (f",{a_fade}" if a_fade else "")
        cmd = [
            ffmpeg_cmd, "-y",
            "-ss", str(clip['start']),
            "-t", str(clip['duration']),  # Trecho original; o setpts do vf ajusta para a duração do TTS
            "-i", str(video_path),
            "-i", str(tts_audio_path),
            "-vf", vf,
            "-map", "0:v",
            "-map", "1:a",
            "-af", af_chain,
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
        v_fade, a_fade = fade_out_filters(clip['duration'])
        if v_fade:
            vf += f",{v_fade}"
        cmd = [
            ffmpeg_cmd, "-y",
            "-ss", str(clip['start']),
            "-t", str(clip['duration']),
            "-i", str(video_path),
            "-vf", vf,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11" + (f",{a_fade}" if a_fade else ""),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path)
        ]

    ok, _, err = run_cmd(cmd)
    if ok:
        print(f"✅ Corte salvo (simplificado): {output_path}")
        return True
    else:
        print(f"❌ Falha total: {err[:500]}")
        return False


def ask_webcam_position(video_path=None, api_key=None):
    """
    Pergunta ao usuário onde fica a facecam do streamer no vídeo 16:9 original.
    Retorna um dicionário com as coordenadas do crop para a webcam (x, y, w, h normalizados 0.0-1.0),
    ou None para opção 7 (sem webcam).

    Opções:
    [1] Canto inferior esquerdo (padrão)
    [2] Canto superior esquerdo
    [3] Canto inferior direito
    [4] Canto superior direito
    [5] Meio-direita / acima do minimapa (x=0.772, y=0.450, w=0.222, h=0.242)
    [6] Detectar automaticamente com IA (usa Groq Vision)
    [7] Sem webcam (apenas zoom central 9:16 no gameplay)
    """
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
                break
            print("   ❌ Opção inválida. Digite 1, 2, 3, 4, 5, 6 ou 7.")
        except (EOFError, KeyboardInterrupt):
            print("\n   Cancelado pelo usuário.")
            return None

    if choice == '7':
        print("   ✅ Modo: Apenas gameplay (crop central 9:16)")
        return None

    # Posições predefinidas (coordenadas normalizadas 0.0-1.0: x, y, w, h)
    # x, y = canto superior esquerdo do crop da webcam
    # w, h = largura e altura do crop da webcam
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

    # Opção 6: Detecção automática com IA
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
    """
    Extrai 1 frame do vídeo (no tempo 10% da duração) e usa Groq Vision
    para detectar a posição da webcam do streamer.

    Retorna dict com x, y, w, h normalizados (0.0-1.0) ou None se não encontrar.
    """
    import tempfile

    try:
        # Obter duração do vídeo
        video_duration = get_video_duration(video_path)
        if video_duration <= 0:
            return None

        # Extrair frame em 10% da duração (evita telas de loading/início)
        timestamp = video_duration * 0.1

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
            return None

        result = extract_json(answer)
        if not result or not isinstance(result, dict):
            return None

        if not result.get('found', False):
            return None

        # Validar coordenadas
        x = float(result.get('x', 0))
        y = float(result.get('y', 0))
        w = float(result.get('w', 0))
        h = float(result.get('h', 0))

        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
            return None

        # Garantir que o crop não saia dos limites
        if x + w > 1.0 or y + h > 1.0:
            return None

        return {'x': x, 'y': y, 'w': w, 'h': h, 'name': 'Detecção automática (IA)'}

    except Exception as e:
        print(f"   ⚠️  Erro na detecção automática: {e}")
        return None


def cut_and_export_gameplay(video_path, clip, segments, output_path, clip_num, webcam_pos):
    """
    Corta vídeo no modo Gameplay/Streamer (tela dividida) com otimizações de retenção.

    - Se webcam_pos não for None: cria split-screen com vstack
      * Topo: webcam recortada (crop com coordenadas normalizadas) → scale 1080x620
      * Base: gameplay central (crop 9:16) → scale 1080x1300
      * Juntados com vstack = 1080x1920
    - Se webcam_pos for None: crop central 9:16 + scale 1080x1920

    Otimizações NOVO 2025:
    - Gancho visual: faixa superior com título de impacto (drawtext) nos primeiros 4s
    - Corte seco para loop: fade-out rápido 0.2s apenas áudio (sem fade vídeo)
    - Legendas: 2-3 palavras, centro, negrito, contorno preto 6px
    - Watermark: logo PNG discreto no canto (se assets/logo.png existir)

    Áudio: original com loudnorm + fade-out rápido 0.2s (apenas áudio)
    Legendas: .ass centralizada (Alignment=5), MarginV=1100

    webcam_pos pode ser:
    - None: sem webcam
    - dict com chaves: x, y, w, h (normalizados 0.0-1.0), name (opcional)

    clip pode conter: hook_title (título de impacto para banner superior)
    """
    webcam_name = "Sem webcam (gameplay only)" if webcam_pos is None else webcam_pos.get('name', 'Posição personalizada')
    hook_title = clip.get('hook_title', '').strip()
    print(f"\n🎬 Processando corte GAMEPLAY {clip_num}: {clip['start']:.1f}s - {clip['end']:.1f}s ({clip['duration']:.1f}s)")
    print(f"   Webcam: {webcam_name}")
    if hook_title:
        print(f"   🎯 Gancho: {hook_title}")
    if webcam_pos and 'x' in webcam_pos:
        print(f"   Coordenadas: x={webcam_pos['x']:.3f}, y={webcam_pos['y']:.3f}, w={webcam_pos['w']:.3f}, h={webcam_pos['h']:.3f}")

    # Criar legendas .ass sincronizadas com as falas
    ass_path = TEMP_DIR / f"gameplay_clip_{clip_num}_subs.ass"
    if not create_ass_subtitle_gameplay(segments, clip['start'], clip['end'], ass_path):
        print("⚠️  Falha ao criar legendas, exportando sem...")
        ass_path = None

    # Construir filtro de vídeo para o modo gameplay
    # NOVO: Fade rápido apenas no áudio (0.2s), sem fade no vídeo para incentivar loop
    v_fade, a_fade = fade_out_filters_gameplay(clip['duration'])
    v_fade = f",{v_fade}" if v_fade else ""
    af_chain = "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary" + (f",{a_fade}" if a_fade else "")

    ffmpeg_cmd = FFMPEG.as_posix()

    # Verificar se existe logo/watermark
    watermark_path = Path(GAMEPLAY_WATERMARK_PATH)
    has_watermark = watermark_path.exists()
    if has_watermark:
        print(f"   🏷️  Watermark: {watermark_path}")

    if webcam_pos is None:
        # Modo sem webcam: apenas gameplay - crop central 9:16
        vf = (
            f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}"
        )

        # Adicionar gancho visual (banner superior) se houver hook_title
        if hook_title:
            # drawtext: faixa amarela vibrante no topo, texto preto negrito centralizado
            # y=140 (terço superior), altura ~120px, duração 4s
            escaped_title = hook_title.replace("'", "\\'").replace(":", "\\:")
            vf += (f",drawtext=text='{escaped_title}':"
                   f"fontfile='C\\:/Windows/Fonts/arialbd.ttf':"
                   f"fontsize=52:fontcolor=black:fontcolor_expr=black:"
                   f"box=1:boxcolor=yellow@0.95:boxborderw=20:"
                   f"x=(w-text_w)/2:y={GAMEPLAY_HOOK_BANNER_Y}:"
                   f"enable='lt(t,{GAMEPLAY_HOOK_BANNER_DURATION})'")

        # Adicionar watermark se existir
        if has_watermark:
            vf += f",movie='{watermark_path.as_posix()}'[wm];[in][wm]overlay=20:20[out]"

        if ass_path and ass_path.exists():
            vf += f",subtitles='{ass_path.as_posix()}'"
        vf += v_fade

        cmd = [
            ffmpeg_cmd, "-y",
            "-ss", str(clip['start']),
            "-t", str(clip['duration']),
            "-i", str(video_path),
            "-vf", vf,
            "-af", af_chain,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path)
        ]
    else:
        # Tela dividida com webcam (usando coordenadas normalizadas do dict)
        wc_x = f"iw*{webcam_pos['x']:.6f}"
        wc_y = f"ih*{webcam_pos['y']:.6f}"
        wc_w = f"iw*{webcam_pos['w']:.6f}"
        wc_h = f"ih*{webcam_pos['h']:.6f}"

        # Filter complex para tela dividida
        # [0:v]crop=wc_w:wc_h:wc_x:wc_y,scale=1080:620:force_original_aspect_ratio=increase,crop=1080:620[cam]
        # [0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1300:force_original_aspect_ratio=increase,crop=1080:1300[game]
        # [cam][game]vstack=inputs=2[vout]
        filter_complex = (
            f"[0:v]crop={wc_w}:{wc_h}:{wc_x}:{wc_y},"
            f"scale={VERTICAL_WIDTH}:620:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:620[cam];"
            f"[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
            f"scale={VERTICAL_WIDTH}:1300:force_original_aspect_ratio=increase,"
            f"crop={VERTICAL_WIDTH}:1300[game];"
            f"[cam][game]vstack=inputs=2[vout]"
        )

        # Adicionar gancho visual (banner superior) se houver hook_title
        if hook_title:
            escaped_title = hook_title.replace("'", "\\'").replace(":", "\\:")
            filter_complex += (
                f";[vout]drawtext=text='{escaped_title}':"
                f"fontfile='C\\:/Windows/Fonts/arialbd.ttf':"
                f"fontsize=52:fontcolor=black:"
                f"box=1:boxcolor=yellow@0.95:boxborderw=20:"
                f"x=(w-text_w)/2:y={GAMEPLAY_HOOK_BANNER_Y}:"
                f"enable='lt(t,{GAMEPLAY_HOOK_BANNER_DURATION})'[vhook]"
            )
            map_v = "[vhook]"
        else:
            filter_complex += f";[vout]null[vhook]"
            map_v = "[vhook]"

        # Adicionar watermark se existir
        if has_watermark:
            filter_complex += f";movie='{watermark_path.as_posix()}'[wm];[vhook][wm]overlay=20:20[vfinal]"
            map_v = "[vfinal]"

        # Adicionar legendas
        if ass_path and ass_path.exists():
            filter_complex += f";[vfinal]subtitles='{ass_path.as_posix()}'{v_fade}[vfinal2]"
            map_v = "[vfinal2]"

        cmd = [
            ffmpeg_cmd, "-y",
            "-ss", str(clip['start']),
            "-t", str(clip['duration']),
            "-i", str(video_path),
            "-filter_complex", filter_complex,
            "-map", map_v,
            "-map", "0:a?",
            "-af", af_chain,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path)
        ]

    print(f"⚙️  Executando ffmpeg (modo gameplay otimizado)...")
    ok, _, err = run_cmd(cmd)

    if not ok:
        print(f"❌ Erro no ffmpeg: {err[:500]}")
        # Fallback simplificado
        return cut_simple_gameplay(video_path, clip, ass_path, output_path, clip_num, webcam_pos)

    print(f"✅ Corte gameplay salvo: {output_path}")
    return True


def cut_simple_gameplay(video_path, clip, ass_path, output_path, clip_num, webcam_pos):
    """Versão simplificada do modo gameplay sem filter_complex (fallback)."""
    print(f"🔄 Tentando versão simplificada (gameplay)...")

    if webcam_pos is None:
        # Apenas gameplay central
        vf = f"crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}"
    else:
        # Simplificado: usa scale+pad com blur (não é split-screen real, mas funciona)
        vf = f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:force_original_aspect_ratio=decrease,pad={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"

    if ass_path and ass_path.exists():
        vf += f",subtitles='{ass_path.as_posix()}'"

    v_fade, a_fade = fade_out_filters(clip['duration'])
    if v_fade:
        vf += f",{v_fade}"

    af_chain = "loudnorm=I=-16:TP=-1.5:LRA=11" + (f",{a_fade}" if a_fade else "")

    ffmpeg_cmd = FFMPEG.as_posix()
    cmd = [
        ffmpeg_cmd, "-y",
        "-ss", str(clip['start']),
        "-t", str(clip['duration']),
        "-i", str(video_path),
        "-vf", vf,
        "-af", af_chain,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path)
    ]

    ok, _, err = run_cmd(cmd)
    if ok:
        print(f"✅ Corte salvo (simplificado): {output_path}")
        return True
    else:
        print(f"❌ Falha total: {err[:500]}")
        return False


def create_ass_subtitle_gameplay(segments, clip_start, clip_end, output_path):
    """
    Cria arquivo ASS para modo gameplay.
    Legendas posicionadas na linha divisória entre webcam e gameplay: MarginV=1240
    (620px topo + 620px até o meio da base = 1240px do topo em 1080x1920)
    Fonte negrito, borda preta, destaque de cor.
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

    # Agrupar em blocos curtos de 2-3 palavras (ritmo dinâmico TikTok/Shorts)
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

    # Zero sobreposição
    for i in range(len(chunks) - 1):
        if chunks[i]['end'] > chunks[i + 1]['start']:
            chunks[i]['end'] = chunks[i + 1]['start']

    chunks = [c for c in chunks if c['end'] - c['start'] >= 0.3]

    # Estilo ASS para gameplay: Legendas centralizadas (Alignment=5), 2-3 palavras por bloco
    # Fonte grande, negrito, contorno preto forte (5px), cor amarela vibrante
    # Position: centro da tela (Alignment=5 = middle-center), MarginV ajustado para ficar na divisória
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
        # Alternar entre estilo Default (amarelo) e Highlight (verde limão) para ênfase visual
        style = "Default"  # Simplificado: usar apenas Default para consistência
        events.append(f"Dialogue: 0,{start_str},{end_str},{style},,0,0,0,,{text}")

    ass_content = ass_header + "\n".join(events)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass_content)

    return True


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
    parser.add_argument("--audio", choices=["original", "ia", "gameplay"], default="original",
                        help="Modo de áudio: original (padrão), ia (narração com edge-tts) ou gameplay (tela dividida streamer)")
    args = parser.parse_args()

    url = args.url
    audio_mode = args.audio

    print("=" * 60)
    print("✂️  GERADOR DE CORTES VIRAIS - TikTok / YouTube Shorts")
    audio_mode_display = {
        'original': 'Áudio original',
        'ia': 'Narração IA (edge-tts)',
        'gameplay': 'Gameplay / Streamer (Tela Dividida + Áudio Original)'
    }
    print(f"🔊 Modo de áudio: {audio_mode_display.get(audio_mode, audio_mode)}")
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

    # O YouTube exige um runtime JavaScript + o pacote yt-dlp-ejs no yt-dlp (senão: 403/formatos faltando)
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

    # Carregar .env (GROQ_API_KEY e, opcionalmente, GROQ_VISION_MODEL / GROQ_TEXT_MODEL)
    load_env_file()
    groq_api_key = get_groq_key()

    if groq_api_key:
        print(f"🔑 GROQ_API_KEY carregada (visão: {get_vision_model()} | texto: {get_text_model()})")
    else:
        print("⚠️  GROQ_API_KEY não encontrada no .env: sem ela não há roteiro por IA (Passo B/C) nem fallback sem legendas")

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

    # === FLUXO ESPECIAL: MODO GAMEPLAY / STREAMER (TELA DIVIDIDA) ===
    if audio_mode == "gameplay":
        print("\n" + "=" * 60)
        print("🎮 MODO GAMEPLAY / STREAMER (TELA DIVIDIDA + ÁUDIO ORIGINAL)")
        print("   Fluxo: Transcrição → IA analisa faixas temporais → Seleção de clímax → Split-screen → FFmpeg")
        print("=" * 60)

        # Perguntar quantos cortes gerar
        print(f"\n📊 Quantidade de cortes (padrão: {GAMEPLAY_DEFAULT_CLIPS}, min: {GAMEPLAY_MIN_CLIPS}, max: {GAMEPLAY_MAX_CLIPS}):")
        while True:
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

        # Perguntar posição da webcam (passa video_path e api_key para detecção automática)
        webcam_pos = ask_webcam_position(video_path, groq_api_key)
        if webcam_pos is None:
            print("❌ Opção inválida. Cancelando.")
            sys.exit(1)

        # Forçar áudio original no modo gameplay
        audio_mode = "original"

        # Encontrar melhores clips usando IA distribuída pelo vídeo
        if groq_api_key:
            clips = find_best_clips_gameplay_ai(segments, video_duration, groq_api_key, num_clips)
        else:
            print("   ⚠️  Sem GROQ_API_KEY, usando método tradicional de sliding window...")
            clips = find_best_clips(segments, video_duration, num_clips)

        if not clips:
            print("❌ Não foi possível identificar trechos viáveis.")
            sys.exit(1)

        print(f"\n🎯 {len(clips)} melhores trechos identificados:")
        for i, clip in enumerate(clips, 1):
            print(f"  {i}. {clip['start']:.1f}s - {clip['end']:.1f}s ({clip['duration']:.1f}s) | Score: {clip['score']:.1f}")
            print(f"     Motivo: {clip.get('reason', 'N/A')}")
            print(f"     Texto: {clip['text'][:120]}...")

        # Processar cada corte no modo gameplay
        print("\n" + "=" * 60)
        print("🎬 INICIANDO EXPORTAÇÃO DOS CORTES (GAMEPLAY)")
        print("=" * 60)

        success_count = 0
        for i, clip in enumerate(clips, 1):
            safe_text = clean_filename(clip['text'][:50])
            output_name = f"corte_gameplay_{i}_{safe_text}.mp4"
            output_path = OUTPUT_DIR / output_name

            if cut_and_export_gameplay(video_path, clip, segments, output_path, i, webcam_pos):
                success_count += 1

        # Limpeza
        print("\n🧹 Limpando arquivos temporários...")
        import shutil
        if TEMP_DIR.exists():
            shutil.rmtree(TEMP_DIR)

        # Resumo final
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