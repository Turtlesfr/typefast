"""Qt plumbing for Windows: the event loop, the shared look, and focus helpers.

Counterpart of ui_common.py. Widgets get their look from an object name (the
GTK CSS class without "tf-") and boolean properties for states (on, dim, sel,
focus), which the style sheet below matches.
"""

import ctypes
import signal
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QWidget

from typefast.win import user32

ICONS_DIR = Path(__file__).parent.parent / "icons"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]

MONO = "Consolas"
STYLE_SHEET = f"""
QWidget {{ font-family: "Segoe UI"; }}
#card {{ background-color: rgba(27, 31, 40, 250); border: 1px solid rgba(255, 255, 255, 26);
        border-radius: 14px; }}
#card QLabel {{ color: #eef1f6; background: transparent; }}
QLabel#brand {{ font-weight: bold; font-size: 15px; }}
QLabel#hint, QLabel#rows, QLabel#footText, QLabel#jump, QLabel#styleDesc, QLabel#langName, QLabel#empty, QLabel#status, QLabel#hudHint {{ color: #9aa4b5; font-size: 12px; }}
QLabel#section {{ color: #9aa4b5; font-size: 11px; letter-spacing: 1.2px; }}
QLabel#count {{ color: #f2b544; font-size: 13px; }}
QLabel#jump {{ font-family: "{MONO}"; font-size: 13px; }}
QLabel#jump[hit="true"] {{ color: #f2b544; }}
#lang {{ border: 1px solid rgba(255, 255, 255, 26); border-radius: 10px; background-color: rgba(255, 255, 255, 8); }}
QLabel#langCode {{ font-family: "{MONO}"; font-weight: bold; font-size: 24px; }}
#lang[on="true"] {{ background-color: #f2b544; border-color: #f2b544; }}
#lang[on="true"] QLabel {{ color: #2a1d05; }}
#lang[on="true"][dim="true"] {{ background-color: rgba(242, 181, 68, 41); border-color: rgba(242, 181, 68, 128); }}
#lang[on="true"][dim="true"] #langCode {{ color: #eef1f6; }}
#lang[on="true"][dim="true"] #langName {{ color: #9aa4b5; }}
#style {{ border: 1px solid rgba(255, 255, 255, 31); border-radius: 11px; padding: 3px 12px;
         background-color: rgba(255, 255, 255, 8); }}
#style[on="true"] {{ background-color: #f2b544; border-color: #f2b544; }}
#style QLabel {{ color: #cfd5df; font-size: 12px; }}
#style[on="true"] QLabel {{ color: #2a1d05; }}
#style[on="true"][dim="true"] {{ background-color: rgba(242, 181, 68, 41); border-color: rgba(242, 181, 68, 128); }}
#style[on="true"][dim="true"] QLabel {{ color: #ffe2a6; }}
#cell {{ border: 1px solid rgba(255, 255, 255, 26); border-radius: 7px; background-color: rgba(255, 255, 255, 9); }}
#cell QLabel {{ color: #cfd5df; font-size: 13px; }}
#check {{ border-radius: 2px; border: 1px solid rgba(255, 255, 255, 71); }}
#cell[sel="true"] {{ background-color: rgba(242, 181, 68, 43); border-color: rgba(242, 181, 68, 140); }}
#cell[sel="true"] QLabel {{ color: #ffe2a6; }}
#cell[sel="true"] #check {{ background-color: #f2b544; border-color: #f2b544; }}
#cell[focus="true"] {{ border: 2px solid #f2b544; }}
#cell[focus="true"] QLabel {{ color: #ffffff; }}
#cell[sel="true"][focus="true"] {{ background-color: #f2b544; }}
#cell[sel="true"][focus="true"] QLabel {{ color: #2a1d05; }}
#cell[sel="true"][focus="true"] #check {{ background-color: #2a1d05; border-color: #2a1d05; }}
QLabel#msg {{ color: #ffd27a; font-size: 12px; }}
#foot {{ border-top: 1px solid rgba(255, 255, 255, 26); }}
QLabel#kbd {{ font-family: "{MONO}"; font-size: 11px; border: 1px solid rgba(255, 255, 255, 51);
       border-bottom-width: 2px; border-radius: 4px; padding: 0 5px; background-color: rgba(255, 255, 255, 13); }}
QLabel#chip {{ font-family: "{MONO}"; font-weight: bold; font-size: 12px; background-color: rgba(255, 255, 255, 26);
        border-radius: 5px; padding: 1px 6px; }}
QLabel#status {{ font-size: 13px; }}
#card[error="true"] #status {{ color: #ff9a92; }}
QLabel#timer {{ font-family: "{MONO}"; font-size: 13px; }}
QLabel#live {{ font-size: 14px; }}
QLabel#hudHint {{ font-size: 11px; }}
#progress {{ border: none; border-radius: 2px; background-color: rgba(255, 255, 255, 26); max-height: 4px; }}
#progress::chunk {{ border-radius: 2px; background-color: #f2b544; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
"""


