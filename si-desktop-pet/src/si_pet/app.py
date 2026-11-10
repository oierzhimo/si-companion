import argparse
import ctypes
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from PySide6.QtCore import QTimer,QAbstractNativeEventFilter
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QLocalServer,QLocalSocket
from PySide6.QtWidgets import QApplication,QSystemTrayIcon,QMessageBox
from .storage import Store,data_root
from .sprites import Sprites
from .controller import Controller
from .pet_window import PetWindow,request_exit
from .panel import Panel
from .voice import VoiceEngine
from . import native,__version__

class RecoveryHotkey(QAbstractNativeEventFilter):
    def __init__(self,fn):super().__init__();self.fn=fn
    def nativeEventFilter(self,event_type,message):
        if native.WINDOWS:
            msg=ctypes.wintypes.MSG.from_address(int(message))
            if msg.message==0x0312 and msg.wParam==0x5351:
                QTimer.singleShot(0,self.fn);return True,0
        return False,0


def configure_logging():
    """Keep the pet usable if a portable log directory disappears or is locked."""
    candidates=[]
    try:
        candidates.append(data_root()/'app.log')
    except OSError:
        pass
    candidates.append(Path(os.environ.get('LOCALAPPDATA',tempfile.gettempdir()))/'SiDesktopPet'/'app.log')
    candidates.append(Path(tempfile.gettempdir())/'SiDesktopPet'/'app.log')
    logger=logging.getLogger()
    logger.setLevel(logging.INFO)
    for path in dict.fromkeys(candidates):
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            handler=logging.FileHandler(path,encoding='utf-8')
        except OSError:
            continue
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
        return path
    logger.addHandler(logging.StreamHandler(sys.stderr))
    return None

