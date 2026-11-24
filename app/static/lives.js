"use strict";
/* Aba "Monitor de lives": a lista de canais (Twitch e YouTube) que o monitor pode assistir, adicionados pelo link do canal.
   Usa $, api, post, selo de app.js. Todo texto vindo do servidor entra na página com textContent. */

const CN = { canais: [], layouts: [], editando: null, maximo: 2 };
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
  const acoes = document.createElement("div"); acoes.className = "cn-acoes";
  acoes.append(
    cnBotao("Editar", "", () => cnEditar(c)),
    cnBotao(c.situacao === "suspenso" ? "Reativar" : "Suspender", "", () => cnAtivo(c, c.situacao === "suspenso")),
    cnBotao("Apagar", "perigo", () => cnApagar(c)));
  li.append(acoes);
  return li;
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
  lista.replaceChildren(...CN.canais.map(cnLinha));
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
