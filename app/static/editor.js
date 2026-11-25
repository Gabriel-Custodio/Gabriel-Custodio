"use strict";
/* Menu "Editar" de cada corte: caixas de crop (câmera e gameplay) sobre um quadro do próprio corte bruto, prévia em
   retrato, opções (tarja, legenda, resumo, chamada final) e renderização. Usa $, api, post e carregarCortes de app.js.
   Todo texto vindo do servidor entra na página com textContent. */

const ED_LARGURA = 1080, ED_ALTURA = 1920, ED_FAIXA_ALTURA = 220;
const ED_CAM_MIN = 300, ED_CAM_MAX = 1100;
const ED_MIN_LARGURA = 0.05;                       // caixa nunca menor que 5% da largura do quadro
const ED_MIN_ALTURA = 0.05;                        // nem que 5% da altura
let ED_Z = 10;

const ED = {
  lib: "cortes",                                   // "cortes" (vertical, caixas) ou "longos" (vídeo longo horizontal)
  momentos: [],                                    // vídeo longo: os momentos (para a lista ao lado da linha do tempo)
  corte: null,                                     // o corte que está sendo editado (objeto da lista)
  cam: { x: 0.74, y: 0.35, w: 0.24, h: 0.2 },
  game: { x: 0.2, y: 0, w: 0.5, h: 1 },
  rec: { x: 0.1, y: 0.1, w: 0.8, h: 0.8 },        // vídeo longo: recorte livre do quadro
  camAltura: 600,
  ordem: "cam_primeiro",
  tarja: true,
  manter: false,                                   // ao trocar de quadro, mantém as caixas
  timer: null,
};

const edImg = $("ed-img");
const edCanvas = $("ed-retrato");
const edCtx = edCanvas.getContext("2d");
const edDlg = $("dlg-editar");
const edVideo = $("ed-video");

const edLongo = () => ED.lib === "longos";
const edApi = (acao) => `/api/${edLongo() ? "longos" : "cortes"}/${acao}`;

/* Mostra só o que vale para o tipo de vídeo: data-so="curto" (cortes verticais) ou data-so="longo" (vídeo longo horizontal) */
function edModo(lib) {
  ED.lib = lib;
  for (const el of document.querySelectorAll("[data-so]")) el.hidden = el.dataset.so !== (lib === "longos" ? "longo" : "curto");
}

function edErro(msg) { $("ed-erro").textContent = msg || ""; $("ed-erro").hidden = !msg; }
function edEstado(msg) { $("ed-estado").textContent = msg || ""; $("ed-estado").hidden = !msg; }

// ---------------------------------------------------------------- geometria
const edFaixa = () => (ED.tarja ? ED_FAIXA_ALTURA : 0);           // sem tarja, os blocos ficam colados
const edGameAltura = () => ED_ALTURA - ED.camAltura - edFaixa();
const edAlturaDoBloco = (tipo) => (tipo === "cam" ? ED.camAltura : edGameAltura());
const edImgOk = () => edImg.naturalWidth > 0 && edImg.naturalHeight > 0;

/* h/w (em frações do quadro) que mantém a proporção do espaço da caixa no vídeo vertical (caixa inicial e "ajustar") */
function edRazao(tipo) {
  const A = ED_LARGURA / edAlturaDoBloco(tipo);
  return edImg.naturalWidth / (A * edImg.naturalHeight);
}

/* A caixa pode ter QUALQUER formato (uma webcam retangular, por exemplo). Se o formato for diferente do espaço dela no
   vídeo, o recorte inteiro é encaixado no espaço, com o resto preenchido por um fundo desfocado (nada é cortado nem esticado).
   Dentro de 3% de diferença ele só preenche o espaço, igual ao servidor (ENCAIXE_TOLERANCIA). */
const ED_ENCAIXE_TOLERANCIA = 0.03;
const ED_LARGURA_LONGO = 1920, ED_ALTURA_LONGO = 1080;
function edProporcaoDiferente(tipo) {
  const c = ED[tipo];
  if (!edImgOk() || c.w <= 0 || c.h <= 0) return false;
  const caixa = (c.w * edImg.naturalWidth) / (c.h * edImg.naturalHeight);
  return Math.abs(caixa / (ED_LARGURA / edAlturaDoBloco(tipo)) - 1) > ED_ENCAIXE_TOLERANCIA;
}

/* Mantém a caixa dentro do quadro (largura e altura livres, com um mínimo) */
function edAjustar(tipo) {
  const c = ED[tipo];
  c.w = Math.min(Math.max(c.w, ED_MIN_LARGURA), 1);
  c.h = Math.min(Math.max(c.h, ED_MIN_ALTURA), 1);
  c.x = Math.min(Math.max(c.x, 0), 1 - c.w);
  c.y = Math.min(Math.max(c.y, 0), 1 - c.h);
}

// ---------------------------------------------------------------- desenho
function edPosicionarCaixas() {
  for (const tipo of ["cam", "game"]) {
    const el = $(`ed-${tipo}`), c = ED[tipo];
    el.style.left = `${c.x * 100}%`;
    el.style.top = `${c.y * 100}%`;
    el.style.width = `${c.w * 100}%`;
    el.style.height = `${c.h * 100}%`;
  }
}

