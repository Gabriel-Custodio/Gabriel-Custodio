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

### Vídeo longo: os melhores momentos juntos (v4.0)

No modo Gameplay / Streamer, além dos cortes curtos, dá para pedir **um vídeo longo** de **4 a 12 minutos** com os melhores momentos do mesmo vídeo. Na interface: marque **"Também criar um vídeo longo"** e escolha a duração; no terminal: `--video-longo MIN`. Com **0 cortes** (ou `--cortes 0`) sai só o vídeo longo.

- **Uma análise só:** o programa baixa e transcreve o vídeo uma vez, a IA dá nota aos trechos, e daí saem os cortes curtos (os N melhores, bem espaçados) e o vídeo longo (os melhores até fechar a duração). Pedir o vídeo longo gasta a mesma cota de IA de antes (um pouco mais de texto por chamada).
- **Regras da escolha:** só entra momento com **nota 6,5 ou mais** (a mesma regra do monitor de lives), com pelo menos **60 s** entre os inícios de dois momentos, em **ordem cronológica**. O último momento pode passar da duração pedida em até 45 s, para o vídeo não ficar abaixo do que você pediu. Se o vídeo não tiver momentos bons o bastante, o vídeo longo **sai mais curto** e o terminal avisa o motivo (menos de 2 momentos ou de 60 s: ele não é gerado).
- **O vídeo longo ignora o histórico** de trechos já cortados (ele reúne os melhores momentos, mesmo os que já viraram corte curto). Os cortes curtos continuam sem repetir.
- **Saída:** `videos_longos/_brutos/VideoLongo_AAAAMMDD_HHMMSS.mp4` (bruto, **horizontal 1920x1080**, 30 fps, sem legenda, tarja nem efeito; cada momento tem um fade de áudio de 0,12 s na emenda) e `videos_longos/VideoLongo_....json` (momentos com tempo no vídeo original e no vídeo longo, nota, motivo e título; transcrição; legenda já no tempo do vídeo longo). O botão **Abrir pasta dos vídeos longos** (na tela Gerar cortes e na aba Vídeos longos) abre a pasta.
- **Os cortes curtos vêm primeiro:** se o vídeo longo falhar, os cortes já estão salvos e o terminal diz que o longo não saiu. Precisa da `GROQ_API_KEY` (as notas vêm da IA); sem ela só os cortes saem. Vídeos de até 90 s não geram vídeo longo.
- **Espaço em disco:** o bruto de 12 minutos fica na casa de algumas centenas de MB (qualidade CRF 18). Não existe cópia duplicada: a lista lê o bruto direto, e o arquivo final só aparece depois da primeira edição.

**Dois streamers no mesmo vídeo longo:** na tela Gerar cortes, com **"Também criar um vídeo longo"** marcado, aparece o campo **Segundo link (opcional)** (e o **nome do segundo streamer**, também opcional). No terminal: `--segundo-link URL --streamer2 "NOME"`. O programa baixa, transcreve e dá nota aos momentos do segundo vídeo exatamente como no primeiro e os dois **se intercalam**: um momento do primeiro, um do segundo, e assim por diante (cada um com o melhor que ainda sobra dele), então cada streamer ocupa cerca de metade do tempo; se um dos vídeos tiver poucos momentos bons (nota 6,5 ou mais), o outro completa. Dentro de cada streamer a ordem é a cronológica do vídeo dele, a regra de 60 s entre momentos vale **dentro de cada vídeo**, e a legenda de cada momento vem da transcrição do vídeo certo.
- **Os cortes curtos saem só do primeiro link** (o segundo alimenta apenas o vídeo longo). O `.json` ganha `fontes` (link, nome e quantos momentos de cada) e cada momento diz de qual streamer veio; o título vira "Melhores momentos de A e B" e o card mostra os dois nomes.
- **Se o segundo vídeo não baixar, for curto demais (até 90 s), não tiver fala ou a IA não der notas,** o vídeo longo sai só com o primeiro e o terminal explica o motivo (os cortes já estão salvos). O mesmo link nos dois campos é ignorado, e o segundo vídeo **não** usa nem grava o histórico de trechos. O segundo download e a análise dele **somam tempo** à execução.

