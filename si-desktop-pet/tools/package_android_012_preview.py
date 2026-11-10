"""Verify the built Android 0.1.2-preview APK, then publish it with source and a report.

The 0.1.0 and 0.1.1 APKs stored character frames under entry names containing
literal backslashes. Python's ZipInfo.filename normalizes backslashes on
Windows, which hid the defect from earlier package checks, while Android's
AssetManager did not find the frames. This script inspects
ZipInfo.orig_filename directly on both the APK and the source ZIP.

Publication also requires qa/android-012-device-review.json: the report records
only a partial physical-device verification, using the review's schema,
version, package, pulled-APK hash and per-check results. It does not claim
native Android TLS, a physical multi-PC LAN or HarmonyOS acceptance.
"""
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
NAME='SiCompanion-Android-0.1.2-preview'
SOURCE_NAME='SiCompanion-Android-0.1.2-Source'
VERSION='0.1.2-preview'
VERSION_CODE='1006'
PACKAGE='cn.si.companion'
MIN_SDK='26'
TARGET_SDK='35'
JDK=Path(r'E:\create\focusguard\tools\jdk\jdk-17.0.20.1+1')
SDK=Path(r'E:\create\focusguard\tools\build-tools-35.0.0\android-15')
OLD_APK=RELEASE/'SiCompanion-Android-0.1.0-preview.apk'
PREVIOUS_APK=RELEASE/'SiCompanion-Android-0.1.1-preview.apk'
OLD_FILES=(OLD_APK,RELEASE/'SiCompanion-Android-0.1.0-Source.zip',
           RELEASE/'VERIFIED-Android-0.1.0.json',
           ANDROID/'dist/SiCompanion-Android-0.1.0-preview.apk',
           ANDROID/'dist/SiCompanion-Android-0.1.0-preview.apk.idsig')