function edBlocos() {
  const camH = ED.camAltura, gameH = edGameAltura(), f = edFaixa();
  return ED.ordem === "cam_primeiro"
    ? { cam: 0, faixa: camH, game: camH + f, camH, gameH, topo: camH }
    : { game: 0, faixa: gameH, cam: gameH + f, camH, gameH, topo: gameH };
}

/* Desenha o recorte `c` no bloco (dx, dy, dw, dh) da prévia: preenchendo o bloco, ou (encaixe) inteiro sobre um fundo desfocado.
   Com "Espelhar" ligado o conteúdo sai invertido, como no vídeo final (tarja e legenda não). */
function edDesenharBloco(c, W, H, dx, dy, dw, dh, encaixe, ctx = edCtx, img = edImg) {
  const sx = c.x * W, sy = c.y * H, sw = c.w * W, sh = c.h * H;
  ctx.save();
  ctx.beginPath(); ctx.rect(dx, dy, dw, dh); ctx.clip();
  if ($("ed-espelhar").checked) { ctx.translate(dx + dw, 0); ctx.scale(-1, 1); ctx.translate(-dx, 0); }
  if (!encaixe) {
    ctx.drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh);
  } else {
    const cobre = Math.max(dw / sw, dh / sh);                // fundo: o mesmo recorte esticado até cobrir o bloco, desfocado
    try { ctx.filter = "blur(6px)"; } catch (_) { /* sem filtro: fica só mais escuro */ }
    ctx.drawImage(img, sx, sy, sw, sh, dx + (dw - sw * cobre) / 2, dy + (dh - sh * cobre) / 2, sw * cobre, sh * cobre);
    ctx.filter = "none";
    ctx.fillStyle = "rgba(0,0,0,.25)";
    ctx.fillRect(dx, dy, dw, dh);
    const cabe = Math.min(dw / sw, dh / sh);                 // frente: o recorte inteiro, centralizado
    ctx.drawImage(img, sx, sy, sw, sh, dx + (dw - sw * cabe) / 2, dy + (dh - sh * cabe) / 2, sw * cabe, sh * cabe);
  }
  ctx.restore();
}

function edDesenharRetrato() {
  const k = edCanvas.width / ED_LARGURA, b = edBlocos();
  edCtx.fillStyle = "#000";
  edCtx.fillRect(0, 0, edCanvas.width, edCanvas.height);
  if (edImgOk()) {
    const W = edImg.naturalWidth, H = edImg.naturalHeight;
    for (const tipo of ["cam", "game"]) {
      edDesenharBloco(ED[tipo], W, H, 0, b[tipo] * k, edCanvas.width, (tipo === "cam" ? b.camH : b.gameH) * k, edProporcaoDiferente(tipo));
    }
  }
  if (ED.tarja) {
    edCtx.fillStyle = "#d91e18";
    edCtx.fillRect(0, b.faixa * k, edCanvas.width, ED_FAIXA_ALTURA * k);
    edCtx.fillStyle = "#fff";
    edCtx.textAlign = "center";
    if ($("ed-legenda").checked) {
      edCtx.font = "bold 13px system-ui, sans-serif";
      edCtx.fillText("LEGENDA", edCanvas.width / 2, (b.faixa + 100) * k + 4);
    }
    edCtx.font = "bold 9px system-ui, sans-serif";
    edCtx.fillText("CURTA · COMENTE · COMPARTILHE", edCanvas.width / 2, (b.faixa + 178) * k);
  } else if ($("ed-legenda").checked) {
    edCtx.fillStyle = "#fff";
    edCtx.textAlign = "center";
    edCtx.font = "bold 13px system-ui, sans-serif";
    edCtx.strokeStyle = "#000"; edCtx.lineWidth = 3;
    edCtx.strokeText("LEGENDA", edCanvas.width / 2, b.topo * k + 5);
    edCtx.fillText("LEGENDA", edCanvas.width / 2, b.topo * k + 5);
  }
  // linha de arraste entre o bloco de cima e a faixa (ou o outro bloco)
  const y = b.topo * k;
  edCtx.strokeStyle = "#fff";
  edCtx.lineWidth = 2;
  edCtx.setLineDash([6, 4]);
  edCtx.beginPath(); edCtx.moveTo(0, y); edCtx.lineTo(edCanvas.width, y); edCtx.stroke();
  edCtx.setLineDash([]);
  edCtx.fillStyle = "#fff";
  edCtx.fillRect(edCanvas.width / 2 - 16, y - 3, 32, 6);
}

/* Quantas vezes a câmera é esticada até a largura da tela (1080 px). Acima de ~2,2x ela perde definição. */
function edAmpliacao() {
  if (!edImgOk() || ED.cam.w <= 0 || ED.cam.h <= 0) return 0;
  const largura = ED.cam.w * edImg.naturalWidth, altura = ED.cam.h * edImg.naturalHeight;
  return edProporcaoDiferente("cam") ? Math.min(ED_LARGURA / largura, ED.camAltura / altura) : ED_LARGURA / largura;
}

