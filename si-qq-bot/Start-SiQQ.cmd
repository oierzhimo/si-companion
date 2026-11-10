@echo off
setlocal
set "APP_ROOT=%~dp0"
set "SIQQ_EXE=%APP_ROOT%release\SiQQ-0.2.5-Windows-x64\SiQQ-0.2.5-Windows-x64.exe"
if not exist "%SIQQ_EXE%" (
  echo SiQQ 0.2.5 executable was not found.
  pause
  exit /b 1
)
powershell.exe -NoProfile -File "%APP_ROOT%Start-LocalServices.ps1"
start "" "%SIQQ_EXE%" --connect-existing-napcat --local-router
endlocal

