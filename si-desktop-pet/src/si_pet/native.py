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


# ---------------------------------------------------------------------------
# Smart Auto-Voice Environment Detection: Headphones + Local Game Suppression
# ---------------------------------------------------------------------------

import time as _time

# Default competitive & immersive local game executables ("apex 彩六 等本地游戏")
BLOCKED_GAME_PROCESSES = frozenset({
    # Apex Legends
    'r5apex.exe',
    'r5apex_dx12.exe',
    # Rainbow Six Siege (彩虹六号：围攻)
    'rainbowsix.exe',
    'rainbowsix_vulkan.exe',
    'rainbowsix_be.exe',
    'rainbowsixgame.exe',
    # Tactical / Competitive FPS & Battle Royale
    'cs2.exe',
    'csgo.exe',
    'valorant-win64-shipping.exe',
    'valorant.exe',
    'overwatch.exe',
    'tslgame.exe',
    'narakabladepoint.exe',
    'escapefromtarkov.exe',
    'deltaforceclient-win64-shipping.exe',
    'thefinals.exe',
    'marvelrivals-win64-shipping.exe',
    'strinova-win64-shipping.exe',
    'calabi-win64-shipping.exe',
    'cod.exe',
    'bf2042.exe',
    'bfv.exe',
    'bf1.exe',
    # MOBA / Action / Local Full-Immersion Titles
    'dota2.exe',
    'league of legends.exe',
    'eldenring.exe',
    'b1-win64-shipping.exe',
    'cyberpunk2077.exe',
    'monsterhunterwilds.exe',
    'monsterhunterworld.exe',
    'endfield.exe',
    'arknightsendfield.exe',
    'genshinimpact.exe',
    'yuanshen.exe',
    'starrail.exe',
})

_HEADPHONE_NAME_HINTS = (
    '耳机', '耳麦', '头戴', '耳塞', '入耳',
    'headphone', 'headset', 'earphone', 'earbud', 'airpods', 'buds',
    'wh-1000', 'wf-1000', 'linkbuds', 'inzone',
    'hyperx', 'cloud ii', 'cloud iii', 'cloud alpha', 'cloud stinger',
    'arctis', 'kraken', 'blackshark', 'barracuda',
    'g pro x', 'g733', 'g435', 'g535', 'hs80', 'virtuoso',
)

_NON_HEADPHONE_NAME_HINTS = (
    'digital output', 's/pdif', 'spdif', 'optical',
    'amd high definition audio', 'nvidia high definition audio',
    'intel(r) display audio', 'displayport', 'hdmi',
)

# EndpointFormFactor enum values in Windows mmdeviceapi.h
ENDPOINT_FORMFACTOR_HEADPHONES = 3
ENDPOINT_FORMFACTOR_HEADSET = 5
ENDPOINT_FORMFACTOR_SPDIF = 8
ENDPOINT_FORMFACTOR_DIGITAL_DISPLAY = 9

_audio_cache = (0.0, False, '')
_game_cache = (0.0, (), None)


class _GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', w.DWORD),
        ('Data2', w.WORD),
        ('Data3', w.WORD),
        ('Data4', ctypes.c_ubyte * 8),
    ]


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [('fmtid', _GUID), ('pid', w.DWORD)]


class _PROPVARIANT_UNION(ctypes.Union):
    _fields_ = [
        ('ulVal', w.ULONG),
        ('pwszVal', w.LPWSTR),
        ('_pad', ctypes.c_ubyte * 16),
    ]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [
        ('vt', w.USHORT),
        ('wReserved1', w.USHORT),
        ('wReserved2', w.USHORT),
        ('wReserved3', w.USHORT),
        ('val', _PROPVARIANT_UNION),
    ]


def _make_guid(d1, d2, d3, b0, b1, b2, b3, b4, b5, b6, b7):
    return _GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(b0, b1, b2, b3, b4, b5, b6, b7))


def _classify_audio_endpoint(form_factor: int | None, friendly_name: str) -> bool:
    """Pure classifier for unit testing and Core Audio endpoint checks."""
    low = (friendly_name or '').casefold()
    if form_factor in (ENDPOINT_FORMFACTOR_HEADPHONES, ENDPOINT_FORMFACTOR_HEADSET):
        return True
    if form_factor in (ENDPOINT_FORMFACTOR_SPDIF, ENDPOINT_FORMFACTOR_DIGITAL_DISPLAY):
        return False
    if any(token in low for token in _HEADPHONE_NAME_HINTS):
        return True
    if any(token in low for token in _NON_HEADPHONE_NAME_HINTS):
        return False
    return False


