"use strict";
/* Aba "Monitor de lives": a lista de canais (Twitch e YouTube) que o monitor pode assistir, adicionados pelo link do canal.
   Usa $, api, post, selo de app.js. Todo texto vindo do servidor entra na página com textContent. */

const CN = { canais: [], layouts: [], editando: null, maximo: 2, lives: {}, maxLives: 2, logs: {} };
const cnCheia = () => CN.canais.length >= CN.maximo;

function cnErro(msg) { $("cn-erro").textContent = msg || ""; $("cn-erro").hidden = !msg; }
function cnEstado(msg) { $("cn-estado").textContent = msg || ""; $("cn-estado").hidden = !msg; }

function cnLimpar() {
  CN.editando = null;
  $("cn-titulo-form").textContent = "Monitor de lives";
  $("cn-salvar").textContent = "Adicionar";
  $("cn-canal").value = "";
  $("cn-nome").value = ""; $("cn-obs").value = "";
  $("cn-layout").value = "";
  cnErro(""); cnEstado("");
  cnAtualizarCheio();
}

/* Lista cheia (máximo de canais): só dá para editar, apagar ou suspender; para adicionar, apague um canal. */
function cnAtualizarCheio() {
  const cheia = cnCheia() && !CN.editando;
  $("cn-canal").disabled = cheia || !!CN.editando;
  $("cn-salvar").disabled = cheia;
  $("cn-contagem").textContent = `${CN.canais.length} de ${CN.maximo}`;
  if (cheia) cnEstado(`Lista cheia (máximo ${CN.maximo} canais): apague um canal para adicionar outro`);
  else if ($("cn-estado").textContent.startsWith("Lista cheia")) cnEstado("");
}

/* Separa vários links colados no mesmo campo (espaço, vírgula, ponto e vírgula ou linha nova) */
function cnLinks(texto) {
  return String(texto || "").split(/[\s,;]+/).map((t) => t.trim()).filter(Boolean);
}

function cnPreencherLayouts() {
  const sel = $("cn-layout"), atual = sel.value;
  sel.replaceChildren(new Option("Nenhum", ""));
  for (const l of CN.layouts) sel.append(new Option(l.nome || l.slug, l.slug));
  sel.value = CN.layouts.some((l) => l.slug === atual) ? atual : "";
}

function cnEditar(c) {
  CN.editando = c.id;
  $("cn-titulo-form").textContent = `Editar canal: ${c.nome}`;
  $("cn-salvar").textContent = "Salvar alterações";
  $("cn-canal").value = c.url_live ? c.url_live.replace(/\/live$/, "") : c.canal; $("cn-canal").disabled = true;
  $("cn-nome").value = c.nome || "";
  $("cn-obs").value = c.observacoes || "";
  cnPreencherLayouts();
  $("cn-layout").value = c.layout || "";
  $("cn-mais").open = true;
  cnErro(""); cnEstado("");
  $("cn-form").scrollIntoView({ behavior: "smooth", block: "start" });
}

function cnBotao(texto, classe, fn) {
  const b = document.createElement("button");
  b.type = "button"; b.className = `btn ${classe || ""}`.trim(); b.textContent = texto;
  b.addEventListener("click", fn);
  return b;
}

function cnLinha(c) {
  const li = document.createElement("li");
  li.className = `cn-item ${c.situacao}`;
  const topo = document.createElement("div");
  topo.className = "cn-topo";
  const nome = document.createElement("span"); nome.className = "cn-nome"; nome.textContent = c.nome;
  const canal = document.createElement("span"); canal.className = "cn-canal";
  canal.textContent = `${c.plataforma === "twitch" ? "Twitch" : "YouTube"} · ${c.canal}`;
  const nivel = { ativo: "concluido", suspenso: "cancelado" }[c.situacao] || "";
  const rotulo = { ativo: "ativo", suspenso: "suspenso" }[c.situacao] || c.situacao;
  topo.append(nome, canal, selo(rotulo, nivel));
  const d1 = document.createElement("p"); d1.className = "cn-detalhe";
  d1.textContent = c.url_live ? c.url_live.replace(/\/live$/, "") : "";
  li.append(topo, d1);
  if (c.observacoes) { const d3 = document.createElement("p"); d3.className = "cn-detalhe"; d3.textContent = `Obs.: ${c.observacoes}`; li.append(d3); }
  if (c.layout) {
    const l = CN.layouts.find((x) => x.slug === c.layout);
    const d4 = document.createElement("p"); d4.className = "cn-detalhe"; d4.textContent = `Layout salvo: ${l ? l.nome : c.layout}`; li.append(d4);
  }
  li.append(cnMonitor(c));
  const acoes = document.createElement("div"); acoes.className = "cn-acoes";
  acoes.append(
    cnBotao("Editar", "", () => cnEditar(c)),
    cnBotao(c.situacao === "suspenso" ? "Reativar" : "Suspender", "", () => cnAtivo(c, c.situacao === "suspenso")),
    cnBotao("Apagar", "perigo", () => cnApagar(c)));
  li.append(acoes);
  return li;
}