**Aba "Vídeos longos"** (lista, player e editor próprios, no mesmo fluxo dos cortes: o bruto nunca muda e cada edição parte dele):
- cada card mostra o player horizontal, o título, os selos (bruto/editado, nº de momentos, duração, streamer), os **momentos** (horário, título, nota, motivo; o botão do horário leva o player até lá), o texto falado e o resumo narrado; **Aprovar**, **Copiar título**, **Baixar** e **Descartar** funcionam como nos cortes (descartar leva também o bruto para `videos_longos/_descartados/`);
- **Editar** abre o mesmo menu dos cortes no modo horizontal: sem caixas de câmera/gameplay (a câmera já está no quadro) e sem tarja. A **linha do tempo** tem os mesmos recursos (aparar, dividir, excluir trecho, zoom até 40x, desfazer) e mostra **um marcador amarelo e numerado por momento**; a lista "Momentos do vídeo" tem **Ir** (leva o cursor até o momento) e **Excluir momento / Restaurar momento** (tira ou devolve o momento inteiro). A onda do áudio vem pronta do servidor (um vídeo de 12 minutos é grande demais para o navegador abrir inteiro).
- **Opções** (tudo ligado por padrão): legenda karaokê em 1920x1080; **título de cada momento** no começo dele (canto de cima: "3 · SUSTO NO BAÚ"); **resumo narrado** no início (a IA lê só os títulos e as falas dos momentos que sobraram, e o vídeo para no primeiro quadro enquanto a voz abre); **chamada final** (like, compartilhar e se inscrever); e o **título do vídeo** (campo de até 100 caracteres, com **Sugerir outros títulos** pela IA a partir dos momentos; vale para o card e o botão Copiar título, não aparece no vídeo).
- **Recorte do quadro e espelhar:** no bloco "Recorte do quadro (opcional)" marque **Recortar o vídeo**, pause o player num momento típico, clique em **Usar este quadro** e arraste a caixa laranja (cantos e lados mudam o tamanho, Shift mantém a proporção; **Quadro inteiro** volta ao tamanho total). A caixa tem **qualquer formato** e vale para o vídeo todo: quase 16:9 preenche o quadro; outro formato aparece **inteiro** sobre um fundo desfocado, sem cortar nem esticar. O resultado aparece ao lado (com o aviso de nitidez: recorte pequeno demais fica borrado) e **Espelhar o vídeo** inverte direita e esquerda do conteúdo (legenda e títulos não). Recorte e espelho acontecem antes da legenda, do resumo e da chamada final.
- **Renderizar de novo** refaz o vídeo a partir do bruto, em 1920x1080, com volume uniforme (loudnorm) e fade no fim. Não há limite fixo de tempo: o andamento aparece na tela (`⏳ Renderizando: 35%`) e o programa só desiste se o ffmpeg ficar **5 minutos sem andar**. Um vídeo de 12 minutos leva vários minutos, e o resumo e a chamada final somam mais um pouco. Se algo falhar, o vídeo continua como estava. Quando o resumo ou a chamada ficam de fora, o card diz o motivo.

**Descrição para postar e reedição:**
- Todo card de **corte** e de **vídeo longo** tem a seção **Descrição para postar**: escreva a sua ou clique em **Sugerir descrições** (a IA devolve até 3 opções, como nos títulos; clicar numa a coloca no campo para você ajustar) e em **Salvar descrição** / **Copiar descrição**. A IA lê **só a fala do corte** (ou os títulos e motivos dos momentos do vídeo longo): não inventa fatos, usa de 3 a 5 hashtags (4 a 6 no longo) e não escreve tempo nenhum. No **vídeo longo**, o programa acrescenta no fim as **marcas de tempo dos capítulos** (`0:00 Título`...), calculadas a partir da linha do tempo real do vídeo (com o resumo do início e os trechos que você cortou), no formato que o YouTube transforma em capítulos (ele exige 3 ou mais, o primeiro em 0:00). A descrição **não entra no vídeo**: fica no `.json` (`descricao`) e vale na hora, sem renderizar, inclusive para cortes antigos sem versão bruta. Precisa da `GROQ_API_KEY` só para as sugestões.
- **Reeditar:** um corte ou vídeo longo que já foi editado mostra o botão **Reeditar** (no lugar de Editar): ele reabre o editor com as opções da última edição (layout, caixas, linha do tempo, legenda, recorte...) e renderiza de novo a partir do bruto, que nunca muda. Dentro do editor, **Recomeçar do bruto** volta tudo ao padrão de um vídeo ainda sem edição (o título e a descrição ficam). Cortes antigos **sem versão bruta** continuam sem o editor de layout (o layout já foi gravado no vídeo), mas a descrição vale para eles.

