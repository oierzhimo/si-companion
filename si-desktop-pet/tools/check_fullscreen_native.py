"""Controlled Windows fullscreen probe, no screen capture or external app input."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QLabel
from si_pet import native


def child(marker):
    app=QApplication([])
    window=QLabel('小祀全屏识别测试\n窗口将在数秒内自动关闭')
    window.setWindowTitle('Si controlled fullscreen check')
    window.setStyleSheet('background:#e1eadf;color:#244839;font:24px "Microsoft YaHei UI";padding:50px')
    window.showMaximized();window.raise_();window.activateWindow()
    def report(phase):
        marker.write_text(json.dumps({'phase':phase,'monitor':native.monitor_device_for_screen(window.screen()),'qt_name':window.screen().name(),'pid':os.getpid(),'hwnd':int(window.winId())}),encoding='utf-8')
    def fullscreen():
        window.showFullScreen();window.raise_();window.activateWindow()
        QTimer.singleShot(150,lambda:report('fullscreen'))
    QTimer.singleShot(200,lambda:report('maximized'))
    QTimer.singleShot(1250,fullscreen)
    QTimer.singleShot(2900,app.quit)
    return app.exec()


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--child':return child(Path(sys.argv[2]))
    if not native.WINDOWS:raise SystemExit('Windows only')
    app=QApplication([])  # Match the production Qt DPI-awareness context.
    report={'controlled_window_only':True,'screen_capture':False,'phases':{}}
    with tempfile.TemporaryDirectory() as folder:
        marker=Path(folder)/'phase.json'
        env=os.environ.copy();env.pop('QT_QPA_PLATFORM',None)
        proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child',str(marker)],env=env)
        try:
            deadline=time.monotonic()+6
            while proc.poll() is None and time.monotonic()<deadline:
                try:data=json.loads(marker.read_text(encoding='utf-8'))
                except (OSError,ValueError):time.sleep(.04);continue
                if data['phase'] in report['phases']:
                    app.processEvents();time.sleep(.04);continue
                pid=native.w.DWORD()
                hwnd=native.user.GetForegroundWindow()
                native.user.GetWindowThreadProcessId(hwnd,native.ctypes.byref(pid))
                observed=native._fullscreen_window_monitor(data['hwnd'])
                expected=data['monitor'] if data['phase']=='fullscreen' else None
                report['phases'][data['phase']]={'foreground_verified':pid.value==proc.pid,'qt_name':data['qt_name'],'window_style':native.user.GetWindowLongW(data['hwnd'],-16),'expected':expected,'observed':observed,'ok':observed==expected and (data['phase']!='fullscreen' or expected is not None)}
                app.processEvents();time.sleep(.04)
            if proc.poll() is None:proc.terminate()
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:proc.kill();proc.wait(timeout=5)
    report['classification_ok']=set(report['phases'])=={'maximized','fullscreen'} and all(p['ok'] for p in report['phases'].values())
    report['foreground_transition_verified']=report['classification_ok'] and all(p['foreground_verified'] for p in report['phases'].values())
    report['physical_multi_monitor_tested']=False
    output=ROOT/'qa'/'fullscreen-controlled-native.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report['classification_ok'] else 1


if __name__=='__main__':raise SystemExit(main())
