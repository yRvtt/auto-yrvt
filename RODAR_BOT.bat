@echo off
:: Roda o bot como administrador
cd /d "%~dp0"
powershell -Command "Start-Process python -ArgumentList 'mir4_bot.py' -WorkingDirectory '%~dp0' -Verb RunAs"
