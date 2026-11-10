"""Opt-in packaged-app diagnostic, using only the smoke-test's isolated saves."""
import threading
import traceback
from PySide6.QtCore import QObject,Signal,Slot,QTimer
from .storage import Store
from .sync_transport import pair,exchange


class LanProbe(QObject):
    finished=Signal(object)

    def __init__(self,panel,directory):
        super().__init__(panel)
        self.panel=panel
        self.directory=directory
        self.done=False
        self.report={'ok':False}
        self.finished.connect(self.complete)
        self.timeout=QTimer(self);self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda:self.complete({'ok':False,'error':'LAN probe timeout'}))

    def start(self):
        self.timeout.start(18000)
        try:
            self.panel.open_lan_sync()
            self.manager=self.panel.lan_manager
            invitation=self.manager.invitation('127.0.0.1',port=0)
            remote=Store(self.directory/'probe-peer'/'settings.json')
            remote.enable_sync_preview()
            def worker():
                try:
                    peer=pair(invitation,remote.sync_ledger.device_id,'隔离检查设备')
                    remote.record_sync('pat')
                    remote.log('便携程序局域网检查')
                    remote.import_sync_bundle(exchange(peer,remote.sync_ledger.device_id,remote.sync_ledger.export()))
                    self.finished.emit({'ok':True,'remote_pats':remote['pats'],
                                        'tls_paired':True,'exchange_completed':True})
                except Exception as exc:
                    self.finished.emit({'ok':False,'error':str(exc),'traceback':traceback.format_exc()})
            threading.Thread(target=worker,name='SiFrozenLanProbe',daemon=True).start()
        except Exception as exc:
            self.complete({'ok':False,'error':str(exc),'traceback':traceback.format_exc()})

    @Slot(object)
    def complete(self,result):
        if self.done:
            return
        self.timeout.stop()
        if result.get('ok'):
            result['main_store_pats']=self.panel.ctrl.store['pats']
            result['ok']=result['main_store_pats']==result['remote_pats']==1
        self.report=result
        self.done=True
        if getattr(self.panel,'lan_manager',None):
            self.panel.lan_manager.stop()
            self.panel.lan_dialog.hide()
