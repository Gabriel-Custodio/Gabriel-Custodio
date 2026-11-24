"""Detectores de sinais e agregador de momentos (lógica pura: sem ffmpeg, sem rede, sem relógio próprio).

Todo tempo aqui é o "tempo do monitor": segundos numa linha única (a do vídeo gravado). Quem chama diz que horas são,
o que torna tudo determinístico e testável.

Fluxo:  medições de áudio e mensagens de chat -> Sinal (tipo, força 0-10)  ->  Agregador  ->  Momento (janela do corte)
"""
import re
import statistics
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional

# parâmetros por sensibilidade (a tela escolhe uma; o padrão é "media")
SENSIBILIDADES = {
    "baixa": {"audio_db": 8.0, "chat_fator": 3.0, "gatilho": 6.5, "max_hora": 12},
    "media": {"audio_db": 6.0, "chat_fator": 2.2, "gatilho": 5.0, "max_hora": 20},
    "alta":  {"audio_db": 4.0, "chat_fator": 1.7, "gatilho": 3.5, "max_hora": 30},
}
SENSIBILIDADE_PADRAO = "media"
NOTA_DESCARTE = 6.5            # momento com nota abaixo disso (arredondada em 1 casa) vira descarte: não conta no limite por hora

PISO_LUFS = -45.0              # abaixo disso é silêncio: não entra na linha de base nem dispara
AQUECIMENTO_AUDIO = 12         # medições (segmentos) antes de poder disparar: a live precisa "mostrar o normal" antes
JANELA_BASE_AUDIO = 90         # medições na linha de base (~6 min com segmentos de 4 s)
CHAT_MIN_MENSAGENS_MIN = 15    # abaixo disso (msg/min) chat nunca é "explosão"
CHAT_JANELA_S = 30.0           # janela em que se mede o ritmo do chat
CHAT_AQUECIMENTO_S = 120.0     # tempo observando o chat antes de poder disparar por ritmo

PALAVRAS_CHAT = ("clipa", "clip", "meu deus", "pogchamp", "poggers", "pog", "omg", "mentira", "nossa",
                 "que isso", "socorro", "caramba", "lendario", "lendário", "absurdo", "insano", "loucura", "gg")
_RE_RISO = re.compile(r"(?<!\w)k{4,}(?!\w)")
_RE_PALAVRAS = re.compile(r"(?<![\w])(" + "|".join(re.escape(p) for p in sorted(PALAVRAS_CHAT, key=len, reverse=True)) + r")(?![\w])")


@dataclass
class Sinal:
    t: float                   # tempo do monitor (s)
    tipo: str                  # "audio", "chat" ou "palavra"
    forca: float               # 0 a 10
    detalhe: str = ""


@dataclass
class Momento:
    """Um trecho que parece bom. t0..t1 é a janela do corte (ancorada no pico, não em quando o processamento rodou)."""
    pico_t: float
    primeiro_t: float
    ultimo_t: float
    nota: float
    t0: float
    t1: float
    sinais: List[Sinal] = field(default_factory=list)

    def resumo(self):
        tipos = Counter(s.tipo for s in self.sinais)
        return {"pico_t": round(self.pico_t, 2), "t0": round(self.t0, 2), "t1": round(self.t1, 2), "nota": round(self.nota, 2),
                "sinais": dict(tipos), "detalhes": [s.detalhe for s in self.sinais][:6]}


def _limitar(v, a=0.0, b=10.0):
    return max(a, min(b, v))


class DetectorAudio:
    """Compara o volume (LUFS) de cada segmento com o "normal" da própria live (mediana móvel), não com um limite fixo."""

    def __init__(self, sensibilidade=SENSIBILIDADE_PADRAO):
        self.limiar_db = SENSIBILIDADES[sensibilidade]["audio_db"]
        self.base: Deque[float] = deque(maxlen=JANELA_BASE_AUDIO)
        self.medicoes = 0
        self.sequencia = 0          # segmentos seguidos acima do limiar (risada e grito duram mais de um segmento)

    def processar(self, t, lufs) -> Optional[Sinal]:
        """t = tempo do fim do segmento; lufs = volume integrado do segmento. None = nada de especial."""
        if lufs is None or lufs < PISO_LUFS:
            self.sequencia = 0
            return None
        sinal = None
        if self.medicoes >= AQUECIMENTO_AUDIO and len(self.base) >= AQUECIMENTO_AUDIO:
            mediana = statistics.median(self.base)
            ganho = lufs - mediana
            if ganho >= self.limiar_db:
                self.sequencia += 1
                forca = _limitar(4.0 + (ganho - self.limiar_db) * 0.9 + min(self.sequencia - 1, 2) * 0.8)
                sinal = Sinal(t, "audio", round(forca, 2), f"volume {ganho:+.1f} dB acima do normal ({lufs:.1f} LUFS)")
            else:
                self.sequencia = 0
        self.base.append(lufs)
        self.medicoes += 1
        return sinal


