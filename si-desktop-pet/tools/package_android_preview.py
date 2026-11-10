"""Package an APK and its corresponding Android source, excluding private signing material."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import zipfile
import sys

ROOT=Path(__file__).resolve().parents[1]
ANDROID=ROOT/'mobile/android'
RELEASE=ROOT/'release'
NAME='SiCompanion-Android-0.1.0-preview'
JDK=Path(r'E:\create\focusguard\tools\jdk\jdk-17.0.20.1+1')
SDK=Path(r'E:\create\focusguard\tools\build-tools-35.0.0\android-15')
sys.path.insert(0,str(ROOT/'src'))
from si_pet.companion import LINES

def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def main():
    apk=ANDROID/'dist'/f'{NAME}.apk'
    target=RELEASE/apk.name
    source=RELEASE/'SiCompanion-Android-0.1.0-Source.zip'
    if target.exists() or source.exists():
        raise SystemExit('Published preview already exists; use a new version.')
    signed=subprocess.run([str(JDK/'bin/java.exe'),'-jar',str(SDK/'lib/apksigner.jar'),
                           'verify','--verbose',str(apk)],capture_output=True,check=True,
                          encoding='utf-8',errors='replace').stdout
    if 'v2): true' not in signed or 'v3): true' not in signed:
        raise SystemExit('Required APK signatures were not verified')
    with zipfile.ZipFile(apk) as archive:
        if archive.testzip():
            raise SystemExit('APK ZIP integrity failed')
        required={'classes.dex','AndroidManifest.xml','resources.arsc','assets/animations.json','assets/lines.json'}
        if not required.issubset(archive.namelist()):
            raise SystemExit('APK content missing')
        frames=json.loads(archive.read('assets/animations.json'))
        if json.loads(archive.read('assets/lines.json'))!=LINES:
            raise SystemExit('Mobile administrator dialogue differs from shared source')
        count=0
        for spec in frames.values():
            for name in spec['files']:
                if archive.read('assets/'+name)!=(ROOT/'assets'/name).read_bytes():
                    raise SystemExit('Mobile frame differs from project original')
                count+=1
        if count!=14:
            raise SystemExit('Unexpected mobile frame count')
    RELEASE.mkdir(exist_ok=True)
    before={p.name:digest(p) for p in RELEASE.glob('SiDesktopPet-*.zip')}
    shutil.copy2(apk,target)
    prefix=source.stem
    with zipfile.ZipFile(source,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(ANDROID.rglob('*')):
            relative=path.relative_to(ANDROID)
            if path.is_file() and not any(part in {'build','dist','__pycache__'} for part in relative.parts) and path.name!='json-jvm-tests.jar':
                archive.write(path,f'{prefix}/mobile/android/{relative.as_posix()}')
        for name in ('__init__.py','sync_v1.py','sync_transport.py'):
            archive.write(ROOT/'src/si_pet'/name,f'{prefix}/src/si_pet/{name}')
        for name in ('LICENSE','THIRD_PARTY_NOTICES.md','requirements.txt'):
            archive.write(ROOT/name,f'{prefix}/{name}')
        for path in sorted((ROOT/'licenses').rglob('*')):
            if path.is_file():
                archive.write(path,f'{prefix}/{path.relative_to(ROOT).as_posix()}')
        archive.writestr(f'{prefix}/README.txt',
            'Android 对应源码位于 mobile/android；已含原始角色帧与台词资产。\n'
            '使用 JDK17、Android SDK35 构建，路径参数见 mobile/android/build-apk.ps1。\n'
            'src/si_pet 只包含供 JVM/PC 协议互测的 Windows 协议文件，不是完整 Windows 应用。\n'
            '测试依赖 JSON-java jar 不随包分发，下载信息见 mobile/android/tools/TEST_DEPENDENCY.md。\n'
            '本地预览签名密钥不在源码包内；构建时可指定自己的 SigningKey。\n')
    with zipfile.ZipFile(source) as archive:
        if archive.testzip():
            raise SystemExit('Source archive integrity failed')
    if before!={p.name:digest(p) for p in RELEASE.glob('SiDesktopPet-*.zip')}:
        raise SystemExit('An existing Windows archive changed')
    protocol=json.loads((ROOT/'qa/android-protocol-checks.json').read_text(encoding='utf-8'))
    if not protocol.get('ok'):
        raise SystemExit('Run the cross-language protocol checks first')
    report={'version':'0.1.0-preview','package':'cn.si.companion','min_sdk':26,'target_sdk':35,
            'apk_v2_v3_signatures_verified':True,'zip_integrity':True,'original_frames_verified':count,
            'java_python_protocol_check':protocol,'android_device_tested':False,
            'existing_windows_archives_unchanged':True,
            'files':{p.name:{'bytes':p.stat().st_size,'sha256':digest(p)} for p in (target,source)}}
    (RELEASE/'VERIFIED-Android-0.1.0.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