def main(argv=None):
    parser=argparse.ArgumentParser(description='祀 · 独立桌面伙伴')
    parser.add_argument('--quiet',action='store_true')
    parser.add_argument('--smoke-test',type=Path)
    parser.add_argument('--render-preview',type=Path)
    parser.add_argument('--sync-preview',action='store_true',help='Opt-in local event recording prototype')
    parser.add_argument('--check-lan',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--check-genai-live',type=Path,metavar='ABSOLUTE_REPORT_PATH',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if args.check_lan and not (args.smoke_test and args.sync_preview):
        parser.error('--check-lan requires an isolated --smoke-test with --sync-preview')
    if args.check_genai_live is not None:
        if not args.check_genai_live.is_absolute():
            parser.error('--check-genai-live requires an absolute report path')
        if args.quiet or args.smoke_test or args.render_preview or args.sync_preview or args.check_lan:
            parser.error('--check-genai-live must be used alone')
        # Development-only live acceptance path.  It deliberately returns
        # before logging, single-instance handling, settings, the pet, voice,
        # tray, hotkeys, or any proxy lifecycle code.
        from .genai_frozen_probe import run_frozen_genai_probe
        app=QApplication(sys.argv[:1]);app.setApplicationName('SiDesktopPet');app.setOrganizationName('SiCompanion')
        return run_frozen_genai_probe(app,args.check_genai_live)
    app=QApplication(sys.argv[:1]);app.setApplicationName('SiDesktopPet');app.setOrganizationName('SiCompanion')
    app.setQuitOnLastWindowClosed(False)
    logpath=configure_logging()
    def excepthook(kind,value,tb):
        logging.error('Unhandled exception',exc_info=(kind,value,tb))
        # Avoid recurring modal dialogs from a timer; a single log preserves recovery.
        if not getattr(excepthook,'reported',False):
            excepthook.reported=True
            location=f'详情已保存在：\n{logpath}' if logpath else '日志文件暂时无法写入。'
            QMessageBox.critical(None,'祀需要一点帮助',f'运行遇到问题。\n{location}\n\n{value}')
    sys.excepthook=excepthook
    server=None
    if not args.smoke_test and not args.render_preview:
        probe=QLocalSocket();probe.connectToServer('SiDesktopPet-Local-1')
        if probe.waitForConnected(350):
            probe.write(b'show');probe.waitForBytesWritten(350);probe.disconnectFromServer()
            if args.sync_preview:
                QMessageBox.warning(None,'协同预览未启动','已有桌宠实例正在运行。请先退出旧实例，再用协同预览入口启动。')
            return 0
        QLocalServer.removeServer('SiDesktopPet-Local-1');server=QLocalServer();server.listen('SiDesktopPet-Local-1')
    test_data=(args.smoke_test or args.render_preview)
    store=Store(test_data.parent/'qa-settings.json' if test_data else None)
    if args.sync_preview:store.enable_sync_preview()
    sprites=Sprites();ctrl=Controller(store,sprites)
    voice=VoiceEngine(store);ctrl.voice_speech.connect(voice.speak)
    panel=None
    def show_panel():panel.show();panel.raise_();panel.activateWindow()
    pet=PetWindow(ctrl,show_panel);panel=Panel(ctrl,pet,voice)
    lan_probe=None
    if args.check_lan:
        from .lan_probe import LanProbe
        lan_probe=LanProbe(panel,args.smoke_test.parent)
        QTimer.singleShot(100,lan_probe.start)
    pet.show();pet.tick()
    tray=QSystemTrayIcon(sprites.icon(),app);tray.setToolTip('祀 · 桌面伙伴')
    menu=pet.menu();tray.setContextMenu(menu)
    def tray_activate(reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,QSystemTrayIcon.ActivationReason.DoubleClick):show_panel()
    tray.activated.connect(tray_activate)
    if QSystemTrayIcon.isSystemTrayAvailable():tray.show()
    else:panel.can_hide=False
    def completed():
        if tray.isVisible() and not ctrl.presentation_suppressed:tray.showMessage('专注完成','抬头看看远处，给眼睛放个假。',QSystemTrayIcon.MessageIcon.Information,5000)
    ctrl.focus_done.connect(completed)
    if server:
        connections=[]
        def incoming():
            sock=server.nextPendingConnection();connections.append(sock)
            show_panel()
            sock.readyRead.connect(lambda:sock.readAll())
            sock.disconnected.connect(sock.deleteLater)
        server.newConnection.connect(incoming)
    hotkey=RecoveryHotkey(pet.recover);app.installNativeEventFilter(hotkey)
    registered=native.WINDOWS and native.user.RegisterHotKey(None,0x5351,0x0001|0x0002|0x4000,0x53)
    ctrl.hotkey_available=bool(registered)
    if not registered:logging.info('Global recovery hotkey unavailable; tray recovery remains enabled')
    poll=QTimer();poll.timeout.connect(ctrl.refresh_platforms);poll.start(100)
    # Display topology changes invalidate every previous platform and clamp the pet safely.
    def display_changed(*_):ctrl.refresh_platforms();ctrl.clamp_recovery()
    def wire_screen(screen):
        try:screen.availableGeometryChanged.connect(display_changed)
        except (RuntimeError,TypeError):pass
    def screen_added(screen):wire_screen(screen);display_changed()
    app.screenAdded.connect(screen_added);app.screenRemoved.connect(display_changed)
    for screen in app.screens():wire_screen(screen)
    def shutdown():
        if getattr(panel,'lan_manager',None):panel.lan_manager.stop()
        b=ctrl.physics.body;store['last_x']=b.x;store['last_y']=b.y
        store['total_seconds']+=max(0,time.monotonic()-ctrl.last_save)
        store.save();voice.stop();tray.hide();pet.bubble.hide()
        if registered:native.user.UnregisterHotKey(None,0x5351)
        if server:server.close()
    app.aboutToQuit.connect(shutdown)
    if not args.quiet:show_panel()
    if args.render_preview:
        def render():
            panel.show();panel.resize(1000,740)
            args.render_preview.parent.mkdir(parents=True,exist_ok=True)
            panel.grab().save(str(args.render_preview));request_exit()
        QTimer.singleShot(450,render)
    if args.smoke_test:
        def smoke():
            if lan_probe and not lan_probe.done:
                QTimer.singleShot(100,smoke);return
            b=ctrl.physics.body
            report={'ok':not getattr(excepthook,'reported',False),'version':__version__,'screens':[{'name':s.name(),'geometry':[s.geometry().x(),s.geometry().y(),s.geometry().width(),s.geometry().height()],'dpr':s.devicePixelRatio()} for s in app.screens()],
                    'platforms':len(ctrl.physics.platforms),'state':ctrl.state,'position':[b.x,b.y],'scale':store['scale'],'animations':{k:len(v) for k,v in sprites.frames.items()},'tray_available':QSystemTrayIcon.isSystemTrayAvailable(),'recovery_hotkey':bool(registered)}
            if lan_probe:
                report['lan_check']=lan_probe.report
                report['ok']=report['ok'] and lan_probe.report.get('ok',False)
            # Exercise the optional dialog's frozen imports and Qt construction
            # in the isolated smoke directory, without contacting a model.
            from .conversation_ui import ConversationDialog
            dialog=ConversationDialog(ctrl,store.path.parent/'qa-conversation.json',panel)
            report['conversation_ui_constructed']=True
            args.smoke_test.parent.mkdir(parents=True,exist_ok=True)
            if not dialog.grab().save(str(args.smoke_test.parent/'conversation-preview.png')):
                raise RuntimeError('Conversation preview could not be saved')
            dialog.close()
            args.smoke_test.parent.mkdir(parents=True,exist_ok=True)
            args.smoke_test.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');request_exit()
        QTimer.singleShot(2500,smoke)
    logging.info('Started independent Si desktop pet')
    return app.exec()
