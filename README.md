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
| **Pacotes Python** (`requirements.txt`) | yt-dlp, edge-tts, pillow (faixa do gancho) etc. | seção 3 |
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
- **aac** (áudio);
- versão **5.1 ou mais nova** (o programa usa a opção `-fps_mode`). A primeira linha de `-version` mostra a versão.

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
| 2 | **reparar/reprocessar** um corte que já existe (pede o caminho do arquivo `.mp4`) |
| 3 | gera cortes de uma URL **com narração por IA** (voz pt-BR) |
| 4 | **modo Gameplay / Streamer**: gera cortes de uma URL mantendo o áudio original |
| 5 | **monitor de lives** (YouTube, Twitch, Kick): grava a live e gera cortes sozinho |
| 6 | sair |

O modo "áudio original" para vídeos comuns não tem mais opção no menu. Para usá-lo, abra o CMD na pasta do projeto e rode:

```
.venv\Scripts\python.exe gerar_cortes.py "URL_DO_VIDEO" --audio original
```

Os cortes ficam em `cortes_prontos/`. Cada corte deve ter **de 30 a 55 segundos**, com legenda e um fade curto no final.

### Layout dos cortes

Por padrão, o corte é o **vídeo original inteiro** em 1080x1920, **sem recorte e sem zoom**: o vídeo é só reduzido para caber na largura e fica no meio da tela, com legenda embaixo. O espaço que sobra em cima e embaixo recebe um fundo (por padrão, uma cópia desfocada do próprio vídeo). Se sobrar espaço em cima, aparece a faixa vermelha do gancho.

A webcam do streamer já aparece dentro do vídeo, por isso ela **não** é mais recortada nem ampliada. Recortar e ampliar a webcam e o centro do jogo era o que deixava a imagem borrada.

Para mudar, edite o arquivo `.env` (veja o `.env.example`):

| Variável | Valores | Efeito |
|---|---|---|
| `CORTES_LAYOUT` | `cam` (padrão), `fit` ou `clipflow` | `cam`: câmera em cima, faixa vermelha com a legenda e gameplay embaixo; `fit`: só o vídeo inteiro; `clipflow`: layout antigo de 3 camadas, com zoom |
| `CORTES_FUNDO` | `blur` (padrão) ou `preto` | fundo desfocado ou preto atrás do vídeo |

### Não repetir trechos (histórico)

O programa **lembra** quais trechos de cada vídeo já viraram corte. Se você processar o **mesmo vídeo de novo**, ele pula esses trechos e escolhe outros. O histórico fica em `cortes_prontos\_historico_cortes.json` e vale para qualquer forma do link (`watch?v=...`, com `&t=...`, `youtu.be`, `shorts`).

- Um trecho conta como repetido se ele se sobrepõe a um corte anterior ou fica colado nele (uns 15 segundos de folga). Cortes **descartados** na interface também contam como usados.
- Quando o vídeo acaba os trechos novos, o programa avisa (`Só N trecho(s) novo(s)`) em vez de repetir.
- **Para permitir repetir:** use `--repetir` na linha de comando, ou marque "Permitir repetir trechos" nas opções avançadas da interface. Os cortes novos continuam sendo anotados.
- **Cortes feitos antes do histórico existir** não guardam de qual vídeo saíram. Se todos os que estão na pasta vieram do mesmo vídeo, rode uma vez com `--importar-existentes` (ou marque a caixa correspondente na interface) para marcá-los como usados:

```
.venv\Scripts\python.exe gerar_cortes.py "URL_DO_VIDEO" --audio gameplay --importar-existentes
```

- **Para esquecer tudo:** apague o arquivo `_historico_cortes.json`.

> **Atenção:** no modo bruto (padrão) o resumo narrado, a chamada final, a legenda e a tarja **não são aplicados na geração**: eles são escolhidos no menu **Editar** de cada corte (veja "Corte bruto + menu Editar"). Esta seção, a da chamada final e a da tarja descrevem como cada recurso funciona e valem na edição e no modo antigo (`CORTES_GAMEPLAY_BRUTO=0`), onde `--sem-resumo`, `--sem-cta` e `CORTES_RESUMO`/`CORTES_CTA` ainda mandam.

### Resumo narrado no início (modo gameplay)

Cada corte do modo gameplay **começa com o vídeo parado** no primeiro quadro enquanto uma voz (a mesma do modo IA, `pt-BR-AntonioNeural`) resume o assunto em 1 ou 2 frases; depois o corte roda normalmente. Vem **ligado por padrão**.