function edNitidez() {
  const a = edAmpliacao(), W = edImg.naturalWidth, H = edImg.naturalHeight;
  let nivel = "ok", texto = `Câmera ampliada ${a.toFixed(1)}x: nítida.`;
  if (a > 3.2) {
    nivel = "erro";
    texto = `Câmera ampliada ${a.toFixed(1)}x: vai ficar borrada. Puxe os cantos da caixa da câmera para pegar uma área maior.`;
  } else if (a > 2.2) {
    nivel = "aviso";
    texto = `Câmera ampliada ${a.toFixed(1)}x: pode ficar um pouco macia. Uma caixa maior fica mais nítida.`;
  } else if (a > 1.6) {
    texto = `Câmera ampliada ${a.toFixed(1)}x: boa.`;
  }
  if (W < 1800 && H < 1000) {
    texto += ` O vídeo de origem tem só ${W}x${H}; com 1080p a câmera ficaria bem mais nítida (instale o Node.js e gere o corte de novo).`;
    if (nivel === "ok") nivel = "aviso";
  } else {
    texto += ` (origem ${W}x${H})`;
  }
  return { nivel, texto };
}

function edAtualizar() {
  if (!edImgOk()) return;
  edAjustar("cam");
  edAjustar("game");
  edPosicionarCaixas();
  edDesenharRetrato();
  const n = edNitidez();
  const el = $("ed-nitidez");
  el.className = `ed-nitidez ${n.nivel}`;
  el.textContent = n.texto;
}

// ---------------------------------------------------------------- mover e redimensionar caixas
for (const tipo of ["cam", "game", "rec"]) {
  const el = $(`ed-${tipo}`);
  for (const canto of ["nw", "ne", "sw", "se", "n", "s", "e", "w"]) {
    const h = document.createElement("span");
    h.className = `ed-alca ${canto}`;
    h.dataset.canto = canto;
    el.appendChild(h);
  }
  el.addEventListener("pointerdown", (ev) => edIniciarArraste(ev, tipo, el));
}

function edPonto(ev, tipo) {
  const r = $(tipo === "rec" ? "ed-rec-paisagem" : "ed-paisagem").getBoundingClientRect();
  return { x: (ev.clientX - r.left) / r.width, y: (ev.clientY - r.top) / r.height };
}

function edIniciarArraste(ev, tipo, el) {
  if (tipo === "rec" ? !edRecImgOk() : !edImgOk()) return;
  ev.preventDefault();
  const canto = ev.target.dataset ? ev.target.dataset.canto : undefined;
  const p0 = edPonto(ev, tipo), c0 = { ...ED[tipo] };
  ED_Z += 1;
  el.style.zIndex = String(ED_Z);                     // a caixa mexida fica por cima (sem mexer no DOM: isso pode interromper o arraste)
  try { el.setPointerCapture(ev.pointerId); } catch (_) { /* sem captura: os eventos seguem no próprio elemento */ }
  const mover = (e) => {
    const p = edPonto(e, tipo), c = ED[tipo];
    if (!canto) {
      c.x = c0.x + (p.x - p0.x);
      c.y = c0.y + (p.y - p0.y);
    } else {
      // o lado (ou canto) oposto fica fixo; cada lado puxado mexe só no seu eixo. Com Shift, a proporção da caixa é mantida.
      const dx = canto.includes("e") ? 1 : canto.includes("w") ? -1 : 0, dy = canto.includes("s") ? 1 : canto.includes("n") ? -1 : 0;
      const ax = dx > 0 ? c0.x : c0.x + c0.w, ay = dy > 0 ? c0.y : c0.y + c0.h;
      const folgaX = dx > 0 ? 1 - ax : ax, folgaY = dy > 0 ? 1 - ay : ay;
      let w = dx ? Math.min(Math.max(ED_MIN_LARGURA, dx * (p.x - ax)), folgaX) : c0.w;
      let h = dy ? Math.min(Math.max(ED_MIN_ALTURA, dy * (p.y - ay)), folgaY) : c0.h;
      if (e.shiftKey && dx && dy) {
        const R = c0.h / c0.w;
        w = Math.min(w, folgaY / R); h = w * R;
        if (h > folgaY) { h = folgaY; w = h / R; }
      }
      c.w = w; c.h = h;
      c.x = dx > 0 ? ax : dx < 0 ? ax - w : c0.x;
      c.y = dy > 0 ? ay : dy < 0 ? ay - h : c0.y;
    }
    if (tipo === "rec") edAtualizarRecorte(); else edAtualizar();
  };
  const soltar = () => {
    el.removeEventListener("pointermove", mover);
    el.removeEventListener("pointerup", soltar);
    el.removeEventListener("pointercancel", soltar);
  };
  el.addEventListener("pointermove", mover);
  el.addEventListener("pointerup", soltar);
  el.addEventListener("pointercancel", soltar);
}

// ---------------------------------------------------------------- linha divisória no retrato
edCanvas.addEventListener("pointerdown", (ev) => {
  if (!edImgOk()) return;
  const r = edCanvas.getBoundingClientRect(), k = edCanvas.width / ED_LARGURA, esc = r.height / edCanvas.height;
  const yTela = (ev.clientY - r.top) / esc;
  if (Math.abs(yTela - edBlocos().topo * k) > 12) return;
  ev.preventDefault();
  try { edCanvas.setPointerCapture(ev.pointerId); } catch (_) { /* idem */ }
  const mover = (e) => {
    const topo = Math.round(((e.clientY - r.top) / esc) / k / 2) * 2;
    const camAltura = ED.ordem === "cam_primeiro" ? topo : ED_ALTURA - edFaixa() - topo;
    ED.camAltura = Math.min(Math.max(camAltura, ED_CAM_MIN), ED_CAM_MAX);
    edAtualizar();
  };
  const soltar = () => {
    edCanvas.removeEventListener("pointermove", mover);
    edCanvas.removeEventListener("pointerup", soltar);
    edCanvas.removeEventListener("pointercancel", soltar);
  };
  edCanvas.addEventListener("pointermove", mover);
  edCanvas.addEventListener("pointerup", soltar);
  edCanvas.addEventListener("pointercancel", soltar);
});
edCanvas.addEventListener("pointermove", (ev) => {
  if (!edImgOk()) return;
  const r = edCanvas.getBoundingClientRect(), k = edCanvas.width / ED_LARGURA, esc = r.height / edCanvas.height;
  edCanvas.style.cursor = Math.abs((ev.clientY - r.top) / esc - edBlocos().topo * k) <= 12 ? "row-resize" : "default";
});

