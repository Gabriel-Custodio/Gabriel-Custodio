"""O processo de UMA live: junta ingestão, anel, detectores, agregador e o estado em disco.

Um MonitorCanal por live (a interface sobe um processo `python -m monitor` por canal). Ele:
  1. confere se o canal está na lista de canais permitidos (e reconfere a cada minuto);
  2. grava a live de forma contínua num anel de segmentos;
  3. mede o áudio de cada segmento e ouve o chat, gerando sinais;
  4. o agregador junta os sinais em "momentos" ancorados no horário do pico;
  5. cada momento vira um registro em momentos.jsonl e (opcional) um corte candidato extraído do anel.
Com `enviar_cortes`, cada momento passa pelo ProdutorCortes (corte.py): transcrição, juízo da IA e corte BRUTO em cortes_prontos.
Estado em disco (para a interface): status.json; controle: arquivos parar.flag e pausar.flag na pasta de estado.
"""
import json
import os
import queue
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .analise import medir_audio
from .anel import Anel, extrair_janela
from .chat import ChatArquivo, ChatReal
from .ingestao import Ingestor, achar_ffmpeg, resolver_stream
from .sinais import Agregador, DetectorAudio, DetectorChat, SENSIBILIDADES, SENSIBILIDADE_PADRAO

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "app"))
from canais import RegistroCanais, canal_de_url, url_da_live  # noqa: E402

ANEL_SEGUNDOS = 240.0
MAX_CLIPES_MOMENTO = 30
CHECAGEM_PERMISSAO_S = 60.0
INTERVALO_STATUS_S = 2.0


def slug(texto):
    return re.sub(r"[^a-z0-9_-]+", "-", str(texto).lower()).strip("-")[:60] or "live"


def log(msg, nivel="INFO"):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{nivel}] {msg}", flush=True)


