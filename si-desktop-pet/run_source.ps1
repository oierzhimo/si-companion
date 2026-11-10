$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$siPython = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $siPython)) { throw 'Run build_windows.ps1 once to prepare the environment.' }
Start-Process -FilePath $siPython -ArgumentList 'main.py' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
