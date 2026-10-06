"""The running app: tray, trigger, overlay, dictation sessions."""

import logging
import os
import shutil
import signal
import sys
import time
from pathlib import Path

from gi.repository import GLib, Gtk

from typefast import config, ipc, styles, textops
from typefast.history import History
from typefast.hud import Hud
from typefast.injector import Typer, copy_to_clipboard
from typefast.overlay import Overlay
from typefast.session import DictationSession
from typefast.tray import Tray
from typefast.ui_common import ICONS_DIR, bring_to_front, load_css
from typefast.vocab import Vocabulary
from typefast.windows import SettingsWindow, VocabularyWindow
from typefast.x11 import InputListener

log = logging.getLogger(__name__)

AUTOSTART_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "typefast.desktop"


def launcher_command() -> str:
    local = Path.home() / ".local" / "bin" / "typefast"
    if local.exists():
        return str(local)
    found = shutil.which("typefast")
    return found or f"{sys.executable} -m typefast"


def desktop_entry(autostart: bool) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=TypeFast",
        "Comment=Dictate anywhere: live speech to text with a polished final pass",
        f"Exec={launcher_command()}",
        f"Icon={ICONS_DIR / 'typefast.svg'}",
        "Terminal=false",
        "Categories=Utility;Accessibility;",
        "Keywords=dictation;speech;voice;transcription;",
    ]
    if autostart:
        lines.append("X-GNOME-Autostart-enabled=true")
    return "\n".join(lines) + "\n"


