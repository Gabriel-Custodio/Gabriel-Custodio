"""Do momento detectado ao corte BRUTO em "Cortes prontos" (a mesma coisa que o modo Gameplay faz com um vídeo).

Quem decide é a NOTA do momento (0-10, a que o painel mostra): abaixo de 6,5 o corte é descartado; de 6,5 a 7,5 entra com
a marcação "Potencial Médio"; acima de 7,5 (7,6 a 10) entra com "Potencial Alto". A IA NÃO veta: para os momentos que
entram, ela lê a transcrição e escreve o título, a pergunta e o motivo (e dá a própria nota, guardada à parte).
O corte é o candidato que o monitor extraiu (a janela ancorada no pico) e vai para cortes_prontos/_brutos + uma cópia
visível em cortes_prontos, com o .json no formato do modo Gameplay (legenda_segmentos, bruto, edicao) e a origem (live,
canal, sinais). Dali segue o fluxo normal: Editar, linha do tempo, aprovar. Nome: CorteLive_AAAAMMDD_HHMMSS_notaN.N.mp4.
Sem chave do Groq ou sem fala, o corte vai igual, sem legenda e com o título padrão.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .sinais import NOTA_DESCARTE      # 6,5: nota (0-10) abaixo disso o corte é descartado (a mesma conta do limite por hora)

NOTA_ALTO = 7.5               # acima disso (7,6 a 10): Potencial Alto; de 6,5 a 7,5: Potencial Médio
PALAVRAS_MINIMAS = 6          # menos palavras que isso = "sem fala": a IA nem é chamada
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
        f"de um trecho de {duracao:.0f} segundos de uma live de {canal_nome or 'um streamer'}, que o sistema JÁ escolheu "
        "como corte porque houve um pico (volume, chat ou palavras de reação). Seu trabalho é escrever o título, a "
        "pergunta e dar uma nota ao trecho.\n\n"
        "Uma nota alta é para reação explosiva, história engraçada ou chocante, clutch/vitória, fail épico, frase de "
        "impacto, risada. Nota baixa é para saudação, enrolação, explicação de contexto, leitura de doações, conversa "
        "sem graça ou texto sem sentido (transcrição ruim).\n\n"
        f"SINAIS DETECTADOS (só contexto, NÃO são fatos do vídeo): {tipos}; {sinais}\n\n"
        f"TRANSCRIÇÃO:\n{texto[:TEXTO_MAX_CHARS]}\n\n"
        "Responda APENAS com JSON válido neste formato:\n"
        '{"score": 8.5, "reason": "motivo curto (máx 60 caracteres)", '
        '"titulo_gancho": "TÍTULO EM CAIXA ALTA, MÁX 5 PALAVRAS", "pergunta_cta": "PERGUNTA CURTA?"}\n\n'
        "Regras:\n"
        "- score: de 0 a 10, o potencial viral do trecho.\n"
        "- titulo_gancho e pergunta_cta: baseados SOMENTE no que foi dito na transcrição. NÃO invente fatos, nomes nem "
        "acontecimentos. Se o texto não fizer sentido, use \"\" nos dois."
    )


def julgar_groq(raiz, chave, texto, resumo, duracao, canal_nome):
    """-> {"score" (ou None), "reason", "titulo_gancho", "pergunta_cta"} ou None se a IA não respondeu direito.

    A IA não aprova nem reprova: quem decide se o corte entra é a nota do momento.
    """
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
        score = None if score != score else max(0.0, min(10.0, score))      # NaN = sem nota
    except (TypeError, ValueError):
        score = None
    titulo, pergunta = g._clean_hook_title(dados.get("titulo_gancho")), g._clean_hook_question(dados.get("pergunta_cta"))
    if score is None and not (titulo and pergunta):
        return None
    return {"score": score, "reason": str(dados.get("reason") or "").strip()[:60],
            "titulo_gancho": titulo, "pergunta_cta": pergunta}


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
    @staticmethod
    def classificar(nota):
        """(nota arredondada em 1 casa, "descartado" | "medio" | "alto"): a mesma nota que aparece no nome e no painel."""
        n = round(float(nota or 0), 1)
        if n < NOTA_DESCARTE:
            return n, "descartado"
        return n, ("medio" if n <= NOTA_ALTO else "alto")

    def _julgar(self, texto, resumo, duracao, palavras):
        """A IA só escreve título, pergunta e motivo (e dá a nota dela). None se não houver chave, fala ou resposta."""
        if not self.chave or palavras < PALAVRAS_MINIMAS:
            return None
        try:
            return self.julgar(texto, resumo, duracao)
        except Exception as e:                                       # a IA nunca derruba o monitor
            self.log(f"a IA falhou ao escrever o título do corte: {e}", "WARN")
            return None

    def _apagar(self, caminho):
        try:
            Path(caminho).unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------ gancho
    def __call__(self, momento, caminho):
        if not caminho or not Path(caminho).is_file():
            return {"resultado": "sem_corte", "motivo": "o corte candidato não foi extraído"}
        resumo = momento.resumo()
        nota, potencial = self.classificar(resumo.get("nota"))
        if potencial == "descartado":
            self._apagar(caminho)                                    # descartado de vez: não sobra arquivo
            self.log(f"momento descartado: nota {nota:.1f} abaixo de {NOTA_DESCARTE:.1f}")
            return {"resultado": "descartado", "nota": nota,
                    "motivo": f"nota {nota:.1f}, abaixo do mínimo de {NOTA_DESCARTE:.1f}"}
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
                self._apagar(audio)
            texto = " ".join(s["text"] for s in segmentos).strip()
        palavras = len(texto.split())
        ia = self._julgar(texto, resumo, duracao, palavras)
        try:
            nome = self._exportar(momento, resumo, Path(caminho), duracao, segmentos, texto, palavras, nota, potencial, ia)
        except OSError as e:
            self.log(f"não foi possível guardar o corte bruto: {e}", "ERROR")
            return {"resultado": "erro", "nota": nota, "motivo": f"não foi possível guardar o corte: {e}"}
        self._apagar(caminho)                                        # o corte agora está em Cortes prontos
        rotulo = "Potencial Alto" if potencial == "alto" else "Potencial Médio"
        self.log(f"corte enviado para Cortes prontos: {nome} (nota {nota:.1f}, {rotulo})", "CLIP")
        return {"resultado": "enviado", "arquivo": nome, "nota": nota, "potencial": potencial,
                "motivo": (ia or {}).get("reason") or rotulo}

    # ------------------------------------------------------------------ gravação
    def _nome(self, nota):
        base = f"CorteLive_{self.agora().strftime('%Y%m%d_%H%M%S')}_nota{nota:.1f}"
        nome, n = f"{base}.mp4", 2
        while (self.saida / nome).exists():
            nome, n = f"{base}_{n}.mp4", n + 1
        return nome

    def _exportar(self, momento, resumo, candidato, duracao, segmentos, texto, palavras, nota, potencial, ia):
        canal = self.canal
        pasta_brutos = self.saida / BRUTOS
        pasta_brutos.mkdir(parents=True, exist_ok=True)
        nome = self._nome(nota)
        bruto, visivel = pasta_brutos / nome, self.saida / nome
        tmp_bruto = pasta_brutos / (nome + ".tmp")
        shutil.copy2(str(candidato), str(tmp_bruto))
        tmp_bruto.replace(bruto)
        titulo = (ia or {}).get("titulo_gancho") or ""
        pergunta = (ia or {}).get("pergunta_cta") or ""
        com_gancho = bool(titulo and pergunta)
        meta = {
            "titulo_gancho": titulo if com_gancho else "MOMENTO INCRÍVEL NA LIVE",
            "pergunta_cta": pergunta if com_gancho else "O QUE VOCÊ ACHOU?",
            "nota": int(round(nota * 10)),
            "transcricao": texto,
            "transcricao_fonte": "transcrição real do corte (live)" if texto else "sem fala transcrita",
            "palavras": palavras,
            "palavras_por_segundo": round(palavras / duracao, 2) if duracao else None,
            "fonte_titulo": "ia_texto_real" if com_gancho else "padrao",
            "score_selecao": nota,
            "motivo_selecao": (ia or {}).get("reason") or "; ".join(str(d) for d in (resumo.get("detalhes") or [])[:3])[:60],
            "fonte_selecao": "sinais",
            "potencial": potencial,
            "score_ia": round(ia["score"], 1) if ia and ia.get("score") is not None else None,
            "motivo_ia": (ia or {}).get("reason") or None,
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
