# Fábrica de Cortes

Ferramenta para Windows que transforma vídeos do YouTube em cortes verticais (1080x1920) para TikTok, Reels e Shorts, com legendas e, se você quiser, narração por IA. Os cortes saem na pasta `cortes_prontos/`.

Este arquivo explica **o que precisa estar instalado para funcionar** (principalmente o ffmpeg) e **como testar**.

---

## 1. O que precisa estar instalado

| Item | Para quê | Como conferir |
|---|---|---|
| **Python 3** (o projeto foi feito com a 3.15) | roda os scripts | `python --version` |
| **ffmpeg** | corta o vídeo, coloca legenda, mistura o áudio | veja a seção 2 |
| **Node.js LTS** | o YouTube exige um runtime JavaScript para o download funcionar | `node --version` |
| **Pacotes Python** (`requirements.txt`) | yt-dlp, edge-tts etc. | seção 3 |
| **Chave do Groq** no arquivo `.env` | roteiro por IA, transcrição de vídeo sem legenda | seção 4 |

Sem o Node.js, o download do YouTube pode falhar com erro 403 ou formatos faltando. Para instalar: `winget install OpenJS.NodeJS.LTS` e depois **fechar e abrir o terminal**.

---

## 2. ffmpeg: condições para funcionar

O programa procura o ffmpeg nesta ordem:

1. `bin\ffmpeg.exe` (dentro da pasta do projeto);
2. o `ffmpeg` que estiver no PATH do Windows.

Se não achar nenhum, o programa para com a mensagem `ffmpeg não encontrado`.

### Opção A: copiar o `ffmpeg.exe` para a pasta `bin` (recomendada)

1. Baixe a versão **release essentials** em https://www.gyan.dev/ffmpeg/builds/ (arquivo `ffmpeg-release-essentials.zip`).
2. Abra o zip e pegue **só** o arquivo `bin\ffmpeg.exe`.
3. Crie a pasta `bin` na raiz do projeto (ao lado do `iniciar.bat`), se ela não existir, e coloque o `ffmpeg.exe` lá.

Fica assim: `D:\fabrica-cortes\bin\ffmpeg.exe`. A pasta `bin` não vai para o GitHub (está no `.gitignore`), então **cada computador precisa ter a sua**.

### Opção B: instalar pelo winget

```
winget install --id Gyan.FFmpeg --exact
```

Depois **feche e abra o terminal**. O programa usa o ffmpeg do PATH.

### Condições que o ffmpeg precisa cumprir

Nem todo ffmpeg serve. O build precisa ter:

- **libx264** (gera o vídeo);
- **libass** (o filtro `subtitles`, que coloca a legenda);
- **aac** (áudio).

O build "release essentials" do gyan.dev tem tudo isso. Builds muito reduzidos, ou de origem desconhecida, podem não ter o filtro de legenda.

### Como conferir (rode na pasta do projeto)

Versão:

```
bin\ffmpeg.exe -version
```

Tem que aparecer `ffmpeg version ...` sem erro. Se você usa a Opção B, troque `bin\ffmpeg.exe` por `ffmpeg`.

Filtro de legenda:

```
bin\ffmpeg.exe -hide_banner -filters | findstr subtitles
```

Tem que aparecer uma linha com `subtitles`. Se não aparecer nada, esse build não serve.

Encoder de vídeo:

```
bin\ffmpeg.exe -hide_banner -encoders | findstr libx264
```

Tem que aparecer uma linha com `libx264`.

### Erros comuns do ffmpeg

| Mensagem | Causa | O que fazer |
|---|---|---|
| `ffmpeg não encontrado em ... nem no PATH` | não há `bin\ffmpeg.exe` e o PATH não tem ffmpeg | opção A ou B |
| `ffmpeg não funciona` | arquivo corrompido ou bloqueado pelo Windows/antivírus | baixe de novo, clique direito no arquivo, Propriedades e marque "Desbloquear" se aparecer |
| `Erro no ffmpeg` seguido de `Tentando versão simplificada...` | o corte com fundo desfocado falhou | anote o texto do erro que vem junto e envie |
| `No such filter: 'subtitles'` | build sem libass | use o build essentials da opção A |

---

## 3. Primeira instalação (uma vez por computador)

Na pasta do projeto, no terminal (CMD):

```
python -m venv .venv
```