class MonitorCanal:
    def __init__(self, raiz=None, url=None, replay=None, chat_arquivo=None, sensibilidade=SENSIBILIDADE_PADRAO,
                 identificador=None, salvar_momentos=True, registro=None, ffmpeg=None, ao_momento=None,
                 chat_real=True, relogio=time.time, fabrica_ingestor=None, executar=None, espera_offline=60.0,
                 enviar_cortes=False, produtor=None):
        if sensibilidade not in SENSIBILIDADES:
            raise ValueError("sensibilidade inválida")
        self.raiz = Path(raiz or RAIZ)
        self.replay = Path(replay) if replay else None
        self.chat_arquivo = Path(chat_arquivo) if chat_arquivo else None
        self.sensibilidade, self.salvar_momentos, self.chat_real = sensibilidade, salvar_momentos, chat_real
        self.ao_momento, self.relogio, self.executar, self.espera_offline = ao_momento, relogio, executar, espera_offline
        self.enviar_cortes, self.produtor = enviar_cortes, produtor
        self.canal_info = {}
        self.registro = RegistroCanais(registro or (self.raiz / "canais_permitidos.json"))
        self.url_canal = url
        self.plataforma = self.canal = None
        self.url_live = None
        if self.replay is None:
            if not url:
                raise ValueError("informe o link do canal (--url) ou um arquivo de replay (--replay)")
            self.plataforma, self.canal = canal_de_url(url)              # levanta CanalInvalido se não der para saber o canal
            self.url_live = url_da_live(self.plataforma, self.canal)
        self.id = identificador or (f"{self.plataforma}-{slug(self.canal)}" if self.canal else f"replay-{slug(self.replay.stem)}")
        self.pasta_estado = self.raiz / "cortes_prontos" / "_lives" / self.id
        self.pasta_anel = self.raiz / "temp_downloads" / "lives" / self.id
        self.pasta_momentos = self.pasta_estado / "momentos"
        self.ffmpeg = ffmpeg or achar_ffmpeg(self.raiz)

        self.anel = Anel(ANEL_SEGUNDOS)
        self.det_audio, self.det_chat = DetectorAudio(sensibilidade), DetectorChat(sensibilidade)
        self.agregador = Agregador(sensibilidade)
        self.trava = threading.Lock()
        self.parar_ev = threading.Event()
        self.fila_analise: "queue.Queue" = queue.Queue()
        self.fila_extracao: "queue.Queue" = queue.Queue()
        self.estado, self.detalhe, self.erro = "iniciando", "", ""
        self.ultimo_t = 0.0
        self.contagem = {"segmentos": 0, "sinais_audio": 0, "sinais_chat": 0, "sinais_palavra": 0, "momentos": 0,
                         "momentos_salvos": 0, "mensagens_chat": 0, "cortes_enviados": 0, "cortes_descartados": 0, "cortes_erro": 0}
        self.ultimo_sinal = self.ultimo_momento = self.ultimo_corte = None
        self.ia_ligada = False
        self.iniciado_em = datetime.now().isoformat(timespec="seconds")
        self.threads = []
        self.ingestor = None
        self.fabrica_ingestor = fabrica_ingestor
        self.chat_estado = "desligado"
        self.codigo_saida = 0

    # ------------------------------------------------------------------ permissão
    def conferir_permissao(self):
        """(ok, motivo). Replay não tem canal: sempre liberado. Canal real: só se estiver ativo na lista."""
        if self.replay is not None:
            return True, "replay (arquivo local)"
        r = self.registro.verificar(self.url_canal)
        if r.get("permitido") and r.get("canal_info"):
            self.canal_info = r["canal_info"]
        return bool(r.get("permitido")), r.get("motivo", "")

    # ------------------------------------------------------------------ estado em disco
    def _status(self):
        with self.trava:
            ult_sinal, ult_mom, ult_corte = self.ultimo_sinal, self.ultimo_momento, self.ultimo_corte
            return {
                "id": self.id, "estado": self.estado, "detalhe": self.detalhe, "erro": self.erro,
                "plataforma": self.plataforma, "canal": self.canal, "replay": str(self.replay) if self.replay else None,
                "sensibilidade": self.sensibilidade, "pausado": self.pausado(), "chat": self.chat_estado,
                "buffer_s": self.anel.segundos(), "tempo_monitor_s": round(self.ultimo_t, 1),
                **self.contagem, "descartes": dict(self.agregador.descartes),
                "reinicios": self.ingestor.reinicios if self.ingestor else 0,
                "ultimo_sinal": ult_sinal, "ultimo_momento": ult_mom, "ultimo_corte": ult_corte,
                "enviar_cortes": bool(self.ao_momento), "ia": self.ia_ligada,
                "pid": os.getpid(), "iniciado_em": self.iniciado_em,
                "atualizado_em": datetime.now().isoformat(timespec="seconds"),
            }

    def escrever_status(self):
        try:
            self.pasta_estado.mkdir(parents=True, exist_ok=True)
            arq = self.pasta_estado / "status.json"
            tmp = arq.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._status(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(arq)
        except OSError as e:
            log(f"não foi possível gravar o status: {e}", "WARN")

    def pausado(self):
        return (self.pasta_estado / "pausar.flag").exists()

    def _definir_estado(self, estado, detalhe=""):
        with self.trava:
            mudou = (estado, detalhe) != (self.estado, self.detalhe)
            self.estado, self.detalhe = estado, detalhe
        if mudou:
            log(f"{estado}: {detalhe}", "ERROR" if estado == "erro" else "INFO")

    # ------------------------------------------------------------------ entrada de dados
    def _ao_segmento(self, seg):
        self.anel.registrar(seg)
        with self.trava:
            self.contagem["segmentos"] += 1
            self.ultimo_t = max(self.ultimo_t, seg.fim)
        self.fila_analise.put(seg)

    def _ao_mensagem(self, t, texto):
        with self.trava:
            self.contagem["mensagens_chat"] += 1
            if self.pausado():
                return
            sinais = self.det_chat.mensagem(t, texto)
            self._receber_sinais(sinais)

    def _receber_sinais(self, sinais):
        """Chamar com self.trava segurada."""
        for s in sinais:
            self.contagem[f"sinais_{s.tipo}"] += 1
            self.ultimo_sinal = {"tipo": s.tipo, "forca": s.forca, "t": round(s.t, 1), "detalhe": s.detalhe}
            self.agregador.adicionar(s)

    def _tratar_segmento(self, seg):
        """Roda quando a medição de áudio do segmento termina (thread de análise)."""
        with self.trava:
            if not self.pausado():
                sinal = self.det_audio.processar(seg.fim, seg.lufs)
                self._receber_sinais(([sinal] if sinal else []) + self.det_chat.tick(seg.fim))
            else:
                self.det_audio.processar(seg.fim, seg.lufs)                # a linha de base continua aprendendo
            prontos = self.agregador.avancar(seg.fim)
            for m in prontos:
                self.contagem["momentos"] += 1
                self.ultimo_momento = {"nota": m.nota, "pico_t": round(m.pico_t, 1), "t0": round(m.t0, 1), "t1": round(m.t1, 1)}
        for m in prontos:
            log(f"MOMENTO nota {m.nota:.1f} ({', '.join(sorted({s.tipo for s in m.sinais}))}) janela {m.t1 - m.t0:.0f}s", "CLIP")
            self.fila_extracao.put(m)

    # ------------------------------------------------------------------ threads de trabalho
    def _laco_analise(self):
        while not self.parar_ev.is_set():
            try:
                seg = self.fila_analise.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                seg.lufs, _ = medir_audio(self.ffmpeg, seg.path, self.executar)
                self._tratar_segmento(seg)
            except Exception as e:                                          # nunca derruba a análise
                log(f"erro ao analisar um segmento: {e}", "ERROR")
            finally:
                self.fila_analise.task_done()

    def _laco_extracao(self):
        while not self.parar_ev.is_set():
            try:
                m = self.fila_extracao.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._entregar_momento(m)
            except Exception as e:
                log(f"erro ao tratar um momento: {e}", "ERROR")
            finally:
                self.fila_extracao.task_done()

    def _entregar_momento(self, m):
        caminho = None
        cobertura = self.anel.cobertura(m.t0, m.t1)
        if self.salvar_momentos and self.ffmpeg:
            self.pasta_momentos.mkdir(parents=True, exist_ok=True)
            nome = f"momento_{datetime.now().strftime('%Y%m%d_%H%M%S')}_nota{m.nota:.1f}.mp4"
            ok, msg, cobertura = extrair_janela(self.ffmpeg, self.anel, m.t0, m.t1, self.pasta_momentos / nome,
                                                executar=self.executar, pasta_temp=self.pasta_anel)
            if ok:
                caminho = self.pasta_momentos / nome
                with self.trava:
                    self.contagem["momentos_salvos"] += 1
                log(f"corte candidato salvo: {nome} (cobertura {cobertura * 100:.0f}%)", "OK")
                antigos = sorted(self.pasta_momentos.glob("momento_*.mp4"))[:-MAX_CLIPES_MOMENTO]
                for a in antigos:
                    try:
                        a.unlink()
                    except OSError:
                        pass
            else:
                log(f"não foi possível extrair o corte candidato: {msg}", "WARN")
        registro = {**m.resumo(), "cobertura": round(cobertura, 2), "arquivo": caminho.name if caminho else None,
                    "em": datetime.now().isoformat(timespec="seconds")}
        if self.ao_momento:
            try:
                resultado = self.ao_momento(m, caminho)
            except Exception as e:                                          # o corte nunca derruba o monitor
                log(f"erro ao produzir o corte do momento: {e}", "ERROR")
                resultado = {"resultado": "erro", "motivo": str(e)[:200]}
            if isinstance(resultado, dict):
                chave = {"enviado": "cortes_enviados", "descartado": "cortes_descartados", "erro": "cortes_erro"}.get(resultado.get("resultado"))
                with self.trava:
                    if chave:
                        self.contagem[chave] += 1
                    self.ultimo_corte = {**resultado, "em": datetime.now().isoformat(timespec="seconds")}
                registro["corte"] = resultado
        try:
            self.pasta_estado.mkdir(parents=True, exist_ok=True)
            with open(self.pasta_estado / "momentos.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        except OSError as e:
            log(f"não foi possível registrar o momento: {e}", "WARN")

    # ------------------------------------------------------------------ ciclo de vida
    def _entrada(self):
        if self.replay is not None:
            return str(self.replay)
        return resolver_stream(self.url_live)

    def _aviso_chat(self, msg):
        self.chat_estado = "desligado"
        log(msg, "WARN")

    def _preparar_produtor(self):
        """Liga a IA e o corte bruto (se pedido e se ninguém passou um gancho próprio)."""
        if self.ao_momento is not None or not self.enviar_cortes:
            return
        produtor = self.produtor
        if produtor is None:
            from .corte import ProdutorCortes
            info = self.canal_info or {}
            canal = {"plataforma": self.plataforma or "replay", "canal": self.canal or self.id, "nome": info.get("nome") or self.canal or self.id,
                     "layout": info.get("layout"), "url": self.url_live}
            produtor = ProdutorCortes(self.raiz, canal, self.ffmpeg, executar=self.executar, pasta_temp=self.pasta_anel, registrar=log)
        self.ao_momento = produtor
        self.ia_ligada = bool(getattr(produtor, "chave", None))
        log("IA " + ("ligada: cada momento é transcrito e julgado antes de virar corte" if self.ia_ligada
                     else "desligada (sem GROQ_API_KEY no .env): vale só a nota dos sinais"))

    def iniciar(self):
        self._preparar_produtor()
        self.pasta_estado.mkdir(parents=True, exist_ok=True)
        self.pasta_anel.mkdir(parents=True, exist_ok=True)
        for f in ("parar.flag",):
            try:
                (self.pasta_estado / f).unlink()
            except OSError:
                pass
        fab = self.fabrica_ingestor or Ingestor
        self.ingestor = fab(self.pasta_anel, self.ffmpeg, self._entrada, self._ao_segmento, self._definir_estado,
                            replay=self.replay is not None, espera_offline=self.espera_offline)
        for alvo, nome in ((self._laco_analise, "analise"), (self._laco_extracao, "extracao")):
            t = threading.Thread(target=alvo, daemon=True, name=nome)
            t.start()
            self.threads.append(t)
        if self.chat_arquivo is not None:
            c = ChatArquivo(self.chat_arquivo, lambda: self.ingestor.ref, self._ao_mensagem)
            self.chat_estado = f"arquivo ({self.chat_arquivo.name})"
            c.start(); self.threads.append(c)
        elif self.chat_real and self.replay is None:
            c = ChatReal(self.url_live, self._ao_mensagem, self._aviso_chat)
            self.chat_estado = "ligado"
            c.start(); self.threads.append(c)
        self.ingestor.start()

    def parar(self, motivo=""):
        if motivo:
            log(motivo)
        self.parar_ev.set()

    def executar_ate_parar(self):
        """Laço principal (bloqueia). Devolve o código de saída: 0 parou normal, 3 canal não permitido, 4 sem ffmpeg."""
        if not self.ffmpeg:
            log("ffmpeg não encontrado (bin\\ffmpeg.exe ou no PATH).", "ERROR")
            self.estado, self.erro = "erro", "ffmpeg não encontrado"
            self.escrever_status()
            return 4
        ok, motivo = self.conferir_permissao()
        if not ok:
            log(f"BLOQUEADO: {motivo}", "ERROR")
            self.estado, self.erro = "bloqueado", motivo
            self.escrever_status()
            return 3
        log(f"monitor iniciado: {self.id} ({'replay' if self.replay else self.url_live}), sensibilidade {self.sensibilidade}")
        self.iniciar()
        ultima_permissao = ultimo_status = 0.0
        try:
            while not self.parar_ev.is_set():
                agora = self.relogio()
                if (self.pasta_estado / "parar.flag").exists():
                    self.parar("pedido de parada recebido")
                    break
                if agora - ultima_permissao >= CHECAGEM_PERMISSAO_S:
                    ultima_permissao = agora
                    ok, motivo = self.conferir_permissao()
                    if not ok:
                        log(f"PERMISSÃO REMOVIDA: {motivo}; parando.", "ERROR")
                        self.estado, self.erro, self.codigo_saida = "bloqueado", motivo, 3
                        break
                if agora - ultimo_status >= INTERVALO_STATUS_S:
                    ultimo_status = agora
                    self.anel.limpar(self.ultimo_t)
                    self.escrever_status()
                if self.ingestor.estado_final:
                    break
                self.parar_ev.wait(0.5)
        finally:
            self.encerrar()
        return self.codigo_saida

    def _esperar_fila(self, fila, limite):
        fim = time.time() + limite
        while fila.unfinished_tasks and time.time() < fim:
            time.sleep(0.1)

    def encerrar(self):
        """Para tudo, mas antes deixa a análise e a extração terminarem o que já estava na fila (e fecha o momento aberto)."""
        fim_natural = bool(self.ingestor and self.ingestor.estado_final)      # o replay acabou: vale esperar a análise terminar
        if self.ingestor:
            self.ingestor.parar()
        for t in self.threads:
            if hasattr(t, "parar"):
                t.parar()
        self._esperar_fila(self.fila_analise, 120 if fim_natural else 5)
        with self.trava:
            restantes = self.agregador.encerrar() if self.codigo_saida == 0 and self.estado != "bloqueado" else []
            for m in restantes:
                self.contagem["momentos"] += 1
                self.ultimo_momento = {"nota": m.nota, "pico_t": round(m.pico_t, 1), "t0": round(m.t0, 1), "t1": round(m.t1, 1)}
        for m in restantes:
            log(f"MOMENTO (no fim da gravação) nota {m.nota:.1f}", "CLIP")
            self.fila_extracao.put(m)
        self._esperar_fila(self.fila_extracao, 120)
        self.parar_ev.set()
        if self.estado != "bloqueado":
            self.estado, self.detalhe = "parado", "monitor encerrado"
        self.escrever_status()
        try:
            for s in list(self.pasta_anel.glob("s*/*")):
                s.unlink()
        except OSError:
            pass
