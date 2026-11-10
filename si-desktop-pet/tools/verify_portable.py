"""Unzip and run the real EXE in another directory with Python removed from PATH."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
from make_release import VERSION
parser=argparse.ArgumentParser()
parser.add_argument('--archive',type=Path,default=ROOT/f'release/SiDesktopPet-{VERSION}-Windows-x64.zip')
args=parser.parse_args()
archive=args.archive.resolve()
target=ROOT/'qa'/f'portable-check-{archive.stem}'
if not target.resolve().is_relative_to((ROOT/'qa').resolve()):raise RuntimeError('Unsafe portable check path')
if target.exists():shutil.rmtree(target)
target.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(archive) as z:
    for entry in z.namelist():
        if not (target/entry).resolve().is_relative_to(target.resolve()):raise RuntimeError('Unexpected archive path')
    z.extractall(target)
folder=target/archive.stem
exe=folder/'Si-DesktopPet.exe'
report_path=ROOT/('qa/portable-smoke.json' if archive.stem==f'SiDesktopPet-{VERSION}-Windows-x64' else f'qa/portable-smoke-{archive.stem}.json')
if report_path.exists():report_path.unlink()
env=os.environ.copy()
env['PATH']=str(Path(env.get('SystemRoot',r'C:\Windows'))/'System32')+';'+env.get('SystemRoot',r'C:\Windows')
for key in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV'):env.pop(key,None)
completed=subprocess.run([str(exe),'--quiet','--smoke-test',str(report_path)],cwd=folder,env=env,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
if completed.returncode or not report_path.exists():raise SystemExit('Relocated executable did not complete its check')
payload=json.loads(report_path.read_text(encoding='utf-8'))
if not payload.get('ok'):raise SystemExit('Relocated application reported a failure')
payload['portable_verification']={'relocated_executable':str(exe),'python_removed_from_path':True,'exit_code':completed.returncode,'zip_integrity':True}
with zipfile.ZipFile(archive) as z:
    if z.testzip() is not None:raise SystemExit('ZIP integrity failed')
report_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(payload,ensure_ascii=False,indent=2))