**Vídeo longo de uma live monitorada:** no **Monitor de lives**, cada canal tem a linha "Vídeo longo com os melhores cortes desta live": escolha a duração (4 a 12 min) e o período (últimas 3, 6, 12, 24 horas, 3 dias ou todos) e clique em **Montar vídeo longo**. O programa junta, em ordem cronológica, os cortes que o monitor já fez daquele canal e que estão em Cortes prontos (nota 6,5 ou mais; dois cortes que se sobrepõem na live não entram juntos; cortes descartados não entram). Não precisa do monitor rodando e não mexe nos cortes: só os lê. O andamento aparece no log da aba "Gerar cortes" e o resultado na aba "Vídeos longos", com `fonte` no `.json`. **Canal suspenso não pode ter os cortes usados** (a permissão acabou). No terminal: `python gerar_cortes.py --longo-da-live twitch:CANAL --video-longo 8 --live-horas 6`.

### Tarja vermelha: título + "CURTA · COMENTE · COMPARTILHE"

**Layout `cam` (padrão no modo gameplay):** de cima para baixo, a **câmera do streamer** (largura toda, 1080x600), uma **faixa vermelha** com a legenda e, embaixo dela, `CURTA · COMENTE · COMPARTILHE`, e o **gameplay** preenchendo o resto (recorte central, sem barras). A câmera é recortada na proporção do bloco, com uma folga em volta da caixa indicada. A caixa vem de, nesta ordem: `--camera X,Y,LARG,ALT` (frações do vídeo, ex.: `0.78,0.37,0.21,0.22`; na interface, o campo "Câmera manual"), `--webcam` 1 a 5 (posições prontas) ou a IA, que procura a câmera num quadro do vídeo (precisa da `GROQ_API_KEY`). Em todos os casos o programa salva `cortes_prontos/_previa_camera.jpg`, um quadro com a caixa detectada (amarelo) e o que vai aparecer em cima (verde): confira antes de aprovar os cortes e, se errou, rode de novo com `--camera`. Se a câmera é pequena no vídeo ela é bastante ampliada e pode ficar borrada: o terminal avisa quando passa de 2,2x e o `.json` guarda `camera_ampliacao`. Sem câmera, com caixa inválida ou fora do modo gameplay, o corte sai no layout `fit`. O título do gancho não aparece nesse layout (continua no `.json`); o `.json` também guarda o `layout` usado.

