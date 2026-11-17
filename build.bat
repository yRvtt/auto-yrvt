@echo off
cd /d "%~dp0"
pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --name MIR4Bot mir4_bot.py
echo.
echo Gerado: dist\MIR4Bot.exe
