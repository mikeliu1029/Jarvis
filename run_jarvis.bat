@echo off
rem Double-click to start J.A.R.V.I.S. Extra options: --text  --mute  --no-hud  --selftest
setlocal
cd /d "%~dp0"
set "JARVIS_PY=%USERPROFILE%\.jarvis\venv\Scripts\python.exe"
if not exist "%JARVIS_PY%" (
  echo Jarvis isn't installed yet - running setup first...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
  if errorlevel 1 goto :eof
)
title J.A.R.V.I.S.
"%JARVIS_PY%" -m jarvis %*
