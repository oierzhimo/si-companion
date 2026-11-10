"""Windows integration: visible window ledges, tray recovery hotkey, startup opt-in."""
import ctypes
import os
import sys
from ctypes import wintypes as w
from .physics import Platform, visible_segments

WINDOWS = sys.platform=='win32'

if WINDOWS:
    user=ctypes.WinDLL('user32',use_last_error=True)
    dwm=ctypes.WinDLL('dwmapi',use_last_error=True)
    user.GetWindowLongW.argtypes=[w.HWND,ctypes.c_int]
    user.GetWindowLongW.restype=w.LONG
    user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
    user.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
    user.IsWindowVisible.argtypes=[w.HWND]
    user.IsIconic.argtypes=[w.HWND]
    user.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int]
    user.EnumWindows.argtypes=[ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM),w.LPARAM]
    dwm.DwmGetWindowAttribute.argtypes=[w.HWND,w.DWORD,ctypes.c_void_p,w.DWORD]
    user.RegisterHotKey.argtypes=[w.HWND,ctypes.c_int,w.UINT,w.UINT]
    user.UnregisterHotKey.argtypes=[w.HWND,ctypes.c_int]
    user.GetForegroundWindow.restype=w.HWND
    user.MonitorFromWindow.argtypes=[w.HWND,w.DWORD]
    user.MonitorFromWindow.restype=w.HMONITOR

def covers_monitor(rect, monitor, tolerance=2):
    """Full monitor, not just maximized work area; both rectangles physical."""
    return all(abs(a-b)<=tolerance for a,b in zip(rect,monitor))

def foreground_fullscreen_monitor():
    """Return a fullscreen foreground monitor's device name, without reading content."""
    if not WINDOWS:return None
    return _fullscreen_window_monitor(user.GetForegroundWindow())

def _fullscreen_window_monitor(hwnd):
    """Classify a known window; separate from foreground lookup for controlled QA."""
    if not WINDOWS:return None
    if not hwnd or not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd):return None
    # With an auto-hidden taskbar, an ordinary maximized window can cover the
    # full monitor too. A retained title bar identifies it as a normal window.
    if user.GetWindowLongW(hwnd,-16)&0x00c00000==0x00c00000:return None
    pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    if pid.value==os.getpid():return None
    cls=ctypes.create_unicode_buffer(256);user.GetClassNameW(hwnd,cls,256)
    if cls.value in {'Progman','WorkerW','Shell_TrayWnd','Shell_SecondaryTrayWnd'}:return None
    cloaked=w.DWORD()
    if dwm.DwmGetWindowAttribute(hwnd,14,ctypes.byref(cloaked),4)==0 and cloaked.value:return None
    rect=w.RECT()
    if not user.GetWindowRect(hwnd,ctypes.byref(rect)):return None
    monitor=user.MonitorFromWindow(hwnd,0)
    if not monitor:return None
    class MI(ctypes.Structure):
        _fields_=[('cbSize',w.DWORD),('rcMonitor',w.RECT),('rcWork',w.RECT),('dwFlags',w.DWORD),('szDevice',w.WCHAR*32)]
    info=MI();info.cbSize=ctypes.sizeof(info)
    user.GetMonitorInfoW.argtypes=[w.HMONITOR,ctypes.c_void_p]
    if not user.GetMonitorInfoW(monitor,ctypes.byref(info)):return None
    m=info.rcMonitor
    if covers_monitor((rect.left,rect.top,rect.right,rect.bottom),(m.left,m.top,m.right,m.bottom)):
        return info.szDevice
    return None

def monitor_device_for_screen(screen):
    """Qt may return a friendly display name rather than Win32's device name.

    On Windows Qt retains physical monitor origins while scaling the sizes.
    Match origins, never multiply a global origin by its DPR.
    """
    origins=monitor_origins()
    geo=screen.geometry()
    matches=[name for name,origin in origins.items() if origin==(geo.x(),geo.y())]
    return matches[0] if len(matches)==1 else None

