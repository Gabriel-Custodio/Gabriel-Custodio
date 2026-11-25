"""Escolha dos momentos do vídeo longo (só biblioteca padrão; não chama IA nem ffmpeg).

Entrada: candidatos que já têm nota (a IA deu a nota; o CÓDIGO faz as contas de tempo). Cada candidato é um dict com
`start`, `end` (segundos no vídeo base) e `score` (0 a 10). Saída: os momentos escolhidos em ORDEM CRONOLÓGICA.

Regras:
  * só entra momento com nota >= NOTA_MINIMA (a mesma regra do monitor de lives: abaixo disso é descartado);
  * os melhores primeiro, até fechar a duração-alvo (o último pode passar do alvo em até FOLGA_ULTIMO_S, para o vídeo
    nunca ficar abaixo do que foi pedido só porque o próximo momento não coube);
  * os INÍCIOS de dois momentos ficam a pelo menos DISTANCIA_MIN_S um do outro (sem sobreposição, variedade);
  * se não houver momento bom o bastante para fechar o alvo, o vídeo sai mais curto (e o resumo avisa).
"""
import math

DURACAO_MIN_MIN = 4              # menor duração-alvo aceita (minutos)
DURACAO_MAX_MIN = 12             # maior duração-alvo aceita (minutos)
DURACAO_PADRAO_MIN = 8
NOTA_MINIMA = 6.5                # igual a NOTA_DESCARTE do monitor de lives
DISTANCIA_MIN_S = 60.0           # distância mínima entre os inícios de dois momentos
FOLGA_ULTIMO_S = 45.0            # o último momento pode passar do alvo em até isso (um momento tem de 32 a 45 s)
MIN_MOMENTOS = 2                 # menos que isso não é um "vídeo de melhores momentos"
MIN_TOTAL_S = 60.0               # nem menos que isso de vídeo


def duracao_valida(minutos):
    """True para um número inteiro de minutos entre DURACAO_MIN_MIN e DURACAO_MAX_MIN (bool não vale)."""
    return (isinstance(minutos, int) and not isinstance(minutos, bool)
            and DURACAO_MIN_MIN <= minutos <= DURACAO_MAX_MIN)


def _numero(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def _intervalo(candidato):
    """(início, fim, nota) do candidato, ou None se algum número estiver faltando ou for absurdo."""
    inicio, fim, nota = (_numero(candidato.get(chave)) for chave in ("start", "end", "score"))
    if inicio is None or fim is None or nota is None or inicio < 0 or fim - inicio <= 0:
        return None
    return inicio, fim, nota


def selecionar(candidatos, alvo_s, nota_minima=NOTA_MINIMA, distancia_min_s=DISTANCIA_MIN_S):
    """Escolhe os momentos do vídeo longo. Devolve (escolhidos em ordem cronológica, resumo).

    `resumo`: alvo_s, total_s, elegiveis (quantos passaram na nota), escolhidos, curto (ficou abaixo do alvo),
    viavel (dá para montar: pelo menos MIN_MOMENTOS e MIN_TOTAL_S) e motivo (texto para o log quando não é viável
    ou ficou curto). Os candidatos de entrada não são alterados.
    """
    alvo_s = float(alvo_s)
    maximo = alvo_s + FOLGA_ULTIMO_S
    elegiveis = []
    for cand in candidatos or []:
        dados = _intervalo(cand)
        if dados is not None and round(dados[2], 1) >= nota_minima:
            elegiveis.append((dados, cand))
    elegiveis.sort(key=lambda item: (-item[0][2], item[0][0]))      # melhor nota primeiro; empate: o mais cedo

    escolhidos, total = [], 0.0
    for (inicio, fim, _), cand in elegiveis:
        if total >= alvo_s:
            break
        duracao = fim - inicio
        if total + duracao > maximo:
            continue                                                # não cabe: tenta um menor
        if any(abs(inicio - o["start"]) < distancia_min_s or (inicio < o["end"] and o["start"] < fim)
               for o in escolhidos):
            continue
        item = dict(cand)
        item["start"], item["end"], item["duration"] = inicio, fim, duracao
        escolhidos.append(item)
        total += duracao
    escolhidos.sort(key=lambda c: c["start"])

    viavel = len(escolhidos) >= MIN_MOMENTOS and total >= MIN_TOTAL_S
    curto = total < alvo_s * 0.9
    motivo = ""
    if not viavel:
        motivo = (f"só {len(escolhidos)} momento(s) com nota {nota_minima:g} ou mais ({total:.0f}s): "
                  f"não dá um vídeo longo (mínimo {MIN_MOMENTOS} momentos e {MIN_TOTAL_S:.0f}s)")
    elif curto:
        motivo = (f"só havia momentos bons para {total / 60:.1f} min (alvo {alvo_s / 60:g} min): "
                  "o vídeo sai mais curto em vez de incluir momentos fracos")
    return escolhidos, {"alvo_s": alvo_s, "total_s": round(total, 2), "elegiveis": len(elegiveis),
                        "escolhidos": len(escolhidos), "curto": curto, "viavel": viavel, "motivo": motivo}
