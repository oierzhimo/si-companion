from pathlib import Path
import ssl
root=Path(SPECPATH)
# This bundled Python's ssl module loads an adjacent CA-policy resource when
# creating any TLS context. Preserve it in frozen apps as well as its code.
ssl_policy=Path(ssl.__file__).with_name('codex-rejected-cas.pem')
ssl_data=[(str(ssl_policy),'.')] if ssl_policy.is_file() else []
a=Analysis([str(root/'main.py')],pathex=[str(root/'src')],binaries=[],
    datas=[(str(root/'assets'),'assets'),(str(root/'licenses'),'licenses')]+ssl_data,
    hiddenimports=['PySide6.QtNetwork'],hookspath=[],hooksconfig={},runtime_hooks=[],
    excludes=['tkinter','PySide6.QtQml','PySide6.QtQuick','PySide6.QtDesigner','PySide6.QtHelp','PIL','unittest'],noarchive=False)
# Qt uses Windows' unversioned ICU API. The host build environment also has a
# different ICU with version-suffixed exports on PATH. Never redistribute it or
# OS API-set / UCRT libraries in place of Windows 10/11's own implementations.
a.binaries=[entry for entry in a.binaries if Path(entry[0]).name.lower() not in {'icuuc.dll','icudt78.dll','ucrtbase.dll'} and not Path(entry[0]).name.lower().startswith('api-ms-win-')]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='Si-DesktopPet',debug=False,
    bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    icon=str(root/'assets/si.ico'),uac_admin=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='SiDesktopPet')
