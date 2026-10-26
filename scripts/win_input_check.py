"""Exercise the Windows key injector and input listener for real.

Types into a text box the script creates (see win_testwindow.py), and fakes
"user" keys (without TypeFast's marker) to check the listener and the
modifier safety. Keep your hands off the keyboard while it runs (about 5 s).
Usage: python scripts/win_input_check.py
"""

import ctypes
import threading
import time

from win_testwindow import TestWindow

from typefast.win import user32
from typefast.win.input import InputListener, WindowInfo
from typefast.win.keys import INPUT_KEYBOARD, KEYEVENTF_KEYUP, VK_CONTROL, KeyInjector, _INPUT, _KEYBDINPUT

TEXT = "J'ai regardé la roadmap OroCommerce : ça coûte 12 € œuvre 👍\nligne 2"
LONG = "Avanzamos a la par: hablamos para planear la agenda anual. " * 2


def user_key(vk: int, up: bool = False) -> None:
    """A key as if the user pressed it: no marker, so the listener must see it."""
    ev = _INPUT(type=INPUT_KEYBOARD, ki=_KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0))
    user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(_INPUT))


def main():
    seen = {"gesture": 0, "key": 0, "click": 0, "trigger_down": 0}
    listener = InputListener(
        on_gesture=lambda: seen.__setitem__("gesture", seen["gesture"] + 1),
        on_trigger_down=lambda: seen.__setitem__("trigger_down", seen["trigger_down"] + 1),
        on_trigger_up=lambda in_seq: None,
        on_user_input=lambda kind: seen.__setitem__(kind, seen[kind] + 1),
    )
    listener.start()
    box = TestWindow()
    results = {"focus": box.focus()}
    if not results["focus"]:
        box.close()
        raise SystemExit("NO FOCUS, not typing")
    keys = KeyInjector()
    stop = lambda: not box.has_focus()  # noqa: E731  (never type into another window)

    # Typing: accents, symbols, an emoji (surrogate pair) and a line break.
    sent = keys.type_text(TEXT, 2, stop)
    time.sleep(0.3)
    results["typed"] = (sent == len(TEXT), box.text() == TEXT)
    done = keys.backspace(7, stop, 1)
    time.sleep(0.3)
    results["backspace"] = (done == 7, box.text() == TEXT[:-7])
    # Erasing then typing again, as live corrections do.
    keys.backspace(len("12 € œuvre 👍\n"), stop, 1)
    keys.type_text("13 €", 2, stop)
    time.sleep(0.3)
    results["retype"] = box.text() == TEXT[:TEXT.index("12 €")] + "13 €"

    # Our own keys must be invisible to the listener.
    results["own_keys_ignored"] = seen["key"] == 0

    # A real Ctrl held mid-typing pauses the typing: no Ctrl+letter shortcut.
    box.set_text("")
    def hold_ctrl():
        time.sleep(0.05)
        user_key(VK_CONTROL)
        time.sleep(0.4)
        user_key(VK_CONTROL, up=True)
    threading.Thread(target=hold_ctrl).start()
    start = time.monotonic()
    sent = keys.type_text(LONG, 2, stop)
    time.sleep(0.3)
    # Either Ctrl was seen before a key and typing waited it out, or it slipped in between
    # the check and the key (the async key state lags SendInput) and typing stopped there.
    got = box.text()
    paused = sent == len(LONG) and got == LONG and not keys.collided and time.monotonic() - start > 0.4
    caught = keys.collided and sent < len(LONG) and LONG.startswith(got)
    results["ctrl_safe"] = ("waited" if paused else "caught" if caught else f"UNSAFE {got!r}", paused or caught)
    keys.collided = False

    # The listener: three quick Ctrl taps make a gesture, a letter is user input.
    box.set_text("")
    seen.update(gesture=0, key=0)
    for _ in range(3):
        user_key(VK_CONTROL)
        time.sleep(0.03)
        user_key(VK_CONTROL, up=True)
        time.sleep(0.08)
    time.sleep(0.1)
    if box.has_focus():
        user_key(0x41)
        user_key(0x41, up=True)
    time.sleep(0.2)
    results["gesture"] = seen["gesture"] == 1
    results["user_key"] = seen["key"] == 1
    win = WindowInfo()
    results["not_terminal"] = win.is_terminal(box.top) is False
    box.close()
    listener.stop()

    ok = True
    for name, value in results.items():
        passed = value[-1] if name == "ctrl_safe" else all(value) if isinstance(value, tuple) else value
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {name}  {value}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
