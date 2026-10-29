"""A native Windows text box for the check scripts, owned by the script itself.

Tk is no good as a target: it turns the key-ups of SendInput's Unicode
events into extra characters. A plain EDIT control behaves like the text
fields of most Windows apps. The checks only ever type into this window,
and stop when it does not have the focus.
"""

import ctypes
import threading
import time
from ctypes import wintypes

from typefast.win import user32
from typefast.win.input import WindowInfo
from typefast.win.keys import KEYEVENTF_KEYUP, VK_MENU, _key, _send

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_CHILD, WS_VSCROLL = 0x00CF0000, 0x10000000, 0x40000000, 0x00200000
ES_MULTILINE, ES_AUTOVSCROLL, ES_WANTRETURN = 0x0004, 0x0040, 0x1000
WS_EX_TOPMOST = 0x00000008
WM_DESTROY, WM_SETFOCUS, WM_CLOSE, WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x02, 0x07, 0x10, 0x0C, 0x0D, 0x0E
EM_SETSEL = 0x00B1

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                   wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class TestWindow:
    """A top-level window holding one multi-line EDIT control, pumped on its own thread."""

    def __init__(self, title: str = "TypeFast check"):
        self.top = self.edit = 0
        self._proc = _WNDPROC(self._wndproc)
        ready = threading.Event()
        threading.Thread(target=self._run, args=(title, ready), daemon=True).start()
        ready.wait(5)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_SETFOCUS and self.edit:
            user32.SetFocus(self.edit)  # the frame got the focus: hand it to the text box
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self, title: str, ready: threading.Event) -> None:
        inst = kernel32.GetModuleHandleW(None)
        cls = _WNDCLASSW(lpfnWndProc=self._proc, hInstance=inst, lpszClassName="TypeFastCheck")
        user32.RegisterClassW(ctypes.byref(cls))
        # Shown only once the text box exists, so the first focus lands in it.
        self.top = user32.CreateWindowExW(WS_EX_TOPMOST, "TypeFastCheck", title, WS_OVERLAPPEDWINDOW,
                                          40, 80, 640, 280, None, None, inst, None)
        self.edit = user32.CreateWindowExW(0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_VSCROLL | ES_MULTILINE
                                           | ES_AUTOVSCROLL | ES_WANTRETURN, 0, 0, 620, 240, self.top, None, inst, None)
        user32.ShowWindow(self.top, 5)  # SW_SHOW
        user32.SetFocus(self.edit)
        ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def focus(self) -> bool:
        """Bring the window to the front. False if Windows would not give it the focus."""
        win = WindowInfo()
        for _ in range(10):
            if self.has_focus():
                return True
            # A background process may only take the foreground right after a key press;
            # a marked Alt tap (ignored by the listener) provides one.
            _send(_key(VK_MENU))
            _send(_key(VK_MENU, flags=KEYEVENTF_KEYUP))
            user32.SetForegroundWindow(self.top)
            time.sleep(0.2)
        return win.active_window() == self.top

    def has_focus(self) -> bool:
        return WindowInfo().active_window() == self.top

    def text(self) -> str:
        n = user32.SendMessageW(self.edit, WM_GETTEXTLENGTH, 0, 0)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.SendMessageW(self.edit, WM_GETTEXT, n + 1, ctypes.cast(buf, ctypes.c_void_p).value)
        return buf.value.replace("\r\n", "\n")

    def set_text(self, text: str) -> None:
        """Replace the content and put the caret at the end."""
        value = ctypes.c_wchar_p(text.replace("\n", "\r\n"))
        user32.SendMessageW(self.edit, WM_SETTEXT, 0, ctypes.cast(value, ctypes.c_void_p).value)
        user32.SendMessageW(self.edit, EM_SETSEL, len(text) * 2, len(text) * 2)  # past the end: clamped

    def close(self) -> None:
        user32.PostMessageW(self.top, WM_CLOSE, 0, 0)