- O resumo usa **só o que foi dito no trecho**. Se a IA não conseguir um resumo fiel (ou o trecho passar de 60 s com a intro), o corte sai **sem intro**, e o terminal avisa o motivo.
- A intro soma uns 6 a 9 segundos ao corte. O `.json` guarda o texto em `resumo_intro` e a duração total em `duracao_final_s`.
- O corte é juntado à intro **sem recodificar** (`juncao: copia`), então a qualidade não cai. Se isso falhar, ele recodifica (`juncao: reencode`).
- **Nome do streamer:** o resumo usa o nome do **canal** do vídeo (lido pelo yt-dlp). Se o canal parecer de cortes/compilações ("Cortes", "Clips", "Highlights"...), o nome **não** é usado sozinho e o resumo diz "o streamer". Nesse caso (ou se o nome do canal for diferente do nome do streamer) informe com `--streamer "NOME"` ou no campo "Nome do streamer" da interface. O nome vai também para o `.json` (`streamer`).
- **Para desligar:** `--sem-resumo` na linha de comando, `CORTES_RESUMO=0` no `.env`, ou desmarque a caixa na interface.

### Chamada final: like, compartilhar e inscrever-se (modo gameplay)

No **fim** de cada corte, depois do fade, o último quadro fica parado (um pouco mais escuro) e a voz diz: "Gostou? Deixe o like, comente, compartilhe com um amigo e se inscreva no canal para ver mais cortes!". A mesma frase aparece na legenda. Vem **ligada por padrão** e soma uns 6 a 7 segundos.

- Funciona **sem** a chave do Groq (a frase é fixa) e independe do resumo no início: dá para usar um sem o outro.
- Junção **sem recodificar** (`cta_final.juncao: copia` no `.json`), com plano B recodificando. Se algo falhar, o corte sai sem a chamada, e o terminal diz o motivo.
- Se corte + resumo + chamada passarem de 60 s, a chamada final é a que é pulada.
- **Trocar a frase:** `CORTES_CTA_TEXT=...` no `.env` (3 a 25 palavras).
- **Para desligar:** `--sem-cta`, `CORTES_CTA=0` no `.env` ou a caixa "Chamada final" na interface.

### Tarja vermelha: título + "CURTA · COMENTE · COMPARTILHE"

**Layout `cam` (padrão no modo gameplay):** de cima para baixo, a **câmera do streamer** (largura toda, 1080x600), uma **faixa vermelha** com a legenda e, embaixo dela, `CURTA · COMENTE · COMPARTILHE`, e o **gameplay** preenchendo o resto (recorte central, sem barras). A câmera é recortada na proporção do bloco, com uma folga em volta da caixa indicada. A caixa vem de, nesta ordem: `--camera X,Y,LARG,ALT` (frações do vídeo, ex.: `0.78,0.37,0.21,0.22`; na interface, o campo "Câmera manual"), `--webcam` 1 a 5 (posições prontas) ou a IA, que procura a câmera num quadro do vídeo (precisa da `GROQ_API_KEY`). Em todos os casos o programa salva `cortes_prontos/_previa_camera.jpg`, um quadro com a caixa detectada (amarelo) e o que vai aparecer em cima (verde): confira antes de aprovar os cortes e, se errou, rode de novo com `--camera`. Se a câmera é pequena no vídeo ela é bastante ampliada e pode ficar borrada: o terminal avisa quando passa de 2,2x e o `.json` guarda `camera_ampliacao`. Sem câmera, com caixa inválida ou fora do modo gameplay, o corte sai no layout `fit`. O título do gancho não aparece nesse layout (continua no `.json`); o `.json` também guarda o `layout` usado.

