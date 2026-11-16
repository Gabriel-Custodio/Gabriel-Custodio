@echo off
cd /d "%~dp0"
git pull
if errorlevel 1 goto ERRO
echo.
echo Projeto atualizado com a versao mais recente do GitHub!
pause
exit /b 0

:ERRO
echo.
echo [ERRO] Nao foi possivel atualizar. Veja a mensagem do git acima.
echo Se voce alterou arquivos locais, envie-os com salvar_github.bat antes.
pause
exit /b 1
