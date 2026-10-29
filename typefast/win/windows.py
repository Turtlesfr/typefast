"""Settings and Vocabulary windows, opened from the tray menu (Qt version of windows.py)."""

import copy
import threading

import httpx
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from typefast import audio, languages, styles
from typefast.vocab import Vocabulary, fold
from typefast.win.qt import bring_to_front, idle_add

API_KEYS_URL = "https://www.assemblyai.com/dashboard/api-keys"


def _note(widget: QWidget, note: str | None) -> QWidget:
    if not note:
        return widget
    box = QWidget()
    col = QVBoxLayout(box)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(4)
    col.addWidget(widget)
    n = QLabel(note)
    n.setWordWrap(True)
    n.setStyleSheet("color: palette(placeholder-text);")
    col.addWidget(n)
    return box


def _combo(options, active) -> QComboBox:
    combo = QComboBox()
    for key, text in options:
        combo.addItem(text, key)
    i = combo.findData(active)
    combo.setCurrentIndex(max(0, i))
    return combo


def _hbox(*widgets) -> QWidget:
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    for w in widgets:
        row.addWidget(QLabel(w) if isinstance(w, str) else w)
    row.addStretch(1)
    return box


class SettingsWindow:
    def __init__(self, cfg: dict, autostart: bool, on_save, first_run: bool = False):
        self.cfg = copy.deepcopy(cfg)
        self.on_save = on_save
        self.win = QWidget()
        self.win.setWindowTitle("TypeFast settings")
        self.win.resize(680, 0)
        outer = QVBoxLayout(self.win)
        outer.setContentsMargins(20, 18, 20, 14)
        outer.setSpacing(14)

        if first_run:
            intro = QLabel("<b>Welcome to TypeFast.</b> It needs an AssemblyAI API key to transcribe your voice. "
                           "Creating one is free and comes with free credit.")
            intro.setWordWrap(True)
            outer.addWidget(intro)

        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        outer.addLayout(form)

        # API key
        self.key = QLineEdit(self.cfg["api_key"])
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Paste your AssemblyAI API key")
        show = QPushButton("Show")
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        test = QPushButton("Test")
        test.clicked.connect(self._test_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(show)
        key_row.addWidget(test)
        link = QLabel(f'<a href="{API_KEYS_URL}">Get a free API key</a>')
        link.setOpenExternalLinks(True)
        self.key_status = QLabel("")
        key_col = QWidget()
        kc = QVBoxLayout(key_col)
        kc.setContentsMargins(0, 0, 0, 0)
        kc.addLayout(key_row)
        kc.addWidget(_hbox(link, self.key_status))
        form.addRow("AssemblyAI API key", key_col)

        # Languages, in overlay order
        self.lang_list = QListWidget()
        self.lang_list.setFixedHeight(130)
        for code in self.cfg["languages"]:
            self._append_lang(code)
        up, down, rm = QPushButton("Move up"), QPushButton("Move down"), QPushButton("Remove")
        up.clicked.connect(lambda: self._move_lang(-1))
        down.clicked.connect(lambda: self._move_lang(1))
        rm.clicked.connect(self._remove_lang)
        self.add_lang = QComboBox()
        self.add_lang.activated.connect(self._add_lang)
        self._refresh_add_lang()
        lang_col = QWidget()
        lc = QVBoxLayout(lang_col)
        lc.setContentsMargins(0, 0, 0, 0)
        lc.addWidget(self.lang_list)
        buttons = QHBoxLayout()
        for b in (up, down, rm):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.add_lang)
        lc.addLayout(buttons)
        form.addRow("Languages", _note(lang_col, "Shown in the overlay in this order. Up to 6 fit on one row."))

        self.style = _combo([(k, f"{styles.LABELS[k]}: {styles.DESCRIPTIONS[k]}") for k in styles.STYLES],
                            self.cfg["default_style"])
        form.addRow("Default style", _note(self.style, "The overlay opens with it. Press ↑ in the overlay to pick another."))

        self.instruction = QPlainTextEdit(styles.formatted_instruction(self.cfg))
        self.instruction.setMinimumHeight(90)
        reset = QPushButton("Reset to the default instruction")
        reset.clicked.connect(lambda: self.instruction.setPlainText(styles.DEFAULT_FORMATTED_INSTRUCTION))
        instr = QWidget()
        ic = QVBoxLayout(instr)
        ic.setContentsMargins(0, 0, 0, 0)
        ic.addWidget(self.instruction)
        ic.addWidget(_hbox(reset))
        form.addRow("Formatted style", _note(instr, "How the Formatted style rewrites your dictation (max 2048 characters)."))

        self.backup = QCheckBox("When AssemblyAI is slow, rewrite the live text with a fast model")
        self.backup.setChecked(self.cfg.get("backup", True))
        form.addRow("Backup", _note(self.backup, "Uses the same API key (AssemblyAI LLM Gateway), only on slow days."))

        self.model = _combo([("auto", "Auto: word by word when the language allows it"),
                             (languages.MODEL_U3_PRO, "Universal-3.6 Pro: most accurate, updates about once a second")],
                            self.cfg["live_model"])
        form.addRow("Live text", _note(self.model, "Word by word works for English, French, Spanish, German, Italian "
                                                   "and Portuguese."))

        self.trig_key = _combo([("ctrl", "Ctrl"), ("shift", "Shift"), ("alt", "Alt")], self.cfg["trigger"]["key"])
        self.trig_taps = QSpinBox()
        self.trig_taps.setRange(2, 5)
        self.trig_taps.setValue(self.cfg["trigger"]["taps"])
        self.trig_ms = QSpinBox()
        self.trig_ms.setRange(400, 3000)
        self.trig_ms.setSingleStep(100)
        self.trig_ms.setValue(self.cfg["trigger"]["window_ms"])
        form.addRow("Trigger", _hbox(self.trig_key, "tapped", self.trig_taps, "times within", self.trig_ms, "ms"))

        self.strip = QCheckBox("Remove ¿ and ¡ from Spanish text")
        self.strip.setChecked(self.cfg["strip_inverted_punctuation"])
        form.addRow("Punctuation", self.strip)

        mics = [("", "System default")] + [(name, name) for name in audio.input_devices()]
        self.mic = _combo(mics, self.cfg.get("microphone") or "")
        form.addRow("Microphone", self.mic)

        self.hud_pos = _combo([("top", "Top of the screen"), ("bottom", "Bottom of the screen")], self.cfg["hud_position"])
        form.addRow("Live bar", _note(self.hud_pos, "Where the bar showing what was heard appears while you dictate."))

        self.region = _combo([("us", "United States"), ("eu", "European Union")], self.cfg["region"])
        form.addRow("Data region", _note(self.region, "Where AssemblyAI processes your audio."))

        self.autostart = QCheckBox("Start TypeFast when I sign in to Windows")
        self.autostart.setChecked(autostart)
        form.addRow("Startup", self.autostart)

        cancel, save = QPushButton("Cancel"), QPushButton("Save")
        save.setDefault(True)
        cancel.clicked.connect(self.win.close)
        save.clicked.connect(self._save)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(cancel)
        actions.addWidget(save)
        outer.addLayout(actions)
        bring_to_front(self.win)
        if first_run:
            self.key.setFocus()

    @staticmethod
    def _lang_label(code: str) -> str:
        lang = languages.get(code)
        return f"{code.upper()}   {lang.name}" + (f" ({lang.native})" if lang.native != lang.name else "")

    def _append_lang(self, code: str) -> None:
        item = QListWidgetItem(self._lang_label(code))
        item.setData(Qt.ItemDataRole.UserRole, code)
        self.lang_list.addItem(item)

    def _codes(self) -> list[str]:
        return [self.lang_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.lang_list.count())]

    def _refresh_add_lang(self) -> None:
        self.add_lang.clear()
        self.add_lang.addItem("Add a language…", "")
        for lang in sorted(languages.LANGUAGES, key=lambda x: x.name):
            if lang.code not in self._codes():
                self.add_lang.addItem(self._lang_label(lang.code), lang.code)

    def _add_lang(self, index: int) -> None:
        code = self.add_lang.itemData(index)
        if code:
            self._append_lang(code)
            self._refresh_add_lang()

    def _move_lang(self, step: int) -> None:
        row = self.lang_list.currentRow()
        other = row + step
        if row < 0 or not 0 <= other < self.lang_list.count():
            return
        self.lang_list.insertItem(other, self.lang_list.takeItem(row))
        self.lang_list.setCurrentRow(other)

    def _remove_lang(self) -> None:
        row = self.lang_list.currentRow()
        if row >= 0 and self.lang_list.count() > 1:
            self.lang_list.takeItem(row)
            self._refresh_add_lang()

    def _test_key(self) -> None:
        key = self.key.text().strip()
        self.key_status.setText("Checking…")

        def check():
            try:
                r = httpx.get("https://api.assemblyai.com/v2/transcript?limit=1", headers={"Authorization": key}, timeout=10)
                msg = "✓ The key works" if r.status_code == 200 else ("✗ Key rejected" if r.status_code in (401, 403) else f"✗ HTTP {r.status_code}")
            except httpx.HTTPError as e:
                msg = f"✗ {e.__class__.__name__}"
            idle_add(self.key_status.setText, msg)

        threading.Thread(target=check, daemon=True).start()

    def _save(self) -> None:
        cfg = self.cfg
        cfg["api_key"] = self.key.text().strip()
        cfg["languages"] = self._codes() or ["en"]
        cfg["default_style"] = self.style.currentData()
        text = self.instruction.toPlainText().strip()
        cfg["formatted_instruction"] = "" if text == styles.DEFAULT_FORMATTED_INSTRUCTION else text[:styles.MAX_INSTRUCTION]
        cfg["backup"] = self.backup.isChecked()
        cfg["live_model"] = self.model.currentData()
        cfg["trigger"] = {"key": self.trig_key.currentData(), "taps": self.trig_taps.value(),
                          "window_ms": self.trig_ms.value()}
        cfg["strip_inverted_punctuation"] = self.strip.isChecked()
        cfg["microphone"] = self.mic.currentData() or None
        cfg["region"] = self.region.currentData()
        cfg["hud_position"] = self.hud_pos.currentData()
        self.on_save(cfg, self.autostart.isChecked())
        self.win.close()


