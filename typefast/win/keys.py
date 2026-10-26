"""Keystroke injection through SendInput, in-process.

Much simpler than XTEST: KEYEVENTF_UNICODE sends any character as itself,
whatever the active layout, so there is no keymap to read and no spare key
to bind. The safety rules are the same as in xkeys.py: wait while the user
physically holds a modifier, and flag a key that landed while Ctrl, Alt or
Windows was down, since the app may have taken it as a shortcut.
"""

import ctypes
import logging
import time
from ctypes import wintypes

from typefast.win import MARKER, user32

log = logging.getLogger(__name__)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_BACK, VK_RETURN, VK_SHIFT, VK_CONTROL, VK_MENU = 0x08, 0x0D, 0x10, 0x11, 0x12
VK_INSERT, VK_LWIN, VK_RWIN, VK_V = 0x2D, 0x5B, 0x5C, 0x56
FOREIGN_VKS = (VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)
MODIFIER_VKS = (VK_SHIFT,) + FOREIGN_VKS


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    # The mouse member sets the union's size; SendInput rejects a wrong cbSize.
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


def _key(vk=0, scan=0, flags=0) -> _INPUT:
    return _INPUT(type=INPUT_KEYBOARD, ki=_KEYBDINPUT(vk, scan, flags, 0, MARKER))


def _send(*events: _INPUT) -> bool:
    arr = (_INPUT * len(events))(*events)
    return user32.SendInput(len(events), arr, ctypes.sizeof(_INPUT)) == len(events)


def _down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def char_events(ch: str) -> list[tuple[int, int]]:
    """(scan, flags) pairs for a character: one UTF-16 unit per event, down then up.

    Characters outside the BMP (emoji) take two units, sent as a surrogate pair.
    """
    data = ch.encode("utf-16-le")
    units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
    return [(u, KEYEVENTF_UNICODE) for u in units] + [(u, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP) for u in units]


class KeyInjector:
    """Same interface as xkeys.KeyInjector."""

    def __init__(self):
        self.collided = False  # set when a real modifier may have combined with our key

    def restore(self) -> None:
        pass  # nothing borrowed: no spare keys on Windows

    def modifiers_held(self) -> bool:
        return any(_down(vk) for vk in MODIFIER_VKS)

    def _wait_clear(self, should_stop) -> bool:
        """Wait until no modifier is held. False if the caller wants us to stop."""
        deadline = time.monotonic() + 5.0
        while True:
            if should_stop():
                return False
            if not self.modifiers_held():
                return True
            if time.monotonic() > deadline:
                return False
            time.sleep(0.01)

    def _tap(self, downs: list[_INPUT], ups: list[_INPUT], check: bool = True) -> bool:
        """Send the key-downs, check no real Ctrl/Alt/Windows is down, send the key-ups.

        As in xkeys.py, the check after the press catches a modifier pressed in the
        gap between _wait_clear and the press.
        """
        if not _send(*downs):
            log.warning("SendInput was blocked (UIPI: the focused app runs elevated?)")
            return False
        clean = not check or not any(_down(vk) for vk in FOREIGN_VKS)
        _send(*ups)
        if not clean:
            self.collided = True
            log.warning("a modifier was held when our key landed")
        return clean

    def type_text(self, text: str, delay_ms: int, should_stop) -> int:
        """Type text one character at a time. Returns how many characters went out."""
        sent = 0
        for ch in text:
            if not self._wait_clear(should_stop):
                break
            if ch == "\n":
                # Shift+Return: a new line in Gmail, Slack or WhatsApp, where Return alone sends.
                ok = self._tap([_key(VK_SHIFT), _key(VK_RETURN)],
                               [_key(VK_RETURN, flags=KEYEVENTF_KEYUP), _key(VK_SHIFT, flags=KEYEVENTF_KEYUP)])
            else:
                events = [_key(scan=s, flags=f) for s, f in char_events(ch)]
                half = len(events) // 2
                ok = self._tap(events[:half], events[half:])
            if not ok:
                break  # the app probably got a shortcut, not this character
            sent += 1
            if delay_ms:
                time.sleep(delay_ms / 1000)
        return sent

    def backspace(self, count: int, should_stop, delay_ms: int = 1) -> int:
        done = 0
        for _ in range(count):
            if not self._wait_clear(should_stop):
                break
            if not self._tap([_key(VK_BACK)], [_key(VK_BACK, flags=KEYEVENTF_KEYUP)]):
                break  # Ctrl+Backspace deletes a whole word: the field is no longer ours to edit
            done += 1
            if delay_ms:
                time.sleep(delay_ms / 1000)
        return done

    def paste(self, terminal: bool, should_stop) -> bool:
        if not self._wait_clear(should_stop):
            return False
        # Shift+Insert pastes in conhost, Windows Terminal and mintty alike; Ctrl+V does not in mintty.
        mod, key = (VK_SHIFT, VK_INSERT) if terminal else (VK_CONTROL, VK_V)
        return self._tap([_key(mod), _key(key)],
                         [_key(key, flags=KEYEVENTF_KEYUP), _key(mod, flags=KEYEVENTF_KEYUP)], check=False)

    def close(self) -> None:
        pass