document.querySelectorAll('input[name="ed-ordem"]').forEach((el) => el.addEventListener("change", () => {
  ED.ordem = document.querySelector('input[name="ed-ordem"]:checked').value;
  edAtualizar();
}));
$("ed-tarja").addEventListener("change", () => { ED.tarja = $("ed-tarja").checked; edAtualizar(); });
$("ed-legenda").addEventListener("change", edAtualizar);
$("ed-espelhar").addEventListener("change", () => { edAtualizar(); edAtualizarRecorte(); });

/* Deixa o bloco da câmera com a MESMA proporção da caixa: o recorte preenche o bloco inteiro, sem fundo desfocado nas laterais */
$("ed-ajustar-bloco").addEventListener("click", () => {
  if (!edImgOk()) return;
  const c = ED.cam, alvo = ED_LARGURA * (c.h * edImg.naturalHeight) / (c.w * edImg.naturalWidth);
  ED.camAltura = Math.min(Math.max(Math.round(alvo / 2) * 2, ED_CAM_MIN), ED_CAM_MAX);
  edAtualizar();
});

// ---------------------------------------------------------------- layout: tela inteira ou câmera + gameplay
const edLayout = () => document.querySelector('input[name="ed-layout"]:checked').value;

function edMostrarLayout() {
  if (edLongo()) return;                                    // o vídeo longo não tem layout: é sempre a tela inteira, na horizontal
  const empilhado = edLayout() === "empilhado";
  $("ed-caixas").hidden = !empilhado;
  $("ed-inteiro-info").hidden = empilhado;
  $("ed-pegar-quadro").hidden = !empilhado;                 // o vídeo e a linha do tempo servem aos dois layouts
  $("ed-tarja-rotulo").textContent = empilhado
    ? "Tarja vermelha entre a câmera e o gameplay (com “CURTA · COMENTE · COMPARTILHE”)"
    : "Tarja vermelha acima do vídeo (título do corte e “CURTA · COMENTE · COMPARTILHE”)";
  if (empilhado) edAtualizar();
}
document.querySelectorAll('input[name="ed-layout"]').forEach((el) => el.addEventListener("change", edMostrarLayout));

// ---------------------------------------------------------------- quadro do próprio corte bruto
function edCaixasPadrao() {
  // caixas iniciais com a proporção do espaço delas no vídeo (a pessoa muda o formato depois, se quiser)
  ED.cam = { x: 0.74, y: 0.35, w: 0.24, h: 0.24 * edRazao("cam") };
  const larguraGame = 0.8 / edRazao("game");                       // 80% da altura: sobra espaço para mover nos dois sentidos
  ED.game = { x: 0, y: 0, w: larguraGame, h: larguraGame * edRazao("game") };
  edAjustar("cam");
  edAjustar("game");
  ED.game.x = (1 - ED.game.w) / 2;
  ED.game.y = (1 - ED.game.h) / 2;
}

function edMostrarImagem(src, manter) {
  edImg.onload = () => {
    if (!manter) edCaixasPadrao();
    $("ed-area-caixas").hidden = false;
    edAtualizar();
    edEstado("Quadro carregado");
  };
  edImg.onerror = () => { edErro("Não foi possível usar esse quadro."); edEstado(""); };
  edImg.src = src;
}

/* Congela o quadro que o vídeo está mostrando (o navegador desenha o quadro; nada é baixado) */
function edPegarQuadro(manter) {
  const w = edVideo.videoWidth, h = edVideo.videoHeight;
  if (!w || !h) { edErro("O vídeo ainda está carregando."); return; }
  edErro("");
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  c.getContext("2d").drawImage(edVideo, 0, 0, w, h);
  edMostrarImagem(c.toDataURL("image/jpeg", 0.92), manter);
}
$("ed-pegar-quadro").addEventListener("click", () => edPegarQuadro(true));

// ---------------------------------------------------------------- vídeo longo: recorte livre do quadro
/* Uma única caixa, de qualquer formato, vale para o vídeo inteiro. 16:9 (ou quase) preenche o quadro; outro formato aparece
   inteiro, centralizado sobre uma cópia desfocada dele mesmo (igual ao servidor e aos blocos dos cortes). */
const edRecImg = $("ed-rec-img");
const edRecCanvas = $("ed-rec-previa");
const edRecCtx = edRecCanvas.getContext("2d");
const edRecImgOk = () => edRecImg.naturalWidth > 0 && edRecImg.naturalHeight > 0;
const ED_REC_PADRAO = { x: 0.1, y: 0.1, w: 0.8, h: 0.8 };