def window_platforms(screens,headroom=8):
    if not WINDOWS: return []
    rows=[]
    callback_type=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    def collect(hwnd,_):
        pid=w.DWORD()
        user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value==os.getpid() or not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd): return True
        cls=ctypes.create_unicode_buffer(256);user.GetClassNameW(hwnd,cls,256)
        if cls.value in {'Progman','WorkerW','Shell_TrayWnd','Shell_SecondaryTrayWnd'}: return True
        ex_style=user.GetWindowLongW(hwnd,-20)
        # WS_EX_TRANSPARENT affects mouse input, not visibility: such an
        # overlay still hides the ledge underneath it, so keep it as an
        # occluder while refusing to use it as a platform.
        transparent=bool(ex_style&0x20)
        tool_window=bool(ex_style&0x80)
        cloaked=w.DWORD()
        if dwm.DwmGetWindowAttribute(hwnd,14,ctypes.byref(cloaked),4)==0 and cloaked.value: return True
        r=w.RECT()
        if not user.GetWindowRect(hwnd,ctypes.byref(r)): return True
        if r.right<=r.left or r.bottom<=r.top:return True
        platform_ok=not transparent and not tool_window and r.right-r.left>=100 and r.bottom-r.top>=60
        rows.append((int(hwnd),r.left,r.top,r.right,r.bottom,platform_ok))
        return True
    cb=callback_type(collect)
    user.EnumWindows(cb,0)
    # GetWindowRect is physical in a per-monitor-DPI-aware process. Convert using
    # the target monitor's scale and its actual physical origin (not origin*dpr).
    origins=monitor_origins();top_margin=max(8,headroom)
    result=[];higher=[]
    for hwnd,left,top,right,bottom,platform_ok in rows:
        physical=(left,top,right,bottom)
        if not platform_ok:
            higher.append((hwnd,*physical))
            continue
        covered=[(a,c) for _,a,b,c,d in higher if b<=top+5<d]
        for screen in screens:
            geo=screen.geometry();dpr=screen.devicePixelRatio()
            origin=origins.get(screen.name(),(geo.x(),geo.y()))
            physical_left,physical_top=origin
            physical_right=physical_left+geo.width()*dpr
            physical_bottom=physical_top+geo.height()*dpr
            if not physical_top<=top<physical_bottom:continue
            start=max(left,physical_left);end=min(right,physical_right)
            if end<=start:continue
            y=geo.y()+(top-physical_top)/dpr
            if not geo.top()+top_margin<y<screen.availableGeometry().bottom()-12:continue
            window_origin_x=geo.x()+(left-physical_left)/dpr
            for a,b in visible_segments(start,end,covered,min_width=40*dpr):
                lo=geo.x()+(a-physical_left)/dpr;hi=geo.x()+(b-physical_left)/dpr
                result.append(Platform(f'window:{hwnd}:{a-left}',lo,hi,y+1,origin_x=window_origin_x))
        higher.append((hwnd,*physical))
    return result

def monitor_origins():
    if not WINDOWS:return {}
    class MI(ctypes.Structure):
        _fields_=[('cbSize',w.DWORD),('rcMonitor',w.RECT),('rcWork',w.RECT),('dwFlags',w.DWORD),('szDevice',w.WCHAR*32)]
    cbtype=ctypes.WINFUNCTYPE(w.BOOL,w.HMONITOR,w.HDC,ctypes.POINTER(w.RECT),w.LPARAM)
    result={}
    def collect(monitor,dc,rect,data):
        info=MI();info.cbSize=ctypes.sizeof(info)
        if user.GetMonitorInfoW(monitor,ctypes.byref(info)):
            result[info.szDevice]=(info.rcMonitor.left,info.rcMonitor.top)
        return True
    user.GetMonitorInfoW.argtypes=[w.HMONITOR,ctypes.c_void_p]
    user.EnumDisplayMonitors.argtypes=[w.HDC,ctypes.c_void_p,cbtype,w.LPARAM]
    cb=cbtype(collect);user.EnumDisplayMonitors(None,None,cb,0)
    return result

def set_startup(enabled):
    if not WINDOWS:return
    import winreg
    if getattr(sys,'frozen',False): command=f'"{sys.executable}" --quiet'
    else: command=f'"{sys.executable}" "{os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),"main.py")}" --quiet'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        if enabled: winreg.SetValueEx(key,'SiDesktopPet',0,winreg.REG_SZ,command)
        else:
            try: winreg.DeleteValue(key,'SiDesktopPet')
            except FileNotFoundError:pass
