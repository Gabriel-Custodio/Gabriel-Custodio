import sys
import json
import subprocess
from datetime import datetime, timedelta

def formatar_duracao(segundos):
    if not segundos:
        return "00:00"
    m, s = divmod(int(segundos), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def buscar():
    print("=" * 65)
    print("BUSCADOR DE TENDENCIAS - YouTube -> Cortes Virais")
    print("=" * 65)
    tema = input("\nDigite o tema/nicho para buscar (ex: tecnologia, games, financas): ").strip()
    if not tema:
        print("Tema vazio. Encerrando.")
        return

    print(f"\nBuscando videos sobre '{tema}' (ultimos 7 dias, > 4 min)...")
    data_limite = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        f"ytsearch30:{tema}",
        "--dump-json",
        "--no-warnings",
        "--ignore-errors",
        "--dateafter", data_limite,
        "--match-filter", "duration > 240"
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        videos = []
        for linha in res.stdout.splitlines():
            linha = linha.strip()
            if linha.startswith("{"):
                try:
                    videos.append(json.loads(linha))
                except json.JSONDecodeError:
                    pass

        videos.sort(key=lambda x: x.get("view_count") or 0, reverse=True)
        top_videos = videos[:15]

        if not top_videos:
            print("\nNenhum video encontrado com esses filtros. Tente um termo mais amplo.")
            return

        print(f"\n{'#':<3} | {'VIEWS':<10} | {'DURACAO':<8} | {'CANAL':<18} | {'TITULO'}")
        print("-" * 95)
        for i, v in enumerate(top_videos, 1):
            views = f"{v.get('view_count', 0):,}".replace(",", ".")
            dur = formatar_duracao(v.get("duration", 0))
            canal = (v.get("channel") or v.get("uploader") or "Desconhecido")[:18]
            titulo = (v.get("title") or "Sem titulo")[:45]
            url = v.get("webpage_url") or f"https://www.youtube.com/watch?v={v.get('id')}"
            print(f"{i:<3} | {views:<10} | {dur:<8} | {canal:<18} | {titulo}")
            print(f"    Link: {url}\n")

    except Exception as e:
        print(f"\nErro ao executar busca: {e}")

if __name__ == "__main__":
    buscar()
