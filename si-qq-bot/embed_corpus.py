"""Build optional BGE-M3 vectors locally; no downloads and no chat access."""
import hashlib,json,math,urllib.request
from pathlib import Path
from model_connection import build_opener_for
from knowledge_rag import ROOT,load_corpus
def main():
    rows,digest=load_corpus(ROOT)
    if not rows:raise SystemExit('No valid knowledge corpus')
    opener=build_opener_for('http')
    with opener.open('http://127.0.0.1:11434/api/tags',timeout=5) as response:
        tags=json.loads(response.read(256_000))
    if not any(m.get('name','').split(':')[0]=='bge-m3' for m in tags.get('models',[])):
        raise SystemExit('bge-m3 is not installed; retaining lexical retrieval. No model download attempted.')
    matrix=[]
    for start in range(0,len(rows),8):
        req=urllib.request.Request('http://127.0.0.1:11434/api/embed',data=json.dumps({
            'model':'bge-m3','input':[r['title']+'\n'+r['text'] for r in rows[start:start+8]],
            'truncate':False,'keep_alive':0}).encode(),headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=120) as response:raw=response.read(2_000_001)
        if len(raw)>2_000_000:raise SystemExit('Embedding reply too large')
        batch=json.loads(raw)['embeddings']
        if len(batch)!=len(rows[start:start+8]):raise SystemExit('Embedding count mismatch')
        matrix.extend(batch)
    width=len(matrix[0])
    if not 32<=width<=4096 or not all(len(v)==width and all(type(x) in (int,float) and math.isfinite(x) for x in v) for v in matrix):
        raise SystemExit('Invalid embedding vectors')
    value=dict(corpus_sha256=digest,model='bge-m3',ids=[r['id'] for r in rows],vectors=matrix)
    target=ROOT/'vectors.json'
    if target.exists():raise SystemExit('Existing vectors retained; move them aside explicitly before rebuilding')
    target.write_text(json.dumps(value,separators=(',',':')),encoding='utf-8')
    print(json.dumps({'vector_count':len(matrix),'dimensions':width,'corpus_sha256':digest}))
if __name__=='__main__':main()