class TypeFastApp:
    def __init__(self):
        self.cfg = config.load()
        self.vocab = Vocabulary()
        self.history = History()
        self.state = "idle"          # idle | overlay | recording | polishing
        self.overlay: Overlay | None = None
        self.session: DictationSession | None = None
        self.settings_win = None
        self.vocab_win = None
        self.first_window = 0
        self.freeze_reason = None     # "key" or "click" once the user touched the field
        self.freeze_time = 0.0
        self.stream_failed = False

    # -- startup --------------------------------------------------------------------

    def run(self) -> None:
        load_css()
        Gtk.IconTheme.get_default().append_search_path(str(ICONS_DIR))
        self.hud = Hud()
        self.hud.position = self.cfg["hud_position"]
        self.hud.trigger_hint = self.trigger_hint
        self.typer = Typer(self.cfg["typing_delay_ms"], self.cfg["paste_threshold"])
        self.typer.on_first_type = self._on_first_type
        self.typer.on_moved = self._on_focus_moved
        self.tray = Tray({
            "start": self.open_overlay, "stop": lambda: self.stop_session(from_tray=True),
            "cancel": self.cancel_session, "keep_live": self.keep_live_text,
            "vocabulary": self.open_vocabulary, "settings": self.open_settings,
            "copy": self._copy_entry, "clear_history": self._clear_history, "quit": self.quit,
        })
        self.tray.set_hint(self.trigger_hint)
        self.tray.set_history(self.history.entries)
        t = self.cfg["trigger"]
        self.listener = InputListener(
            on_gesture=lambda: GLib.idle_add(self.toggle),
            on_trigger_down=lambda: self.typer.hold(True),
            on_trigger_up=self._on_trigger_up,
            on_user_input=self._on_user_input,
            trigger_key=t["key"], taps=t["taps"], window_ms=t["window_ms"],
        )
        self.listener.start()
        self.ipc = ipc.Server(lambda cmd: GLib.idle_add(self._command, cmd))
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self.quit)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.quit)
        if not self.cfg["api_key"]:
            GLib.idle_add(self.open_settings, True)
        log.info("TypeFast running, trigger %s", self.trigger_hint)
        Gtk.main()

    @property
    def trigger_hint(self) -> str:
        t = self.cfg["trigger"]
        return f"{t['key'].capitalize()} ×{t['taps']}"

    def _command(self, cmd: str) -> bool:
        log.info("command: %s (state %s)", cmd, self.state)
        {"toggle": self.toggle, "overlay": self.open_overlay, "settings": self.open_settings,
         "vocabulary": self.open_vocabulary, "quit": self.quit}[cmd]()
        return False

    # -- trigger ----------------------------------------------------------------------

    def toggle(self) -> bool:
        if self.state == "idle":
            self.open_overlay()
        elif self.state == "overlay" and self.overlay:
            self.overlay.close()
        elif self.state == "recording":
            self.stop_session()
        elif self.state == "polishing":
            self.keep_live_text()
        return False

    def keep_live_text(self) -> None:
        """Stop waiting for the polish; the live text already in the field stays."""
        if self.state == "polishing" and self.session:
            self.session.give_up()
            self.hud.notice("Keeping the live text…")

    def _on_trigger_up(self, in_sequence: bool) -> None:
        self.typer.hold(False)
        if in_sequence:
            # The user may be in the middle of the gesture: wait before typing more.
            self.typer.pause_for(self.cfg["trigger"]["window_ms"] / 1000 + 0.1)

    def _on_user_input(self, kind: str = "key") -> None:
        """A real key or click. Once text is in the field, that makes in-place edits unsafe."""
        if self.state not in ("recording", "polishing") or self.typer.frozen or not self.typer.typed:
            return
        self.typer.freeze()
        self.freeze_reason, self.freeze_time = kind, time.monotonic()
        GLib.idle_add(self.hud.notice, "Typing paused because you used the keyboard or mouse. "
                                       "The final text will go to the clipboard.")

    def _on_first_type(self, window_id: int) -> None:
        self.first_window = window_id

    def _on_focus_moved(self) -> None:
        """Typer thread: another window took the focus while we were typing."""
        if self.freeze_reason is None:
            self.freeze_reason, self.freeze_time = "focus", time.monotonic()
        GLib.idle_add(self.hud.notice, "Typing paused because another window took the focus. "
                                       "The final text will go to the clipboard.")

    # -- overlay -----------------------------------------------------------------------

    def open_overlay(self) -> None:
        if self.state != "idle":
            return
        if not self.cfg["api_key"]:
            self.open_settings(True)
            return
        log.info("opening overlay")
        try:
            self.vocab = Vocabulary()  # pick up edits made in the vocabulary window
            overlay = Overlay(self.cfg["languages"], self.cfg["last_language"], self.vocab, self.trigger_hint,
                              on_start=self.start_session, on_close=self._overlay_closed,
                              style=self.cfg["default_style"])
        except Exception as e:  # noqa: BLE001
            log.exception("could not open the overlay")
            self.hud.show_error("Could not open the overlay", str(e))
            return
        self.overlay = overlay
        self.state = "overlay"
        overlay.present()

    def _overlay_closed(self) -> None:
        self.overlay = None
        if self.state == "overlay":
            self.state = "idle"

    # -- dictation ---------------------------------------------------------------------

    def start_session(self, language: str, style: str = styles.DEFAULT_STYLE) -> None:
        self.overlay = None
        self.typer.reset()
        self.first_window = 0
        self.freeze_reason = None
        self.stream_failed = False
        session = None
        try:
            self.cfg["last_language"] = language
            try:
                config.save(self.cfg)
            except OSError as e:
                log.warning("could not save the last language: %s", e)
            keyterms = self.vocab.keyterms()
            holder = {}
            session = DictationSession(
                self.cfg, language, keyterms,
                on_live=lambda text: GLib.idle_add(self._on_live, text, holder["s"]),
                on_error=lambda msg: GLib.idle_add(self._on_stream_error, msg, holder["s"]),
                on_polished=lambda result: GLib.idle_add(self._on_polished, result, holder["s"]),
                on_late=lambda result: GLib.idle_add(self._on_late_polish, result, holder["s"]),
                style=style,
            )
            holder["s"] = session
            session.start()
        except Exception as e:  # noqa: BLE001
            log.exception("could not start recording")
            if session is not None:
                try:
                    session.cancel()
                except Exception:  # noqa: BLE001
                    pass
            self.session = None
            self.state = "idle"
            self.tray.set_state("idle")
            self.hud.show_error("Could not start recording", str(e))
            return
        self.session = session
        self.state = "recording"
        self.tray.set_state("recording")
        self.hud.show_listening(self.session, language, self.trigger_hint, len(keyterms),
                                style=None if style == styles.DEFAULT_STYLE else styles.LABELS[style])

    def _on_live(self, text: str, session) -> bool:
        if self.state == "recording" and session is self.session:
            self.hud.set_live_text(text)
            self.typer.set_target(text)
        return False

    def _on_stream_error(self, message: str, session) -> bool:
        if self.state != "recording" or session is not self.session:
            return False
        if "API key" in message:
            self.cancel_session()
            self.hud.show_error("AssemblyAI rejected the API key", "Open Settings from the tray icon to fix it.")
            self.open_settings()
            return False
        self.stream_failed = True
        self.hud.notice(f"{message} Keep talking: the final text will still be produced.")
        return False

    def stop_session(self, from_tray: bool = False) -> None:
        if self.state != "recording" or not self.session:
            return
        if from_tray and self.freeze_reason == "click" and time.monotonic() - self.freeze_time < 10:
            # The "click" was the user opening the tray menu, not moving the caret.
            self.typer.unfreeze()
            self.freeze_reason = None
        self.state = "polishing"
        self.tray.set_state("polishing")
        self.session.stop()
        self.hud.show_polishing()

    def cancel_session(self) -> None:
        if self.session:
            self.session.cancel()
        self.session = None
        self.typer.cancel_final()
        self.state = "idle"
        self.tray.set_state("idle")
        self.hud.hide()

    def _on_polished(self, result, session) -> bool:
        if self.state != "polishing" or session is None or session is not self.session:
            return False
        live = session.live.text
        if result.timed_out or result.gave_up:
            # Slow service, or the user tapped the trigger: the live text stays as typed.
            self._remember(session, live, result)
            self._finish_session()
            if result.gave_up:
                self.hud.show_done("Kept the live text", "If the polished version arrives, it goes to Recent dictations.")
            else:
                self.hud.show_error("AssemblyAI is slow right now: the live text stays",
                                    "If the polished version arrives, it goes to Recent dictations (tray menu).", seconds=6)
            return False
        chosen = styles.pick_text(session.style, result)
        text = textops.finalize(chosen, self.cfg["strip_inverted_punctuation"])
        if not result.ok and text and live:
            # Some segments failed: the polished text is missing part of the speech.
            self._remember(session, live, result)
            self._finish_session()
            self.hud.show_error("Polish incomplete, the live text stays", "; ".join(result.errors))
            return False
        if not text:
            if result.ok and not live:
                self._finish_session()
                self.hud.show_done("Nothing to transcribe", "No speech was detected.")
                return False
            self._remember(session, live, result)
            self._finish_session()
            reason = "; ".join(result.errors) or "empty result"
            self.hud.show_error("Polish failed, the live text stays", reason)
            return False
        if self.freeze_reason is not None or self.typer.frozen:
            copy_to_clipboard(text)
            self._remember(session, text, result)
            self._finish_session()
            self.hud.show_done("Copied to the clipboard",
                               "You used the keyboard or mouse while dictating, so the field was left alone. Paste with Ctrl+V.",
                               seconds=6)
            return False

        def replaced(ok: bool, _reason: str) -> None:
            GLib.idle_add(self._after_replace, ok, text, session, result)

        self.typer.replace(text, self.first_window, replaced)
        return False

    def _after_replace(self, ok: bool, text: str, session, result) -> bool:
        self._remember(session, text, result)
        if session is not self.session:
            return False  # cancelled, or a new dictation already started
        self._finish_session()
        words = len(text.split())
        if ok:
            label = "Replaced" if session.style == styles.DEFAULT_STYLE else styles.LABELS[session.style]
            notes = []
            if result.source == "backup":
                notes.append("AssemblyAI was slow: the live text was rewritten by the backup model.")
            if self.typer.pasted:
                notes.append("Also in your clipboard.")
            self.hud.show_done(f"{label} · {words} words", " ".join(notes))
        else:
            copy_to_clipboard(text)
            self.hud.show_done("Copied to the clipboard", "Another window has the focus now. Paste with Ctrl+V.", seconds=6)
        return False

    def _remember(self, session, inserted: str, result) -> None:
        session.history_entry = self.history.add(session.language, inserted, result.clean, result.verbatim,
                                                 session.live.text)
        session.history_entry["style"] = session.style
        self.history.save()
        self.tray.set_history(self.history.entries)

    def _on_late_polish(self, result, session) -> bool:
        """The polished text arrived after the deadline: keep it in the history only."""
        entry = session.history_entry
        if entry is None:
            return False
        chosen = styles.pick_text(session.style, result)
        entry["clean"], entry["verbatim"] = result.clean, result.verbatim
        entry["polished_late"] = textops.finalize(chosen, self.cfg["strip_inverted_punctuation"])
        self.history.save()
        self.tray.set_history(self.history.entries)
        if self.state == "idle":
            self.hud.show_done("Polished version ready", "Copy it from Recent dictations in the tray menu.", seconds=5)
        return False

    def _finish_session(self) -> None:
        self.session = None
        self.state = "idle"
        self.tray.set_state("idle")

    # -- tray actions -------------------------------------------------------------------

    def _copy_entry(self, entry: dict) -> None:
        copy_to_clipboard(entry.get("polished_late") or entry.get("inserted") or entry.get("clean") or "")

    def _clear_history(self) -> None:
        self.history.clear()
        self.tray.set_history([])

    def open_settings(self, first_run: bool = False) -> bool:
        if self.settings_win and self.settings_win.win.get_visible():
            bring_to_front(self.settings_win.win)
            return False
        self.settings_win = SettingsWindow(self.cfg, AUTOSTART_FILE.exists(), self._save_settings, first_run=first_run)
        return False

    def _save_settings(self, cfg: dict, autostart: bool) -> None:
        self.cfg = config.normalize(cfg)
        config.save(self.cfg)
        t = self.cfg["trigger"]
        self.listener.configure(t["key"], t["taps"], t["window_ms"])
        self.typer.delay_ms = self.cfg["typing_delay_ms"]
        self.typer.paste_threshold = self.cfg["paste_threshold"]
        self.tray.set_hint(self.trigger_hint)
        self.hud.position = self.cfg["hud_position"]
        self.hud.trigger_hint = self.trigger_hint
        set_autostart(autostart)

    def open_vocabulary(self) -> None:
        if self.vocab_win and self.vocab_win.win.get_visible():
            bring_to_front(self.vocab_win.win)
            return
        self.vocab = Vocabulary()
        self.vocab_win = VocabularyWindow(self.vocab)

    def quit(self, *_args) -> bool:
        if self.session:
            self.session.cancel()
        self.listener.stop()
        self.ipc.close()
        self.typer.close()  # gives back the spare keycodes used for missing characters
        Gtk.main_quit()
        return False


def set_autostart(enabled: bool) -> None:
    if enabled:
        AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART_FILE.write_text(desktop_entry(autostart=True))
    elif AUTOSTART_FILE.exists():
        AUTOSTART_FILE.unlink()
