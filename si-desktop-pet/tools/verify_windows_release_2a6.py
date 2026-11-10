"""Verify the actual relocated executable in ordinary and opt-in sync modes."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
NAME='SiDesktopPet-2.0.0a6-Windows-x64'

def main():
    archive_path=ROOT/'release'/f'{NAME}.zip'
    target=Path(tempfile.mkdtemp(dir=ROOT/'qa',prefix=f'portable-check-{NAME}-'))
    with zipfile.ZipFile(archive_path) as archive:
        bad=archive.testzip()
        if bad:
            raise RuntimeError(f'Corrupt ZIP entry: {bad}')
        if any(not (target/name).resolve().is_relative_to(target.resolve()) for name in archive.namelist()):
            raise RuntimeError('Unsafe archive path')
        archive.extractall(target)
    folder=target/NAME
    exe=folder/'Si-DesktopPet.exe'
    env=os.environ.copy()
    system=Path(env.get('SystemRoot',r'C:\Windows'))
    env['PATH']=str(system/'System32')+';'+str(system)
    for key in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','PYTHONSTARTUP','CONDA_PREFIX'):
        env.pop(key,None)
    report={'version':'2.0.0a6','zip_integrity':True,'python_removed_from_path':True,'modes':{}}
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
        if not data.get('ok') or data.get('version')!='2.0.0a6':
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
    output=ROOT/'qa'/'release-2.0.0a6-verification.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()


