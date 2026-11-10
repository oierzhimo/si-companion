"""Optional check that the pet can start and stop an existing VoiceStudio backend."""
import json
import sys
import time
import urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtCore import QCoreApplication
from si_pet.storage import Store
from si_pet.voice import VoiceEngine

root=Path(__file__).resolve().parents[1]
app=QCoreApplication([]);store=Store(root/'qa/voice-launch-settings.json')
store['voice_enabled']=True;store['voice_profile_id']='06332768';store['voice_volume']=65
voice=VoiceEngine(store);voice.client.cache_dir=root/'qa/voice-live-cache'
states=[];voice.status.connect(states.append)
voice.start_backend();ready=False;deadline=time.monotonic()+75
while time.monotonic()<deadline and not ready:
    app.processEvents()
    try:
        with urllib.request.urlopen('http://127.0.0.1:3900/health',timeout=.4) as response:
            ready=json.load(response).get('status')=='ok'
    except (OSError,ValueError):pass
    if not ready:time.sleep(.2)
owned=voice.backend is not None
if ready:
    voice.speak('管理员，我在。')
    playback_deadline=time.monotonic()+8
    while time.monotonic()<playback_deadline and not any('已播放' in s for s in states):
        app.processEvents();time.sleep(.05)
played=any('已播放' in s for s in states)
voice.stop();app.processEvents()
report={'ok':ready and owned and played and voice.backend.state().value==0,'health_ready':ready,'owned_process':owned,'played':played,'stopped':owned and voice.backend.state().value==0,'status':states}
(root/'qa/voice-launch-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
app.quit();raise SystemExit(0 if report['ok'] else 1)