**Corte bruto + menu Editar (modo Gameplay / Streamer, padrão):** a geração só **recorta** o trecho: o corte sai com a **tela completa do streamer**, sem fundo desfocado, tarja, legenda, resumo nem chamada final. Na aba **Cortes prontos**, cada corte tem o botão **Editar**, que abre um menu com:
0. **Linha do tempo** (embaixo do vídeo): régua, **onda do áudio** e **quadros do vídeo**, com um cursor que acompanha a reprodução (clique ou arraste para ir a um ponto) e **zoom**. Puxe as **alças amarelas** para aparar o início e o fim, ou use **Dividir no cursor** e **Excluir trecho** (o mesmo botão vira **Restaurar trecho** num trecho já excluído) para tirar um pedaço do meio; **Desfazer** e **Refazer** (também Ctrl+Z / Ctrl+Y) voltam cada passo. O vídeo toca pulando o que foi excluído, e o que sobra dura no mínimo 1 segundo. Nada é cortado na hora: os trechos valem quando você clica em **Renderizar de novo**, e a **legenda e o texto do resumo acompanham o que sobrou**. O bruto nunca é alterado, e reabrir o Editar mostra os cortes da última edição. A onda e os quadros são desenhados pelo navegador a partir do próprio vídeo; se o navegador não conseguir ler o áudio, a timeline continua funcionando, só sem a onda.
1. **Layout:** *Câmera + gameplay (caixas)* ou *Tela inteira* (o vídeo todo no meio, com o fundo desfocado).
2. **Caixas de crop** (no layout de caixas): o menu mostra um quadro do próprio corte (o do meio; dá para pausar o vídeo em outro momento e clicar em "Usar este quadro"). Arraste a caixa rosa (câmera) e a azul (gameplay) para movê-las e puxe um canto ou um lado para mudar o tamanho. **A caixa pode ter qualquer formato** (uma câmera retangular, por exemplo): quando o formato é diferente do espaço dela no vídeo vertical, o recorte aparece **inteiro** no bloco, centralizado sobre uma cópia desfocada dele mesmo, sem cortar nem esticar. Segure **Shift** ao puxar um canto para manter a proporção, ou clique em **Ajustar o bloco da câmera ao formato da caixa** para o bloco ter a mesma proporção da caixa (sem fundo desfocado). A prévia em retrato ao lado mostra o resultado; arraste a linha branca para mudar a altura dos blocos e escolha a ordem (câmera ou gameplay em cima). Há também a opção **Espelhar o vídeo** (inverte direita e esquerda do conteúdo; a legenda, a tarja, o resumo e a chamada final não são invertidos, mas textos que aparecem no próprio jogo ficam ao contrário). Ela existe também na edição do vídeo longo.
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
17. **Corte bruto e menu Editar (interface, gameplay):** gere cortes no modo Gameplay / Streamer: eles devem sair **brutos** (tela completa, sem fundo desfocado, tarja, legenda, resumo ou chamada final) e o selo "bruto: sem edição" deve aparecer. Em Cortes prontos, clique em **Editar**: o menu deve abrir com um quadro do corte e as duas caixas. Mova e redimensione as caixas (cantos e lados mudam o tamanho livremente, Shift mantém a proporção; a caixa não sai do quadro). Faça uma caixa bem retangular na câmera: ela deve aparecer inteira no bloco, com fundo desfocado nas sobras, e "Ajustar o bloco da câmera ao formato da caixa" deve tirar o fundo desfocado. Marque **Espelhar o vídeo**, renderize e confira que o conteúdo da câmera e do gameplay saiu invertido na horizontal (e a legenda e a tarja não); repita na "Tela inteira". **No editor de um vídeo longo** (roteiro 25): marque **Recortar o vídeo**, use o quadro, faça uma caixa retangular de outro formato (deve ficar inteira sobre fundo desfocado) e outra quase 16:9 (preenche), renderize e confira o recorte no vídeo todo, a legenda e os títulos de momento por cima, o resumo e a chamada final sem recorte, e o espelhar junto; reabra o editor (o recorte volta marcado), arraste a linha branca da prévia (câmera entre 300 e 1100 px), troque a ordem, ligue e desligue tarja, legenda, resumo e chamada final e clique em **Renderizar de novo**: o corte deve ser refeito com a câmera e o gameplay exatamente nas caixas escolhidas, e o selo deve mudar para "editado". Confira a legenda (dentro da tarja; sem tarja, na junção dos blocos), o resumo e a chamada final. Teste: "Tela inteira" (legenda embaixo do vídeo, título na tarja de cima), editar o mesmo corte uma segunda vez (parte do bruto, não do editado), salvar um layout e aplicá-lo em outro corte, descartar um corte editado (o bruto vai junto) e uma renderização que falha (o corte deve continuar como estava).
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

**Aba "Vídeos longos":** os vídeos de melhores momentos (horizontais) com player, momentos, edição e as mesmas ações dos cortes. Veja a seção "Vídeo longo" na parte 5.

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