/* ---------------------------------------------------------------- monitor ao vivo (por canal) */
const LV_ESTADOS = {
  parado: ["Monitor parado", ""], iniciando: ["Iniciando o monitor…", "rodando"], conectando: ["Conectando à live…", "rodando"],
  gravando: ["Assistindo a live agora", "concluido"], offline: ["Live fora do ar (verificando de novo)", "aviso"],
  erro: ["Com problema", "erro"], bloqueado: ["Bloqueado: canal não permitido", "erro"], parando: ["Parando…", "rodando"],
  fim: ["Acabou", ""],
};

function lvDuracao(s) {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h} h ${m} min` : (m ? `${m} min ${r} s` : `${r} s`);
}

function cnMonitor(c) {
  const box = document.createElement("div");
  box.className = "lv"; box.dataset.id = c.id;
  const topo = document.createElement("div"); topo.className = "lv-topo";
  const estado = document.createElement("span"); estado.className = "selo lv-estado";
  const resumo = document.createElement("span"); resumo.className = "lv-resumo";
  const acoes = document.createElement("div"); acoes.className = "lv-acoes";
  const iniciar = cnBotao("Iniciar monitor", "primario", () => lvIniciar(c));
  const pausar = cnBotao("Pausar", "", () => lvPausar(c));
  const parar = cnBotao("Parar", "perigo", () => lvParar(c));
  iniciar.classList.add("lv-iniciar"); pausar.classList.add("lv-pausar"); parar.classList.add("lv-parar");
  acoes.append(iniciar, pausar, parar);
  topo.append(estado, acoes);
  const numeros = document.createElement("p"); numeros.className = "cn-detalhe lv-numeros";
  const msg = document.createElement("p"); msg.className = "erro lv-msg"; msg.hidden = true;
  const momentos = document.createElement("ul"); momentos.className = "lv-momentos";
  const logd = document.createElement("details"); logd.className = "lv-log";
  const sum = document.createElement("summary"); sum.textContent = "Ver o que o monitor está fazendo (log)";
  const pre = document.createElement("pre"); pre.className = "lv-pre";
  logd.append(sum, pre);
  logd.addEventListener("toggle", () => { if (logd.open) lvLog(c.id); });
  box.append(topo, resumo, numeros, msg, momentos, logd, lvLongoCaixa(c));
  lvPintar(box, CN.lives[c.id]);
  return box;
}

/* Vídeo longo com os cortes que o monitor já fez deste canal (não precisa do monitor rodando) */
function lvLongoCaixa(c) {
  const caixa = document.createElement("div");
  caixa.className = "lv-longo";
  const rot = document.createElement("span");
  rot.className = "cn-detalhe";
  rot.textContent = "Vídeo longo com os melhores cortes desta live:";
  const minutos = document.createElement("select");
  minutos.className = "lv-longo-min"; minutos.setAttribute("aria-label", "Duração do vídeo longo");
  for (const m of [4, 5, 6, 8, 10, 12]) minutos.append(new Option(`${m} min`, String(m), false, m === 8));
  const horas = document.createElement("select");
  horas.className = "lv-longo-horas"; horas.setAttribute("aria-label", "Período dos cortes");
  for (const [h, texto] of [[3, "últimas 3 h"], [6, "últimas 6 h"], [12, "últimas 12 h"], [24, "últimas 24 h"], [72, "últimos 3 dias"], [0, "todos os cortes"]]) {
    horas.append(new Option(texto, String(h), false, h === 6));
  }
  const botao = cnBotao("Montar vídeo longo", "", () => lvMontarLongo(c, Number(minutos.value), Number(horas.value) || null));
  botao.classList.add("lv-longo-btn");
  caixa.append(rot, minutos, horas, botao);
  return caixa;
}

async function lvMontarLongo(c, minutos, horas) {
  lvMsg(c.id, "");
  try {
    await post("/api/lives/longo", { id: c.id, minutos, horas });
  } catch (e) {
    lvMsg(c.id, e.message);
    return;
  }
  mostrarAba("gerar");                                  // o andamento aparece no log da aba "Gerar cortes"
  proximoLog = 0; linhasNaTela = []; $("log").textContent = "";
  iniciarConsulta();
}

function lvPintar(box, v) {
  const q = (cl) => box.querySelector(cl);
  const c = CN.canais.find((x) => x.id === box.dataset.id) || {};
  v = v || { rodando: false, estado: "parado" };
  let [rotulo, nivel] = LV_ESTADOS[v.estado] || [v.estado, ""];
  if (v.rodando && v.pausado) { rotulo = "Pausado (continua assistindo, sem gerar momentos)"; nivel = "aviso"; }
  if (v.sem_resposta) { rotulo = "Sem resposta do monitor"; nivel = "erro"; }
  const el = q(".lv-estado");
  el.textContent = rotulo; el.className = `selo lv-estado ${nivel}`;
  q(".lv-resumo").textContent = v.detalhe && v.estado !== "gravando" ? v.detalhe : "";
  q(".lv-resumo").hidden = !q(".lv-resumo").textContent;
  const n = q(".lv-numeros");
  if (v.rodando && v.estado === "gravando") {
    const chat = v.chat && v.chat !== "desligado" ? `chat: ${v.mensagens_chat || 0} mensagens` : "chat desligado";
    n.textContent = `Assistindo há ${lvDuracao(v.tempo_monitor_s)} · ${v.total_momentos || 0} momento(s) detectado(s) · ` +
                    `${(v.sinais_audio || 0) + (v.sinais_chat || 0) + (v.sinais_palavra || 0)} sinal(is) · ${chat}` +
                    (v.reinicios ? ` · ${v.reinicios} reconexão(ões)` : "");
    if (v.enviar_cortes) {
      n.textContent += ` · ${v.cortes_enviados || 0} corte(s) enviado(s) para Cortes prontos` +
                       (v.cortes_descartados ? ` (${v.cortes_descartados} descartado(s) por nota baixa)` : "") +
                       (v.cortes_erro ? ` · ${v.cortes_erro} com erro` : "") + (v.ia ? " · IA ligada (escreve o título)" : " · IA desligada: sem GROQ_API_KEY no .env");
    }
    n.hidden = false;
  } else if (!v.rodando && v.segundos != null) {
    n.textContent = `Última vez: ${lvDuracao(v.segundos)} · ${v.total_momentos || 0} momento(s) detectado(s)`;
    n.hidden = false;
  } else n.hidden = true;
  const msg = q(".lv-msg");
  const erro = !v.rodando && v.estado === "erro" || v.estado === "bloqueado" ? (v.erro || v.detalhe) : "";
  msg.textContent = erro || ""; msg.hidden = !erro;
  const ativo = c.situacao === "ativo";
  const cheio = Object.values(CN.lives).filter((x) => x.rodando).length >= CN.maxLives;
  const ini = q(".lv-iniciar"), pau = q(".lv-pausar"), par = q(".lv-parar");
  const longo = q(".lv-longo-btn");
  longo.disabled = !ativo; longo.title = ativo ? "" : "Canal suspenso: reative-o para usar os cortes dele";
  ini.hidden = !!v.rodando; ini.disabled = !ativo || cheio;
  ini.title = !ativo ? "Canal suspenso: reative-o para monitorar" : (cheio ? `Já há ${CN.maxLives} lives sendo monitoradas` : "");
  pau.hidden = !v.rodando; pau.textContent = v.pausado ? "Retomar" : "Pausar";
  par.hidden = !v.rodando; par.disabled = !!v.parando;
  const ul = q(".lv-momentos"), itens = (v.momentos || []);
  ul.replaceChildren(...itens.map((m) => {
    const li = document.createElement("li");
    const tipos = Object.keys(m.sinais || {}).map((t) => ({ audio: "volume", chat: "chat", palavra: "palavras" }[t] || t)).join(" + ");
    let fim = "";
    if (m.corte) {
      const r = m.corte;
      fim = r.resultado === "enviado" ? ` → enviado para Cortes prontos${r.potencial ? ` (Potencial ${r.potencial === "alto" ? "Alto" : "Médio"})` : ""}: ${r.arquivo}`
          : r.resultado === "descartado" ? ` → descartado: ${r.motivo || "sem motivo"}`
          : ` → não virou corte: ${r.motivo || r.resultado}`;
    }
    li.textContent = `Momento nota ${Number(m.nota).toFixed(1)} · ${tipos || "sinais"} · ${m.em ? m.em.replace("T", " ") : ""}${fim}`;
    return li;
  }));
  ul.hidden = !itens.length;
}

function lvMsg(id, texto) {
  const box = document.querySelector(`.lv[data-id="${CSS.escape(id)}"] .lv-msg`);
  if (box) { box.textContent = texto || ""; box.hidden = !texto; }
}

async function lvAcao(c, caminho, corpo) {
  try { await post(caminho, { id: c.id, ...(corpo || {}) }); }
  catch (e) { lvMsg(c.id, e.message); return false; }
  await carregarLives();
  return true;
}
const lvIniciar = (c) => lvAcao(c, "/api/lives/iniciar");
const lvParar = (c) => lvAcao(c, "/api/lives/parar");
const lvPausar = (c) => lvAcao(c, "/api/lives/pausar", { pausar: !(CN.lives[c.id] && CN.lives[c.id].pausado) });

async function lvLog(id) {
  const pre = document.querySelector(`.lv[data-id="${CSS.escape(id)}"] .lv-pre`);
  if (!pre) return;
  try {
    const d = await api(`/api/lives/log?id=${encodeURIComponent(id)}&desde=0`);
    pre.textContent = (d.linhas || []).slice(-60).join("\n") || "Ainda sem mensagens.";
    pre.scrollTop = pre.scrollHeight;
  } catch (e) { pre.textContent = e.message; }
}

/* Atualiza só o painel de cada canal (não refaz a lista, para não fechar o log aberto) */
async function carregarLives() {
  let d;
  try { d = await api("/api/lives"); } catch (e) { return; }
  CN.maxLives = d.maximo || CN.maxLives;
  CN.lives = {};
  for (const l of d.lives || []) CN.lives[l.canal.id] = l;
  for (const box of document.querySelectorAll(".lv")) {
    lvPintar(box, CN.lives[box.dataset.id]);
    const det = box.querySelector(".lv-log");
    if (det && det.open) lvLog(box.dataset.id);
  }
}

let lvTemporizador = null;
function lvAtivarAtualizacao() {
  if (lvTemporizador) return;
  lvTemporizador = setInterval(() => {
    if (document.hidden || $("painel-lives").hidden) return;
    carregarLives();
  }, 2000);
}

async function carregarCanais() {
  try {
    const d = await api("/api/canais");
    CN.canais = d.canais || []; CN.layouts = d.layouts || []; CN.maximo = d.maximo || CN.maximo;
  } catch (e) {
    cnErro(e.message);
    return;
  }
  cnPreencherLayouts();
  const lista = $("cn-lista");
  try { const l = await api("/api/lives"); CN.maxLives = l.maximo || CN.maxLives; CN.lives = {}; for (const x of l.lives || []) CN.lives[x.canal.id] = x; } catch (e) { /* servidor antigo: sem monitor */ }
  lista.replaceChildren(...CN.canais.map(cnLinha));
  lvAtivarAtualizacao();
  $("cn-vazio").hidden = CN.canais.length > 0;
  cnAtualizarCheio();
}

async function cnAtivo(c, ativo) {
  cnErro("");
  try {
    await post("/api/canais/ativo", { id: c.id, ativo });
    await carregarCanais();
  } catch (e) { cnErro(e.message); }
}

async function cnApagar(c) {
  if (!confirm(`Apagar o canal “${c.nome}” da lista? O monitor deixa de poder assisti-lo.`)) return;
  cnErro("");
  try {
    await post("/api/canais/apagar", { id: c.id });
    if (CN.editando === c.id) cnLimpar();
    await carregarCanais();
  } catch (e) { cnErro(e.message); }
}

$("cn-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  cnErro(""); cnEstado("");
  if (CN.editando) {                                  // editar mantém o canal e o estado ativo/suspenso
    const atual = CN.canais.find((c) => c.id === CN.editando);
    if (!atual) { cnLimpar(); return; }
    $("cn-salvar").disabled = true;
    try {
      const r = await post("/api/canais", { plataforma: atual.plataforma, canal: atual.canal, ativo: atual.ativo !== false,
                                            nome: $("cn-nome").value, layout: $("cn-layout").value, observacoes: $("cn-obs").value });
      CN.editando = null;
      await carregarCanais();
      cnLimpar();
      cnEstado(`Canal “${r.canal.nome}” salvo`);
    } catch (e) { cnErro(e.message); }
    finally { $("cn-salvar").disabled = false; }
    return;
  }
  const links = cnLinks($("cn-canal").value);
  if (!links.length) { cnErro("Cole o link do canal."); return; }
  const novos = new Set(links);
  if (CN.canais.length + novos.size > CN.maximo) {
    cnErro(`A lista aceita no máximo ${CN.maximo} canais (já tem ${CN.canais.length}). Apague um canal para adicionar outro.`);
    return;
  }
  const um = links.length === 1;                      // nome, layout e observações valem quando se adiciona um canal por vez
  $("cn-salvar").disabled = true;
  const salvos = [];
  try {
    for (const link of links) {
      const r = await post("/api/canais", { canal: link, nome: um ? $("cn-nome").value : "",
                                            layout: um ? $("cn-layout").value : "", observacoes: um ? $("cn-obs").value : "" });
      salvos.push(r.canal.nome);
    }
    await carregarCanais();
    cnLimpar();
    cnEstado(`Salvo: ${salvos.join(", ")}`);
  } catch (e) {
    await carregarCanais();
    cnErro(salvos.length ? `${e.message} (já salvos: ${salvos.join(", ")})` : e.message);
  } finally {
    cnAtualizarCheio();
  }
});
$("cn-limpar").addEventListener("click", cnLimpar);

cnLimpar();
