"""The language and vocabulary picker that opens on the trigger (Qt version of overlay.py).

On X11 the overlay grabs the keyboard without taking the focus. Windows has no
such grab, so the overlay takes the foreground and hands it back to the window
the user was typing in before dictation starts; that window keeps its caret.
"""

import html
import logging
import time

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from typefast import languages, styles
from typefast.overlay_model import OverlayModel
from typefast.vocab import Vocabulary, match_span
from typefast.win.qt import (card_window, force_foreground, foreground_window, keycaps, label, pointer_workarea,
                             restore_foreground, set_state)

log = logging.getLogger(__name__)

CELL_HEIGHT = 34
CELL_SPACING = 6
CELL_MIN_WIDTH = 190
MAX_VISIBLE_ROWS = 7
MAX_LANG_COLUMNS = 6
SHADOW = 24

# Qt keys -> the GDK key names OverlayModel speaks.
KEY_NAMES = {
    Qt.Key.Key_Return: "Return", Qt.Key.Key_Enter: "KP_Enter", Qt.Key.Key_Escape: "Escape",
    Qt.Key.Key_Left: "Left", Qt.Key.Key_Right: "Right", Qt.Key.Key_Up: "Up", Qt.Key.Key_Down: "Down",
    Qt.Key.Key_Tab: "Tab", Qt.Key.Key_Backtab: "ISO_Left_Tab", Qt.Key.Key_Space: "space",
    Qt.Key.Key_Backspace: "BackSpace", Qt.Key.Key_Delete: "Delete", Qt.Key.Key_Z: "z",
}
MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr}


class _Clickable(QFrame):
    def __init__(self, name: str, on_click, on_double=None):
        super().__init__()
        self.setObjectName(name)
        self._on_click, self._on_double = on_click, on_double

    def mousePressEvent(self, _event):
        self._on_click()

    def mouseDoubleClickEvent(self, _event):
        (self._on_double or self._on_click)()


