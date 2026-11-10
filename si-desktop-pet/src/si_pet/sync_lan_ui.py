"""GUI owner for LAN workers; the Store is only touched on Qt's main thread."""
import json
import socket
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject,Signal,Slot,QTimer,Qt
from PySide6.QtWidgets import (QApplication,QDialog,QVBoxLayout,QHBoxLayout,QLabel,
    QPushButton,QLineEdit,QPlainTextEdit,QListWidget,QListWidgetItem,QCheckBox,QSpinBox,QWidget)

from .sync_transport import (PeerRegistry,LanServer,TransportError,local_address,
                             pair,exchange,write_json,parse_invitation)


class LanSync(QObject):
    status=Signal(str)
    changed=Signal()
    incoming=Signal(object)
    result=Signal(object)
    paired=Signal()

    def __init__(self,controller,parent=None):
        super().__init__(parent)
        self.ctrl=controller
        self.store=controller.store
        if self.store.sync_ledger is None:
            raise TransportError('请先使用协同预览入口启动')
        self.directory=self.store.path.with_suffix('.lan')
        self.registry=PeerRegistry(self.directory,self.store.sync_ledger.device_id,socket.gethostname()[:60])
        self.path=self.directory/'connections.json'
        saved=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        if not isinstance(saved,dict):
            raise TransportError('本机连接记录格式不正确')
        self.peers=saved.get('peers',{})
        if not isinstance(self.peers,dict):
            raise TransportError('本机连接记录格式不正确')
        for device,peer in self.peers.items():
            if (not isinstance(peer,dict) or peer.get('device_id')!=device
                    or not isinstance(peer.get('key'),str) or not 32<=len(peer['key'])<=128):
                raise TransportError('本机连接记录格式不正确')
            invitation={key:value for key,value in peer.items() if key!='key'}
            invitation.update(transport=1,invitation='x'*43)
            parse_invitation(json.dumps(invitation))
        self.server=None
        self.busy=False
        self.stopping=False
        self.incoming.connect(self.apply_incoming,Qt.ConnectionType.QueuedConnection)
        self.result.connect(self.apply_result,Qt.ConnectionType.QueuedConnection)
        self.timer=QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.sync_all)
        self.set_automatic(saved.get('automatic') is True,persist=False)

    def save(self):
        write_json(self.path,{'automatic':self.timer.isActive(),'peers':self.peers})

    def set_automatic(self,enabled,persist=True):
        if enabled:
            self.timer.start()
        else:
            self.timer.stop()
        if persist:
            self.save()

    def _receive(self,data):
        job={'data':data,'done':threading.Event()}
        self.incoming.emit(job)
        if not job['done'].wait(10):
            raise TimeoutError('界面暂时忙碌，请重试')
        if 'error' in job:
            raise job['error']
        return job['response']

    @Slot(object)
    def apply_incoming(self,job):
        try:
            if self.stopping:
                raise TransportError('桌宠正在退出')
            added=self.store.import_sync_bundle(job['data'])
            job['response']=self.store.sync_ledger.export()
            self.ctrl.changed.emit()
            self.status.emit(f'收到对方记录，合并 {added} 条新事件。')
            self.changed.emit()
        except Exception as exc:
            job['error']=exc
        finally:
            job['done'].set()

    def invitation(self,host,port=39591):
        host=local_address(host)
        if self.server is None:
            self.server=LanServer(self.registry,self._receive,
                                  host='127.0.0.1' if host.startswith('127.') else '0.0.0.0',port=port)
        return self.server.invitation(host)

    def stop_receiving(self):
        if self.server:
            self.server.close()
            self.server=None
        self.status.emit('已停止接收局域网连接。')

    def work(self,kind,fn):
        if self.busy or self.stopping:
            self.status.emit('正在处理上一项连接，请稍候。');return
        self.busy=True
        self.status.emit('正在连接对方…')
        def run():
            try:
                outcome={'kind':kind,'value':fn()}
            except Exception as exc:
                outcome={'kind':kind,'error':str(exc)}
            self.result.emit(outcome)
        threading.Thread(target=run,name='SiLanClient',daemon=True).start()

    def pair_with(self,invitation):
        self.work('pair',lambda:pair(invitation,self.registry.device_id,self.registry.name))

    def sync_all(self):
        if self.busy or self.stopping:
            return
        if not self.peers:
            self.status.emit('还没有可连接的设备，请先粘贴对方邀请进行配对。');return
        bundle=self.store.sync_ledger.export()
        peers=[dict(peer) for peer in self.peers.values()]
        def run():
            results=[]
            for peer in peers:
                try:
                    results.append({'name':peer['name'],'bundle':exchange(peer,self.registry.device_id,bundle)})
                except Exception as exc:
                    results.append({'name':peer.get('name','对方设备'),'error':str(exc)})
            return results
        self.work('sync',run)

    @Slot(object)
    def apply_result(self,outcome):
        self.busy=False
        if self.stopping:
            return
        try:
            if 'error' in outcome:
                raise TransportError(outcome['error'])
            if outcome['kind']=='pair':
                peer=outcome['value']
                self.peers[peer['device_id']]=peer
                self.save()
                self.status.emit(f'已连接 {peer["name"]}，现在可以同步记录。')
                self.paired.emit()
            else:
                added=0
                successes=0
                failures=[]
                for result in outcome['value']:
                    if 'error' in result:
                        failures.append(result['name']+'：'+result['error']);continue
                    try:
                        added+=self.store.import_sync_bundle(result['bundle'])
                        successes+=1
                    except (ValueError,OSError) as exc:
                        failures.append(result['name']+'：'+str(exc))
                self.ctrl.changed.emit()
                message=f'同步完成，合并 {added} 条新事件。' if successes else '未完成同步。'
                self.status.emit(message+('；'.join(failures) if failures else ''))
            self.changed.emit()
        except (ValueError,OSError) as exc:
            self.status.emit('未完成：'+str(exc))

    def forget(self,direction,device):
        if direction=='receive':
            self.registry.revoke(device)
        else:
            self.peers.pop(device,None)
            self.save()
        self.changed.emit()

    def stop(self):
        self.stopping=True
        self.timer.stop()
        self.stop_receiving()


