"""Keystroke injection through XTEST, in-process.

Replaces xdotool, which misbehaves with several keyboard layouts: it picks a
key from whichever layout lists it first and switches the active layout on
the fly to press it (dozens of switches per sentence, wrong letters when the
switch lands late, and a flood of layout events for GNOME Shell and IBus).

Here, only the active layout is used. A character missing from it (é on a
Spanish keyboard, œ on a US one) is bound once to a spare keycode, with the
same symbol in every layout slot, and that binding is reused for the rest of
the app's life. Before every key press, the caller can pause us, and we wait
while the user physically holds a modifier, so an injected letter never
combines with a real Ctrl into a shortcut.
"""

import ctypes
import logging
import time
from collections import OrderedDict

from Xlib import X, XK, display
from Xlib.ext import xtest

log = logging.getLogger(__name__)

XKB_USE_CORE_KBD = 0x100
LOCK_MASK = 0x02
# Ctrl, Alt, Super: if one is active when our key lands, the app got a shortcut.
FOREIGN_MODS = X.ControlMask | X.Mod1Mask | X.Mod4Mask
SPARE_RESERVED = 8           # keycode 8 is never a real key
BIND_SETTLE_S = 0.03         # let apps read a new binding before using it

_x11 = ctypes.CDLL("libX11.so.6")
_x11.XOpenDisplay.restype = ctypes.c_void_p
_x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
_x11.XkbKeycodeToKeysym.restype = ctypes.c_ulong
_x11.XkbKeycodeToKeysym.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_int, ctypes.c_int]
_x11.XkbRefreshKeyboardMapping.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_x11.XCloseDisplay.argtypes = [ctypes.c_void_p]


class _XkbState(ctypes.Structure):
    _fields_ = [("group", ctypes.c_ubyte), ("locked_group", ctypes.c_ubyte), ("base_group", ctypes.c_ushort),
                ("latched_group", ctypes.c_ushort), ("mods", ctypes.c_ubyte), ("base_mods", ctypes.c_ubyte),
                ("latched_mods", ctypes.c_ubyte), ("locked_mods", ctypes.c_ubyte), ("compat_state", ctypes.c_ubyte),
                ("grab_mods", ctypes.c_ubyte), ("compat_grab_mods", ctypes.c_ubyte), ("lookup_mods", ctypes.c_ubyte),
                ("compat_lookup_mods", ctypes.c_ubyte), ("ptr_buttons", ctypes.c_ushort)]


_x11.XkbGetState.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(_XkbState)]

SPECIAL_KEYSYMS = {"\n": XK.XK_Return, "\t": XK.XK_Tab}


def char_to_keysym(ch: str) -> int:
    if ch in SPECIAL_KEYSYMS:
        return SPECIAL_KEYSYMS[ch]
    cp = ord(ch)
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp  # Latin-1 keysyms equal their code points
    return 0x01000000 | cp


def is_cased(ch: str) -> bool:
    return ch.lower() != ch.upper()


def plan_key(keysym: int, table: dict, caps_lock: bool, cased: bool):
    """(keycode, shift) for a keysym in the active layout, or None.

    table maps keysym -> (keycode, level) where level 0 is plain and level 1
    is Shift. Caps Lock flips the level of letters.
    """
    hit = table.get(keysym)
    if hit is None:
        return None
    keycode, level = hit
    shift = level == 1
    if caps_lock and cased:
        shift = not shift
    return keycode, shift


