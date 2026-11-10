"""Optional live check: requires VoiceStudio on 127.0.0.1:3900 and a real cached sample."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtCore import QCoreApplication
from si_pet.storage import Store
from si_pet.voice import VoiceEngine
from si_pet.voice_client import VoiceClient

root=Path(__file__).resolve().parents[1]
app=QCoreApplication([])
store=Store(root/'qa/voice-live-settings.json')
store['voice_enabled']=True;store['voice_profile_id']='06332768';store['voice_volume']=65
voice=VoiceEngine(store);voice.client=VoiceClient(root/'qa/voice-live-cache')
states=[];voice.status.connect(states.append)
voice.speak('管理员，我在。')
deadline=time.monotonic()+8
while time.monotonic()<deadline and not any('已播放' in s or '失败' in s or '未连接' in s for s in states):
    app.processEvents();time.sleep(.05)
report={'ok':any('已播放' in s for s in states),'status':states}
(root/'qa/voice-live-playback.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
voice.stop();app.quit()
raise SystemExit(0 if report['ok'] else 1)
