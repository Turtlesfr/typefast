"""Windows version of typer_check.py: live updates, Ctrl held, final swap (typed and pasted).

Types into a text box the script creates (see win_testwindow.py); if it cannot
get the focus, nothing is typed, and the Typer freezes if the focus moves.
Keep your hands off the keyboard while it runs (about 15 s).
Usage: python scripts/win_typer_check.py
"""

import ctypes
import threading
import time

from win_testwindow import TestWindow

from typefast.injector import Typer
from typefast.win import user32
from typefast.win.keys import INPUT_KEYBOARD, KEYEVENTF_KEYUP, VK_CONTROL, _INPUT, _KEYBDINPUT

PREFILL = "Bonjour Camille,\n"
LIVE = ["j'ai", "j'ai regardé la", "j'ai regardé la roadmap oro", "j'ai regardé la roadmap oro commerce",
        "J'ai regardé la roadmap Oro commerce.", "J'ai regardé la roadmap Oro commerce. le connecteur a kenio"]
FINAL_SHORT = "J'ai regardé la roadmap OroCommerce. Le connecteur Akeneo est prêt."
CTRL_TEXT = "Avanzamos a la par: hablamos para planear la agenda anual, ya casi acabada, gracias a Ana y a Alba. " * 2
FINAL_LONG = FINAL_SHORT + "\n\n" + "Est-ce qu'on peut caler 30 minutes vendredi pour en parler ? Ça m'arrangerait beaucoup. " * 2


def user_key(vk: int, up: bool = False) -> None:
    """A key as if the user pressed it (no TypeFast marker)."""
    ev = _INPUT(type=INPUT_KEYBOARD, ki=_KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0))
    user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(_INPUT))


def main():
    box = TestWindow()
    if not box.focus():
        box.close()
        raise SystemExit("NO FOCUS, not typing")
    box.set_text(PREFILL)
    typer = Typer(delay_ms=4, paste_threshold=1000)
    results = {}

    def wait_idle():
        for _ in range(400):
            if typer.typed == typer._target:
                return
            time.sleep(0.02)

    for step in LIVE:
        typer.set_target(step)
        time.sleep(0.25)
    wait_idle()
    time.sleep(0.3)
    results["live"] = box.text()

    # A Ctrl held in the middle of typing must never combine with a letter.
    done = threading.Event()
    typer.replace(CTRL_TEXT, box.top, lambda ok, reason: (results.__setitem__("ctrl_ok", (ok, reason)), done.set()))
    time.sleep(0.25)
    user_key(VK_CONTROL)
    time.sleep(0.4)
    user_key(VK_CONTROL, up=True)
    done.wait(30)
    time.sleep(0.5)
    results["ctrl"] = box.text()
    results["ctrl_frozen"] = typer.frozen

    for name, final, threshold in (("typed", FINAL_SHORT, 1000), ("pasted", FINAL_LONG, 50)):
        typer.reset()
        box.set_text(PREFILL)
        time.sleep(0.3)
        typer.paste_threshold = threshold
        typer.set_target(LIVE[-1])
        wait_idle()
        done = threading.Event()
        typer.replace(final, box.top, lambda ok, reason: (results.__setitem__(name + "_ok", (ok, reason)), done.set()))
        done.wait(30)
        time.sleep(0.6)
        results[name] = box.text()
    results["pasted_flag"] = typer.pasted
    typer.close()
    box.close()

    ctrl_text = results.get("ctrl") or ""
    rest = ctrl_text[len(PREFILL):]
    checks = {
        "live": results.get("live") == PREFILL + LIVE[-1],
        # As in typer_check.py: Ctrl waited out, or a collision caught and the field left alone.
        "ctrl": (ctrl_text == PREFILL + CTRL_TEXT and results.get("ctrl_ok") == (True, ""))
                or (results.get("ctrl_frozen") and ctrl_text.startswith(PREFILL) and CTRL_TEXT.startswith(rest)),
        "typed": results.get("typed") == PREFILL + FINAL_SHORT and results.get("typed_ok") == (True, ""),
        "pasted": results.get("pasted") == PREFILL + FINAL_LONG and results.get("pasted_ok") == (True, "")
                  and results.get("pasted_flag"),
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  {results.get(name)!r} {results.get(name + '_ok')!r}"))
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
