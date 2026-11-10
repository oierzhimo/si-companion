"""Export reviewed synthetic style data only; never reads QQ user records."""
import json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def prepare():
    rows=[json.loads(line) for line in (ROOT/'data/style_candidates.jsonl').read_text('utf-8').splitlines() if line.strip()]
    approved=[r for r in rows if r.get('review_status')=='approved']
    if len(approved)<200:raise SystemExit(f'Only {len(approved)} approved samples; need at least 200. Training not started.')
    seen=set();out=[]
    for row in approved:
        if any(not isinstance(row.get(k),str) or not 0<len(row[k])<=12000 for k in ('instruction','output')):
            raise SystemExit('Invalid reviewed sample')
        key=''.join(row['instruction'].split())
        if key in seen:raise SystemExit('Duplicate prompt; review the dataset')
        seen.add(key);out.append({k:row.get(k,'') for k in ('instruction','input','output')})
    held=[json.loads(line) for line in (ROOT/'data/heldout.jsonl').read_text('utf-8').splitlines() if line.strip()]
    if any(''.join(r['prompt'].split()) in seen for r in held):raise SystemExit('Evaluation leakage')
    target=ROOT/'data/si_style_approved.json'
    target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'data/dataset_info.json').write_text(json.dumps({'si_style_approved':{'file_name':target.name,
      'columns':{'prompt':'instruction','query':'input','response':'output'}}},indent=2),encoding='utf-8')
    print(json.dumps({'approved':len(out),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'training_started':False}))
if __name__=='__main__':prepare()