class DetectorChat:
    """Ritmo de mensagens (contra o ritmo normal do chat daquela live) e palavras de reação."""

    def __init__(self, sensibilidade=SENSIBILIDADE_PADRAO):
        self.fator = SENSIBILIDADES[sensibilidade]["chat_fator"]
        self.mensagens: Deque[float] = deque()
        self.base: Deque[float] = deque(maxlen=60)     # ritmo (msg/min) amostrado a cada ~10 s
        self.inicio: Optional[float] = None
        self.ultima_amostra = -1e9
        self.ultimo_disparo = -1e9

    def mensagem(self, t, texto) -> List[Sinal]:
        if self.inicio is None:
            self.inicio = t
        self.mensagens.append(t)
        saida = []
        minusculo = str(texto or "").lower()
        achou = _RE_PALAVRAS.search(minusculo)
        if achou:
            saida.append(Sinal(t, "palavra", 3.0, f"palavra no chat: {achou.group(1)}"))
        elif _RE_RISO.search(minusculo):
            saida.append(Sinal(t, "palavra", 3.0, "risada no chat (kkkk)"))
        saida.extend(self.tick(t))
        return saida

    def _ritmo(self, t):
        while self.mensagens and self.mensagens[0] < t - CHAT_JANELA_S:
            self.mensagens.popleft()
        return len(self.mensagens) * (60.0 / CHAT_JANELA_S)

    def tick(self, t) -> List[Sinal]:
        """Confere o ritmo do chat agora (chamado a cada mensagem e, de tempos em tempos, sem mensagens novas)."""
        if self.inicio is None:
            return []
        ritmo = self._ritmo(t)
        saida = []
        pronto = t - self.inicio >= CHAT_AQUECIMENTO_S and len(self.base) >= 6
        if pronto and ritmo >= CHAT_MIN_MENSAGENS_MIN and t - self.ultimo_disparo >= 10.0:
            razao = ritmo / max(statistics.median(self.base), 3.0)
            if razao >= self.fator:
                self.ultimo_disparo = t
                saida.append(Sinal(t, "chat", round(_limitar(3.0 + (razao - self.fator) * 2.0), 2),
                                   f"chat em {ritmo:.0f} msg/min ({razao:.1f}x o normal)"))
        if t - self.ultima_amostra >= 10.0:                  # o "normal" é amostrado a cada ~10 s (depois da checagem)
            self.ultima_amostra = t
            self.base.append(ritmo)
        return saida


