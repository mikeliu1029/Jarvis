@echo off
rem Double-click to start J.A.R.V.I.S. The first time, it installs everything it needs (about 5 minutes).
setlocal
cd /d "%~dp0"
set "JARVIS_PY=%USERPROFILE%\.jarvis\venv\Scripts\python.exe"
if not exist "%JARVIS_PY%" (
  title Installing J.A.R.V.I.S.
  echo Installing Jarvis. This happens once and takes about five minutes - leave this window open.
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
  if errorlevel 1 (
    echo.
    echo Setup didn't finish - see the message above.
    pause
    goto :eof
  )
)
"%JARVIS_PY%" -m jarvis.deps
title J.A.R.V.I.S.
"%JARVIS_PY%" -m jarvis %*
if errorlevel 1 (
  echo.
  echo Jarvis stopped with an error - see above.
  pause
)