PREVIOUS_FILES=(PREVIOUS_APK,RELEASE/'SiCompanion-Android-0.1.1-Source.zip',
                RELEASE/'VERIFIED-Android-0.1.1.json',
                ANDROID/'dist/SiCompanion-Android-0.1.1-preview.apk',
                ANDROID/'dist/SiCompanion-Android-0.1.1-preview.apk.idsig',
                ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic.apk',
                ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic.apk.idsig',
                ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic2.apk',
                ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic2.apk.idsig',
                ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic3.apk',
                ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic3.apk.idsig')
EXCLUDED_DIRS={'build','dist','__pycache__','qa','video','videos','model','models',
               'signing','keystores'}
EXCLUDED_SUFFIXES={'.jks','.keystore','.bks','.p12','.pfx','.pem','.key','.der','.crt','.cer',
                   '.idsig','.apk','.aab','.mp4','.mov','.webm','.avi','.mkv',
                   '.glb','.gltf','.obj','.fbx','.onnx','.pt','.pth','.safetensors','.gguf'}
SECRET_NAMES={'keystore','keystore.properties','signing.properties','signing.gradle'}
DEVICE_REVIEW=ROOT/'qa/android-012-device-review.json'
DEVICE_REVIEW_SCHEMA='si-android-device-review/v1'
DEVICE_REVIEW_CHECKS=('cold_launch','character_visible_in_main_activity',
                      'asset_failure_description_absent',
                      'pat_response_and_counter_increment',
                      'interaction_totals_preserved_after_upgrade',
                      'interaction_totals_persist_after_force_stop_and_relaunch',
                      'overlay_foreground_service_start','overlay_window_created',
                      'overlay_drag_and_edge_snap',
                      'overlay_stop_service_and_window_removed',
                      'existing_quiet_preference_preserved')
DEVICE_OBSERVATION_FIELDS=('before_test','after_one_pat','after_relaunch',
                          'overlay_before_drag','overlay_after_drag_and_snap',
                          'overlay_stopped_after_test','quiet_mode',
                          'app_data_cleared','app_uninstalled')
PRIVATE_MARKERS=('serial','imei','meid','android_id','advertising_id','token',
                 'secret','password','private_key','keystore','signing_key')
PATH_FIX_TOOL=f'{SOURCE_NAME}/mobile/android/tools/NormalizeApkPaths.java'
PATH_FIX_EXPLANATION=(
    'Android 0.1.0 and 0.1.1 stored the 14 character frames under entry names '
    'containing literal backslashes (for example assets/hd\\idle\\00.png); '
    'AssetManager.list("hd") was empty on a real device and the character area '
    'stayed blank. Python ZipInfo.filename silently converts backslashes to '
    'slashes on Windows, so earlier package checks did not see the defect. '
    'build-apk.ps1 now runs mobile/android/tools/NormalizeApkPaths.java with '
    'JDK 17 after the dex update and before zipalign, rewriting every entry '
    'name to POSIX separators; this report checks ZipInfo.orig_filename '
    'directly on the APK and on the source ZIP and rejects backslashes, '
    'absolute paths, relative segments and duplicate entry names.')
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


def private_identifiers(value,path=''):
    found=set()
    if isinstance(value,dict):
        for key,item in value.items():
            name=str(key)
            if any(marker in name.lower() for marker in PRIVATE_MARKERS):
                found.add(path+name)
            found.update(private_identifiers(item,path+name+'.'))
    elif isinstance(value,list):
        for index,item in enumerate(value):
            found.update(private_identifiers(item,f'{path}[{index}].'))
    return found


def load_device_review(apk_sha256):
    """Load the physical-device QA record and refuse private identifiers."""
    if not DEVICE_REVIEW.is_file():
        raise SystemExit(f'Missing Android device review: {DEVICE_REVIEW}')
    raw=DEVICE_REVIEW.read_bytes()
    review=json.loads(raw.decode('utf-8'))
    if not isinstance(review,dict):
        raise SystemExit('Android device review must be a JSON object')
    private=private_identifiers(review)
    if private:
        raise SystemExit(f'Android device review contains private identifiers: {sorted(private)}')
    expected={'schema':DEVICE_REVIEW_SCHEMA,'version':VERSION,
              'version_code':int(VERSION_CODE),'package':PACKAGE}
    for field,value in expected.items():
        if review.get(field)!=value:
            raise SystemExit(f'Android device review {field} mismatch: {review.get(field)!r} != {value!r}')
    for field in ('android_device_tested','apk_installed_on_device',
                  'installed_apk_pulled_and_hash_matches'):
        if review.get(field) is not True:
            raise SystemExit(f'Android device review requires {field}=true')
    recorded=review.get('apk_sha256')
    if not isinstance(recorded,str) or recorded.lower()!=apk_sha256:
        raise SystemExit('Android device review apk_sha256 does not match the APK being published')
    checks=review.get('checks')
    if not isinstance(checks,dict):
        raise SystemExit('Android device review is missing checks')
    failed=[name for name in DEVICE_REVIEW_CHECKS if checks.get(name) is not True]
    if failed:
        raise SystemExit(f'Android device review checks are not all true: {failed}')
    scope=review.get('verification_scope')
    if not isinstance(scope,str) or not scope.strip():
        raise SystemExit('Android device review is missing verification_scope')
    not_verified=review.get('not_verified')
    if (not isinstance(not_verified,list) or not not_verified
            or not all(isinstance(item,str) and item.strip() for item in not_verified)):
        raise SystemExit('Android device review is missing a non-empty not_verified list')
    observations=review.get('observations')
    if not isinstance(observations,dict):
        raise SystemExit('Android device review is missing observations')
    evidence=review.get('evidence',[])
    if not isinstance(evidence,list) or not all(isinstance(item,str) for item in evidence):
        raise SystemExit('Android device review evidence must be a list of paths')
    return review,hashlib.sha256(raw).hexdigest()


def device_review_facts(review):
    """Copy only the reviewed, non-private device facts into the report."""
    observations=review['observations']
    checks=review['checks']
    return {
        'schema':review['schema'],
        'date':review.get('date'),
        'version':review['version'],
        'version_code':review['version_code'],
        'package':review['package'],
        'device_model':review.get('device_model'),
        'android_release':review.get('android_release'),
        'apk_sha256':review['apk_sha256'].lower(),
        'installed_apk_pulled_and_hash_matches':True,
        'apk_installed_on_device':True,
        'android_device_tested':True,
        'verification_scope':review['verification_scope'],
        'checks':{name:checks[name] for name in DEVICE_REVIEW_CHECKS},
        'observations':{name:observations[name] for name in DEVICE_OBSERVATION_FIELDS
                        if name in observations},
        'not_verified':list(review['not_verified']),
        'evidence':list(review.get('evidence',[])),
        'runtime_art':review.get('runtime_art'),
        'root_cause':review.get('root_cause'),
    }


def raw_entry_names(archive,label):
    """Read ZipInfo.orig_filename, which is not normalized by the host OS."""
    names=[]
    seen=set()
    for info in archive.infolist():
        raw=info.orig_filename
        if '\\' in raw:
            raise SystemExit(f'{label} entry uses a backslash separator: {raw!r}')
        if raw.startswith('/') or (len(raw)>=2 and raw[0].isalpha() and raw[1]==':'):
            raise SystemExit(f'{label} entry is an absolute path: {raw!r}')
        if '\x00' in raw:
            raise SystemExit(f'{label} entry name contains NUL: {raw!r}')
        body=raw[:-1] if raw.endswith('/') else raw
        if not body:
            raise SystemExit(f'{label} entry name has no file component: {raw!r}')
        if any(segment in ('','.','..') for segment in body.split('/')):
            raise SystemExit(f'{label} entry name is not a canonical relative path: {raw!r}')
        if raw in seen:
            raise SystemExit(f'{label} contains a duplicate entry name: {raw!r}')
        seen.add(raw)
        names.append(raw)
    if not names:
        raise SystemExit(f'{label} contains no entries')
    return names


def apk_raw_entry_names(apk):
    with zipfile.ZipFile(apk) as archive:
        return raw_entry_names(archive,'APK')


def read_apk(apk,raw_names):
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
                entry='assets/'+name
                if entry not in raw_names:
                    raise SystemExit(f'APK frame entry name is not the expected POSIX path: {entry}')
                if archive.read(entry)!=(ROOT/'assets'/name).read_bytes():
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
    if PATH_FIX_TOOL not in entries:
        raise SystemExit('Source archive is missing the APK path normalization tool')
    readme=('Android 0.1.2-preview（versionCode 1006）对应源码位于 mobile/android。\n'
            '已含与 Windows 相同的 11 类各 6 句、共 66 句台词，以及 14 帧现有原始角色素材；未新增立绘。\n'
            '本版修复 APK 资源路径：build-apk.ps1 在更新 classes.dex 之后、zipalign 之前，'
            '以 JDK 17 单文件源码方式运行 tools/NormalizeApkPaths.java，'
            '把含字面反斜杠的 entry 名改为 POSIX 斜杠，并保留全部解压后内容。'
            '0.1.0/0.1.1 的同类路径问题曾导致真机角色空白；本版已在 V2564A / Android 16 上完成部分真机验证，不代表完整验收。\n'
            '使用 JDK 17、Android SDK 35 构建，路径参数见 mobile/android/build-apk.ps1；'
            '构建目录按版本分开，默认输出 dist/SiCompanion-Android-0.1.2-preview.apk。\n'
            'src/si_pet 只包含供 JVM/PC 协议互测的 Windows 协议文件，不是完整 Windows 应用。\n'
            '测试依赖 JSON-java jar 不随包分发，下载信息见 mobile/android/tools/TEST_DEPENDENCY.md。\n'
            '本地预览签名密钥不在源码包内；构建时必须沿用现有 SigningKey，升级安装需同包名同证书。\n'
            '本源码包不代表完整真机验收；native Android TLS、物理多 PC LAN 与 HarmonyOS 仍未验收。\n')
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
        raw_entry_names(archive,'Source ZIP')
    return data,len(entries)


def main():
    apk=ANDROID/'dist'/f'{NAME}.apk'
    target_apk=RELEASE/f'{NAME}.apk'
    target_source=RELEASE/f'{SOURCE_NAME}.zip'
    target_report=RELEASE/'VERIFIED-Android-0.1.2.json'
    targets=(target_apk,target_source,target_report)
    if any(path.exists() for path in targets):
        raise SystemExit('Android 0.1.2-preview publication already exists; use a new version.')
    if not apk.is_file():
        raise SystemExit(f'Build the APK first: {apk}')
    if not OLD_APK.is_file():
        raise SystemExit(f'Missing 0.1.0 release APK used for certificate comparison: {OLD_APK}')
    if not PREVIOUS_APK.is_file():
        raise SystemExit(f'Missing 0.1.1 release APK used for certificate comparison: {PREVIOUS_APK}')
    protocol_path=ROOT/'qa/android-protocol-checks.json'
    protocol=json.loads(protocol_path.read_text(encoding='utf-8'))
    if protocol.get('ok') is not True:
        raise SystemExit('Run the cross-language protocol checks first; qa/android-protocol-checks.json is not ok')
    if protocol.get('android_device_tested') is not False:
        raise SystemExit('Protocol report must not claim Android device testing')
    old_hashes={str(path.relative_to(ROOT)):digest(path) for path in OLD_FILES if path.is_file()}
    previous_hashes={str(path.relative_to(ROOT)):digest(path) for path in PREVIOUS_FILES if path.is_file()}

    badging(apk)
    certs=certificates(apk)
    old_certs=certificates(OLD_APK)
    previous_certs=certificates(PREVIOUS_APK)
    if certs!=old_certs:
        raise SystemExit('APK signing certificate SHA-256 differs from Android 0.1.0-preview')
    if certs!=previous_certs:
        raise SystemExit('APK signing certificate SHA-256 differs from Android 0.1.1-preview')
    raw_names=apk_raw_entry_names(apk)
    total,count=read_apk(apk,raw_names)
    apk_bytes=apk.read_bytes()
    apk_sha256=hashlib.sha256(apk_bytes).hexdigest()
    review,review_sha256=load_device_review(apk_sha256)
    device_facts=device_review_facts(review)
    source_bytes,source_entries=build_source()

    if any(path.exists() for path in targets):
        raise SystemExit('Publication target appeared while validating; refusing to overwrite.')
    if old_hashes!={str(path.relative_to(ROOT)):digest(path) for path in OLD_FILES if path.is_file()}:
        raise SystemExit('An existing Android 0.1.0 file changed during validation')
    if previous_hashes!={str(path.relative_to(ROOT)):digest(path) for path in PREVIOUS_FILES if path.is_file()}:
        raise SystemExit('An existing Android 0.1.1 or diagnostic file changed during validation')
    if digest(DEVICE_REVIEW)!=review_sha256:
        raise SystemExit('The Android device review changed during validation')
    RELEASE.mkdir(exist_ok=True)
    target_apk.write_bytes(apk_bytes)
    target_source.write_bytes(source_bytes)
    if old_hashes!={str(path.relative_to(ROOT)):digest(path) for path in OLD_FILES if path.is_file()}:
        raise SystemExit('An existing Android 0.1.0 file changed')
    if previous_hashes!={str(path.relative_to(ROOT)):digest(path) for path in PREVIOUS_FILES if path.is_file()}:
        raise SystemExit('An existing Android 0.1.1 or diagnostic file changed')
    if digest(DEVICE_REVIEW)!=review_sha256:
        raise SystemExit('The Android device review changed')
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
        'previous_0_1_1_signing_certificate_sha256':previous_certs[0],
        'signing_certificate_matches_0_1_1':True,
        'zip_integrity':True,
        'dialogue_lines':{'categories':11,'per_category':6,'total':total},
        'dialogue_matches_current_windows_lines':True,
        'original_frames_verified':count,
        'apk_entry_path_normalization':{
            'tool':'mobile/android/tools/NormalizeApkPaths.java',
            'applied_before_zipalign':True,
            'raw_entry_paths_are_posix':True,
        },
        'raw_entry_paths_are_posix':True,
        'apk_raw_entry_paths_are_posix':True,
        'source_archive_raw_entry_paths_are_posix':True,
        'apk_raw_entry_count':len(raw_names),
        'path_normalization_fix':PATH_FIX_EXPLANATION,
        'source_includes_apk_path_normalizer':True,
        'runtime_art':'legacy',
        'apk_installed_on_device':True,
        'android_device_tested':True,
        'partial_android_device_verification':True,
        'device_verification_scope':review['verification_scope'],
        'device_verification_not_verified':list(review['not_verified']),
        'device_verification_source':'qa/android-012-device-review.json',
        'device_verification_source_sha256':review_sha256,
        'device_verification':device_facts,
        'native_android_tls_verified':False,
        'physical_multi_pc_lan_verified':False,
        'harmony_verified':False,
        'protocol_check_scope':('JVM/PC only; verifies the shared protocol and TLS code between '
                                'Java and Python, not native Android TLS or a complete device test'),
        'java_python_protocol_check_source':'qa/android-protocol-checks.json',
        'java_python_protocol_check':protocol,
        'source_archive_verified':True,
        'source_archive_entries':source_entries,
        'existing_android_0_1_0_files_unchanged':True,
        'existing_android_0_1_0_files_sha256':old_hashes,
        'existing_previous_android_files_unchanged':True,
        'existing_previous_android_files_sha256':previous_hashes,
        'files':{
            target_apk.name:{'bytes':len(apk_bytes),'sha256':apk_sha256},
            target_source.name:{'bytes':len(source_bytes),'sha256':hashlib.sha256(source_bytes).hexdigest()},
        },
    }
    target_report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
