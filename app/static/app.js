"use strict";
/* Interface da Fábrica de Cortes. Sem bibliotecas externas. Todo texto vindo do servidor
   (títulos, falas, log) entra na página com textContent, nunca como HTML. */

const $ = (id) => document.getElementById(id);
const MAX_LINHAS_TELA = 3000;

let proximoLog = 0;
let temporizador = null;
let linhasNaTela = [];
let cortes = [];

async function api(caminho, opcoes = {}) {
  const resposta = await fetch(caminho, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Requested-With": "fabrica" },
    ...opcoes,
  });
  let dados = {};
  try { dados = await resposta.json(); } catch (_) { /* resposta sem JSON */ }
  if (!resposta.ok) throw new Error(dados.erro || `Erro ${resposta.status}`);
  return dados;
}

const post = (caminho, corpo) => api(caminho, { method: "POST", body: JSON.stringify(corpo || {}) });

function formatarTempo(segundos) {
  const m = Math.floor(segundos / 60), s = segundos % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// ------------------------------------------------------------- abas
const ABAS = ["gerar", "cortes"];
function mostrarAba(nome) {
  for (const a of ABAS) {
    const ativa = a === nome;
    $(`aba-${a}`).setAttribute("aria-selected", String(ativa));
    $(`aba-${a}`).tabIndex = ativa ? 0 : -1;
    $(`painel-${a}`).hidden = !ativa;
  }
  if (nome === "cortes") carregarCortes();
}
for (const a of ABAS) $(`aba-${a}`).addEventListener("click", () => mostrarAba(a));
$("ver-cortes").addEventListener("click", () => mostrarAba("cortes"));

// ------------------------------------------------------------- pré-requisitos
function pilula(texto, nivel, dica) {
  const el = document.createElement("span");
  el.className = `pilula ${nivel}`;
  el.textContent = texto;
  if (dica) el.title = dica;
  return el;
}

function mostrarPrerequisitos(p) {
  const caixa = $("prereq");
  caixa.replaceChildren(
    pilula(p.script ? "gerar_cortes.py" : "gerar_cortes.py não encontrado", p.script ? "ok" : "erro"),
    pilula(p.ffmpeg ? "ffmpeg" : "ffmpeg não encontrado", p.ffmpeg ? "ok" : "erro",
      p.ffmpeg ? "" : "Veja a seção 2 do README (bin\\ffmpeg.exe ou PATH)."),
    pilula(p.chave_groq ? "Chave Groq" : "Sem chave Groq", p.chave_groq ? "ok" : "aviso",
      p.chave_groq ? "" : "Sem GROQ_API_KEY no .env não há IA (roteiro, título, vídeo sem legenda)."),
    pilula(p.js_runtime ? "Node/Deno" : "Sem Node/Deno", p.js_runtime ? "ok" : "aviso",
      p.js_runtime ? "" : "Sem Node.js/Deno o YouTube entrega só vídeo de baixa qualidade (360p/720p) e pode falhar com erro 403. Instale o Node.js (nodejs.org) e reabra o interface.bat."),
  );
}

// ------------------------------------------------------------- gerar
function alternarOpcoes() {
  const modo = document.querySelector('input[name="modo"]:checked').value;
  $("opcoes-gameplay").hidden = modo !== "gameplay";
  $("campo-streamer").hidden = modo !== "gameplay";
  $("aviso-bruto").hidden = modo !== "gameplay";
}
document.querySelectorAll('input[name="modo"]').forEach((el) => el.addEventListener("change", alternarOpcoes));

function mostrarErroForm(msg) {
  $("erro-form").textContent = msg || "";
  $("erro-form").hidden = !msg;
}

function atualizarEstadoJob(job) {
  const selo = $("estado-job");
  const rodando = !!job && job.estado === "rodando";
  const nomes = { rodando: "Rodando", concluido: "Concluído", erro: "Terminou com erro", cancelado: "Cancelado" };
  selo.className = "selo" + (job ? ` ${job.estado}` : "");
  selo.textContent = job ? `${nomes[job.estado] || job.estado} · ${formatarTempo(job.segundos)}` : "Parado";
  $("btn-gerar").disabled = rodando;
  $("btn-parar").hidden = !rodando;
  if (job && !rodando) {
    const ok = job.estado === "concluido";
    $("fim-texto").textContent = ok
      ? "Execução concluída. Confira o resultado abaixo e veja os cortes."
      : job.estado === "cancelado" ? "Execução cancelada." : "A execução terminou com erro. Veja as últimas linhas do andamento.";
    $("fim-job").hidden = false;
  } else {
    $("fim-job").hidden = true;
  }
}

function acrescentarLog(linhas) {
  if (!linhas.length) return;
  const log = $("log");
  const embaixo = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  linhasNaTela.push(...linhas);
  if (linhasNaTela.length > MAX_LINHAS_TELA) linhasNaTela = linhasNaTela.slice(-MAX_LINHAS_TELA);
  log.textContent = linhasNaTela.join("\n");
  if ($("rolar").checked || embaixo) log.scrollTop = log.scrollHeight;
}

async function consultarLog() {
  try {
    const dados = await api(`/api/log?desde=${proximoLog}`);
    proximoLog = dados.proximo;
    acrescentarLog(dados.linhas);
    atualizarEstadoJob(dados.job);
    if (!dados.job || dados.job.estado !== "rodando") {
      pararConsulta();
      atualizarContador();
    }
  } catch (e) {
    acrescentarLog([`⚠ Não consegui falar com o servidor: ${e.message}`]);
    pararConsulta();
  }
}

function iniciarConsulta() {
  pararConsulta();
  temporizador = setInterval(consultarLog, 1000);
  consultarLog();
}
function pararConsulta() {
  if (temporizador) clearInterval(temporizador);
  temporizador = null;
}

$("form-gerar").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  mostrarErroForm("");
  const f = new FormData(ev.target);
  const modo = f.get("modo");
  const corpo = { url: String(f.get("url") || "").trim(), modo,
                  layout: f.get("layout") || "", fundo: f.get("fundo") || "",
                  repetir: f.get("repetir") === "on", importar_existentes: f.get("importar") === "on" };
  if (modo === "gameplay") {
    corpo.cortes = Number(f.get("cortes") || 5);
    corpo.streamer = String(f.get("streamer") || "").trim();
  }
  try {
    await post("/api/executar", corpo);
  } catch (e) {
    mostrarErroForm(e.message);
    return;
  }
  proximoLog = 0;
  linhasNaTela = [];
  $("log").textContent = "";
  iniciarConsulta();
});

