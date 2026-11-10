"""Copy original desktop frames and shared administrator dialogue for the native project."""
import json
import shutil
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
HARMONY=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from si_pet.companion import LINES
raw=HARMONY/'entry/src/main/resources/rawfile'
raw.mkdir(parents=True,exist_ok=True)
manifest=json.loads((ROOT/'assets/animations.json').read_text(encoding='utf-8'))
selected={state:manifest[state] for state in ('idle','waving','sitting')}
for spec in selected.values():
    for name in spec['files']:
        target=raw/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/'assets'/name,target)
(raw/'animations.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
(raw/'lines.json').write_text(json.dumps(LINES,ensure_ascii=False,indent=2),encoding='utf-8')
print('Copied 14 unmodified frames and shared administrator dialogue to the native project.')
