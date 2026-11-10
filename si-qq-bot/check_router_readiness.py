"""Read-only local model availability check. Does not enable routing or infer."""
import json,urllib.request
from pathlib import Path
from model_connection import build_opener_for
from model_router import RouterConfig
def main():
    cfg=RouterConfig()
    with build_opener_for('http').open('http://127.0.0.1:11434/api/tags',timeout=5) as response:
        raw=response.read(256_001)
    if len(raw)>256_000:raise SystemExit('Model list too large')
    tags=json.loads(raw)
    installed={m['name'] for m in tags['models']}
    roles={'text':cfg.text_model,'vision':cfg.vision_model}
    report={'roles':{role:{'model':model,'installed':model in installed} for role,model in roles.items()},
            'ready':all(model in installed for model in roles.values()),'routing_enabled':False,
            'chat_messages_sent':False,'model_inference_performed':False,
            'old_model_preserved':'qwen2.5vl:7b' in installed}
    Path('qa-router-readiness.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
