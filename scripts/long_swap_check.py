"""Final swap of a long dictation (~5000 characters) for real, timed, in GTK and Chrome.

The live text is put straight into the field, as if it had been typed, then the
Typer swaps it for the polished text: erase all of it, then paste. Each run
reports when the Typer was done sending keys and when the field really showed
the polished text (the app may still be working through the keys), and checks
the field holds the untouched prefix plus exactly the polished text.

The GTK field runs in its own process and grabs the keyboard, so keys can only
land there. Chrome runs in a temporary profile and is only typed into while it
has the focus. The clipboard is restored at the end.

Usage: python scripts/long_swap_check.py [gtk] [chrome] [rich] [--runs N] [--old]
  --old   use the previous 1 ms pause per Backspace, for comparison
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import typefast  # noqa: F401
from typefast import injector, x11
from typefast.injector import Typer

PORT = 9334
TITLE = "TypeFast long swap test"
PREFIX = "Texte d'avant, à garder. "
LIVE = ("bon alors j'ai regardé la roadmap oro commerce avec l'équipe ce matin et on s'est dit que le connecteur "
        "a kenio devait être prêt avant la fin du mois parce que sinon le client va être déçu ") * 28
FINAL = (("J'ai regardé la roadmap OroCommerce avec l'équipe ce matin. Le connecteur Akeneo doit être prêt "
         "avant la fin du mois, sinon le client sera déçu.\n\n") * 32).strip()
LANDED_WAIT_S = 60


def same_text(a: str, b: str) -> bool:
    """Equal, ignoring how many line breaks separate paragraphs (rich editors add their own)."""
    return "\n".join(line for line in a.split("\n") if line) == "\n".join(line for line in b.split("\n") if line)


def first_diff(text: str, want: str) -> str:
    i = next((k for k, (x, y) in enumerate(zip(text, want)) if x != y), min(len(text), len(want)))
    return f"{len(text)} chars (want {len(want)}), differs at {i}: {text[max(0, i - 15):i + 25]!r}"


def swap(typer: Typer, guard: int, read) -> dict:
    """Declare LIVE as typed, swap it for FINAL, and time it until read() shows the final text."""
    typer.reset()
    with typer._cond:
        typer._typed = typer._target = LIVE
        typer._window = guard
    done = threading.Event()
    res = {}
    started = time.monotonic()
    typer.replace(FINAL, guard, lambda ok, reason: (res.update(ok=ok, reason=reason), done.set()))
    estimates = []
    while not done.wait(0.1):
        p = typer.swap_progress()
        if p is not None:
            estimates.append(round(p.seconds_left, 1))
    res["sent_s"] = round(time.monotonic() - started, 2)
    res["estimate_s"] = estimates[0] if estimates else None
    want = PREFIX + FINAL
    text = None
    while time.monotonic() - started < LANDED_WAIT_S:
        text = read()
        if text is not None and same_text(text, want):
            res["landed_s"] = round(time.monotonic() - started, 2)
            break
        time.sleep(0.1)
    else:
        res["landed_s"] = None
        res["field"] = first_diff(text or "", want)
    return res


# -- GTK -------------------------------------------------------------------------

def gtk_field(state_path: str) -> None:
    """Child process: a text field holding PREFIX + LIVE that grabs the keyboard and reports its text."""
    from gi.repository import Gdk, GLib, Gtk
    win = Gtk.Window(type=Gtk.WindowType.POPUP)
    win.set_default_size(560, 220)
    win.move(30, 80)
    scroll = Gtk.ScrolledWindow()
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
    scroll.add(view)
    win.add(scroll)
    win.set_focus(view)
    buf = view.get_buffer()
    buf.set_text(PREFIX + LIVE)
    buf.place_cursor(buf.get_end_iter())
    last = [None]

    def report():
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if text != last[0]:
            last[0] = text
            tmp = state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"xid": win.get_window().get_xid(), "text": text}, f)
            os.replace(tmp, state_path)
        return True

    def start():
        seat = Gdk.Display.get_default().get_default_seat()
        for _ in range(50):
            if seat.grab(win.get_window(), Gdk.SeatCapabilities.KEYBOARD, False, None, None, None, None) == Gdk.GrabStatus.SUCCESS:
                GLib.timeout_add(50, report)
                return False
            time.sleep(0.02)
        Gtk.main_quit()  # no grab: never report, so nothing gets typed
        return False

    win.show_all()
    GLib.timeout_add(250, start)
    GLib.timeout_add_seconds(LANDED_WAIT_S + 30, Gtk.main_quit)
    Gtk.main()


def check_gtk(runs: int) -> list[str]:
    lines = []
    patched = x11.WindowInfo.is_terminal, x11.WindowInfo.active_window
    typer = None
    try:
        for i in range(runs):
            state = tempfile.mktemp(prefix="tf-gtk-field-", suffix=".json")
            child = subprocess.Popen([sys.executable, __file__, "--field-gtk", state],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            def read():
                try:
                    with open(state) as f:
                        return json.load(f)
                except (OSError, ValueError):
                    return None
            try:
                for _ in range(100):
                    s = read()
                    if s and s["text"] == PREFIX + LIVE:
                        break
                    time.sleep(0.05)
                else:
                    lines.append(f"gtk    run {i + 1}: ABORT, the field never grabbed the keyboard; nothing was typed")
                    continue
                xid = s["xid"]
                x11.WindowInfo.is_terminal = lambda self, wid: False
                x11.WindowInfo.active_window = lambda self: xid  # the field holds the grab, not the WM focus
                typer = typer or Typer(delay_ms=4, paste_threshold=300)
                res = swap(typer, xid, lambda: (read() or {}).get("text"))
                lines.append(f"gtk    run {i + 1}: {'OK' if res['landed_s'] and res.get('ok') else 'FAIL'}  {res}")
            finally:
                child.terminate()
                child.wait(5)
                for p in (state, state + ".tmp"):
                    if os.path.exists(p):
                        os.remove(p)
    finally:
        if typer:
            typer.close()
        x11.WindowInfo.is_terminal, x11.WindowInfo.active_window = patched
    return lines


# -- Chrome ------------------------------------------------------------------------

def cdp_eval(expr):
    from websockets.sync.client import connect
    pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
    page = next(p for p in pages if p.get("type") == "page")
    with connect(page["webSocketDebuggerUrl"], max_size=2**24) as ws:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                return m["result"]["result"].get("value")


def check_chrome(runs: int, rich: bool) -> list[str]:
    """A fresh Chrome for every run: the focus check stays meaningful."""
    return [chrome_once(i + 1, rich) for i in range(runs)]


def chrome_once(run: int, rich: bool) -> str:
    name = f"{'rich  ' if rich else 'chrome'} run {run}:"
    if rich:
        field = ("<div id=t contenteditable=true style='width:95%;height:300px;overflow:auto;border:1px solid gray'>"
                 "</div><script>document.getElementById('t').focus()</script>")
        fill = ("t.textContent = {text}; t.focus(); const r = document.createRange(); r.selectNodeContents(t); "
                "r.collapse(false); const s = getSelection(); s.removeAllRanges(); s.addRange(r);")
        read_js = "document.getElementById('t').innerText"
    else:
        field = "<textarea id=t autofocus style='width:95%;height:300px'></textarea>"
        fill = "t.value = {text}; t.focus(); t.setSelectionRange(t.value.length, t.value.length);"
        read_js = "document.getElementById('t').value"
    html = "data:text/html," + urllib.parse.quote(f"<title>{TITLE}</title>{field}")
    profile = tempfile.mkdtemp(prefix="tf-chrome-")
    chrome = subprocess.Popen(["google-chrome", f"--user-data-dir={profile}", f"--remote-debugging-port={PORT}",
                               "--no-first-run", "--no-default-browser-check", "--new-window",
                               "--window-size=700,460", "--window-position=60,90", html],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    typer = None
    try:
        win = x11.WindowInfo()
        target = 0
        for _ in range(80):
            time.sleep(0.25)
            wid = win.active_window()
            title = subprocess.run(["xdotool", "getwindowname", str(wid)], capture_output=True, text=True).stdout
            if TITLE in title:
                target = wid
                break
        if not target:
            return f"{name} ABORT, the test Chrome window never got the focus; nothing was typed"
        typer = Typer(delay_ms=4, paste_threshold=300)

        def read():
            return (cdp_eval(read_js) or "").replace(" ", " ").rstrip("\n")
        focused = cdp_eval("(() => { const t = document.getElementById('t'); "
                           + fill.format(text=json.dumps(PREFIX + LIVE))
                           + " return document.hasFocus() && document.activeElement === t; })()")
        time.sleep(0.5)
        if not focused or win.active_window() != target:
            return f"{name} ABORT, the field does not have the keyboard focus; nothing was typed"
        res = swap(typer, target, read)
        return f"{name} {'OK' if res['landed_s'] and res.get('ok') else 'FAIL'}  {res}"
    finally:
        if typer:
            typer.close()
        chrome.terminate()
        try:
            chrome.wait(5)
        except subprocess.TimeoutExpired:
            chrome.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main():
    args = sys.argv[1:]
    if args[:1] == ["--field-gtk"]:
        gtk_field(args[1])
        return
    runs = int(args[args.index("--runs") + 1]) if "--runs" in args else 2
    if "--old" in args:
        injector.FINAL_ERASE_DELAY_MS = 1
    targets = [a for a in args if a in ("gtk", "chrome", "rich")] or ["gtk", "chrome", "rich"]
    saved = subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True, timeout=2).stdout
    print(f"live {len(LIVE)} chars -> final {len(FINAL)} chars, erase pause {injector.FINAL_ERASE_DELAY_MS} ms per key")
    try:
        for t in targets:
            for line in (check_gtk(runs) if t == "gtk" else check_chrome(runs, rich=(t == "rich"))):
                print(line, flush=True)
    finally:
        if saved:
            subprocess.run(["xclip", "-i", "-selection", "clipboard"], input=saved,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)


if __name__ == "__main__":
    main()
