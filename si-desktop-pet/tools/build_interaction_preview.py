"""Independent build entry for the 1.2.2 InteractionPreview."""
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
DIST=ROOT/'dist-1.2.2'
WORK=ROOT/'build-1.2.2'

def run(arguments):
    print('\n$', ' '.join(str(part) for part in arguments), flush=True)
    subprocess.run([str(part) for part in arguments],cwd=ROOT,check=True)

def main():
    from si_pet import __version__
    if __version__!='1.2.2':
        raise SystemExit('Source has advanced beyond 1.2.2; refuse to rebuild the frozen interaction preview.')
    try:
        import PySide6  # noqa: F401
        import PyInstaller  # noqa: F401
    except ImportError as exc:
        raise SystemExit('Use .venv\\Scripts\\python.exe with PySide6 and PyInstaller installed.') from exc
    run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_*.py','-v'])
    for script in ('integration_check.py','v11_check.py','preview_check.py','native_check.py'):
        run([sys.executable,str(ROOT/'tests'/script)])
    run([sys.executable,'-m','PyInstaller','--noconfirm','--clean',
         '--distpath',str(DIST),'--workpath',str(WORK),'SiDesktopPet.spec'])
    run([sys.executable,str(ROOT/'tools/make_interaction_preview.py'),
         '--built',str(DIST/'SiDesktopPet')])
    run([sys.executable,str(ROOT/'tools/verify_interaction_preview.py')])
    print('\nIndependent 1.2.2 InteractionPreview package is ready.')

if __name__=='__main__':
    main()
