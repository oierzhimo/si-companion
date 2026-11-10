"""Compile the Android-independent production Java core and exercise it against PC code."""
import json
import subprocess
import sys
import tempfile
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
ANDROID=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from si_pet.sync_v1 import Ledger,canonical
from si_pet.sync_transport import PeerRegistry,LanServer

JDK=Path(os.environ.get('SI_ANDROID_TEST_JDK',r'E:\create\focusguard\tools\jdk\jdk-17.0.20.1+1'))
JAR=ANDROID/'tools'/'json-jvm-tests.jar'
SOURCE=ANDROID/'app/src/main/java/cn/si/companion'
(ROOT/'qa'/'tmp').mkdir(parents=True,exist_ok=True)
work=Path(tempfile.mkdtemp(dir=ROOT/'qa'/'tmp',prefix='android-protocol-'))
classes=work/'classes';classes.mkdir()
subprocess.run([str(JDK/'bin/javac.exe'),'-encoding','UTF-8','--release','8','-cp',str(JAR),'-d',str(classes),
                *[str(SOURCE/name) for name in ('JsonCodec.java','EventLedger.java','PinnedPeer.java')],
                str(ANDROID/'tests/ProtocolChecks.java'),str(ANDROID/'tests/NetworkChecks.java')],check=True)
fixture=Ledger(work/'python-fixture.json','pc-fixture')
fixture.seed_legacy({'pats':2,'snacks':0,'focus_sessions':0,'affinity':2,'history':[]})
fixture.add('snack',occurred_at='2026-10-26T09:00:00+08:00')
fixture.add('focus_completed',{'minutes':25},occurred_at='2026-10-26T01:10:00Z')
fixture.add('journal',{'text':'管理员，墨色🖌️\n</script>\u2028'},occurred_at='2026-10-26T01:11:00Z')
fixture_path=work/'fixture.json';fixture_path.write_bytes(fixture.export())
classpath=str(classes)+';'+str(JAR)
subprocess.run([str(JDK/'bin/java.exe'),'-cp',classpath,'cn.si.companion.ProtocolChecks',str(work),str(fixture_path)],check=True)
expected=canonical({'text':'管理员，墨色🖌️\n</script>\u2028'})
assert (work/'java-canonical.json').read_bytes()==expected
assert fixture.import_bundle((work/'android-export.json').read_bytes())==2
assert fixture.summary()['affinity']==11
host=Ledger(work/'host-ledger.json','pc-host');host.seed_legacy({})
host.add('snack')
registry=PeerRegistry(work/'host',host.device_id,'PC host')
def receive(data):
    host.import_bundle(data)
    return host.export()
server=LanServer(registry,receive)
try:
    invitation=work/'invitation.txt';invitation.write_text(server.invitation('127.0.0.1'),encoding='utf-8')
    output=work/'network-result.json'
    subprocess.run([str(JDK/'bin/java.exe'),'-cp',classpath,'cn.si.companion.NetworkChecks',str(invitation),
                    str(work/'network-ledger.json'),str(output)],check=True,timeout=35)
    assert host.import_bundle(output.read_bytes())==0
    assert (host.summary()['pats'],host.summary()['snacks'],host.summary()['affinity'])==(1,1,4)
finally:
    server.close()
report={'ok':True,'java_python_canonical_json_matches':True,'protocol_fixture_and_reload':True,
        'actual_java_python_tls_roundtrip':True,'android_device_tested':False,'work':str(work)}
(ROOT/'qa/android-protocol-checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
