# Fábrica de Cortes - Regras do Projeto

## Ambiente e Execução
- **SO:** Windows 10/11 | **Python:** 3.15 (não usar bibliotecas incompatíveis como `faster-whisper`).
- **Dependências:** Sempre chamar `yt-dlp` como módulo (`sys.executable, "-m", "yt_dlp"`) e usar o FFmpeg local em `./bin/ffmpeg.exe`.
- **Python do projeto:** Usar sempre o Python do ambiente virtual (`.venv\Scripts\python.exe`) no `iniciar.bat`.
- **API:** Credenciais ficam no arquivo `.env` na raiz (nunca expor chaves no código).
  - `GROQ_API_KEY`: Chave da API Groq (transcrição Whisper, análise de frames e roteiros por IA). Copie `.env.example` para `.env`.
  - `GROQ_VISION_MODEL` / `GROQ_TEXT_MODEL` (opcionais): trocam os modelos padrão sem editar o código.

## Arquitetura dos Arquivos
- `iniciar.bat`: Menu principal em Batch (manter codificação ASCII sem acentos nos `echo` para não quebrar no CMD). Usa `.venv\Scripts\python.exe` e oferece 3 opções: buscar tendências, gerar cortes com áudio original, gerar cortes com narração IA.
- `buscar_tendencias.py`: Busca vídeos em alta no YouTube via `yt-dlp`.
- `gerar_cortes.py`: Baixa legendas automáticas do YouTube (`.vtt`/`.json3`), seleciona os melhores trechos via IA e renderiza cortes verticais `1080x1920` na pasta `cortes_prontos/`.
  - **Proteção contra erros 403/JS runtime:** Todos os comandos `yt-dlp` usam `--js-runtimes node --rm-cache-dir --extractor-args "youtube:player_client=android,web"` com fallback automático para `player_client=web` se o primeiro falhar.
  - **Modo `--audio original`**: Mantém áudio original do vídeo, usa legendas do YouTube sincronizadas com o corte.
  - **Modo `--audio ia`**: Gera narração com `edge-tts` (voz `pt-BR-AntonioNeural`), cria roteiro viral resumido com gancho forte, remove áudio original (`-an`), ajusta duração do vídeo ao MP3 gerado (acelera/desacelera vídeo via `setpts`) e sincroniza legendas `.ass` com a narração.
    - **Bug fixes aplicados (2025):**
      1. **Legenda pulando/duplicada**: Modo IA usa EXCLUSIVAMENTE eventos `WordBoundary` do `edge_tts.Communicate.stream()` — NUNCA usa `.vtt` do YouTube. Estilo `.ass` único (`Alignment=2`, `MarginV=320`), zero sobreposição, sem alternância de estilos (sem karaoke).
      2. **Voz sem respiro**: `generate_viral_script()` garante pontuação natural rigorosa (vírgulas, pontos, exclamações a cada frase curta) e NÃO remove pontuação antes de enviar ao `edge-tts`.
      3. **Sincronia exata áudio/vídeo**: o `-t` da ENTRADA do vídeo é a duração do trecho original (`clip['duration']`), e o `setpts` (`final_duration / clip['duration']`, ou seja `1/speed_ratio`) é quem ajusta o trecho para durar a duração REAL do MP3 (`final_duration`). NUNCA usar `final_duration` no `-t` de entrada: a escala seria aplicada duas vezes e o `-shortest` cortaria a narração.

## Sistema de Fallback de Legendas (3 Passos) — NOVO 2025
Para resolver "Legendas: NÃO ENCONTRADAS" em vídeos longos de gameplay:

### Passo A — Legendas do YouTube (Prioridade Máxima)
- **Limpeza de URL:** `clean_youtube_url()` remove parâmetros de tracking, playlist, timestamp, shorts, mantendo apenas `https://www.youtube.com/watch?v=<ID>`.
- **Busca expandida:** `--sub-lang "pt,pt-BR,pt-PT,en"` — inclui inglês como fallback para vídeos internacionais.
- **Formatos:** `.json3` (preferido), `.vtt`, `.srt` — parse unificado via `load_subtitles()`.

### Passo B — Transcrição de Áudio via Groq Whisper API
- **Trigger:** Falha no Passo A OU legendas do YouTube com < 30 palavras (provável gameplay sem narração).
- **Extração de áudio:** `extract_audio_for_transcription()` usa `./bin/ffmpeg.exe`:
  - MP3 mono 64kbps, 16kHz (otimizado para Whisper)
  - **Chunking automático:** Vídeos > 20 min divididos em blocos de 15 min
- **API:** Groq `whisper-large-v3-turbo` via `GROQ_API_KEY` do `.env`
- **Retorno:** Segmentos com timestamps no formato padrão do projeto
- **Validação:** Se total de palavras < 30 → passa para Passo C

### Passo C — Modo Visual Gameplay (Último Recurso)
- **Trigger:** Passo B falhou OU áudio tem < 30 palavras
- **Extração de frames:** `extract_key_frames()` via `./bin/ffmpeg.exe`:
  - 1 frame a cada 30s (ajustado para máx. 20 frames)
  - Resolução 1280x720, qualidade alta