function edRecProporcaoDiferente() {
  const c = ED.rec;
  if (!edRecImgOk() || c.w <= 0 || c.h <= 0) return false;
  const caixa = (c.w * edRecImg.naturalWidth) / (c.h * edRecImg.naturalHeight);
  return Math.abs(caixa / (ED_LARGURA_LONGO / ED_ALTURA_LONGO) - 1) > ED_ENCAIXE_TOLERANCIA;
}

function edRecNitidez() {
  const cw = ED.rec.w * edRecImg.naturalWidth, ch = ED.rec.h * edRecImg.naturalHeight;
  const fator = edRecProporcaoDiferente() ? Math.min(ED_LARGURA_LONGO / cw, ED_ALTURA_LONGO / ch)
                                           : Math.max(ED_LARGURA_LONGO / cw, ED_ALTURA_LONGO / ch);
  let nivel = "ok", texto = `Recorte ampliado ${fator.toFixed(1)}x: nítido.`;
  if (fator > 3.2) { nivel = "erro"; texto = `Recorte ampliado ${fator.toFixed(1)}x: vai ficar borrado. Puxe os cantos da caixa para pegar uma área maior.`; }
  else if (fator > 2.2) { nivel = "aviso"; texto = `Recorte ampliado ${fator.toFixed(1)}x: pode ficar um pouco macio. Uma caixa maior fica mais nítida.`; }
  else if (fator > 1.6) texto = `Recorte ampliado ${fator.toFixed(1)}x: bom.`;
  return { nivel, texto: `${texto} (origem ${edRecImg.naturalWidth}x${edRecImg.naturalHeight})` };
}

function edAtualizarRecorte() {
  if (!edLongo() || !$("ed-rec-usar").checked || !edRecImgOk()) return;
  edAjustar("rec");
  const el = $("ed-rec"), c = ED.rec;
  el.style.left = `${c.x * 100}%`; el.style.top = `${c.y * 100}%`;
  el.style.width = `${c.w * 100}%`; el.style.height = `${c.h * 100}%`;
  edRecCtx.fillStyle = "#000";
  edRecCtx.fillRect(0, 0, edRecCanvas.width, edRecCanvas.height);
  edDesenharBloco(c, edRecImg.naturalWidth, edRecImg.naturalHeight, 0, 0, edRecCanvas.width, edRecCanvas.height,
                  edRecProporcaoDiferente(), edRecCtx, edRecImg);
  const n = edRecNitidez();
  $("ed-rec-nitidez").className = `ed-nitidez ${n.nivel}`;
  $("ed-rec-nitidez").textContent = n.texto;
}

/* Congela o quadro que o vídeo está mostrando (o navegador desenha o quadro; nada é baixado) */
function edRecPegarQuadro(manter) {
  const w = edVideo.videoWidth, h = edVideo.videoHeight;
  if (!w || !h) { edErro("O vídeo ainda está carregando."); return false; }
  edErro("");
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  c.getContext("2d").drawImage(edVideo, 0, 0, w, h);
  edRecImg.onload = () => {
    if (!manter) ED.rec = { ...ED_REC_PADRAO };
    $("ed-rec-quadro-area").hidden = false;
    edAtualizarRecorte();
  };
  edRecImg.src = c.toDataURL("image/jpeg", 0.92);
  return true;
}

function edRecMostrar() {
  const ligado = $("ed-rec-usar").checked;
  $("ed-rec-area").hidden = !ligado;
  if (ligado && !edRecImgOk()) edRecPegarQuadro(false);
  else edAtualizarRecorte();
}
$("ed-rec-usar").addEventListener("change", edRecMostrar);
$("ed-rec-quadro").addEventListener("click", () => edRecPegarQuadro(true));
$("ed-rec-tudo").addEventListener("click", () => { ED.rec = { x: 0, y: 0, w: 1, h: 1 }; edAtualizarRecorte(); });

// ---------------------------------------------------------------- abrir e fechar
function edMarcar(nome, valor) {
  const el = document.querySelector(`input[name="${nome}"][value="${valor}"]`);
  if (el) el.checked = true;
}

function edAbrir(corte, lib = "cortes") {
  edModo(lib);
  ED.corte = corte;
  edErro(""); edEstado("");
  $("ed-log").hidden = true;
  $("ed-log").textContent = "";
  if (edLongo()) { edAbrirLongo(corte); return; }
  $("dlg-titulo").textContent = `Editar: ${corte.titulo || corte.nome}`;
  const op = corte.edicao && corte.edicao.opcoes;           // opções da última edição (se houve)
  edTituloAbrir(corte);
  edMarcar("ed-layout", op ? op.layout : "empilhado");
  $("ed-tarja").checked = op ? op.tarja : true;
  $("ed-legenda").checked = op ? op.legenda : true;
  $("ed-resumo").checked = op ? op.resumo : true;
  $("ed-cta").checked = op ? op.cta : true;
  $("ed-espelhar").checked = !!(op && op.espelhar);
  ED.tarja = $("ed-tarja").checked;
  ED.manter = false;
  if (op && op.layout === "empilhado") {
    ED.cam = { ...op.cam }; ED.game = { ...op.game }; ED.camAltura = op.cam_altura; ED.ordem = op.ordem;
    ED.manter = true;
  } else {
    ED.camAltura = 600; ED.ordem = "cam_primeiro";
  }
  edMarcar("ed-ordem", ED.ordem);
  $("ed-nome").value = "";
  $("ed-area-caixas").hidden = true;
  edImg.removeAttribute("src");
  edMostrarLayout();
  $("ed-renderizar").disabled = false;
  edVideo.onloadedmetadata = () => { edVideo.currentTime = Math.max(0.1, edVideo.duration * 0.4); };
  edVideo.onseeked = () => { edVideo.onseeked = null; edPegarQuadro(ED.manter); };   // 1º quadro: o meio do corte
  const src = `/video?nome=${encodeURIComponent(corte.nome)}&bruto=1&t=${Date.now()}`;
  TLUI.abrir(src, op && op.trechos);                         // linha do tempo: refaz os cortes da última edição
  edVideo.src = src;
  edCarregarLista();
  if (typeof edDlg.showModal === "function") edDlg.showModal(); else edDlg.setAttribute("open", "");
}

