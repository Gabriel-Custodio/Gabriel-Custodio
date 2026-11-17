#!/usr/bin/env python3
"""
Verificador e instalador automático de dependências para Windows.
Verifica Python, pip, yt-dlp, ffmpeg e instala automaticamente se necessário.
"""
import subprocess
import sys
import os
import shutil
from pathlib import Path


def run_command(cmd, capture=True, check=False):
    """Executa comando e retorna resultado."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=capture, text=True, check=check
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def check_python():
    """Verifica se Python está instalado e versão >= 3.8."""
    ok, out, _ = run_command("python --version")
    if ok:
        version_str = out.split()[1]
        major, minor = map(int, version_str.split(".")[:2])
        if major >= 3 and minor >= 8:
            print(f"✅ Python {version_str} encontrado")
            return True
        else:
            print(f"❌ Python {version_str} muito antigo (precisa >= 3.8)")
    else:
        print("❌ Python não encontrado")
    return False


def check_pip():
    """Verifica se pip está disponível."""
    ok, out, _ = run_command("pip --version")
    if ok:
        print(f"✅ pip encontrado: {out}")
        return True
    print("❌ pip não encontrado")
    return False


def check_yt_dlp():
    """Verifica se yt-dlp está instalado."""
    ok, out, _ = run_command(f"{sys.executable} -m yt_dlp --version")
    if ok:
        print(f"✅ yt-dlp {out} encontrado")
        return True
    print("❌ yt-dlp não encontrado")
    return False


def check_ffmpeg():
    """Verifica se ffmpeg está instalado (local ou PATH)."""
    # Primeiro tenta local
    local_ffmpeg = Path("bin/ffmpeg.exe")
    if local_ffmpeg.exists():
        ok, out, _ = run_command(f'"{local_ffmpeg.as_posix()}" -version')
        if ok:
            version_line = out.split("\n")[0]
            print(f"✅ ffmpeg local encontrado: {version_line}")
            return True
    # Fallback para PATH
    ok, out, _ = run_command("ffmpeg -version")
    if ok:
        version_line = out.split("\n")[0]
        print(f"✅ ffmpeg (PATH) encontrado: {version_line}")
        return True
    print("❌ ffmpeg não encontrado")
    return False


def install_via_winget(package):
    """Instala pacote via winget."""
    print(f"📦 Instalando {package} via winget...")
    ok, out, err = run_command(f"winget install --id {package} --silent --accept-source-agreements --accept-package-agreements")
    if ok:
        print(f"✅ {package} instalado com sucesso")
        return True
    else:
        print(f"❌ Falha ao instalar {package}: {err}")
        return False


def install_via_pip(package):
    """Instala pacote via pip."""
    print(f"📦 Instalando {package} via pip...")
    ok, out, err = run_command(f"pip install {package}")
    if ok:
        print(f"✅ {package} instalado com sucesso")
        return True
    else:
        print(f"❌ Falha ao instalar {package}: {err}")
        return False


def install_requirements():
    """Instala dependências do requirements.txt."""
    req_file = Path(__file__).parent / "requirements.txt"
    if req_file.exists():
        print("📦 Instalando dependências do requirements.txt...")
        ok, out, err = run_command(f"pip install -r {req_file}")
        if ok:
            print("✅ Dependências Python instaladas")
            return True
        else:
            print(f"❌ Falha ao instalar requirements: {err}")
            return False
    else:
        print("❌ requirements.txt não encontrado")
        return False


def main():
    print("=" * 60)
    print("🔍 VERIFICADOR DE DEPENDÊNCIAS - Fábrica de Cortes")
    print("=" * 60)

    all_ok = True

    # 1. Python
    if not check_python():
        all_ok = False
        print("\n⚠️  Python não encontrado ou versão antiga.")
        print("   Instale manualmente em: https://python.org/downloads")
        print("   Marque 'Add Python to PATH' durante a instalação.")

    # 2. pip
    if not check_pip():
        all_ok = False
        print("\n🔧 Tentando instalar pip via ensurepip...")
        ok, _, _ = run_command("python -m ensurepip --upgrade")
        if not ok:
            print("   Falha. Instale Python corretamente com pip incluso.")

    # 3. yt-dlp
    if not check_yt_dlp():
        all_ok = False
        print("\n🔧 Instalando yt-dlp via pip...")
        if not install_via_pip("yt-dlp"):
            print("   Tentando via winget...")
            install_via_winget("yt-dlp")

    # 4. ffmpeg
    if not check_ffmpeg():
        all_ok = False
        print("\n🔧 Instalando ffmpeg via winget...")
        if not install_via_winget("Gyan.FFmpeg"):
            print("   Tentando alternativa...")
            install_via_winget("FFmpeg")

    # 5. Dependências Python
    print("\n📦 Verificando dependências Python...")
    install_requirements()

    print("\n" + "=" * 60)
    if all_ok:
        print("✅ TODAS AS DEPENDÊNCIAS ESTÃO PRONTAS!")
    else:
        print("⚠️  ALGUMAS DEPENDÊNCIAS FALTAM - verifique acima")
        print("   Reinicie o terminal após instalações via winget")
    print("=" * 60)

    return all_ok


if __name__ == "__main__":
    success = main()
    input("\nPressione Enter para sair...")
    sys.exit(0 if success else 1)
