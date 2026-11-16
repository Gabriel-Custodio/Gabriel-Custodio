@echo off
cd /d "%~dp0"

echo Arquivos que serao enviados:
echo.
git status --short
echo.

set "msg="
set /p msg="O que voce atualizou no codigo? (deixe vazio para cancelar) "
if not defined msg goto CANCELAR
rem aspas dentro da mensagem quebrariam o comando do git
set "msg=%msg:"='%"

set "ok="
set /p ok="Enviar tudo isso para o GitHub? (S/N) "
if /i not "%ok%"=="S" goto CANCELAR

git add -A
git commit -m "%msg%"
if errorlevel 1 goto ERRO_COMMIT

git push
if errorlevel 1 goto ERRO_PUSH

echo.
echo Atualizacao enviada para o GitHub com sucesso!
pause
exit /b 0

:CANCELAR
echo.
echo Cancelado. Nada foi enviado.
pause
exit /b 1

:ERRO_COMMIT
echo.
echo [ERRO] O commit falhou (talvez nao haja nada para enviar). Nada foi enviado.
pause
exit /b 1

:ERRO_PUSH
echo.
echo [ERRO] O envio para o GitHub falhou. O commit ficou salvo so no seu computador.
echo Se alguem atualizou o projeto antes, rode baixar_github.bat e tente de novo.
pause
exit /b 1
