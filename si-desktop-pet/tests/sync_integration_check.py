"""Exercise opt-in sync event recording through actual controller actions."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtWidgets import QApplication
from si_pet.storage import Store
from si_pet.sprites import Sprites
from si_pet.controller import Controller
from si_pet.sync_v1 import Ledger
from si_pet.pet_window import PetWindow
from si_pet.panel import Panel
from PySide6.QtWidgets import QPushButton

root=Path(__file__).resolve().parents[1]
temp_root=root/'qa'/'tmp'
temp_root.mkdir(parents=True,exist_ok=True)
app=QApplication([])
with tempfile.TemporaryDirectory(dir=temp_root) as directory:
    path=Path(directory)/'settings.json'
    store=Store(path)
    ledger=store.enable_sync_preview()
    ctrl=Controller(store,Sprites())
    ctrl.pat()
    ctrl.feed()
    ctrl.focus(1)
    ctrl.focus_end=time.monotonic()-.1
    ctrl.update(.016)
    assert (store['pats'],store['snacks'],store['focus_sessions'],store['affinity'])==(1,1,1,9)
    assert (ledger.summary()['pats'],ledger.summary()['snacks'],ledger.summary()['focus_sessions'],ledger.summary()['affinity'])==(1,1,1,9)
    assert len([event for event in ledger.events.values() if event['kind']=='journal'])>=2
    pet=PetWindow(ctrl,lambda:None)
    panel=Panel(ctrl,pet)
    panel.stack.setCurrentIndex(2)
    panel.show();app.processEvents()
    panel.grab().save(str(root/'qa'/'sync-preview-journal-panel.png'))
    labels={widget.text() for widget in panel.findChildren(QPushButton)}
    assert {'导出同步事件包','导入同步事件包'} <= labels
    remote=Ledger(Path(directory)/'remote.json','remote-pc')
    remote.save()  # A fresh device has no legacy migration baseline.
    remote.add('snack')
    assert store.import_sync_bundle(remote.export())==1
    panel.refresh()
    assert store['affinity']==12 and panel.stat_labels[0].text()=='12'
    store.save()
    assert Ledger(ledger.path).summary()==ledger.summary()
    assert Store(path).enable_sync_preview().summary()==ledger.summary()
    pet.timer.stop();pet.bubble.hide();pet.hide();panel.hide()
print('Opt-in controller interactions recorded and recovered from the event ledger.')
app.quit()
