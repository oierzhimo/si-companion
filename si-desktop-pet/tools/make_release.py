from pathlib import Path
import shutil
import zipfile
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
RELEASE=ROOT/'release'
VERSION='1.2.0'

def zip_tree(source,destination,exclude=None,prefix=None):
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(source.rglob('*')):
            rel=path.relative_to(source)
            if path.is_file() and not any(part in (exclude or set()) for part in rel.parts):
                archive.write(path,str(Path(prefix or source.name)/rel))

def main():
    built=ROOT/'dist/SiDesktopPet'
    if not (built/'Si-DesktopPet.exe').is_file():raise SystemExit('Build the Windows executable first.')
    RELEASE.mkdir(exist_ok=True)
    folder=RELEASE/f'SiDesktopPet-{VERSION}-Windows-x64'
    shutil.copytree(built,folder,dirs_exist_ok=True,ignore=shutil.ignore_patterns('data','__pycache__'))
    for name in ['README.md','LICENSE','THIRD_PARTY_NOTICES.md']:
        shutil.copy2(ROOT/name,folder/name)
    shutil.copytree(ROOT/'docs',folder/'docs',dirs_exist_ok=True)
    shutil.copytree(ROOT/'licenses',folder/'licenses',dirs_exist_ok=True)
    (folder/'data').mkdir(exist_ok=True)
    (folder/'data/README.txt').write_text('便携存档保存在这里。复制整个程序文件夹即可携带陪伴记录。',encoding='utf-8')
    (folder/'开始陪伴.txt').write_text('请双击 Si-DesktopPet.exe。\n完整复制此文件夹即可在其他 Windows 10/11 x64 电脑上运行。\n右键桌宠或托盘图标可退出。\n',encoding='utf-8')
    portable=RELEASE/f'{folder.name}.zip'
    zip_tree(folder,portable)
    source=RELEASE/f'SiDesktopPet-{VERSION}-Source.zip'
    zip_tree(ROOT,source,{'.venv','.tools','build','dist','release','data','__pycache__','.git','tmp','portable-check'},f'SiDesktopPet-{VERSION}-Source')
    checksums={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in [portable,source]}
    (RELEASE/'SHA256SUMS.json').write_text(json.dumps(checksums,indent=2),encoding='utf-8')
    print(json.dumps(checksums,indent=2))
if __name__=='__main__':main()
