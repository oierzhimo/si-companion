"""Verify the built Android 0.1.1-preview APK, then publish it with source and a report."""
from pathlib import Path
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
ANDROID=ROOT/'mobile/android'
RELEASE=ROOT/'release'
NAME='SiCompanion-Android-0.1.1-preview'
SOURCE_NAME='SiCompanion-Android-0.1.1-Source'
VERSION='0.1.1-preview'
VERSION_CODE='1002'
PACKAGE='cn.si.companion'
MIN_SDK='26'
TARGET_SDK='35'
JDK=Path(r'E:\create\focusguard\tools\jdk\jdk-17.0.20.1+1')
SDK=Path(r'E:\create\focusguard\tools\build-tools-35.0.0\android-15')
OLD_APK=RELEASE/'SiCompanion-Android-0.1.0-preview.apk'
OLD_FILES=(OLD_APK,RELEASE/'SiCompanion-Android-0.1.0-Source.zip',
           RELEASE/'VERIFIED-Android-0.1.0.json',
           ANDROID/'dist/SiCompanion-Android-0.1.0-preview.apk',
           ANDROID/'dist/SiCompanion-Android-0.1.0-preview.apk.idsig')
EXCLUDED_DIRS={'build','dist','__pycache__','qa','video','videos','model','models',
               'signing','keystores'}
EXCLUDED_SUFFIXES={'.jks','.keystore','.bks','.p12','.pfx','.pem','.key','.der','.crt','.cer',
                   '.idsig','.apk','.aab','.mp4','.mov','.webm','.avi','.mkv',
                   '.glb','.gltf','.obj','.fbx','.onnx','.pt','.pth','.safetensors','.gguf'}
SECRET_NAMES={'keystore','keystore.properties','signing.properties','signing.gradle'}
sys.path.insert(0,str(ROOT/'src'))
from si_pet.companion import LINES


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def run(command):
    result=subprocess.run([str(part) for part in command],capture_output=True,
                          encoding='utf-8',errors='replace')
    if result.returncode:
        raise SystemExit(f'Command failed: {command[0]}\n{result.stdout}\n{result.stderr}')
    return result.stdout+result.stderr


def badging(apk):
    text=run([SDK/'aapt2.exe','dump','badging',apk])
    package=re.search(r"package: name='([^']*)' versionCode='([^']*)' versionName='([^']*)'",text)
    sdk=re.search(r"^[ \t]*(?:minSdkVersion|sdkVersion):'(\d+)'",text,re.M)
    target=re.search(r"targetSdkVersion:'(\d+)'",text)
    if not (package and sdk and target):
        raise SystemExit('Could not read APK badging fields')
    found=(package.group(1),package.group(2),package.group(3),sdk.group(1),target.group(1))
    expected=(PACKAGE,VERSION_CODE,VERSION,MIN_SDK,TARGET_SDK)
    if found!=expected:
        raise SystemExit(f'APK badging mismatch: {found} != {expected}')
    return found


def certificates(apk):
    text=run([JDK/'bin/java.exe','-jar',SDK/'lib/apksigner.jar',
              'verify','--verbose','--print-certs',apk])
    for scheme in ('v2','v3'):
        match=re.search(rf'Verified using {scheme} scheme[^:]*:\s*(true|false)',text,re.I)
        if not match or match.group(1).lower()!='true':
            raise SystemExit(f'APK {scheme} signature was not verified')
    found=re.findall(r'Signer #\d+ certificate SHA-256 digest:\s*([0-9a-fA-F:]+)',text)
    if not found:
        raise SystemExit('Could not read APK signing certificate')
    return sorted(item.replace(':','').lower() for item in found)


def read_apk(apk):
    with zipfile.ZipFile(apk) as archive:
        if archive.testzip():
            raise SystemExit('APK ZIP integrity failed')
        required={'classes.dex','AndroidManifest.xml','resources.arsc',
                  'assets/animations.json','assets/lines.json'}
        if not required.issubset(archive.namelist()):
            raise SystemExit('APK content missing')
        lines=json.loads(archive.read('assets/lines.json'))
        if lines!=LINES:
            raise SystemExit('APK dialogue differs from current Windows LINES')
        if len(lines)!=11 or any(len(items)!=6 for items in lines.values()):
            raise SystemExit('APK dialogue is not 11 categories of 6 lines')
        total=sum(len(items) for items in lines.values())
        if total!=66:
            raise SystemExit('APK dialogue total is not 66 lines')
        frames=json.loads(archive.read('assets/animations.json'))
        count=0
        for spec in frames.values():
            for name in spec['files']:
                if archive.read('assets/'+name)!=(ROOT/'assets'/name).read_bytes():
                    raise SystemExit('APK frame differs from the existing original frame')
                count+=1
        if count!=14:
            raise SystemExit('Unexpected APK frame count')
    return total,count


def include_source(path):
    relative=path.relative_to(ANDROID)
    if any(part.lower() in EXCLUDED_DIRS for part in relative.parts):
        return False
    if path.name.lower() in SECRET_NAMES or path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return path.name!='json-jvm-tests.jar'