**Corte bruto + menu Editar (modo Gameplay / Streamer, padrão):** a geração só **recorta** o trecho: o corte sai com a **tela completa do streamer**, sem fundo desfocado, tarja, legenda, resumo nem chamada final. Na aba **Cortes prontos**, cada corte tem o botão **Editar**, que abre um menu com:
0. **Linha do tempo** (embaixo do vídeo): régua, **onda do áudio** e **quadros do vídeo**, com um cursor que acompanha a reprodução (clique ou arraste para ir a um ponto) e **zoom**. Puxe as **alças amarelas** para aparar o início e o fim, ou use **Dividir no cursor** e **Excluir trecho** (o mesmo botão vira **Restaurar trecho** num trecho já excluído) para tirar um pedaço do meio; **Desfazer** e **Refazer** (também Ctrl+Z / Ctrl+Y) voltam cada passo. O vídeo toca pulando o que foi excluído, e o que sobra dura no mínimo 1 segundo. Nada é cortado na hora: os trechos valem quando você clica em **Renderizar de novo**, e a **legenda e o texto do resumo acompanham o que sobrou**. O bruto nunca é alterado, e reabrir o Editar mostra os cortes da última edição. A onda e os quadros são desenhados pelo navegador a partir do próprio vídeo; se o navegador não conseguir ler o áudio, a timeline continua funcionando, só sem a onda.
1. **Layout:** *Câmera + gameplay (caixas)* ou *Tela inteira* (o vídeo todo no meio, com o fundo desfocado).
2. **Caixas de crop** (no layout de caixas): o menu mostra um quadro do próprio corte (o do meio; dá para pausar o vídeo em outro momento e clicar em "Usar este quadro"). Arraste a caixa rosa (câmera) e a azul (gameplay) para movê-las e puxe um canto para mudar o tamanho; a proporção fica travada (a do espaço da caixa no vídeo vertical), então nada é esticado. A prévia em retrato ao lado mostra o resultado; arraste a linha branca para mudar a altura dos blocos e escolha a ordem (câmera ou gameplay em cima).
3. **O que colocar:** tarja vermelha (com `CURTA · COMENTE · COMPARTILHE`; sem ela os blocos ficam colados), legenda karaokê, resumo narrado no início e chamada final. Tudo vem ligado.
4. **Renderizar de novo:** o corte é refeito **sempre a partir do bruto** (guardado em `cortes_prontos/_brutos/`, que nunca é alterado), então dá para editar quantas vezes quiser. Se a renderização falhar, o corte continua como estava. Depois é só conferir e **Aprovar**.
**Nitidez da câmera:** o vídeo final sempre sai em 1080x1920 (1080p vertical), mas a câmera do streamer ocupa só uma parte pequena do vídeo de origem e é esticada até a largura da tela, então ela perde definição quando a caixa é pequena. O menu mostra, embaixo da prévia, quantas vezes a câmera está sendo ampliada (verde até ~2x, amarelo até ~3x, vermelho acima disso): para ganhar nitidez, **puxe os cantos da caixa da câmera para pegar uma área maior**. Ampliações acima de 1,3x também levam um realce leve de nitidez. O fator que mais pesa é a **resolução do vídeo de origem**: sem o **Node.js** instalado (o selo "Sem Node/Deno" no topo da interface), o YouTube costuma entregar só 360p/720p. Instale o Node.js (nodejs.org), feche e abra o `interface.bat` e gere os cortes de novo. Mesmo com Node, o vídeo podia vir em 360p porque o programa forçava o cliente `android` do YouTube, que só entrega esse formato: agora, antes de baixar, o programa **testa os clientes do YouTube** (sem baixar nada; o terminal mostra "cliente padrão do yt-dlp: até 1080p", "cliente tv,web_safari: até 360p"...) e baixa com o primeiro que chega a 1080p (ou o que oferecer mais). Depois do download o terminal mostra "Resolução do vídeo baixado: LxA" e avisa se for baixa. Se mesmo assim vier pequeno, atualize o yt-dlp (`pip install -U "yt-dlp[default]"`) e confira o que o YouTube oferece com `python -m yt_dlp -F LINK_DO_VIDEO`: se o `-F` só lista até 360p, o vídeo original no YouTube é pequeno. O `.json` do corte guarda `fonte_resolucao`.

Dá para salvar as caixas com um nome (por exemplo, o do streamer) e aplicar em outros cortes ("Meus layouts"; ficam em `layouts_salvos/`, só neste computador). O `.json` do corte guarda a legenda (palavras com tempo) e, depois da edição, `layout` e `edicao` com as opções usadas. Descartar um corte leva o bruto junto para `_descartados/_brutos/`. Para voltar ao comportamento antigo (renderizar o layout na hora, sem menu), use `CORTES_GAMEPLAY_BRUTO=0` no `.env`. No terminal, `--renderizar CORTE.mp4` refaz um corte com as opções de `cortes_prontos/_edicoes/`.

