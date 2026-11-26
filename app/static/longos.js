"use strict";
/* Aba "Vídeos longos": lista os vídeos de melhores momentos (horizontais) e abre o mesmo editor dos cortes, no modo longo.
   Usa $, api, post, selo e formatarTempo de app.js e edAbrir de editor.js. Todo texto vindo do servidor entra na página com
   textContent, nunca como HTML. */

let longos = [];

function duracaoLonga(segundos) {
  if (typeof segundos !== "number" || !isFinite(segundos)) return "";
  const m = Math.floor(segundos / 60), s = Math.round(segundos % 60);
  return `${m}:${String(s).padStart(2, "0")} min`;
}

function montarLongo(v) {
  const no = $("tpl-longo").content.firstElementChild.cloneNode(true);
  no.classList.toggle("aprovado", v.estado === "aprovado");
  const video = no.querySelector("video");
  video.src = `/video?nome=${encodeURIComponent(v.nome)}&lib=longos`;
  no.querySelector(".titulo").textContent = v.titulo || v.nome.replace(/\.mp4$/i, "");

  const selos = no.querySelector(".selos");
  if (v.estado === "aprovado") selos.append(selo("Aprovado", "concluido"));
  selos.append(selo(v.editado ? "editado" : "bruto: sem edição", v.editado ? "concluido" : "aviso"));
  selos.append(selo("horizontal 16:9"));
  if (v.momentos.length) selos.append(selo(`${v.momentos.length} momento${v.momentos.length > 1 ? "s" : ""}`));
  const dur = duracaoLonga(v.duracao_s);
  if (dur) selos.append(selo(dur));
  if (v.fontes && v.fontes.length === 2) {
    selos.append(selo("2 streamers", "concluido"));
    for (const f of v.fontes) if (f.streamer) selos.append(selo(`${f.streamer}${f.momentos != null ? ` · ${f.momentos}` : ""}`));
  } else if (v.streamer) {
    selos.append(selo(v.streamer));
  }
  if (v.editado) {
    selos.append(selo(v.resumo ? "com resumo no início" : "sem resumo", v.resumo ? "" : "cancelado"));
    selos.append(selo(v.cta ? "com chamada final" : "sem chamada final", v.cta ? "" : "cancelado"));
  }

  no.querySelector(".meta").textContent = [v.nome, `${v.tamanho_mb} MB`, v.modificado.replace("T", " ")].join(" · ");

  const aviso = no.querySelector(".aviso-selecao");
  const motivos = [v.selecao && v.selecao.motivo, v.resumo_motivo && `Sem resumo no início: ${v.resumo_motivo}`,
                   v.cta_motivo && `Sem chamada final: ${v.cta_motivo}`].filter(Boolean);
  aviso.textContent = motivos.join(" · ");
  aviso.hidden = motivos.length === 0;

  const lista = no.querySelector(".lista-momentos");
  const detalhe = no.querySelector(".momentos");
  detalhe.hidden = v.momentos.length === 0;
  for (const m of v.momentos) {
    const li = document.createElement("li");
    const ir = document.createElement("button");
    ir.type = "button"; ir.className = "btn"; ir.textContent = formatarTempo(Math.round(m.inicio));
    ir.title = "Ir até este momento no vídeo";
    ir.addEventListener("click", () => { video.currentTime = m.inicio; video.play().catch(() => {}); });
    const txt = document.createElement("span");
    const nota = typeof m.nota === "number" ? ` · nota ${m.nota.toFixed(1)}` : "";
    txt.textContent = `${m.titulo || "Momento"}${m.streamer ? ` [${m.streamer}]` : ""}${nota}${m.motivo ? ` — ${m.motivo}` : ""}`;
    li.append(ir, txt);
    lista.append(li);
  }

  const fala = no.querySelector(".fala");
  fala.hidden = !v.transcricao;
  no.querySelector(".transcricao").textContent = v.transcricao;
  const resumo = no.querySelector(".resumo-intro");
  resumo.hidden = !v.resumo;
  no.querySelector(".texto-resumo").textContent = v.resumo || "";

  descricaoMontar(no, v, "longos");
  const editar = no.querySelector(".editar");
  if (v.editado) {                                                           // já editado: o mesmo botão reabre o editor com as opções da última edição
    editar.textContent = "Reeditar";
    editar.title = "Reabre o editor com as opções da última edição (dá para recomeçar do bruto lá dentro)";
  }
  editar.addEventListener("click", () => edAbrir(v, "longos"));
  const aprovar = no.querySelector(".aprovar");
  aprovar.textContent = v.estado === "aprovado" ? "Desfazer aprovação" : "Aprovar";
  aprovar.addEventListener("click", () => acaoLongo(v.nome, v.estado === "aprovado" ? "desfazer" : "aprovar"));
  no.querySelector(".copiar").addEventListener("click", async (ev) => {
    try {
      await navigator.clipboard.writeText(v.titulo || v.nome);
      ev.target.textContent = "Copiado!";
    } catch (_) {
      ev.target.textContent = "Não consegui copiar";
    }
    setTimeout(() => { ev.target.textContent = "Copiar título"; }, 1800);
  });
  const baixar = no.querySelector(".baixar");
  baixar.href = video.src;
  baixar.setAttribute("download", v.nome);
  no.querySelector(".descartar").addEventListener("click", () => {
    if (!confirm(`Descartar este vídeo longo?\n\n${v.titulo || v.nome}\n\nEle vai para a pasta “_descartados” (não é apagado).`)) return;
    acaoLongo(v.nome, "descartar");
  });
  return no;
}

async function acaoLongo(nome, acao) {
  try {
    await post("/api/longos/acao", { nome, acao });
  } catch (e) {
    alert(e.message);
  }
  carregarLongos();
}

function desenharLongos() {
  const filtro = $("filtro-longos").value;
  const visiveis = longos.filter((v) => filtro === "todos" || v.estado === filtro);
  $("lista-longos").replaceChildren(...visiveis.map(montarLongo));
  $("vazio-longos").hidden = visiveis.length > 0;
}

async function carregarLongos() {
  try {
    longos = (await api("/api/longos")).longos;
  } catch (e) {
    longos = [];
    alert(e.message);
  }
  $("contador-longos").textContent = String(longos.length);
  desenharLongos();
}

async function atualizarContadorLongos() {
  try { $("contador-longos").textContent = String((await api("/api/longos")).longos.length); } catch (_) { /* ignora */ }
}

$("filtro-longos").addEventListener("change", desenharLongos);
$("btn-atualizar-longos").addEventListener("click", carregarLongos);
$("btn-pasta-longos-aba").addEventListener("click", async () => {
  try { await post("/api/pasta", { pasta: "longos" }); } catch (e) { alert(e.message); }
});
atualizarContadorLongos();
