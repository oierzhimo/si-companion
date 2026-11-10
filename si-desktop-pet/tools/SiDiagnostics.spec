from pathlib import Path
root=Path(SPECPATH).parent
a=Analysis([str(root/'main.py')],pathex=[str(root/'src')],binaries=[],datas=[(str(root/'assets'),'assets'),(str(root/'licenses'),'licenses')],hiddenimports=['PySide6.QtNetwork'],hookspath=[],hooksconfig={},runtime_hooks=[],excludes=['tkinter','PySide6.QtQml','PySide6.QtQuick','PySide6.QtDesigner','PySide6.QtHelp','PIL','unittest'],noarchive=False)
a.binaries=[entry for entry in a.binaries if Path(entry[0]).name.lower() not in {'icuuc.dll','icudt78.dll','ucrtbase.dll'} and not Path(entry[0]).name.lower().startswith('api-ms-win-')]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='Si-Diagnostics',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=True,icon=str(root/'assets/si.ico'),uac_admin=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='SiDiagnostics')
