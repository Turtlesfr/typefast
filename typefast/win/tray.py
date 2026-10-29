"""Tray icon and its menu (Qt version of tray.py, same methods)."""

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from typefast.win.qt import ICONS_DIR

ICONS = {"idle": "typefast-tray", "recording": "typefast-tray-rec", "polishing": "typefast-tray-busy",
         "replacing": "typefast-tray-busy"}


class Tray:
    def __init__(self, actions: dict):
        """actions: start, stop, cancel, vocabulary, settings, copy(entry), clear_history, quit."""
        self.actions = actions
        self._state = "idle"
        self._hint = ""
        self._history: list[dict] = []
        self.icon = QSystemTrayIcon(self._icon("idle"))
        self.icon.setToolTip("TypeFast")
        self.menu = QMenu()
        self.icon.setContextMenu(self.menu)
        self._build()
        self.icon.show()

    @staticmethod
    def _icon(state: str) -> QIcon:
        return QIcon(str(ICONS_DIR / f"{ICONS.get(state, ICONS['idle'])}.svg"))

    @staticmethod
    def _item(menu: QMenu, text: str, callback, enabled: bool = True) -> QAction:
        action = menu.addAction(text)
        action.triggered.connect(lambda *_: callback())
        action.setEnabled(enabled)
        return action

    def _build(self) -> None:
        menu = self.menu
        menu.clear()
        a = self.actions
        if self._state == "recording":
            self._item(menu, "Finish and polish", a["stop"])
            self._item(menu, "Cancel dictation", a["cancel"])
        elif self._state == "polishing":
            self._item(menu, "Polishing…", lambda: None, enabled=False)
            self._item(menu, "Keep the live text", a["keep_live"])
        elif self._state == "replacing":
            self._item(menu, "Replacing the live text…", lambda: None, enabled=False)
        else:
            self._item(menu, f"Start dictation\t{self._hint}".rstrip(), a["start"])
        menu.addSeparator()
        self._item(menu, "Vocabulary…", a["vocabulary"])

        sub = menu.addMenu("Recent dictations")
        if self._history:
            for entry in self._history[:10]:
                text = entry.get("polished_late") or entry.get("inserted") or ""
                short = " ".join(text.split())
                short = short[:48] + "…" if len(short) > 48 else short
                item = self._item(sub, f"{entry.get('time', '')[-5:]}  {entry.get('language', '').upper()}  {short}",
                                  lambda e=entry: a["copy"](e))
                item.setToolTip("Copy to the clipboard")
            sub.addSeparator()
            self._item(sub, "Clear history", a["clear_history"])
        else:
            self._item(sub, "Nothing yet", lambda: None, enabled=False)

        self._item(menu, "Settings…", a["settings"])
        menu.addSeparator()
        self._item(menu, "Quit TypeFast", a["quit"])

    def set_state(self, state: str) -> None:
        if state != self._state:
            self.icon.setIcon(self._icon(state))
            self.icon.setToolTip(f"TypeFast: {state}")
        self._state = state
        self._build()

    def set_hint(self, hint: str) -> None:
        self._hint = hint
        self._build()

    def set_history(self, entries: list[dict]) -> None:
        self._history = entries
        self._build()
