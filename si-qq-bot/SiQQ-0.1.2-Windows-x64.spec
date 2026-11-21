# -*- mode: python ; coding: utf-8 -*-
"""Portable Windows build for the QQ private-chat source preview."""
import os
import sys

sys.path.insert(0, 'F:/si_accompany/si-qq-bot/.venv/Lib/site-packages')
from PyInstaller.utils.hooks import collect_submodules


hiddenimports = ['si_pet.conversation']
hiddenimports += collect_submodules('websockets')


a = Analysis(
    ['F:/si_accompany/si-qq-bot/main.py'],
    pathex=[
        'F:/si_accompany/si-qq-bot',
        'F:/si_accompany/si-qq-bot/.venv/Lib/site-packages',
        'E:/create/si-desktop-pet/.venv/Lib/site-packages',
        'F:/si_accompany/si-desktop-pet/src',
    ],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['F:/si_accompany/si-qq-bot/pyi_rth_qt_dlls.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# PyInstaller's analysis environment can see ICU from the Codex Poppler runtime
# on PATH. Those ICU 78 DLLs are incompatible with this Qt build, which uses
# Windows' ICU implementation (the PySide6 development environment resolves
# `icuuc.dll` from System32). Omit the unrelated copies to avoid a DLL name
# collision in the frozen package.
_incompatible_icu = {'icuuc.dll', 'icudt78.dll'}
a.binaries = [
    entry for entry in a.binaries
    if os.path.basename(entry[0]).casefold() not in _incompatible_icu
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SiQQ-0.1.2-Windows-x64',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
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
    upx=False,
    upx_exclude=[],
    name='SiQQ-0.1.2-Windows-x64',
)
