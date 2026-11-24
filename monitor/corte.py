"""Do momento detectado ao corte BRUTO em "Cortes prontos" (a mesma coisa que o modo Gameplay faz com um vídeo).

Para cada momento que o monitor detecta:
  1. transcreve a fala do corte candidato (Whisper do Groq);
  2. a IA (Groq) lê a transcrição e os sinais e decide: serve como corte? nota 0-10, motivo, título e pergunta;
  3. se servir, guarda o corte BRUTO (sem layout, tarja nem legenda) em cortes_prontos/_brutos + uma cópia visível em
     cortes_prontos, com o .json no mesmo formato do modo Gameplay (legenda_segmentos, bruto, edicao) mais a origem
     (live, canal, sinais). Dali segue o fluxo normal: Editar, linha do tempo, aprovar.
Sem chave do Groq ou sem fala, vale só a nota dos sinais (mais exigente) e o corte vai sem legenda.
A IA nunca vê a imagem e nunca inventa tempo: o corte é a janela que o monitor ancorou no pico.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

NOTA_MINIMA_IA = 6.0          # score 0-10 que a IA precisa dar para o corte ir para "Cortes prontos"
NOTA_MINIMA_SINAIS = 7.0      # sem IA (sem chave, sem fala ou IA fora do ar): nota dos sinais (0-10) mínima
PALAVRAS_MINIMAS = 6          # menos palavras que isso = "sem fala"
TEXTO_MAX_CHARS = 2400        # quanto da transcrição vai para a IA
BRUTOS = "_brutos"
RE_NOME = re.compile(r"[^a-z0-9_-]+")


def ler_chave_groq(raiz):
    """Chave do Groq (variável de ambiente ou .env da pasta do projeto) ou None. Nunca é escrita em log."""
    valor = os.environ.get("GROQ_API_KEY", "").strip()
    if not valor:
        try:
            for linha in (Path(raiz) / ".env").read_text(encoding="utf-8-sig", errors="replace").splitlines():
                linha = linha.strip()
                if linha.startswith("export "):
                    linha = linha[7:].strip()
                if linha.startswith("GROQ_API_KEY") and "=" in linha:
                    valor = linha.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except OSError:
            pass
    return valor if valor and valor != "cole_sua_chave_aqui" else None


def _gerar_cortes(raiz):
    """Importa o gerar_cortes.py (Groq, Whisper, utilidades de JSON) só quando precisa."""
    if str(raiz) not in sys.path:
        sys.path.insert(0, str(raiz))
    import gerar_cortes
    gerar_cortes.load_env_file(Path(raiz) / ".env")
    return gerar_cortes


def _padrao_executar(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def duracao_do_video(ffmpeg, caminho, executar=None):
    """Duração em segundos (pelo cabeçalho que o ffmpeg imprime); None se não der para ler."""
    executar = executar or _padrao_executar
    try:
        r = executar([str(ffmpeg), "-hide_banner", "-i", str(caminho)], 30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    achado = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", (getattr(r, "stderr", "") or "") + (getattr(r, "stdout", "") or ""))
    if not achado:
        return None
    h, m, s = achado.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def extrair_audio(ffmpeg, video, saida, executar=None):
    """MP3 mono de 16 kHz (o que o Whisper precisa). True se criou o arquivo."""
    executar = executar or _padrao_executar
    cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
           "-b:a", "64k", "-f", "mp3", str(saida)]
    try:
        r = executar(cmd, 120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return getattr(r, "returncode", 1) == 0 and Path(saida).is_file() and Path(saida).stat().st_size > 0


def transcrever_groq(raiz, audio, chave):
    """Segmentos [{start, end, text, words?}] com tempo relativo ao início do áudio. [] se falhar."""
    g = _gerar_cortes(raiz)
    return g.transcribe_audio_groq(Path(audio), chave) or []


def montar_prompt(texto, resumo, duracao, canal_nome):
    sinais = "; ".join(str(d) for d in (resumo.get("detalhes") or [])[:6]) or "sem detalhes"
    tipos = ", ".join(sorted((resumo.get("sinais") or {}).keys())) or "nenhum"
    return (
        "Você é um editor especialista em Shorts/TikTok/Reels de games e lives. Abaixo está a transcrição EXATA da fala "
        f"de um trecho de {duracao:.0f} segundos de uma live de {canal_nome or 'um streamer'}, que o sistema separou "
        "porque houve um pico (volume, chat ou palavras de reação). Decida se vale virar um corte.\n\n"
        "ACEITE: reação explosiva, história engraçada ou chocante, clutch/vitória, fail épico, frase de impacto, risada.\n"
        "REJEITE: saudações, enrolação, explicação de contexto, conversa com o chat sem graça, texto sem sentido "
        "(transcrição ruim) ou fala cortada que não faz sentido sozinha.\n\n"
        f"SINAIS DETECTADOS (só contexto, NÃO são fatos do vídeo): {tipos}; {sinais}\n\n"
        f"TRANSCRIÇÃO:\n{texto[:TEXTO_MAX_CHARS]}\n\n"
        "Responda APENAS com JSON válido neste formato:\n"
        '{"aprovar": true, "score": 8.5, "reason": "motivo curto (máx 60 caracteres)", '
        '"titulo_gancho": "TÍTULO EM CAIXA ALTA, MÁX 5 PALAVRAS", "pergunta_cta": "PERGUNTA CURTA?"}\n\n'
        "Regras:\n"
        "- score: de 0 a 10, o potencial viral do trecho.\n"
        "- titulo_gancho e pergunta_cta: baseados SOMENTE no que foi dito na transcrição. NÃO invente fatos, nomes nem "
        "acontecimentos. Se o texto não fizer sentido, use \"\" nos dois.\n"
        "- Se não for bom, use \"aprovar\": false."
    )


def julgar_groq(raiz, chave, texto, resumo, duracao, canal_nome):
    """-> {"aprovar", "score", "reason", "titulo_gancho", "pergunta_cta"} ou None se a IA não respondeu direito."""
    g = _gerar_cortes(raiz)
    resposta = g.groq_chat_with_fallback([{"role": "user", "content": montar_prompt(texto, resumo, duracao, canal_nome)}],
                                         chave, g.get_text_models_to_try, max_tokens=500, temperature=0.3)
    if not resposta:
        return None
    dados = g.extract_json(resposta)
    if not isinstance(dados, dict):
        return None
    try:
        score = float(dados.get("score"))
    except (TypeError, ValueError):
        return None
    if score != score:                                          # NaN
        return None
    aprovar = dados.get("aprovar")
    return {"aprovar": bool(aprovar) if isinstance(aprovar, bool) else score >= NOTA_MINIMA_IA,
            "score": max(0.0, min(10.0, score)), "reason": str(dados.get("reason") or "").strip()[:60],
            "titulo_gancho": g._clean_hook_title(dados.get("titulo_gancho")),
            "pergunta_cta": g._clean_hook_question(dados.get("pergunta_cta"))}


def segmentos_do_corte(segmentos):
    """Garante o formato do .json: segmentos com start/end/text (e words com word/start/end), tempos em segundos do corte."""
    saida = []
    for s in segmentos or []:
        try:
            item = {"start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3), "text": str(s["text"]).strip()}
        except (KeyError, TypeError, ValueError):
            continue
        if not item["text"]:
            continue
        palavras = []
        for w in s.get("words") or []:
            try:
                palavras.append({"word": str(w["word"]), "start": round(float(w["start"]), 3), "end": round(float(w["end"]), 3)})
            except (KeyError, TypeError, ValueError):
                continue
        if palavras:
            item["words"] = palavras
        saida.append(item)
    return saida


class ProdutorCortes:
    """O gancho `ao_momento` do MonitorCanal: chame com (momento, caminho_do_corte_candidato)."""

    def __init__(self, raiz, canal, ffmpeg, chave=None, transcrever=None, julgar=None, executar=None, agora=datetime.now,
                 pasta_temp=None, registrar=None):
        """canal: {"plataforma", "canal", "nome", "layout", "url"} do cadastro (o que vai para o .json do corte)."""
        self.raiz = Path(raiz)
        self.saida = self.raiz / "cortes_prontos"
        self.canal, self.ffmpeg, self.executar, self.agora = canal, ffmpeg, executar, agora
        self.chave = chave if chave is not None else ler_chave_groq(self.raiz)
        self.transcrever = transcrever or (lambda audio: transcrever_groq(self.raiz, audio, self.chave))
        self.julgar = julgar or (lambda texto, resumo, dur: julgar_groq(self.raiz, self.chave, texto, resumo, dur, canal.get("nome")))
        self.pasta_temp = Path(pasta_temp or (self.raiz / "temp_downloads" / "lives"))
        self.log = registrar or (lambda msg, nivel="INFO": print(f"[{nivel}] {msg}", flush=True))

    # ------------------------------------------------------------------ decisão
    def _decidir(self, momento, resumo, texto, palavras, duracao):
        """-> (aprovado, fonte, score 0-10, motivo, ia). ia é o dicionário da IA (ou None)."""
        nota_sinais = float(resumo.get("nota") or 0)
        if self.chave and palavras >= PALAVRAS_MINIMAS:
            try:
                ia = self.julgar(texto, resumo, duracao)
            except Exception as e:                                   # a IA nunca derruba o monitor
                self.log(f"a IA falhou ao julgar o momento: {e}", "WARN")
                ia = None
            if ia is not None:
                ok = bool(ia["aprovar"]) and ia["score"] >= NOTA_MINIMA_IA
                return ok, "ia", ia["score"], ia["reason"] or ("aprovado pela IA" if ok else "a IA não achou bom"), ia
            motivo = "IA indisponível; vale a nota dos sinais"
        elif not self.chave:
            motivo = "sem chave do Groq; vale a nota dos sinais"
        else:
            motivo = "quase sem fala; vale a nota dos sinais"
        ok = nota_sinais >= NOTA_MINIMA_SINAIS
        return ok, "sinais", nota_sinais, motivo if ok else f"{motivo} ({nota_sinais:.1f} < {NOTA_MINIMA_SINAIS:.1f})", None

    # ------------------------------------------------------------------ gancho
    def __call__(self, momento, caminho):
        if not caminho or not Path(caminho).is_file():
            return {"resultado": "sem_corte", "motivo": "o corte candidato não foi extraído"}
        resumo = momento.resumo()
        duracao = duracao_do_video(self.ffmpeg, caminho, self.executar) or (momento.t1 - momento.t0)
        self.pasta_temp.mkdir(parents=True, exist_ok=True)
        segmentos, texto = [], ""
        if self.chave:
            audio = self.pasta_temp / f"audio_{Path(caminho).stem}.mp3"
            try:
                if extrair_audio(self.ffmpeg, caminho, audio, self.executar):
                    segmentos = segmentos_do_corte(self.transcrever(audio))
            except Exception as e:
                self.log(f"não foi possível transcrever o corte: {e}", "WARN")
                segmentos = []
            finally:
                try:
                    audio.unlink()
                except OSError:
                    pass
            texto = " ".join(s["text"] for s in segmentos).strip()
        palavras = len(texto.split())
        ok, fonte, score, motivo, ia = self._decidir(momento, resumo, texto, palavras, duracao)
        base = {"fonte": fonte, "score": round(score, 1), "motivo": motivo}
        if not ok:
            self.log(f"momento descartado ({fonte}, {score:.1f}): {motivo}")
            return {"resultado": "descartado", **base}
        try:
            nome = self._exportar(momento, resumo, Path(caminho), duracao, segmentos, texto, palavras, fonte, score, motivo, ia)
        except OSError as e:
            self.log(f"não foi possível guardar o corte bruto: {e}", "ERROR")
            return {"resultado": "erro", "motivo": f"não foi possível guardar o corte: {e}"}
        self.log(f"corte bruto enviado para Cortes prontos: {nome} (nota {round(score * 10)}/100, {fonte})", "CLIP")
        return {"resultado": "enviado", "arquivo": nome, **base}

    # ------------------------------------------------------------------ gravação
    def _nome(self, canal):
        base = f"live_{RE_NOME.sub('-', str(canal.get('canal', 'canal')).lower().lstrip('@')).strip('-')[:30] or 'canal'}_{self.agora().strftime('%Y%m%d_%H%M%S')}"
        nome, n = f"{base}.mp4", 2
        while (self.saida / nome).exists():
            nome, n = f"{base}_{n}.mp4", n + 1
        return nome

    def _exportar(self, momento, resumo, candidato, duracao, segmentos, texto, palavras, fonte, score, motivo, ia):
        canal = self.canal
        pasta_brutos = self.saida / BRUTOS
        pasta_brutos.mkdir(parents=True, exist_ok=True)
        nome = self._nome(canal)
        bruto, visivel = pasta_brutos / nome, self.saida / nome
        tmp_bruto = pasta_brutos / (nome + ".tmp")
        shutil.copy2(str(candidato), str(tmp_bruto))                       # o candidato continua na pasta da live
        tmp_bruto.replace(bruto)
        titulo = (ia or {}).get("titulo_gancho") or ""
        pergunta = (ia or {}).get("pergunta_cta") or ""
        com_gancho = bool(titulo and pergunta)
        meta = {
            "titulo_gancho": titulo if com_gancho else "MOMENTO INCRÍVEL NA LIVE",
            "pergunta_cta": pergunta if com_gancho else "O QUE VOCÊ ACHOU?",
            "nota": int(round(score * 10)),
            "transcricao": texto,
            "transcricao_fonte": "transcrição real do corte (live)" if texto else "sem fala transcrita",
            "palavras": palavras,
            "palavras_por_segundo": round(palavras / duracao, 2) if duracao else None,
            "fonte_titulo": "ia_texto_real" if com_gancho else "padrao",
            "score_selecao": round(score, 2),
            "motivo_selecao": motivo,
            "fonte_selecao": fonte,
            "clip_start": 0.0,
            "clip_end": round(duracao, 2),
            "clip_duration": round(duracao, 2),
            "modo": "gameplay",
            "streamer": canal.get("nome") or None,
            "bruto": True,
            "edicao": {"estado": "bruto"},
            "legenda_segmentos": segmentos,
            "duracao_final_s": round(duracao, 2),
            "origem": "live",
            "live": {"plataforma": canal.get("plataforma"), "canal": canal.get("canal"), "nome": canal.get("nome"),
                     "url": canal.get("url"), "layout_sugerido": canal.get("layout") or None,
                     "pico_t": resumo.get("pico_t"), "nota_sinais": resumo.get("nota"), "sinais": resumo.get("sinais"),
                     "detalhes": resumo.get("detalhes"), "detectado_em": self.agora().isoformat(timespec="seconds")},
            "timestamp": self.agora().isoformat(),
        }
        arq_json = visivel.with_suffix(".json")
        tmp_json = arq_json.with_suffix(".tmp")
        tmp_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_json.replace(arq_json)                                           # o .json antes do vídeo: o card já nasce completo
        tmp_video = self.saida / (nome + ".tmp")
        shutil.copy2(str(bruto), str(tmp_video))
        tmp_video.replace(visivel)                                           # só agora o corte aparece na lista
        return nome
