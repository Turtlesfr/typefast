"""Types into whatever field has the keyboard focus, through XTEST (see xkeys.py).

One worker thread owns the field. Callers only say what the text should be
(`set_target`); the worker turns that into Backspace presses and typing,
always working from what it typed itself. It types in small chunks, never
while a modifier key is held, and pauses while the trigger key is down, so a
real Ctrl press can never combine with an injected letter into Ctrl+W.
"""

import logging
import subprocess
import threading
import time

from typefast import clipboard, textops
from typefast.x11 import WindowInfo
from typefast.xkeys import KeyInjector

log = logging.getLogger(__name__)

LIVE_CHUNK = 8      # characters per step while dictating (the target is re-read between steps)
FINAL_CHUNK = 48    # characters per step for the final replacement
BACKSPACE_BATCH = 120
# Apps behind IBus (GTK's default input method) lose a few keys when erasing
# starts right after typing, or typing right after erasing: measured 1 wrong
# rewrite in 12 with no pause, none in 36 with 30-60 ms.
DIRECTION_SETTLE_S = 0.06


def settle_delay(last_op: str | None, op: str, since: float) -> float:
    """Seconds to wait before `op` ("type" or "erase") after `last_op` ended `since` seconds ago."""
    if last_op is None or last_op == op:
        return 0.0
    return max(0.0, DIRECTION_SETTLE_S - since)