class VocabularyWindow:
    def __init__(self, vocab: Vocabulary, on_change=None):
        self.vocab = vocab
        self.on_change = on_change
        self.win = QWidget()
        self.win.setWindowTitle("TypeFast vocabulary")
        self.win.resize(520, 620)
        box = QVBoxLayout(self.win)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(10)

        intro = QLabel("Names and terms AssemblyAI should spell your way. Tick the ones to use, "
                       "here or in the overlay (up to 100 at a time).")
        intro.setWordWrap(True)
        box.addWidget(intro)

        self.input = QPlainTextEdit()
        self.input.setFixedHeight(60)
        QShortcut(QKeySequence("Ctrl+Return"), self.input, self._add)
        box.addWidget(self.input)
        add = QPushButton("Add")
        add.clicked.connect(self._add)
        hint = QLabel("Separate words with commas or new lines. Ctrl+Enter adds.")
        hint.setStyleSheet("color: palette(placeholder-text);")
        add_row = QHBoxLayout()
        add_row.addWidget(hint, 1)
        add_row.addWidget(add)
        box.addLayout(add_row)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self._apply_filter())
        box.addWidget(self.search)

        self.view = QListWidget()
        self.view.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.view.itemChanged.connect(self._on_item_changed)
        QShortcut(QKeySequence.StandardKey.Delete, self.view, self._delete)
        box.addWidget(self.view, 1)

        bottom = QHBoxLayout()
        self.count = QLabel("")
        bottom.addWidget(self.count, 1)
        delete, clear = QPushButton("Delete selected"), QPushButton("Untick all")
        delete.clicked.connect(self._delete)
        clear.clicked.connect(self._clear_selection)
        bottom.addWidget(delete)
        bottom.addWidget(clear)
        box.addLayout(bottom)

        self._reload()
        bring_to_front(self.win)
        self.input.setFocus()

    def _reload(self) -> None:
        self._loading = True
        self.view.clear()
        for w in self.vocab.words:
            item = QListWidgetItem(w)
            item.setData(Qt.ItemDataRole.UserRole, w)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            item.setCheckState(Qt.CheckState.Checked if w in self.vocab.selected else Qt.CheckState.Unchecked)
            self.view.addItem(item)
        self._loading = False
        self._apply_filter()
        self.count.setText(f"{len(self.vocab.words)} words · {len(self.vocab.selected)} ticked")

    def _apply_filter(self) -> None:
        q = fold(self.search.text().strip())
        for i in range(self.view.count()):
            item = self.view.item(i)
            item.setHidden(bool(q) and q not in fold(item.text()))

    def _changed(self) -> None:
        self.vocab.save()
        idle_add(self._reload)  # not from inside the itemChanged signal of the items it deletes
        if self.on_change:
            self.on_change()

    def _add(self) -> None:
        if self.vocab.add(self.input.toPlainText()):
            self.input.clear()
            self._changed()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        old = item.data(Qt.ItemDataRole.UserRole)
        new = item.text()
        ticked = item.checkState() == Qt.CheckState.Checked
        if new != old:
            if new.strip() and self.vocab.rename(old, new):
                self._changed()
            else:
                idle_add(self._reload)
        elif ticked != (old in self.vocab.selected):
            self.vocab.toggle(old)
            self._changed()

    def _delete(self) -> None:
        words = [item.data(Qt.ItemDataRole.UserRole) for item in self.view.selectedItems()]
        if words:
            self.vocab.remove(words)
            self._changed()

    def _clear_selection(self) -> None:
        self.vocab.selected.clear()
        self._changed()
