"""Final transcript, produced while the user is still talking.

Audio is uploaded to AssemblyAI's Dictation API as it is recorded, in segments
of at most ~110 s (the API takes 120 s per call). A segment is cut at the first
pause after 45 s, so only the last stretch is still processing when the user
stops. Each segment returns a verbatim transcript and a cleaned one (fillers
removed, self-corrections resolved).

The user is waiting, so the result has a firm deadline (10 s by default) and
can be abandoned at any time (give_up). A segment that fails fast is retried
once from the audio kept in memory, if time remains. Whatever is still in
flight at the deadline keeps running in the background and is delivered
through on_late, so the polished text can still land in the history.
"""

import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx

from typefast import textops
from typefast.audio import BYTES_PER_SECOND, SAMPLE_RATE

log = logging.getLogger(__name__)

DICTATION_HOSTS = {"us": "dictation.assemblyai.com", "eu": "dictation.eu.assemblyai.com"}
LATE_WAIT_S = 120.0     # how long a late result is still awaited in the background


@dataclass
class PolishResult:
    clean: str = ""
    verbatim: str = ""
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    timed_out: bool = False   # the deadline passed with requests still in flight
    gave_up: bool = False     # the user chose to keep the live text
    source: str = "dictation"  # or "backup": the LLM rewrite of the live text won the race
    rewrite_missing: bool = False  # a segment came back without its LLM rewrite


class _Segment:
    def __init__(self, index: int):
        self.index = index
        self.audio = bytearray()
        self.queue: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.result: dict | None = None
        self.error: str | None = None
        self.closed = False
        self.body_end: float | None = None   # when the last audio byte went out

    @property
    def seconds(self) -> float:
        return len(self.audio) / BYTES_PER_SECOND

    def write(self, chunk: bytes) -> None:
        self.audio.extend(chunk)
        self.queue.put(chunk)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.queue.put(None)


