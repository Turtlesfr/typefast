"""Small click-through bar shown while dictating (Qt version of hud.py, same methods)."""

import math
import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from typefast import textops
from typefast.win.qt import card_window, label, pointer_workarea, set_state

WIDTH = 640
SHADOW = 24  # card_window's margin around the card
METER_BARS = 9
SPINNER = "◐◓◑◒"


class _Paint(QWidget):
    def __init__(self, w: int, h: int, paint):
        super().__init__()
        self.setFixedSize(w, h)
        self._paint = paint

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        self._paint(p, self)
        p.end()


class Hud:
    def __init__(self):
        # Never takes the focus, and every click goes through to the app below.
        self.win, card = card_window(Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.card = card
        card.setFixedWidth(WIDTH)
        col = QVBoxLayout(card)
        col.setContentsMargins(14, 11, 14, 11)
        col.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.dot = _Paint(14, 14, self._draw_dot)
        self.spinner = label("", "count")
        self.check = label("✓", "count")
        self.chip = label("EN", "chip")
        self.status = label("Listening", "status")
        self.meter = _Paint(METER_BARS * 6, 18, self._draw_meter)
        self.timer = label("0:00", "timer")
        for w in (self.dot, self.spinner, self.check, self.chip, self.status):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.meter)
        row.addWidget(self.timer)
        col.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setObjectName("progress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        col.addWidget(self.bar)

        self.live = label("", "live", wrap=True)
        col.addWidget(self.live)
        self.hint = label("", "hudHint", wrap=True)
        col.addWidget(self.hint)

        self.position = "top"   # or "bottom"; form fields often sit at the bottom
        self.trigger_hint = "Ctrl ×3"
        self._mode = "hidden"
        self._session = None
        self._level = 0.0
        self._phase = 0.0
        self._progress = None      # returns the final swap's SwapProgress while replacing
        self._polished_s = 0.0
        self._ticker = QTimer()
        self._ticker.timeout.connect(self._tick)
        self._hider = QTimer()
        self._hider.setSingleShot(True)
        self._hider.timeout.connect(self.hide)

    # -- window -------------------------------------------------------------------

    def _place(self) -> None:
        area = pointer_workarea()
        self.win.adjustSize()
        h = self.win.sizeHint().height()
        x = area.x() + (area.width() - WIDTH) // 2 - SHADOW
        y = area.y() + 16 - SHADOW if self.position == "top" else area.y() + area.height() - h - 48 + SHADOW
        self.win.move(x, y)

    def _show(self, mode: str) -> None:
        self._mode = mode
        self._hider.stop()
        self.dot.setVisible(mode == "listening")
        self.meter.setVisible(mode == "listening")
        self.spinner.setVisible(mode in ("polishing", "replacing"))
        self.bar.setVisible(mode == "replacing")
        self.check.setVisible(mode == "done")
        set_state(self.card, "error", mode == "error")
        if mode != "replacing":
            self._progress = None
        self._place()
        self.win.show()
        if not self._ticker.isActive():
            self._ticker.start(80)

    def hide(self) -> None:
        self._mode = "hidden"
        self._session = None
        self.win.hide()
        self._ticker.stop()

    def _hide_later(self, seconds: float) -> None:
        self._hider.start(int(seconds * 1000))

    # -- states ---------------------------------------------------------------------

    def show_listening(self, session, language: str, trigger_hint: str, keyterm_count: int, style: str | None = None) -> None:
        self._session = session
        self.chip.setText(language.upper() + (f" · {style}" if style else ""))
        self.status.setText("Listening")
        self.live.setText("Start speaking. Text appears where your cursor is.")
        extra = f"   ·   {keyterm_count} vocabulary words active" if keyterm_count else ""
        self.hint.setText(f"{trigger_hint} to finish and polish{extra}")
        self.timer.setVisible(True)
        self._show("listening")

    def set_live_text(self, text: str) -> None:
        if text:
            self.live.setText(textops.tail(text, 220))

    def notice(self, message: str) -> None:
        self.hint.setText(message)
        self._place()

    def show_polishing(self) -> None:
        secs = int(self._session.elapsed) if self._session else 0
        self.status.setText(f"Polishing {secs // 60}:{secs % 60:02d} of audio")
        self.hint.setText(f"Hands off the keyboard for a moment.   {self.trigger_hint} keeps the live text.")
        self._show("polishing")

    def show_replacing(self, progress, polished_s: float, text: str) -> None:
        """The polished text is ready and replaces the live text; progress() -> SwapProgress | None."""
        self._progress = progress
        self._polished_s = polished_s
        self.live.setText(textops.tail(text, 220))
        self.bar.setValue(0)
        self._show("replacing")
        self._show_progress()

    def _show_progress(self) -> None:
        p = self._progress() if self._progress else None
        if p is None:
            return
        self.bar.setValue(int(p.fraction * 1000))
        doing = {"erase": "erasing the live text", "type": "typing it in",
                 "paste": "pasting it in"}.get(p.phase, "replacing the live text")
        self.status.setText(f"Polished in {self._polished_s:.1f} s · {doing}")
        left = math.ceil(p.seconds_left)
        self.hint.setText("Hands off the keyboard and mouse: "
                          + (f"about {left} s left." if left >= 2 else "almost done."))

    def show_done(self, message: str, detail: str = "", seconds: float = 3.0) -> None:
        self.status.setText(message)
        self.hint.setText(detail)
        self.timer.setVisible(False)
        self._show("done")
        self._hide_later(seconds)

    def show_error(self, message: str, detail: str = "", seconds: float = 7.0) -> None:
        self.status.setText(message)
        self.hint.setText(detail)
        self.timer.setVisible(False)
        self._show("error")
        self._hide_later(seconds)

    # -- animation ----------------------------------------------------------------

    def _tick(self) -> None:
        s = self._session
        if s is not None and self._mode == "listening":
            secs = int(s.elapsed)
            self.timer.setText(f"{secs // 60}:{secs % 60:02d}")
            self._level = s.level
        if self._mode == "replacing":
            self._show_progress()
        self._phase = (self._phase + 0.08) % 1.0
        self.spinner.setText(SPINNER[int(self._phase * len(SPINNER))])
        self.dot.update()
        self.meter.update()

    def _draw_dot(self, p: QPainter, _w) -> None:
        pulse = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi)
        p.setBrush(QColor(255, 89, 79, int(64 * pulse)))
        p.drawEllipse(QRectF(0, 0, 14, 14))
        p.setBrush(QColor(255, 89, 79))
        p.drawEllipse(QRectF(2.5, 2.5, 9, 9))

    def _draw_meter(self, p: QPainter, w) -> None:
        h = w.height()
        now = time.monotonic()
        p.setBrush(QColor(232, 237, 242, 230))
        for i in range(METER_BARS):
            shape = 1 - abs(i - METER_BARS // 2) / (METER_BARS / 1.6)
            wobble = 0.75 + 0.25 * math.sin(now * 9 + i * 1.7)
            frac = max(0.12, min(1.0, self._level * shape * wobble * 1.4))
            bar_h = max(2, h * frac)
            p.drawRect(QRectF(i * 6, (h - bar_h) / 2, 3, bar_h))
