import json
import threading
import time

import httpx
import pytest

from typefast import polish

CHUNK = b"\x00\x01" * 800  # 50 ms of 16 kHz mono s16le


class FakeAPI:
    """Stands in for the Dictation API; answers per request in arrival order."""

    def __init__(self, statuses=None):
        self.statuses = list(statuses or [])
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        n = len(self.requests)
        self.requests.append(body)
        status = self.statuses.pop(0) if self.statuses else 200
        if status != 200:
            return httpx.Response(status, json={"detail": "nope"})
        return httpx.Response(200, json={"text": f"verbatim{n}", "llm_response": f"clean{n}"})


@pytest.fixture
def api(monkeypatch):
    fake = FakeAPI()
    real_client = httpx.Client
    monkeypatch.setattr(polish.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(fake.handler)))
    return fake


def feed_seconds(p, seconds):
    for _ in range(int(seconds * 20)):
        p.feed(CHUNK)


def test_single_segment(api):
    p = polish.Polisher("key", "us", "fr", ["OroCommerce"])
    feed_seconds(p, 3)
    r = p.finish()
    assert r.ok and r.clean == "clean0" and r.verbatim == "verbatim0"
    body = api.requests[0]
    assert b'"language_codes": ["fr"]' in body and b"OroCommerce" in body
    assert body.count(CHUNK) == 60


def test_cut_at_pause_after_min_length(api):
    p = polish.Polisher("key", "us", "es", [], min_cut_s=5)
    feed_seconds(p, 3)
    p.turn_ended()            # too early: no cut
    feed_seconds(p, 3)
    p.turn_ended()            # 6 s: cut
    feed_seconds(p, 2)
    r = p.finish()
    assert len(api.requests) == 2
    assert r.clean == "clean0 clean1"


def test_forced_cut_at_max_length(api):
    p = polish.Polisher("key", "us", "en", [], max_segment_s=4)
    feed_seconds(p, 9)
    p.finish()
    assert len(api.requests) == 3


def test_failed_segment_is_retried_from_memory(api):
    api.statuses = [500]
    p = polish.Polisher("key", "us", "en", [])
    feed_seconds(p, 2)
    r = p.finish()
    assert r.ok and r.clean == "clean1"
    assert api.requests[1].count(CHUNK) == 40  # the whole segment, resent


def test_rejected_key_is_not_retried(api):
    api.statuses = [401]
    p = polish.Polisher("bad", "us", "en", [])
    feed_seconds(p, 1)
    r = p.finish()
    assert not r.ok and "API key" in r.errors[0]
    assert len(api.requests) == 1


def test_null_llm_response_falls_back_to_text(monkeypatch):
    real_client = httpx.Client

    def handler(request):
        request.read()
        return httpx.Response(200, json={"text": "raw words", "llm_response": None, "llm_error": "timeout"})

    monkeypatch.setattr(polish.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    p = polish.Polisher("key", "us", "en", [])
    feed_seconds(p, 1)
    assert p.finish().clean == "raw words"


def test_no_audio_means_empty_result(api):
    r = polish.Polisher("key", "us", "en", []).finish()
    assert r.ok and r.clean == "" and not api.requests


def test_config_part_comes_first(api):
    p = polish.Polisher("key", "eu", "de", ["A"])
    feed_seconds(p, 1)
    p.finish()
    body = api.requests[0].decode("latin-1")
    assert body.index('name="config"') < body.index('name="audio"')
    cfg = json.loads(body.split("Content-Type: application/json\r\n\r\n", 1)[1].split("\r\n", 1)[0])
    assert cfg == {"sample_rate": 16000, "channels": 1, "language_codes": ["de"], "keyterms_prompt": ["A"]}


def slow_api(monkeypatch, delay):
    real_client = httpx.Client

    def handler(request):
        request.read()
        time.sleep(delay)
        return httpx.Response(200, json={"text": "late words", "llm_response": "Late words."})

    monkeypatch.setattr(polish.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))