**Layout `cam` e `--layout-salvo` (opcionais, só com `CORTES_GAMEPLAY_BRUTO=0`):** o layout de câmera em cima, faixa e gameplay embaixo (`CORTES_LAYOUT=cam`, com `--webcam`/`--camera` ou detecção por IA) e `--layout-salvo NOME` seguem disponíveis para o modo antigo.

No layout `fit`, a tarja vermelha acima do vídeo mostra o **título do gancho** (maior que antes, encolhe se for comprido) e, embaixo dele, uma **linha fixa** em branco: `CURTA · COMENTE · COMPARTILHE`. Fica na tela o corte inteiro. O layout antigo (`clipflow`) não ganha a linha, porque a legenda karaokê usa o meio da faixa. Se o vídeo ocupar quase toda a altura (sobrar pouco espaço acima), a tarja inteira não aparece, como já era.

- **Trocar a frase:** `CORTES_BANNER_CTA=OUTRA FRASE` no `.env`. **Tirar a linha:** `CORTES_BANNER_CTA=0`.

### Relatório no fim da execução

Ao terminar o modo gameplay, o terminal lista quantos cortes ficaram com resumo no início e com chamada final, e para cada corte que **saiu sem** um deles diz o motivo (por exemplo: `corte 3 saiu SEM resumo no início: a IA não deu um resumo fiel à fala do trecho`). Recurso desligado aparece como `desligado`, não como falha.

---

## 6. Roteiro de teste para o QA

Anote para cada item: **passou ou falhou**, a **URL** usada, a **opção** do menu e, se falhou, o **texto completo do terminal**.

1. **Opção 1.** Lista vídeos em alta sem erro.
2. **Áudio original** (comando da seção 5) com uma URL de um vídeo com fala e legenda. Gera 1 a 3 cortes.
3. **URL "suja"**, por exemplo `...watch?v=ID&t=1214s&pp=...`, no item 2. Deve baixar só aquele vídeo.
4. **Opção 3** (narração IA) com o mesmo vídeo. Confira:
   - a narração fala as frases até o fim, sem cortar no meio;
   - a legenda acompanha a voz (3 a 5 palavras por vez, sem pular);
   - o vídeo **não** fica acelerado nem em câmera lenta de forma estranha.
5. **Opção 4** (Gameplay/Streamer) com uma live gravada. A webcam do streamer deve aparecer **dentro** do vídeo, sem ser recortada.
   **Teste de título e texto (importante):** para cada corte gerado, abra o `.json` que fica ao lado do `.mp4` e compare com o vídeo:
   - a **legenda na tela**, o **texto `transcricao` do `.json`** e o **título da faixa vermelha** devem tratar do que o streamer **realmente fala naquele corte** (ouça o corte);
   - o nome do arquivo (`corte_gameplay_N_...`) deve ser o título do gancho, e não uma frase solta de outro momento;
   - em `fonte_titulo`, o valor esperado é `ia_trecho_real` ou `ia_texto_real` (`padrao` indica que a IA falhou);
   - os cortes devem estar **espalhados pela live inteira** (veja `faixa_do_video` e `clip_start`), não todos no começo;
   - um trecho **sem fala** (música, silêncio) **não pode** virar corte: o terminal deve mostrar `Corte descartado: fala insuficiente`.
   Se um título não combinar com o corte, anote o `.json` inteiro e o texto do terminal.
6. **Nitidez (o teste mais importante).** Abra um corte no tamanho real, no celular ou em tela cheia:
   - o vídeo está inteiro, **sem corte nas laterais** e sem zoom;
   - o texto do jogo e o rosto estão nítidos, **não** borrados nem com blocos;
   - o fundo atrás é só um enfeite (desfocado), e o vídeo no meio é o que importa.
   Compare com um corte feito com `CORTES_LAYOUT=clipflow` no `.env` para ver a diferença.
