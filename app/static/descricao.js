"use strict";
/* Descrição para postar, nos cards de cortes e de vídeos longos: campo editável, "Sugerir descrições" (a IA lê a fala do corte
   ou os momentos do vídeo; as opções aparecem como botões, como os títulos), salvar e copiar. A descrição NÃO entra no vídeo:
   fica no .json dele, então vale na hora, sem renderizar de novo (serve também para cortes antigos, sem versão bruta).
   Carregado ANTES do app.js (os cards são montados assim que a lista chega); só usa $ e post de app.js quando é chamado. Todo texto vindo do servidor entra na página com textContent/value, nunca como HTML. */

const DESC_PREVIA = 150;                 // quanto de cada sugestão aparece no botão (o texto inteiro vai para o campo ao clicar)

function descricaoMontar(no, item, lib) {
  const caixa = no.querySelector(".descricao");
  if (!caixa) return;
  const texto = caixa.querySelector(".desc-texto"), titulo = caixa.querySelector("summary");
  const btnSugerir = caixa.querySelector(".desc-sugerir"), btnSalvar = caixa.querySelector(".desc-salvar");
  const btnCopiar = caixa.querySelector(".desc-copiar"), estado = caixa.querySelector(".desc-estado");
  const erro = caixa.querySelector(".desc-erro"), lista = caixa.querySelector(".desc-sugestoes");
  const rotulo = lib === "longos" ? "Descrição do vídeo (para o YouTube)" : "Descrição para postar";
  let salva = item.descricao || "";
  texto.value = salva;

  const mostrarErro = (msg) => { erro.textContent = msg || ""; erro.hidden = !msg; };
  const mostrarEstado = (msg) => {
    estado.textContent = msg || ""; estado.hidden = !msg;
    if (msg) setTimeout(() => { if (estado.textContent === msg) estado.hidden = true; }, 2200);
  };
  const atualizar = () => {
    titulo.textContent = salva ? `${rotulo} ✓` : rotulo;
    btnSalvar.disabled = texto.value === salva;
    btnCopiar.disabled = !texto.value.trim();
  };
  texto.addEventListener("input", atualizar);
  atualizar();

  btnSugerir.addEventListener("click", async () => {
    mostrarErro("");
    btnSugerir.disabled = true; btnSugerir.textContent = "Pedindo à IA…";
    try {
      const d = await post(`/api/${lib}/descricoes`, { nome: item.nome });
      lista.replaceChildren(...(d.descricoes || []).map((s) => {
        const li = document.createElement("li");
        const b = document.createElement("button");
        b.type = "button"; b.className = "btn";
        b.textContent = s.descricao.length > DESC_PREVIA ? `${s.descricao.slice(0, DESC_PREVIA)}…` : s.descricao;
        b.title = s.descricao;
        b.addEventListener("click", () => {
          texto.value = s.descricao;
          for (const o of lista.querySelectorAll(".btn")) o.classList.toggle("escolhido", o === b);
          atualizar();
          mostrarEstado("Colocada no campo: confira e clique em Salvar");
        });
        li.append(b);
        return li;
      }));
    } catch (e) {
      mostrarErro(e.message);
    } finally {
      btnSugerir.disabled = false; btnSugerir.textContent = "Sugerir descrições";
    }
  });

  btnSalvar.addEventListener("click", async () => {
    mostrarErro("");
    btnSalvar.disabled = true;
    try {
      const d = await post(`/api/${lib}/descricao`, { nome: item.nome, descricao: texto.value });
      salva = d.descricao || ""; item.descricao = salva; texto.value = salva;
      mostrarEstado(salva ? "Descrição salva" : "Descrição apagada");
    } catch (e) {
      mostrarErro(e.message);
    }
    atualizar();
  });

  btnCopiar.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(texto.value);
      mostrarEstado("Copiada!");
    } catch (_) {
      mostrarErro("Não consegui copiar: selecione o texto e use Ctrl+C.");
    }
  });
}
