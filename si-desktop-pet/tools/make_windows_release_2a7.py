"""Package the runnable 2.0.0a7 Windows desktop pet without replacing prior releases."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import tempfile

from make_release import ROOT, RELEASE, zip_tree

sys.path.insert(0, str(ROOT/'src'))
from si_pet import __version__
from si_pet.companion import LINES

VERSION='2.0.0a7'
NAME=f'SiDesktopPet-{VERSION}-Windows-x64'
MOBILE_LINES=(ROOT/'mobile/android/app/src/main/assets/lines.json',
              ROOT/'mobile/harmony/entry/src/main/resources/rawfile/lines.json')
# Build outputs and the local qa directory (including voice listening references)
# must never enter the source archive.
EXCLUDE={'.venv','.tools','.build-tmp','.pip-cache','build','build-1.2.2',
         'build-2.0a1','build-2.0a2','build-2.0a3','build-2.0a4','build-2.0a5',
         'build-2.0a6','build-2.0a7','dist','dist-1.2.2','dist-2.0a1','dist-2.0a2',
         'dist-2.0a3','dist-2.0a4','dist-2.0a5','dist-2.0a6','dist-2.0a7',
         'release','data','qa','__pycache__','.git','tmp','portable-check',
         'acl-recovery','qa-settings.json'}

def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def dialogue_summary():
    if len(LINES)!=11 or any(len(lines)!=6 for lines in LINES.values()):
        raise SystemExit('Expected 11 occasions with 6 lines each; update the release notes.')
    for path in MOBILE_LINES:
        data=json.loads(path.read_text(encoding='utf-8'))
        if list(data)!=list(LINES) or any(data[kind]!=lines for kind,lines in LINES.items()):
            raise SystemExit(f'Mobile dialogue table differs from Windows: {path}')
    return {'occasions':len(LINES),'lines':sum(len(lines) for lines in LINES.values()),
            'mobile_tables_synced':True}

def main():
    if __version__!=VERSION:
        raise SystemExit('Source version changed; choose a new release name.')
    dialogue=dialogue_summary()
    built=ROOT/'dist-2.0a7'/'SiDesktopPet'
    if not (built/'Si-DesktopPet.exe').is_file():
        raise SystemExit('Build dist-2.0a7/SiDesktopPet first.')
    RELEASE.mkdir(exist_ok=True)
    previous={p.name:digest(p) for p in RELEASE.glob('*.zip')}
    portable=RELEASE/f'{NAME}.zip'
    source=RELEASE/f'SiDesktopPet-{VERSION}-Source.zip'
    if portable.exists() or source.exists():
        raise SystemExit('Release name already exists; bump version instead of overwriting it.')
    with tempfile.TemporaryDirectory(dir=RELEASE,prefix='.release-2a7-') as directory:
        folder=Path(directory)/NAME
        shutil.copytree(built,folder,ignore=shutil.ignore_patterns('data','__pycache__'))
        for name in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md'):
            shutil.copy2(ROOT/name,folder/name)
        shutil.copytree(ROOT/'docs',folder/'docs')
        shutil.copytree(ROOT/'licenses',folder/'licenses')
        (folder/'data').mkdir()
        (folder/'data'/'README.txt').write_text('用户存档保存在本目录。升级时先退出旧版，再复制旧 settings.json；保留旧文件夹。',encoding='utf-8')
        (folder/'START_HERE.txt').write_text(
            '祀桌宠 2.0.0a7 Windows 便携版\n\n'
            '完整解压本文件夹后，双击 Si-DesktopPet.exe。第一次会打开陪伴面板。\n'
            '退出：右键桌宠或系统托盘图标。找不到桌宠：从托盘选择“找回祀”。\n'
            '升级：先退出旧版，将旧文件夹整个 data 目录复制到新版；保留旧文件夹。\n'
            '默认离线运行，不会自动同步。局域网配对与手动事件交换均为可选预览，'
            '需要时才双击 Start-Sync-Preview.cmd。请先阅读 MANUAL_SYNC_PREVIEW.txt。\n'
            '开启后，在“相伴手记 → 局域网配对与同步”中生成邀请或粘贴另一台电脑的邀请。'
            '配对后可点击“现在同步”或勾选每分钟自动连接。完整说明见 docs/LAN_SYNC_PREVIEW.md。\n'
            '本版把 Windows 本地陪伴短句扩充为 11 类、每类 6 句，共 66 句，并按情境独立轮换。\n'
            'Android 与鸿蒙工程只同步了同一份台词数据和轮换逻辑；本版没有重新安装 APK，'
            '也未取得 Harmony HAP。\n'
            '语音默认关闭；启用时的默认声线仍是 VoiceStudio 档案 A；另一条 B 仅作实验试听，尚未加入可选声线。\n'
            '当前动作仍用旧图集；v18 形象尚未获得管理员确认，本版不是形象修复。\n'
            'a3/a4 已生成的零值账本不会自动改写，详见 docs/LAN_SYNC_PREVIEW.md 的升级限制。\n',
            encoding='utf-8')
        (folder/'Start-Sync-Preview.cmd').write_text(
            '@echo off\r\ncd /d "%~dp0"\r\nstart "" "Si-DesktopPet.exe" --sync-preview\r\n',encoding='ascii')
        (folder/'MANUAL_SYNC_PREVIEW.txt').write_text(
            '可选：Windows 手动协同预览\n\n'
            '先退出普通桌宠，再双击 Start-Sync-Preview.cmd。首次启用会备份旧存档并创建事件账本。\n'
            '在“相伴手记”页导出事件包，传到另一台 Windows PC 后导入；'
            '另一台也导出并传回本机导入，才能让两端取得相同事件。\n'
            '请保管事件包，其中有互动和手记。摘要只校验损坏，不做身份认证。\n'
            '不要复制另一台电脑的 settings.sync-v1.json；全新空白电脑可接收已有电脑的旧记录，旧存档已分叉时会拒绝自动合并。\n'
            'a3/a4 已生成的零值账本不会自动改写，请阅读 docs/LAN_SYNC_PREVIEW.md 的升级限制。\n'
            'Android 与鸿蒙目前只同步了台词源数据和轮换逻辑；Android 尚未重新安装，鸿蒙尚无 HAP。\n'
            '物理多设备协同尚未验收。\n',encoding='utf-8')
        zip_tree(folder,portable)
    zip_tree(ROOT,source,EXCLUDE,prefix=f'SiDesktopPet-{VERSION}-Source')
    current={p.name:digest(p) for p in RELEASE.glob('*.zip')}
    if any(current.get(name)!=value for name,value in previous.items()):
        raise SystemExit('An earlier release archive changed during packaging.')
    report={'version':VERSION,'dialogue':dialogue,
            'archives':{p.name:{'bytes':p.stat().st_size,'sha256':digest(p)}
                        for p in (portable,source)},
            'previous_archives_unchanged':True}
    (RELEASE/f'SHA256SUMS-{VERSION}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
