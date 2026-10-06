"""Shared GTK helpers for the overlay and the HUD."""

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk  # noqa: E402
from Xlib import X, display, protocol  # noqa: E402

ICONS_DIR = Path(__file__).parent / "icons"
_css_loaded = False


def load_css() -> None:
    global _css_loaded
    if _css_loaded:
        return
    provider = Gtk.CssProvider()
    provider.load_from_path(str(Path(__file__).parent / "style.css"))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_loaded = True


def glass_popup() -> Gtk.Window:
    """Borderless override-redirect window with a transparent background.

    Override-redirect means the window manager never gives it focus, so the
    app the user is typing into keeps its caret.
    """
    load_css()
    win = Gtk.Window(type=Gtk.WindowType.POPUP)
    win.set_app_paintable(True)
    visual = win.get_screen().get_rgba_visual()
    if visual is not None:
        win.set_visual(visual)
    win.get_style_context().add_class("tf-window")
    win.set_keep_above(True)
    return win


def bring_to_front(win: Gtk.Window) -> None:
    """Show a window in front, with the keyboard focus.

    GNOME refuses to raise a window that cannot prove a recent user action
    (focus-stealing prevention), and a tray menu or our socket gives no event
    timestamp. The X server's current time is newer than any earlier input,
    so presenting with it counts as "the user just asked for this".
    """
    win.realize()
    gdk_win = win.get_window()
    if gdk_win is None or not isinstance(gdk_win, GdkX11.X11Window):
        win.show_all()
        win.present()
        return
    now = GdkX11.x11_get_server_time(gdk_win)
    # Set before mapping: GNOME reads it when the window appears.
    gdk_win.set_user_time(now)
    win.show_all()
    win.present_with_time(now)
    # GNOME still refuses if another window got a keystroke a moment ago. These
    # windows only open on an explicit request (tray menu, `typefast settings`),
    # so also ask the way docks and pagers do, which GNOME always honors.
    GLib.timeout_add(120, _activate_as_pager, gdk_win)


def _activate_as_pager(gdk_win) -> bool:
    try:
        # A real, fresh timestamp: with 0, Mutter substitutes a possibly stale time
        # and the X server ignores focus changes that look older than the last one.
        now = GdkX11.x11_get_server_time(gdk_win)
        d = display.Display()
        win = d.create_resource_object("window", gdk_win.get_xid())
        msg = protocol.event.ClientMessage(window=win, client_type=d.intern_atom("_NET_ACTIVE_WINDOW"),
                                           data=(32, [2, now, 0, 0, 0]))  # 2 = pager
        d.screen().root.send_event(msg, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        d.flush()
        d.close()
    except Exception:  # noqa: BLE001
        pass
    return False


def pointer_monitor_workarea() -> Gdk.Rectangle:
    display = Gdk.Display.get_default()
    seat = display.get_default_seat()
    _, x, y = seat.get_pointer().get_position()
    monitor = display.get_monitor_at_point(x, y) or display.get_primary_monitor() or display.get_monitor(0)
    return monitor.get_workarea()


def label(text="", css=None, xalign=0.0, **kwargs) -> Gtk.Label:
    lab = Gtk.Label(label=text, xalign=xalign, **kwargs)
    if css:
        for c in css.split():
            lab.get_style_context().add_class(c)
    return lab


def add_class(widget, *classes) -> None:
    ctx = widget.get_style_context()
    for c in classes:
        ctx.add_class(c)


def set_class(widget, name: str, on: bool) -> None:
    ctx = widget.get_style_context()
    if on:
        ctx.add_class(name)
    else:
        ctx.remove_class(name)


def keycaps(items) -> Gtk.Box:
    """Footer of key hints: [(["←", "→"], "language"), ...]."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    for keys, text in items:
        group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for k in keys:
            group.pack_start(label(k, "tf-kbd"), False, False, 0)
        group.pack_start(label(text), False, False, 2)
        box.pack_start(group, False, False, 0)
    return box
