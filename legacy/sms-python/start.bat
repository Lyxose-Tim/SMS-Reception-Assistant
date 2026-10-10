@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Project environment missing. Run scripts\windows.ps1 -Action Setup in PowerShell 7.5 or later.
  exit /b 1
)
set "APP_MODE=demo"
set "ENABLE_REAL_SEND=false"
".venv\Scripts\python.exe" -B app.py %*
