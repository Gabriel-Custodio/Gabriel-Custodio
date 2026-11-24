"use strict";
/* Lógica da linha do tempo do editor (sem DOM, para poder ser testada à parte).
   O corte bruto é dividido em "peças" contíguas [{a, b, keep}] (segundos). Peça com keep=false foi excluída.
   Todas as funções devolvem um array NOVO (o anterior nunca é alterado), o que torna desfazer/refazer trivial. */

const TL_MIN_PECA = 0.2;       // uma peça menor que isso não tem utilidade (igual ao servidor)
const TL_MIN_TOTAL = 1.0;      // o corte aparado precisa durar pelo menos isso
const TL_MAX_TRECHOS = 60;
const TL_EPS = 1e-6;

const tlCopiar = (pecas) => pecas.map((p) => ({ a: p.a, b: p.b, keep: p.keep }));

function tlNovo(dur) {
  return [{ a: 0, b: dur, keep: true }];
}

function tlDuracao(pecas) {
  return pecas.reduce((s, p) => s + (p.keep ? p.b - p.a : 0), 0);
}

/* índice da peça que contém o tempo t (no fim exato, a última) */
function tlPecaEm(pecas, t) {
  for (let i = 0; i < pecas.length; i++) {
    if (t >= pecas[i].a && t < pecas[i].b) return i;
  }
  return pecas.length - 1;
}

/* Divide a peça sob t em duas. null se t estiver perto demais de uma borda (peças minúsculas) */
function tlDividir(pecas, t) {
  const i = tlPecaEm(pecas, t), p = pecas[i];
  if (!p || t - p.a < TL_MIN_PECA || p.b - t < TL_MIN_PECA) return null;
  if (pecas.length >= TL_MAX_TRECHOS) return null;
  const novo = tlCopiar(pecas);
  novo.splice(i, 1, { a: p.a, b: t, keep: p.keep }, { a: t, b: p.b, keep: p.keep });
  return novo;
}

/* Exclui ou restaura a peça i. null se o resultado ficasse curto demais */
function tlAlternar(pecas, i) {
  if (!pecas[i]) return null;
  const novo = tlCopiar(pecas);
  novo[i].keep = !novo[i].keep;
  if (tlDuracao(novo) < TL_MIN_TOTAL - TL_EPS) return null;
  return novo;
}

/* divide em t sem a regra de tamanho mínimo (usado ao aparar as pontas) */
function tlCortarEm(pecas, t) {
  const i = tlPecaEm(pecas, t), p = pecas[i];
  if (!p || t <= p.a + TL_EPS || t >= p.b - TL_EPS) return tlCopiar(pecas);
  const novo = tlCopiar(pecas);
  novo.splice(i, 1, { a: p.a, b: t, keep: p.keep }, { a: t, b: p.b, keep: p.keep });
  return novo;
}

/* junta as peças excluídas coladas nas pontas (o bloco aparado vira uma só) */
function tlLimparPontas(pecas) {
  const out = tlCopiar(pecas);
  while (out.length > 1 && !out[0].keep && !out[1].keep) { out[0].b = out[1].b; out.splice(1, 1); }
  while (out.length > 1 && !out[out.length - 1].keep && !out[out.length - 2].keep) {
    out[out.length - 2].b = out[out.length - 1].b; out.pop();
  }
  return out;
}

function tlInicio(pecas) {
  const p = pecas.find((x) => x.keep);
  return p ? p.a : 0;
}
function tlFim(pecas) {
  for (let i = pecas.length - 1; i >= 0; i--) if (pecas[i].keep) return pecas[i].b;
  return 0;
}

