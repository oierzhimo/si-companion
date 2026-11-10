"""Create a local configuration alias, reusing installed weights unchanged."""
import json
import urllib.request

BASE = 'http://127.0.0.1:11434'
ALIAS = 'si-qwen2.5vl:7b'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(BASE + '/api/tags', timeout=10) as response:
    tags = json.load(response).get('models', [])
needs_profile = not any(item.get('name') == ALIAS for item in tags)
if not needs_profile:
    show=urllib.request.Request(BASE+'/api/show',json.dumps({'model':ALIAS}).encode(),
        {'Content-Type':'application/json'},method='POST')
    with opener.open(show,timeout=10) as response:
        parameters=json.load(response).get('parameters','')
    needs_profile='num_ctx' not in parameters or '16384' not in parameters
if needs_profile:
    body = json.dumps({'model': ALIAS, 'from': 'qwen2.5vl:7b', 'stream': False,
        'parameters': {'num_ctx': 16384, 'temperature': .5, 'top_p': .9,
                       'repeat_penalty': 1.06, 'num_predict': 2048}}).encode()
    request = urllib.request.Request(BASE + '/api/create', body,
        {'Content-Type':'application/json'}, method='POST')
    with opener.open(request, timeout=90) as response:
        result = json.load(response)
    if result.get('status') != 'success':
        raise RuntimeError('Local configuration alias was not created')
print('Local alias ready: ' + ALIAS + '; original installed model retained.')