```
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

O `iniciar.bat` usa o Python dessa pasta `.venv`. Se ela não existir, ele cai para o `python` global, e aí os pacotes do passo acima podem não estar disponíveis.

Opcional: `python verificar_dependencias.py` confere Python, yt-dlp, ffmpeg e Node.js, e tenta instalar o que faltar via winget.

---

## 4. Chave do Groq (arquivo `.env`)

1. Copie o arquivo `.env.example` para `.env` (mesma pasta).
2. Abra o `.env` e troque `cole_sua_chave_aqui` pela sua chave (crie uma em https://console.groq.com/keys).

O `.env` **nunca vai para o GitHub**. Não cole a chave em chat, e-mail ou print.

Sem a chave, o programa ainda funciona quando o vídeo tem legendas do YouTube. Sem a chave **não há**: roteiro reescrito por IA, transcrição de vídeo sem legenda, nem análise visual de gameplay.

---

## 5. Como usar

Dê dois cliques em `iniciar.bat`.

| Opção | O que faz |
|---|---|
| 1 | busca vídeos em alta no YouTube |
| 2 | gera cortes de uma URL **mantendo o áudio original** |
| 3 | gera cortes de uma URL **com narração por IA** (voz pt-BR) |
| 4 | sair |

Os cortes ficam em `cortes_prontos/`. Cada corte deve ter **de 30 a 55 segundos**, com legenda e um fade curto no final.

---

## 6. Roteiro de teste para o QA

Anote para cada item: **passou ou falhou**, a **URL** usada, a **opção** do menu e, se falhou, o **texto completo do terminal**.

1. **Opção 1.** Lista vídeos em alta sem erro.
2. **Opção 2 com URL limpa** (`https://www.youtube.com/watch?v=ID`) de um vídeo com fala e legenda. Gera 1 a 3 cortes.
3. **Opção 2 com URL "suja"**, por exemplo `...watch?v=ID&t=1214s&pp=...`. Deve baixar só aquele vídeo e funcionar igual ao item 2.
4. **Opção 3** com o mesmo vídeo. Confira:
   - a narração fala as frases até o fim, sem cortar no meio;
   - a legenda acompanha a voz (3 a 5 palavras por vez, sem pular);
   - o vídeo **não** fica acelerado de forma estranha.
5. **Duração.** Cada corte tem entre 30 e 55 segundos. **Corte de poucos segundos é falha**: envie o arquivo e o terminal. Se a narração sair curta demais, o programa deve avisar `Narração curta demais` e usar o áudio original.
6. **Final do corte.** O áudio e a imagem terminam com um fade curto, sem "corte seco".
7. **Vídeo sem legenda** (ou só música/gameplay sem fala). Deve cair na transcrição por áudio e, se não houver fala, na análise visual. Sem `GROQ_API_KEY`, deve avisar e **não** gerar corte inventado.
8. **Sem o ffmpeg** (renomeie `bin` temporariamente e tire o PATH, se souber). Deve parar com a mensagem clara da seção 2, não com um erro estranho.
9. **URL inválida ou vídeo privado.** Deve falhar com mensagem legível, sem travar.

### O que mandar quando algo falhar

1. O texto **inteiro** do terminal (selecione, copie e cole; não só print do final).
2. A URL, a opção do menu e se o `.env` tinha a chave.
3. Se gerou um arquivo ruim, o `.mp4`.
4. A saída de `bin\ffmpeg.exe -version` e de `python --version`.

---

## 7. Mantendo o projeto atualizado

Quem **só testa** deve usar `baixar_github.bat` para atualizar e **não editar nenhum arquivo** do projeto. Se alguém editar, o próximo `baixar_github.bat` pode falhar ou gerar conflito.

Se aparecer algo como `<<<<<<< Updated upstream` dentro de um arquivo, é um conflito do git na sua cópia (não é bug do código). Para voltar à versão do GitHub, na pasta do projeto:

```
git fetch origin
```

```
git reset --hard origin/main
```

Isso **descarta qualquer alteração local** nos arquivos do projeto. O `.env` e a pasta `bin` são preservados, porque o git os ignora.

---

## 8. Para quem desenvolve: a skill `/watch`

A pasta `.claude/skills/watch/` tem uma skill do Claude Code que assiste vídeos (útil para avaliar cortes gerados). Ela **não faz parte do produto** e tem requisitos próprios (`ffmpeg`, `ffprobe` e `yt-dlp` no PATH, mais o Deno). Veja a seção "Análise Visual de Vídeos" do `CLAUDE.md`.