def test_slow_answer_hits_the_deadline_and_arrives_late(monkeypatch):
    slow_api(monkeypatch, 1.5)
    late = threading.Event()
    got = {}
    p = polish.Polisher("key", "us", "es", [])
    p.on_late = lambda r: (got.setdefault("r", r), late.set())
    feed_seconds(p, 1)
    t0 = time.monotonic()
    r = p.finish(deadline_s=0.4)
    assert time.monotonic() - t0 < 1.0          # never waits past the deadline
    assert r.timed_out and not r.ok and not r.gave_up
    assert late.wait(5) and got["r"].clean == "Late words."


def test_give_up_returns_at_once(monkeypatch):
    slow_api(monkeypatch, 2.0)
    p = polish.Polisher("key", "us", "es", [])
    feed_seconds(p, 1)
    threading.Timer(0.2, p.give_up).start()
    t0 = time.monotonic()
    r = p.finish(deadline_s=10)
    assert time.monotonic() - t0 < 0.8
    assert r.gave_up and not r.timed_out


def test_fast_answer_is_not_marked_late(api):
    p = polish.Polisher("key", "us", "en", [])
    p.on_late = lambda r: pytest.fail("no late delivery expected")
    feed_seconds(p, 1)
    r = p.finish(deadline_s=5)
    assert r.ok and not r.timed_out and r.clean == "clean0"


def test_style_instruction_goes_in_the_config(api):
    p = polish.Polisher("key", "us", "es", [], llm_instruction="Format as an email.")
    feed_seconds(p, 1)
    p.finish()
    assert b'"llm_instruction": "Format as an email."' in api.requests[0]


def test_backup_wins_when_dictation_is_slow(monkeypatch):
    slow_api(monkeypatch, 2.0)
    calls = []
    p = polish.Polisher("key", "us", "es", [])
    feed_seconds(p, 1)
    t0 = time.monotonic()
    r = p.finish(deadline_s=10, hedge=lambda text, timeout: calls.append(text) or "Texto de respaldo.",
                 hedge_after=0.2, live_text=lambda: "texto en vivo")
    assert time.monotonic() - t0 < 1.0
    assert r.ok and r.source == "backup" and r.clean == "Texto de respaldo." and calls == ["texto en vivo"]


def test_backup_unused_when_dictation_is_fast(api):
    p = polish.Polisher("key", "us", "en", [])
    feed_seconds(p, 1)
    r = p.finish(deadline_s=5, hedge=lambda text, timeout: pytest.fail("backup should not run"),
                 hedge_after=2.0, live_text=lambda: "live")
    assert r.source == "dictation" and r.clean == "clean0"


def test_missing_rewrite_is_reformatted_from_the_transcript(monkeypatch):
    real_client = httpx.Client

    def handler(request):
        request.read()
        return httpx.Response(200, json={"text": "uno dos tres", "llm_response": None, "llm_error": "timeout"})

    monkeypatch.setattr(polish.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    got = []
    p = polish.Polisher("key", "us", "es", [], llm_instruction="Format.")
    feed_seconds(p, 1)
    r = p.finish(deadline_s=5, hedge=lambda text, timeout: got.append(text) or "- uno\n- dos\n- tres",
                 hedge_after=3.0, live_text=lambda: "live")
    assert got == ["uno dos tres"] and r.clean == "- uno\n- dos\n- tres" and r.source == "backup"


def test_failing_backup_falls_back_to_the_deadline(monkeypatch):
    slow_api(monkeypatch, 1.5)

    def broken(text, timeout):
        raise RuntimeError("gateway down")

    p = polish.Polisher("key", "us", "es", [])
    feed_seconds(p, 1)
    r = p.finish(deadline_s=0.6, hedge=broken, hedge_after=0.1, live_text=lambda: "live")
    assert r.timed_out and r.source == "dictation"
