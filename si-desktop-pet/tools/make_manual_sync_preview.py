"""Package the isolated Windows 2.0.0a1 manual sync preview."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import tempfile

from make_release import ROOT, RELEASE, zip_tree

sys.path.insert(0,str(ROOT/'src'))
from si_pet import __version__

VERSION='2.0.0a1-ManualSyncPreview'
EXCLUDE={'.venv','.tools','.build-tmp','.pip-cache','build','build-1.2.2','build-2.0a1',
         'dist','dist-1.2.2','dist-2.0a1','release','data','qa','__pycache__','.git','tmp',
         'portable-check','acl-recovery'}


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def main():
    if __version__!='2.0.0a1':
        raise SystemExit('Source version changed; choose a new preview package name.')
    built=ROOT/'dist-2.0a1'/'SiDesktopPet'
    if not (built/'Si-DesktopPet.exe').is_file():
        raise SystemExit('Build dist-2.0a1/SiDesktopPet first.')
    RELEASE.mkdir(exist_ok=True)
    before={file.name:digest(file) for file in RELEASE.iterdir()
            if file.is_file() and file.name.startswith('SiDesktopPet-1.')}
    portable=RELEASE/f'SiDesktopPet-{VERSION}-Windows-x64.zip'
    source=RELEASE/f'SiDesktopPet-{VERSION}-Source.zip'
    with tempfile.TemporaryDirectory(dir=RELEASE,prefix='.manual-sync-preview-') as directory:
        stage=Path(directory)
        if not stage.resolve().is_relative_to(ROOT.resolve()):
            raise RuntimeError('Unsafe staging path')
        folder=stage/portable.stem
        shutil.copytree(built,folder,ignore=shutil.ignore_patterns('data','__pycache__'))
        for name in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md'):
            shutil.copy2(ROOT/name,folder/name)
        shutil.copytree(ROOT/'docs',folder/'docs')
        shutil.copytree(ROOT/'licenses',folder/'licenses')
        (folder/'data').mkdir()
        (folder/'data'/'README.txt').write_text(
            '旧版数据迁移：退出旧桌宠后，将旧版 data/settings.json 复制到本目录 data/。'
            '保留原版文件夹；不要复制另一台电脑的 settings.sync-v1.json。\n',encoding='utf-8')
        (folder/'Start-Sync-Preview.cmd').write_text(
            '@echo off\r\ncd /d "%~dp0"\r\nstart "" "Si-DesktopPet.exe" --sync-preview\r\n',encoding='ascii')
        (folder/'MANUAL_SYNC_PREVIEW.txt').write_text(
            '祀桌宠 2.0.0a1 Windows 手动协同预览\n\n'
            '双击 Start-Sync-Preview.cmd 启动。请始终通过此入口使用本预览。\n'
            '首次启用会备份 data/settings.json，建立独立的 settings.sync-v1.json 账本。\n'
            '在“相伴手记”页导出事件包，传到另一台 Windows PC 后导入；'
            '另一台再导出，传回本机导入，才能让两端取得相同事件。\n'
            '事件包包含亲密度、互动和手记，请自行保管。摘要只校验损坏，不提供身份认证。\n'
            '本机位置、缩放、语音和权限不跨设备覆盖。旧存档若迁移前已分叉，导入会拒绝自动合并。\n'
            '当前仍使用旧动作图集；1.3 立绘和动画尚未完成。'
            '本预览未验证两台物理 PC，也不包含 Android 或鸿蒙客户端。\n',encoding='utf-8')
        zip_tree(folder,portable)
    zip_tree(ROOT,source,EXCLUDE,prefix=f'SiDesktopPet-{VERSION}-Source')
    after={file.name:digest(file) for file in RELEASE.iterdir()
           if file.is_file() and file.name.startswith('SiDesktopPet-1.')}
    if before!=after:
        raise SystemExit('A 1.x archive changed during 2.0 preview packaging.')
    report={'version':VERSION,'archives':{path.name:{'bytes':path.stat().st_size,'sha256':digest(path)}
                                         for path in (portable,source)},'1x_archives_unchanged':True}
    (RELEASE/'SHA256SUMS-manual-sync-preview.json').write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
