"use strict";
/* Linha do tempo do menu Editar: régua, onda do áudio, quadros do vídeo, cursor, alças de início/fim, dividir/excluir
   trechos, zoom e desfazer/refazer. A matemática está em timeline-logica.js (sem DOM). Usa $ de app.js.
   Tudo é desenhado no navegador a partir do próprio corte bruto: nada é baixado nem enviado. Nada altera o vídeo bruto:
   os trechos mantidos só viram corte de verdade quando o servidor renderiza de novo. */

const TLUI = (() => {
  const S = {
    dur: 0, pecas: [], hist: null, zoom: 1, picos: null, picosFalhou: false,
    quadros: [],                    // canvases pequenos, um por posição
    ficha: 0,                       // muda a cada abertura: tarefas antigas (ondas, quadros) se descartam sozinhas
    src: "", pendente: null, rAF: 0,
    ondaUrl: "",                    // vídeo longo: a onda do áudio vem pronta do servidor (o arquivo é grande demais para o navegador)
    marcas: [],                     // vídeo longo: um marcador por momento [{n, a, b, titulo}]
    aoMudar: null,                  // chamado quando as peças mudam (o editor atualiza a lista de momentos)
  };
  const video = $("ed-video"), rolagem = $("tl-rolagem"), conteudo = $("tl-conteudo");
  const cReg = $("tl-regua"), cOnda = $("tl-ondas"), cQuad = $("tl-quadros");
  const MAX_LARGURA = 30000;

  const pronto = () => S.dur > 0 && S.pecas.length > 0;
  const tempoEm = (ev) => {
    const r = conteudo.getBoundingClientRect();
    return Math.min(Math.max((ev.clientX - r.left) / r.width, 0), 1) * S.dur;
  };
  function aviso(msg) { const el = $("tl-info"); el.dataset.aviso = msg || ""; atualizarInfo(); }

  // ------------------------------------------------------------ desenho
  function dimensionar(canvas, largura, altura) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(largura * dpr));
    canvas.height = Math.max(1, Math.round(altura * dpr));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  }

  function desenharRegua(W) {
    const ctx = dimensionar(cReg, W, cReg.clientHeight || 22);
    ctx.fillStyle = "#0f0f10"; ctx.fillRect(0, 0, W, 40);
    ctx.fillStyle = "#b5b5ba"; ctx.strokeStyle = "#55555b"; ctx.font = "11px system-ui, sans-serif"; ctx.textBaseline = "top";
    const passo = tlPassoRegua(S.dur / W, 70);
    for (let t = 0; t <= S.dur + 1e-6; t += passo) {
      const x = Math.round((t / S.dur) * W) + 0.5;
      ctx.beginPath(); ctx.moveTo(x, 12); ctx.lineTo(x, 22); ctx.stroke();
      ctx.fillText(tlFormatar(t), Math.min(x + 3, W - 34), 2);
    }
  }

  function desenharOndas(W) {
    const H = cOnda.clientHeight || 58;
    const ctx = dimensionar(cOnda, W, H);
    ctx.fillStyle = "#0f0f10"; ctx.fillRect(0, 0, W, H);
    const meio = H / 2;
    if (!S.picos) {
      ctx.strokeStyle = "#55555b"; ctx.beginPath(); ctx.moveTo(0, meio); ctx.lineTo(W, meio); ctx.stroke();
      ctx.fillStyle = "#8a8a90"; ctx.font = "11px system-ui, sans-serif"; ctx.textBaseline = "middle";
      ctx.fillText(S.picosFalhou ? "Não foi possível desenhar a onda do áudio (o corte continua editável)." : "Lendo o áudio…", 8, meio);
      return;
    }
    ctx.fillStyle = "#8d8d93";
    const n = S.picos.length;
    for (let x = 0; x < W; x += 2) {
      const i = Math.min(n - 1, Math.floor((x / W) * n)), j = Math.min(n - 1, Math.floor(((x + 2) / W) * n));
      let p = 0;
      for (let k = i; k <= j; k++) if (S.picos[k] > p) p = S.picos[k];
      const h = Math.max(1, p * (H - 6));
      ctx.fillRect(x, meio - h / 2, 1.4, h);
    }
  }

  function desenharQuadros(W) {
    const H = cQuad.clientHeight || 54;
    const ctx = dimensionar(cQuad, W, H);
    ctx.fillStyle = "#1a1a1c"; ctx.fillRect(0, 0, W, H);
    const n = S.quadros.length;
    if (!n) return;
    const larg = W / S.totalQuadros;
    for (let i = 0; i < n; i++) {
      const q = S.quadros[i];
      if (!q) continue;
      const x = i * larg;
      // "cover": preenche o bloco sem esticar
      const r = Math.max(larg / q.width, H / q.height), sw = larg / r, sh = H / r;
      ctx.drawImage(q, (q.width - sw) / 2, (q.height - sh) / 2, sw, sh, x, 0, larg + 1, H);
    }
  }

  function larguraAtual() {
    return Math.min(MAX_LARGURA, Math.max(200, Math.round((rolagem.clientWidth || 600) * S.zoom)));
  }

  function desenharTudo() {
    if (!pronto()) return;
    const W = larguraAtual();
    conteudo.style.width = `${W}px`;
    desenharRegua(W); desenharOndas(W); desenharQuadros(W);
    desenharMarcas();
    atualizarPecas();
  }

  /* marcadores dos momentos (vídeo longo), em porcentagem: acompanham o zoom sem redesenhar */
  function desenharMarcas() {
    const box = $("tl-marcas");
    box.replaceChildren();
    if (!S.dur) return;
    for (const m of S.marcas) {
      const a = Math.min(Math.max(m.a, 0), S.dur), b = Math.min(Math.max(m.b, 0), S.dur);
      if (b <= a) continue;
      const d = document.createElement("div");
      d.className = "tl-marca";
      d.style.left = `${(a / S.dur) * 100}%`;
      d.style.width = `${((b - a) / S.dur) * 100}%`;
      const r = document.createElement("span");
      r.className = "tl-marca-rot";
      r.textContent = `${m.n}`;
      d.title = `${m.n} · ${m.titulo || "momento"}`;
      d.appendChild(r);
      box.appendChild(d);
    }
  }

  /* peças, alças e cursor em porcentagem: acompanham o zoom sem redesenhar */
  function atualizarPecas() {
    if (!pronto()) return;
    const box = $("tl-pecas"), cur = tempoAtual();
    box.replaceChildren();
    const atual = tlPecaEm(S.pecas, cur);
    S.pecas.forEach((p, i) => {
      const d = document.createElement("div");
      d.className = `tl-peca${p.keep ? "" : " fora"}${i === atual ? " atual" : ""}`;
      d.style.left = `${(p.a / S.dur) * 100}%`;
      d.style.width = `${((p.b - p.a) / S.dur) * 100}%`;
      box.appendChild(d);
    });
    $("tl-ini").style.left = `${(tlInicio(S.pecas) / S.dur) * 100}%`;
    $("tl-fim").style.left = `${(tlFim(S.pecas) / S.dur) * 100}%`;
    $("tl-excluir").textContent = S.pecas[atual].keep ? "Excluir trecho" : "Restaurar trecho";
    $("tl-desfazer").disabled = !tlHistPodeDesfazer(S.hist);
    $("tl-refazer").disabled = !tlHistPodeRefazer(S.hist);
    atualizarInfo();
    if (S.aoMudar) S.aoMudar();
  }

  function tempoAtual() { return Math.min(Math.max(video.currentTime || 0, 0), S.dur || 0); }

  function atualizarCursor() {
    if (!pronto()) return;
    const t = tempoAtual();
    $("tl-cursor").style.left = `${(t / S.dur) * 100}%`;
    $("tl-tempo").textContent = tlFormatar(t);
  }

  function atualizarInfo() {
    const el = $("tl-info");
    if (!pronto()) { el.textContent = "Carregando o vídeo…"; return; }
    const mantido = tlDuracao(S.pecas), tr = tlTrechos(S.pecas);
    let txt = tr
      ? `Depois dos cortes o vídeo terá ${tlFormatar(mantido, true)} (de ${tlFormatar(S.dur, true)}) em ${tr.length} trecho${tr.length > 1 ? "s" : ""}.`
      : `Sem cortes: o vídeo inteiro (${tlFormatar(S.dur, true)}) será mantido.`;
    if (el.dataset.aviso) txt += ` ${el.dataset.aviso}`;
    el.textContent = txt;
  }

  // ------------------------------------------------------------ histórico e ações
  function aplicar(novo, mensagem) {
    if (!novo) return false;
    S.pecas = novo;
    tlHistAdicionar(S.hist, novo);
    aviso(mensagem || "");
    atualizarPecas();
    return true;
  }

  $("tl-dividir").addEventListener("click", () => {
    if (!pronto()) return;
    if (!aplicar(tlDividir(S.pecas, tempoAtual()))) aviso("Não dá para dividir aqui: leve o cursor para o meio de um trecho (e há um limite de trechos).");
  });
  $("tl-excluir").addEventListener("click", () => {
    if (!pronto()) return;
    const i = tlPecaEm(S.pecas, tempoAtual());
    if (!aplicar(tlAlternar(S.pecas, i))) aviso("O vídeo precisa ficar com pelo menos 1 segundo.");
  });
  function desfazer() {
    if (!pronto() || !tlHistPodeDesfazer(S.hist)) return;
    S.pecas = tlHistDesfazer(S.hist); aviso(""); atualizarPecas();
  }
  function refazer() {
    if (!pronto() || !tlHistPodeRefazer(S.hist)) return;
    S.pecas = tlHistRefazer(S.hist); aviso(""); atualizarPecas();
  }
  $("tl-desfazer").addEventListener("click", desfazer);
  $("tl-refazer").addEventListener("click", refazer);
  $("dlg-editar").addEventListener("keydown", (ev) => {
    if (!(ev.ctrlKey || ev.metaKey) || /^(INPUT|TEXTAREA)$/.test((ev.target.tagName || ""))) return;
    const k = ev.key.toLowerCase();
    if (k === "z" && !ev.shiftKey) { ev.preventDefault(); desfazer(); }
    else if (k === "y" || (k === "z" && ev.shiftKey)) { ev.preventDefault(); refazer(); }
  });

  $("tl-zoom").addEventListener("input", () => {
    if (!pronto()) return;
    const antes = tempoAtual() / S.dur;
    S.zoom = Number($("tl-zoom").value) || 1;
    desenharTudo();
    rolagem.scrollLeft = Math.max(0, antes * conteudo.clientWidth - rolagem.clientWidth / 2);   // o cursor fica no meio
  });

  // ------------------------------------------------------------ ponteiro: ir a um ponto e arrastar as alças
  conteudo.addEventListener("pointerdown", (ev) => {
    if (!pronto() || ev.target.classList.contains("tl-alca")) return;
    ev.preventDefault();
    try { conteudo.setPointerCapture(ev.pointerId); } catch (_) { /* sem captura, segue no elemento */ }
    const ir = (e) => { video.currentTime = tempoEm(e); atualizarCursor(); atualizarPecas(); };
    const soltar = () => {
      conteudo.removeEventListener("pointermove", ir);
      conteudo.removeEventListener("pointerup", soltar);
      conteudo.removeEventListener("pointercancel", soltar);
    };
    ir(ev);
    conteudo.addEventListener("pointermove", ir);
    conteudo.addEventListener("pointerup", soltar);
    conteudo.addEventListener("pointercancel", soltar);
  });

  for (const lado of ["ini", "fim"]) {
    const alca = $(`tl-${lado}`);
    alca.addEventListener("pointerdown", (ev) => {
      if (!pronto()) return;
      ev.preventDefault(); ev.stopPropagation();
      try { alca.setPointerCapture(ev.pointerId); } catch (_) { /* idem */ }
      const base = S.pecas.map((p) => ({ ...p })), antes = JSON.stringify(base);
      const mover = (e) => {
        const t = tempoEm(e);
        S.pecas = tlAparar(base, lado, t);
        video.currentTime = Math.min(Math.max(t, 0), S.dur);
        atualizarCursor(); atualizarPecas();
      };
      const soltar = () => {
        alca.removeEventListener("pointermove", mover);
        alca.removeEventListener("pointerup", soltar);
        alca.removeEventListener("pointercancel", soltar);
        if (JSON.stringify(S.pecas) !== antes) tlHistAdicionar(S.hist, S.pecas);
        atualizarPecas();
      };
      alca.addEventListener("pointermove", mover);
      alca.addEventListener("pointerup", soltar);
      alca.addEventListener("pointercancel", soltar);
    });
  }

  // ------------------------------------------------------------ acompanhar a reprodução (e pular o que foi excluído)
  function laco() {
    S.rAF = 0;
    if (!pronto()) return;
    if (!video.paused && !video.ended) {
      const t = tempoAtual(), prox = tlProximoMantido(S.pecas, t);
      if (prox === null) { video.pause(); video.currentTime = tlFim(S.pecas); }
      else if (prox !== t) video.currentTime = prox;
      atualizarCursor();
      S.rAF = requestAnimationFrame(laco);
    }
  }
  const iniciarLaco = () => { if (!S.rAF) S.rAF = requestAnimationFrame(laco); };
  video.addEventListener("play", iniciarLaco);
  video.addEventListener("playing", iniciarLaco);
  for (const nome of ["timeupdate", "seeked", "pause"]) video.addEventListener(nome, () => { atualizarCursor(); if (nome !== "timeupdate") atualizarPecas(); });

  video.addEventListener("loadedmetadata", () => {
    if (!S.src || !isFinite(video.duration) || video.duration <= 0) return;
    S.dur = video.duration;
    S.pecas = tlDeTrechos(S.pendente, S.dur);
    S.hist = tlHistNovo(S.pecas);
    desenharTudo(); atualizarCursor();
    carregarOnda(S.ficha); carregarQuadros(S.ficha);
  });
  if (typeof ResizeObserver === "function") new ResizeObserver(() => { if (pronto()) desenharTudo(); }).observe(rolagem);

  // ------------------------------------------------------------ onda do áudio (decodificada no navegador)
  async function carregarOnda(ficha) {
    let ctx = null;
    try {
      if (S.ondaUrl) {                                  // vídeo longo: picos calculados pelo servidor (baixar 600 MB aqui travaria)
        const resp = await fetch(S.ondaUrl, { credentials: "same-origin" });
        const dados = await resp.json().catch(() => ({}));
        if (!resp.ok || !Array.isArray(dados.picos) || !dados.picos.length) throw new Error(dados.erro || `HTTP ${resp.status}`);
        if (ficha !== S.ficha) return;
        S.picos = Float32Array.from(dados.picos); S.picosFalhou = false;
        desenharTudo();
        return;
      }
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) throw new Error("sem AudioContext");
      const resp = await fetch(S.src);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const dados = await resp.arrayBuffer();
      if (ficha !== S.ficha) return;
      ctx = new AC();
      const audio = await ctx.decodeAudioData(dados);
      if (ficha !== S.ficha) return;
      const N = 2400, picos = new Float32Array(N), canais = [];
      for (let c = 0; c < audio.numberOfChannels; c++) canais.push(audio.getChannelData(c));
      const por = Math.max(1, Math.floor(audio.length / N));
      let maximo = 0;
      for (let i = 0; i < N; i++) {
        let p = 0;
        const fim = Math.min(audio.length, (i + 1) * por);
        for (const dado of canais) for (let k = i * por; k < fim; k += 4) { const v = Math.abs(dado[k]); if (v > p) p = v; }
        picos[i] = p; if (p > maximo) maximo = p;
      }
      for (let i = 0; i < N; i++) picos[i] = maximo > 0 ? Math.sqrt(picos[i] / maximo) : 0;    // raiz: mostra melhor o que é baixo
      S.picos = picos; S.picosFalhou = false;
    } catch (_) {
      if (ficha !== S.ficha) return;
      S.picos = null; S.picosFalhou = true;
    } finally {
      if (ctx && ctx.close) { try { ctx.close(); } catch (_) { /* nada */ } }
    }
    if (ficha === S.ficha) desenharTudo();
  }

  // ------------------------------------------------------------ quadros do vídeo (um segundo <video> escondido tira os "prints")
  async function carregarQuadros(ficha) {
    const v = document.createElement("video");
    v.muted = true; v.preload = "auto"; v.playsInline = true;
    const esperar = (alvo, evento, ms) => new Promise((ok) => {
      let feito = false;
      const fim = () => { if (!feito) { feito = true; alvo.removeEventListener(evento, fim); ok(); } };
      alvo.addEventListener(evento, fim); setTimeout(fim, ms);
    });
    try {
      v.src = S.src;
      await esperar(v, "loadedmetadata", 8000);
      if (ficha !== S.ficha || !v.videoWidth) return;
      const N = Math.min(60, Math.max(8, Math.round(S.dur / 2)));
      S.totalQuadros = N; S.quadros = new Array(N).fill(null);
      const h = 90, w = Math.max(16, Math.round((h * v.videoWidth) / v.videoHeight));
      for (let i = 0; i < N; i++) {
        if (ficha !== S.ficha) return;
        v.currentTime = Math.min(S.dur - 0.05, ((i + 0.5) / N) * S.dur);
        await esperar(v, "seeked", 4000);
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        try { c.getContext("2d").drawImage(v, 0, 0, w, h); } catch (_) { return; }
        S.quadros[i] = c;
        if (i % 3 === 2 || i === N - 1) desenharQuadros(larguraAtual());
      }
    } finally {
      v.removeAttribute("src"); v.load();
    }
  }

  // ------------------------------------------------------------ API para o editor
  return {
    /* `extra` (vídeo longo): { ondaUrl, marcas: [{n, a, b, titulo}], zoomMax } */
    abrir(src, trechos, extra) {
      S.ficha += 1;
      extra = extra || {};
      Object.assign(S, { dur: 0, pecas: [], hist: tlHistNovo([{ a: 0, b: 1, keep: true }]), zoom: 1, picos: null, picosFalhou: false,
                         quadros: [], totalQuadros: 0, src, pendente: trechos || null,
                         ondaUrl: extra.ondaUrl || "", marcas: extra.marcas || [] });
      $("tl-zoom").max = String(extra.zoomMax || 8);
      $("tl-marcas").replaceChildren();
      $("tl-zoom").value = "1";
      $("tl-info").dataset.aviso = "";
      rolagem.scrollLeft = 0;
      conteudo.style.width = "100%";
      for (const c of [cReg, cOnda, cQuad]) c.getContext("2d").clearRect(0, 0, c.width, c.height);
      $("tl-pecas").replaceChildren();
      $("tl-tempo").textContent = "00:00";
      $("tl-desfazer").disabled = true; $("tl-refazer").disabled = true;
      atualizarInfo();
    },
    fechar() { S.ficha += 1; S.src = ""; S.aoMudar = null; if (S.rAF) { cancelAnimationFrame(S.rAF); S.rAF = 0; } },
    pronto,
    duracao() { return S.dur; },
    aoMudar(fn) { S.aoMudar = fn; },
    irPara(t) { if (!pronto()) return; video.currentTime = Math.min(Math.max(t, 0), S.dur); atualizarCursor(); atualizarPecas();
                const x = (video.currentTime / S.dur) * conteudo.clientWidth;               // traz o cursor para a parte visível
                if (x < rolagem.scrollLeft || x > rolagem.scrollLeft + rolagem.clientWidth) rolagem.scrollLeft = Math.max(0, x - rolagem.clientWidth / 2); },
    /* fração (0 a 1) do intervalo que continua no vídeo */
    fracaoMantida(a, b) { return pronto() ? tlFracaoMantida(S.pecas, a, b) : 1; },
    /* exclui o intervalo (se estiver mais mantido que excluído) ou o restaura; false se não puder (mínimo de 1 s, limite de trechos) */
    alternarIntervalo(a, b) {
      if (!pronto()) return false;
      const novo = tlMarcarIntervalo(S.pecas, a, b, tlFracaoMantida(S.pecas, a, b) < 0.5);
      return aplicar(novo);
    },
    /* trechos mantidos para o servidor (null = corte inteiro) */
    trechos() { return pronto() ? tlTrechos(S.pecas) : null; },
  };
})();
