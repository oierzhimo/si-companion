# -*- mode: python ; coding: utf-8 -*-
import ssl
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


root = Path(SPECPATH).resolve()
tls_policy = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
datas = [(str(root / name), name) for name in ('knowledge', 'reactions')]
if tls_policy.is_file():
    datas.append((str(tls_policy), '.'))

analysis = Analysis(
    [str(root / 'main.py')],
    pathex=[str(root), str(root / 'src')],
    datas=datas,
    hiddenimports=collect_submodules('websockets'),
    runtime_hooks=[str(root / 'pyi_rth_qt_dlls.py')],
)
# Use Windows' ICU and UCRT rather than similarly named copies on PATH.
analysis.binaries = [
    entry for entry in analysis.binaries
    if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icudt78.dll', 'ucrtbase.dll'}
    and not Path(entry[0]).name.lower().startswith('api-ms-win-')
]
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True,
          name='SiQQ-Handoff', console=False, upx=False)
coll = COLLECT(exe, analysis.binaries, analysis.datas, upx=False, name='SiQQ-Handoff')