**Núcleo do monitor (pacote `monitor/`; a tela o inicia por você, mas também roda sozinho no terminal).** Um processo por live: `python -m monitor --url https://www.twitch.tv/NOME` (só roda se o canal estiver **ativo** na lista; confere de novo a cada minuto e para se você suspender). Ele grava a live de forma contínua (um único ffmpeg, sem buracos), mede o volume de cada trecho de 4 s e escuta o chat (opcional: `pip install chat-downloader`; sem ele o áudio segue sozinho). Um volume bem acima do **normal daquela live**, uma explosão de mensagens no chat ou palavras de reação (`kkkkk`, "meu deus", "clipa") viram sinais; os sinais próximos viram um **momento**, com nota de 0 a 10, e a janela do corte (35 s antes e 10 s depois do pico) é ancorada **no horário do pico**. Cada momento é registrado em `cortes_prontos\_lives\ID\momentos.jsonl` e vira um **corte candidato** em `cortes_prontos\_lives\ID\momentos\` (os 30 mais recentes). O estado fica em `status.json` na mesma pasta; para parar, crie o arquivo `parar.flag` lá (ou Ctrl+C); `pausar.flag` pausa a detecção sem parar de gravar. Opções: `--sensibilidade baixa|media|alta` (alta pega mais momentos; o limite é de **12, 20 e 30 cortes por hora**, e só contam os momentos de nota **6,5 ou mais**: os fracos, que seriam descartados, não gastam a cota; se mesmo assim a cota acabar, o `descartes` do `status.json` mostra `limite_por_hora`), `--sem-cortes-candidatos` (só detecta e registra), `--sem-chat`. **IA e corte bruto.** Cada momento detectado vira um corte candidato e passa por três passos: (1) a fala do corte é transcrita (Whisper do Groq); (2) a IA lê a transcrição e escreve o título, a pergunta e o motivo (só com base no que foi dito; ela não vê a imagem e **não veta nada**); (3) quem decide se o corte entra é a **nota do momento** (a que o painel mostra, de 0 a 10): **abaixo de 6,5 o corte é descartado automaticamente** (nem arquivo sobra); **de 6,5 a 7,5** vai para **Cortes prontos** com a marcação **"Potencial Médio"**; **de 7,6 a 10** vai com **"Potencial Alto"**. O corte **bruto** (sem layout, tarja nem legenda) entra com a etiqueta **"live · canal"**, no mesmo formato do modo Gameplay, e o arquivo se chama `CorteLive_AAAAMMDD_HHMMSS_notaN.N.mp4`: dali você usa **Editar**, a linha do tempo e **Aprovar** como sempre. A legenda já vem com o tempo de cada palavra. **Sem `GROQ_API_KEY` no `.env`, sem fala ou com a IA fora do ar**, o corte entra do mesmo jeito (pela nota), só que sem legenda e com título padrão; o painel avisa "IA desligada". Para só detectar sem enviar cortes: `--sem-corte-bruto`.

**Testar sem uma live de verdade (modo replay):** `python -m monitor --replay MEU_VIDEO.mp4` toca um vídeo qualquer em tempo real, como se fosse uma live, e roda o monitor inteiro sobre ele (não precisa estar na lista, porque não há canal). Para simular o chat, use `--chat-arquivo chat.jsonl` com uma linha por mensagem: `{"t": 125.0, "texto": "KKKKKKK meu deus"}` (`t` = segundos desde o início do vídeo; o chat só dispara depois de uns 2 minutos "aprendendo o normal").

Roteiros de teste: 29. **Reeditar:** em um corte já editado e em um vídeo longo já editado o botão deve dizer **Reeditar** (e **Editar** nos que ainda estão brutos); ao abrir, o título da janela diz "Reeditar" e as opções (layout, caixas, trechos da linha do tempo, legenda, recorte, espelhar) voltam como na última edição. Altere algo e **Renderizar de novo**: o resultado parte do bruto (não do editado). Clique em **Recomeçar do bruto** (pede confirmação): tudo volta ao padrão, o título e a descrição ficam, e nada é refeito até renderizar; o botão não aparece em vídeo que nunca foi editado. Um corte antigo sem bruto continua avisando que não dá para editar o layout. 28. **Descrição:** em um corte, abra **Descrição para postar**, clique em **Sugerir descrições**: até 3 opções coerentes com a fala (1 a 2 frases e hashtags, sem inventar nada); clique numa (vai para o campo), edite, **Salvar descrição** (o selo confirma), clique em **Atualizar** e confira que continua lá, e **Copiar descrição** (cole em um bloco de notas). Apague o texto e salve (some). Faça o mesmo em um vídeo longo: cada opção deve terminar com as **marcas de tempo dos capítulos**; confira que batem com o vídeo (principalmente depois de editar com resumo no início e com um momento excluído) e cole a descrição de teste no YouTube (não publicado) para ver se os capítulos aparecem. Teste sem `GROQ_API_KEY` (a tela diz o motivo, mas dá para escrever e salvar à mão), um corte antigo sem versão bruta (a descrição funciona) e renderizar de novo com a descrição salva (ela não se perde). 27. **Dois streamers no vídeo longo:** em Gerar cortes, marque "Também criar um vídeo longo", cole o link de um streamer em "Link do vídeo" e o de **outro streamer** em "Segundo link" (ambos com fala animada e de 40 min ou mais), 3 cortes, 8 min: o log mostra a análise do primeiro, os 3 cortes (só do primeiro vídeo), o bloco "SEGUNDO VÍDEO" (download, transcrição, "Segundo streamer: ...") e "VÍDEO LONGO ... dois streamers" com "N momento(s) do primeiro vídeo e M do segundo, intercalados". Assista: os momentos alternam entre os dois streamers, cada legenda bate com a fala do streamer da vez, não há cena repetida nem estalo nas emendas, o card mostra "2 streamers" e o nome de cada um (e `[nome]` em cada momento), e o título é "Melhores momentos de A e B". Confira o `.json` (`fontes`, `streamer` por momento) e o **resumo narrado** depois de editar (cita os dois nomes?). Teste: preencher o nome do segundo streamer (vence o do canal); 0 cortes; o mesmo link nos dois campos (ignorado); um segundo link inválido (a tela recusa) e um que não baixa (o longo sai só com o primeiro e o log explica); um segundo vídeo com pouca graça (o primeiro completa). Anote **quanto tempo a mais** levou por causa do segundo download. 26. **Vídeo longo de uma live:** com o monitor tendo gerado pelo menos 6 cortes de um canal (nota 6,5 ou mais), vá em **Monitor de lives**, escolha 4 min e "todos os cortes" e clique em **Montar vídeo longo**: a tela volta para "Gerar cortes" e o log mostra "VÍDEO LONGO DA LIVE", quantos cortes o canal tem, os escolhidos em ordem e "Vídeo longo bruto salvo". Em **Vídeos longos** o card aparece com os momentos (o primeiro é o corte mais antigo), o vídeo toca sem cena repetida entre um corte e outro e a legenda (ao editar) bate com a fala. Confira o `.json`: `fonte.tipo` = `live`, `fonte.cortes` com os nomes `CorteLive_...`, e que os cortes continuam em Cortes prontos. Teste: canal **sem cortes** (mensagem clara, nada é gerado), período curto demais (poucos cortes: sai mais curto ou avisa que não dá), **suspender o canal** e tentar de novo (recusa com a mensagem), e montar com uma execução já em andamento (recusa). 25. **Aba Vídeos longos e edição:** abra um vídeo longo e clique em **Editar**: o menu mostra o player, a linha do tempo com a **onda do áudio** e **um marcador amarelo e numerado por momento**, a lista "Momentos do vídeo", o título do vídeo (com **Sugerir outros títulos**) e as opções (legenda, título de cada momento, resumo, chamada final); **não** deve haver layout, caixas de câmera nem tarja. Clique em **Ir** (o cursor e o player vão ao momento), **Excluir momento** (o trecho fica listrado e o vídeo pula por cima ao tocar; o botão vira Restaurar) e use zoom, dividir e Desfazer. **Renderizar de novo**: o selo mostra a porcentagem, o resultado tem 1920x1080, legenda no tempo certo, o título do momento aparecendo uns 4 s no começo de cada um (e **nenhum** título dos momentos excluídos), o resumo narrado no início, a chamada final no fim, volume uniforme e fade no fim; o card passa a "editado". Anote **quanto tempo levou** (e o tamanho do arquivo) para um vídeo de 8 e de 12 min. Teste também: renderizar com tudo desligado, editar uma segunda vez (parte do bruto, não do editado), título novo + Copiar título, aprovar/descartar, cancelar a renderização (o vídeo fica como estava) e uma máquina sem Groq (o resumo some e o card diz por quê). 24. **Vídeo longo (gerar cortes):** com uma live gravada de uns 40 minutos ou mais, com fala animada e `GROQ_API_KEY` no `.env`, marque **"Também criar um vídeo longo"** (8 min) com 3 cortes: o terminal deve mostrar uma única análise por faixas, os 3 cortes e depois o bloco "VÍDEO LONGO" com a lista dos momentos (nota 6,5 ou mais, ordem cronológica) e "Vídeo longo bruto salvo". Em `videos_longos\_brutos\` o `.mp4` deve ter entre uns 7 e 9 minutos, tocar sem travar, ser **horizontal (16:9)**, ter som nas emendas sem estalo e mostrar, na ordem, momentos animados do vídeo (confira com os horários do `.json`, `inicio_video`). Teste: **0 cortes** (só o longo); duração 4 e 12 (mínimo e máximo); um vídeo com pouca graça (deve sair mais curto e avisar, ou não sair se houver menos de 2 momentos bons); um vídeo de até 90 s (não gera longo); sem `GROQ_API_KEY` (os cortes saem e o longo não); e **o mesmo vídeo de novo** (os cortes curtos não repetem, o longo pode usar os mesmos momentos). Fechar a interface no meio não deve deixar `ffmpeg` aberto. Anote quanto tempo levou e o tamanho do arquivo. 23. **Título do corte:** abra **Editar** em um corte: no bloco "Título do corte" aparecem o título e a pergunta atuais. Troque o texto à mão, clique em **Renderizar de novo** e confira que a tarja do vídeo e o título do card mudaram (o card ganha a etiqueta "título escolhido por você"). Clique em **Sugerir outros títulos**: devem aparecer até 5 opções diferentes e coerentes com a fala; clicar numa preenche os dois campos; **Voltar ao título atual** desfaz. Sem `GROQ_API_KEY`, ou em um corte sem transcrição, a tela mostra o motivo. Com a tarja desligada o título só muda no card. 22. **IA e corte bruto:** com `GROQ_API_KEY` no `.env` e uma live com fala animada (ou um replay), deixe o monitor assistir por 15 a 20 minutos: o painel deve mostrar "N corte(s) enviado(s) para Cortes prontos" e, em cada momento, "enviado (Potencial Alto/Médio)" ou "descartado: nota X, abaixo do mínimo de 6.5"; na aba **Cortes prontos** o corte aparece com o nome `CorteLive_..._notaN.N.mp4`, a etiqueta "live · canal" e a marcação **Potencial Alto** (nota 7,6 a 10) ou **Potencial Médio** (6,5 a 7,5), o vídeo toca, a transcrição e o título fazem sentido com o que foi dito, e **Editar** abre com a legenda e a linha do tempo funcionando. Nota abaixo de 6,5 não deixa arquivo nenhum. Sem a chave: o painel diz "IA desligada" e os cortes entram sem legenda, com título padrão. 21. **Assistir pela tela:** com um canal da Twitch **ao vivo** na lista, clique em "Iniciar monitor": o selo deve passar de "Iniciando" para "Conectando" e "Assistindo a live agora" em até uns 20 s, os números (tempo, sinais, mensagens do chat) devem subir sozinhos, e "Ver o que o monitor está fazendo" deve mostrar o log. Com um canal **fora do ar**: "Live fora do ar (verificando de novo)". Teste Pausar/Retomar, Parar (o selo volta a "Monitor parado"), iniciar dois canais ao mesmo tempo (o botão dos outros trava com o máximo), suspender o canal com o monitor rodando (ele para) e fechar a interface com um monitor rodando (nenhum `ffmpeg` deve ficar no Gerenciador de Tarefas). Sem ffmpeg em `bin\`, deve aparecer "Com problema: ffmpeg não encontrado". 19. **Lista de canais:** adicione um canal da Twitch e um do YouTube pelo `@nome` (ou os dois links de uma vez); o contador deve mostrar "2 de 2" e o campo travar; apague um e adicione outro; edite um (nome, observações), suspenda e reative; tente um link `watch?v=` do YouTube e um link de outro site (devem recusar com o motivo); feche e abra a interface: a lista continua. 20. **Núcleo (replay):** com um vídeo de uma live ou de gameplay com falas e gritos (de uns 5 a 10 minutos), rode `python -m monitor --replay VIDEO.mp4` e deixe terminar: confira que `status.json` fecha com `"estado": "parado"`, que `momentos.jsonl` tem momentos perto dos trechos mais altos do vídeo, que os cortes em `momentos\` têm cerca de 45 s e terminam logo depois do pico, e que o volume do vídeo não muda sozinho. Teste `--sensibilidade alta` e `baixa`, `parar.flag` no meio, e `python -m monitor --url` de um canal que **não** está na lista (deve sair com "BLOQUEADO"). Com uma live real **de um canal da lista e ao vivo**: o terminal deve mostrar "gravando a live"; se a live estiver fora do ar, "offline" e nova checagem a cada minuto.

Se algo falhar, mande o texto do andamento (aba "Gerar cortes") e o arquivo mais recente de `app\logs\`.

### Limitações desta versão

- **Não** busca métricas do YouTube nem publica vídeos: isso vem nas próximas fases.
- Uma execução por vez.
- Só roda a geração de cortes (o monitor de lives e o reparo continuam pelo `iniciar.bat`).
- Testes automáticos do servidor: `python -m unittest discover -s app/tests -v` (rodam sem internet e sem ffmpeg).