7. **Duração.** Cada corte tem entre 30 e 55 segundos. **Corte de poucos segundos é falha**: envie o arquivo e o terminal. Se a narração sair curta demais, o programa deve avisar `Narração curta demais` e usar o áudio original.
8. **Final do corte.** O áudio e a imagem terminam com um fade curto, sem "corte seco".
9. **Vídeo sem legenda** (ou só música/gameplay sem fala). Deve cair na transcrição por áudio e, se não houver fala, na análise visual. Sem `GROQ_API_KEY`, deve avisar e **não** gerar corte inventado.
10. **Sem o ffmpeg** (renomeie `bin` temporariamente e tire o PATH, se souber). Deve parar com a mensagem clara da seção 2.
11. **URL inválida ou vídeo privado.** Deve falhar com mensagem legível, sem travar.
12. **Sem repetir (histórico):** gere cortes de um vídeo, depois gere de novo **o mesmo vídeo** (pode ser com outro formato de link). Os cortes da segunda vez **não podem repetir** os da primeira (compare o `clip_start`/`clip_end` dos `.json` ou os trechos assistidos). Depois repita marcando "Permitir repetir trechos": agora pode repetir.
13. **Resumo no início (gameplay):** o corte começa com o primeiro quadro **parado** e uma voz resumindo; a legenda da voz aparece embaixo; quando a voz termina, o vídeo **continua do mesmo ponto** com o áudio original, sem estalo, sem pulo de imagem e sem perder a sincronia da legenda. Confira:
   - o resumo **combina com o que o streamer fala** naquele corte (compare com `transcricao` no `.json`) e não inventa fato;
   - o resumo diz o **nome do streamer** (no terminal: `Nome do streamer para o resumo: ...`); confira se o nome está certo e se a voz o **pronuncia bem** (nomes de usuário podem sair estranhos; anote qual);
   - com um vídeo de canal de cortes, o terminal deve avisar que não identificou o nome, e `--streamer "NOME"` deve resolver;
   - no `.json`, `resumo_intro.juncao` é `copia` (se vier `reencode`, anote o terminal e compare a nitidez);
   - o corte inteiro tem no máximo 60 s e o fade final continua funcionando;
   - rode de novo com `--sem-resumo`: o corte deve sair **sem** intro e com `resumo_intro: null`.
14. **Chamada final (gameplay):** o corte termina com o fade, aparece o último quadro parado e a voz pede like, compartilhamento e inscrição, com a legenda da frase. Confira que não há estalo na emenda, que o áudio do corte não é cortado antes do fade, que o `.json` tem `cta_final` (e `duracao_final_s` somando resumo + corte + chamada) e que `--sem-cta` tira só a chamada (o resumo do início continua).
15. **Tarja e relatório (gameplay, layout fit):** a tarja vermelha mostra o título em amarelo e, embaixo, `CURTA · COMENTE · COMPARTILHE` em branco, legível no celular e sem cortar nas bordas (teste também um título comprido). No fim do terminal aparece o bloco "Resumo no início e chamada final" com a contagem e o motivo de cada corte que ficou sem. Com `CORTES_BANNER_CTA=0` a linha some.
16. **Layout cam (gameplay):** confira de cima para baixo câmera do streamer, faixa vermelha com a **legenda dentro dela** e `CURTA · COMENTE · COMPARTILHE`, gameplay. Abra `cortes_prontos/_previa_camera.jpg`: o retângulo verde deve pegar a câmera inteira com o rosto no meio; se não, rode de novo com `--camera X,Y,LARG,ALT` (ou o campo "Câmera manual" da interface). Veja se a câmera está nítida (o terminal avisa se foi ampliada mais de 2,2x), se a legenda cabe na faixa (teste frase longa) e se o `.json` tem `"layout": "cam"`. Sem câmera encontrada o corte deve sair em `fit` com aviso. Compare com `CORTES_LAYOUT=fit`.
17. **Corte bruto e menu Editar (interface, gameplay):** gere cortes no modo Gameplay / Streamer: eles devem sair **brutos** (tela completa, sem fundo desfocado, tarja, legenda, resumo ou chamada final) e o selo "bruto: sem edição" deve aparecer. Em Cortes prontos, clique em **Editar**: o menu deve abrir com um quadro do corte e as duas caixas. Mova e redimensione as caixas (os cantos mantêm a proporção; a caixa não sai do quadro), arraste a linha branca da prévia (câmera entre 300 e 1100 px), troque a ordem, ligue e desligue tarja, legenda, resumo e chamada final e clique em **Renderizar de novo**: o corte deve ser refeito com a câmera e o gameplay exatamente nas caixas escolhidas, e o selo deve mudar para "editado". Confira a legenda (dentro da tarja; sem tarja, na junção dos blocos), o resumo e a chamada final. Teste: "Tela inteira" (legenda embaixo do vídeo, título na tarja de cima), editar o mesmo corte uma segunda vez (parte do bruto, não do editado), salvar um layout e aplicá-lo em outro corte, descartar um corte editado (o bruto vai junto) e uma renderização que falha (o corte deve continuar como estava).
18. **Linha do tempo no Editar:** abra o Editar de um corte: embaixo do vídeo devem aparecer a régua, a onda do áudio e os quadros do vídeo. Clique e arraste na timeline (o vídeo acompanha e o cursor segue a reprodução), dê zoom, puxe a alça amarela do início e a do fim, divida no cursor e exclua um trecho do meio (ele fica listrado, escrito "excluído", e o vídeo pula por cima dele ao tocar), restaure, use **Desfazer/Refazer** e Ctrl+Z/Ctrl+Y. A linha de baixo deve dizer quanto o vídeo vai durar. Clique em **Renderizar de novo**: o corte novo deve ter só os trechos mantidos, a **legenda no tempo certo** (sem frases do trecho excluído) e, com o resumo ligado, o resumo falando só do que sobrou. Reabra o Editar: os cortes devem aparecer de novo. Teste também: tela inteira com cortes, excluir tudo (não deve deixar: mínimo de 1 s) e uma renderização que falha (o corte continua como estava).
18. **Opção 5 (monitor de lives):** só teste com uma live **sua** ou com autorização de quem a faz.

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

