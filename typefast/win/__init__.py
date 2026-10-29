"""Windows counterparts of x11.py and xkeys.py, with the same classes and methods."""

import ctypes
import sys
import winreg
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)

# Stamped on every event TypeFast injects, so the listener can skip its own keys
# (the X11 version skips the XTEST device instead).
MARKER = 0x54464B59  # "TFKY"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launcher_command() -> str:
    """pythonw (no console window) running this TypeFast."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" -m typefast'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, "TypeFast")
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "TypeFast", 0, winreg.REG_SZ, launcher_command())
        elif autostart_enabled():
            winreg.DeleteValue(key, "TypeFast")
