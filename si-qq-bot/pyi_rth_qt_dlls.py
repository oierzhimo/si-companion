"""Keep bundled Qt and Shiboken DLL directories available to Windows."""
import os
import sys

_si_qq_dll_handles = []
for _relative in ("PySide6", "shiboken6"):
    _directory = os.path.join(sys._MEIPASS, _relative)
    if os.path.isdir(_directory):
        _si_qq_dll_handles.append(os.add_dll_directory(_directory))