---

## 9. Interface gráfica (protótipo)

Uma tela no navegador, com **preview dos cortes** e **geração pela própria tela**, sem usar o terminal. Não precisa instalar nada além do que o projeto já usa.

### Como abrir

Dê dois cliques em `interface.bat`. O navegador abre em `http://127.0.0.1:8765`. **Deixe a janela preta aberta** enquanto usar (ela é o servidor). Para encerrar, feche a janela ou aperte Ctrl+C.

A interface só funciona **neste computador** (endereço `127.0.0.1`): ninguém na rede consegue abri-la.

### O que ela faz

**Aba "Gerar cortes":**
- cole o link do vídeo, escolha o modo (Gameplay / Streamer, Narração por IA ou Áudio original);
- no modo Gameplay, escolha quantos cortes (1 a 10); em "Opções avançadas" dá para trocar o layout e o fundo só nesta execução;
- acompanhe o **andamento ao vivo** (as mesmas mensagens do terminal) e use **Parar** se precisar;
- no topo aparecem os **pré-requisitos**: `ffmpeg`, chave do Groq e Node/Deno. Vermelho = não vai funcionar; amarelo = funciona com limitações.

**Aba "Cortes prontos":**
- lista os vídeos de `cortes_prontos/`, do mais novo para o mais antigo, **com player** para assistir ali mesmo;
- mostra o título, a pergunta, a nota, de onde veio o título e o **texto falado** no corte (lido do `.json`);
- **Aprovar** marca o corte (fica verde); **Copiar título** copia título e pergunta para colar no YouTube; **Baixar**; **Descartar** move o corte para `cortes_prontos/_descartados/` (**nada é apagado**);
- **Abrir pasta** abre `cortes_prontos` no Windows.

### Por trás

A tela chama o mesmo `gerar_cortes.py` de sempre. Ele ganhou duas opções para não precisar perguntar no terminal (a tela as usa; no terminal continuam as perguntas):

```
.venv\Scripts\python.exe gerar_cortes.py "URL" --audio gameplay --cortes 5 --webcam 5
```

