@echo off
setlocal
set "APP_ROOT=%~dp0"
set "PY_EXE=E:\create\si-desktop-pet\.venv\Scripts\python.exe"
if not exist "%PY_EXE%" (
  echo Python environment not found at %PY_EXE%
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%APP_ROOT%Start-LocalServices.ps1"
start "" /D "%APP_ROOT%" "%PY_EXE%" "%APP_ROOT%main.py" --connect-existing-napcat --local-router
endlocal