/* Vídeo longo: sem quadro nem caixas; a linha do tempo mostra um marcador por momento e a onda do áudio vem do servidor */
function edAbrirLongo(v) {
  const op = v.edicao && v.edicao.opcoes;
  $("dlg-titulo").textContent = `Editar vídeo longo: ${v.titulo || v.nome}`;
  edTituloAbrir(v);
  $("ed-legenda").checked = op ? op.legenda : true;
  $("ed-capitulos").checked = op ? op.capitulos : true;
  $("ed-resumo").checked = op ? op.resumo : true;
  $("ed-cta").checked = op ? op.cta : true;
  $("ed-espelhar").checked = !!(op && op.espelhar);
  $("ed-renderizar").disabled = false;
  edRecImg.removeAttribute("src");
  ED.rec = op && op.recorte ? { ...op.recorte } : { ...ED_REC_PADRAO };
  $("ed-rec-usar").checked = !!(op && op.recorte);
  $("ed-rec-area").hidden = !$("ed-rec-usar").checked;
  $("ed-rec-quadro-area").hidden = true;
  ED.momentos = (v.momentos || []).filter((m) => typeof m.inicio === "number" && typeof m.fim === "number");
  const nome = encodeURIComponent(v.nome);
  const src = `/video?nome=${nome}&lib=longos&bruto=1&t=${Date.now()}`;
  edVideo.onloadedmetadata = null;
  edVideo.onseeked = null;
  if (op && op.recorte) {                                   // reabre o recorte da última edição: pega um quadro do meio do vídeo
    edVideo.onloadedmetadata = () => { edVideo.currentTime = Math.max(0.1, edVideo.duration * 0.4); };
    edVideo.onseeked = () => { edVideo.onseeked = null; edRecPegarQuadro(true); };
  }
  TLUI.abrir(src, op && op.trechos, {
    ondaUrl: `/api/longos/onda?nome=${nome}`, zoomMax: 40,
    marcas: ED.momentos.map((m) => ({ n: m.n, a: m.inicio, b: m.fim, titulo: m.titulo })),
  });
  TLUI.aoMudar(edAtualizarMomentos);
  edVideo.src = src;
  edDesenharMomentos();
  if (typeof edDlg.showModal === "function") edDlg.showModal(); else edDlg.setAttribute("open", "");
}

/* lista de momentos ao lado da linha do tempo: ir até ele, excluir ou restaurar o momento inteiro */
function edDesenharMomentos() {
  $("ed-bloco-momentos").hidden = !ED.momentos.length;
  $("ed-momentos").replaceChildren(...ED.momentos.map((m, i) => {
    const li = document.createElement("li");
    const ir = document.createElement("button");
    ir.type = "button"; ir.className = "btn"; ir.textContent = `Ir · ${tlFormatar(m.inicio)}`;
    ir.addEventListener("click", () => TLUI.irPara(m.inicio));
    const txt = document.createElement("span");
    txt.className = "ed-mom-txt";
    const nota = typeof m.nota === "number" ? ` (nota ${m.nota.toFixed(1)})` : "";
    txt.textContent = `${m.n || i + 1}. ${m.titulo || "Momento"}${nota}`;      // texto vindo do servidor: só textContent
    if (m.motivo) txt.title = m.motivo;
    const alt = document.createElement("button");
    alt.type = "button"; alt.className = "btn alternar";
    alt.addEventListener("click", () => {
      edErro("");
      if (!TLUI.alternarIntervalo(m.inicio, m.fim)) edErro("O vídeo precisa ficar com pelo menos 1 segundo (e há um limite de trechos).");
    });
    li.append(ir, txt, alt);
    return li;
  }));
  edAtualizarMomentos();
}

function edAtualizarMomentos() {
  const itens = $("ed-momentos").children;
  for (let i = 0; i < itens.length; i++) {
    const m = ED.momentos[i], fora = TLUI.pronto() && TLUI.fracaoMantida(m.inicio, m.fim) < 0.5;
    itens[i].classList.toggle("excluido", fora);
    itens[i].querySelector(".alternar").textContent = fora ? "Restaurar momento" : "Excluir momento";
  }
}

function edFechar() {
  edVideo.pause();
  TLUI.fechar();
  edDlg.close();
}
$("ed-fechar").addEventListener("click", edFechar);
$("ed-fechar-x").addEventListener("click", edFechar);
edDlg.addEventListener("close", () => { edVideo.pause(); TLUI.fechar(); });

