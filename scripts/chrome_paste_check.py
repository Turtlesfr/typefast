"""Final swap into a real Chrome textarea (separate temporary profile), checked through DevTools.

Only types when the test Chrome window has the focus. Usage: python scripts/chrome_paste_check.py
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

from websockets.sync.client import connect

import typefast  # noqa: F401
from typefast.injector import Typer
from typefast.x11 import WindowInfo

PORT = 9333
MARKER = "OLD CLIPBOARD CONTENT " + str(time.time())
LIVE = "Hi psalm, I hope you're doing fine. I'm writing to utility know"
FINAL = "Hi Sam,\n\nI hope you're doing well.\n\nI'm writing to let you know.\n\n- one\n- two"


def cdp_eval(expr):
    pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
    page = next(p for p in pages if p.get("type") == "page")
    with connect(page["webSocketDebuggerUrl"], max_size=2**22) as ws:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                return m["result"]["result"].get("value")


def main():
    logging.basicConfig(level=logging.DEBUG, format="%(relativeCreated)6d %(name)s %(message)s",
                        handlers=[logging.FileHandler(sys.argv[1] if len(sys.argv) > 1 else "/dev/stderr")])
    profile = tempfile.mkdtemp(prefix="tf-chrome-")
    rich = os.environ.get("RICH") == "1"
    field = ("<div id=t contenteditable=true style='width:95%25;height:300px;border:1px solid gray'></div>"
             "<script>document.getElementById('t').focus()</script>") if rich else \
            "<textarea id=t autofocus style='width:95%25;height:300px'></textarea>"
    html = "data:text/html,<title>TypeFast paste test</title>" + field
    chrome = subprocess.Popen(["google-chrome", f"--user-data-dir={profile}", f"--remote-debugging-port={PORT}",
                               "--no-first-run", "--no-default-browser-check", "--new-window",
                               "--window-size=640,420", "--window-position=60,90", html],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        win = WindowInfo()
        target = 0
        for _ in range(60):
            time.sleep(0.25)
            wid = win.active_window()
            name = subprocess.run(["xdotool", "getwindowname", str(wid)], capture_output=True, text=True).stdout
            if "TypeFast paste test" in name:
                target = wid
                break
        if not target:
            print("ABORT: the test Chrome window never got the focus; nothing was typed")
            return
        subprocess.run(["xclip", "-i", "-selection", "clipboard"], input=MARKER.encode(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        typer = Typer(delay_ms=4, paste_threshold=1000)
        typer.set_target(LIVE)
        for _ in range(500):
            if typer.typed == LIVE:
                break
            time.sleep(0.02)
        if win.active_window() != target:
            print("ABORT: focus moved during the test")
            return
        read = "document.getElementById('t').innerText" if rich else "document.getElementById('t').value"
        live_seen = (cdp_eval(read) or "").replace("\u00a0", " ")
        done = threading.Event()
        res = {}
        typer.replace(FINAL, target, lambda ok, reason: (res.update(ok=ok, reason=reason), done.set()))
        done.wait(30)
        time.sleep(1.5)
        value = (cdp_eval(read) or "").replace("\u00a0", " ").rstrip("\n")
        print("live typed correctly:", live_seen == LIVE, "" if live_seen == LIVE else repr(live_seen))
        print("replace callback:", res)
        print("field after swap:", "OK" if value.replace("\n\n", "\n") == FINAL.replace("\n\n", "\n") else
              ("OLD CLIPBOARD PASTED" if MARKER in value else repr(value)))
        typer.close()
    finally:
        chrome.terminate()
        try:
            chrome.wait(5)
        except subprocess.TimeoutExpired:
            chrome.kill()


if __name__ == "__main__":
    main()