def build_source():
    prefix=SOURCE_NAME
    entries={}
    for path in sorted(ANDROID.rglob('*')):
        if path.is_file() and include_source(path):
            arcname=f'{prefix}/mobile/android/{path.relative_to(ANDROID).as_posix()}'
            entries[arcname]=path.read_bytes()
    for name in ('__init__.py','sync_v1.py','sync_transport.py'):
        entries[f'{prefix}/src/si_pet/{name}']=(ROOT/'src/si_pet'/name).read_bytes()
    for name in ('LICENSE','THIRD_PARTY_NOTICES.md','requirements.txt'):
        entries[f'{prefix}/{name}']=(ROOT/name).read_bytes()
    for path in sorted((ROOT/'licenses').rglob('*')):
        if path.is_file():
            entries[f'{prefix}/{path.relative_to(ROOT).as_posix()}']=path.read_bytes()
    readme=('Android 0.1.1-preview（versionCode 1002）对应源码位于 mobile/android。\n'
            '已含与 Windows 相同的 11 类各 6 句、共 66 句台词，以及 14 帧现有原始角色素材；未新增立绘。\n'
            '使用 JDK 17、Android SDK 35 构建，路径参数见 mobile/android/build-apk.ps1；'
            '构建目录按版本分开，默认输出 dist/SiCompanion-Android-0.1.1-preview.apk。\n'
            'src/si_pet 只包含供 JVM/PC 协议互测的 Windows 协议文件，不是完整 Windows 应用。\n'
            '测试依赖 JSON-java jar 不随包分发，下载信息见 mobile/android/tools/TEST_DEPENDENCY.md。\n'
            '本地预览签名密钥不在源码包内；构建时必须沿用现有 SigningKey，升级安装需同包名同证书。\n'
            '本源码包不代表已在 Android 真机上验证；APK 需独立构建并验签。\n')
    entries[f'{prefix}/README.txt']=readme.encode('utf-8')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for arcname,data in entries.items():
            archive.writestr(arcname,data)
    data=buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip():
            raise SystemExit('Source archive integrity failed')
        if set(archive.namelist())!=set(entries):
            raise SystemExit('Source archive entries differ from the selected files')
        for arcname,expected in entries.items():
            if archive.read(arcname)!=expected:
                raise SystemExit(f'Source archive content changed: {arcname}')
    return data,len(entries)


def main():
    apk=ANDROID/'dist'/f'{NAME}.apk'
    target_apk=RELEASE/f'{NAME}.apk'
    target_source=RELEASE/f'{SOURCE_NAME}.zip'
    target_report=RELEASE/'VERIFIED-Android-0.1.1.json'
    targets=(target_apk,target_source,target_report)
    if any(path.exists() for path in targets):
        raise SystemExit('Android 0.1.1-preview publication already exists; use a new version.')
    if not apk.is_file():
        raise SystemExit(f'Build the APK first: {apk}')
    if not OLD_APK.is_file():
        raise SystemExit(f'Missing 0.1.0 release APK used for certificate comparison: {OLD_APK}')
    protocol_path=ROOT/'qa/android-protocol-checks.json'
    protocol=json.loads(protocol_path.read_text(encoding='utf-8'))
    if protocol.get('ok') is not True:
        raise SystemExit('Run the cross-language protocol checks first; qa/android-protocol-checks.json is not ok')
    if protocol.get('android_device_tested') is not False:
        raise SystemExit('Protocol report must not claim Android device testing')
    old_hashes={str(path.relative_to(ROOT)):digest(path) for path in OLD_FILES if path.is_file()}

    badging(apk)
    certs=certificates(apk)
    old_certs=certificates(OLD_APK)
    if certs!=old_certs:
        raise SystemExit('APK signing certificate SHA-256 differs from Android 0.1.0-preview')
    total,count=read_apk(apk)
    apk_bytes=apk.read_bytes()
    source_bytes,source_entries=build_source()

    if any(path.exists() for path in targets):
        raise SystemExit('Publication target appeared while validating; refusing to overwrite.')
    if old_hashes!={str(path.relative_to(ROOT)):digest(path) for path in OLD_FILES if path.is_file()}:
        raise SystemExit('An existing Android 0.1.0 file changed during validation')
    RELEASE.mkdir(exist_ok=True)
    target_apk.write_bytes(apk_bytes)
    target_source.write_bytes(source_bytes)
    if old_hashes!={str(path.relative_to(ROOT)):digest(path) for path in OLD_FILES if path.is_file()}:
        raise SystemExit('An existing Android 0.1.0 file changed')
    report={
        'version':VERSION,
        'version_code':int(VERSION_CODE),
        'package':PACKAGE,
        'min_sdk':int(MIN_SDK),
        'target_sdk':int(TARGET_SDK),
        'apk_v2_v3_signatures_verified':True,
        'signing_certificate_sha256':certs[0],
        'previous_signing_certificate_sha256':old_certs[0],
        'signing_certificate_matches_0_1_0':True,
        'zip_integrity':True,
        'dialogue_lines':{'categories':11,'per_category':6,'total':total},
        'dialogue_matches_current_windows_lines':True,
        'original_frames_verified':count,
        'runtime_art':'legacy',
        'apk_installed_on_device':False,
        'android_device_tested':False,
        'protocol_check_scope':'JVM/PC only; this report does not represent an Android device test',
        'java_python_protocol_check_source':'qa/android-protocol-checks.json',
        'java_python_protocol_check':protocol,
        'source_archive_verified':True,
        'source_archive_entries':source_entries,
        'existing_android_0_1_0_files_unchanged':True,
        'existing_android_0_1_0_files_sha256':old_hashes,
        'files':{
            target_apk.name:{'bytes':len(apk_bytes),'sha256':hashlib.sha256(apk_bytes).hexdigest()},
            target_source.name:{'bytes':len(source_bytes),'sha256':hashlib.sha256(source_bytes).hexdigest()},
        },
    }
    target_report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