class KeyInjector:
    """Owns its own X connections; use it from one thread only."""

    MODIFIER_NAMES = ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
                      "Super_L", "Super_R", "ISO_Level3_Shift", "Meta_L", "Meta_R")

    def __init__(self, max_spare: int = 40):
        self.d = display.Display()
        if not self.d.has_extension("XTEST"):
            raise RuntimeError("The X server has no XTEST extension")
        self._xkb = _x11.XOpenDisplay(None)
        if not self._xkb:
            raise RuntimeError("Cannot open the X display")
        self.min_kc = self.d.display.info.min_keycode
        self.max_kc = self.d.display.info.max_keycode
        self.max_spare = max_spare
        self._group = -1
        self._table: dict[int, tuple[int, int]] = {}
        self._bound: OrderedDict[int, int] = OrderedDict()   # keysym -> spare keycode, LRU order
        self._free: list[int] = []
        self._modifier_codes: set[int] = set()
        self._shift = 0
        self.collided = False  # set when a real modifier may have combined with our key
        self._refresh(force=True)

    # -- keymap ------------------------------------------------------------------

    def _keysym(self, keycode: int, group: int, level: int) -> int:
        return int(_x11.XkbKeycodeToKeysym(self._xkb, keycode, group, level))

    def _state(self) -> _XkbState:
        st = _XkbState()
        _x11.XkbGetState(self._xkb, XKB_USE_CORE_KBD, ctypes.byref(st))
        return st

    def _drain_mapping_events(self) -> bool:
        changed = False
        while self.d.pending_events():
            e = self.d.next_event()
            if e.type == X.MappingNotify:
                self.d.refresh_keyboard_mapping(e)
                changed = True
        return changed

    def _refresh(self, force: bool = False) -> None:
        group = self._state().group
        if self._drain_mapping_events():
            # Someone changed the keymap; make libX11's XKB copy re-read it too.
            _x11.XCloseDisplay(self._xkb)
            self._xkb = _x11.XOpenDisplay(None)
            force = True
        if not force and group == self._group:
            return
        self._group = group
        bound_codes = set(self._bound.values())
        table: dict[int, tuple[int, int]] = {}
        free = []
        for kc in range(max(self.min_kc, SPARE_RESERVED + 1), self.max_kc + 1):
            if kc in bound_codes:
                continue
            l0, l1 = self._keysym(kc, group, 0), self._keysym(kc, group, 1)
            if 0xFF80 <= l0 <= 0xFFBD:
                continue  # keypad: its second level depends on Num Lock
            for level, ks in ((0, l0), (1, l1)):
                if ks and ks not in table:
                    table[ks] = (kc, level)
            if not any(self._keysym(kc, g, lv) for g in range(4) for lv in range(4)):
                free.append(kc)
        for ks, kc in self._bound.items():
            table.setdefault(ks, (kc, 0))
        self._table = table
        self._free = free
        self._modifier_codes = {kc for name in self.MODIFIER_NAMES
                                for kc, _ in self.d.keysym_to_keycodes(XK.string_to_keysym(name))}
        self._shift = self.d.keysym_to_keycode(XK.XK_Shift_L)

    def _bind(self, keysym: int) -> int:
        """Put `keysym` on a spare keycode, in every layout slot."""
        if self._free and len(self._bound) < self.max_spare:
            kc = self._free.pop(0)
        elif self._bound:
            old_ks, kc = self._bound.popitem(last=False)  # reuse the least recently used
            self._table.pop(old_ks, None)
        else:
            raise RuntimeError("no spare keycode available")
        per = len(self.d.get_keyboard_mapping(kc, 1)[0])
        self.d.change_keyboard_mapping(kc, [(keysym,) * per])
        self.d.sync()
        self._drain_mapping_events()
        self._bound[keysym] = kc
        self._table[keysym] = (kc, 0)
        time.sleep(BIND_SETTLE_S)
        log.debug("bound keysym %#x to spare keycode %d", keysym, kc)
        return kc

    def restore(self) -> None:
        """Give the spare keycodes back (empty them)."""
        for kc in self._bound.values():
            try:
                per = len(self.d.get_keyboard_mapping(kc, 1)[0])
                self.d.change_keyboard_mapping(kc, [(X.NoSymbol,) * per])
            except Exception:  # noqa: BLE001
                pass
        self._bound.clear()
        try:
            self.d.sync()
        except Exception:  # noqa: BLE001
            pass

    # -- safety ------------------------------------------------------------------

    def modifiers_held(self) -> bool:
        keys = self.d.query_keymap()
        return any(keys[kc // 8] & (1 << (kc % 8)) for kc in self._modifier_codes)

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

    # -- output ------------------------------------------------------------------

    def _tap(self, keycode: int, shift: bool = False, extra_mods=()) -> bool:
        """Press and release a key. False if a real Ctrl/Alt/Super was active when it landed.

        The modifier check before the press cannot be atomic with it, so a
        modifier pressed in that sub-millisecond gap is caught here instead.
        """
        mods = [self._shift] if shift else []
        mods += list(extra_mods)
        for m in mods:
            xtest.fake_input(self.d, X.KeyPress, m)
        xtest.fake_input(self.d, X.KeyPress, keycode)
        clean = extra_mods or not (self.d.screen().root.query_pointer().mask & FOREIGN_MODS)
        xtest.fake_input(self.d, X.KeyRelease, keycode)
        for m in reversed(mods):
            xtest.fake_input(self.d, X.KeyRelease, m)
        self.d.sync()
        if not clean:
            self.collided = True
            log.warning("a modifier was held when keycode %d landed", keycode)
        return bool(clean)

    def type_text(self, text: str, delay_ms: int, should_stop) -> int:
        """Type text one character at a time. Returns how many characters went out."""
        self._refresh()
        caps = bool(self._state().locked_mods & LOCK_MASK)
        sent = 0
        for ch in text:
            if ch == "\n":
                # Shift+Return: a new line in Gmail, Slack or WhatsApp, where Return alone sends.
                if not self._wait_clear(should_stop) or not self._tap(self.d.keysym_to_keycode(XK.XK_Return), True):
                    break
                sent += 1
                continue
            ks = char_to_keysym(ch)
            kc = self._bound.get(ks)
            if kc is not None:
                self._bound.move_to_end(ks)
                plan = (kc, False)  # spare keys carry the symbol on their plain level
            else:
                plan = plan_key(ks, self._table, caps, is_cased(ch))
                if plan is None:
                    plan = (self._bind(ks), False)
            if not self._wait_clear(should_stop):
                break
            if not self._tap(*plan):
                break  # the app probably got a shortcut, not this character
            sent += 1
            if delay_ms:
                time.sleep(delay_ms / 1000)
        return sent

    def backspace(self, count: int, should_stop, delay_ms: int = 1) -> int:
        self._refresh()
        kc = self.d.keysym_to_keycode(XK.XK_BackSpace)
        done = 0
        for _ in range(count):
            if not self._wait_clear(should_stop):
                break
            if not self._tap(kc):
                break  # Ctrl+Backspace deletes a whole word: the field is no longer ours to edit
            done += 1
            if delay_ms:
                time.sleep(delay_ms / 1000)
        return done

    def paste(self, terminal: bool, should_stop) -> bool:
        self._refresh()
        v = plan_key(ord("v"), self._table, False, False)
        if v is None or not self._wait_clear(should_stop):
            return False
        ctrl = self.d.keysym_to_keycode(XK.XK_Control_L)
        extra = [ctrl, self._shift] if terminal else [ctrl]
        self._tap(v[0], False, extra)
        return True

    def close(self) -> None:
        self.restore()
        try:
            self.d.close()
        except Exception:  # noqa: BLE001
            pass
        if self._xkb:
            _x11.XCloseDisplay(self._xkb)
            self._xkb = None
