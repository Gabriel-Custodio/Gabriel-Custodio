@echo off
cd /d "%~dp0"
set /p msg="O que voce atualizou no codigo? "
git add .
git commit -m "%msg%"
git push
echo.
echo Atualizacao enviada para o GitHub com sucesso!
pause
