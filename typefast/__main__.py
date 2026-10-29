"""Command line: `typefast` runs the app, `typefast toggle` controls it."""

import argparse
import logging
import logging.handlers
import os
import shutil
import sys

from typefast import config, ipc

USAGE = """commands:
  (none)       start TypeFast (it lives in the tray)
  toggle       open the overlay, or finish the current dictation
  overlay      open the overlay
  settings     open the settings window
  vocabulary   open the vocabulary window
  quit         quit the running TypeFast"""


def setup_logging(debug: bool) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(config.DATA_DIR / "typefast.log", maxBytes=1_000_000, backupCount=2)]
    if debug or (sys.stderr and sys.stderr.isatty()):  # pythonw has no stderr
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    # Exceptions raised in GTK callbacks only reach sys.excepthook; send them to the log.
    def log_uncaught(kind, value, tb):
        logging.getLogger("typefast").error("uncaught exception", exc_info=(kind, value, tb))
    sys.excepthook = log_uncaught


def fatal_dialog(title: str, message: str) -> None:
    print(f"{title}\n{message}", file=sys.stderr)
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE, text=title)
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()
    except Exception:  # noqa: BLE001
        pass


def check_environment() -> bool:
    if sys.platform == "win32":
        return True  # everything TypeFast needs ships with Windows or the Python packages
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or (os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY")):
        fatal_dialog("TypeFast needs an X11 session",
                     "Wayland does not let apps listen to the keyboard globally or type into other windows.\n\n"
                     "Log out, click the gear icon on the login screen, choose \"Ubuntu on Xorg\", and log in again.")
        return False
    if not os.environ.get("DISPLAY"):
        print("TypeFast needs a graphical X11 session (DISPLAY is not set).", file=sys.stderr)
        return False
    missing = [tool for tool in ("xclip",) if not shutil.which(tool)]
    import ctypes.util
    if not ctypes.util.find_library("portaudio"):
        missing.append("libportaudio2")
    if missing:
        fatal_dialog("TypeFast is missing system tools",
                     f"Install them with:\n\nsudo apt install {' '.join(missing)}")
        return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="typefast", description="Dictate anywhere on Ubuntu.",
                                     epilog=USAGE, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=ipc.COMMANDS, help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help="verbose logging to the terminal")
    args = parser.parse_args(argv)

    if args.command:
        if ipc.send(args.command):
            return 0
        if args.command == "quit":
            return 0
        if args.command not in ("toggle", "overlay"):
            print("TypeFast is not running. Start it with: typefast", file=sys.stderr)
            return 1
        # toggle/overlay with nothing running: start the app instead.

    if ipc.send("settings" if not args.command else args.command):
        print("TypeFast is already running.")
        return 0
    if not check_environment():
        return 1
    setup_logging(args.debug)

    from typefast.app import TypeFastApp

    app = TypeFastApp()
    app.overlay_on_start = args.command in ("toggle", "overlay")
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
