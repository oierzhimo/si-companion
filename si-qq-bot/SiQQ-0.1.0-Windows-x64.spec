# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['si_pet.conversation']
hiddenimports += collect_submodules('websockets')


a = Analysis(
    ['F:/si_accompany/si-qq-bot/main.py'],
    pathex=['F:/si_accompany/si-qq-bot', 'F:/si_accompany/si-qq-bot/.venv/Lib/site-packages', 'F:/si_accompany/si-desktop-pet/src'],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SiQQ-0.1.0-Windows-x64',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SiQQ-0.1.0-Windows-x64',
)
