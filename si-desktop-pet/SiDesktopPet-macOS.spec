from pathlib import Path
import ssl

root = Path(SPECPATH)
ssl_policy = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
ssl_data = [(str(ssl_policy), '.')] if ssl_policy.is_file() else []

runtime_assets = [
    (str(root / 'assets/animations.json'), 'assets'),
    (str(root / 'assets/si.ico'), 'assets'),
    (str(root / 'assets/hd'), 'assets/hd'),
]
if (root / 'assets/portraits').is_dir():
    runtime_assets.append((str(root / 'assets/portraits'), 'assets/portraits'))

a = Analysis(
    [str(root / 'main.py')],
    pathex=[str(root / 'src')],
    binaries=[],
    datas=runtime_assets + [(str(root / 'licenses'), 'licenses')] + ssl_data,
    hiddenimports=['PySide6.QtNetwork'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtDesigner', 'PySide6.QtHelp', 'unittest'],
    noarchive=False,
)

pyz = PYZ(a.pure)
icon_icns = root / 'assets' / 'si.icns'
icon_arg = str(icon_icns) if icon_icns.is_file() else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Si-DesktopPet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon_arg,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='SiDesktopPet',
)

app = BUNDLE(
    coll,
    name='SiDesktopPet.app',
    icon=icon_arg,
    bundle_identifier='com.sicompanion.desktoppet',
    info_plist={
        'CFBundleDisplayName': '小祀 · Si Companion',
        'CFBundleName': 'SiDesktopPet',
        'CFBundleShortVersionString': '2.1.2',
        'CFBundleVersion': '2.1.2',
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
        'NSLocalNetworkUsageDescription': '小祀需要局域网权限以在同一 Wi-Fi 下与手机或 Windows 端同步陪伴记录与对话。',
    },
)
