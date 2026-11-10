"""Package the 1.2.2 InteractionPreview without touching stable archives."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from make_release import ROOT,RELEASE,zip_tree
sys.path.insert(0,str(ROOT/'src'))

VERSION='1.2.2-InteractionPreview'
STABLE_PREFIXES=('SiDesktopPet-1.2.0-','SiDesktopPet-1.2.1-')
SOURCE_EXCLUDE={'.venv','.tools','.build-tmp','.pip-cache','build','build-1.2.2',
    'dist','dist-1.2.2','release','data','qa','__pycache__','.git','tmp',
    'portable-check','acl-recovery'}

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def stable_fingerprints():
    result={}
    for path in sorted(RELEASE.iterdir()):
        if path.is_file() and path.name.startswith(STABLE_PREFIXES):
            result[path.name]={'bytes':path.stat().st_size,'sha256':digest(path)}
    return result

def main():
    from si_pet import __version__
    if __version__!='1.2.2':
        raise SystemExit('Source has advanced beyond 1.2.2; frozen archive names are reserved.')
    parser=argparse.ArgumentParser()
    parser.add_argument('--built',type=Path,default=ROOT/'dist-1.2.2/SiDesktopPet')
    args=parser.parse_args()
    built=args.built.resolve()
    if not (built/'Si-DesktopPet.exe').is_file():
        raise SystemExit('Build dist-1.2.2/SiDesktopPet first.')
    RELEASE.mkdir(exist_ok=True)
    before=stable_fingerprints()
    portable=RELEASE/f'SiDesktopPet-{VERSION}-Windows-x64.zip'
    source=RELEASE/f'SiDesktopPet-{VERSION}-Source.zip'
    # These names are deliberately distinct from the frozen 1.2.0/1.2.1
    # archives. Never turn a preview build into a stable archive by copying it
    # over an existing stable filename.
    for path in (portable,source):
        if path.name.startswith(STABLE_PREFIXES):
            raise SystemExit(f'Refusing to write a stable release name: {path.name}')
    with tempfile.TemporaryDirectory(dir=RELEASE,prefix='.interaction-preview-') as directory:
        stage=Path(directory)
        if not stage.resolve().is_relative_to(ROOT.resolve()):
            raise RuntimeError('Unsafe package staging path')
        folder=stage/f'SiDesktopPet-{VERSION}-Windows-x64'
        shutil.copytree(built,folder,ignore=shutil.ignore_patterns('data','__pycache__'))
        for name in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md'):
            shutil.copy2(ROOT/name,folder/name)
        shutil.copytree(ROOT/'docs',folder/'docs')
        shutil.copytree(ROOT/'licenses',folder/'licenses')
        (folder/'data').mkdir()
        (folder/'data/README.txt').write_text(
            '便携存档位于此处。升级时先退出旧程序，再复制旧版 data 目录；不要覆盖旧版文件夹。',
            encoding='utf-8')
        (folder/'INTERACTION_PREVIEW.txt').write_text(
            '祀桌宠 1.2.2 互动预览\n\n'
            '这是互动与边界行为的预览，不是 1.3 形象/动画完成版。\n'
            '运行时仍使用旧动作图集；art-source/v1.3 中的 v12 是面部待校准的造型基准，尚未接入运行时。\n\n'
            '右键桌宠或打开陪伴面板，可使用“和祀说一句”的五个明确话题：\n'
            '  想做点新东西 / 有点卡住了 / 想休息一会儿 / 想听你聊画 / 先安静陪我。\n'
            '桌宠只回应用户点选的话题，不会根据窗口标题、截图或键盘内容推断你在做什么。\n'
            '选择“先安静陪我”会靠边、坐下并降低不透明度；关闭“安静陪伴”会释放这次靠边停留。\n\n'
            '请勿覆盖 1.2.0 稳定版或 1.2.1 语音预览文件夹。需要继承存档时，退出旧程序后复制旧 data 目录。\n',
            encoding='utf-8')
        (folder/'开始陪伴.txt').write_text(
            '请双击 Si-DesktopPet.exe。\n'
            '完整复制此文件夹即可在其他 Windows 10/11 x64 电脑上运行。\n'
            '右键桌宠或托盘图标可退出；语音默认关闭，可在“行为与显示”中启用。\n',
            encoding='utf-8')
        zip_tree(folder,portable)
    zip_tree(ROOT,source,SOURCE_EXCLUDE,prefix=f'SiDesktopPet-{VERSION}-Source')
    after=stable_fingerprints()
    if before!=after:
        raise SystemExit('Stable 1.2.0/1.2.1 release files changed; refusing to report success.')
    archives={path.name:{'bytes':path.stat().st_size,'sha256':digest(path)} for path in (portable,source)}
    report={
        'version':VERSION,
        'archives':archives,
        'stable_archives_unchanged':True,
        'stable_fingerprints':before,
    }
    (RELEASE/'SHA256SUMS-interaction-preview.json').write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
