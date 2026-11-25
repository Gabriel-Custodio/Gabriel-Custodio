"""Vídeo longo (v4.0): junta os melhores momentos de UM vídeo num bruto horizontal de 4 a 12 minutos.

O gerar_cortes.py analisa o vídeo uma vez (transcrição + nota da IA por trecho). Daí saem os cortes curtos
e, se pedido (--video-longo MIN), este vídeo longo. A lógica fica aqui, separada e testável:

  selecao.py   escolhe os melhores momentos até a duração-alvo (nota mínima, distância mínima, ordem cronológica)
  montagem.py  corta cada momento do vídeo baixado, padroniza em 1920x1080 e junta num bruto único + o .json
  edicao.py    opções da edição, linha do tempo aparada, capítulos e comandos do ffmpeg da renderização final
  legenda.py   legenda karaokê e títulos de capítulo em ASS 1920x1080
  live.py      os cortes que o monitor de lives já fez de um canal, como candidatos para o mesmo vídeo longo
"""
from .selecao import (DISTANCIA_MIN_S, DURACAO_MAX_MIN, DURACAO_MIN_MIN, DURACAO_PADRAO_MIN, NOTA_MINIMA,  # noqa: F401
                      duracao_valida, selecionar)
from .montagem import (ALTURA, FPS, LARGURA, PASTA, PASTA_BRUTOS, legenda_dos_momentos, metadados,  # noqa: F401
                       montar_bruto, nome_do_video)
from .edicao import (CAPITULO_DURACAO_S, TITULO_MAX, capitulos, comando_aparar, comando_final, duracao_dos_trechos,  # noqa: F401
                     limpar_titulo, mapear_tempo, normalizar_opcoes, texto_para_resumo)
from .legenda import montar_ass  # noqa: F401
from .live import listar_cortes_da_live  # noqa: F401