def suggested_address():
    try:
        for item in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET):
            address=local_address(item[4][0])
            if not address.startswith('127.'):
                return address
    except (OSError,ValueError):
        pass
    return '127.0.0.1'


class LanSyncDialog(QDialog):
    def __init__(self,manager,parent=None):
        super().__init__(parent)
        self.manager=manager
        self.setWindowTitle('祀 · 设备配对与同步')
        self.resize(700,730)
        layout=QVBoxLayout(self)
        info=QLabel('把相伴记录带到另一台电脑。两台电脑需在同一局域网；只合并互动与手记。')
        info.setWordWrap(True);layout.addWidget(info)
        advanced=QPushButton('连接设置');layout.addWidget(advanced)
        options=QWidget();row=QHBoxLayout(options)
        row.addWidget(QLabel('本机地址'))
        self.host=QLineEdit(suggested_address());row.addWidget(self.host,1)
        row.addWidget(QLabel('端口'))
        self.port=QSpinBox();self.port.setRange(1024,65535);self.port.setValue(39591);row.addWidget(self.port)
        layout.addWidget(options);options.hide()
        advanced.clicked.connect(lambda:options.setVisible(not options.isVisible()))
        row=QHBoxLayout()
        make=QPushButton('生成 3 分钟配对邀请');make.clicked.connect(self.make_invitation);row.addWidget(make)
        stop=QPushButton('停止接收');stop.clicked.connect(self.stop_receiving);row.addWidget(stop)
        layout.addLayout(row)
        self.offer=QPlainTextEdit();self.offer.setReadOnly(True);self.offer.setMaximumHeight(90)
        self.offer.hide()
        self.offer_summary=QLabel('生成邀请后，复制到另一台电脑即可配对。');layout.addWidget(self.offer_summary)
        self.copy=QPushButton('复制本机邀请');self.copy.setEnabled(False)
        self.copy.clicked.connect(lambda:QApplication.clipboard().setText(self.offer.toPlainText()));layout.addWidget(self.copy)
        self.join=QPlainTextEdit();self.join.setMaximumHeight(90)
        self.join.setPlaceholderText('或在这里粘贴另一台电脑的邀请。')
        layout.addWidget(self.join)
        connect=QPushButton('连接对方设备');connect.clicked.connect(lambda:manager.pair_with(self.join.toPlainText().strip()));layout.addWidget(connect)
        self.devices=QListWidget();layout.addWidget(self.devices,1)
        row=QHBoxLayout()
        sync=QPushButton('现在同步');sync.clicked.connect(manager.sync_all);row.addWidget(sync)
        forget=QPushButton('移除选中的连接 / 授权');forget.clicked.connect(self.forget);row.addWidget(forget)
        layout.addLayout(row)
        self.automatic=QCheckBox('每分钟自动连接已配对设备（可随时关闭）')
        self.automatic.setChecked(manager.timer.isActive())
        self.automatic.toggled.connect(self.set_automatic);layout.addWidget(self.automatic)
        self.message=QLabel('尚未开启接收。关闭此窗口后，已开启的接收和自动同步会继续；退出桌宠后停止。')
        self.message.setWordWrap(True);self.message.setObjectName('lan-status');layout.addWidget(self.message)
        manager.status.connect(self.message.setText)
        manager.paired.connect(self.join.clear)
        manager.changed.connect(self.refresh)
        self.refresh_timer=QTimer(self);self.refresh_timer.timeout.connect(self.refresh);self.refresh_timer.start(1000)
        self.refresh()

    def make_invitation(self):
        try:
            self.offer.setPlainText(self.manager.invitation(self.host.text().strip(),self.port.value()))
            self.refresh()
            self.message.setText('已开启接收。将邀请交给另一台电脑；仅向你授权的设备分享。')
        except (ValueError,OSError) as exc:
            self.message.setText('未能开始接收：'+str(exc))

    def stop_receiving(self):
        self.manager.stop_receiving()
        self.offer.clear();self.refresh()

    def set_automatic(self,enabled):
        try:
            self.manager.set_automatic(enabled)
        except OSError as exc:
            self.message.setText('未能保存自动同步设置：'+str(exc))

    def forget(self):
        item=self.devices.currentItem()
        if item:
            try:
                self.manager.forget(*item.data(Qt.ItemDataRole.UserRole))
            except OSError as exc:
                self.message.setText('未能移除：'+str(exc))

    def refresh(self):
        invite=self.manager.registry.invitation
        remaining=max(0,int(invite['deadline']-time.monotonic())) if invite else 0
        self.copy.setEnabled(remaining>0 and bool(self.offer.toPlainText()))
        self.offer_summary.setText(f'邀请剩余 {remaining} 秒有效，仅可使用一次。' if remaining else '没有有效邀请；需要时可重新生成。')
        selected=self.devices.currentItem()
        current=selected.data(Qt.ItemDataRole.UserRole) if selected else None
        rows=[('connect',device,peer['name']) for device,peer in self.manager.peers.items()]
        with self.manager.registry.lock:
            rows += [('receive',device,peer['name']) for device,peer in self.manager.registry.peers.items()]
        signature=tuple(rows)
        if signature==getattr(self,'_rows',None):
            return
        self._rows=signature;self.devices.clear()
        for direction,device,name in rows:
            label=('本机可连接：' if direction=='connect' else '已允许对方连接：')+name
            item=QListWidgetItem(label);item.setData(Qt.ItemDataRole.UserRole,(direction,device))
            self.devices.addItem(item)
            if current==(direction,device):
                self.devices.setCurrentItem(item)
