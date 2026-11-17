"""One controlled native window in a child process; no screenshots, external-app input or user save."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel
from si_pet import native
from si_pet.controller import Controller
from si_pet.pet_window import PetWindow
from si_pet.sprites import Sprites
from si_pet.storage import Store


def _run_peer_child(state_file: Path, cmd_file: Path):
    app = QApplication([])
    peer = QLabel('祀 · 运行舒适度受控测试\n约六秒后自动关闭')
    peer.setWindowTitle('Si controlled visibility pause')
    peer.setStyleSheet('background:#e1eadf;color:#244839;font:24px "Microsoft YaHei UI";padding:40px')
    peer.resize(760, 420)
    peer.show()
    peer.raise_()
    peer.activateWindow()
    app.processEvents()
    state_file.write_text(json.dumps({'hwnd': int(peer.winId()), 'pid': os.getpid()}), encoding='utf-8')
    last_cmd = ''

    def poll():
        nonlocal last_cmd
        if not cmd_file.exists():
            return
        cmd = cmd_file.read_text(encoding='utf-8').strip()
        if cmd == last_cmd:
            return
        last_cmd = cmd
        if cmd == 'full':
            peer.showFullScreen()
            peer.raise_()
            peer.activateWindow()
        elif cmd == 'restore':
            peer.showNormal()
            peer.raise_()
            peer.activateWindow()
        elif cmd == 'quit':
            peer.close()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(40)
    QTimer.singleShot(45000, app.quit)
    app.exec()


if len(sys.argv) == 4 and sys.argv[1] == '--peer-child':
    _run_peer_child(Path(sys.argv[2]), Path(sys.argv[3]))
    raise SystemExit(0)

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
tmp = tempfile.TemporaryDirectory(prefix='si-visibility-native-')
state_file = Path(tmp.name) / 'peer-state.json'
cmd_file = Path(tmp.name) / 'peer-cmd.txt'
store = Store(Path(tmp.name) / 'settings.json')
store['window_edges'] = False
store['bubbles'] = False
store['voice_enabled'] = False
ctrl = Controller(store, Sprites())
g = app.primaryScreen().availableGeometry()
b = ctrl.physics.body
b.x = g.center().x()
b.y = g.bottom() - 6 * store['scale']
b.support = next(p.key for p in ctrl.physics.platforms if p.floor and p.left <= b.x <= p.right)
b.vx = 65
b.vy = 0
ctrl.action = 'walk'
ctrl.direction = 1
ctrl.decision_at = time.monotonic() + 60
ctrl.last_save = time.monotonic() + 600
pet = PetWindow(ctrl, lambda: None)
pet.show()

child = subprocess.Popen([sys.executable, __file__, '--peer-child', str(state_file), str(cmd_file)])
peer_hwnd = 0
report = {'physical_multi_monitor_tested': False, 'game_tested': False, 'screen_capture': False, 'phases': {}}


def _fg_hwnd() -> int:
    return int(native.user.GetForegroundWindow() or 0)


def record(name):
    report['phases'][name] = {
        'position': [b.x, b.y],
        'velocity': [b.vx, b.vy],
        'suppressed': ctrl.presentation_suppressed,
        'pet_visible': pet.isVisible(),
        'action': ctrl.action,
        'fixture_foreground': bool(peer_hwnd and _fg_hwnd() == peer_hwnd),
    }


def send_cmd(cmd: str):
    cmd_file.write_text(cmd, encoding='utf-8')


started = False


def begin_when_foreground():
    global started, peer_hwnd
    if not peer_hwnd and state_file.exists():
        try:
            peer_hwnd = int(json.loads(state_file.read_text(encoding='utf-8'))['hwnd'])
        except Exception:
            return
    if started or not peer_hwnd or _fg_hwnd() != peer_hwnd:
        return
    started = True
    QTimer.singleShot(800, lambda: send_cmd('full'))
    QTimer.singleShot(2000, lambda: record('hidden_start'))
    QTimer.singleShot(3200, lambda: record('hidden_end'))
    QTimer.singleShot(3500, lambda: send_cmd('restore'))
    QTimer.singleShot(4500, lambda: record('restored'))
    QTimer.singleShot(4900, app.quit)


wait_foreground = QTimer()
wait_foreground.timeout.connect(begin_when_foreground)
wait_foreground.start(100)
QTimer.singleShot(8000, app.quit)
app.exec()
send_cmd('quit')
try:
    child.wait(timeout=3)
except Exception:
    child.kill()
pet.timer.stop()
pet.bubble.close()
pet.close()
phases = report['phases']
if set(phases) == {'hidden_start', 'hidden_end', 'restored'}:
    start, end, shown = (phases[k] for k in ('hidden_start', 'hidden_end', 'restored'))
    report['passed'] = (
        start['suppressed'] and end['suppressed']
        and not start['pet_visible'] and not end['pet_visible']
        and start['position'] == end['position']
        and shown['pet_visible'] and not shown['suppressed']
        and shown['action'] == 'idle'
        and all(p['fixture_foreground'] for p in phases.values())
    )
else:
    report['passed'] = False
(ROOT / 'qa/native-visibility-pause.json').parent.mkdir(parents=True, exist_ok=True)
(ROOT / 'qa/native-visibility-pause.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
tmp.cleanup()
print(json.dumps(report), flush=True)
raise SystemExit(0 if report['passed'] else 1)
