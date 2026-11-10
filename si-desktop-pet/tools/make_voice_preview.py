"""Package an opt-in VoiceStudio preview without replacing the stable 1.2.0 release."""
from pathlib import Path
import hashlib
import json
import shutil
import tempfile
from make_release import ROOT,RELEASE,zip_tree

VERSION='1.2.1-VoicePreview'

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    built=ROOT/'dist/SiDesktopPet'
    if not (built/'Si-DesktopPet.exe').is_file():raise SystemExit('Build the preview executable first.')
    RELEASE.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=RELEASE,prefix='.voice-preview-') as directory:
        stage=Path(directory)
        if not stage.resolve().is_relative_to(ROOT.resolve()):raise RuntimeError('Unsafe package staging path')
        folder=stage/f'SiDesktopPet-{VERSION}-Windows-x64'
        shutil.copytree(built,folder,ignore=shutil.ignore_patterns('data','__pycache__'))
        for name in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md'):
            shutil.copy2(ROOT/name,folder/name)
        shutil.copytree(ROOT/'docs',folder/'docs')
        shutil.copytree(ROOT/'licenses',folder/'licenses')
        (folder/'data').mkdir()
        (folder/'data/README.txt').write_text('便携存档位于此处；升级时先退出旧程序，再复制旧版 data 目录。',encoding='utf-8')
        (folder/'VOICE_PREVIEW.txt').write_text(
            '祀桌宠 1.2.1 语音预览\n\n'
            '语音默认关闭。请先在“行为与显示”里点击“连接本机 VoiceStudio 引擎”，再启用语音并试听。\n'
            '本机 VoiceStudio 运行环境与声线需已存在；如果未连接，文字互动仍正常。\n'
            '这是 1.3 形象与动画重制前的语音功能预览，仍使用旧图集。\n'
            '请勿覆盖旧版文件夹；需继承存档时，退出旧程序后复制旧 data 目录。\n',encoding='utf-8')
        portable=RELEASE/f'{folder.name}.zip';zip_tree(folder,portable)
    source=RELEASE/f'SiDesktopPet-{VERSION}-Source.zip'
    zip_tree(ROOT,source,{'.venv','.tools','build','dist','release','data','__pycache__','.git','tmp','portable-check','qa'},f'SiDesktopPet-{VERSION}-Source')
    report={p.name:{'bytes':p.stat().st_size,'sha256':digest(p)} for p in (portable,source)}
    (RELEASE/'SHA256SUMS-voice-preview.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
