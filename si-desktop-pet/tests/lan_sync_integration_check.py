"""Actual Qt UI + two isolated stores + pinned HTTPS sockets on loopback."""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication,QPushButton
from si_pet.storage import Store
from si_pet.sprites import Sprites
from si_pet.controller import Controller
from si_pet.pet_window import PetWindow
from si_pet.panel import Panel
from si_pet.sync_lan_ui import LanSync


app=QApplication([])
app.setQuitOnLastWindowClosed(False)
def until(predicate,timeout=8):
    deadline=time.monotonic()+timeout
    while not predicate():
        app.processEvents()
        if time.monotonic()>deadline:
            raise AssertionError('Timed out waiting for Qt/network completion')
        time.sleep(.005)
    app.processEvents()

with tempfile.TemporaryDirectory(dir=ROOT/'qa'/'tmp') as directory:
    root=Path(directory)
    a=Store(root/'a'/'settings.json');b=Store(root/'b'/'settings.json')
    a.enable_sync_preview();b.enable_sync_preview()
    sprites=Sprites()
    ca=Controller(a,sprites);cb=Controller(b,sprites)
    host=LanSync(cb)
    assert host.server is None and not host.timer.isActive()
    gui_thread=threading.get_ident()
    imports=[]
    original=b.import_sync_bundle
    def checked_import(data):
        assert threading.get_ident()==gui_thread,'Network thread wrote the GUI store'
        imports.append(True)
        return original(data)
    b.import_sync_bundle=checked_import
    invitation=host.invitation('127.0.0.1',port=0)
    pet=PetWindow(ca,lambda:None)
    panel=Panel(ca,pet)
    panel.open_lan_sync()
    client=panel.lan_manager;dialog=panel.lan_dialog
    dialog.join.setPlainText(invitation)
    next(w for w in dialog.findChildren(QPushButton) if w.text()=='连接对方设备').click()
    until(lambda:not client.busy)
    assert len(client.peers)==1,dialog.message.text()
    assert host.registry.authorized(a.sync_ledger.device_id,next(iter(client.peers.values()))['key'])
    ca.pat();cb.feed()
    next(w for w in dialog.findChildren(QPushButton) if w.text()=='现在同步').click()
    until(lambda:not client.busy)
    assert a.sync_ledger.summary()==b.sync_ledger.summary(),dialog.message.text()
    assert panel.stat_labels[0].text()=='4'
    assert imports
    dialog.automatic.setChecked(True)
    client.timer.setInterval(30)
    assert client.timer.isActive()
    ca.last_pat-=2;ca.pat()
    until(lambda:b['pats']==2 and not client.busy)
    dialog.automatic.setChecked(False)
    assert not client.timer.isActive()
    until(lambda:not client.busy)
    assert json.loads(client.path.read_text(encoding='utf-8'))['automatic'] is False
    host.registry.revoke(a.sync_ledger.device_id)
    before=b.sync_ledger.export()
    ca.last_pat-=2;ca.pat();client.sync_all()
    until(lambda:not client.busy)
    assert '失效' in dialog.message.text(),repr(dialog.message.text())
    assert before==b.sync_ledger.export()
    dialog.grab().save(str(ROOT/'qa'/'lan-sync-dialog.png'))
    client.stop();host.stop()
    dialog.hide();panel.hide();pet.timer.stop();pet.hide();pet.bubble.hide()
report={'ok':True,'tls_loopback_exchange':True,'store_writes_on_gui_thread':True,
        'panel_counts_refreshed':True,'automatic_timer_exchange':True,'revocation_rejected':True,
        'physical_devices_tested':1,'second_pc_android_harmony_verified':False}
(ROOT/'qa'/'lan-sync-integration.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
