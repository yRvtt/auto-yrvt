@echo off
:: Roda o Auto yRvt como administrador
cd /d "%~dp0"
powershell -Command "Start-Process python -ArgumentList 'run.py' -WorkingDirectory '%~dp0' -Verb RunAs"
