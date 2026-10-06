"""Detects a repeated tap of one modifier key, like Ctrl Ctrl Ctrl.

A tap is the key pressed and released on its own, quickly, with no other key
in between. Ctrl+C, Ctrl+V or a held Ctrl never count, and any other key
resets the sequence. Pure logic, fed with key events by the X11 listener.
"""


class TapDetector:
    def __init__(self, keycodes, taps: int = 3, window_ms: int = 1000, max_hold_ms: int = 350):
        self.keycodes = set(keycodes)
        self.taps = taps
        self.window_ms = window_ms
        self.max_hold_ms = max_hold_ms
        self._down_at: float | None = None
        self._clean = False
        self._times: list[float] = []

    def configure(self, keycodes=None, taps=None, window_ms=None):
        if keycodes is not None:
            self.keycodes = set(keycodes)
        if taps is not None:
            self.taps = taps
        if window_ms is not None:
            self.window_ms = window_ms
        self.reset()

    def reset(self):
        self._down_at = None
        self._clean = False
        self._times = []

    @property
    def armed(self) -> bool:
        """True while the trigger key is held alone: a tap may be under way."""
        return self._down_at is not None and self._clean

    @property
    def in_sequence(self) -> bool:
        return bool(self._times)

    def press(self, keycode: int, t_ms: float) -> None:
        if keycode in self.keycodes:
            if self._down_at is None:  # ignore auto-repeat and duplicates
                self._down_at = t_ms
                self._clean = True
            return
        # Any other key breaks a tap in progress and the whole sequence.
        self._clean = False
        self._times = []

    def release(self, keycode: int, t_ms: float) -> bool:
        """Returns True when this release completes the gesture."""
        if keycode not in self.keycodes or self._down_at is None:
            return False
        held = t_ms - self._down_at
        clean = self._clean
        self._down_at = None
        self._clean = False
        if not clean or held > self.max_hold_ms:
            self._times = []
            return False
        self._times = [t for t in self._times if t_ms - t <= self.window_ms]
        self._times.append(t_ms)
        if len(self._times) >= self.taps:
            self._times = []
            return True
        return False
