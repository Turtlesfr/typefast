"""X11 plumbing: a global key listener (XInput2 raw events) and window queries.

Raw events reach every client that asks for them, without grabbing anything,
so other apps keep working normally. Each event names the device it came
from, which lets us ignore the keys TypeFast injects itself through XTEST.
"""

import logging
import select
import threading
import time

from Xlib import X, XK, display
from Xlib.ext import xinput
from Xlib.protocol import rq

from typefast.taps import TapDetector

log = logging.getLogger(__name__)

_RawEventData = rq.Struct(
    xinput.DEVICEID("deviceid"),
    rq.Card32("time"),
    rq.Card32("detail"),
    xinput.DEVICEID("sourceid"),
    rq.Card16("valuators_len"),
    rq.Card32("flags"),
    rq.Pad(4),
)

TRIGGER_KEYSYMS = {
    "ctrl": ("Control_L", "Control_R"),
    "shift": ("Shift_L", "Shift_R"),
    "alt": ("Alt_L", "Alt_R"),
}
MODIFIER_KEYSYMS = ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R",
                    "Meta_L", "Meta_R", "ISO_Level3_Shift", "Caps_Lock", "Num_Lock")


def keycodes_for(d: display.Display, names) -> set[int]:
    codes = set()
    for name in names:
        sym = XK.string_to_keysym(name)
        if sym:
            codes.update(kc for kc, _ in d.keysym_to_keycodes(sym))
    return codes


class InputListener:
    """Watches the keyboard and mouse from a background thread.

    on_gesture(): the trigger key was tapped N times.
    on_trigger_down() / on_trigger_up(): the trigger key alone is held / released,
        so injected typing can pause and never mix with a real Ctrl press.
    on_user_input(kind): "key" for any real key (not a lone modifier), "click" for a mouse click.
    All callbacks run on the listener thread.
    """

    def __init__(self, on_gesture, on_trigger_down, on_trigger_up, on_user_input,
                 trigger_key="ctrl", taps=3, window_ms=1000):
        self.on_gesture = on_gesture
        self.on_trigger_down = on_trigger_down
        self.on_trigger_up = on_trigger_up
        self.on_user_input = on_user_input
        self._trigger_key = trigger_key
        self.detector = TapDetector((), taps, window_ms)
        self._stop = threading.Event()
        self._pending_config = None
        self._thread = threading.Thread(target=self._run, name="typefast-input", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def configure(self, trigger_key: str, taps: int, window_ms: int) -> None:
        self._pending_config = (trigger_key, taps, window_ms)

    def _apply_config(self, d) -> None:
        key, taps, window_ms = self._pending_config or (self._trigger_key, self.detector.taps, self.detector.window_ms)
        self._pending_config = None
        self._trigger_key = key
        self.detector.configure(keycodes_for(d, TRIGGER_KEYSYMS.get(key, TRIGGER_KEYSYMS["ctrl"])), taps, window_ms)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._listen()
            except Exception as e:  # noqa: BLE001
                log.error("input listener crashed, restarting: %s", e)
                time.sleep(1.0)

    def _listen(self) -> None:
        d = display.Display()
        try:
            info = d.query_extension("XInputExtension")
            for ev in (xinput.RawKeyPress, xinput.RawKeyRelease, xinput.RawButtonPress):
                d.ge_add_event_data(info.major_opcode, ev, _RawEventData)
            synthetic = {dev.deviceid for dev in d.xinput_query_device(xinput.AllDevices).devices if "XTEST" in dev.name}
            modifiers = keycodes_for(d, MODIFIER_KEYSYMS)
            self._apply_config(d)
            d.screen().root.xinput_select_events([
                (xinput.AllMasterDevices, xinput.RawKeyPressMask | xinput.RawKeyReleaseMask | xinput.RawButtonPressMask)
            ])
            d.flush()
            fd = d.fileno()
            while not self._stop.is_set():
                if self._pending_config:
                    self._apply_config(d)
                if not d.pending_events():
                    select.select([fd], [], [], 0.25)
                while d.pending_events():
                    e = d.next_event()
                    evtype = getattr(e, "evtype", None)
                    if evtype not in (xinput.RawKeyPress, xinput.RawKeyRelease, xinput.RawButtonPress):
                        continue
                    if e.data.sourceid in synthetic:
                        continue  # our own injected keystrokes
                    self._handle(evtype, e.data.detail, e.data.time, modifiers)
        finally:
            d.close()

    def _handle(self, evtype: int, detail: int, t_ms: int, modifiers: set[int]) -> None:
        det = self.detector
        if evtype == xinput.RawButtonPress:
            if detail in (1, 2, 3):  # clicks, not scroll wheel
                self.on_user_input("click")
            return
        if evtype == xinput.RawKeyPress:
            det.press(detail, t_ms)
            if det.armed:
                self.on_trigger_down()
            elif detail not in modifiers:
                self.on_user_input("key")
            return
        was_trigger = detail in det.keycodes
        fired = det.release(detail, t_ms)
        if was_trigger:
            self.on_trigger_up(det.in_sequence)
        if fired:
            self.on_gesture()


class WindowInfo:
    """Window queries on a private connection (python-xlib is not thread-safe)."""

    TERMINAL_HINTS = ("term", "kitty", "alacritty", "tilix", "konsole", "wezterm", "ghostty", "foot", "urxvt", "st-256color")

    def __init__(self):
        self.d = display.Display()
        self.root = self.d.screen().root
        self._active = self.d.intern_atom("_NET_ACTIVE_WINDOW")

    def active_window(self) -> int:
        prop = self.root.get_full_property(self._active, X.AnyPropertyType)
        return int(prop.value[0]) if prop and len(prop.value) else 0

    def is_terminal(self, window_id: int) -> bool:
        if not window_id:
            return False
        try:
            cls = self.d.create_resource_object("window", window_id).get_wm_class() or ()
        except Exception:  # noqa: BLE001
            return False
        joined = " ".join(cls).lower()
        return any(h in joined for h in self.TERMINAL_HINTS)

    def close(self) -> None:
        self.d.close()
