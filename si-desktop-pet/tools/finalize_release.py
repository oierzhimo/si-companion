"""Attach completed portable verification to source package and refresh hashes."""
import json
import hashlib
from make_release import ROOT,RELEASE,VERSION,zip_tree

def main():
    report=json.loads((ROOT/'qa/portable-smoke.json').read_text(encoding='utf-8'))
    if not report.get('ok') or not report.get('portable_verification',{}).get('python_removed_from_path'):
        raise SystemExit('Run tools/verify_portable.py successfully first.')
    source=RELEASE/f'SiDesktopPet-{VERSION}-Source.zip'
    zip_tree(ROOT,source,{'.venv','.tools','build','dist','release','data','__pycache__','.git','tmp','portable-check'},f'SiDesktopPet-{VERSION}-Source')
    portable=RELEASE/f'SiDesktopPet-{VERSION}-Windows-x64.zip'
    results={p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in [portable,source]}
    (RELEASE/'SHA256SUMS.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results,indent=2))
if __name__=='__main__':main()
