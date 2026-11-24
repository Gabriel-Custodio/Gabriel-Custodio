"""Núcleo novo do monitor de lives (um processo por live): ingestão contínua, detectores e agregador.

Não confundir com o monitor_lives.py antigo (opção 5 do iniciar.bat), que continua como está.
Rodar:  python -m monitor --url https://www.twitch.tv/CANAL      (só canais da lista "Monitor de lives")
        python -m monitor --replay arquivo.mp4                   (toca um vídeo como se fosse uma live, para testar)
"""
