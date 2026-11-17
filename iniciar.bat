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
echo [2] Gerar cortes de uma URL (audio original)
echo [3] Gerar cortes de uma URL (narracao IA - edge-tts)
echo [4] Sair
echo.
set /p op="Escolha uma opcao: "

if "%op%"=="1" (
    "%VENV_PYTHON%" buscar_tendencias.py
    pause
    goto MENU
)
if "%op%"=="2" goto GERAR_ORIGINAL
if "%op%"=="3" goto GERAR_IA
if "%op%"=="4" exit
goto MENU

:GERAR_ORIGINAL
echo.
set "url="
set /p url="Cole a URL do video do YouTube: "
if not defined url goto MENU
"%VENV_PYTHON%" gerar_cortes.py "%url%" --audio original
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