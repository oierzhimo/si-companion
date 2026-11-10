"""One controlled native window; no screenshots, external-app input or user save."""
import os
import sys
import json
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QLabel
from si_pet import native
from si_pet.controller import Controller
from si_pet.pet_window import PetWindow
from si_pet.sprites import Sprites
from si_pet.storage import Store

app=QApplication([])
app.setQuitOnLastWindowClosed(False)
tmp=tempfile.TemporaryDirectory(prefix='si-visibility-native-')
store=Store(Path(tmp.name)/'settings.json')
store['window_edges']=False;store['bubbles']=False;store['voice_enabled']=False
ctrl=Controller(store,Sprites())
g=app.primaryScreen().availableGeometry();b=ctrl.physics.body
b.x=g.center().x();b.y=g.bottom()-6*store['scale']
b.support=next(p.key for p in ctrl.physics.platforms if p.floor and p.left<=b.x<=p.right)
b.vx=65;b.vy=0;ctrl.action='walk';ctrl.direction=1;ctrl.decision_at=time.monotonic()+60
ctrl.last_save=time.monotonic()+600
pet=PetWindow(ctrl,lambda:None)
peer=QLabel('祀 · 运行舒适度受控测试\n约六秒后自动关闭')
peer.setWindowTitle('Si controlled visibility pause')
peer.setStyleSheet('background:#e1eadf;color:#244839;font:24px "Microsoft YaHei UI";padding:40px')
peer.resize(760,420)
report={'physical_multi_monitor_tested':False,'game_tested':False,'screen_capture':False,'phases':{}}

def record(name):
    report['phases'][name]={
        'position':[b.x,b.y], 'velocity':[b.vx,b.vy],
        'suppressed':ctrl.presentation_suppressed,'pet_visible':pet.isVisible(),
        'action':ctrl.action,
        'fixture_foreground':int(native.user.GetForegroundWindow())==int(peer.winId()),
    }

def full():
    peer.showFullScreen();peer.raise_();peer.activateWindow()

def restore():
    peer.showNormal();peer.raise_();peer.activateWindow()

peer.show();peer.raise_();peer.activateWindow();pet.show()
started=False
def begin_when_foreground():
    global started
    if started or int(native.user.GetForegroundWindow())!=int(peer.winId()):return
    started=True
    QTimer.singleShot(800,full)
    QTimer.singleShot(2000,lambda:record('hidden_start'))
    QTimer.singleShot(3200,lambda:record('hidden_end'))
    QTimer.singleShot(3500,restore)
    QTimer.singleShot(4500,lambda:record('restored'))
    QTimer.singleShot(4900,app.quit)
wait_foreground=QTimer();wait_foreground.timeout.connect(begin_when_foreground);wait_foreground.start(100)
QTimer.singleShot(45000,app.quit)
app.exec()
pet.timer.stop();pet.bubble.close();pet.close();peer.close()
phases=report['phases']
if set(phases)=={'hidden_start','hidden_end','restored'}:
    start,end,shown=(phases[k] for k in ('hidden_start','hidden_end','restored'))
    report['passed']=(start['suppressed'] and end['suppressed']
        and not start['pet_visible'] and not end['pet_visible']
        and start['position']==end['position']
        and shown['pet_visible'] and not shown['suppressed']
        and shown['action']=='idle'
        and all(p['fixture_foreground'] for p in phases.values()))
else:report['passed']=False
(ROOT/'qa/native-visibility-pause.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
tmp.cleanup()
print(json.dumps(report),flush=True)
raise SystemExit(0 if report['passed'] else 1)
