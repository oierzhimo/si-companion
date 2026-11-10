"""Verify the actual 2.0.0a7 relocated executable and source archive boundaries."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='2.0.0a7'
NAME=f'SiDesktopPet-{VERSION}-Windows-x64'
SOURCE_NAME=f'SiDesktopPet-{VERSION}-Source'
BANNED_PARTS={'qa','build','dist','release','data','__pycache__','.venv','.tools',
              '.build-tmp','.pip-cache','tmp','portable-check','acl-recovery'}
BANNED_PREFIXES=('build-1.2.2','dist-1.2.2','build-2.0','dist-2.0')
AUDIO_SUFFIXES={'.wav','.mp3','.flac','.ogg','.m4a'}

def safe_names(archive, target):
    bad=archive.testzip()
    if bad:
        raise RuntimeError(f'Corrupt ZIP entry: {bad}')
    names=archive.namelist()
    if any(not (target/name).resolve().is_relative_to(target.resolve()) for name in names):
        raise RuntimeError('Unsafe archive path')
    return names

def verify_source_archive():
    path=ROOT/'release'/f'{SOURCE_NAME}.zip'
    if not path.is_file():
        raise RuntimeError('Source archive is missing')
    android_name=f'{SOURCE_NAME}/mobile/android/app/src/main/assets/lines.json'
    harmony_name=f'{SOURCE_NAME}/mobile/harmony/entry/src/main/resources/rawfile/lines.json'
    with zipfile.ZipFile(path) as archive:
        names=safe_names(archive,ROOT)
        if not names or any(not name.replace('\\','/').startswith(f'{SOURCE_NAME}/') for name in names):
            raise RuntimeError('Source archive has an unexpected top-level layout')
        entries={name.replace('\\','/'):name for name in names}
        for name in names:
            clean=name.replace('\\','/')
            parts=Path(clean).parts
            if any(part in BANNED_PARTS or part.startswith(BANNED_PREFIXES) for part in parts):
                raise RuntimeError(f'Source archive contains a build/qa path: {name}')
            if Path(clean).suffix.lower() in AUDIO_SUFFIXES:
                raise RuntimeError(f'Source archive contains listening audio: {name}')
        if android_name not in entries or harmony_name not in entries:
            raise RuntimeError('Source archive is missing a mobile dialogue table')
        android=archive.read(entries[android_name])
        harmony=archive.read(entries[harmony_name])
    if android!=harmony:
        raise RuntimeError('The two mobile dialogue tables differ')
    data=json.loads(android.decode('utf-8'))
    if len(data)!=11 or any(len(lines)!=6 for lines in data.values()):
        raise RuntimeError('Source archive dialogue table is not 11 occasions by 6 lines')
    return {'integrity':True,'build_and_qa_excluded':True,'audio_excluded':True,
            'mobile_lines_identical':True,'occasions':len(data),
            'lines':sum(len(lines) for lines in data.values()),
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def main():
    archive_path=ROOT/'release'/f'{NAME}.zip'
    if not archive_path.is_file():
        raise RuntimeError('Portable archive is missing')
    source_report=verify_source_archive()
    target=Path(tempfile.mkdtemp(dir=ROOT/'qa',prefix=f'portable-check-{NAME}-'))
    with zipfile.ZipFile(archive_path) as archive:
        safe_names(archive,target)
        archive.extractall(target)
    folder=target/NAME
    exe=folder/'Si-DesktopPet.exe'
    env=os.environ.copy()
    system=Path(env.get('SystemRoot',r'C:\Windows'))
    env['PATH']=str(system/'System32')+';'+str(system)
    for key in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','PYTHONSTARTUP','CONDA_PREFIX'):
        env.pop(key,None)
    report={'version':VERSION,'zip_integrity':True,'python_removed_from_path':True,
            'source_archive':source_report,'modes':{}}
    for mode in ('ordinary','sync'):
        run=target/mode
        run.mkdir()
        output=run/'smoke.json'
        args=[str(exe),'--smoke-test',str(output)]
        if mode=='sync':
            args.extend(['--sync-preview','--check-lan'])
        result=subprocess.run(args,cwd=run,env=env,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not output.is_file():
            raise RuntimeError(f'{mode} startup failed: {result.returncode}')
        data=json.loads(output.read_text(encoding='utf-8'))
        if not data.get('ok') or data.get('version')!=VERSION:
            raise RuntimeError(f'{mode} startup report failed')
        if not data.get('conversation_ui_constructed'):
            raise RuntimeError('Frozen conversation dialog did not construct')
        if mode=='sync' and not data.get('lan_check',{}).get('ok'):
            raise RuntimeError('Frozen app LAN pairing/exchange failed')
        ledger=run/'qa-settings.sync-v1.json'
        if ledger.exists()!=(mode=='sync'):
            raise RuntimeError('Sync was not opt-in')
        report['modes'][mode]=data
    preview=target/'panel.png'
    result=subprocess.run([str(exe),'--render-preview',str(preview)],cwd=target,env=env,
                          timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not preview.is_file():
        raise RuntimeError('Frozen panel rendering failed')
    log=(folder/'data'/'app.log').read_text(encoding='utf-8')
    if 'Unhandled exception' in log:
        raise RuntimeError('Frozen app logged an exception')
    report.update(ok=True,archive_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                  panel_preview=str(preview),log_has_no_unhandled_exception=True)
    output=ROOT/'qa'/f'release-{VERSION}-verification.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
