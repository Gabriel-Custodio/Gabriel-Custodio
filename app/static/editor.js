"use strict";
/* Editor de layout: duas caixas (câmera e gameplay) sobre um quadro do vídeo, com a prévia em retrato ao lado.
   Usa $, api e post de app.js. Todo texto vindo do servidor entra na página com textContent. */

const ED_LARGURA = 1080, ED_ALTURA = 1920, ED_FAIXA = 220;
const ED_CAM_MIN = 300, ED_CAM_MAX = 1100;
const ED_MIN_LARGURA = 0.05;                       // caixa nunca menor que 5% da largura do quadro

const ED = {
  quadro: null,                                    // nome do quadro no servidor
  cam: { x: 0.74, y: 0.35, w: 0.24, h: 0.2 },
  game: { x: 0.2, y: 0, w: 0.5, h: 1 },
  camAltura: 600,
  ordem: "cam_primeiro",
  slugEditando: null,
};

const edImg = $("ed-img");
const edCanvas = $("ed-retrato");
const edCtx = edCanvas.getContext("2d");

function edErro(msg) { $("ed-erro").textContent = msg || ""; $("ed-erro").hidden = !msg; }
function edEstado(msg) { $("ed-estado").textContent = msg || ""; $("ed-estado").hidden = !msg; }

// ---------------------------------------------------------------- geometria
const edGameAltura = () => ED_ALTURA - ED.camAltura - ED_FAIXA;
const edAlturaDoBloco = (tipo) => (tipo === "cam" ? ED.camAltura : edGameAltura());
const edImgOk = () => edImg.naturalWidth > 0 && edImg.naturalHeight > 0;

/* h/w (em frações do quadro) que mantém a proporção do espaço da caixa no vídeo vertical */
function edRazao(tipo) {
  const A = ED_LARGURA / edAlturaDoBloco(tipo);
  return edImg.naturalWidth / (A * edImg.naturalHeight);
}

/* Recalcula a altura a partir da largura, mantém tudo dentro do quadro */
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
  const camH = ED.camAltura, gameH = edGameAltura();
  return ED.ordem === "cam_primeiro"
    ? { cam: 0, faixa: camH, game: camH + ED_FAIXA, camH, gameH, topo: camH }
    : { game: 0, faixa: gameH, cam: gameH + ED_FAIXA, camH, gameH, topo: gameH };
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
  edCtx.fillStyle = "#d91e18";
  edCtx.fillRect(0, b.faixa * k, edCanvas.width, ED_FAIXA * k);
  edCtx.fillStyle = "#fff";
  edCtx.textAlign = "center";
  edCtx.font = "bold 13px system-ui, sans-serif";
  edCtx.fillText("LEGENDA", edCanvas.width / 2, (b.faixa + 100) * k + 4);
  edCtx.font = "bold 9px system-ui, sans-serif";
  edCtx.fillText("CURTA · COMENTE · COMPARTILHE", edCanvas.width / 2, (b.faixa + 178) * k);
  // linha de arraste entre o bloco de cima e a faixa
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
  el.parentElement.appendChild(el);                   // a caixa mexida fica por cima (antes da captura: mover no DOM a solta)
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
    const camAltura = ED.ordem === "cam_primeiro" ? topo : ED_ALTURA - ED_FAIXA - topo;
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

// ---------------------------------------------------------------- quadro (do vídeo ou enviado)
function edCaixasPadrao() {
  ED.cam = { x: 0.74, y: 0.35, w: 0.24, h: 0 };
  ED.game = { x: 0, y: 0, w: 1, h: 1 };
  edAjustar("cam");
  edAjustar("game");
  ED.game.x = (1 - ED.game.w) / 2;
}

function edCarregarImagem(nomeQuadro, manterCaixas) {
  ED.quadro = nomeQuadro;
  edImg.onload = () => {
    if (!manterCaixas) edCaixasPadrao();
    $("ed-area").hidden = false;
    edAtualizar();
    edEstado("Quadro carregado");
  };
  edImg.onerror = () => { edErro("Não foi possível abrir a imagem do quadro."); edEstado(""); };
  edImg.src = `/api/quadro?id=${encodeURIComponent(nomeQuadro)}&t=${Date.now()}`;
}