def _detect_default_audio_endpoint() -> tuple[int | None, str]:
    """Return ``(form_factor, friendly_name)`` of the active Windows default render endpoint."""
    if not WINDOWS:
        return None, ''
    ole32 = ctypes.WinDLL('ole32', use_last_error=True)
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, w.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID), ctypes.c_void_p, w.DWORD,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.PropVariantClear.argtypes = [ctypes.POINTER(_PROPVARIANT)]
    ole32.PropVariantClear.restype = ctypes.c_long

    hr_init = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    need_uninit = hr_init in (0, 1)  # S_OK or S_FALSE

    CLSID_MMDeviceEnumerator = _make_guid(
        0xBCDE0395, 0xE52F, 0x467C, 0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E
    )
    IID_IMMDeviceEnumerator = _make_guid(
        0xA95664D2, 0x9614, 0x4F35, 0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6
    )
    PKEY_FormFactor = _PROPERTYKEY(
        _make_guid(0x1DA5D803, 0xD492, 0x4EDD, 0x8C, 0x23, 0xE0, 0xC0, 0xFF, 0xEE, 0x7F, 0x0E),
        0,
    )
    PKEY_FriendlyName = _PROPERTYKEY(
        _make_guid(0xA45C254E, 0xDF1C, 0x4EFD, 0x80, 0x20, 0x67, 0xD1, 0x46, 0xA8, 0x50, 0xE0),
        14,
    )
    PKEY_IfaceName = _PROPERTYKEY(
        _make_guid(0x026E516E, 0xB814, 0x414B, 0x83, 0xCD, 0x85, 0x6D, 0x6F, 0xEF, 0x48, 0x22),
        2,
    )

    def _release(ptr):
        if ptr and ptr.value:
            vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            rel = ctypes.WINFUNCTYPE(w.ULONG, ctypes.c_void_p)(vtable[2])
            rel(ptr)
            ptr.value = None

    enum_ptr = ctypes.c_void_p()
    dev_ptr = ctypes.c_void_p()
    store_ptr = ctypes.c_void_p()
    form_factor = None
    names: list[str] = []
    try:
        hr = ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDeviceEnumerator), None, 0x17,  # CLSCTX_ALL
            ctypes.byref(IID_IMMDeviceEnumerator), ctypes.byref(enum_ptr),
        )
        if hr != 0 or not enum_ptr.value:
            return _fallback_waveout_name()

        # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender=0, eMultimedia=1, &dev_ptr)
        enum_vt = ctypes.cast(enum_ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        get_default = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
        )(enum_vt[4])
        hr = get_default(enum_ptr, 0, 1, ctypes.byref(dev_ptr))
        if hr != 0 or not dev_ptr.value:
            return None, ''

        # IMMDevice::OpenPropertyStore(STGM_READ=0, &store_ptr)
        dev_vt = ctypes.cast(dev_ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        open_store = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, w.DWORD, ctypes.POINTER(ctypes.c_void_p)
        )(dev_vt[4])
        hr = open_store(dev_ptr, 0, ctypes.byref(store_ptr))
        if hr != 0 or not store_ptr.value:
            return _fallback_waveout_name()

        # IPropertyStore::GetValue(&pkey, &propvar)
        store_vt = ctypes.cast(store_ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        get_val = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)
        )(store_vt[5])

        pv = _PROPVARIANT()
        if get_val(store_ptr, ctypes.byref(PKEY_FormFactor), ctypes.byref(pv)) == 0:
            try:
                if pv.vt in (19, 3, 23):  # VT_UI4 / VT_I4 / VT_UINT
                    form_factor = int(pv.val.ulVal)
            finally:
                ole32.PropVariantClear(ctypes.byref(pv))

        for pkey in (PKEY_FriendlyName, PKEY_IfaceName):
            pv = _PROPVARIANT()
            if get_val(store_ptr, ctypes.byref(pkey), ctypes.byref(pv)) == 0:
                try:
                    if pv.vt == 31 and pv.val.pwszVal:  # VT_LPWSTR
                        names.append(str(pv.val.pwszVal))
                finally:
                    ole32.PropVariantClear(ctypes.byref(pv))
        return form_factor, ' '.join(names).strip()
    except Exception:
        return _fallback_waveout_name()
    finally:
        _release(store_ptr)
        _release(dev_ptr)
        _release(enum_ptr)
        if need_uninit:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass


