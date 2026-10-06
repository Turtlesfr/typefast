"""Settings and Vocabulary windows, opened from the tray menu."""

import copy
import threading

import httpx
from gi.repository import Gdk, GLib, Gtk

from typefast import audio, languages, styles
from typefast.ui_common import bring_to_front
from typefast.vocab import Vocabulary, fold

API_KEYS_URL = "https://www.assemblyai.com/dashboard/api-keys"


def _row(grid, r, title, widget, note=None):
    lab = Gtk.Label(label=title, xalign=0.0, yalign=0.0)
    lab.set_margin_top(6)
    grid.attach(lab, 0, r, 1, 1)
    if note:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(widget, False, False, 0)
        n = Gtk.Label(label=note, xalign=0.0, wrap=True, max_width_chars=60)
        n.get_style_context().add_class("dim-label")
        box.pack_start(n, False, False, 0)
        widget = box
    widget.set_hexpand(True)
    grid.attach(widget, 1, r, 1, 1)


def _combo(options, active):
    combo = Gtk.ComboBoxText()
    for key, text in options:
        combo.append(key, text)
    combo.set_active_id(active)
    return combo


class SettingsWindow:
    def __init__(self, cfg: dict, autostart: bool, on_save, first_run: bool = False):
        self.cfg = copy.deepcopy(cfg)
        self.on_save = on_save
        self.win = Gtk.Window(title="TypeFast settings")
        self.win.set_default_size(620, -1)
        self.win.set_position(Gtk.WindowPosition.CENTER)
        self.win.set_icon_name("typefast")
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        outer.set_margin_top(18)
        outer.set_margin_bottom(14)
        outer.set_margin_start(20)
        outer.set_margin_end(20)
        self.win.add(outer)

        if first_run:
            intro = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=70)
            intro.set_markup("<b>Welcome to TypeFast.</b> It needs an AssemblyAI API key to transcribe your voice. "
                             "Creating one is free and comes with free credit.")
            outer.pack_start(intro, False, False, 0)

        grid = Gtk.Grid(column_spacing=16, row_spacing=12)
        outer.pack_start(grid, False, False, 0)
        r = 0

        # API key
        key_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.key = Gtk.Entry(text=self.cfg["api_key"], visibility=False, placeholder_text="Paste your AssemblyAI API key")
        self.key.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic")
        self.key.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Show or hide the key")
        self.key.connect("icon-press", lambda e, *_: e.set_visibility(not e.get_visibility()))
        key_box.pack_start(self.key, True, True, 0)
        test = Gtk.Button(label="Test")
        test.connect("clicked", self._test_key)
        key_box.pack_start(test, False, False, 0)
        key_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        key_col.pack_start(key_box, False, False, 0)
        links = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        links.pack_start(Gtk.LinkButton.new_with_label(API_KEYS_URL, "Get a free API key"), False, False, 0)
        self.key_status = Gtk.Label(xalign=0.0)
        links.pack_start(self.key_status, False, False, 0)
        key_col.pack_start(links, False, False, 0)
        _row(grid, r, "AssemblyAI API key", key_col)
        r += 1

        # Languages, in overlay order
        lang_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.lang_store = Gtk.ListStore(str, str)
        for code in self.cfg["languages"]:
            self.lang_store.append([code, self._lang_label(code)])
        self.lang_view = Gtk.TreeView(model=self.lang_store, headers_visible=False)
        self.lang_view.append_column(Gtk.TreeViewColumn("Language", Gtk.CellRendererText(), text=1))
        self.lang_view.set_size_request(-1, 130)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        scroll.add(self.lang_view)
        lang_col.pack_start(scroll, True, True, 0)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for text, fn in (("Move up", -1), ("Move down", 1)):
            b = Gtk.Button(label=text)
            b.connect("clicked", self._move_lang, fn)
            buttons.pack_start(b, False, False, 0)
        rm = Gtk.Button(label="Remove")
        rm.connect("clicked", self._remove_lang)
        buttons.pack_start(rm, False, False, 0)
        self.add_lang = Gtk.ComboBoxText()
        self.add_lang.connect("changed", self._add_lang)
        buttons.pack_end(self.add_lang, False, False, 0)
        lang_col.pack_start(buttons, False, False, 0)
        self._refresh_add_lang()
        _row(grid, r, "Languages", lang_col, "Shown in the overlay in this order. Up to 6 fit on one row.")
        r += 1

        self.style = _combo([(k, f"{styles.LABELS[k]}: {styles.DESCRIPTIONS[k]}") for k in styles.STYLES],
                            self.cfg["default_style"])
        _row(grid, r, "Default style", self.style, "The overlay opens with it. Press ↑ in the overlay to pick another.")
        r += 1

        instr_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.instruction = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.instruction.get_buffer().set_text(styles.formatted_instruction(self.cfg))
        instr_scroll = Gtk.ScrolledWindow()
        instr_scroll.set_shadow_type(Gtk.ShadowType.IN)
        instr_scroll.set_min_content_height(90)
        instr_scroll.add(self.instruction)
        instr_col.pack_start(instr_scroll, True, True, 0)
        reset = Gtk.Button(label="Reset to the default instruction")
        reset.set_halign(Gtk.Align.START)
        reset.connect("clicked", lambda *_: self.instruction.get_buffer().set_text(styles.DEFAULT_FORMATTED_INSTRUCTION))
        instr_col.pack_start(reset, False, False, 0)
        _row(grid, r, "Formatted style", instr_col, "How the Formatted style rewrites your dictation (max 2048 characters).")
        r += 1

        self.backup = Gtk.CheckButton(label="When AssemblyAI is slow, rewrite the live text with a fast model")
        self.backup.set_active(self.cfg.get("backup", True))
        _row(grid, r, "Backup", self.backup, "Uses the same API key (AssemblyAI LLM Gateway), only on slow days.")
        r += 1

        self.model = _combo([("auto", "Auto: word by word when the language allows it"),
                             (languages.MODEL_U3_PRO, "Universal-3.6 Pro: most accurate, updates about once a second")],
                            self.cfg["live_model"])
        _row(grid, r, "Live text", self.model, "Word by word works for English, French, Spanish, German, Italian and Portuguese.")
        r += 1

        trig = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.trig_key = _combo([("ctrl", "Ctrl"), ("shift", "Shift"), ("alt", "Alt")], self.cfg["trigger"]["key"])
        trig.pack_start(self.trig_key, False, False, 0)
        trig.pack_start(Gtk.Label(label="tapped"), False, False, 0)
        self.trig_taps = Gtk.SpinButton.new_with_range(2, 5, 1)
        self.trig_taps.set_value(self.cfg["trigger"]["taps"])
        trig.pack_start(self.trig_taps, False, False, 0)
        trig.pack_start(Gtk.Label(label="times within"), False, False, 0)
        self.trig_ms = Gtk.SpinButton.new_with_range(400, 3000, 100)
        self.trig_ms.set_value(self.cfg["trigger"]["window_ms"])
        trig.pack_start(self.trig_ms, False, False, 0)
        trig.pack_start(Gtk.Label(label="ms"), False, False, 0)
        _row(grid, r, "Trigger", trig, "Or bind the command `typefast toggle` to a keyboard shortcut in GNOME Settings.")
        r += 1

        self.strip = Gtk.CheckButton(label="Remove ¿ and ¡ from Spanish text")
        self.strip.set_active(self.cfg["strip_inverted_punctuation"])
        _row(grid, r, "Punctuation", self.strip)
        r += 1

        mics = [("", "System default")] + [(name, name) for name in audio.input_devices()]
        self.mic = _combo(mics, self.cfg.get("microphone") or "")
        if self.mic.get_active_id() is None:
            self.mic.set_active_id("")
        _row(grid, r, "Microphone", self.mic)
        r += 1

        self.hud_pos = _combo([("top", "Top of the screen"), ("bottom", "Bottom of the screen")], self.cfg["hud_position"])
        _row(grid, r, "Live bar", self.hud_pos, "Where the bar showing what was heard appears while you dictate.")
        r += 1

        self.region = _combo([("us", "United States"), ("eu", "European Union")], self.cfg["region"])
        _row(grid, r, "Data region", self.region, "Where AssemblyAI processes your audio.")
        r += 1

        self.autostart = Gtk.CheckButton(label="Start TypeFast when I log in")
        self.autostart.set_active(autostart)
        _row(grid, r, "Startup", self.autostart)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.win.destroy())
        save = Gtk.Button(label="Save")
        save.get_style_context().add_class("suggested-action")
        save.connect("clicked", self._save)
        actions.pack_end(save, False, False, 0)
        actions.pack_end(cancel, False, False, 0)
        outer.pack_start(actions, False, False, 0)
        bring_to_front(self.win)
        if first_run:
            self.key.grab_focus()

    @staticmethod
    def _lang_label(code: str) -> str:
        lang = languages.get(code)
        return f"{code.upper()}   {lang.name}" + (f" ({lang.native})" if lang.native != lang.name else "")

    def _codes(self) -> list[str]:
        return [row[0] for row in self.lang_store]

    def _refresh_add_lang(self) -> None:
        self._refreshing = True
        self.add_lang.remove_all()
        self.add_lang.append("", "Add a language…")
        for lang in sorted(languages.LANGUAGES, key=lambda x: x.name):
            if lang.code not in self._codes():
                self.add_lang.append(lang.code, self._lang_label(lang.code))
        self.add_lang.set_active_id("")
        self._refreshing = False

    def _add_lang(self, combo) -> None:
        code = combo.get_active_id()
        if getattr(self, "_refreshing", False) or not code:
            return
        self.lang_store.append([code, self._lang_label(code)])
        GLib.idle_add(self._refresh_add_lang)

    def _selected_lang(self):
        model, it = self.lang_view.get_selection().get_selected()
        return it

    def _move_lang(self, _b, step: int) -> None:
        it = self._selected_lang()
        if it is None:
            return
        other = self.lang_store.iter_next(it) if step > 0 else self.lang_store.iter_previous(it)
        if other is not None:
            self.lang_store.swap(it, other)

    def _remove_lang(self, _b) -> None:
        it = self._selected_lang()
        if it is not None and len(self.lang_store) > 1:
            self.lang_store.remove(it)
            self._refresh_add_lang()

    def _test_key(self, _b) -> None:
        key = self.key.get_text().strip()
        self.key_status.set_text("Checking…")

        def check():
            try:
                r = httpx.get("https://api.assemblyai.com/v2/transcript?limit=1", headers={"Authorization": key}, timeout=10)
                msg = "✓ The key works" if r.status_code == 200 else ("✗ Key rejected" if r.status_code in (401, 403) else f"✗ HTTP {r.status_code}")
            except httpx.HTTPError as e:
                msg = f"✗ {e.__class__.__name__}"
            GLib.idle_add(self.key_status.set_text, msg)

        threading.Thread(target=check, daemon=True).start()

    def _save(self, _b) -> None:
        cfg = self.cfg
        cfg["api_key"] = self.key.get_text().strip()
        cfg["languages"] = self._codes() or ["en"]
        cfg["default_style"] = self.style.get_active_id()
        buf = self.instruction.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        cfg["formatted_instruction"] = "" if text == styles.DEFAULT_FORMATTED_INSTRUCTION else text[:styles.MAX_INSTRUCTION]
        cfg["backup"] = self.backup.get_active()
        cfg["live_model"] = self.model.get_active_id()
        cfg["trigger"] = {"key": self.trig_key.get_active_id(), "taps": int(self.trig_taps.get_value()),
                          "window_ms": int(self.trig_ms.get_value())}
        cfg["strip_inverted_punctuation"] = self.strip.get_active()
        cfg["microphone"] = self.mic.get_active_id() or None
        cfg["region"] = self.region.get_active_id()
        cfg["hud_position"] = self.hud_pos.get_active_id()
        self.on_save(cfg, self.autostart.get_active())
        self.win.destroy()


