$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$siPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $siPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) { py -3.12 -m venv .venv }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { python -m venv .venv }
    else { throw 'Install Python 3.12 x64, then run this script again.' }
}
& $siPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $siPython tools\prepare_assets.py
if ($LASTEXITCODE -ne 0) { throw 'Asset preparation failed.' }
& $siPython -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& $siPython tests\integration_check.py
if ($LASTEXITCODE -ne 0) { throw 'Integration checks failed.' }
& $siPython tests\v11_check.py
if ($LASTEXITCODE -ne 0) { throw 'High DPI and animation checks failed.' }
& $siPython tests\preview_check.py
if ($LASTEXITCODE -ne 0) { throw 'Preview centering checks failed.' }
& $siPython -m PyInstaller --noconfirm SiDesktopPet.spec
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed.' }
& $siPython tools\make_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release creation failed.' }
& $siPython tools\verify_portable.py
if ($LASTEXITCODE -ne 0) { throw 'Portable verification failed.' }
& $siPython tools\finalize_release.py
if ($LASTEXITCODE -ne 0) { throw 'Source archive finalization failed.' }
Write-Host 'Ready: release\SiDesktopPet-1.2.0-Windows-x64.zip'