No layout padrão (`fit`) a pergunta da webcam não aparece, porque a webcam já está dentro do vídeo. Os logs de cada execução ficam em `app\logs\`.

### Roteiro de teste da interface (QA)

1. Abrir `interface.bat`: o navegador abre sozinho e os quatro selos do topo aparecem. O `ffmpeg` deve estar verde.
2. Colar um link, escolher "Narração por IA" e clicar **Gerar cortes**: o botão fica desabilitado, o andamento aparece e o selo mostra "Rodando" com o tempo.
3. Durante uma execução, clicar **Parar**: o selo vira "Cancelado". Depois dar para gerar de novo.
4. Ao terminar ("Concluído"), clicar em **Ver cortes prontos**: o corte novo aparece primeiro, o vídeo toca e dá para pular pela barra.
5. Conferir que o **título e o texto falado** batem com o que se ouve no corte.
6. **Aprovar**, **Copiar título** (colar em um bloco de notas), **Baixar** e **Descartar** (o arquivo deve aparecer em `cortes_prontos\_descartados`).
7. Tentar gerar com o link vazio ou inválido: a tela mostra a mensagem de erro e não inicia nada.
8. Fechar e abrir a interface de novo: os cortes continuam na lista e os aprovados continuam marcados.

### Monitor de lives (em construção)

**Lista de canais (aba "Monitor de lives").** Cole o **link dos canais** (Twitch: `twitch.tv/nome`; YouTube: `youtube.com/@nome` ou `youtube.com/channel/UC…`), **no máximo 2** (dois links de uma vez, separados por espaço, também vale; com 2 canais na lista o campo trava até você apagar um). Só deve entrar na lista quem **permite cortar a live e postar os cortes em outra plataforma**: não há campo de confirmação, quem cadastra responde por isso, e o canal fica **até você suspender ou apagar** (não há prazo). Em "Mais opções" dá para pôr o nome do streamer, um layout salvo e observações (valem quando se adiciona um canal por vez). Cada canal tem **Editar**, **Suspender/Reativar** (o monitor para de poder assisti-lo, sem perder o cadastro) e **Apagar**. Links de vídeo do YouTube (`watch?v=`, `youtu.be`, `/live/…`) não dizem de qual canal são: use o link do canal. A lista fica em `canais_permitidos.json` na pasta do projeto, **fora do git** (o repositório é público).

**Assistir pela tela.** Cada canal da lista tem um painel de monitor: **Iniciar monitor** sobe um processo para aquela live (no máximo **2 lives ao mesmo tempo**), e o painel mostra a situação em tempo real: *Iniciando*, *Conectando*, **Assistindo a live agora** (com há quanto tempo, quantos momentos e sinais foram detectados e quantas mensagens do chat foram lidas), *Live fora do ar (verificando de novo)*, *Com problema* (com o motivo) ou *Bloqueado*. Também há **Pausar/Retomar** (continua assistindo, sem gerar momentos), **Parar**, a lista dos últimos momentos detectados e o **log** do monitor. Suspender ou apagar o canal para o monitor na hora; fechar a interface para todos. A tela atualiza a cada 2 segundos.

**Núcleo do monitor (pacote `monitor/`; a tela o inicia por você, mas também roda sozinho no terminal).** Um processo por live: `python -m monitor --url https://www.twitch.tv/NOME` (só roda se o canal estiver **ativo** na lista; confere de novo a cada minuto e para se você suspender). Ele grava a live de forma contínua (um único ffmpeg, sem buracos), mede o volume de cada trecho de 4 s e escuta o chat (opcional: `pip install chat-downloader`; sem ele o áudio segue sozinho). Um volume bem acima do **normal daquela live**, uma explosão de mensagens no chat ou palavras de reação (`kkkkk`, "meu deus", "clipa") viram sinais; os sinais próximos viram um **momento**, com nota de 0 a 10, e a janela do corte (35 s antes e 10 s depois do pico) é ancorada **no horário do pico**. Cada momento é registrado em `cortes_prontos\_lives\ID\momentos.jsonl` e vira um **corte candidato** em `cortes_prontos\_lives\ID\momentos\` (os 30 mais recentes). O estado fica em `status.json` na mesma pasta; para parar, crie o arquivo `parar.flag` lá (ou Ctrl+C); `pausar.flag` pausa a detecção sem parar de gravar. Opções: `--sensibilidade baixa|media|alta` (alta pega mais momentos), `--sem-cortes-candidatos` (só detecta e registra), `--sem-chat`. **IA e corte bruto.** Cada momento detectado vira um corte candidato e passa por três passos: (1) a fala do corte é transcrita (Whisper do Groq); (2) a IA lê a transcrição e escreve o título, a pergunta e o motivo (só com base no que foi dito; ela não vê a imagem e **não veta nada**); (3) quem decide se o corte entra é a **nota do momento** (a que o painel mostra, de 0 a 10): **abaixo de 6,5 o corte é descartado automaticamente** (nem arquivo sobra); **de 6,5 a 7,5** vai para **Cortes prontos** com a marcação **"Potencial Médio"**; **de 7,6 a 10** vai com **"Potencial Alto"**. O corte **bruto** (sem layout, tarja nem legenda) entra com a etiqueta **"live · canal"**, no mesmo formato do modo Gameplay, e o arquivo se chama `CorteLive_AAAAMMDD_HHMMSS_notaN.N.mp4`: dali você usa **Editar**, a linha do tempo e **Aprovar** como sempre. A legenda já vem com o tempo de cada palavra. **Sem `GROQ_API_KEY` no `.env`, sem fala ou com a IA fora do ar**, o corte entra do mesmo jeito (pela nota), só que sem legenda e com título padrão; o painel avisa "IA desligada". Para só detectar sem enviar cortes: `--sem-corte-bruto`.

