"""Tray icon and its menu."""

import gi

gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import Gtk  # noqa: E402

from typefast.ui_common import ICONS_DIR  # noqa: E402

ICONS = {"idle": "typefast-tray", "recording": "typefast-tray-rec", "polishing": "typefast-tray-busy"}


class Tray:
    def __init__(self, actions: dict):
        """actions: start, stop, cancel, vocabulary, settings, copy(entry), clear_history, quit."""
        self.actions = actions
        self.ind = AppIndicator.Indicator.new("typefast", self._icon("idle"), AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.ind.set_title("TypeFast")
        self._state = "idle"
        self._hint = ""
        self._history: list[dict] = []
        self._build()

    @staticmethod
    def _icon(state: str) -> str:
        # Absolute paths: the Ubuntu AppIndicator extension fails to look our names up in a theme path.
        return str(ICONS_DIR / f"{ICONS.get(state, ICONS['idle'])}.svg")

    def _item(self, menu, text, callback, sensitive=True):
        item = Gtk.MenuItem(label=text)
        item.connect("activate", lambda *_: callback())
        item.set_sensitive(sensitive)
        menu.append(item)
        return item

    def _build(self) -> None:
        menu = Gtk.Menu()
        a = self.actions
        if self._state == "recording":
            self._item(menu, "Finish and polish", a["stop"])
            self._item(menu, "Cancel dictation", a["cancel"])
        elif self._state == "polishing":
            self._item(menu, "Polishing…", lambda: None, sensitive=False)
            self._item(menu, "Keep the live text", a["keep_live"])
        else:
            self._item(menu, f"Start dictation   {self._hint}".rstrip(), a["start"])
        menu.append(Gtk.SeparatorMenuItem())
        self._item(menu, "Vocabulary…", a["vocabulary"])

        recent = Gtk.MenuItem(label="Recent dictations")
        sub = Gtk.Menu()
        if self._history:
            for entry in self._history[:10]:
                text = entry.get("polished_late") or entry.get("inserted") or ""
                short = " ".join(text.split())
                short = short[:48] + "…" if len(short) > 48 else short
                item = Gtk.MenuItem(label=f"{entry.get('time', '')[-5:]}  {entry.get('language', '').upper()}  {short}")
                item.set_tooltip_text("Copy to the clipboard")
                item.connect("activate", lambda _i, e=entry: a["copy"](e))
                sub.append(item)
            sub.append(Gtk.SeparatorMenuItem())
            clear = Gtk.MenuItem(label="Clear history")
            clear.connect("activate", lambda *_: a["clear_history"]())
            sub.append(clear)
        else:
            empty = Gtk.MenuItem(label="Nothing yet")
            empty.set_sensitive(False)
            sub.append(empty)
        recent.set_submenu(sub)
        menu.append(recent)

        self._item(menu, "Settings…", a["settings"])
        menu.append(Gtk.SeparatorMenuItem())
        self._item(menu, "Quit TypeFast", a["quit"])
        menu.show_all()
        self.ind.set_menu(menu)
        self.menu = menu

    def set_state(self, state: str) -> None:
        if state != self._state:
            self.ind.set_icon_full(self._icon(state), f"TypeFast: {state}")
        self._state = state
        self._build()

    def set_hint(self, hint: str) -> None:
        self._hint = hint
        self._build()

    def set_history(self, entries: list[dict]) -> None:
        self._history = entries
        self._build()
