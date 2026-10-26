"""Global key and mouse listener (low-level hooks) and window queries.

Low-level hooks see every event without taking it from anyone: the hook
procedure always passes it on. Events TypeFast injects carry MARKER in
dwExtraInfo and are skipped, like the XTEST device in x11.py.
"""

import ctypes
import logging
import threading
from ctypes import wintypes

from typefast.taps import TapDetector
from typefast.win import MARKER, user32

log = logging.getLogger(__name__)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT = 0x0012
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
CLICKS = (0x0201, 0x0204, 0x0207)  # left, right, middle button down; not the wheel
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
# AltGr sends a fake Left Ctrl before Right Alt, with this scan code.
ALTGR_CTRL_SCAN = 0x21D

TRIGGER_VKS = {
    "ctrl": (0xA2, 0xA3),
    "shift": (0xA0, 0xA1),
    "alt": (0xA4, 0xA5),
}
MODIFIER_VKS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C, 0x14, 0x90}


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class InputListener:
    """Same interface and callbacks as x11.InputListener; callbacks run on the hook thread.

    They must return quickly: Windows silently removes a low-level hook that
    keeps it waiting longer than LowLevelHooksTimeout (about a second).
    """

    def __init__(self, on_gesture, on_trigger_down, on_trigger_up, on_user_input,
                 trigger_key="ctrl", taps=3, window_ms=1000):
        self.on_gesture = on_gesture
        self.on_trigger_down = on_trigger_down
        self.on_trigger_up = on_trigger_up
        self.on_user_input = on_user_input
        self.detector = TapDetector(TRIGGER_VKS.get(trigger_key, TRIGGER_VKS["ctrl"]), taps, window_ms)
        self._pending_config = None
        self._thread_id = 0
        # Keep the callbacks referenced: ctypes would free them under the hooks otherwise.
        self._procs = (_HOOKPROC(self._on_key), _HOOKPROC(self._on_mouse))
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="typefast-input", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(2.0)

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def configure(self, trigger_key: str, taps: int, window_ms: int) -> None:
        self._pending_config = (trigger_key, taps, window_ms)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        hooks = [user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._procs[0], None, 0),
                 user32.SetWindowsHookExW(WH_MOUSE_LL, self._procs[1], None, 0)]
        self._ready.set()
        if not all(hooks):
            log.error("could not install the input hooks: error %d", ctypes.get_last_error())
        msg = wintypes.MSG()
        try:
            # Hooks are called from this loop; it ends on WM_QUIT from stop().
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            for h in hooks:
                if h:
                    user32.UnhookWindowsHookEx(h)

    def _on_key(self, code, wparam, lparam):
        if code >= 0:
            try:
                ev = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                if ev.dwExtraInfo != MARKER and ev.scanCode != ALTGR_CTRL_SCAN:
                    self._handle_key(wparam in (WM_KEYDOWN, WM_SYSKEYDOWN), ev.vkCode, ev.time)
            except Exception:  # noqa: BLE001
                log.exception("key hook failed")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _on_mouse(self, code, wparam, lparam):
        if code >= 0 and wparam in CLICKS:
            try:
                ev = ctypes.cast(lparam, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                if ev.dwExtraInfo != MARKER:
                    self.on_user_input("click")
            except Exception:  # noqa: BLE001
                log.exception("mouse hook failed")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _handle_key(self, pressed: bool, vk: int, t_ms: int) -> None:
        if self._pending_config:
            key, taps, window_ms = self._pending_config
            self._pending_config = None
            self.detector.configure(TRIGGER_VKS.get(key, TRIGGER_VKS["ctrl"]), taps, window_ms)
        det = self.detector
        if pressed:
            det.press(vk, t_ms)
            if det.armed:
                self.on_trigger_down()
            elif vk not in MODIFIER_VKS:
                self.on_user_input("key")
            return
        was_trigger = vk in det.keycodes
        fired = det.release(vk, t_ms)
        if was_trigger:
            self.on_trigger_up(det.in_sequence)
        if fired:
            self.on_gesture()


class WindowInfo:
    """Same interface as x11.WindowInfo; window ids are HWNDs."""

    TERMINAL_CLASSES = ("consolewindowclass", "cascadia_hosting_window_class", "mintty", "virtualconsoleclass",
                        "putty", "alacritty", "wezterm")

    def active_window(self) -> int:
        return user32.GetForegroundWindow() or 0

    def is_terminal(self, window_id: int) -> bool:
        if not window_id:
            return False
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(window_id, buf, 256)
        joined = (buf.value + " " + _process_name(window_id)).lower()
        return any(h in joined for h in self.TERMINAL_CLASSES)

    def close(self) -> None:
        pass


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        return buf.value.rsplit("\\", 1)[-1] if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(h)
