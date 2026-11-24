"""python -m monitor --url LINK_DO_CANAL | --replay ARQUIVO.mp4   (um processo por live)."""
import argparse
import signal
import sys
from pathlib import Path

from .processo import MonitorCanal, log
from .sinais import SENSIBILIDADES, SENSIBILIDADE_PADRAO


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m monitor", description="Monitora UMA live e detecta momentos para corte.")
    fonte = ap.add_mutually_exclusive_group(required=True)
    fonte.add_argument("--url", help="link do canal na Twitch ou no YouTube (precisa estar na lista de canais permitidos)")
    fonte.add_argument("--replay", metavar="ARQUIVO", help="toca um vídeo local como se fosse uma live (para testar)")
    ap.add_argument("--chat-arquivo", metavar="ARQUIVO.jsonl", help='mensagens de chat simuladas: {"t": segundos, "texto": "..."} por linha (só com --replay)')
    ap.add_argument("--sensibilidade", choices=sorted(SENSIBILIDADES), default=SENSIBILIDADE_PADRAO)
    ap.add_argument("--id", help="nome da pasta de estado desta live (padrão: plataforma-canal)")
    ap.add_argument("--sem-cortes-candidatos", action="store_true", help="só detecta e registra; não extrai os cortes candidatos")
    ap.add_argument("--sem-chat", action="store_true", help="não abre o chat da live")
    ap.add_argument("--sem-corte-bruto", action="store_true", help="só detecta e registra; não manda cortes para a pasta Cortes prontos")
    ap.add_argument("--raiz", help="pasta do projeto (padrão: a deste programa)")
    a = ap.parse_args(argv)
    if a.chat_arquivo and not a.replay:
        ap.error("--chat-arquivo só vale com --replay")
    if a.replay and not Path(a.replay).is_file():
        ap.error(f"arquivo de replay não encontrado: {a.replay}")
    try:
        m = MonitorCanal(raiz=a.raiz, url=a.url, replay=a.replay, chat_arquivo=a.chat_arquivo, sensibilidade=a.sensibilidade,
                         identificador=a.id, salvar_momentos=not a.sem_cortes_candidatos, chat_real=not a.sem_chat,
                         enviar_cortes=not a.sem_corte_bruto and not a.sem_cortes_candidatos)
    except ValueError as e:
        ap.error(str(e))
    for nome in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, nome):
            signal.signal(getattr(signal, nome), lambda *_: m.parar("sinal de parada recebido"))
    codigo = m.executar_ate_parar()
    log(f"monitor finalizado (código {codigo})")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
