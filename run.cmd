@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Run setup.ps1 first to create the virtual environment.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -X utf8 app.py %*
