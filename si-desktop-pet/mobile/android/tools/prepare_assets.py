"""Copy selected existing project frames verbatim; no new artwork or image edits."""
import json
import shutil
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
ANDROID=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from si_pet.companion import LINES
destination=ANDROID/'app/src/main/assets'
destination.mkdir(parents=True,exist_ok=True)
manifest=json.loads((ROOT/'assets/animations.json').read_text(encoding='utf-8'))
selected={state:manifest[state] for state in ('idle','waving','sitting')}
for spec in selected.values():
    for name in spec['files']:
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/'assets'/name,target)
(destination/'animations.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
(destination/'lines.json').write_text(json.dumps(LINES,ensure_ascii=False,indent=2),encoding='utf-8')
print('Copied 14 original frames and shared administrator dialogue.')
