"""Exercise the Typer for real: live updates, corrections, final swap (typed and pasted).

Opens a small window that grabs the keyboard, so injected keys can only land
there; if the grab fails, nothing is typed. Usage: python scripts/typer_check.py
"""

import shutil
import subprocess
import threading
import time

import typefast  # noqa: F401
from gi.repository import Gdk, GLib, Gtk

from typefast.injector import Typer

PREFILL = "Bonjour Camille,\n"
LIVE = ["j'ai", "j'ai regardé la", "j'ai regardé la roadmap oro", "j'ai regardé la roadmap oro commerce",
        "J'ai regardé la roadmap Oro commerce.", "J'ai regardé la roadmap Oro commerce. le connecteur a kenio"]
FINAL_SHORT = "J'ai regardé la roadmap OroCommerce. Le connecteur Akeneo est prêt."
CTRL_TEXT = "Avanzamos a la par: hablamos para planear la agenda anual, ya casi acabada, gracias a Ana y a Alba. " * 2
FINAL_LONG = FINAL_SHORT + " " + "Est-ce qu'on peut caler 30 minutes vendredi pour en parler ? Ça m'arrangerait beaucoup. " * 2


def main():
    import logging, os
    if os.environ.get("TYPER_DEBUG"):
        logging.basicConfig(level=logging.DEBUG, filename=os.environ["TYPER_DEBUG"], format="%(relativeCreated)d %(name)s %(message)s")
    win = Gtk.Window(type=Gtk.WindowType.POPUP)
    win.set_default_size(520, 160)
    win.move(30, 80)
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
    view.get_buffer().set_text(PREFILL)
    view.get_buffer().place_cursor(view.get_buffer().get_end_iter())
    win.add(view)
    win.set_focus(view)
    results = {}

    def buffer_text():
        b = view.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def scenario(typer: Typer):
        def wait_idle():
            for _ in range(400):
                if typer.typed == typer._target:
                    return
                time.sleep(0.02)
        for step in LIVE:
            typer.set_target(step)
            time.sleep(0.25)
        wait_idle()
        results["live"] = GLib.idle_add  # placeholder to keep ordering simple
        time.sleep(0.3)
        live_snapshot = []
        GLib.idle_add(lambda: live_snapshot.append(buffer_text()))
        time.sleep(0.2)
        results["live"] = live_snapshot[0] if live_snapshot else None
        # A Ctrl held in the middle of typing must never combine with a letter
        # (Ctrl+A would select everything and the next letter would replace it).
        typer.paste_threshold = 1000
        done = threading.Event()
        typer.replace(CTRL_TEXT, 0, lambda ok, reason: (results.__setitem__("ctrl_ok", ok), done.set()))
        time.sleep(0.25)
        subprocess.run(["xdotool", "keydown", "Control_L"])
        time.sleep(0.4)
        subprocess.run(["xdotool", "keyup", "Control_L"])
        done.wait(30)
        time.sleep(0.5)
        snap = []
        GLib.idle_add(lambda: snap.append(buffer_text()))
        time.sleep(0.2)
        results["ctrl"] = snap[0] if snap else None
        results["ctrl_frozen"] = typer.frozen
        # Start the next checks from a clean field and a fresh session.
        typer.reset()
        GLib.idle_add(lambda: view.get_buffer().set_text(PREFILL) or view.get_buffer().place_cursor(view.get_buffer().get_end_iter()))
        time.sleep(0.3)
        typer.set_target("")
        for name, final, threshold in (("typed", FINAL_SHORT, 1000), ("pasted", FINAL_LONG, 50)):
            typer.paste_threshold = threshold
            done = threading.Event()
            typer.replace(final, 0, lambda ok, reason: (results.__setitem__(name + "_ok", ok), done.set()))
            done.wait(30)
            time.sleep(0.6)
            snap = []
            GLib.idle_add(lambda: snap.append(buffer_text()))
            time.sleep(0.2)
            results[name] = snap[0] if snap else None
        GLib.idle_add(finish)

    def finish():
        seat.ungrab()
        Gtk.main_quit()
        return False

    def start():
        global seat
        seat = Gdk.Display.get_default().get_default_seat()
        for _ in range(50):
            if seat.grab(win.get_window(), Gdk.SeatCapabilities.KEYBOARD, False, None, None, None, None) == Gdk.GrabStatus.SUCCESS:
                break
            time.sleep(0.02)
        else:
            print("GRAB FAILED, not typing")
            Gtk.main_quit()
            return False
        typer = Typer(delay_ms=4, paste_threshold=1000)
        if not shutil.which("xdotool"):
            print("(xdotool missing: the Ctrl part of the check will fail)")
        from typefast import x11
        x11.WindowInfo.is_terminal = lambda self, wid: False  # the test window is a GTK text view
        xid = win.get_window().get_xid()
        x11.WindowInfo.active_window = lambda self: xid  # it holds the grab, not the WM focus
        threading.Thread(target=scenario, args=(typer,), daemon=True).start()
        return False

    win.show_all()
    GLib.timeout_add(250, start)
    Gtk.main()

    expect_live = PREFILL + LIVE[-1]
    print("live   ", "OK" if results.get("live") == expect_live else f"DIFF {results.get('live')!r}")
    ctrl_text = results.get("ctrl") or ""
    rest = ctrl_text[len(PREFILL):]
    if ctrl_text == PREFILL + CTRL_TEXT and results.get("ctrl_ok"):
        print("ctrl    OK (Ctrl was caught between keys, typing resumed after it)")
    elif results.get("ctrl_frozen") and ctrl_text.startswith(PREFILL) and CTRL_TEXT.startswith(rest):
        print(f"ctrl    OK (collision caught: field frozen after {len(rest)} clean characters)")
    else:
        print(f"ctrl    DIFF {ctrl_text!r}")
    print("typed  ", "OK" if results.get("typed") == PREFILL + FINAL_SHORT and results.get("typed_ok") else f"DIFF {results.get('typed')!r}")
    print("pasted ", "OK" if results.get("pasted") == PREFILL + FINAL_LONG and results.get("pasted_ok") else f"DIFF {results.get('pasted')!r}")


if __name__ == "__main__":
    main()
