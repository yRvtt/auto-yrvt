@echo off
cd /d "%~dp0"
pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --icon "auto_yrvt\resources\icon.ico" --add-data "auto_yrvt\resources\icon.ico;auto_yrvt\resources" --collect-all pywinauto --collect-all comtypes --name AutoYrvt run.py
echo.
echo Gerado: dist\AutoYrvt.exe