// ---------------------------------------------------------------- título do corte (campo e sugestões da IA)
function edTituloAbrir(corte) {
  const longo = edLongo();
  $("ed-titulo-h3").textContent = longo ? "Título do vídeo" : "Título do corte";
  $("ed-titulo-rotulo").textContent = longo ? "Título (até 100 caracteres)" : "Título (até 60 caracteres)";
  $("ed-titulo").maxLength = longo ? 100 : 60;
  $("ed-titulo-ajuda").textContent = longo
    ? "O título aparece no card do vídeo (e no botão “Copiar título”, para colar ao postar). Escreva o seu ou peça sugestões à IA, que lê só os títulos e os motivos dos momentos. Vale ao clicar em Renderizar de novo."
    : "O título e a pergunta aparecem na tarja do vídeo (se a tarja estiver ligada) e no card do corte. Escreva o seu ou peça sugestões à IA, que lê só a fala do corte. Vale ao clicar em Renderizar de novo.";
  ED.tituloInicial = corte.titulo || ""; ED.perguntaInicial = longo ? "" : (corte.pergunta || "");
  $("ed-titulo").value = ED.tituloInicial; $("ed-pergunta").value = ED.perguntaInicial;
  $("ed-sugestoes").replaceChildren();
  $("ed-sug-erro").hidden = true; $("ed-sug-estado").hidden = true;
  $("ed-sugerir").disabled = false; $("ed-sugerir").textContent = "Sugerir outros títulos";
  edTituloMudou();
}

function edTituloMudou() {
  $("ed-titulo-voltar").hidden = $("ed-titulo").value === ED.tituloInicial && $("ed-pergunta").value === ED.perguntaInicial;
}
$("ed-titulo").addEventListener("input", edTituloMudou);
$("ed-pergunta").addEventListener("input", edTituloMudou);
$("ed-titulo-voltar").addEventListener("click", () => {
  $("ed-titulo").value = ED.tituloInicial; $("ed-pergunta").value = ED.perguntaInicial;
  for (const b of document.querySelectorAll("#ed-sugestoes .btn")) b.classList.remove("escolhido");
  edTituloMudou();
});

function edMostrarSugestoes(itens) {
  $("ed-sugestoes").replaceChildren(...itens.map((s) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button"; b.className = "btn";
    b.textContent = s.pergunta ? `${s.titulo} · ${s.pergunta}` : s.titulo;     // texto vindo do servidor: só textContent
    b.addEventListener("click", () => {
      $("ed-titulo").value = s.titulo; $("ed-pergunta").value = s.pergunta || "";
      for (const o of document.querySelectorAll("#ed-sugestoes .btn")) o.classList.toggle("escolhido", o === b);
      edTituloMudou();
    });
    li.append(b);
    return li;
  }));
}

$("ed-sugerir").addEventListener("click", async () => {
  const nome = ED.corte && ED.corte.nome;
  if (!nome) return;
  $("ed-sug-erro").hidden = true;
  $("ed-sugerir").disabled = true; $("ed-sugerir").textContent = "Pedindo à IA…";
  $("ed-sug-estado").textContent = edLongo() ? "A IA está lendo os momentos do vídeo" : "A IA está lendo a fala do corte"; $("ed-sug-estado").hidden = false;
  try {
    const d = await post(edApi("titulos"), { nome });
    if (!ED.corte || ED.corte.nome !== nome) return;                          // trocou de corte enquanto esperava
    edMostrarSugestoes(d.titulos || []);
  } catch (e) {
    $("ed-sug-erro").textContent = e.message; $("ed-sug-erro").hidden = false;
  } finally {
    $("ed-sugerir").disabled = false; $("ed-sugerir").textContent = "Sugerir outros títulos";
    $("ed-sug-estado").hidden = true;
  }
});

// ---------------------------------------------------------------- renderizar de novo
function edOpcoes() {
  if (edLongo()) {
    const lo = { legenda: $("ed-legenda").checked, capitulos: $("ed-capitulos").checked,
                 resumo: $("ed-resumo").checked, cta: $("ed-cta").checked, espelhar: $("ed-espelhar").checked };
    if ($("ed-rec-usar").checked) lo.recorte = ED.rec;         // sem a opção ligada, o quadro inteiro
    const aparado = TLUI.trechos();                           // null = vídeo inteiro
    if (aparado) lo.trechos = aparado;
    const t = $("ed-titulo").value.trim();
    if (t && t !== ED.tituloInicial) lo.titulo = t;
    return lo;
  }
  const o = { layout: edLayout(), tarja: $("ed-tarja").checked, legenda: $("ed-legenda").checked,
              resumo: $("ed-resumo").checked, cta: $("ed-cta").checked, espelhar: $("ed-espelhar").checked };
  if (o.layout === "empilhado") {
    Object.assign(o, { cam: ED.cam, game: ED.game, cam_altura: ED.camAltura, ordem: ED.ordem });
  }
  const trechos = TLUI.trechos();                             // null = vídeo inteiro
  if (trechos) o.trechos = trechos;
  const titulo = $("ed-titulo").value.trim(), pergunta = $("ed-pergunta").value.trim();
  if (titulo && titulo !== ED.tituloInicial) o.titulo = titulo;                // só manda o que o usuário mudou
  if (pergunta && pergunta !== ED.perguntaInicial) o.pergunta = pergunta;
  return o;
}

