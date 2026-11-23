"use strict";
/* Menu "Editar" de cada corte: caixas de crop (câmera e gameplay) sobre um quadro do próprio corte bruto, prévia em
   retrato, opções (tarja, legenda, resumo, chamada final) e renderização. Usa $, api, post e carregarCortes de app.js.
   Todo texto vindo do servidor entra na página com textContent. */

const ED_LARGURA = 1080, ED_ALTURA = 1920, ED_FAIXA_ALTURA = 220;
const ED_CAM_MIN = 300, ED_CAM_MAX = 1100;
const ED_MIN_LARGURA = 0.05;                       // caixa nunca menor que 5% da largura do quadro
let ED_Z = 10;

const ED = {
  corte: null,                                     // o corte que está sendo editado (objeto da lista)
  cam: { x: 0.74, y: 0.35, w: 0.24, h: 0.2 },
  game: { x: 0.2, y: 0, w: 0.5, h: 1 },
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

function edErro(msg) { $("ed-erro").textContent = msg || ""; $("ed-erro").hidden = !msg; }
function edEstado(msg) { $("ed-estado").textContent = msg || ""; $("ed-estado").hidden = !msg; }

// ---------------------------------------------------------------- geometria
const edFaixa = () => (ED.tarja ? ED_FAIXA_ALTURA : 0);           // sem tarja, os blocos ficam colados
const edGameAltura = () => ED_ALTURA - ED.camAltura - edFaixa();
const edAlturaDoBloco = (tipo) => (tipo === "cam" ? ED.camAltura : edGameAltura());
const edImgOk = () => edImg.naturalWidth > 0 && edImg.naturalHeight > 0;

/* h/w (em frações do quadro) que mantém a proporção do espaço da caixa no vídeo vertical */
function edRazao(tipo) {
  const A = ED_LARGURA / edAlturaDoBloco(tipo);
  return edImg.naturalWidth / (A * edImg.naturalHeight);
}

/* Recalcula a altura a partir da largura e mantém tudo dentro do quadro */
function edAjustar(tipo) {
  const c = ED[tipo], R = edRazao(tipo);
  c.w = Math.min(Math.max(c.w, ED_MIN_LARGURA), 1);
  c.h = c.w * R;
  if (c.h > 1) { c.h = 1; c.w = 1 / R; }
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

function edDesenharRetrato() {
  const k = edCanvas.width / ED_LARGURA, b = edBlocos();
  edCtx.fillStyle = "#000";
  edCtx.fillRect(0, 0, edCanvas.width, edCanvas.height);
  if (edImgOk()) {
    const W = edImg.naturalWidth, H = edImg.naturalHeight;
    for (const tipo of ["cam", "game"]) {
      const c = ED[tipo], altura = tipo === "cam" ? b.camH : b.gameH;
      edCtx.drawImage(edImg, c.x * W, c.y * H, c.w * W, c.h * H, 0, b[tipo] * k, edCanvas.width, altura * k);
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

function edAtualizar() {
  if (!edImgOk()) return;
  edAjustar("cam");
  edAjustar("game");
  edPosicionarCaixas();
  edDesenharRetrato();
}

// ---------------------------------------------------------------- mover e redimensionar caixas
for (const tipo of ["cam", "game"]) {
  const el = $(`ed-${tipo}`);
  for (const canto of ["nw", "ne", "sw", "se"]) {
    const h = document.createElement("span");
    h.className = `ed-alca ${canto}`;
    h.dataset.canto = canto;
    el.appendChild(h);
  }
  el.addEventListener("pointerdown", (ev) => edIniciarArraste(ev, tipo, el));
}

function edPonto(ev) {
  const r = $("ed-paisagem").getBoundingClientRect();
  return { x: (ev.clientX - r.left) / r.width, y: (ev.clientY - r.top) / r.height };
}

function edIniciarArraste(ev, tipo, el) {
  if (!edImgOk()) return;
  ev.preventDefault();
  const canto = ev.target.dataset ? ev.target.dataset.canto : undefined;
  const p0 = edPonto(ev), c0 = { ...ED[tipo] };
  ED_Z += 1;
  el.style.zIndex = String(ED_Z);                     // a caixa mexida fica por cima (sem mexer no DOM: isso pode interromper o arraste)
  try { el.setPointerCapture(ev.pointerId); } catch (_) { /* sem captura: os eventos seguem no próprio elemento */ }
  const mover = (e) => {
    const p = edPonto(e), c = ED[tipo];
    if (!canto) {
      c.x = c0.x + (p.x - p0.x);
      c.y = c0.y + (p.y - p0.y);
    } else {
      // o canto oposto fica fixo; a largura vem da posição horizontal do ponteiro, a altura segue a proporção
      const sx = canto.includes("e") ? 1 : -1, sy = canto.includes("s") ? 1 : -1;
      const ax = sx > 0 ? c0.x : c0.x + c0.w, ay = sy > 0 ? c0.y : c0.y + c0.h;
      const R = edRazao(tipo);
      let w = Math.max(ED_MIN_LARGURA, sx * (p.x - ax));
      w = Math.min(w, sx > 0 ? 1 - ax : ax, (sy > 0 ? 1 - ay : ay) / R);
      c.w = w; c.h = w * R;
      c.x = sx > 0 ? ax : ax - w;
      c.y = sy > 0 ? ay : ay - c.h;
    }
    edAtualizar();
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

// ---------------------------------------------------------------- layout: tela inteira ou câmera + gameplay
const edLayout = () => document.querySelector('input[name="ed-layout"]:checked').value;

function edMostrarLayout() {
  const empilhado = edLayout() === "empilhado";
  $("ed-caixas").hidden = !empilhado;
  $("ed-inteiro-info").hidden = empilhado;
  $("ed-tarja-rotulo").textContent = empilhado
    ? "Tarja vermelha entre a câmera e o gameplay (com “CURTA · COMENTE · COMPARTILHE”)"
    : "Tarja vermelha acima do vídeo (título do corte e “CURTA · COMENTE · COMPARTILHE”)";
  if (empilhado) edAtualizar();
}
document.querySelectorAll('input[name="ed-layout"]').forEach((el) => el.addEventListener("change", edMostrarLayout));

// ---------------------------------------------------------------- quadro do próprio corte bruto
function edCaixasPadrao() {
  ED.cam = { x: 0.74, y: 0.35, w: 0.24, h: 0 };
  ED.game = { x: 0, y: 0, w: 0.8 / edRazao("game"), h: 0 };   // 80% da altura: sobra espaço para mover nos dois sentidos
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

// ---------------------------------------------------------------- abrir e fechar
function edMarcar(nome, valor) {
  const el = document.querySelector(`input[name="${nome}"][value="${valor}"]`);
  if (el) el.checked = true;
}

function edAbrir(corte) {
  ED.corte = corte;
  edErro(""); edEstado("");
  $("ed-log").hidden = true;
  $("ed-log").textContent = "";
  $("dlg-titulo").textContent = `Editar: ${corte.titulo || corte.nome}`;
  const op = corte.edicao && corte.edicao.opcoes;           // opções da última edição (se houve)
  edMarcar("ed-layout", op ? op.layout : "empilhado");
  $("ed-tarja").checked = op ? op.tarja : true;
  $("ed-legenda").checked = op ? op.legenda : true;
  $("ed-resumo").checked = op ? op.resumo : true;
  $("ed-cta").checked = op ? op.cta : true;
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
  edVideo.src = `/video?nome=${encodeURIComponent(corte.nome)}&bruto=1&t=${Date.now()}`;
  edCarregarLista();
  if (typeof edDlg.showModal === "function") edDlg.showModal(); else edDlg.setAttribute("open", "");
}

function edFechar() {
  edVideo.pause();
  edDlg.close();
}
$("ed-fechar").addEventListener("click", edFechar);
$("ed-fechar-x").addEventListener("click", edFechar);
edDlg.addEventListener("close", () => edVideo.pause());

// ---------------------------------------------------------------- renderizar de novo
function edOpcoes() {
  const o = { layout: edLayout(), tarja: $("ed-tarja").checked, legenda: $("ed-legenda").checked,
              resumo: $("ed-resumo").checked, cta: $("ed-cta").checked };
  if (o.layout === "empilhado") {
    Object.assign(o, { cam: ED.cam, game: ED.game, cam_altura: ED.camAltura, ordem: ED.ordem });
  }
  return o;
}

$("ed-renderizar").addEventListener("click", async () => {
  edErro("");
  if (edLayout() === "empilhado" && !edImgOk()) { edErro("Espere o quadro carregar."); return; }
  $("ed-renderizar").disabled = true;
  try {
    await post("/api/cortes/editar", { nome: ED.corte.nome, opcoes: edOpcoes() });
  } catch (e) {
    edErro(e.message);
    $("ed-renderizar").disabled = false;
    return;
  }
  edEstado("Renderizando… pode levar alguns minutos (o resumo e a chamada final usam voz da IA)");
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
      if (!job || job.estado === "rodando") return;
      clearInterval(ED.timer); ED.timer = null;
      $("ed-renderizar").disabled = false;
      if (job.estado === "concluido") {
        edEstado("Pronto! O corte foi renderizado de novo. Confira e aprove na lista.");
        await carregarCortes();
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
