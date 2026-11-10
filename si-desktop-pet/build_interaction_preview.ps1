$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$siPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $siPython)) {
    throw 'Run this from the project with .venv ready: .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt'
}
& $siPython tools\build_interaction_preview.py
if ($LASTEXITCODE -ne 0) { throw '1.2.2 InteractionPreview build failed.' }