class Polisher:
    def __init__(self, api_key: str, region: str, language: str, keyterms: list[str],
                 min_cut_s: float = 45.0, max_segment_s: float = 110.0, llm_instruction: str | None = None):
        self.api_key = api_key
        self.llm_instruction = llm_instruction
        self.region = region
        self.language = language
        self.keyterms = keyterms[:100]
        self.min_cut_s = min_cut_s
        self.max_segment_s = max_segment_s
        self._segments: list[_Segment] = []
        self._current: _Segment | None = None
        self._lock = threading.Lock()
        self._finished = False
        self._give_up = threading.Event()
        self.on_late = None       # called with a PolishResult that completed after the deadline
        self._client = httpx.Client(timeout=httpx.Timeout(90.0, connect=10.0))
        self._url = f"https://{DICTATION_HOSTS.get(region, DICTATION_HOSTS['us'])}/v1/transcribe/live"

    # -- feeding (audio and stream threads) ---------------------------------

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            if self._finished:
                return
            if self._current is None:
                self._current = self._open_segment()
            self._current.write(chunk)
            if self._current.seconds >= self.max_segment_s:
                self._close_current()

    def turn_ended(self) -> None:
        """A pause in speech: a good place to cut a long segment."""
        with self._lock:
            if self._current is not None and self._current.seconds >= self.min_cut_s:
                self._close_current()

    def _open_segment(self) -> _Segment:
        seg = _Segment(len(self._segments))
        self._segments.append(seg)
        seg.thread = threading.Thread(target=self._upload, args=(seg,), name=f"typefast-polish-{seg.index}", daemon=True)
        seg.thread.start()
        return seg

    def _close_current(self) -> None:
        if self._current is not None:
            self._current.close()
            self._current = None

    # -- requests -------------------------------------------------------------

    def _config(self) -> dict:
        cfg = {"sample_rate": SAMPLE_RATE, "channels": 1, "language_codes": [self.language]}
        if self.keyterms:
            cfg["keyterms_prompt"] = self.keyterms
        if self.llm_instruction:
            cfg["llm_instruction"] = self.llm_instruction
        return cfg

    def _body(self, boundary: str, frames):
        yield (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="config"\r\n'
            "Content-Type: application/json\r\n\r\n"
            f"{json.dumps(self._config())}\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="audio"; filename="audio.pcm"\r\n'
            "Content-Type: audio/pcm\r\n\r\n"
        ).encode()
        for frame in frames:
            yield frame
        yield f"\r\n--{boundary}--\r\n".encode()

    def _post(self, frames, timeout: float | None = None) -> dict:
        boundary = "typefast-" + uuid.uuid4().hex
        kwargs = {"timeout": httpx.Timeout(timeout, connect=min(5.0, timeout))} if timeout else {}
        r = self._client.post(
            self._url,
            headers={"Authorization": self.api_key, "Content-Type": f"multipart/form-data; boundary={boundary}"},
            content=self._body(boundary, frames),
            **kwargs,
        )
        if r.status_code != 200:
            raise RuntimeError(_api_error(r))
        return r.json()

    def _upload(self, seg: _Segment) -> None:
        def frames():
            while True:
                chunk = seg.queue.get()
                if chunk is None:
                    seg.body_end = time.monotonic()
                    return
                yield chunk
        try:
            seg.result = self._post(frames())
            after = time.monotonic() - seg.body_end if seg.body_end else -1
            log.info("polish segment %d: %.1fs of audio, answer %.2fs after the last byte (server %.2fs)%s",
                     seg.index, seg.seconds, after, (seg.result.get("request_time_ms") or 0) / 1000,
                     f", rewrite error: {seg.result.get('llm_error')}" if seg.result.get("llm_error") else "")
        except Exception as e:  # noqa: BLE001
            seg.error = str(e)
            log.warning("polish segment %d failed: %s", seg.index, e)
            # Drain so the producer side never blocks; the audio is kept in seg.audio.
            seg.close()

    def _retry(self, seg: _Segment, timeout: float) -> None:
        try:
            seg.result = self._post([bytes(seg.audio)], timeout=timeout)
            seg.error = None
            log.info("polish segment %d: retry succeeded", seg.index)
        except Exception as e:  # noqa: BLE001
            seg.error = str(e)
            log.warning("polish segment %d: retry failed: %s", seg.index, e)

    # -- results ----------------------------------------------------------------

    def give_up(self) -> None:
        """Stop waiting now (the user keeps the live text). Late results still arrive."""
        self._give_up.set()

    @staticmethod
    def _assemble(segments) -> PolishResult:
        result = PolishResult()
        clean, verbatim = [], []
        for seg in segments:
            if seg.seconds < 0.3:
                continue  # too short to hold speech; the API may reject it
            if seg.result is None:
                result.ok = False
                result.errors.append(seg.error or "no answer yet")
                continue
            text = seg.result.get("text") or ""
            verbatim.append(text)
            if not seg.result.get("llm_response") and text:
                result.rewrite_missing = True
            clean.append(seg.result.get("llm_response") or text)
        result.clean = textops.join_segments(clean)
        result.verbatim = textops.join_segments(verbatim)
        return result

    def finish(self, deadline_s: float = 10.0, hedge=None, hedge_after: float | None = None,
               live_text=None) -> PolishResult:
        """Close the upload and collect the result, waiting at most deadline_s.

        hedge(text, timeout) -> str is the backup rewrite. It starts on the live
        text (live_text()) once hedge_after seconds pass without the Dictation
        answer, and wins if it finishes first. It also reformats the transcript
        when the Dictation API returns it without its rewrite.
        """
        started = time.monotonic()
        with self._lock:
            self._finished = True
            self._close_current()
            segments = list(self._segments)
        deadline = started + deadline_s
        backup: dict = {}

        def in_flight():
            return [seg for seg in segments if seg.thread is not None and seg.thread.is_alive()]

        def start_backup(text: str) -> threading.Thread | None:
            if not hedge or not text.strip():
                return None

            def run():
                try:
                    backup["text"] = hedge(text, max(1.0, deadline - time.monotonic()))
                    log.info("polish: backup rewrite ready after %.2fs", time.monotonic() - started)
                except Exception as e:  # noqa: BLE001
                    backup["error"] = str(e)
                    log.warning("polish: backup rewrite failed: %s", e)
            log.info("polish: Dictation API is late, starting the backup rewrite")
            t = threading.Thread(target=run, name="typefast-backup", daemon=True)
            t.start()
            return t

        backup_thread = None
        while in_flight() and time.monotonic() < deadline and not self._give_up.is_set():
            if (backup_thread is None and hedge_after is not None and live_text is not None
                    and time.monotonic() - started >= hedge_after):
                backup_thread = start_backup(live_text()) or threading.Thread()
            if backup.get("text"):
                break
            time.sleep(0.05)
        # A segment that failed fast gets one retry, if there is time for it.
        if not backup.get("text"):
            for seg in segments:
                remaining = deadline - time.monotonic()
                fatal = seg.error is not None and "API key" in seg.error
                if (seg.result is None and seg.error and not fatal and seg.seconds >= 0.3
                        and remaining > 1.0 and not self._give_up.is_set()):
                    self._retry(seg, remaining)
        result = self._assemble(segments)
        pending = in_flight()
        result.gave_up = self._give_up.is_set()

        if not result.gave_up and hedge and (not result.ok or result.rewrite_missing):
            # Missing or unrewritten parts: the backup can still save the day before the deadline.
            if result.ok and result.rewrite_missing and not backup.get("text"):
                backup_thread = start_backup(result.verbatim)
            elif backup_thread is None and live_text is not None:
                backup_thread = start_backup(live_text())
            while (backup_thread is not None and backup_thread.is_alive() and time.monotonic() < deadline
                   and not self._give_up.is_set()):
                time.sleep(0.05)
            result.gave_up = self._give_up.is_set()

        if backup.get("text") and not result.gave_up and (not result.ok or result.rewrite_missing or pending):
            won = PolishResult(clean=backup["text"], verbatim=result.verbatim or backup["text"], source="backup")
            log.info("polish: backup rewrite used after %.2fs", time.monotonic() - started)
            threading.Thread(target=self._drain, args=(segments,), daemon=True).start()
            return won

        result.timed_out = bool(pending) and not result.gave_up
        log.info("polish: %s after %.2fs", "kept the live text" if pending else ("done" if result.ok else "failed"),
                 time.monotonic() - started)
        if pending:
            threading.Thread(target=self._await_late, args=(segments,), name="typefast-polish-late", daemon=True).start()
        else:
            self._client.close()
        return result

    def _drain(self, segments) -> None:
        """The backup won: let in-flight requests end, then close the connection."""
        for seg in segments:
            if seg.thread is not None:
                seg.thread.join(LATE_WAIT_S)
        self._client.close()

    def _await_late(self, segments) -> None:
        end = time.monotonic() + LATE_WAIT_S
        for seg in segments:
            if seg.thread is not None:
                seg.thread.join(max(0.0, end - time.monotonic()))
        result = self._assemble(segments)
        self._client.close()
        if result.ok and result.clean and self.on_late:
            log.info("polish: late result delivered")
            self.on_late(result)

    def cancel(self) -> None:
        with self._lock:
            self._finished = True
            self._close_current()
        threading.Thread(target=self._client.close, daemon=True).start()


def _api_error(r: httpx.Response) -> str:
    try:
        j = r.json()
        detail = j.get("detail") or j.get("error") or j.get("title") or r.text
    except ValueError:
        detail = r.text
    if r.status_code == 401:
        return "AssemblyAI rejected the API key"
    return f"HTTP {r.status_code}: {str(detail)[:200]}"
