@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.11 -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import tkinter, playwright.sync_api, drmdl" >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements-gui.txt
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" voyo.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Could not start. Install Python 3.11 with Tcl/Tk support and Google Chrome.
echo See the error above. Your device file and browser profile were not removed.
pause
exit /b 1
