@echo off
rem Opens the ssb-alarm main window. Requires Python 3 (python.org), with "Add python.exe to PATH".
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
  echo Python 3 not found. Install it from https://www.python.org/downloads/
  echo and check "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)
start "" pythonw "%~dp0ssb_alarm.py"
