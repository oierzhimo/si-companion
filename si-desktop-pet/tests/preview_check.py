"""Regression for high-DPI centering, pose changes, resize and new instances."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtWidgets import QApplication
from si_pet.storage import Store
from si_pet.sprites import Sprites
from si_pet.controller import Controller
from si_pet.pet_window import PetWindow
from si_pet.panel import Panel,Preview

root=Path(__file__).resolve().parents[1];app=QApplication([])
store=Store(root/'qa/preview-settings.json');sprites=Sprites();ctrl=Controller(store,sprites)
class ScreenPreview(Preview):
    ratio=1
    def devicePixelRatioF(self):return self.ratio

checks=[]
for launch in range(2):
    preview=ScreenPreview(ctrl);preview.timer.stop()
    for size in ((310,350),(356,465),(600,700)):
        preview.resize(*size)
        for dpr in (1,1.5,2,3):
            preview.ratio=dpr
            for state in ('idle','sitting','sleeping','walk-left','walk-right','jumping'):
                pix,position,visible,area=preview.frame_layout(state,0)
                assert abs(visible.center().x()-area.center().x())<.01
                assert abs(visible.center().y()-area.center().y())<.01
                assert area.adjusted(-.01,-.01,.01,.01).contains(visible)
                assert pix.devicePixelRatio()==dpr
                # Actual physical alpha pixels must also map inside the picture area.
                from PySide6.QtCore import QRectF
                from PySide6.QtGui import QRegion
                alpha=QRectF(QRegion(pix.mask()).boundingRect())
                actual=QRectF(position.x()+alpha.x()/dpr,position.y()+alpha.y()/dpr,alpha.width()/dpr,alpha.height()/dpr)
                assert area.adjusted(-4,-4,4,4).contains(actual),(state,dpr,actual,area)
                assert abs(actual.center().x()-area.center().x())<4
                assert abs(actual.center().y()-area.center().y())<4
                checks.append({'launch':launch+1,'size':size,'dpr':dpr,'state':state,'ok':True})
    preview.close();preview.deleteLater()
pet=PetWindow(ctrl,lambda:None);pet.timer.stop();panel=Panel(ctrl,pet);panel.show();app.processEvents()
for state in ('idle','sitting','sleeping'):
    ctrl.state=state
    ctrl.animation=lambda s=state:(s,0)
    panel.grab().save(str(root/f'qa/v111-panel-{state}.png'))
panel.close();pet.bubble.hide();pet.hide()
(root/'qa/v111-preview-report.json').write_text(json.dumps({'ok':True,'cases':len(checks),'checks':checks},indent=2),encoding='utf-8')
print(f'{len(checks)} centering cases passed; startup, resize, poses and DPI verified.')