class Agregador:
    """Junta os sinais próximos num "momento" e decide quando ele vale um corte.

    - Sinais próximos (até fusao_s) viram UM momento: o cooldown funde em vez de descartar.
    - A janela do corte termina pos_roll s depois do pico e tem `duracao` s (ancorada no horário do pico).
    - Só fecha o momento quando já passou pos_roll sem novos sinais, ou quando ele passou do limite de duração.
    - Momento muito perto do anterior (distancia_min_s) ou acima do máximo por hora é descartado (e contado).
    - O máximo por hora conta só os momentos que valem corte (nota >= NOTA_DESCARTE): os fracos não gastam a cota.
    """

    def __init__(self, sensibilidade=SENSIBILIDADE_PADRAO, pre_roll=35.0, pos_roll=10.0, fusao_s=20.0,
                 distancia_min_s=30.0, limite_aberto_s=45.0):
        cfg = SENSIBILIDADES[sensibilidade]
        self.gatilho, self.max_hora = cfg["gatilho"], cfg["max_hora"]
        self.pre_roll, self.pos_roll = pre_roll, pos_roll
        self.duracao = pre_roll + pos_roll
        self.fusao_s, self.distancia_min_s, self.limite_aberto_s = fusao_s, distancia_min_s, limite_aberto_s
        self.sinais: Deque[Sinal] = deque()
        self.aberto: Optional[dict] = None
        self.fechados: List[float] = []              # tempo (do pico) dos momentos já entregues (vale para a distância mínima)
        self.contados: List[float] = []              # idem, só os que valem corte (vale para o limite por hora)
        self.descartes: Counter = Counter()
        self.agora = 0.0

    @staticmethod
    def nota_de(sinais):
        """0-10: o sinal mais forte + parte dos outros tipos + bônus quando áudio e chat reagem juntos."""
        if not sinais:
            return 0.0
        por_tipo = {}
        for s in sinais:
            por_tipo[s.tipo] = max(por_tipo.get(s.tipo, 0.0), s.forca)
        forcas = sorted(por_tipo.values(), reverse=True)
        nota = forcas[0] + 0.4 * sum(forcas[1:])
        if "audio" in por_tipo and "chat" in por_tipo:
            nota += 2.0
        return round(_limitar(nota), 2)

    def adicionar(self, sinal: Sinal):
        """Registra um sinal. Pode abrir um momento ou atualizar o aberto."""
        self.agora = max(self.agora, sinal.t)
        self.sinais.append(sinal)
        while self.sinais and self.sinais[0].t < self.agora - 120.0:
            self.sinais.popleft()
        proximos = [s for s in self.sinais if abs(s.t - sinal.t) <= self.fusao_s]
        nota = self.nota_de(proximos)
        if self.aberto is not None:
            a = self.aberto
            a["ultimo_t"] = max(a["ultimo_t"], sinal.t)
            a["sinais"].append(sinal)
            if nota > a["nota"]:
                a["nota"], a["pico_t"] = nota, sinal.t
            return
        if nota < self.gatilho:
            return
        if self.fechados and abs(sinal.t - self.fechados[-1]) < self.distancia_min_s:
            self.descartes["muito_perto_do_anterior"] += 1
            return
        recentes = [p for p in self.contados if p > self.agora - 3600.0]
        if len(recentes) >= self.max_hora:
            self.descartes["limite_por_hora"] += 1
            return
        self.aberto = {"pico_t": sinal.t, "primeiro_t": sinal.t, "ultimo_t": sinal.t, "nota": nota, "sinais": list(proximos)}

    def _fechar(self, a):
        self.fechados.append(a["pico_t"])
        self.fechados = self.fechados[-200:]
        if round(a["nota"], 1) >= NOTA_DESCARTE:
            self.contados.append(a["pico_t"])
            self.contados = self.contados[-200:]

    def avancar(self, agora) -> List[Momento]:
        """Diz que horas são no vídeo; devolve os momentos que ficaram prontos (o buffer já cobre o pós-roll)."""
        self.agora = max(self.agora, agora)
        prontos = []
        a = self.aberto
        coberto = a is not None and self.agora >= a["pico_t"] + self.pos_roll          # o vídeo já tem o pós-roll
        if coberto and (self.agora - a["ultimo_t"] >= self.pos_roll or self.agora - a["primeiro_t"] >= self.limite_aberto_s):
            fim = a["pico_t"] + self.pos_roll
            prontos.append(Momento(pico_t=a["pico_t"], primeiro_t=a["primeiro_t"], ultimo_t=a["ultimo_t"], nota=a["nota"],
                                   t0=fim - self.duracao, t1=fim, sinais=a["sinais"]))
            self._fechar(a)
            self.aberto = None
        return prontos

    def encerrar(self) -> List[Momento]:
        """Fim da gravação: entrega o momento que ainda estava aberto, com a janela limitada ao que foi gravado."""
        a = self.aberto
        if a is None:
            return []
        fim = min(a["pico_t"] + self.pos_roll, max(self.agora, a["pico_t"]))
        self.aberto = None
        self._fechar(a)
        return [Momento(pico_t=a["pico_t"], primeiro_t=a["primeiro_t"], ultimo_t=a["ultimo_t"], nota=a["nota"],
                        t0=fim - self.duracao, t1=fim, sinais=a["sinais"])]
