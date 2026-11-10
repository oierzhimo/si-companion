"""Package the runnable 2.0.0a2 Windows desktop pet without replacing prior releases."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import tempfile

from make_release import ROOT, RELEASE, zip_tree

sys.path.insert(0, str(ROOT/'src'))
from si_pet import __version__

VERSION='2.0.0a2'
NAME=f'SiDesktopPet-{VERSION}-Windows-x64'
EXCLUDE={'.venv','.tools','.build-tmp','.pip-cache','build','build-1.2.2',
         'build-2.0a1','build-2.0a2','dist','dist-1.2.2','dist-2.0a1',
         'dist-2.0a2','release','data','qa','__pycache__','.git','tmp',
         'portable-check','acl-recovery'}

def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    if __version__!=VERSION:
        raise SystemExit('Source version changed; choose a new release name.')
    built=ROOT/'dist-2.0a2'/'SiDesktopPet'
    if not (built/'Si-DesktopPet.exe').is_file():
        raise SystemExit('Build dist-2.0a2/SiDesktopPet first.')
    RELEASE.mkdir(exist_ok=True)
    previous={p.name:digest(p) for p in RELEASE.glob('SiDesktopPet-*.zip') if not p.name.startswith(NAME)}
    portable=RELEASE/f'{NAME}.zip'
    source=RELEASE/f'SiDesktopPet-{VERSION}-Source.zip'
    if portable.exists() or source.exists():
        raise SystemExit('Release name already exists; bump version instead of overwriting it.')
    with tempfile.TemporaryDirectory(dir=RELEASE,prefix='.release-2a2-') as directory:
        folder=Path(directory)/NAME
        shutil.copytree(built,folder,ignore=shutil.ignore_patterns('data','__pycache__'))
        for name in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md'):
            shutil.copy2(ROOT/name,folder/name)
        shutil.copytree(ROOT/'docs',folder/'docs')
        shutil.copytree(ROOT/'licenses',folder/'licenses')
        (folder/'data').mkdir()
        (folder/'START_HERE.txt').write_text(
            '祀桌宠 2.0.0a2 Windows 便携版\n\n'
            '完整解压本文件夹后，双击 Si-DesktopPet.exe。第一次会打开陪伴面板。\n'
            '退出：右键桌宠或系统托盘图标。找不到桌宠：从托盘选择“找回祀”。\n'
            '升级：先退出旧版，将旧文件夹 data/settings.json 复制到新版 data/；保留旧文件夹。\n'
            '默认离线运行，不会自动同步。两台 Windows PC 的手动事件交换是可选预览，'
            '需要时才双击 Start-Sync-Preview.cmd。请先阅读 MANUAL_SYNC_PREVIEW.txt。\n'
            '当前动作仍用旧图集；立绘颜色、面部与动作重制还在校准中。\n',encoding='utf-8')
        (folder/'Start-Sync-Preview.cmd').write_text(
            '@echo off\r\ncd /d "%~dp0"\r\nstart "" "Si-DesktopPet.exe" --sync-preview\r\n',encoding='ascii')
        (folder/'MANUAL_SYNC_PREVIEW.txt').write_text(
            '可选：Windows 手动协同预览\n\n'
            '先退出普通桌宠，再双击 Start-Sync-Preview.cmd。首次启用会备份旧存档并创建事件账本。\n'
            '在“相伴手记”页导出事件包，传到另一台 Windows PC 后导入；'
            '另一台也导出并传回本机导入，才能让两端取得相同事件。\n'
            '请保管事件包，其中有互动和手记。摘要只校验损坏，不做身份认证。\n'
            '不要复制另一台电脑的 settings.sync-v1.json；旧存档已分叉时会拒绝自动合并。\n'
            '两台物理 PC、Android 手机及鸿蒙 5 平板尚未验证或实现。\n',encoding='utf-8')
        zip_tree(folder,portable)
    zip_tree(ROOT,source,EXCLUDE,prefix=f'SiDesktopPet-{VERSION}-Source')
    after={p.name:digest(p) for p in RELEASE.glob('SiDesktopPet-*.zip') if not p.name.startswith(NAME) and p!=source}
    if previous!=after:
        raise SystemExit('An earlier release archive changed during packaging.')
    report={'version':VERSION,'archives':{p.name:{'bytes':p.stat().st_size,'sha256':digest(p)}
                                          for p in (portable,source)},'previous_archives_unchanged':True}
    (RELEASE/f'SHA256SUMS-{VERSION}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
