"""Legenda (karaokê) e títulos de capítulo do vídeo longo, em ASS para 1920x1080 (sem ffmpeg e sem arquivos).

Recebe os segmentos já na linha do tempo do vídeo final (começam em 0). `censurar` (palavra -> palavra) vem do
gerar_cortes.py; aqui nada depende dele, por isso dá para testar à parte.
"""
LARGURA, ALTURA = 1920, 1080
PALAVRAS_POR_BLOCO = 4
DURACAO_MAX_BLOCO_S = 3.0
DURACAO_MIN_BLOCO_S = 0.3
TAMANHO_LEGENDA = 64
MARGEM_V_LEGENDA = 64

CABECALHO = f"""[Script Info]
Title: Fabrica de Cortes - video longo (legenda e capitulos)
ScriptType: v4.00+
PlayResX: {LARGURA}
PlayResY: {ALTURA}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Legenda,Impact,{TAMANHO_LEGENDA},&H00FFFFFF,&H00FFFF00,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,60,60,{MARGEM_V_LEGENDA},1
Style: Capitulo,Arial,44,&H00FFFFFF,&H00FFFFFF,&H00181ED9,&H00181ED9,-1,0,0,0,100,100,0,0,3,10,0,7,60,60,56,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def tempo_ass(segundos):
    """h:mm:ss.cc"""
    segundos = max(0.0, float(segundos))
    horas, resto = int(segundos // 3600), segundos % 3600
    minutos, segs = int(resto // 60), resto % 60
    centesimos = min(int(round((segs - int(segs)) * 100)), 99)
    return f"{horas}:{minutos:02d}:{int(segs):02d}.{centesimos:02d}"


def limpar_texto(texto):
    """Texto seguro para um evento ASS (chaves e barras viram sintaxe da legenda; quebras de linha não valem)."""
    return " ".join(str(texto).replace("{", "").replace("}", "").replace("\\", "").split())


def blocos(segmentos, duracao, censurar=None):
    """Quebra a fala em blocos de poucas palavras: [{start, end, words}] sem sobreposição, dentro de [0, duracao]."""
    censurar = censurar or (lambda palavra: palavra)
    itens = []
    for seg in segmentos or []:
        if seg["end"] <= 0:
            continue
        if seg["start"] >= duracao:
            break
        inicio, fim = max(0.0, seg["start"]), min(duracao, seg["end"])
        if fim <= inicio:
            continue
        palavras = [dict(p) for p in seg.get("words") or [] if p.get("word") and p["end"] > 0 and p["start"] < duracao]
        if palavras:
            for p in palavras:
                p["start"], p["end"] = max(0.0, p["start"]), min(duracao, p["end"])
            for i in range(0, len(palavras), PALAVRAS_POR_BLOCO):
                grupo = palavras[i:i + PALAVRAS_POR_BLOCO]
                itens.append({"start": grupo[0]["start"], "end": grupo[-1]["end"],
                              "words": [limpar_texto(censurar(p["word"])).upper() for p in grupo]})
            continue
        texto = str(seg.get("text") or "").split()
        if not texto:
            continue
        por_palavra = (fim - inicio) / len(texto)
        for i in range(0, len(texto), PALAVRAS_POR_BLOCO):
            grupo = texto[i:i + PALAVRAS_POR_BLOCO]
            itens.append({"start": inicio + i * por_palavra, "end": min(inicio + (i + len(grupo)) * por_palavra, fim),
                          "words": [limpar_texto(censurar(p)).upper() for p in grupo]})
    itens = [b for b in itens if any(b["words"])]
    for i in range(len(itens) - 1):
        if itens[i]["end"] > itens[i + 1]["start"]:
            itens[i]["end"] = itens[i + 1]["start"]
    for b in itens:
        if b["end"] - b["start"] > DURACAO_MAX_BLOCO_S:
            b["end"] = b["start"] + DURACAO_MAX_BLOCO_S
    return [b for b in itens if b["end"] - b["start"] >= DURACAO_MIN_BLOCO_S]


def montar_ass(segmentos, duracao, capitulos=None, censurar=None):
    """Texto do arquivo .ass com a legenda karaokê e os títulos de capítulo. None se não houver nada para mostrar."""
    eventos = []
    for b in blocos(segmentos, duracao, censurar):
        por_palavra = (b["end"] - b["start"]) / len(b["words"])
        centesimos = max(1, int(por_palavra * 100))
        partes = [f"{{\\k{centesimos}\\t(\\c&H00D7FF&)}}{p}{{\\t(\\c&HFFFFFF&)}}" for p in b["words"]]
        eventos.append(f"Dialogue: 0,{tempo_ass(b['start'])},{tempo_ass(b['end'])},Legenda,,0,0,0,karaoke,{' '.join(partes)}")
    for c in capitulos or []:
        texto = limpar_texto(f"{c['n']} · {c['titulo']}").upper()
        eventos.append(f"Dialogue: 1,{tempo_ass(c['inicio'])},{tempo_ass(c['fim'])},Capitulo,,0,0,0,,{{\\fad(250,250)}}{texto}")
    if not eventos:
        return None
    return CABECALHO + "\n".join(eventos) + "\n"