**Testar sem uma live de verdade (modo replay):** `python -m monitor --replay MEU_VIDEO.mp4` toca um vídeo qualquer em tempo real, como se fosse uma live, e roda o monitor inteiro sobre ele (não precisa estar na lista, porque não há canal). Para simular o chat, use `--chat-arquivo chat.jsonl` com uma linha por mensagem: `{"t": 125.0, "texto": "KKKKKKK meu deus"}` (`t` = segundos desde o início do vídeo; o chat só dispara depois de uns 2 minutos "aprendendo o normal").

Roteiros de teste: 22. **IA e corte bruto:** com `GROQ_API_KEY` no `.env` e uma live com fala animada (ou um replay), deixe o monitor assistir por 15 a 20 minutos: o painel deve mostrar "N corte(s) enviado(s) para Cortes prontos" e, em cada momento, "enviado (Potencial Alto/Médio)" ou "descartado: nota X, abaixo do mínimo de 6.5"; na aba **Cortes prontos** o corte aparece com o nome `CorteLive_..._notaN.N.mp4`, a etiqueta "live · canal" e a marcação **Potencial Alto** (nota 7,6 a 10) ou **Potencial Médio** (6,5 a 7,5), o vídeo toca, a transcrição e o título fazem sentido com o que foi dito, e **Editar** abre com a legenda e a linha do tempo funcionando. Nota abaixo de 6,5 não deixa arquivo nenhum. Sem a chave: o painel diz "IA desligada" e os cortes entram sem legenda, com título padrão. 21. **Assistir pela tela:** com um canal da Twitch **ao vivo** na lista, clique em "Iniciar monitor": o selo deve passar de "Iniciando" para "Conectando" e "Assistindo a live agora" em até uns 20 s, os números (tempo, sinais, mensagens do chat) devem subir sozinhos, e "Ver o que o monitor está fazendo" deve mostrar o log. Com um canal **fora do ar**: "Live fora do ar (verificando de novo)". Teste Pausar/Retomar, Parar (o selo volta a "Monitor parado"), iniciar dois canais ao mesmo tempo (o botão dos outros trava com o máximo), suspender o canal com o monitor rodando (ele para) e fechar a interface com um monitor rodando (nenhum `ffmpeg` deve ficar no Gerenciador de Tarefas). Sem ffmpeg em `bin\`, deve aparecer "Com problema: ffmpeg não encontrado". 19. **Lista de canais:** adicione um canal da Twitch e um do YouTube pelo `@nome` (ou os dois links de uma vez); o contador deve mostrar "2 de 2" e o campo travar; apague um e adicione outro; edite um (nome, observações), suspenda e reative; tente um link `watch?v=` do YouTube e um link de outro site (devem recusar com o motivo); feche e abra a interface: a lista continua. 20. **Núcleo (replay):** com um vídeo de uma live ou de gameplay com falas e gritos (de uns 5 a 10 minutos), rode `python -m monitor --replay VIDEO.mp4` e deixe terminar: confira que `status.json` fecha com `"estado": "parado"`, que `momentos.jsonl` tem momentos perto dos trechos mais altos do vídeo, que os cortes em `momentos\` têm cerca de 45 s e terminam logo depois do pico, e que o volume do vídeo não muda sozinho. Teste `--sensibilidade alta` e `baixa`, `parar.flag` no meio, e `python -m monitor --url` de um canal que **não** está na lista (deve sair com "BLOQUEADO"). Com uma live real **de um canal da lista e ao vivo**: o terminal deve mostrar "gravando a live"; se a live estiver fora do ar, "offline" e nova checagem a cada minuto.

Se algo falhar, mande o texto do andamento (aba "Gerar cortes") e o arquivo mais recente de `app\logs\`.

### Limitações desta versão

- **Não** busca métricas do YouTube nem publica vídeos: isso vem nas próximas fases.
- Uma execução por vez.
- Só roda a geração de cortes (o monitor de lives e o reparo continuam pelo `iniciar.bat`).
- Testes automáticos do servidor: `python -m unittest discover -s app/tests -v` (rodam sem internet e sem ffmpeg).
