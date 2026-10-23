"""Small click-through bar shown while dictating, at the top of the screen by default."""

import math
import time

import cairo
from gi.repository import GLib, Gtk, Pango

from typefast import textops
from typefast.ui_common import add_class, glass_popup, label, pointer_monitor_workarea, set_class

WIDTH = 640
METER_BARS = 9


class Hud:
    def __init__(self):
        self.win = glass_popup()
        self.win.set_accept_focus(False)
        self.win.connect("realize", self._make_click_through)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.set_size_request(WIDTH, -1)
        add_class(card, "tf-card", "tf-hud")
        self.card = card
        self.win.add(card)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.dot = Gtk.DrawingArea()
        self.dot.set_size_request(14, 14)
        self.dot.connect("draw", self._draw_dot)
        self.spinner = Gtk.Spinner()
        self.check = label("✓", "tf-count")
        self.chip = label("EN", "tf-chip")
        self.status = label("Listening", "tf-status")
        self.meter = Gtk.DrawingArea()
        self.meter.set_size_request(METER_BARS * 6, 18)
        self.meter.connect("draw", self._draw_meter)
        self.timer = label("0:00", "tf-timer")
        for w in (self.dot, self.spinner, self.check, self.chip, self.status):
            row.pack_start(w, False, False, 0)
        row.pack_end(self.timer, False, False, 0)
        row.pack_end(self.meter, False, False, 0)
        card.pack_start(row, False, False, 0)
        self.bar = Gtk.ProgressBar()
        add_class(self.bar, "tf-progress")
        card.pack_start(self.bar, False, False, 0)

        self.live = label("", "tf-live", wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.START)
        self.live.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.live.set_max_width_chars(80)
        card.pack_start(self.live, False, False, 0)
        self.hint = label("", "tf-hud-hint", wrap=True)
        card.pack_start(self.hint, False, False, 0)
        card.show_all()

        self.position = "top"   # or "bottom"; form fields often sit at the bottom
        self.trigger_hint = "Ctrl ×3"
        self._mode = "hidden"
        self._session = None
        self._tick_id = 0
        self._hide_id = 0
        self._level = 0.0
        self._phase = 0.0
        self._progress = None      # returns the final swap's SwapProgress while replacing
        self._polished_s = 0.0

    # -- window -------------------------------------------------------------------

    def _make_click_through(self, *_):
        # An empty input region lets every click fall through to the app below.
        self.win.input_shape_combine_region(cairo.Region())

    def _place(self) -> None:
        area = pointer_monitor_workarea()
        _, h = self.card.get_preferred_height_for_width(WIDTH)
        x = area.x + (area.width - WIDTH) // 2
        y = area.y + 16 if self.position == "top" else area.y + area.height - h - 48
        self.win.move(x, y)

    def _show(self, mode: str) -> None:
        self._mode = mode
        if self._hide_id:
            GLib.source_remove(self._hide_id)
            self._hide_id = 0
        self.dot.set_visible(mode == "listening")
        self.meter.set_visible(mode == "listening")
        self.spinner.set_visible(mode in ("polishing", "replacing"))
        self.bar.set_visible(mode == "replacing")
        self.check.set_visible(mode == "done")
        set_class(self.card, "tf-hud-error", mode == "error")
        if mode != "replacing":
            self._progress = None
        if mode in ("polishing", "replacing"):
            self.spinner.start()
        else:
            self.spinner.stop()
        self._place()
        self.win.show()
        if not self._tick_id:
            self._tick_id = GLib.timeout_add(80, self._tick)

    def hide(self) -> None:
        self._mode = "hidden"
        self._session = None
        self.win.hide()
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0

    def _hide_later(self, seconds: float) -> None:
        if self._hide_id:
            GLib.source_remove(self._hide_id)
        self._hide_id = GLib.timeout_add(int(seconds * 1000), self._auto_hide)

    def _auto_hide(self) -> bool:
        self._hide_id = 0
        self.hide()
        return False

    # -- states ---------------------------------------------------------------------

    def show_listening(self, session, language: str, trigger_hint: str, keyterm_count: int, style: str | None = None) -> None:
        self._session = session
        self.chip.set_text(language.upper() + (f" · {style}" if style else ""))
        self.status.set_text("Listening")
        self.live.set_text("Start speaking. Text appears where your cursor is.")
        extra = f"   ·   {keyterm_count} vocabulary words active" if keyterm_count else ""
        self.hint.set_text(f"{trigger_hint} to finish and polish{extra}")
        self.timer.set_visible(True)
        self._show("listening")

    def set_live_text(self, text: str) -> None:
        if text:
            self.live.set_text(textops.tail(text, 220))

    def notice(self, message: str) -> None:
        self.hint.set_text(message)
        self._place()

    def show_polishing(self) -> None:
        secs = int(self._session.elapsed) if self._session else 0
        self.status.set_text(f"Polishing {secs // 60}:{secs % 60:02d} of audio")
        self.hint.set_text(f"Hands off the keyboard for a moment.   {self.trigger_hint} keeps the live text.")
        self._show("polishing")

    def show_replacing(self, progress, polished_s: float, text: str) -> None:
        """The polished text is ready and replaces the live text; progress() -> SwapProgress | None."""
        self._progress = progress
        self._polished_s = polished_s
        self.live.set_text(textops.tail(text, 220))
        self.bar.set_fraction(0.0)
        self._show("replacing")
        self._show_progress()

    def _show_progress(self) -> None:
        p = self._progress() if self._progress else None
        if p is None:
            return
        self.bar.set_fraction(p.fraction)
        doing = {"erase": "erasing the live text", "type": "typing it in",
                 "paste": "pasting it in"}.get(p.phase, "replacing the live text")
        self.status.set_text(f"Polished in {self._polished_s:.1f} s · {doing}")
        left = math.ceil(p.seconds_left)
        self.hint.set_text("Hands off the keyboard and mouse: "
                           + (f"about {left} s left." if left >= 2 else "almost done."))

    def show_done(self, message: str, detail: str = "", seconds: float = 3.0) -> None:
        self.status.set_text(message)
        self.hint.set_text(detail)
        self.timer.set_visible(False)
        self._show("done")
        self._hide_later(seconds)

    def show_error(self, message: str, detail: str = "", seconds: float = 7.0) -> None:
        self.status.set_text(message)
        self.hint.set_text(detail)
        self.timer.set_visible(False)
        self._show("error")
        self._hide_later(seconds)

    # -- animation ----------------------------------------------------------------

    def _tick(self) -> bool:
        if self._mode == "hidden":
            self._tick_id = 0
            return False
        s = self._session
        if s is not None and self._mode == "listening":
            secs = int(s.elapsed)
            self.timer.set_text(f"{secs // 60}:{secs % 60:02d}")
            self._level = s.level
        if self._mode == "replacing":
            self._show_progress()
        self._phase = (self._phase + 0.08) % 1.0
        self.dot.queue_draw()
        self.meter.queue_draw()
        return True

    def _draw_dot(self, _w, cr) -> None:
        pulse = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi)
        cr.set_source_rgba(1.0, 0.35, 0.31, 0.25 * pulse)
        cr.arc(7, 7, 7, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgb(1.0, 0.35, 0.31)
        cr.arc(7, 7, 4.5, 0, 2 * math.pi)
        cr.fill()

    def _draw_meter(self, w, cr) -> None:
        h = w.get_allocated_height()
        now = time.monotonic()
        for i in range(METER_BARS):
            shape = 1 - abs(i - METER_BARS // 2) / (METER_BARS / 1.6)
            wobble = 0.75 + 0.25 * math.sin(now * 9 + i * 1.7)
            frac = max(0.12, min(1.0, self._level * shape * wobble * 1.4))
            bar_h = max(2, h * frac)
            cr.set_source_rgba(0.91, 0.93, 0.95, 0.9)
            cr.rectangle(i * 6, (h - bar_h) / 2, 3, bar_h)
            cr.fill()
