@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PATH=%~dp0bin;%PATH%"

set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    set "VENV_PYTHON=python"
)

echo ==========================================
echo   FABRICA DE CORTES - INTERFACE
echo ==========================================
echo.
echo O navegador vai abrir em http://127.0.0.1:8765
echo Deixe esta janela aberta enquanto usar. Para encerrar: Ctrl+C.
echo.
"%VENV_PYTHON%" app\servidor.py
pause
