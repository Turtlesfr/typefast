"""Paste through the clipboard without racing the app that reads it.

Pasting means: put the text in the CLIPBOARD selection, press Ctrl+V, and let
the focused app fetch the text whenever it gets to it. There is no reliable
way to know when it did: Chrome, for one, pre-reads every new clipboard the
moment it changes, long before a busy page like Gmail handles the Ctrl+V. So
the dictated text simply stays in the clipboard afterwards. Restoring the old
content on any schedule would sometimes paste that old content instead.

TypeFast takes the selection itself first, so it knows it owns it before
pressing Ctrl+V, then hands the text to xclip to keep serving it.
"""

import logging
import subprocess
import time

from Xlib import X, Xatom, display
from Xlib.protocol import event

log = logging.getLogger(__name__)

TEXT_TARGETS = ("UTF8_STRING", "text/plain;charset=utf-8", "text/plain", "TEXT", "STRING")
MAX_BYTES = 200_000  # one ChangeProperty request; larger texts would need the INCR protocol


def _run(args, input_bytes=None, timeout=2.0, capture=True):
    out = subprocess.PIPE if capture else subprocess.DEVNULL
    try:
        return subprocess.run(args, input=input_bytes, stdout=out, stderr=out, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def hand_off(text: str) -> None:
    """Keep `text` in the clipboard, served by xclip once TypeFast stops serving it."""
    _run(["xclip", "-i", "-selection", "clipboard"], input_bytes=text.encode(), capture=False)


class ClipboardServer:
    """Owns the CLIPBOARD selection from one thread; create and use it in that thread."""

    def __init__(self):
        self.d = display.Display()
        self.win = self.d.screen().root.create_window(-10, -10, 1, 1, 0, X.CopyFromParent,
                                                      event_mask=X.PropertyChangeMask)
        a = self.d.intern_atom
        self.CLIPBOARD = a("CLIPBOARD")
        self.TARGETS = a("TARGETS")
        self.TIMESTAMP = a("TIMESTAMP")
        self.UTF8 = a("UTF8_STRING")
        self.text_atoms = {a(name): name for name in TEXT_TARGETS}

    def _server_time(self) -> int:
        """A real X timestamp (selection ownership should not use CurrentTime)."""
        stamp = self.d.intern_atom("_TYPEFAST_TIME")
        self.win.change_property(stamp, Xatom.STRING, 8, b"")
        self.d.flush()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            while self.d.pending_events():
                e = self.d.next_event()
                if e.type == X.PropertyNotify and e.atom == stamp:
                    return e.time
            time.sleep(0.005)
        return X.CurrentTime

    def _answer(self, e, data: bytes) -> str | None:
        """Reply to a SelectionRequest. Returns the target name when text was delivered."""
        prop = e.property or e.target
        name = None
        if e.target == self.TARGETS:
            atoms = [self.TARGETS, self.TIMESTAMP] + list(self.text_atoms)
            e.requestor.change_property(prop, Xatom.ATOM, 32, atoms)
        elif e.target == self.TIMESTAMP:
            e.requestor.change_property(prop, Xatom.INTEGER, 32, [self._owned_at])
        elif e.target in self.text_atoms:
            name = self.text_atoms[e.target]
            payload = data.decode().encode("latin-1", "replace") if name == "STRING" else data
            kind = Xatom.STRING if name == "STRING" else (self.UTF8 if name in ("UTF8_STRING", "TEXT") else e.target)
            e.requestor.change_property(prop, kind, 8, payload)
        else:
            prop = X.NONE
        e.requestor.send_event(event.SelectionNotify(time=e.time, requestor=e.requestor, selection=e.selection,
                                                     target=e.target, property=prop))
        self.d.flush()
        return name

    def paste(self, text: str, press_paste, target_window: int, serve_s: float = 1.0) -> bool:
        """Own the clipboard with `text`, press Ctrl+V, then leave `text` in the clipboard.

        Returns False if Ctrl+V could not be pressed (the caller types instead).
        """
        data = text.encode()
        if len(data) > MAX_BYTES:
            return False
        self._owned_at = self._server_time()
        self.win.set_selection_owner(self.CLIPBOARD, self._owned_at)
        self.d.flush()
        if self.d.get_selection_owner(self.CLIPBOARD) != self.win:
            log.warning("could not take the clipboard")
            return False
        if not press_paste():
            return False
        # Answer the first requests ourselves (the target app, clipboard managers,
        # Chrome's pre-read), then let xclip take over without a gap.
        end = time.monotonic() + serve_s
        handed = False
        while True:
            while self.d.pending_events():
                e = self.d.next_event()
                if e.type == X.SelectionRequest:
                    name = self._answer(e, data)
                    log.debug("clipboard request from %#x (target app %#x) for %s -> %s", e.requestor.id,
                              target_window, self.d.get_atom_name(e.target), name or "metadata or refused")
                elif e.type == X.SelectionClear:
                    return True  # xclip (or the user) owns the clipboard now
            if not handed and time.monotonic() >= end:
                hand_off(text)
                handed = True
                end = time.monotonic() + 2.0
            elif handed and time.monotonic() >= end:
                log.warning("xclip did not take over the clipboard")
                return True
            time.sleep(0.01)

    def close(self) -> None:
        try:
            self.d.close()
        except Exception:  # noqa: BLE001
            pass