$("btn-parar").addEventListener("click", async () => {
  if (!confirm("Parar a execução em andamento?")) return;
  try { await post("/api/parar"); } catch (e) { mostrarErroForm(e.message); }
});

// ------------------------------------------------------------- cortes
function selo(texto, classe) {
  const el = document.createElement("span");
  el.className = "selo" + (classe ? ` ${classe}` : "");
  el.textContent = texto;
  return el;
}

function fonteTitulo(valor) {
  return { ia_trecho_real: "título da IA (fala real)", ia_texto_real: "título da IA (fala real)",
           padrao: "título padrão (IA falhou)" }[valor] || null;
}

function montarCorte(c) {
  const no = $("tpl-corte").content.firstElementChild.cloneNode(true);
  no.classList.toggle("aprovado", c.estado === "aprovado");
  const video = no.querySelector("video");
  video.src = `/video?nome=${encodeURIComponent(c.nome)}`;
  no.querySelector(".titulo").textContent = c.titulo || c.nome.replace(/\.mp4$/i, "");
  const pergunta = no.querySelector(".pergunta");
  pergunta.textContent = c.pergunta;
  pergunta.hidden = !c.pergunta;

  const selos = no.querySelector(".selos");
  if (c.estado === "aprovado") selos.append(selo("Aprovado", "concluido"));
  if (c.nota != null) selos.append(selo(`nota ${c.nota}`));
  if (c.score != null) selos.append(selo(`score ${Number(c.score).toFixed(1)}`));
  if (c.modo) selos.append(selo(c.modo));
  if (c.bruto) selos.append(selo("bruto: sem edição", "aviso"));
  else if (c.edicao && c.edicao.estado === "renderizado") selos.append(selo("editado", "concluido"));
  if (c.resumo) selos.append(selo("com resumo no início"));
  if (c.cta) selos.append(selo("com chamada final"));
  const origem = fonteTitulo(c.fonte_titulo);
  if (origem) selos.append(selo(origem, c.fonte_titulo === "padrao" ? "cancelado" : ""));
  if (!c.tem_json) selos.append(selo("sem .json", "cancelado"));

  const partes = [c.nome, `${c.tamanho_mb} MB`, c.modificado.replace("T", " ")];
  if (c.inicio_s != null && c.fim_s != null) {
    partes.push(`trecho ${formatarTempo(Math.round(c.inicio_s))}–${formatarTempo(Math.round(c.fim_s))} do vídeo`);
  }
  no.querySelector(".meta").textContent = partes.join(" · ");

  const fala = no.querySelector(".fala");
  fala.hidden = !c.transcricao;
  no.querySelector(".transcricao").textContent = c.transcricao;

  const resumo = no.querySelector(".resumo-intro");
  resumo.hidden = !c.resumo;
  no.querySelector(".texto-resumo").textContent = c.resumo || "";

  const editar = no.querySelector(".editar");
  if (!c.editavel) selos.append(selo("sem versão bruta", "cancelado"));
  editar.addEventListener("click", () => {
    if (c.editavel) { edAbrir(c); return; }
    // O botão aparece em todos os cortes; sem o bruto guardado não há de onde editar, e a tela diz o porquê.
    if (c.editavel === undefined) {
      alert("O servidor da interface está desatualizado. Feche a janela preta do interface.bat e abra o interface.bat de novo.");
    } else if (c.modo && c.modo !== "gameplay") {
      alert("Só os cortes do modo Gameplay / Streamer podem ser editados.");
    } else {
      alert("Este corte não tem a versão bruta guardada (foi gerado antes do menu Editar, ou com CORTES_GAMEPLAY_BRUTO=0).\n\nGere o corte de novo, no modo Gameplay / Streamer, para poder editá-lo.");
    }
  });

  const aprovar = no.querySelector(".aprovar");
  aprovar.textContent = c.estado === "aprovado" ? "Desfazer aprovação" : "Aprovar";
  aprovar.addEventListener("click", async () => {
    await acaoCorte(c.nome, c.estado === "aprovado" ? "desfazer" : "aprovar");
  });
  no.querySelector(".copiar").addEventListener("click", async (ev) => {
    const texto = [c.titulo, c.pergunta].filter(Boolean).join("\n") || c.nome;
    try {
      await navigator.clipboard.writeText(texto);
      ev.target.textContent = "Copiado!";
    } catch (_) {
      ev.target.textContent = "Não consegui copiar";
    }
    setTimeout(() => { ev.target.textContent = "Copiar título"; }, 1800);
  });
  const baixar = no.querySelector(".baixar");
  baixar.href = video.src;
  baixar.setAttribute("download", c.nome);
  no.querySelector(".descartar").addEventListener("click", async () => {
    if (!confirm(`Descartar este corte?\n\n${c.titulo || c.nome}\n\nEle vai para a pasta “_descartados” (não é apagado).`)) return;
    await acaoCorte(c.nome, "descartar");
  });
  return no;
}