/* Arrasta a ponta: lado "ini" ou "fim", para o tempo t. Parte sempre do estado antes do arraste (base). */
function tlAparar(base, lado, t) {
  const dur = base[base.length - 1].b;
  const ini0 = tlInicio(base), fim0 = tlFim(base);
  let novo;
  if (lado === "ini") {
    t = Math.min(Math.max(t, 0), fim0 - TL_MIN_TOTAL);
    novo = tlCortarEm(base, t);
    for (const p of novo) {
      if (p.b <= t + TL_EPS) p.keep = false;
      else if (p.a >= t - TL_EPS && p.a < ini0 - TL_EPS) p.keep = true;
    }
  } else {
    t = Math.max(Math.min(t, dur), ini0 + TL_MIN_TOTAL);
    novo = tlCortarEm(base, t);
    for (const p of novo) {
      if (p.a >= t - TL_EPS) p.keep = false;
      else if (p.b <= t + TL_EPS && p.b > fim0 + TL_EPS) p.keep = true;
    }
  }
  return tlLimparPontas(novo);
}

/* Se t cair numa peça excluída, devolve onde o vídeo deve continuar (início da próxima peça mantida); null = fim */
function tlProximoMantido(pecas, t) {
  const i = tlPecaEm(pecas, t);
  if (pecas[i].keep) return t;
  for (let j = i + 1; j < pecas.length; j++) if (pecas[j].keep) return pecas[j].a;
  return null;
}

/* Trechos mantidos ([[ini, fim], ...]) para o servidor; null quando o corte inteiro foi mantido */
function tlTrechos(pecas) {
  const out = [];
  for (const p of pecas) {
    if (!p.keep) continue;
    const ult = out[out.length - 1];
    if (ult && Math.abs(ult[1] - p.a) < TL_EPS) ult[1] = p.b;
    else out.push([p.a, p.b]);
  }
  if (out.length === 1 && out[0][0] <= TL_EPS && Math.abs(out[0][1] - pecas[pecas.length - 1].b) < 0.05) return null;
  return out.map(([a, b]) => [Math.round(a * 1000) / 1000, Math.round(b * 1000) / 1000]);
}

/* Reconstrói as peças a partir dos trechos mantidos guardados na última edição */
function tlDeTrechos(trechos, dur) {
  if (!Array.isArray(trechos) || !trechos.length) return tlNovo(dur);
  const out = [];
  let cursor = 0;
  for (const [a0, b0] of trechos) {
    const a = Math.min(Math.max(a0, 0), dur), b = Math.min(Math.max(b0, 0), dur);
    if (b - a < TL_EPS || a < cursor - TL_EPS) continue;
    if (a > cursor + TL_EPS) out.push({ a: cursor, b: a, keep: false });
    out.push({ a, b, keep: true });
    cursor = b;
  }
  if (!out.length) return tlNovo(dur);
  if (cursor < dur - TL_EPS) out.push({ a: cursor, b: dur, keep: false });
  return out;
}

/* "m:ss" (ou "h:mm:ss"); com decimos = true, "m:ss.d" */
function tlFormatar(s, decimos) {
  s = Math.max(0, s || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), seg = s % 60;
  const ss = decimos ? seg.toFixed(1).padStart(4, "0") : String(Math.floor(seg)).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${String(m).padStart(2, "0")}:${ss}`;
}

/* Passo "bonito" entre marcas da régua para a largura/escala atuais (segundos por pixel) */
function tlPassoRegua(segPorPixel, minPx) {
  const alvo = segPorPixel * minPx;
  for (const p of [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600]) if (p >= alvo) return p;
  return 3600;
}

// ---------------------------------------------------------------- histórico (desfazer / refazer)
function tlHistNovo(estado) {
  return { pilha: [tlCopiar(estado)], i: 0 };
}
function tlHistAdicionar(h, estado) {
  h.pilha = h.pilha.slice(0, h.i + 1);
  h.pilha.push(tlCopiar(estado));
  if (h.pilha.length > 100) h.pilha.shift();
  h.i = h.pilha.length - 1;
}
const tlHistPodeDesfazer = (h) => h.i > 0;
const tlHistPodeRefazer = (h) => h.i < h.pilha.length - 1;
function tlHistDesfazer(h) { if (h.i > 0) h.i -= 1; return tlCopiar(h.pilha[h.i]); }
function tlHistRefazer(h) { if (h.i < h.pilha.length - 1) h.i += 1; return tlCopiar(h.pilha[h.i]); }
