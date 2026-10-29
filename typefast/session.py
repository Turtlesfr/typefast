"""One dictation, from the overlay's Enter to the polished text."""

import logging
import threading
import time

from typefast import languages, rewrite, styles, textops
from typefast.audio import Microphone
from typefast.polish import Polisher, PolishResult
from typefast.streaming import LiveTranscriber

log = logging.getLogger(__name__)


class DictationSession:
    """Wires the microphone to the live stream and to the polisher.

    Callbacks run on background threads; the app hops back to GTK itself.
      on_live(text)            live transcript changed
      on_error(message)        live transcription broke (the polish may still work)
      on_polished(result)      PolishResult after stop()
      on_late(result)          the polished text, when it arrived after the deadline
    """

    def __init__(self, cfg: dict, language: str, keyterms: list[str], on_live, on_error, on_polished, on_late=None,
                 style: str = styles.DEFAULT_STYLE):
        self.cfg = cfg
        self.language = language
        self.style = styles.normalize(style)
        self.keyterms = keyterms
        self.on_live = on_live
        self.on_error = on_error
        self.on_polished = on_polished
        self.model = languages.live_model_for(language, cfg["live_model"])
        self.live = textops.LiveTranscript(language)
        self.started_at = 0.0
        self.stopped_at = 0.0
        self.mic = Microphone(cfg.get("microphone"))
        self.stream = LiveTranscriber(cfg["api_key"], cfg["region"], self.model, language, keyterms,
                                      self._on_turn, self._on_stream_error)
        self.polisher = Polisher(cfg["api_key"], cfg["region"], language, keyterms,
                                 llm_instruction=styles.dictation_instruction(self.style, cfg))
        self.polisher.on_late = on_late
        self.history_entry = None  # set by the app, so a late polish can update it
        self.mic.subscribe(self.stream.feed)
        self.mic.subscribe(self.polisher.feed)
        self._lock = threading.Lock()
        self._stopping = False

    @property
    def elapsed(self) -> float:
        end = self.stopped_at or time.monotonic()
        return end - self.started_at if self.started_at else 0.0

    @property
    def level(self) -> float:
        return self.mic.level

    def start(self) -> None:
        self.started_at = time.monotonic()
        self.stream.start()
        self.mic.start()  # raises if no microphone is available

    def _on_turn(self, order: int, transcript: str, final: bool) -> None:
        with self._lock:
            if self._stopping:
                return
            self.live.update(order, transcript, final)
            text = self.live.text
        if final:
            self.polisher.turn_ended()
        self.on_live(text)

    def _on_stream_error(self, message: str) -> None:
        if not self._stopping:
            self.on_error(message)

    def stop(self) -> None:
        """Stop recording; the polished text arrives through on_polished."""
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
        self.stopped_at = time.monotonic()
        self.mic.stop()
        threading.Thread(target=self._finish, name="typefast-finish", daemon=True).start()

    def _finish(self) -> None:
        # Closing the live stream and collecting the polish run side by side.
        closer = threading.Thread(target=self.stream.finish, daemon=True)
        closer.start()
        instruction = styles.backup_instruction(self.style, self.cfg)

        def backup(text, timeout):
            return rewrite.rewrite(self.cfg["api_key"], self.cfg["region"], text, instruction, self.keyterms,
                                   timeout, model=self.cfg.get("backup_model"))
        hedge = backup if instruction else None
        try:
            result = self.polisher.finish(styles.deadline(self.style, self.cfg), hedge=hedge,
                                          hedge_after=styles.hedge_after(self.style, self.cfg),
                                          live_text=lambda: self.live.text)
        except Exception as e:  # noqa: BLE001
            log.exception("polish failed")
            result = PolishResult(ok=False, errors=[str(e)])
        self.on_polished(result)

    def give_up(self) -> None:
        """Stop waiting for the polish: the live text stays."""
        self.polisher.give_up()

    def cancel(self) -> None:
        with self._lock:
            self._stopping = True
        self.mic.stop()
        self.stream.abort()
        self.polisher.cancel()