$("ed-carregar").addEventListener("click", async () => {
  edErro("");
  const url = $("ed-url").value.trim();
  if (!url) { edErro("Cole o link do vídeo."); return; }
  $("ed-carregar").disabled = true;
  edEstado("Baixando um trecho curto do vídeo… pode levar até um minuto");
  try {
    const r = await post("/api/quadro", { url, segundo: Number($("ed-segundo").value || 0) });
    edCarregarImagem(r.quadro, ED.slugEditando !== null);
  } catch (e) {
    edErro(e.message);
    edEstado("");
  } finally {
    $("ed-carregar").disabled = false;
  }
});

$("ed-arquivo").addEventListener("change", async (ev) => {
  const arq = ev.target.files && ev.target.files[0];
  ev.target.value = "";
  if (!arq) return;
  edErro("");
  edEstado("Enviando…");
  try {
    const resposta = await fetch("/api/quadro/enviar", {
      method: "POST", credentials: "same-origin",
      headers: { "X-Requested-With": "fabrica", "Content-Type": arq.type || "application/octet-stream" },
      body: arq,
    });
    let dados = {};
    try { dados = await resposta.json(); } catch (_) { /* sem JSON */ }
    if (!resposta.ok) throw new Error(dados.erro || `Erro ${resposta.status}`);
    edCarregarImagem(dados.quadro, ED.slugEditando !== null);
  } catch (e) {
    edErro(e.message);
    edEstado("");
  }
});

// ---------------------------------------------------------------- layouts salvos
let edLayouts = [];

$("ed-salvar").addEventListener("click", async () => {
  edErro("");
  const nome = $("ed-nome").value.trim();
  if (!nome) { edErro("Dê um nome ao layout."); return; }
  try {
    const r = await post("/api/layouts", { nome, cam: ED.cam, game: ED.game, cam_altura: ED.camAltura, ordem: ED.ordem });
    ED.slugEditando = r.layout.slug;
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
    li.append(nome,
      mk("Usar nos cortes", "", () => edUsar(l)),
      mk("Editar", "", () => edEditar(l)),
      mk("Apagar", "perigo", () => edApagar(l)));
    lista.appendChild(li);
  }
  edAtualizarOpcoesDoFormulario();
}

/* Põe os layouts salvos na caixa “Layout” da aba “Gerar cortes” */
function edAtualizarOpcoesDoFormulario() {
  const sel = $("layout");
  const atual = sel.value;
  sel.querySelectorAll("option[data-salvo]").forEach((o) => o.remove());
  for (const l of edLayouts) {
    const o = document.createElement("option");
    o.value = `salvo:${l.slug}`;
    o.dataset.salvo = "1";
    o.textContent = `Meu layout: ${l.nome || l.slug}`;
    sel.appendChild(o);
  }
  sel.value = [...sel.options].some((o) => o.value === atual) ? atual : "";
}

function edUsar(l) {
  $("layout").value = `salvo:${l.slug}`;
  const modo = document.querySelector('input[name="modo"][value="gameplay"]');
  if (modo) { modo.checked = true; modo.dispatchEvent(new Event("change")); }
  mostrarAba("gerar");
}

function edEditar(l) {
  ED.cam = { ...l.cam };
  ED.game = { ...l.game };
  ED.camAltura = l.cam_altura;
  ED.ordem = l.ordem;
  ED.slugEditando = l.slug;
  document.querySelector(`input[name="ed-ordem"][value="${l.ordem}"]`).checked = true;
  $("ed-nome").value = l.nome || "";
  edErro("");
  if (edImgOk()) {
    $("ed-area").hidden = false;
    edAtualizar();
    edEstado(`Editando “${l.nome || l.slug}”`);
  } else {
    edEstado("");
    edErro("Carregue um quadro do vídeo (ou envie uma imagem) para editar este layout.");
    $("ed-area").hidden = true;
  }
}

async function edApagar(l) {
  if (!confirm(`Apagar o layout “${l.nome || l.slug}”?`)) return;
  try {
    await post("/api/layouts/apagar", { slug: l.slug });
    if (ED.slugEditando === l.slug) ED.slugEditando = null;
    await edCarregarLista();
  } catch (e) {
    edErro(e.message);
  }
}

edCarregarLista();
