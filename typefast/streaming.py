"""Live transcription over AssemblyAI's v3 streaming WebSocket."""

import json
import logging
import queue
import threading
import urllib.parse

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from typefast import languages
from typefast.audio import SAMPLE_RATE

log = logging.getLogger(__name__)

HOSTS = {"us": "streaming.assemblyai.com", "eu": "streaming.eu.assemblyai.com"}


def connection_url(region: str, model: str, language: str, keyterms: list[str]) -> str:
    params = {
        "speech_model": model,
        "sample_rate": SAMPLE_RATE,
        "encoding": "pcm_s16le",
    }
    if model == languages.MODEL_MULTILINGUAL:
        # Formatted finals (punctuation, casing) at each end of turn.
        params["format_turns"] = "true"
    else:
        # Universal-3.x Pro: steer toward the chosen language.
        params["language_codes"] = json.dumps([language])
    if keyterms:
        params["keyterms_prompt"] = json.dumps(keyterms[:100])
    return f"wss://{HOSTS.get(region, HOSTS['us'])}/v3/ws?" + urllib.parse.urlencode(params)


class LiveTranscriber:
    """Streams audio and reports turns.

    on_turn(turn_order, transcript, final) and on_error(message) are called
    from the receiving thread. Audio fed before the socket opens is buffered.
    """

    def __init__(self, api_key, region, model, language, keyterms, on_turn, on_error):
        self.url = connection_url(region, model, language, keyterms)
        self.api_key = api_key
        self.model = model
        self.on_turn = on_turn
        self.on_error = on_error
        self._audio: queue.Queue = queue.Queue()
        self._ws = None
        self._done = threading.Event()
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="typefast-stream", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def feed(self, chunk: bytes) -> None:
        if not self._closing:
            self._audio.put(chunk)

    def finish(self, timeout: float = 4.0) -> None:
        """Stop sending audio and wait for the last turn to come back."""
        self._closing = True
        self._audio.put(None)
        self._done.wait(timeout)
        self.abort()

    def abort(self) -> None:
        self._closing = True
        self._audio.put(None)
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _is_final(self, msg: dict) -> bool:
        if not msg.get("end_of_turn"):
            return False
        # With format_turns, an unformatted end-of-turn may precede the formatted one.
        if self.model == languages.MODEL_MULTILINGUAL:
            return bool(msg.get("turn_is_formatted"))
        return True

    def _send_loop(self, ws) -> None:
        try:
            while True:
                chunk = self._audio.get()
                if chunk is None:
                    break
                # Coalesce a backlog (e.g. buffered while connecting) into fewer frames.
                parts = [chunk]
                while len(parts) < 20:
                    try:
                        nxt = self._audio.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is None:
                        self._audio.put(None)
                        break
                    parts.append(nxt)
                ws.send(b"".join(parts))
            ws.send(json.dumps({"type": "Terminate"}))
        except ConnectionClosed:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("stream send failed: %s", e)

    def _run(self) -> None:
        try:
            with connect(self.url, additional_headers={"Authorization": self.api_key},
                         open_timeout=10, close_timeout=2, max_size=2**22) as ws:
                self._ws = ws
                sender = threading.Thread(target=self._send_loop, args=(ws,), daemon=True)
                sender.start()
                for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    msg = json.loads(raw)
                    kind = msg.get("type")
                    if kind == "Turn":
                        self.on_turn(msg.get("turn_order", 0), msg.get("transcript", ""), self._is_final(msg))
                    elif kind == "Begin":
                        model = (msg.get("configuration") or {}).get("model")
                        log.info("stream open, model=%s", model)
                    elif kind == "Termination":
                        break
                    elif kind == "Error":
                        self.on_error(msg.get("error") or "Streaming error")
                        break
        except ConnectionClosed as e:
            if not self._closing:
                reason = e.rcvd.reason if e.rcvd else ""
                self.on_error(f"Live transcription closed: {reason or e}")
        except Exception as e:  # noqa: BLE001
            if not self._closing:
                self.on_error(_friendly(e))
        finally:
            self._done.set()


def _friendly(e: Exception) -> str:
    text = str(e)
    if "401" in text or "403" in text:
        return "AssemblyAI rejected the API key. Check it in Settings."
    if "Name or service not known" in text or "Temporary failure" in text:
        return "No internet connection."
    return f"Live transcription failed: {text}"
