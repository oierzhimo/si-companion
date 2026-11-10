"""Exercise real Qt widgets and controller transitions without touching user settings."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from si_pet.storage import Store
from si_pet.sprites import Sprites
from si_pet.controller import Controller
from si_pet.pet_window import PetWindow
from si_pet.panel import Panel

root=Path(__file__).resolve().parents[1]
app=QApplication([]);app.setQuitOnLastWindowClosed(False)
store=Store(root/'qa/integration-settings.json');store['scale']=2;store['affinity']=0;store['pats']=0;store['snacks']=0;store['focus_sessions']=0
sprites=Sprites();ctrl=Controller(store,sprites);ctrl.decision_at=time.monotonic()+1000
pet=PetWindow(ctrl,lambda:None);panel=Panel(ctrl,pet);pet.show();panel.show();app.processEvents()
checks=[]
def check(name,predicate):
    ok=bool(predicate);checks.append({'check':name,'ok':ok})
    if not ok:raise AssertionError(name)

check('default exactly 2x / 384x416 logical cell',pet.size().width()==384 and pet.size().height()==416)
check('all 16 look frames are real nonempty images',len(sprites.frames['look'])==16 and all(p.is_file() for p in sprites.frames['look']))
check('new sitting and lying frames loaded',len(sprites.frames['sitting'])==4 and len(sprites.frames['sleeping'])==4)
for page,name in enumerate(['home','settings','journal','about']):
    panel.stack.setCurrentIndex(page);app.processEvents();panel.grab().save(str(root/f'qa/panel-{name}.png'))
    check(f'{name} page renders',panel.stack.currentIndex()==page)
ctrl.pat();check('poke increases affinity',store['pats']==1 and store['affinity']==1)
ctrl.feed();check('feed increases stats',store['snacks']==1 and store['affinity']==4)
ctrl.feed();check('feeding cooldown works',store['snacks']==1)
reply=ctrl.contact('stuck');app.processEvents()
check('explicit chat choice updates panel without claiming to see work',panel.contact_reply_text.text()==reply and '屏幕' not in reply)
menu=pet.menu();check('pet context menu exposes chat choices',len(next(a.menu() for a in menu.actions() if a.text()=='和祀说一句').actions())==5)
ctrl.contact('quiet');app.processEvents()
check('quiet chat choice docks and reduces presence',store['quiet_mode'] and ctrl.docked and pet.windowOpacity()<1)
pet.option('quiet_mode',False);app.processEvents()
check('leaving chat quiet mode releases its docking hold',not ctrl.docked and not ctrl.quiet_dock and ctrl.override_until==0 and pet.windowOpacity()==1)
b=ctrl.physics.body;floor=next(p for p in ctrl.physics.platforms if p.floor);b.x=(floor.left+floor.right)/2;b.y=floor.top;b.support=floor.key
ctrl.perform('sit');ctrl.update(.016);check('sit action selects actual seated animation',ctrl.state=='sitting')
ctrl.perform('sleep');ctrl.update(.016);check('sleep action selects lying animation',ctrl.state=='sleeping')
ctrl.perform('jump');ctrl.update(.016);check('jump leaves surface',b.y<floor.top and b.support is None)
for _ in range(180):ctrl.update(1/60)
check('lands on real floor',b.support is not None and abs(b.y-floor.top)<1)
pet.option('manual',True);QTest.keyPress(pet,Qt.Key.Key_Left);ctrl.update(.016);check('manual left responds',b.vx<0)
QTest.keyRelease(pet,Qt.Key.Key_Left);check('key release stops input','left' not in ctrl.keys)
QTest.keyClick(pet,Qt.Key.Key_Up);check('manual up cycles action',ctrl.action=='sit')
pet.option('manual',False)
pet.option('clickthrough',True);check('clickthrough flag applied',bool(pet.windowFlags()&Qt.WindowType.WindowTransparentForInput))
pet.recover();check('recover removes clickthrough',not store['clickthrough'] and not ctrl.hidden)
store['auto_dock']=False
ctrl.focus(1);ctrl.focus()
check('early focus cancel releases movement hold',ctrl.focus_end is None and ctrl.override_until==0 and ctrl.action=='idle')
ctrl.focus(1);pet.recover()
check('recover during focus restores mouse input',ctrl.focus_end is None and not bool(pet.windowFlags()&Qt.WindowType.WindowTransparentForInput))
store['auto_dock']=True
ctrl.dock();g=app.screenAt(QPoint(round(b.x),round(b.y))).availableGeometry()
check('dock keeps full cell within screen',b.x+96*store['scale']<=g.right() and b.x-96*store['scale']>=g.left())
floor=next(p for p in ctrl.physics.platforms if p.floor)
b.x=g.right()-96*store['scale'];b.y=floor.top;b.support=floor.key;b.vx=0
ctrl.action='walk';ctrl.direction=1;ctrl.override_until=0;ctrl.decision_at=time.monotonic()+1000;ctrl.keys.clear()
ctrl.update(1/60)
check('walking turns before the sprite canvas leaves the screen',ctrl.direction==-1 and b.x+96*store['scale']<=g.right()+.01)
ctrl.perform('wave',announce=False)
ctrl.focus(1);ctrl.focus_end=time.monotonic()-.1;ctrl.update(.016);check('focus completion records a session',store['focus_sessions']==1 and ctrl.focus_end is None)
ctrl.dock();panel.setting('scale',3);app.processEvents()
check('increasing scale at the edge re-clamps the full canvas',pet.width()==576 and b.x+96*store['scale']<=g.right())
panel.setting('scale',1.5);check('scale changes actual window',pet.width()==288)
panel.setting('scale',2);check('size restores to two times',pet.width()==384)
panel.setting('scale',3);ctrl.recover();g=app.screenAt(QPoint(round(b.x),round(b.y))).availableGeometry()
check('recover keeps the full canvas inside the screen at 3x',b.x+96*store['scale']<=g.right() and b.y-202*store['scale']>=g.top())
panel.setting('scale',2)
store.save();check('state survives reload',Store(store.path)['focus_sessions']==1)
panel.stack.setCurrentIndex(0);app.processEvents();panel.grab().save(str(root/'qa/panel-final.png'))
report={'ok':True,'checks':checks,'count':len(checks),'native_platform_count':len(ctrl.physics.platforms)}
(root/'qa/integration-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
pet.timer.stop();pet.bubble.hide();pet.hide();panel.hide();app.quit()
