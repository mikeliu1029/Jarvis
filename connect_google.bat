@echo off
rem Opens Jarvis's Settings on the Google page (Calendar, Gmail, Contacts, Drive, Classroom).
setlocal
cd /d "%~dp0"
set "JARVIS_PYW=%USERPROFILE%\.jarvis\venv\Scripts\pythonw.exe"
if not exist "%JARVIS_PYW%" (
  echo Jarvis isn't installed yet - double-click run_jarvis.bat first.
  pause
  goto :eof
)
start "" "%JARVIS_PYW%" -m jarvis --settings google