class _Window(QWidget):
    """Forwards keys and activation changes to the Overlay (set as .owner)."""

    owner = None

    def keyPressEvent(self, event):
        self.owner._on_key(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and self.owner:
            self.owner._on_activation(self.isActiveWindow())
        super().changeEvent(event)


class Overlay:
    def __init__(self, langs: list[str], last_language: str, vocab: Vocabulary, trigger_hint: str, on_start, on_close,
                 style: str = styles.DEFAULT_STYLE):
        self.vocab = vocab
        self.on_start = on_start
        self.on_close = on_close
        self._closed = False
        self._activated = False
        self._previous = foreground_window()  # where dictation will type

        area = pointer_workarea()
        width = int(min(1280, max(640, area.width() * 0.62)))
        cols = max(2, (width - 44) // CELL_MIN_WIDTH)
        lang_cols = min(len(langs), MAX_LANG_COLUMNS)
        index = langs.index(last_language) if last_language in langs else 0
        self.model = OverlayModel(langs, index, vocab, cols, lang_cols, style=style)
        self.cols = cols
        self.width = width

        self.win, card = card_window(cls=_Window)
        self.win.owner = self
        card.setFixedWidth(width)
        col = QVBoxLayout(card)
        col.setContentsMargins(20, 18, 20, 14)
        col.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(label("TypeFast", "brand"))
        head.addStretch(1)
        head.addWidget(label(f"Esc close · {trigger_hint} toggles", "hint"))
        col.addLayout(head)

        # Styles: above the languages, reached with ↑ (most dictations keep the default)
        style_row = QHBoxLayout()
        style_row.setSpacing(8)
        style_row.addWidget(label("STYLE", "section"))
        self.style_widgets = []
        for i, key in enumerate(styles.STYLES):
            pill = _Clickable("style", lambda i=i: self._on_style_click(i))
            QHBoxLayout(pill).addWidget(label(styles.LABELS[key]))
            pill.layout().setContentsMargins(0, 0, 0, 0)
            style_row.addWidget(pill)
            self.style_widgets.append(pill)
        self.style_desc = label("", "styleDesc")
        style_row.addSpacing(6)
        style_row.addWidget(self.style_desc, 1)
        col.addLayout(style_row)

        # Languages
        col.addWidget(label("LANGUAGE", "section"))
        grid = QGridLayout()
        grid.setSpacing(8)
        self.lang_widgets = []
        for i, code in enumerate(langs):
            lang = languages.get(code)
            box = _Clickable("lang", lambda i=i: self._on_lang_click(i, False), lambda i=i: self._on_lang_click(i, True))
            inner = QVBoxLayout(box)
            inner.setContentsMargins(12, 9, 12, 9)
            inner.setSpacing(1)
            inner.addWidget(label(code.upper(), "langCode"))
            inner.addWidget(label(lang.native + ("  ·  last" if i == index else ""), "langName"))
            grid.addWidget(box, i // lang_cols, i % lang_cols)
            self.lang_widgets.append(box)
        for c in range(lang_cols):
            grid.setColumnStretch(c, 1)
        col.addLayout(grid)

        # Vocabulary
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(label("VOCABULARY", "section"))
        self.rows_label = label("", "rows")
        row.addWidget(self.rows_label)
        row.addStretch(1)
        self.count_label = label("", "count")
        row.addWidget(self.count_label)
        col.addLayout(row)
        self.jump_label = label("", "jump")
        self.jump_label.setTextFormat(Qt.TextFormat.RichText)
        col.addWidget(self.jump_label)

        self.cells = []
        self.scroller = QScrollArea()
        self.scroller.setWidgetResizable(True)
        self.scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroller.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroller.verticalScrollBar().valueChanged.connect(lambda *_: self._update_rows_label())
        col.addWidget(self.scroller)
        self.empty_label = label("No words yet. Type a name or a term, then press Enter to add it.", "empty")
        col.addWidget(self.empty_label)
        self._build_cells()

        self.msg_label = label("", "msg")
        col.addWidget(self.msg_label)

        foot = QWidget()
        foot.setObjectName("foot")
        foot.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        foot_row = QVBoxLayout(foot)
        foot_row.setContentsMargins(0, 10, 0, 0)
        self.foot_lang = keycaps([(["←", "→"], "language"), (["↑"], "style"), (["↓"], "vocabulary"),
                                  (["a-z"], "jump to a word"), (["Enter"], "start dictating")])
        self.foot_style = keycaps([(["←", "→"], "style"), (["↓"], "language"), (["Enter"], "start dictating")])
        self.foot_vocab = keycaps([(["←", "→", "↑", "↓"], "move"), (["a-z"], "jump"), (["Space"], "tick"),
                                   (["Del"], "delete"), (["Ctrl", "Z"], "undo"), (["Enter"], "start dictating")])
        for f in (self.foot_style, self.foot_lang, self.foot_vocab):
            foot_row.addWidget(f)
        col.addWidget(foot)
        self._render()

    # -- display ------------------------------------------------------------------

    def _build_cells(self) -> None:
        """(Re)create the word grid, e.g. after a word was added or deleted."""
        old = self.scroller.takeWidget()
        if old is not None:
            old.deleteLater()
        self.cells = []
        words, cols = self.vocab.words, self.cols
        self.total_rows = (len(words) + cols - 1) // cols
        self.visible_rows = max(1, min(self.total_rows, MAX_VISIBLE_ROWS))
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(CELL_SPACING)
        for i, word in enumerate(words):
            cell = _Clickable("cell", lambda i=i: self._on_cell_click(i))
            cell.setFixedHeight(CELL_HEIGHT)
            inner = QHBoxLayout(cell)
            inner.setContentsMargins(9, 0, 10, 0)
            inner.setSpacing(8)
            check = QFrame()
            check.setObjectName("check")
            check.setFixedSize(10, 10)
            inner.addWidget(check)
            lab = label(word)
            lab.setToolTip(word)
            lab.setMinimumWidth(1)  # long words get cut instead of widening the grid
            inner.addWidget(lab, 1)
            grid.addWidget(cell, i // cols, i % cols)
            self.cells.append((cell, lab, word))
        for c in range(cols):
            grid.setColumnStretch(c, 1)
        grid.setRowStretch(self.total_rows, 1)
        self.scroller.setWidget(holder)
        self.scroller.setFixedHeight(self.visible_rows * CELL_HEIGHT + (self.visible_rows - 1) * CELL_SPACING + 4)
        self.scroller.setVisible(bool(words))
        self.empty_label.setVisible(not words)

    def present(self) -> None:
        area = pointer_workarea()
        self.win.adjustSize()
        h = self.win.sizeHint().height()
        self.win.move(area.x() + (area.width() - self.width) // 2 - SHADOW,
                      area.y() + max(0, (area.height() - h) // 2 - 40) - SHADOW)
        self.win.show()
        self.win.raise_()
        force_foreground(int(self.win.winId()))
        self.win.activateWindow()
        self._render()
        log.debug("overlay shown, previous foreground %#x", self._previous)

    def _render(self) -> None:
        m = self.model
        for i, pill in enumerate(self.style_widgets):
            set_state(pill, "on", i == m.style)
            set_state(pill, "dim", m.zone != "style")
        self.style_desc.setText(styles.DESCRIPTIONS[m.style_key])
        for i, box in enumerate(self.lang_widgets):
            set_state(box, "on", i == m.lang)
            set_state(box, "dim", m.zone != "lang")
        for i, (cell, lab, word) in enumerate(self.cells):
            focus = m.zone == "vocab" and i == m.vi
            set_state(cell, "sel", word in self.vocab.selected)
            set_state(cell, "focus", focus)
            span = match_span(word, m.jump) if focus and m.jump and not m.miss else None
            if span:
                a, b = span
                lab.setTextFormat(Qt.TextFormat.RichText)
                lab.setText(html.escape(word[:a]) + "<u>" + html.escape(word[a:b]) + "</u>" + html.escape(word[b:]))
            else:
                lab.setTextFormat(Qt.TextFormat.PlainText)
                lab.setText(word)
        n_sel, n_words = len(self.vocab.selected), len(self.vocab.words)
        self.count_label.setText(f"{n_sel} selected · {n_words} words" if n_words else "")
        if m.jump:
            typed = html.escape(m.jump)
            if m.miss:
                text = f"new word: <b>{typed}</b>_&nbsp;&nbsp; Enter adds it and ticks it"
            elif m.typed_is_new:
                focused = html.escape(m.words[m.vi])
                text = f"jump: <b>{typed}</b>_&nbsp;&nbsp; Space ticks {focused}, Enter adds {typed} as a new word"
            else:
                text = f"jump: <b>{typed}</b>_&nbsp;&nbsp; Space ticks, Enter starts dictating"
            self.jump_label.setText(text)
            # Unmatched letters are a new word to add, not an error: same accent color.
            set_state(self.jump_label, "hit", True)
        else:
            if self.cells:
                text = ("type a few letters to jump to a word, or a new word to add it" if m.zone == "vocab"
                        else "↓ or start typing to choose words")
            else:
                text = ""
            self.jump_label.setText(html.escape(text))
            set_state(self.jump_label, "hit", False)
        self.scroller.setVisible(bool(self.cells))
        self.empty_label.setVisible(not self.cells)
        self.msg_label.setText(m.message)
        self.msg_label.setVisible(bool(m.message))
        self.foot_style.setVisible(m.zone == "style")
        self.foot_lang.setVisible(m.zone == "lang")
        self.foot_vocab.setVisible(m.zone == "vocab")
        self._scroll_to_focus()

    def _scroll_to_focus(self) -> None:
        bar = self.scroller.verticalScrollBar()
        if self.model.zone != "vocab" or not self.cells:
            bar.setValue(0)
        else:
            self.scroller.ensureWidgetVisible(self.cells[self.model.vi][0], 0, 2)
        self._update_rows_label()

    def _update_rows_label(self) -> None:
        if self.cells and self.total_rows > self.visible_rows:
            first = int(round(self.scroller.verticalScrollBar().value() / (CELL_HEIGHT + CELL_SPACING))) + 1
            last = min(self.total_rows, first + self.visible_rows - 1)
            self.rows_label.setText(f"rows {first} to {last} of {self.total_rows}")
        else:
            self.rows_label.setText("")

    # -- keyboard -------------------------------------------------------------

    def _on_key(self, event) -> None:
        key = Qt.Key(event.key())
        if key in MODIFIER_KEYS:
            return
        mods = event.modifiers()
        # AltGr arrives as Ctrl+Alt on Windows: it types characters (@, €), it is not Ctrl.
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier) and not mods & Qt.KeyboardModifier.AltModifier
        text = event.text()
        char = text if text.isprintable() and not ctrl else ""  # Ctrl+Z comes with "\x1a"
        action = self.model.key(KEY_NAMES.get(key, ""), char, time.monotonic(), ctrl=ctrl)
        if self.model.changed:
            self.model.changed = False
            self._build_cells()
        if action == "start":
            self._finish(start=True)
        elif action == "close":
            self.close()
        else:
            self._render()
            self.win.adjustSize()  # shrink or grow to the new grid or message

    def _on_style_click(self, index: int) -> None:
        self.model.style = index
        self.model.zone = "style"
        self._render()

    def _on_lang_click(self, index: int, double: bool) -> None:
        self.model.lang = index
        self.model.zone = "lang"
        if double:
            self._finish(start=True)
        else:
            self._render()

    def _on_cell_click(self, index: int) -> None:
        self.model.zone = "vocab"
        self.model.vi = index
        self.model.key("space", "", time.monotonic())
        self._render()

    def _on_activation(self, active: bool) -> None:
        """A click elsewhere (or Alt+Tab) closes the overlay, like a click outside on Linux."""
        if active:
            self._activated = True
        elif self._activated:
            self.close()

    # -- closing --------------------------------------------------------------------

    def _finish(self, start: bool) -> None:
        if self._closed:
            return
        self._closed = True
        self.vocab.save()
        language, style = self.model.language, self.model.style_key
        self.win.hide()
        # Before dictation starts: the words must land in the user's window, not here.
        restore_foreground(self._previous)
        self.win.deleteLater()
        if start:
            self.on_start(language, style)
        else:
            self.on_close()

    def close(self) -> None:
        self._finish(start=False)