async function acaoCorte(nome, acao) {
  try {
    await post("/api/cortes/acao", { nome, acao });
  } catch (e) {
    alert(e.message);
  }
  carregarCortes();
}

function desenharCortes() {
  const filtro = $("filtro").value;
  const visiveis = cortes.filter((c) => filtro === "todos" || c.estado === filtro);
  $("lista").replaceChildren(...visiveis.map(montarCorte));
  $("vazio").hidden = visiveis.length > 0;
}

async function carregarCortes() {
  try {
    cortes = (await api("/api/cortes")).cortes;
  } catch (e) {
    cortes = [];
    alert(e.message);
  }
  $("contador").textContent = String(cortes.length);
  desenharCortes();
}

async function atualizarContador() {
  try { $("contador").textContent = String((await api("/api/cortes")).cortes.length); } catch (_) { /* ignora */ }
}

$("filtro").addEventListener("change", desenharCortes);
$("btn-atualizar").addEventListener("click", carregarCortes);
$("btn-pasta").addEventListener("click", async () => {
  try { await post("/api/pasta"); } catch (e) { alert(e.message); }
});

// ------------------------------------------------------------- início
(async function iniciar() {
  alternarOpcoes();
  try {
    const estado = await api("/api/estado");
    mostrarPrerequisitos(estado.prerequisitos);
    $("aviso-versao").hidden = Array.isArray(estado.recursos) && estado.recursos.includes("edicao") && estado.recursos.includes("linha_do_tempo");
    atualizarEstadoJob(estado.job);
    if (estado.job && estado.job.estado === "rodando") iniciarConsulta();
    else if (estado.job) consultarLog();
  } catch (e) {
    mostrarErroForm(e.message);
  }
  atualizarContador();
  if (location.hash === "#cortes") mostrarAba("cortes");   // link direto para a aba de cortes
})();
