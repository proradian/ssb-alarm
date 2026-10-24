@echo off
rem "I'm up": woke up before the alarm - solve 10 problems and the alarm becomes a quiet check.
cd /d "%~dp0"
start "" pythonw "%~dp0ssb_alarm.py" --up
