"""Final swap through the clipboard into a window that behaves like Chrome on a busy Gmail page.

On Ctrl+V the window pre-reads the clipboard at once (Chrome does this on every clipboard
change), then really pastes only after a delay. Puts a marker in the clipboard first and
checks that the field gets the final text, never the marker, and that the final text is
left in the clipboard. Usage: python scripts/paste_check.py [delay_s]
"""

import subprocess
import sys
import threading
import time

import typefast  # noqa: F401
from gi.repository import Gdk, GLib, Gtk

from typefast import x11
from typefast.injector import Typer

DELAY = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
MARKER = "OLD CLIPBOARD CONTENT " + str(time.time())
FINAL = "Hello Sam,\n\nI hope you're doing well.\n\n- point one\n- point two"


def main():
    subprocess.run(["xclip", "-i", "-selection", "clipboard"], input=MARKER.encode(),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.3)
    win = Gtk.Window(type=Gtk.WindowType.POPUP)
    win.set_default_size(520, 180)
    win.move(30, 80)
    view = Gtk.TextView()
    win.add(view)
    win.set_focus(view)
    res = {}

    def on_key(_w, ev):
        # Like a slow web app: act on Ctrl+V only after a delay.
        if ev.keyval in (Gdk.KEY_v, Gdk.KEY_V) and ev.state & Gdk.ModifierType.CONTROL_MASK:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).request_text(lambda *_: None)  # the pre-read
            GLib.timeout_add(int(DELAY * 1000), lambda: view.emit("paste-clipboard") and False)
            return True
        return False
    view.connect("key-press-event", on_key)

    def text():
        b = view.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def scenario(typer):
        typer.set_target("Hello Sam, I hope")
        for _ in range(200):
            if typer.typed == "Hello Sam, I hope":
                break
            time.sleep(0.02)
        done = threading.Event()
        typer.replace(FINAL, 0, lambda ok, reason: done.set())
        done.wait(15)
        time.sleep(DELAY + 1.5)
        box = []
        GLib.idle_add(lambda: box.append(text()))
        time.sleep(0.3)
        res["field"] = box[0] if box else None
        GLib.idle_add(lambda: (seat.ungrab(), Gtk.main_quit()))

    def start():
        global seat
        seat = Gdk.Display.get_default().get_default_seat()
        for _ in range(50):
            if seat.grab(win.get_window(), Gdk.SeatCapabilities.KEYBOARD, False, None, None, None, None) == Gdk.GrabStatus.SUCCESS:
                break
            time.sleep(0.02)
        else:
            print("GRAB FAILED")
            Gtk.main_quit()
            return False
        x11.WindowInfo.is_terminal = lambda self, wid: False
        # The test window holds the keyboard grab but is not the WM's active window.
        xid = win.get_window().get_xid()
        x11.WindowInfo.active_window = lambda self: xid
        typer = Typer(delay_ms=4, paste_threshold=1000)
        threading.Thread(target=scenario, args=(typer,), daemon=True).start()
        return False

    win.show_all()
    GLib.timeout_add(250, start)
    Gtk.main()
    after = subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True, timeout=2).stdout.decode()
    field = res.get("field")
    print(f"slow reader {DELAY}s -> field:", "OK (final text)" if field == FINAL else
          ("WRONG: old clipboard pasted" if field and MARKER in field else f"DIFF {field!r}"))
    print("clipboard afterwards:", "final text" if after == FINAL else ("marker" if after == MARKER else f"{after[:40]!r}"))


if __name__ == "__main__":
    main()
