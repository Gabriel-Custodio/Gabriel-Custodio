@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PATH=%~dp0bin;%APPDATA%\Python\Python315\Scripts;%PATH%"

:MENU
cls
echo ==========================================
echo       FABRICA DE CORTES - MENU
echo ==========================================
echo.
echo [1] Buscar videos em alta (YouTube)
echo [2] Gerar cortes de uma URL
echo [3] Sair
echo.
set /p op="Escolha uma opcao: "

if "%op%"=="1" (
    python buscar_tendencias.py
    pause
    goto MENU
)
if "%op%"=="2" (
    echo.
    set /p url="Cole a URL do video do YouTube: "
    python gerar_cortes.py "%url%"
    pause
    goto MENU
)
if "%op%"=="3" exit
goto MENU
