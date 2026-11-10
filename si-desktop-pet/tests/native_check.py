import sys,json,time,subprocess
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from PySide6.QtWidgets import QApplication
from si_pet.native import window_platforms
root=Path(__file__).resolve().parents[1];app=QApplication([]);info=root/'qa/window-fixture.json'
if info.exists():info.unlink()
child=subprocess.Popen([sys.executable,str(root/'tests/window_fixture.py'),str(info)])
samples=[];start=time.monotonic()
while time.monotonic()-start<4:
    app.processEvents();time.sleep(.15)
    if info.exists():
        hwnd=json.loads(info.read_text())['hwnd']
        rows=[p for p in window_platforms(app.screens()) if p.key.startswith(f'window:{hwnd}:')]
        filtered=[p for p in window_platforms(app.screens(),headroom=100000) if p.key.startswith(f'window:{hwnd}:')]
        samples.append({'elapsed':round(time.monotonic()-start,2),'ledges':[{'left':p.left,'right':p.right,'top':p.top} for p in rows],'headroom_ledges':len(filtered)})
child.wait(timeout=10)
visible=[r for r in samples if r['ledges']]
heights={round(r['ledges'][0]['top']) for r in visible}
checks={'ordinary_window_detected':bool(visible),'moving_window_geometry_updates':len(heights)>1,'minimized_window_removed':bool(samples) and not samples[-1]['ledges'],'headroom_filters_high_ledges':all(r['headroom_ledges']==0 for r in samples)}
report={'ok':all(checks.values()),'checks':checks,'samples':samples}
(root/'qa/native-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'ok':report['ok'],'checks':checks,'observed_ledge_heights':sorted(heights)},indent=2))
raise SystemExit(0 if report['ok'] else 1)
