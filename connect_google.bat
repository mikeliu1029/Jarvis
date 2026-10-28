@echo off
rem Double-click to connect Jarvis to Google: Calendar, Gmail, Contacts, Drive and Classroom.
setlocal
cd /d "%~dp0"
set "JARVIS_PY=%USERPROFILE%\.jarvis\venv\Scripts\python.exe"
if not exist "%JARVIS_PY%" (
  echo Jarvis isn't installed yet - double-click run_jarvis.bat first.
  pause
  goto :eof
)
title J.A.R.V.I.S. - connect Google
"%JARVIS_PY%" -m jarvis --google
