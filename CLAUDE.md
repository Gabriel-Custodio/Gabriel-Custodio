# Fábrica de Cortes - Regras do Projeto

## Ambiente e Execução
- **SO:** Windows 10/11 | **Python:** 3.15 (não usar bibliotecas incompatíveis como `faster-whisper`).
- **Dependências:** Sempre chamar `yt-dlp` como módulo (`sys.executable, "-m", "yt_dlp"`) e usar o FFmpeg local em `./bin/ffmpeg.exe`.
- **Python do projeto:** Usar sempre o Python do ambiente virtual (`.venv\Scripts\python.exe`) no `iniciar.bat`.
- **API:** Credenciais ficam no arquivo `.env` na raiz (nunca expor chaves no código).

## Arquitetura dos Arquivos
- `iniciar.bat`: Menu principal em Batch (manter codificação ASCII sem acentos nos `echo` para não quebrar no CMD). Usa `.venv\Scripts\python.exe` e oferece 3 opções: buscar tendências, gerar cortes com áudio original, gerar cortes com narração IA.
- `buscar_tendencias.py`: Busca vídeos em alta no YouTube via `yt-dlp`.
- `gerar_cortes.py`: Baixa legendas automáticas do YouTube (`.vtt`/`.json3`), seleciona os melhores trechos via IA e renderiza cortes verticais `1080x1920` na pasta `cortes_prontos/`.
  - **Modo `--audio original`**: Mantém áudio original do vídeo, usa legendas do YouTube sincronizadas com o corte.
  - **Modo `--audio ia`**: Gera narração com `edge-tts` (voz `pt-BR-AntonioNeural`), cria roteiro viral resumido com gancho forte, remove áudio original (`-an`), ajusta duração do vídeo ao MP3 gerado (acelera/desacelera vídeo via `setpts`) e sincroniza legendas `.ass` com a narração.

## Padrões Obrigatórios de Legenda (.ass)
- **Zero sobreposição:** Sempre truncar o `end` da legenda atual caso ultrapasse o `start` da próxima.
- **Ritmo de leitura:** Agrupar o texto em blocos curtos de 3 a 5 palavras por vez (estilo dinâmico TikTok/Shorts).
- **Posicionamento:** Usar `Alignment=2` (baixo-centro) e `MarginV=320` para ficar abaixo do quadro central sem encostar nos botões da interface do TikTok/Shorts.
- **Estilos alternados:** Alternar entre estilo `Default` (branco com contorno) e `Highlight` (amarelo) a cada bloco para efeito "karaoke".

## Análise Visual de Vídeos (Projeto Todo)
- Sempre que precisar avaliar a qualidade de um corte gerado, analisar um vídeo de referência ou entender o contexto visual antes de editar qualquer script do projeto, utilize a skill `/watch` (.claude/skills/watch/SKILL.md) para extrair frames com `./bin/ffmpeg.exe` e ler a transcrição.
- Nunca escolha minutagens aleatórias: ao sugerir ou programar cortes, garanta começo (gancho), desenvolvimento e conclusão de raciocínio baseados no contexto completo do vídeo.