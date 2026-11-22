@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PATH=%~dp0bin;%PATH%"

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    set "VENV_PYTHON=python"
)

:MENU
cls
echo ==========================================
echo       FABRICA DE CORTES - MENU
echo ==========================================
echo.
echo [1] Buscar videos em alta (YouTube)
echo [2] Reparar/Reprocessar clipe existente (corrigir sincronia + layout ClipFlow)
echo [3] Gerar cortes de uma URL (narracao IA - edge-tts)
echo [4] Modo Gameplay / Streamer (Tela Dividida + Audio Original)
echo [5] Monitor de Lives (Tempo Real - Clips Automaticos)
echo [6] Sair
echo.
set /p op="Escolha uma opcao: "

if "%op%"=="1" (
    "%VENV_PYTHON%" buscar_tendencias.py
    pause
    goto MENU
)
if "%op%"=="2" goto REPARAR_CLIPE
if "%op%"=="3" goto GERAR_IA
if "%op%"=="4" goto GERAR_GAMEPLAY
if "%op%"=="5" goto MONITOR_LIVES
if "%op%"=="6" exit
goto MENU

:REPARAR_CLIPE
echo.
echo ==========================================
echo   REPARAR / REPROCESSAR CLIPE EXISTENTE
echo ==========================================
echo.
echo Corrige sincronia de audio/video e aplica layout ClipFlow 3 camadas (1080x1920).
echo.
set "clip_path="
set /p clip_path="Caminho do arquivo do clipe (ex: cortes_prontos\corte_1.mp4): "
if not defined clip_path goto MENU
if not exist "%clip_path%" (
    echo [ERRO] Arquivo nao encontrado: %clip_path%
    pause
    goto MENU
)
"%VENV_PYTHON%" gerar_cortes.py --repair "%clip_path%"
pause
goto MENU

:GERAR_IA
echo.
set "url="
set /p url="Cole a URL do video do YouTube: "
if not defined url goto MENU
"%VENV_PYTHON%" gerar_cortes.py "%url%" --audio ia
pause
goto MENU

:GERAR_GAMEPLAY
echo.
set "url="
set /p url="Cole a URL do video do YouTube: "
if not defined url goto MENU
"%VENV_PYTHON%" gerar_cortes.py "%url%" --audio gameplay
pause
goto MENU

:MONITOR_LIVES
echo.
echo ==========================================
echo   MONITOR DE LIVES - TEMPO REAL
echo ==========================================
echo.
echo Suporta: YouTube, Twitch, Kick
echo.
set "url="
set /p url="Cole a URL da live: "
if not defined url goto MENU
echo.
echo Posicao da webcam:
echo [1] Canto inferior esquerdo (x=0.02, y=0.70, w=0.25, h=0.28)
echo [2] Canto superior esquerdo (x=0.02, y=0.02, w=0.25, h=0.28)
echo [3] Canto inferior direito (x=0.73, y=0.70, w=0.25, h=0.28)
echo [4] Canto superior direito (x=0.73, y=0.02, w=0.25, h=0.28)
echo [5] Meio-direita / acima do minimapa (x=0.772, y=0.450, w=0.222, h=0.242)
echo [6] Detectar automaticamente com IA (Groq Vision)
echo [7] Sem webcam (apenas zoom central 9:16)
echo.
set /p wc="Escolha [1-7] (padrao 1): "
if not defined wc set wc=1
"%VENV_PYTHON%" monitor_lives.py "%url%" --webcam %wc%
pause
goto MENU