"""Archive a source-only native project; never label it an installable Harmony build."""
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'mobile/harmony'
RELEASE=ROOT/'release'
sys.path.insert(0,str(ROOT/'src'))
from si_pet.companion import LINES

def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    output=RELEASE/'SiCompanion-Harmony-0.1.0-ProjectPreview.zip'
    if output.exists():
        raise SystemExit('Project preview already published; choose a new version.')
    core=json.loads((ROOT/'qa/harmony-core-checks.json').read_text(encoding='utf-8'))
    api=json.loads((ROOT/'qa/harmony-public-api-check.json').read_text(encoding='utf-8'))
    if not core.get('ok') or not api.get('ok'):
        raise SystemExit('Run the source/core checks first.')
    for config in PROJECT.rglob('*.json5'):
        if 'build' not in config.relative_to(PROJECT).parts:
            json.loads(config.read_text(encoding='utf-8'))
    raw=PROJECT/'entry/src/main/resources/rawfile'
    manifest=json.loads((raw/'animations.json').read_text(encoding='utf-8'))
    count=0
    for spec in manifest.values():
        for name in spec['files']:
            if digest(raw/name)!=digest(ROOT/'assets'/name):
                raise SystemExit('Unexpected modified frame')
            count+=1
    if json.loads((raw/'lines.json').read_text(encoding='utf-8'))!=LINES:
        raise SystemExit('Shared role dialogue differs')
    page=(PROJECT/'entry/src/main/ets/pages/Index.ets').read_text(encoding='utf-8')
    for name in re.findall(r"\$rawfile\('([^']+)'\)",page):
        if not (raw/name).is_file():
            raise SystemExit(f'Missing UI asset: {name}')
    previous={p.name:digest(p) for p in RELEASE.iterdir() if p.is_file() and p.suffix in {'.zip','.apk'}}
    report={'kind':'native-source-project-preview','version':'0.1.0','core_checks':core,
            'public_api_signature_checks':api,'original_frames':count,'shared_dialogue_checked':True,
            'harmony_sdk_build':False,'arkui_page_compiled':False,'installable_hap_created':False,
            'harmony_tablet_tested':False}
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        prefix=output.stem
        for file in sorted(PROJECT.rglob('*')):
            relative=file.relative_to(PROJECT)
            if file.is_file() and not any(part in {'build','oh_modules','node_modules','.hvigor','__pycache__'} for part in relative.parts):
                archive.write(file,f'{prefix}/{relative.as_posix()}')
        for name in ('LICENSE','THIRD_PARTY_NOTICES.md'):
            archive.write(ROOT/name,f'{prefix}/{name}')
        archive.writestr(f'{prefix}/SOURCE_CHECKS.json',json.dumps(report,ensure_ascii=False,indent=2))
        archive.writestr(f'{prefix}/BUILD_STATUS.txt',
            '这是原生 ArkUI/ArkTS 工程源码预览，不是安装包。\n'
            '尚未取得本机 HarmonyOS SDK，未执行 ArkTS/UI 构建，没有 HAP。\n'
            '已验证共享协议核心及公开 API 类型签名，详细边界见 README.md 和 SOURCE_CHECKS.json。\n')
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise SystemExit('Project archive integrity failed')
        if any(name.endswith(('.hap','.p12','.jks','.p7b')) for name in archive.namelist()):
            raise SystemExit('Unexpected binary or signing material in source-only archive')
    if previous!={p.name:digest(p) for p in RELEASE.iterdir() if p.is_file() and p.suffix in {'.zip','.apk'} and p!=output}:
        raise SystemExit('An earlier release changed')
    report['archive']={'name':output.name,'bytes':output.stat().st_size,'sha256':digest(output)}
    (RELEASE/'VERIFIED-Harmony-0.1.0-ProjectPreview.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
