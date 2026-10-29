"""Paste through the Windows clipboard.

Unlike X11, the clipboard holds the data itself: once SetClipboardData
returns, any app can read the text, now or later, without TypeFast serving
it. As on Linux, the dictated text simply stays in the clipboard afterwards.
"""

import ctypes
import logging
import time
from ctypes import wintypes

from typefast.win import user32

log = logging.getLogger(__name__)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]


def set_text(text: str) -> bool:
    """Put `text` in the clipboard. False if another app kept it locked."""
    data = (text.replace("\r\n", "\n").replace("\n", "\r\n") + "\0").encode("utf-16-le")
    for _ in range(20):  # another app (a clipboard manager) may hold it open for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        log.warning("could not open the clipboard")
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)  # still ours: the clipboard only takes it on success
            return False
        return True
    finally:
        user32.CloseClipboard()


def hand_off(text: str) -> None:
    """Keep `text` in the clipboard (same name as in clipboard.py)."""
    set_text(text)


class ClipboardServer:
    """Same interface as clipboard.ClipboardServer."""

    def paste(self, text: str, press_paste, target_window: int, serve_s: float = 1.0) -> bool:
        """Put `text` in the clipboard and press Ctrl+V. False if Ctrl+V could not be pressed."""
        if not set_text(text):
            return False
        return bool(press_paste())

    def close(self) -> None:
        pass
