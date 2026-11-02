@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.11 -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements-gui.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "import tkinter, voyo"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" install_chrome.py
if errorlevel 1 goto failed
pause
exit /b 0
:failed
echo Setup failed. Check the error above. Python 3.11 with Tcl/Tk is required.
pause
exit /b 1
