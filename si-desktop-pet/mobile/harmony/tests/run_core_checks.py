"""TypeScript/core proof only. Does not claim ArkTS UI or native Harmony API execution."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT=Path(os.environ.get('SI_PC_PROJECT',str(Path(__file__).resolve().parents[3])))
HARMONY=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from si_pet.sync_v1 import Ledger,COUNTERS
from si_pet.sync_transport import PeerRegistry,LanServer
NODE=Path(os.environ.get('SI_NODE',str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')))
TSC=Path(os.environ.get('SI_TSC',r'F:\si_accompany\toolchains\typescript-5.6.3\package\lib\tsc.js'))
core=HARMONY/'entry/src/main/ets/core'
subprocess.run([str(NODE),str(TSC),'--strict','--target','ES2020','--module','commonjs',
                '--outDir',str(HARMONY/'build/protocol'),str(core/'Wire.ts'),str(core/'HttpBuffer.ts')],check=True)
(ROOT/'qa/tmp').mkdir(parents=True,exist_ok=True)
work=Path(tempfile.mkdtemp(dir=ROOT/'qa/tmp',prefix='harmony-core-'))
fixture=Ledger(work/'fixture-ledger.json','pc-fixture');fixture.seed_legacy({'pats':2,'affinity':2})
fixture.add('snack',occurred_at='2026-10-26T01:00:00Z')
fixture.add('focus_completed',{'minutes':25},occurred_at='2026-10-26T01:10:00Z')
fixture.add('journal',{'text':'管理员，墨色🖌️\n</script>\u2028'},occurred_at='2026-10-26T09:11:00+08:00')
fixture.add('journal',{'text':'同秒稍后的一条'},occurred_at='2026-10-26T01:11:00.000002Z')
fixture.add('journal',{'text':'同秒稍早的一条'},occurred_at='2026-10-26T01:11:00.000001Z')
fixture.add('pref_set',{'key':'preferred_name','value':'管理员'},occurred_at='2026-10-26T01:12:00Z')
(work/'fixture.json').write_bytes(fixture.export())
expected={'events':len(fixture.events),'counters':{k:fixture.summary()[k] for k in COUNTERS},
          'journal_texts':[row['text'] for row in fixture.journal_rows()]}
(work/'expected.json').write_text(json.dumps(expected,ensure_ascii=False),encoding='utf-8')
host=Ledger(work/'host-ledger.json','pc-host');host.seed_legacy({});host.add('snack')
registry=PeerRegistry(work/'host',host.device_id,'PC host')
def receive(data):
    host.import_bundle(data);return host.export()
server=LanServer(registry,receive)
try:
    (work/'invitation.txt').write_text(server.invitation('127.0.0.1'),encoding='utf-8')
    subprocess.run([str(NODE),str(HARMONY/'tests/protocol_checks.cjs'),str(work)],check=True,timeout=40)
finally:
    server.close()
assert fixture.import_bundle((work/'harmony-export.json').read_bytes())==4
assert fixture.summary()['affinity']==15
assert fixture.summary()['preferences']['preferred_name']=='管理员'
assert host.import_bundle((work/'network-export.json').read_bytes())==0
assert host.summary()['affinity']==4
report={'ok':True,'typescript_strict_check':True,'production_core_python_fixture':True,
        'microsecond_journal_order_matches':True,'real_node_python_tls_exchange':True,
        'native_harmony_sdk_build':False,'native_harmony_network_test':False,'harmony_tablet_tested':False,
        'work':str(work)}
(ROOT/'qa/harmony-core-checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