class _Dispatcher(QObject):
    call = Signal(object)


_app: QApplication | None = None
_dispatcher: _Dispatcher | None = None


def init() -> None:
    """Create the Qt application (before any widget)."""
    global _app, _dispatcher
    _app = QApplication.instance() or QApplication([])
    _app.setQuitOnLastWindowClosed(False)  # the app lives in the tray
    _app.setStyleSheet(STYLE_SHEET)
    _app.setWindowIcon(QIcon(str(ICONS_DIR / "typefast.svg")))
    _dispatcher = _Dispatcher()
    _dispatcher.call.connect(lambda fn: fn(), Qt.ConnectionType.QueuedConnection)


def idle_add(fn, *args) -> None:
    """Run fn(*args) on the UI thread; safe to call from any thread (like GLib.idle_add)."""
    _dispatcher.call.emit(lambda: fn(*args))


def timeout_add(ms: int, fn) -> None:
    """Run fn() once on the UI thread after ms milliseconds (UI thread only)."""
    QTimer.singleShot(ms, fn)


def run_main(on_quit) -> None:
    signal.signal(signal.SIGINT, lambda *_: idle_add(on_quit))
    # Python only handles signals between bytecodes: wake it up now and then.
    ticker = QTimer()
    ticker.timeout.connect(lambda: None)
    ticker.start(250)
    _app.exec()


def quit_main() -> None:
    _app.quit()


def is_visible(window) -> bool:
    return window.isVisible()


# -- focus ---------------------------------------------------------------------------


def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def force_foreground(hwnd: int) -> None:
    """Give a window the foreground and the keyboard.

    Windows only lets the foreground process hand the foreground over. Attaching
    to the foreground thread's input makes us part of it for a moment, which is
    the usual way for a hotkey or tray app to bring up its own window.
    """
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    me = kernel32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)


def restore_foreground(hwnd: int) -> None:
    """Hand the foreground back to the window that had it before ours."""
    if hwnd and user32.IsWindow(hwnd):
        user32.SetForegroundWindow(hwnd)


def bring_to_front(window: QWidget) -> None:
    window.show()
    window.raise_()
    force_foreground(int(window.winId()))
    window.activateWindow()


def pointer_workarea():
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    return screen.availableGeometry()


# -- widgets -------------------------------------------------------------------------


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    lab.setTextFormat(Qt.TextFormat.PlainText)
    return lab


def set_state(widget: QWidget, name: str, on: bool) -> None:
    """Set a state property (on, dim, sel, focus…) and re-apply the style sheet."""
    if widget.property(name) == on:
        return
    widget.setProperty(name, on)
    for w in [widget, *widget.findChildren(QWidget)]:
        w.style().unpolish(w)
        w.style().polish(w)


def card_window(extra_flags=Qt.WindowType(0), cls=QWidget) -> tuple[QWidget, QWidget]:
    """A borderless, always-on-top, translucent window holding a rounded dark card with a shadow."""
    win = cls(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
              | Qt.WindowType.Tool | extra_flags)
    win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    outer = QHBoxLayout(win)
    outer.setContentsMargins(24, 24, 24, 24)  # room for the shadow
    card = QWidget()
    card.setObjectName("card")
    card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    shadow = QGraphicsDropShadowEffect(card)
    shadow.setBlurRadius(48)
    shadow.setOffset(0, 18)
    shadow.setColor(QColor(0, 0, 0, 140))
    card.setGraphicsEffect(shadow)
    outer.addWidget(card)
    return win, card


def keycaps(items) -> QWidget:
    """Footer of key hints: [(["←", "→"], "language"), ...]."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(16)
    for keys, text in items:
        group = QHBoxLayout()
        group.setSpacing(4)
        for k in keys:
            group.addWidget(label(k, "kbd"))
        group.addWidget(label(text, "footText"))
        row.addLayout(group)
    row.addStretch(1)
    return box