def _run(args, input_text=None, timeout=10.0, capture=True):
    # `xclip -i` forks a child that keeps serving the clipboard; capturing its
    # output would wait on that child forever, hence capture=False for it.
    out = subprocess.PIPE if capture else subprocess.DEVNULL
    try:
        return subprocess.run(args, input=input_text, stdout=out, stderr=out, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("%s failed: %s", args[0], e)
        return None


def _ok(result) -> bool:
    return result is not None and result.returncode == 0


class Typer:
    def __init__(self, delay_ms: int = 4, paste_threshold: int = 300):
        self.delay_ms = delay_ms
        self.paste_threshold = paste_threshold
        self._cond = threading.Condition()
        self._typed = ""
        self._target = ""
        self._hold = False
        self._paused_until = 0.0
        self._frozen = False
        self._gen = 0               # bumped by reset(): stale steps must not touch the new session
        self._window = 0            # window that received the first character this session
        self._final = None          # (text, guard_window, callback) waiting to start
        self._final_active = None   # callback waiting for the final text to land
        self._closing = False
        self._clip = None           # ClipboardServer, created in the worker thread
        self.pasted = False         # the last final text went in through the clipboard
        self._last_op = None        # "type" or "erase", for the settle pause
        self._last_op_end = 0.0
        self.on_first_type = None   # called with the active window id
        self.on_moved = None        # called when the focus left the window we type into
        self._thread = threading.Thread(target=self._loop, name="typefast-typer", daemon=True)
        self._thread.start()

    # -- control (any thread) -------------------------------------------------

    @property
    def typed(self) -> str:
        with self._cond:
            return self._typed

    def reset(self) -> None:
        with self._cond:
            self.pasted = False
            self._gen += 1
            self._typed = self._target = ""
            self._window = 0
            self._frozen = False
            self._final = self._final_active = None
            self._cond.notify_all()

    def set_target(self, text: str) -> None:
        with self._cond:
            if not self._frozen and self._final is None and self._final_active is None:
                self._target = text
                self._cond.notify_all()

    def settle(self) -> None:
        """Stop chasing live text: keep what is already typed."""
        with self._cond:
            self._target = self._typed
            self._cond.notify_all()

    def _fail_final(self, reason: str) -> None:
        """Abort a pending or running final swap; its callback reports failure."""
        cb = None
        if self._final is not None:
            cb = self._final[2]
        elif self._final_active is not None:
            cb = self._final_active
        self._final = self._final_active = None
        if cb is not None:
            threading.Thread(target=cb, args=(False, reason), daemon=True).start()

    def freeze(self) -> None:
        """Never touch the field again this session (the user moved the caret)."""
        with self._cond:
            self._frozen = True
            self._target = self._typed
            self._fail_final("moved")
            self._cond.notify_all()

    def unfreeze(self) -> None:
        with self._cond:
            self._frozen = False
            self._cond.notify_all()

    def cancel_final(self) -> None:
        """Drop a final swap without calling anyone back (the session was cancelled)."""
        with self._cond:
            self._final = self._final_active = None
            self._target = self._typed
            self._cond.notify_all()

    @property
    def frozen(self) -> bool:
        return self._frozen

    def hold(self, down: bool) -> None:
        with self._cond:
            self._hold = down
            self._cond.notify_all()

    def pause_for(self, seconds: float) -> None:
        with self._cond:
            self._paused_until = max(self._paused_until, time.monotonic() + seconds)
            self._cond.notify_all()

    def replace(self, text: str, guard_window: int, callback) -> None:
        """Swap everything typed this session for `text`.

        callback(ok, reason) runs on a helper thread. It fails, without
        touching the field, if another window has the focus now.
        """
        with self._cond:
            self._final = (text, guard_window, callback)
            self._cond.notify_all()

    def close(self, timeout: float = 1.5) -> None:
        """Stop the worker and give back the spare keycodes it borrowed."""
        with self._cond:
            self._closing = True
            self._cond.notify_all()
        self._thread.join(timeout)

    # -- worker -----------------------------------------------------------------

    def _blocked(self) -> bool:
        return self._hold or time.monotonic() < self._paused_until

    def _stopper(self, gen: int):
        """Checked before every key: stop at once on a trigger press, a freeze or a new session."""
        return lambda: self._blocked() or self._frozen or self._closing or gen != self._gen

    def _loop(self) -> None:
        win = WindowInfo()
        keys = KeyInjector()
        try:
            self._work(win, keys)
        finally:
            keys.close()
            if self._clip is not None:
                self._clip.close()

    def _work(self, win: WindowInfo, keys: KeyInjector) -> None:
        while not self._closing:
            with self._cond:
                while not self._closing:
                    if self._final is not None and not self._blocked():
                        break
                    if not self._frozen and self._typed != self._target and not self._blocked():
                        break
                    if self._final_active and self._typed == self._target:
                        cb, self._final_active = self._final_active, None
                        threading.Thread(target=cb, args=(True, ""), daemon=True).start()
                    wait = max(0.02, self._paused_until - time.monotonic()) if self._blocked() else None
                    self._cond.wait(wait)
                if self._closing:
                    return
                final, self._final = self._final, None
                typed, target, gen, window = self._typed, self._target, self._gen, self._window
            try:
                if final is not None:
                    self._start_final(win, *final)
                    continue
                if keys.modifiers_held():
                    time.sleep(0.03)
                    continue
                if typed and window and win.active_window() != window:
                    # Focus moved without a click or key (a dialog, a new window):
                    # typing on would land, and Backspace would delete, elsewhere.
                    self.freeze()
                    if self.on_moved:
                        self.on_moved()
                    continue
                progressed = self._step(win, keys, typed, target, gen)
                if keys.collided:
                    # A real Ctrl/Alt/Super combined with one of our keys: the app may have
                    # run a shortcut (select all, delete word). Never edit this field again.
                    keys.collided = False
                    self.freeze()
                    if self.on_moved:
                        self.on_moved()
                elif not progressed:
                    time.sleep(0.05)
            except Exception as e:  # noqa: BLE001
                log.exception("typing failed: %s", e)
                time.sleep(0.2)

    def _start_final(self, win: WindowInfo, text: str, guard: int, callback) -> None:
        with self._cond:
            nothing_typed = not self._typed
            frozen = self._frozen
        # With nothing typed yet, the final text simply goes in at the caret.
        if not nothing_typed and (frozen or (guard and win.active_window() != guard)):
            threading.Thread(target=callback, args=(False, "moved"), daemon=True).start()
            return
        with self._cond:
            self._target = text
            self._final_active = callback
            self._cond.notify_all()

    def _commit(self, gen: int, typed: str) -> bool:
        """Record the new field content, unless reset() started a new session meanwhile."""
        with self._cond:
            if gen != self._gen:
                return False
            self._typed = typed
            return True

    def _step(self, win: WindowInfo, keys: KeyInjector, typed: str, target: str, gen: int) -> bool:
        """One step toward the target. False if nothing could be sent."""
        final_mode = self._final_active is not None
        # Multi-line or long final text goes in by pasting the whole of it: erase all the live
        # text first, so the clipboard (where it stays) holds the complete text.
        paste_mode = final_mode and ("\n" in target or len(target) >= self.paste_threshold)
        edit = textops.Edit(len(typed), target) if paste_mode and typed else textops.plan_edit(typed, target)
        stop = self._stopper(gen)
        log.debug("step: typed=%r target=%r -> back %d, insert %r", typed[-20:], target[-20:], edit.backspaces, edit.insert[:20])
        if edit.backspaces:
            self._settle("erase")
            done = keys.backspace(min(edit.backspaces, BACKSPACE_BATCH), stop)
            self._op_done("erase")
            if done:
                self._commit(gen, typed[: len(typed) - done])
            return done > 0
        if not edit.insert:
            return True
        first = not typed
        self._settle("type")
        # Line breaks are pasted: a typed Return would send the message in chat apps.
        if paste_mode and self._paste(win, keys, edit.insert, stop):
            piece = edit.insert
        else:
            chunk = textops.next_chunk(edit.insert, FINAL_CHUNK if final_mode else LIVE_CHUNK)
            sent = keys.type_text(chunk, self.delay_ms, stop)
            piece = chunk[:sent]
        self._op_done("type")
        if not piece:
            return False
        if self._commit(gen, typed + piece) and first:
            window = win.active_window()
            with self._cond:
                if gen == self._gen:
                    self._window = window
            if self.on_first_type:
                self.on_first_type(window)
        return True

    def _settle(self, op: str) -> None:
        wait = settle_delay(self._last_op, op, time.monotonic() - self._last_op_end)
        if wait:
            time.sleep(wait)

    def _op_done(self, op: str) -> None:
        self._last_op, self._last_op_end = op, time.monotonic()

    def _paste(self, win: WindowInfo, keys: KeyInjector, text: str, stop) -> bool:
        """Paste through the clipboard. False if Ctrl+V could not be sent (the caller types instead).

        The text stays in the clipboard afterwards (see clipboard.py for why).
        """
        if self._clip is None:
            self._clip = clipboard.ClipboardServer()
        target = win.active_window()
        if not self._clip.paste(text, lambda: keys.paste(win.is_terminal(target), stop), target):
            return False
        self.pasted = True
        log.info("pasted %d characters", len(text))
        return True


def copy_to_clipboard(text: str) -> None:
    _run(["xclip", "-i", "-selection", "clipboard"], input_text=text.encode(), timeout=2.0, capture=False)