def _fallback_waveout_name() -> tuple[int | None, str]:
    if not WINDOWS:
        return None, ''
    try:
        winmm = ctypes.WinDLL('winmm', use_last_error=True)

        class _WAVEOUTCAPSW(ctypes.Structure):
            _fields_ = [
                ('wMid', w.WORD),
                ('wPid', w.WORD),
                ('vDriverVersion', w.UINT),
                ('szPname', w.WCHAR * 32),
                ('dwFormats', w.DWORD),
                ('wChannels', w.WORD),
                ('wReserved1', w.WORD),
                ('dwSupport', w.DWORD),
            ]

        caps = _WAVEOUTCAPSW()
        if winmm.waveOutGetDevCapsW(ctypes.c_uint(0xFFFFFFFF), ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            return None, str(caps.szPname or '')
    except Exception:
        pass
    return None, ''


def default_audio_endpoint_info(*, force_refresh: bool = False) -> tuple[bool, str]:
    """Return ``(is_headphone, endpoint_label)`` with a 1.5s cache."""
    global _audio_cache
    now = _time.monotonic()
    if not force_refresh and now - _audio_cache[0] < 1.5:
        return _audio_cache[1], _audio_cache[2]
    form_factor, friendly = _detect_default_audio_endpoint()
    active = _classify_audio_endpoint(form_factor, friendly)
    _audio_cache = (now, active, friendly)
    return active, friendly


def is_headphone_output_active(*, force_refresh: bool = False) -> bool:
    """True when the active default audio output is headphones or a headset."""
    active, _ = default_audio_endpoint_info(force_refresh=force_refresh)
    return active


def _list_process_names() -> set[str]:
    """Enumerate running process executable names in lowercase via Toolhelp32."""
    if not WINDOWS:
        return set()
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ('dwSize', w.DWORD),
            ('cntUsage', w.DWORD),
            ('th32ProcessID', w.DWORD),
            ('th32DefaultHeapID', ctypes.c_size_t),
            ('th32ModuleID', w.DWORD),
            ('cntThreads', w.DWORD),
            ('th32ParentProcessID', w.DWORD),
            ('pcPriClassBase', w.LONG),
            ('dwFlags', w.DWORD),
            ('szExeFile', w.WCHAR * 260),
        ]

    kernel32.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = w.BOOL
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = w.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return set()
    names: set[str] = set()
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            exe = str(entry.szExeFile or '').strip().lower()
            if exe:
                names.add(exe)
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return names


def parse_extra_game_names(raw: str | tuple | list | set) -> tuple[str, ...]:
    """Normalize optional user-supplied game process names (e.g. 'foo.exe, bar')."""
    if not raw:
        return ()
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.replace('；', ',').replace(';', ',').split(',')]
    else:
        parts = [str(p).strip().lower() for p in raw]
    cleaned = []
    for item in parts:
        if not item:
            continue
        if not item.endswith('.exe'):
            item += '.exe'
        cleaned.append(item)
    return tuple(dict.fromkeys(cleaned))


def running_blocked_game(*, extra_names=(), force_refresh: bool = False) -> str | None:
    """Return the first running local game executable (e.g. ``'r5apex.exe'``) or ``None``."""
    global _game_cache
    extra_tuple = parse_extra_game_names(extra_names)
    now = _time.monotonic()
    if not force_refresh and _game_cache[1] == extra_tuple and now - _game_cache[0] < 2.0:
        return _game_cache[2]
    blocked = BLOCKED_GAME_PROCESSES | set(extra_tuple)
    running = _list_process_names()
    matched = sorted(blocked & running)
    found = matched[0] if matched else None
    _game_cache = (now, extra_tuple, found)
    return found


def voice_output_allowed(store, *, force_refresh: bool = False) -> tuple[bool, str]:
    """Evaluate whether voice should play right now alongside text output.

    Policy:
      - Quiet mode or volume <= 0 -> False.
      - Blocked local game running (Apex, Rainbow Six, etc.) -> False (suppresses auto voice;
        only overridden if user explicitly enabled manual ``voice_enabled``? No — games mute
        voice automatically so competitive audio is never disrupted).
      - ``voice_auto_headphone`` True (default in Store) + headphones active + no game -> True!
      - ``voice_enabled`` True (manual speaker override) + no game -> True!
      - Otherwise -> False (text-only).
    """
    if store is None:
        return False, '未提供配置，仅输出文本'

    def getter(key, default=None):
        if hasattr(store, 'get'):
            return store.get(key, default)
        try:
            return store[key]
        except (KeyError, TypeError, IndexError):
            return default
    if bool(getter('quiet_mode', False)):
        return False, '安静陪伴模式中 · 仅输出文本'
    try:
        volume = float(getter('voice_volume', 65))
    except (TypeError, ValueError):
        volume = 65.0
    if volume <= 0:
        return False, '语音音量为 0 · 仅输出文本'

    manual_on = bool(getter('voice_enabled', False))
    auto_headphone = bool(getter('voice_auto_headphone', False))
    if not manual_on and not auto_headphone:
        return False, '语音未启用 · 仅输出文本'

    extra_games = getter('voice_blocked_games', '')
    game = running_blocked_game(extra_names=extra_games, force_refresh=force_refresh)
    if game:
        return False, f'检测到本地游戏（{game}）· 自动静音仅输出文本'

    headphone, dev_name = default_audio_endpoint_info(force_refresh=force_refresh)
    if auto_headphone and headphone:
        label = f'（{dev_name}）' if dev_name else ''
        return True, f'已接入耳机{label}且未运行游戏 · 默认语音+文本同步输出'
    if manual_on:
        return True, '已手动开启扬声器语音 · 语音+文本同步输出'
    return False, '未检测到耳机输出 · 自动仅输出文本'