class VocabularyWindow:
    def __init__(self, vocab: Vocabulary, on_change=None):
        self.vocab = vocab
        self.on_change = on_change
        self.win = Gtk.Window(title="TypeFast vocabulary")
        self.win.set_default_size(520, 620)
        self.win.set_position(Gtk.WindowPosition.CENTER)
        self.win.set_icon_name("typefast")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(16)
        self.win.add(box)

        intro = Gtk.Label(xalign=0.0, wrap=True, max_width_chars=60,
                          label="Names and terms AssemblyAI should spell your way. Tick the ones to use, "
                                "here or in the overlay (up to 100 at a time).")
        box.pack_start(intro, False, False, 0)

        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.input.set_size_request(-1, 60)
        self.input.connect("key-press-event", self._on_input_key)
        frame = Gtk.ScrolledWindow()
        frame.set_shadow_type(Gtk.ShadowType.IN)
        frame.add(self.input)
        box.pack_start(frame, False, False, 0)
        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hint = Gtk.Label(label="Separate words with commas or new lines. Ctrl+Enter adds.", xalign=0.0)
        hint.get_style_context().add_class("dim-label")
        add_row.pack_start(hint, True, True, 0)
        add = Gtk.Button(label="Add")
        add.connect("clicked", lambda *_: self._add())
        add_row.pack_end(add, False, False, 0)
        box.pack_start(add_row, False, False, 0)

        self.search = Gtk.SearchEntry(placeholder_text="Search")
        self.search.connect("search-changed", lambda *_: self.filter.refilter())
        box.pack_start(self.search, False, False, 0)

        self.store = Gtk.ListStore(bool, str)
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(self._visible)
        self.view = Gtk.TreeView(model=self.filter)
        self.view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        toggle = Gtk.CellRendererToggle()
        toggle.connect("toggled", self._on_toggled)
        self.view.append_column(Gtk.TreeViewColumn("Use", toggle, active=0))
        text = Gtk.CellRendererText(editable=True)
        text.connect("edited", self._on_edited)
        col = Gtk.TreeViewColumn("Word", text, text=1)
        col.set_expand(True)
        self.view.append_column(col)
        self.view.connect("key-press-event", self._on_view_key)
        scroll = Gtk.ScrolledWindow()
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        scroll.add(self.view)
        box.pack_start(scroll, True, True, 0)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.count = Gtk.Label(xalign=0.0)
        bottom.pack_start(self.count, True, True, 0)
        clear = Gtk.Button(label="Untick all")
        clear.connect("clicked", self._clear_selection)
        bottom.pack_end(clear, False, False, 0)
        delete = Gtk.Button(label="Delete selected")
        delete.connect("clicked", lambda *_: self._delete())
        bottom.pack_end(delete, False, False, 0)
        box.pack_start(bottom, False, False, 0)

        self._reload()
        bring_to_front(self.win)
        self.input.grab_focus()

    def _visible(self, model, it, _data) -> bool:
        q = fold(self.search.get_text().strip())
        return not q or q in fold(model[it][1])

    def _reload(self) -> None:
        self.store.clear()
        for w in self.vocab.words:
            self.store.append([w in self.vocab.selected, w])
        self.count.set_text(f"{len(self.vocab.words)} words · {len(self.vocab.selected)} ticked")

    def _changed(self) -> None:
        self.vocab.save()
        self._reload()
        if self.on_change:
            self.on_change()

    def _add(self) -> None:
        buf = self.input.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if self.vocab.add(text):
            buf.set_text("")
            self._changed()

    def _on_input_key(self, _w, event) -> bool:
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and event.state & Gdk.ModifierType.CONTROL_MASK:
            self._add()
            return True
        return False

    def _on_toggled(self, _cell, path) -> None:
        word = self.filter[path][1]
        self.vocab.toggle(word)
        self._changed()

    def _on_edited(self, _cell, path, new_text) -> None:
        old = self.filter[path][1]
        if new_text.strip() and new_text != old and self.vocab.rename(old, new_text):
            self._changed()

    def _selected_words(self) -> list[str]:
        model, paths = self.view.get_selection().get_selected_rows()
        return [model[p][1] for p in paths]

    def _delete(self) -> None:
        words = self._selected_words()
        if words:
            self.vocab.remove(words)
            self._changed()

    def _on_view_key(self, _w, event) -> bool:
        if event.keyval == Gdk.KEY_Delete:
            self._delete()
            return True
        return False

    def _clear_selection(self, _b) -> None:
        self.vocab.selected.clear()
        self._changed()
