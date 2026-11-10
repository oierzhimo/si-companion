"""Relocate, run and verify the 1.2.2 InteractionPreview archive."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='1.2.2-InteractionPreview'
DEFAULT_ARCHIVE=ROOT/f'release/SiDesktopPet-{VERSION}-Windows-x64.zip'
parser=argparse.ArgumentParser()
parser.add_argument('--archive',type=Path,default=DEFAULT_ARCHIVE)
args=parser.parse_args()
archive=args.archive.resolve()
if archive.name!=DEFAULT_ARCHIVE.name:
    raise SystemExit(f'Expected {DEFAULT_ARCHIVE.name}, got {archive.name}')
if not archive.is_file():raise SystemExit(f'Archive missing: {archive}')
target=ROOT/'qa'/f'portable-check-{archive.stem}'
if not target.resolve().is_relative_to((ROOT/'qa').resolve()):
    raise RuntimeError('Unsafe portable check path')
if target.exists():shutil.rmtree(target)
target.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(archive) as z:
    bad=z.testzip()
    if bad is not None:raise SystemExit(f'ZIP integrity failed at {bad}')
    for entry in z.namelist():
        if not (target/entry).resolve().is_relative_to(target.resolve()):
            raise SystemExit('Unexpected archive path')
    z.extractall(target)
folder=target/archive.stem
exe=folder/'Si-DesktopPet.exe'
if not exe.is_file():raise SystemExit('Relocated executable missing')
smoke=target/'smoke-report.json'
if smoke.exists():smoke.unlink()
env=os.environ.copy()
env['PATH']=str(Path(env.get('SystemRoot',r'C:\Windows'))/'System32')+';'+env.get('SystemRoot',r'C:\Windows')
for key in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','PYTHONSTARTUP','CONDA_PREFIX'):
    env.pop(key,None)
completed=subprocess.run(
    [str(exe),'--quiet','--smoke-test',str(smoke)],
    cwd=folder,env=env,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
if completed.returncode or not smoke.exists():
    raise SystemExit('Relocated executable did not complete its check')
payload=json.loads(smoke.read_text(encoding='utf-8'))
if not payload.get('ok'):
    raise SystemExit('Relocated application reported a failure')
if payload.get('version')!='1.2.2':
    raise SystemExit(f'Unexpected application version: {payload.get("version")!r}')
payload['portable_verification']={
    'relocated_executable':str(exe),
    'python_removed_from_path':True,
    'exit_code':completed.returncode,
    'zip_integrity':True,
    'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
}
report=ROOT/'qa'/f'portable-smoke-{archive.stem}.json'
report.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(payload,ensure_ascii=False,indent=2))
