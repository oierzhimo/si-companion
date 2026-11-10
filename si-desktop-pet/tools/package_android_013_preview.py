"""Verify the built Android 0.1.3-preview APK, then publish it with source and a report.

Android 0.1.3 adds an ACTION_SEND text/plain intake that only prefills the
pairing invitation after local validation; it never pairs or opens a network
connection by itself. Packaging reuses the checks proven for 0.1.2: aapt2
badging, v2/v3 signatures, the same signing certificate, raw
ZipInfo.orig_filename POSIX paths without duplicates, the 66 dialogue lines and
the 14 unchanged frames.

Publication also requires qa/android-013-device-review.json (schema
si-android-device-review/v2). That review covers one physical phone and one PC
over direct private IPv4, with native Android TLS verified. The report copies
only its non-private facts and keeps its not_verified list. It does not reuse
the 0.1.2 eleven-check UI/overlay acceptance as 0.1.3 evidence, and it does not
claim a second physical PC or HarmonyOS acceptance.
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
NAME='SiCompanion-Android-0.1.3-preview'
SOURCE_NAME='SiCompanion-Android-0.1.3-Source'
VERSION='0.1.3-preview'
VERSION_CODE='1007'
PACKAGE='cn.si.companion'
MIN_SDK='26'
TARGET_SDK='35'
JDK=Path(r'E:\create\focusguard\tools\jdk\jdk-17.0.20.1+1')
SDK=Path(r'E:\create\focusguard\tools\build-tools-35.0.0\android-15')
PREVIOUS_APK=RELEASE/'SiCompanion-Android-0.1.2-preview.apk'
PREVIOUS_FILES=(
    RELEASE/'SiCompanion-Android-0.1.0-preview.apk',
    RELEASE/'SiCompanion-Android-0.1.0-Source.zip',
    RELEASE/'VERIFIED-Android-0.1.0.json',
    RELEASE/'SiCompanion-Android-0.1.1-preview.apk',
    RELEASE/'SiCompanion-Android-0.1.1-Source.zip',
    RELEASE/'VERIFIED-Android-0.1.1.json',
    RELEASE/'SiCompanion-Android-0.1.2-preview.apk',
    RELEASE/'SiCompanion-Android-0.1.2-Source.zip',
    RELEASE/'VERIFIED-Android-0.1.2.json',
    ANDROID/'dist/SiCompanion-Android-0.1.0-preview.apk',
    ANDROID/'dist/SiCompanion-Android-0.1.0-preview.apk.idsig',
    ANDROID/'dist/SiCompanion-Android-0.1.1-preview.apk',
    ANDROID/'dist/SiCompanion-Android-0.1.1-preview.apk.idsig',
    ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic.apk',
    ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic.apk.idsig',
    ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic2.apk',
    ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic2.apk.idsig',
    ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic3.apk',
    ANDROID/'dist/SiCompanion-Android-0.1.2-diagnostic3.apk.idsig',
    ANDROID/'dist/SiCompanion-Android-0.1.2-preview.apk',
    ANDROID/'dist/SiCompanion-Android-0.1.2-preview.apk.idsig',
)
EXCLUDED_DIRS={'build','dist','__pycache__','qa','video','videos','model','models',
               'signing','keystores'}
EXCLUDED_SUFFIXES={'.jks','.keystore','.bks','.p12','.pfx','.pem','.key','.der','.crt','.cer',
                   '.idsig','.apk','.aab','.mp4','.mov','.webm','.avi','.mkv',
                   '.glb','.gltf','.obj','.fbx','.onnx','.pt','.pth','.safetensors','.gguf'}
EXCLUDED_NAMES={'keystore','keystore.properties','signing.properties','signing.gradle',
                'json-jvm-tests.jar','invitation.txt','ledger.json','peers.json'}
DEVICE_REVIEW=ROOT/'qa/android-013-device-review.json'
DEVICE_REVIEW_SCHEMA='si-android-device-review/v2'
DEVICE_REVIEW_CHECKS=('share_invitation_text_exact',
                      'share_does_not_automatically_pair_or_exchange',
                      'explicit_pair_from_native_android_ui',
                      'phone_events_received_on_pc',
                      'first_pc_journal_visible_on_phone',
                      'second_pc_journal_visible_on_phone',
                      'both_pc_journals_visible_once_each',
                      'repeat_exchange_imported_zero',
                      'repeat_event_count_and_export_digest_unchanged',
                      'cold_relaunch_preserved_peer_journals_and_counts',
                      'reconnect_after_cold_relaunch_imported_zero',
                      'native_wrong_fingerprint_rejected',
                      'wrong_fingerprint_did_not_replace_existing_peer',
                      'qa_phone_connection_removed_after_test',
                      'qa_server_and_port_closed',
                      'qa_peer_authorizations_revoked',
                      'automatic_sync_remained_off')
DEVICE_REVIEW_TRANSPORT={'direct_private_ipv4':True,
                         'adb_reverse_used':False,
                         'native_android_tls_verified':True,
                         'physical_pc_android_lan_verified':True,
                         'physical_multi_pc_lan_verified':False,
                         'harmony_verified':False}
DEVICE_OBSERVATION_FIELDS=('native_exchange_count',
                           'phone_events_first_imported_on_pc',
                           'server_events_after_first_exchange',
                           'server_events_after_second_pc_journal',
                           'pc_import_counts',
                           'interaction_counts_at_first_exchange',
                           'interaction_counts_after_sync_and_cleanup',
                           'test_journal_entries_retained_on_phone',
                           'remaining_test_peer_authorizations',
                           'phone_data_cleared',
                           'existing_production_pc_store_accessed')
PRIVATE_MARKERS=('serial','imei','meid','android_id','advertising_id','token',
                 'secret','password','private_key','keystore','signing_key')
EVIDENCE_MARKERS=('private','key','invitation','token','secret','serial','peers',
                  'certificate')
IPV4_LITERAL=re.compile(r'(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])')
PROTOCOL_FIELDS=('ok','java_python_canonical_json_matches','protocol_fixture_and_reload',
                 'actual_java_python_tls_roundtrip','android_device_tested')
PATH_FIX_TOOL=f'{SOURCE_NAME}/mobile/android/tools/NormalizeApkPaths.java'
SHARE_HANDLER=f'{SOURCE_NAME}/mobile/android/app/src/main/java/cn/si/companion/MainActivity.java'
MANIFEST=f'{SOURCE_NAME}/mobile/android/app/src/main/AndroidManifest.xml'
ANDROID_README=f'{SOURCE_NAME}/mobile/android/README.md'
BUILD_SCRIPT=f'{SOURCE_NAME}/mobile/android/build-apk.ps1'
SOURCE_README=f'{SOURCE_NAME}/README.txt'
PATH_FIX_EXPLANATION=(
    'Android 0.1.0 and 0.1.1 stored the 14 character frames under entry names '
    'containing literal backslashes (for example assets/hd\\idle\\00.png); '
    'AssetManager.list("hd") was empty on a real device and the character area '
    'stayed blank. Python ZipInfo.filename silently converts backslashes to '
    'slashes on Windows, so earlier package checks did not see the defect. '
    '0.1.2 fixed this by running mobile/android/tools/NormalizeApkPaths.java '
    'with JDK 17 after the dex update and before zipalign; 0.1.3 keeps that '
    'step. This report reads ZipInfo.orig_filename directly on the APK and on '
    'the source ZIP and rejects backslashes, absolute paths, relative segments '
    'and duplicate entry names.')
DEVICE_VERIFICATION_SUMMARY=(
    'One physical Android phone and one PC over direct private IPv4, using the '
    'installed APK and the production PC sync core. The native Android TLS '
    'pairing and exchange checks passed. This is not two-PC, HarmonyOS or '
    'complete multi-device acceptance.')
sys.path.insert(0,str(ROOT/'src'))
from si_pet.companion import LINES


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def snapshot(paths):
    return {path.relative_to(ROOT).as_posix():digest(path) for path in paths}


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
    """Load the 0.1.3 physical-device QA record and refuse private identifiers."""
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
    missing=[name for name in DEVICE_REVIEW_CHECKS if name not in checks]
    extra=sorted(set(checks)-set(DEVICE_REVIEW_CHECKS))
    if missing or extra:
        raise SystemExit(f'Android device review checks differ from the 17 expected checks: '
                         f'missing={missing} extra={extra}')
    failed=[name for name in DEVICE_REVIEW_CHECKS if checks[name] is not True]
    if failed:
        raise SystemExit(f'Android device review checks are not all true: {failed}')
    transport=review.get('transport')
    if not isinstance(transport,dict):
        raise SystemExit('Android device review is missing transport')
    for field,value in DEVICE_REVIEW_TRANSPORT.items():
        if transport.get(field) is not value:
            raise SystemExit(f'Android device review transport {field} mismatch: '
                             f'{transport.get(field)!r} != {value!r}')
    if review.get('runtime_art')!='legacy':
        raise SystemExit('Android device review must record runtime_art=legacy')
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
    missing_observations=[name for name in DEVICE_OBSERVATION_FIELDS if name not in observations]
    if missing_observations:
        raise SystemExit(f'Android device review is missing observations: {missing_observations}')
    evidence=review.get('evidence')
    if not isinstance(evidence,list) or not evidence:
        raise SystemExit('Android device review evidence must be a non-empty list of paths')
    evidence_files=[]
    for item in evidence:
        if not isinstance(item,str) or not item.strip():
            raise SystemExit('Android device review evidence must be a non-empty list of paths')
        relative=Path(item)
        lowered=item.lower()
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0]!='qa':
            raise SystemExit(f'Android device review evidence must be a relative qa/ path: {item!r}')
        if any(marker in lowered for marker in EVIDENCE_MARKERS):
            raise SystemExit(f'Android device review evidence names private material: {item!r}')
        full=ROOT/relative
        if not full.is_file():
            raise SystemExit(f'Android device review evidence is missing: {item}')
        evidence_files.append((item,full))
    return review,hashlib.sha256(raw).hexdigest(),evidence_files


def device_review_facts(review):
    """Copy only the reviewed, non-private device facts into the report."""
    observations=review['observations']
    checks=review['checks']
    transport=review['transport']
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
        'transport':{name:transport[name] for name in DEVICE_REVIEW_TRANSPORT},
        'checks':{name:checks[name] for name in DEVICE_REVIEW_CHECKS},
        'observations':{name:observations[name] for name in DEVICE_OBSERVATION_FIELDS},
        'not_verified':list(review['not_verified']),
        'evidence':list(review['evidence']),
        'runtime_art':review['runtime_art'],
        'initial_install_attempt':review.get('initial_install_attempt'),
        'prior_version_basic_ui_evidence':review.get('prior_version_basic_ui_evidence'),
    }


def assert_facts_public(value,path=''):
    """Refuse IP literals and private-key blocks in the copied facts."""
    if isinstance(value,dict):
        for key,item in value.items():
            assert_facts_public(item,f'{path}{key}.')
    elif isinstance(value,list):
        for index,item in enumerate(value):
            assert_facts_public(item,f'{path}[{index}].')
    elif isinstance(value,str):
        if IPV4_LITERAL.search(value):
            raise SystemExit(f'Device facts would expose an IP literal at {path}: {value!r}')
        if 'PRIVATE KEY' in value:
            raise SystemExit(f'Device facts would expose a private key at {path}')


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
    if path.name.lower() in EXCLUDED_NAMES or path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return True


def require_entry(entries,arcname,snippets):
    data=entries.get(arcname)
    if data is None:
        raise SystemExit(f'Source archive is missing {arcname}')
    for snippet in snippets:
        if snippet not in data:
            raise SystemExit(f'Source archive entry {arcname} is missing {snippet!r}')


def source_readme():
    return (
        'Android 0.1.3-preview（versionCode 1007）对应源码位于 mobile/android。\n'
        '已含与 Windows 相同的 11 类各 6 句、共 66 句台词，以及 14 帧现有原始角色素材；未新增或重绘立绘。\n'
        '本版新增接收系统分享的配对邀请：MainActivity 对 ACTION_SEND + text/plain 的 EXTRA_TEXT 先做\n'
        '长度（1..4096）、JSON/transport=1/邀请字段，以及 PinnedPeer 的局域网 IPv4、端口和证书指纹校验；\n'
        '通过后只用 setText 填入邀请并滚动到连接栏，不调用配对、不发起网络请求，也不打开其中的地址或链接。\n'
        '仍须用户自己确认电脑名称并主动点击“连接我的电脑”。AndroidManifest.xml 为同一 MainActivity\n'
        '增加 ACTION_SEND / CATEGORY_DEFAULT / text/plain intent-filter 并设为 singleTop；未增加 Receiver、\n'
        'URL scheme 或网络权限，原 LAUNCHER filter、exported=true 和现有权限保持不变。\n'
        'APK 资源路径修复沿用 0.1.2：build-apk.ps1 在更新 classes.dex 之后、zipalign 之前，以 JDK 17\n'
        '单文件源码方式运行 tools/NormalizeApkPaths.java，把 entry 名统一为 POSIX 斜杠。\n'
        '使用 JDK 17、Android SDK 35 构建，路径参数见 mobile/android/build-apk.ps1；默认输出\n'
        'dist/SiCompanion-Android-0.1.3-preview.apk。\n'
        '验证范围（2026-10-27，vivo V2564A / Android 16）：一台物理手机与一台 PC 经私网 IPv4 直连，\n'
        '未使用 adb reverse，完成 17 项真机/单 PC 检查且全部为 true：分享文本精确保真且不自动联网、\n'
        '明确点击后原生配对成功、4 次原生 Android TLS 交换、PC 导入计数 [44,0,0,0]、PC 端两条手记在\n'
        '手机各显示一次、重复交换导入 0 且摘要与事件数不变、强停重开后连接与两条手记及计数\n'
        '（亲密度 31 / 摸头 16 / 点心 5）保持、重连导入 0、错误 fingerprint 在 Android 原生握手被拒绝\n'
        '且未替换已有 peer。测试后已移除手机端连接、撤销电脑端授权、关闭服务与端口；autosync 始终 false，\n'
        '现有记录未清除。拉回的真机 base.apk SHA-256 与待发布 APK 一致：\n'
        'e9fcf1c50f1a33a7e1279f50d655f5ef89f4d987aa4e1f692614349552278d40。\n'
        '这不是完整的手机版或多端验收：只覆盖一台手机与一台 PC，未验证两台物理 PC 协同、\n'
        'HarmonyOS 5 平板、断网恢复与多设备冲突、后台长期耗电、每分钟自动同步生命周期、\n'
        '异常或超长分享输入、新实机立绘与完整动画；也未在 0.1.3 上重做 0.1.2 的界面截图与悬浮复测。\n'
        '发布脚本 tools/package_android_013_preview.py 写入 release/SiCompanion-Android-0.1.3-preview.apk、\n'
        'release/SiCompanion-Android-0.1.3-Source.zip 和 release/VERIFIED-Android-0.1.3.json，并默认拒绝覆盖。\n'
        'src/si_pet 只包含供 JVM/PC 协议互测的 Windows 协议文件，不是完整 Windows 应用。\n'
        '测试依赖 JSON-java jar 不随包分发，下载信息见 mobile/android/tools/TEST_DEPENDENCY.md。\n'
        '本地预览签名密钥不在源码包内；构建时必须沿用现有 SigningKey，升级安装需同包名同证书。\n')


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
    require_entry(entries,SHARE_HANDLER,(b'ACTION_SEND',b'EXTRA_TEXT',b'handleShare',
                                          b'setText(shared)'))
    require_entry(entries,MANIFEST,(b'android.intent.action.SEND',
                                    b'android:mimeType="text/plain"',
                                    b'android:launchMode="singleTop"'))
    require_entry(entries,PATH_FIX_TOOL,(b'class NormalizeApkPaths',
                                         b"replace('\\\\','/')"))
    require_entry(entries,BUILD_SCRIPT,(b'NormalizeApkPaths.java',))
    require_entry(entries,ANDROID_README,(b'0.1.3'))
    build_text=entries[BUILD_SCRIPT].decode('utf-8')
    normalizer=build_text.find('NormalizeApkPaths.java')
    aligner=build_text.find('zipalign.exe')
    if normalizer<0 or aligner<0 or normalizer>aligner:
        raise SystemExit('build-apk.ps1 does not run the path normalizer before zipalign')
    readme=source_readme()
    entries[SOURCE_README]=readme.encode('utf-8')
    for snippet in ('0.1.3-preview','1007','17 项','一台物理手机',
                    '不是完整的手机版或多端验收'):
        if snippet not in readme:
            raise SystemExit(f'Source README.txt is out of sync with the verified scope: {snippet!r}')
    for arcname in entries:
        parts=arcname.split('/')
        if any(part.lower() in EXCLUDED_DIRS for part in parts):
            raise SystemExit(f'Source archive contains an excluded directory: {arcname}')
        if parts[-1].lower() in EXCLUDED_NAMES or arcname.lower().endswith(tuple(EXCLUDED_SUFFIXES)):
            raise SystemExit(f'Source archive contains private or build material: {arcname}')
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
    target_report=RELEASE/'VERIFIED-Android-0.1.3.json'
    targets=(target_apk,target_source,target_report)
    if any(path.exists() for path in targets):
        raise SystemExit('Android 0.1.3-preview publication already exists; use a new version.')
    if not apk.is_file():
        raise SystemExit(f'Build the APK first: {apk}')
    if not PREVIOUS_APK.is_file():
        raise SystemExit(f'Missing 0.1.2 release APK used for certificate comparison: {PREVIOUS_APK}')
    missing=[path for path in PREVIOUS_FILES if not path.is_file()]
    if missing:
        raise SystemExit(f'Missing earlier Android release files: {missing}')
    protocol_path=ROOT/'qa/android-protocol-checks.json'
    protocol=json.loads(protocol_path.read_text(encoding='utf-8'))
    if protocol.get('ok') is not True:
        raise SystemExit('Run the cross-language protocol checks first; qa/android-protocol-checks.json is not ok')
    for field in ('java_python_canonical_json_matches','protocol_fixture_and_reload',
                  'actual_java_python_tls_roundtrip'):
        if protocol.get(field) is not True:
            raise SystemExit(f'Cross-language protocol check requires {field}=true')
    if protocol.get('android_device_tested') is not False:
        raise SystemExit('Protocol report must not claim Android device testing')
    protocol_facts={name:protocol.get(name) for name in PROTOCOL_FIELDS}
    previous_hashes=snapshot(PREVIOUS_FILES)

    badging(apk)
    certs=certificates(apk)
    previous_certs=certificates(PREVIOUS_APK)
    if certs!=previous_certs:
        raise SystemExit('APK signing certificate SHA-256 differs from Android 0.1.2-preview')
    raw_names=apk_raw_entry_names(apk)
    total,count=read_apk(apk,raw_names)
    apk_bytes=apk.read_bytes()
    apk_sha256=hashlib.sha256(apk_bytes).hexdigest()
    review,review_sha256,evidence_files=load_device_review(apk_sha256)
    device_facts=device_review_facts(review)
    assert_facts_public(device_facts)
    evidence_hashes={item:digest(full) for item,full in evidence_files}
    source_bytes,source_entries=build_source()

    if any(path.exists() for path in targets):
        raise SystemExit('Publication target appeared while validating; refusing to overwrite.')
    if snapshot(PREVIOUS_FILES)!=previous_hashes:
        raise SystemExit('An existing Android 0.1.2 or earlier release file changed during validation')
    if digest(DEVICE_REVIEW)!=review_sha256:
        raise SystemExit('The Android device review changed during validation')
    if {item:digest(full) for item,full in evidence_files}!=evidence_hashes:
        raise SystemExit('Android device review evidence changed during validation')
    RELEASE.mkdir(exist_ok=True)
    target_apk.write_bytes(apk_bytes)
    target_source.write_bytes(source_bytes)
    if snapshot(PREVIOUS_FILES)!=previous_hashes:
        raise SystemExit('An existing Android 0.1.2 or earlier release file changed')
    if digest(DEVICE_REVIEW)!=review_sha256:
        raise SystemExit('The Android device review changed')
    if {item:digest(full) for item,full in evidence_files}!=evidence_hashes:
        raise SystemExit('Android device review evidence changed')
    report={
        'version':VERSION,
        'version_code':int(VERSION_CODE),
        'package':PACKAGE,
        'min_sdk':int(MIN_SDK),
        'target_sdk':int(TARGET_SDK),
        'apk_v2_v3_signatures_verified':True,
        'signing_certificate_sha256':certs[0],
        'previous_signing_certificate_sha256':previous_certs[0],
        'signing_certificate_matches_0_1_2':True,
        'zip_integrity':True,
        'dialogue_lines':{'categories':11,'per_category':6,'total':total},
        'dialogue_matches_current_windows_lines':True,
        'original_frames_verified':count,
        'apk_entry_path_normalization':{
            'tool':'mobile/android/tools/NormalizeApkPaths.java',
            'applied_before_zipalign':True,
            'raw_entry_paths_are_posix':True,
        },
        'apk_raw_entry_paths_are_posix':True,
        'source_archive_raw_entry_paths_are_posix':True,
        'apk_raw_entry_count':len(raw_names),
        'path_normalization_fix':PATH_FIX_EXPLANATION,
        'share_intake_verified_on_physical_phone':True,
        'share_intake_prefills_only_and_requires_explicit_click':True,
        'source_includes_share_handler':True,
        'source_includes_manifest':True,
        'source_includes_apk_path_normalizer':True,
        'source_includes_build_script':True,
        'source_includes_android_readme':True,
        'source_includes_source_readme':True,
        'source_readme_version':VERSION,
        'source_readme_verification_scope_synced':True,
        'source_archive_excludes_private_material':True,
        'runtime_art':review['runtime_art'],
        'apk_installed_on_device':True,
        'android_device_tested':True,
        'partial_android_device_verification':True,
        'complete_multi_device_acceptance':False,
        'device_verification_summary':DEVICE_VERIFICATION_SUMMARY,
        'device_verification_topology':('one physical Android phone + one PC over direct private IPv4; '
                                        'not two PCs and not HarmonyOS'),
        'device_verification_scope':review['verification_scope'],
        'device_verification_not_verified':list(review['not_verified']),
        'device_verification_checks_all_true':True,
        'device_verification_checks_total':len(DEVICE_REVIEW_CHECKS),
        'device_verification_source':'qa/android-013-device-review.json',
        'device_verification_source_sha256':review_sha256,
        'device_verification_evidence_sha256':evidence_hashes,
        'device_verification':device_facts,
        'prior_version_basic_ui_evidence':'qa/android-012-device-review.json',
        'prior_version_basic_ui_evidence_scope':(
            'Historical 0.1.2 partial UI/overlay acceptance only. 0.1.3 did not repeat '
            'those screenshots or overlay checks, and they are not counted as 0.1.3 evidence.'),
        'native_android_tls_verified':review['transport']['native_android_tls_verified'],
        'physical_pc_android_lan_verified':review['transport']['physical_pc_android_lan_verified'],
        'physical_multi_pc_lan_verified':review['transport']['physical_multi_pc_lan_verified'],
        'harmony_verified':review['transport']['harmony_verified'],
        'direct_private_ipv4':review['transport']['direct_private_ipv4'],
        'adb_reverse_used':review['transport']['adb_reverse_used'],
        'protocol_check_scope':('JVM/PC only; verifies the shared protocol and TLS code between '
                                'Java and Python, not native Android TLS or a complete device test'),
        'java_python_protocol_check_source':'qa/android-protocol-checks.json',
        'java_python_protocol_check':protocol_facts,
        'source_archive_verified':True,
        'source_archive_entries':source_entries,
        'existing_previous_android_files_unchanged':True,
        'existing_previous_android_files_sha256':previous_hashes,
        'files':{
            target_apk.name:{'bytes':len(apk_bytes),'sha256':apk_sha256},
            target_source.name:{'bytes':len(source_bytes),
                                'sha256':hashlib.sha256(source_bytes).hexdigest()},
        },
    }
    target_report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
