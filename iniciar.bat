@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PATH=%~dp0bin;%PATH%"

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
if "%op%"=="2" goto GERAR
if "%op%"=="3" exit
goto MENU

:GERAR
rem O set /p fica fora de um bloco ( ) de proposito: dentro do bloco o %url%
rem seria expandido antes do usuario digitar e chegaria vazio no Python.
echo.
set "url="
set /p url="Cole a URL do video do YouTube: "
if not defined url goto MENU
python gerar_cortes.py "%url%"
pause
goto MENU
