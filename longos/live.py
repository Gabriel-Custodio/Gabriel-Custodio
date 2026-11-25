"""Vídeo longo a partir de uma live monitorada: junta os melhores cortes que o monitor já fez daquele canal.

Cada corte do monitor é um arquivo bruto (`cortes_prontos/_brutos/CorteLive_*.mp4`, uns 45 s) com o `.json` ao lado
(`origem: "live"`, nota, título, legenda). Aqui eles viram candidatos para o `selecionar` e para o `montar_bruto`, na
linha do tempo REAL da live: o instante em que o monitor detectou o momento marca o fim do corte, então dois cortes que
se sobrepõem na live (o monitor aceita 30 s entre momentos e cada corte tem 45 s) não entram juntos (repetiria cena).
"""
import json
from datetime import datetime, timedelta
from pathlib import Path


def _instante(texto):
    """Segundos (época) de um instante ISO guardado no .json do corte; None se não der para ler."""
    try:
        return datetime.fromisoformat(str(texto)).timestamp()
    except (TypeError, ValueError):
        return None


def listar_cortes_da_live(pasta_cortes, plataforma, canal, horas=None, agora=None):
    """Cortes de live de um canal, em ordem cronológica, prontos para o selecionar: lista de dicts.

    Cada item: start/end/duration (segundos na linha do tempo da live), score (nota 0 a 10), reason, hook_title,
    arquivo (o bruto do corte), segmentos (a legenda do corte, relativa ao começo dele), nome, detectado_em.
    `horas`: só os detectados nas últimas N horas (None = todos). Cortes descartados (movidos de pasta), sem o bruto,
    sem nota ou de outro canal ficam de fora.
    """
    pasta_cortes = Path(pasta_cortes)
    corte_minimo = None
    if horas:
        corte_minimo = ((agora or datetime.now()) - timedelta(hours=float(horas))).timestamp()
    itens = []
    for arq in pasta_cortes.glob("*.json"):
        if arq.name.startswith((".", "_")):
            continue
        try:
            meta = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        live = meta.get("live") if isinstance(meta, dict) else None
        if not isinstance(live, dict) or meta.get("origem") != "live":
            continue
        if live.get("plataforma") != plataforma or live.get("canal") != canal:
            continue
        bruto = pasta_cortes / "_brutos" / (arq.stem + ".mp4")
        try:
            nota = float(meta.get("score_selecao"))
            duracao = float(meta.get("clip_duration") or meta.get("duracao_final_s") or 0)
        except (TypeError, ValueError):
            continue
        fim = _instante(live.get("detectado_em")) or _instante(meta.get("timestamp"))
        if not bruto.is_file() or duracao <= 0 or fim is None or nota != nota:
            continue
        if corte_minimo is not None and fim < corte_minimo:
            continue
        titulo = "" if meta.get("fonte_titulo") == "padrao" else str(meta.get("titulo_gancho") or "")
        itens.append({
            "start": fim - duracao, "end": fim, "duration": duracao, "score": nota,
            "reason": str(meta.get("motivo_selecao") or meta.get("motivo_ia") or "")[:50], "hook_title": titulo,
            "arquivo": str(bruto), "nome": arq.stem + ".mp4", "detectado_em": live.get("detectado_em"),
            "segmentos": meta.get("legenda_segmentos") if isinstance(meta.get("legenda_segmentos"), list) else [],
            "texto": str(meta.get("transcricao") or ""), "streamer": meta.get("streamer") or live.get("nome"),
            "url": live.get("url"), "fonte": "live",
        })
    itens.sort(key=lambda c: c["start"])
    return itens
