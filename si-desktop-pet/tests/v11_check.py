"""Visual and timing regression checks for the 1.1 release."""
import sys,time,json,random
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QPainter,QColor
from PySide6.QtCore import Qt
from si_pet.sprites import Sprites
from si_pet.storage import Store
from si_pet.controller import Controller
from si_pet.pet_window import PetWindow
root=Path(__file__).resolve().parents[1];app=QApplication([])
sprites=Sprites();store=Store(root/'qa/v11-settings.json');store['scale']=3
ctrl=Controller(store,sprites);pet=PetWindow(ctrl,lambda:None);pet.timer.stop()
results=[]
def check(name,value):
    results.append({'check':name,'ok':bool(value)})
    assert value,name
for dpr in (1,1.5,2,3):
    pix=sprites.pixmap('idle',0,3,dpr)
    check(f'3x at DPR {dpr}: physical pixels and logical footprint',pix.width()==round(576*dpr) and pix.height()==round(624*dpr) and pix.deviceIndependentSize().width()==576)
check('HD master retains 1536x1664 pixels',sprites.source('idle',0).width()==1536)
for state in sprites.frames:
    for index in range(len(sprites.frames[state])):sprites.pixmap(state,index,3,2)
check('bounded render and source cache',len(sprites.cache)<=12 and len(sprites.sources)<=6)
check('8-frame gait in both directions',len(sprites.frames['walk-left'])==len(sprites.frames['walk-right'])==8)
ctrl.state='walk-right';ctrl.walk_distance=0
a=ctrl.animation();ctrl.walk_distance=18;b=ctrl.animation()
check('walk pose advances by distance',a!=b)
check('stationary pose does not cycle',ctrl.animation()==b)
floor=next(p for p in ctrl.physics.platforms if p.floor);body=ctrl.physics.body
body.x=(floor.left+floor.right)/2;body.y=floor.top;body.support=floor.key;body.vx=0
store['manual']=True;ctrl.keys={'right'};ctrl.update(1/60)
check('walking accelerates instead of snapping',0<body.vx<store['speed'])
for _ in range(60):ctrl.update(1/60)
speed=body.vx;ctrl.keys.clear();ctrl.update(1/60)
check('walking decelerates instead of snapping',0<body.vx<speed)
ctrl.state='idle';store['cursor_follow']=False
seen=[];random.seed(11)
for n in range(16):
    ctrl.idle_next=0
    with patch('si_pet.controller.time.monotonic',return_value=100+n*20):
        state,index=ctrl.animation();seen.append(ctrl.idle_clip)
check('idle choices vary without immediate repeats',len(set(seen))>=3 and all(a!=b for a,b in zip(seen,seen[1:])))
ctrl.idle_next=time.monotonic()+10;ctrl.idle_until=0
check('resting pose holds between gestures',ctrl.animation()==('idle',0))
pet.bubble.speak('我在。安心做你的事吧。\n慢一点，也没关系。',pet);app.processEvents()
bubble=pet.bubble.grab().toImage();center=bubble.pixelColor(8,bubble.height()//2)
check('speech bubble background is opaque',center.alpha()==255 and center.green()>center.red())
pet.bubble.grab().save(str(root/'qa/v11-bubble.png'))
canvas=QImage(3840,2160,QImage.Format.Format_ARGB32);canvas.fill(QColor('#eef2e9'))
p=QPainter(canvas)
# Physical-pixel comparison on a 4K output surface, same 3x size / DPR 2.
old=QImage(str(root/'art-source/si-atlas.png')).copy(0,0,192,208).scaled(576,624,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation).scaled(1152,1248)
p.drawImage(400,500,old)
new=sprites.pixmap('idle',0,3,2).toImage();new.setDevicePixelRatio(1);p.drawImage(2200,500,new)
p.setPen(QColor('#183b35'));font=p.font();font.setPixelSize(42);p.setFont(font)
p.drawText(400,420,'1.0 / 3x / 4K 200%');p.drawText(2200,420,'1.1 / 3x / 4K 200%');p.end()
canvas.save(str(root/'qa/v11-4k-comparison.png'))
for state in ('idle','walk-right','walk-left','look'):
    count=len(sprites.frames[state]);sheet=QImage(384*count,416,QImage.Format.Format_ARGB32);sheet.fill(QColor('#edf3ed'));p=QPainter(sheet)
    for i in range(count):p.drawPixmap(i*384,0,sprites.pixmap(state,i,2))
    p.end();sheet.save(str(root/f'qa/v11-{state}-contact.png'))
pet.bubble.hide();pet.hide()
(root/'qa/v11-report.json').write_text(json.dumps({'ok':True,'checks':results,'count':len(results)},indent=2),encoding='utf-8')
print(json.dumps(results,indent=2))
