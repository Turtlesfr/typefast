"""The language and vocabulary picker that opens on the trigger."""

import logging
import time

from gi.repository import Gdk, GLib, Gtk, Pango

from typefast import languages, styles
from typefast.overlay_model import OverlayModel
from typefast.ui_common import add_class, glass_popup, keycaps, label, pointer_monitor_workarea, set_class
from typefast.vocab import Vocabulary, match_span

log = logging.getLogger(__name__)

CELL_HEIGHT = 34
CELL_SPACING = 6
CELL_MIN_WIDTH = 190
MAX_VISIBLE_ROWS = 7
MAX_LANG_COLUMNS = 6


class Overlay:
    def __init__(self, langs: list[str], last_language: str, vocab: Vocabulary, trigger_hint: str, on_start, on_close,
                 style: str = styles.DEFAULT_STYLE):
        self.vocab = vocab
        self.on_start = on_start
        self.on_close = on_close
        self._closed = False
        self._grab_tries = 0
        self._seat = None

        area = pointer_monitor_workarea()
        width = int(min(1280, max(640, area.width * 0.62)))
        cols = max(2, (width - 44) // CELL_MIN_WIDTH)
        lang_cols = min(len(langs), MAX_LANG_COLUMNS)
        index = langs.index(last_language) if last_language in langs else 0
        self.model = OverlayModel(langs, index, vocab, cols, lang_cols, style=style)
        self.last_index = index

        self.width = width
        self.win = glass_popup()
        self.win.set_default_size(width, -1)
        self.win.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.win.connect("key-press-event", self._on_key)
        self.win.connect("button-press-event", self._on_click_outside)
        self.win.connect("map-event", lambda *_: GLib.idle_add(self._grab))

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.set_size_request(width, -1)
        add_class(card, "tf-card")
        self.win.add(card)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        head.pack_start(label("TypeFast", "tf-brand"), False, False, 0)
        head.pack_end(label(f"Esc close · {trigger_hint} toggles", "tf-hint"), False, False, 0)
        card.pack_start(head, False, False, 0)

        # Styles: above the languages, reached with ↑ (most dictations keep the default)
        style_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        style_box.pack_start(label("STYLE", "tf-section"), False, False, 0)
        self.style_widgets = []
        for i, key in enumerate(styles.STYLES):
            pill = label(styles.LABELS[key], "tf-style", xalign=0.5)
            ev = Gtk.EventBox()
            ev.add(pill)
            ev.connect("button-press-event", self._on_style_click, i)
            style_box.pack_start(ev, False, False, 0)
            self.style_widgets.append(pill)
        self.style_desc = label("", "tf-style-desc", ellipsize=Pango.EllipsizeMode.END)
        style_box.pack_start(self.style_desc, True, True, 6)
        card.pack_start(style_box, False, False, 0)

        # Languages
        lang_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lang_box.pack_start(label("LANGUAGE", "tf-section"), False, False, 0)
        grid = Gtk.Grid(column_spacing=8, row_spacing=8, column_homogeneous=True)
        self.lang_widgets = []
        for i, code in enumerate(langs):
            lang = languages.get(code)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            add_class(box, "tf-lang")
            box.pack_start(label(code.upper(), "tf-lang-code"), False, False, 0)
            name = lang.native + ("  ·  last" if i == index else "")
            box.pack_start(label(name, "tf-lang-name", ellipsize=Pango.EllipsizeMode.END), False, False, 0)
            ev = Gtk.EventBox()
            ev.add(box)
            ev.connect("button-press-event", self._on_lang_click, i)
            grid.attach(ev, i % lang_cols, i // lang_cols, 1, 1)
            self.lang_widgets.append(box)
        # Keep a lone language from stretching across the whole card.
        for filler in range(len(langs), lang_cols):
            grid.attach(Gtk.Box(), filler, 0, 1, 1)
        lang_box.pack_start(grid, False, False, 0)
        card.pack_start(lang_box, False, False, 0)

        # Vocabulary
        voc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.pack_start(label("VOCABULARY", "tf-section"), False, False, 0)
        self.rows_label = label("", "tf-rows")
        row.pack_start(self.rows_label, False, False, 0)
        self.count_label = label("", "tf-count")
        row.pack_end(self.count_label, False, False, 0)
        voc.pack_start(row, False, False, 0)
        self.jump_label = label("", "tf-jump")
        self.jump_label.set_use_markup(True)
        voc.pack_start(self.jump_label, False, False, 0)

        self.cells = []
        self.cols = cols
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        voc.pack_start(self.scroller, False, False, 0)
        self.empty_label = label("No words yet. Type a name or a term, then press Enter to add it.", "tf-empty")
        voc.pack_start(self.empty_label, False, False, 4)
        self._build_cells()
        card.pack_start(voc, False, False, 0)

        self.msg_label = label("", "tf-msg")
        card.pack_start(self.msg_label, False, False, 0)

        foot = Gtk.Box()
        add_class(foot, "tf-foot")
        self.foot_lang = keycaps([(["←", "→"], "language"), (["↑"], "style"), (["↓"], "vocabulary"),
                                  (["a-z"], "jump to a word"), (["Enter"], "start dictating")])
        self.foot_style = keycaps([(["←", "→"], "style"), (["↓"], "language"), (["Enter"], "start dictating")])
        self.foot_vocab = keycaps([(["←", "→", "↑", "↓"], "move"), (["a-z"], "jump"), (["Space"], "tick"),
                                   (["Del"], "delete"), (["Ctrl", "Z"], "undo"), (["Enter"], "start dictating")])
        foot.pack_start(self.foot_style, False, False, 0)
        foot.pack_start(self.foot_lang, False, False, 0)
        foot.pack_start(self.foot_vocab, False, False, 0)
        card.pack_start(foot, False, False, 0)

        card.show_all()
        self._render()
        # Center on the monitor under the pointer.
        _, natural = card.get_preferred_height_for_width(width)
        self.win.move(area.x + (area.width - width) // 2, area.y + max(0, (area.height - natural) // 2 - 40))

    # -- display ------------------------------------------------------------------

    def _build_cells(self) -> None:
        """(Re)create the word grid, e.g. after a word was added or deleted."""
        child = self.scroller.get_child()
        if child is not None:
            self.scroller.remove(child)
            child.destroy()
        self.cells = []
        words, cols = self.vocab.words, self.cols
        self.total_rows = (len(words) + cols - 1) // cols
        self.visible_rows = max(1, min(self.total_rows, MAX_VISIBLE_ROWS))
        grid = Gtk.Grid(column_spacing=CELL_SPACING, row_spacing=CELL_SPACING, column_homogeneous=True)
        for i, word in enumerate(words):
            cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            cell.set_size_request(-1, CELL_HEIGHT)
            add_class(cell, "tf-cell")
            check = Gtk.Box()
            check.set_valign(Gtk.Align.CENTER)
            add_class(check, "tf-check")
            cell.pack_start(check, False, False, 0)
            lab = label(word, ellipsize=Pango.EllipsizeMode.END)
            lab.set_tooltip_text(word)
            cell.pack_start(lab, True, True, 0)
            ev = Gtk.EventBox()
            ev.add(cell)
            ev.connect("button-press-event", self._on_cell_click, i)
            grid.attach(ev, i % cols, i // cols, 1, 1)
            self.cells.append((cell, lab, word))
        for filler in range(len(words), cols):
            grid.attach(Gtk.Box(), filler, 0, 1, 1)
        height = self.visible_rows * CELL_HEIGHT + (self.visible_rows - 1) * CELL_SPACING + 4
        # Order matters: GTK rejects a max below the current min.
        if height > self.scroller.get_max_content_height():
            self.scroller.set_max_content_height(height)
            self.scroller.set_min_content_height(height)
        else:
            self.scroller.set_min_content_height(height)
            self.scroller.set_max_content_height(height)
        self.scroller.add(grid)
        grid.show_all()
        self.scroller.set_visible(bool(words))
        self.empty_label.set_visible(not words)

    def present(self) -> None:
        self.win.show_all()
        self._render()
        x, y = self.win.get_position()
        w, h = self.win.get_size()
        log.debug("overlay shown at %d,%d size %dx%d", x, y, w, h)

    def _render(self) -> None:
        m = self.model
        for i, pill in enumerate(self.style_widgets):
            set_class(pill, "tf-on", i == m.style)
            set_class(pill, "tf-dim", m.zone != "style")
        self.style_desc.set_text(styles.DESCRIPTIONS[m.style_key])
        for i, box in enumerate(self.lang_widgets):
            set_class(box, "tf-on", i == m.lang)
            set_class(box, "tf-dim", m.zone != "lang")
        for i, (cell, lab, word) in enumerate(self.cells):
            focus = m.zone == "vocab" and i == m.vi
            set_class(cell, "tf-sel", word in self.vocab.selected)
            set_class(cell, "tf-focus", focus)
            span = match_span(word, m.jump) if focus and m.jump and not m.miss else None
            if span:
                a, b = span
                lab.set_markup(GLib.markup_escape_text(word[:a]) + "<u>" + GLib.markup_escape_text(word[a:b]) + "</u>"
                               + GLib.markup_escape_text(word[b:]))
            else:
                lab.set_text(word)
        n_sel, n_words = len(self.vocab.selected), len(self.vocab.words)
        self.count_label.set_text(f"{n_sel} selected · {n_words} words" if n_words else "")
        if m.jump:
            typed = GLib.markup_escape_text(m.jump)
            if m.miss:
                text = f'new word: <span weight="bold">{typed}</span>_   Enter adds it and ticks it'
            else:
                text = f'jump: <span weight="bold">{typed}</span>_   Space ticks, keep typing for the next word, Ctrl+Enter adds it as a new word'
            self.jump_label.set_markup(text)
            # Unmatched letters are a new word to add, not an error: same accent color.
            set_class(self.jump_label, "tf-jump-miss", False)
            set_class(self.jump_label, "tf-jump-hit", True)
        elif self.cells:
            self.jump_label.set_text("type a few letters to jump to a word, or a new word to add it" if m.zone == "vocab"
                                     else "↓ or start typing to choose words")
            set_class(self.jump_label, "tf-jump-miss", False)
            set_class(self.jump_label, "tf-jump-hit", False)
        else:
            self.jump_label.set_text("")
        self.scroller.set_visible(bool(self.cells))
        self.empty_label.set_visible(not self.cells)
        self.msg_label.set_text(m.message)
        self.msg_label.set_visible(bool(m.message))
        self.foot_style.set_visible(m.zone == "style")
        self.foot_lang.set_visible(m.zone == "lang")
        self.foot_vocab.set_visible(m.zone == "vocab")
        self._scroll_to_focus()

    def _scroll_to_focus(self) -> None:
        if not self.cells:
            self.rows_label.set_text("")
            return
        pitch = CELL_HEIGHT + CELL_SPACING
        adj = self.scroller.get_vadjustment()
        if self.model.zone != "vocab":
            adj.set_value(0)
        else:
            row = self.model.vi // self.model.cols
            top, page = adj.get_value(), self.visible_rows * pitch
            if row * pitch < top:
                adj.set_value(row * pitch)
            elif (row + 1) * pitch > top + page:
                adj.set_value((row + 1) * pitch - page + 2)
        if self.total_rows > self.visible_rows:
            first = int(round(adj.get_value() / pitch)) + 1
            last = min(self.total_rows, first + self.visible_rows - 1)
            self.rows_label.set_text(f"rows {first} to {last} of {self.total_rows}")
        else:
            self.rows_label.set_text("")

    # -- keyboard -------------------------------------------------------------

    def _grab(self) -> bool:
        if self._closed:
            return False
        gdk_win = self.win.get_window()
        self._seat = Gdk.Display.get_default().get_default_seat()
        status = self._seat.grab(gdk_win, Gdk.SeatCapabilities.KEYBOARD | Gdk.SeatCapabilities.ALL_POINTING,
                                 False, None, None, None, None)
        if status == Gdk.GrabStatus.SUCCESS:
            log.debug("keyboard grabbed after %d tries", self._grab_tries)
            self.model.message = ""
            self._render()
            return False
        # GNOME sometimes holds the keyboard for a moment (e.g. a layout switch).
        self._grab_tries += 1
        if self._grab_tries == 10:
            self.model.message = "Waiting for the keyboard…"
            self._render()
        if self._grab_tries > 150:  # ~3 s
            log.warning("could not grab the keyboard: %s", status)
            self.close()
            return False
        GLib.timeout_add(20, self._grab)
        return False

    def _on_key(self, _w, event) -> bool:
        name = Gdk.keyval_name(event.keyval) or ""
        state = event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SUPER_MASK)
        code = Gdk.keyval_to_unicode(event.keyval)
        char = chr(code) if code and not state else ""
        if name in ("Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R"):
            return True
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        action = self.model.key(name, char, time.monotonic(), ctrl=ctrl)
        if self.model.changed:
            self.model.changed = False
            self._build_cells()
            self.win.resize(self.width, 1)  # shrink or grow to the new grid
        if action == "start":
            self._finish(start=True)
        elif action == "close":
            self.close()
        else:
            self._render()
        return True

    def _on_style_click(self, _w, _event, index: int) -> bool:
        self.model.style = index
        self.model.zone = "style"
        self._render()
        return True

    def _on_lang_click(self, _w, event, index: int) -> bool:
        self.model.lang = index
        self.model.zone = "lang"
        if event.type == Gdk.EventType._2BUTTON_PRESS:
            self._finish(start=True)
        else:
            self._render()
        return True

    def _on_cell_click(self, _w, _event, index: int) -> bool:
        self.model.zone = "vocab"
        self.model.vi = index
        self.model.key("space", "", time.monotonic())
        self._render()
        return True

    def _on_click_outside(self, _w, event) -> bool:
        alloc = self.win.get_allocation()
        if not (0 <= event.x <= alloc.width and 0 <= event.y <= alloc.height):
            self.close()
        return False

    # -- closing --------------------------------------------------------------------

    def _finish(self, start: bool) -> None:
        if self._closed:
            return
        self._closed = True
        if self._seat is not None:
            self._seat.ungrab()
        self.vocab.save()
        language, style = self.model.language, self.model.style_key
        self.win.destroy()
        if start:
            self.on_start(language, style)
        else:
            self.on_close()

    def close(self) -> None:
        self._finish(start=False)
