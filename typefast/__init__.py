"""TypeFast: dictate anywhere on Ubuntu."""

__version__ = "0.1.0"

# Pin GTK 3 before any module imports gi.repository (GTK 4 may also be installed).
try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Pango", "1.0")
    gi.require_version("GdkX11", "3.0")
except (ImportError, ValueError):
    pass
