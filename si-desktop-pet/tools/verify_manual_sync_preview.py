"""Relocate and launch the frozen 2.0.0a1 manual sync preview."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
NAME='SiDesktopPet-2.0.0a1-ManualSyncPreview-Windows-x64'
ARCHIVE=ROOT/'release'/f'{NAME}.zip'


def main():
    if not ARCHIVE.is_file():
        raise SystemExit(f'Missing archive: {ARCHIVE}')
    target=ROOT/'qa'/f'portable-check-{NAME}'
    if not target.resolve().is_relative_to((ROOT/'qa').resolve()):
        raise RuntimeError('Unsafe verification path')
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(ARCHIVE) as archive:
        bad=archive.testzip()
        if bad:
            raise SystemExit(f'ZIP integrity failed at {bad}')
        if any(not (target/name).resolve().is_relative_to(target.resolve())
               for name in archive.namelist()):
            raise SystemExit('Archive contains an unsafe path')
        archive.extractall(target)
    folder=target/NAME
    exe=folder/'Si-DesktopPet.exe'
    launcher=folder/'Start-Sync-Preview.cmd'
    if not exe.is_file() or not launcher.is_file():
        raise SystemExit('Executable or preview launcher missing')
    smoke=target/'smoke.json'
    env=os.environ.copy()
    system_root=Path(env.get('SystemRoot',r'C:\Windows'))
    env['PATH']=str(system_root/'System32')+';'+str(system_root)
    for key in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','PYTHONSTARTUP','CONDA_PREFIX'):
        env.pop(key,None)
    completed=subprocess.run([str(exe),'--quiet','--sync-preview','--smoke-test',str(smoke)],
                             cwd=folder,env=env,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    if completed.returncode or not smoke.is_file():
        raise SystemExit('Relocated executable failed to start')
    report=json.loads(smoke.read_text(encoding='utf-8'))
    if not report.get('ok') or report.get('version')!='2.0.0a1':
        raise SystemExit('Unexpected frozen application report')
    ledger=target/'qa-settings.sync-v1.json'
    if not ledger.is_file():
        raise SystemExit('Opt-in sync ledger was not created')
    document=json.loads(ledger.read_text(encoding='utf-8'))
    if document.get('protocol')!=1 or not any(e.get('kind')=='legacy_snapshot' for e in document.get('events',[])):
        raise SystemExit('Opt-in sync ledger is invalid')
    report['portable_verification']={'zip_integrity':True,'exit_code':completed.returncode,
                                      'python_removed_from_path':True,
                                      'sync_ledger_created':True,
                                      'archive_sha256':hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()}
    output=ROOT/'qa'/f'portable-smoke-{NAME}.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
