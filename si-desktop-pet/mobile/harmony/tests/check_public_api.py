"""Compare native adapter signatures with a pinned public API snapshot; not an ArkTS build."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT=Path(os.environ.get('SI_PC_PROJECT',str(Path(__file__).resolve().parents[3])))
HARMONY=Path(__file__).resolve().parents[1]
API_COMMIT='0ebbb2136397fd2b190547bc3c2cdcf6b4ba257a'
API=Path(os.environ.get('SI_OH_API',f'F:/si_accompany/reference-review/harmony-api12/interface_sdk-js-{API_COMMIT}/api'))
NODE=Path(os.environ.get('SI_NODE',str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')))
TSC=Path(os.environ.get('SI_TSC',r'F:\si_accompany\toolchains\typescript-5.6.3\package\lib\tsc.js'))
source=HARMONY/'entry/src/main/ets'
stage=HARMONY/'build/api-check'
(stage/'core').mkdir(parents=True,exist_ok=True)
(stage/'platform').mkdir(parents=True,exist_ok=True)
for path in (source/'core').glob('*.ts'):
    shutil.copy2(path,stage/'core'/path.name)
files=[]
for path in (source/'platform').glob('*.ets'):
    target=stage/'platform'/path.with_suffix('.ts').name
    shutil.copy2(path,target);files.append(str(target.resolve()))
config={'compilerOptions':{'strict':True,'target':'ES2020','module':'commonjs','skipLibCheck':True,'noEmit':True,
                          'baseUrl':str(stage.resolve()),'paths':{'@ohos.*':[str(API/'@ohos.*.d.ts')]}}}
config['files']=files
(stage/'tsconfig.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
result=subprocess.run([str(NODE),str(TSC),'-p',str(stage/'tsconfig.json')],capture_output=True,encoding='utf-8')
print(result.stdout);print(result.stderr)
if result.returncode:
    raise SystemExit(result.returncode)
report={'ok':True,'public_api_repository':'https://github.com/openharmony/interface_sdk-js',
        'public_api_commit':API_COMMIT,'adapter_signatures_checked':[Path(path).name for path in files],
        'typescript_5_6_3':True,'harmony_vendor_sdk_build':False,'arkui_page_compiled':False,
        'native_network_executed':False,'harmony_tablet_tested':False}
(ROOT/'qa/harmony-public-api-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