- **Análise visual:** `analyze_frames_with_ai()` manda os frames ao Groq em **lotes de no máximo 3 imagens** (limite do modelo de visão; nunca enviar mais por chamada). Cada frame volta com descrição + nota 0-10; o timestamp vem do NOSSO frame, nunca de número devolvido pelo modelo.
- **Escolha dos clímax:** os frames de maior nota (mínimo `MIN_CLIMAX_SCORE`=5), afastados pelo menos `TARGET_DURATION_MAX` segundos entre si.
- **Geração de roteiro:** `_write_script_for_moment()` escreve a narração SÓ com as descrições reais dos frames (sem inventar fatos).
- **Sem chave ou sem clímax confiável = sem corte.** NUNCA criar roteiro genérico/falso de reserva nem cortar em posições fixas (o antigo `_fallback_visual_analysis` foi removido de propósito). Sem `GROQ_API_KEY` o programa para antes de extrair frames.
- **Modo forçado:** Automático `--audio ia` (narração 100% IA, sem áudio original)
- **Modelos:** nomes ficam no `.env` (`GROQ_VISION_MODEL`, `GROQ_TEXT_MODEL`) porque o Groq descontinua modelos a cada poucos meses; os padrões estão em `DEFAULT_GROQ_VISION_MODEL` / `DEFAULT_GROQ_TEXT_MODEL`. Se a API devolver 400/404 de modelo, confira https://console.groq.com/docs/deprecations.

## Roteiro do modo `--audio ia`
- `generate_viral_script_ai()` reescreve o trecho com um modelo de texto do Groq (a transcrição automática do YouTube não tem pontuação). Se não houver chave, ou a IA falhar/devolver pouco texto, cai para `generate_viral_script()` (regras fixas).

## Geração de Áudio com edge-tts (Regra Obrigatória)
**edge-tts 7.x**: O método `.stream()` de `edge_tts.Communicate` **só pode ser iterado uma única vez** — não se pode chamar `.stream()` e depois `.save()` na mesma instância.

**WordBoundary é obrigatório**: no edge-tts 7.x o padrão é `SentenceBoundary` (um evento por frase). Crie sempre `edge_tts.Communicate(text, voice, ..., boundary="WordBoundary")` para ter um evento por palavra e legendas de 3-5 palavras.

**Idioma das legendas**: ao escolher entre vários arquivos baixados, use `pick_subtitle_file()` (português antes de inglês). Nunca `glob(...)[0]`: a ordem alfabética põe `en` antes de `pt`.

**Padrão correto**: Use um único loop `async for chunk in communicate.stream():` que simultaneamente:
1. Escreve `chunk["data"]` (bytes de áudio) diretamente no arquivo `.mp3` via `open(output_mp3, "wb")`
2. Captura eventos `chunk["type"] in ("WordBoundary", "SentenceBoundary")` para timestamps exatos das legendas `.ass`

Exemplo:
```python
with open(output_path, "wb") as f:
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            f.write(chunk["data"])
        elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
            word_boundary_events.append({...})
```
(O `open()` nativo não suporta `async with`; usa-se `with` sincronizado dentro do loop assíncrono)

## Padrões Obrigatórios de Legenda (.ass)
### Modo Original (legendas YouTube)
- **Zero sobreposição:** Sempre truncar o `end` da legenda atual caso ultrapasse o `start` da próxima.
- **Ritmo de leitura:** Agrupar o texto em blocos curtos de 3 a 5 palavras por vez (estilo dinâmico TikTok/Shorts).
- **Posicionamento:** Usar `Alignment=2` (baixo-centro) e `MarginV=320` para ficar abaixo do quadro central sem encostar nos botões da interface do TikTok/Shorts.
- **Estilos alternados:** Alternar entre estilo `Default` (branco com contorno) e `Highlight` (amarelo) a cada bloco para efeito "karaoke".

### Modo IA (narração edge-tts) — REGRAS FIXAS
- **Fonte única:** Eventos `WordBoundary` do `edge_tts.Communicate.stream()` — timestamps exatos por palavra.
- **UM estilo apenas:** `Alignment=2`, `MarginV=320` — texto NUNCA sobe para o meio da tela.
- **Zero sobreposição:** `end` do bloco atual = `start` do próximo (truncamento estrito).
- **Blocos de 3-5 palavras:** Agrupados respeitando pontuação final (`.!?…`) — fecha chunk ao atingir limite OU encontrar pontuação de fim de frase.
- **Sem alternância de estilos:** Estilo `Default` fixo — sem efeito karaoke, sem `Highlight`.

## Análise Visual de Vídeos (Projeto Todo)
- Sempre que precisar avaliar a qualidade de um corte gerado, analisar um vídeo de referência ou entender o contexto visual antes de editar qualquer script do projeto, utilize a skill `/watch` (.claude/skills/watch/SKILL.md) para extrair frames com `./bin/ffmpeg.exe` e ler a transcrição.
- Nunca escolha minutagens aleatórias: ao sugerir ou programar cortes, garanta começo (gancho), desenvolvimento e conclusão de raciocínio baseados no contexto completo do vídeo.