$("ed-renderizar").addEventListener("click", async () => {
  edErro("");
  if (!TLUI.pronto()) { edErro("Espere o vídeo carregar."); return; }
  if (!edLongo() && edLayout() === "empilhado" && !edImgOk()) { edErro("Espere o quadro carregar."); return; }
  if (edLongo() && $("ed-rec-usar").checked && !edRecImgOk()) { edErro("Espere o quadro do recorte carregar (ou clique em Usar este quadro)."); return; }
  $("ed-renderizar").disabled = true;
  try {
    await post(edApi("editar"), { nome: ED.corte.nome, opcoes: edOpcoes() });
  } catch (e) {
    edErro(e.message);
    $("ed-renderizar").disabled = false;
    return;
  }
  edEstado(edLongo()
    ? "Renderizando… um vídeo longo leva vários minutos (o andamento aparece abaixo; o resumo e a chamada final usam voz da IA)"
    : "Renderizando… pode levar alguns minutos (o resumo e a chamada final usam voz da IA)");
  $("ed-log").hidden = false;
  $("ed-log").textContent = "";
  edAcompanhar();
});

function edAcompanhar() {
  if (ED.timer) clearInterval(ED.timer);
  let desde = 0, linhas = [];
  const consultar = async () => {
    try {
      const d = await api(`/api/log?desde=${desde}`);
      desde = d.proximo;
      linhas = linhas.concat(d.linhas).slice(-12);
      $("ed-log").textContent = linhas.join("\n");
      $("ed-log").scrollTop = $("ed-log").scrollHeight;
      const job = d.job;
      if (job && job.estado === "rodando") {                        // "⏳ Renderizando: 35% (4:12 de 12:00)": mostra a porcentagem no selo
        for (let i = linhas.length - 1; i >= 0; i--) {
          const m = /⏳ ([^:]+): (\d+)%/.exec(linhas[i]);
          if (m) { edEstado(`${m[1]}… ${m[2]}%`); break; }
        }
      }
      if (!job || job.estado === "rodando") return;
      clearInterval(ED.timer); ED.timer = null;
      $("ed-renderizar").disabled = false;
      if (job.estado === "concluido") {
        edEstado(edLongo() ? "Pronto! O vídeo longo foi renderizado de novo. Confira e aprove na lista."
                           : "Pronto! O corte foi renderizado de novo. Confira e aprove na lista.");
        await (edLongo() ? carregarLongos() : carregarCortes());
      } else {
        edEstado("");
        edErro(job.estado === "cancelado" ? "A renderização foi cancelada." : "A renderização falhou: o corte continua como estava. Veja o texto abaixo.");
      }
    } catch (e) {
      clearInterval(ED.timer); ED.timer = null;
      edErro(e.message);
      $("ed-renderizar").disabled = false;
    }
  };
  ED.timer = setInterval(consultar, 1000);
  consultar();
}

// ---------------------------------------------------------------- layouts salvos (por streamer)
let edLayouts = [];

$("ed-salvar").addEventListener("click", async () => {
  edErro("");
  const nome = $("ed-nome").value.trim();
  if (!nome) { edErro("Dê um nome ao layout para salvá-lo."); return; }
  if (!edImgOk()) { edErro("Espere o quadro carregar."); return; }
  try {
    const r = await post("/api/layouts", { nome, cam: ED.cam, game: ED.game, cam_altura: ED.camAltura,
                                           ordem: ED.ordem, tarja: ED.tarja });
    edEstado(`Layout “${r.layout.nome}” salvo`);
    await edCarregarLista();
  } catch (e) {
    edErro(e.message);
  }
});

async function edCarregarLista() {
  try {
    edLayouts = (await api("/api/layouts")).layouts || [];
  } catch (_) {
    edLayouts = [];
  }
  const lista = $("ed-lista");
  lista.replaceChildren();
  $("ed-vazio").hidden = edLayouts.length > 0;
  for (const l of edLayouts) {
    const li = document.createElement("li");
    const nome = document.createElement("span");
    nome.className = "ed-nome-item";
    nome.textContent = l.nome || l.slug;
    const mk = (texto, classe, fn) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = `btn ${classe || ""}`.trim(); b.textContent = texto;
      b.addEventListener("click", fn);
      return b;
    };
    li.append(nome, mk("Aplicar", "", () => edAplicar(l)), mk("Apagar", "perigo", () => edApagar(l)));
    lista.appendChild(li);
  }
}

function edAplicar(l) {
  edMarcar("ed-layout", "empilhado");
  ED.cam = { ...l.cam }; ED.game = { ...l.game }; ED.camAltura = l.cam_altura; ED.ordem = l.ordem;
  ED.tarja = l.tarja !== false;
  $("ed-tarja").checked = ED.tarja;
  edMarcar("ed-ordem", l.ordem);
  $("ed-nome").value = l.nome || "";
  edErro("");
  edMostrarLayout();
  edEstado(`Layout “${l.nome || l.slug}” aplicado`);
}

async function edApagar(l) {
  if (!confirm(`Apagar o layout salvo “${l.nome || l.slug}”?`)) return;
  try {
    await post("/api/layouts/apagar", { slug: l.slug });
    await edCarregarLista();
  } catch (e) {
    edErro(e.message);
  }
}
