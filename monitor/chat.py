"""Fontes de chat: o chat real da live (opcional, best-effort) e um arquivo de mensagens para o modo replay.

O chat nunca derruba o monitor: se não abrir, o áudio continua sozinho.
Todas chamam ao_mensagem(t, texto) com t no tempo do monitor.
"""
import json
import threading
import time
from pathlib import Path


class ChatArquivo(threading.Thread):
    """Toca mensagens de um arquivo .jsonl ({"t": segundos desde o início do replay, "texto": "..."}) em tempo real."""

    def __init__(self, caminho, ref_fn, ao_mensagem, relogio=time.time):
        super().__init__(daemon=True, name="chat-arquivo")
        self.caminho, self.ref_fn, self.ao_mensagem, self.relogio = Path(caminho), ref_fn, ao_mensagem, relogio
        self.parar_ev = threading.Event()

    def parar(self):
        self.parar_ev.set()

    @staticmethod
    def carregar(caminho):
        itens = []
        for linha in Path(caminho).read_text(encoding="utf-8", errors="replace").splitlines():
            linha = linha.strip()
            if not linha:
                continue
            try:
                d = json.loads(linha)
                itens.append((float(d["t"]), str(d.get("texto", ""))))
            except (ValueError, KeyError, TypeError):
                continue
        return sorted(itens)

    def run(self):
        try:
            itens = self.carregar(self.caminho)
        except OSError:
            return
        ref = None
        while ref is None and not self.parar_ev.is_set():          # espera a gravação começar (o replay se ancora nela)
            ref = self.ref_fn()
            if ref is None:
                self.parar_ev.wait(0.2)
        for offset, texto in itens:
            if self.parar_ev.is_set():
                return
            espera = (ref + offset) - self.relogio()
            if espera > 0 and self.parar_ev.wait(espera):
                return
            self.ao_mensagem(ref + offset, texto)


class ChatReal(threading.Thread):
    """Chat da live pela biblioteca chat-downloader (opcional). Reconecta com espera; desiste sem barulho se não instalada."""

    def __init__(self, url, ao_mensagem, ao_aviso, abrir=None, relogio=time.time, esperas=(5, 15, 30, 60), max_falhas=8):
        super().__init__(daemon=True, name="chat-real")
        self.url, self.ao_mensagem, self.ao_aviso, self.relogio = url, ao_mensagem, ao_aviso, relogio
        self.abrir, self.esperas, self.max_falhas = abrir or self._abrir_padrao, esperas, max_falhas
        self.parar_ev = threading.Event()

    def parar(self):
        self.parar_ev.set()

    @staticmethod
    def _abrir_padrao(url):
        from chat_downloader import ChatDownloader          # ImportError é tratado em run()
        return ChatDownloader().get_chat(url, message_types=["text_message"])

    def run(self):
        falhas = 0
        while not self.parar_ev.is_set():
            try:
                chat = self.abrir(self.url)
                falhas = 0
                for msg in chat:
                    if self.parar_ev.is_set():
                        return
                    self.ao_mensagem(self.relogio(), str((msg or {}).get("message", "")))
            except ImportError:
                self.ao_aviso("chat desligado: instale com  pip install chat-downloader  (o áudio continua funcionando)")
                return
            except Exception as e:
                falhas += 1
                if falhas >= self.max_falhas:
                    self.ao_aviso(f"chat desligado depois de {falhas} falhas seguidas ({e}); o áudio continua funcionando")
                    return
                espera = self.esperas[min(falhas - 1, len(self.esperas) - 1)]
                self.ao_aviso(f"chat indisponível ({e}); nova tentativa em {espera}s")
                if self.parar_ev.wait(espera):
                    return
            else:
                if self.parar_ev.wait(5):             # o chat terminou (live acabou): tenta de novo mais tarde
                    return
