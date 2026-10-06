"""End-to-end check of streaming + polish against the real API, with a PCM file as the microphone.

Usage: python scripts/e2e_pipeline.py audio.pcm LANG [min_cut_seconds]
The file must be 16 kHz mono s16le. Reads the API key from ~/.config/typefast/config.json.
"""

import sys
import threading
import time

from typefast import config
from typefast.session import DictationSession


class FileMic:
    def __init__(self, pcm: bytes):
        self.pcm, self.subs, self.level, self._stop = pcm, [], 0.3, threading.Event()

    def subscribe(self, fn):
        self.subs.append(fn)

    def start(self):
        def run():
            for i in range(0, len(self.pcm), 1600):
                if self._stop.is_set():
                    return
                for fn in self.subs:
                    fn(self.pcm[i:i + 1600])
                time.sleep(0.05)
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self._stop.set()


def main():
    pcm = open(sys.argv[1], "rb").read()
    lang = sys.argv[2]
    cfg = config.load()
    done = threading.Event()
    out = {}
    lives = []
    import os
    if os.environ.get("POLISH_DEADLINE"):
        cfg["polish_timeout_s"] = float(os.environ["POLISH_DEADLINE"])
    late = threading.Event()
    from typefast import styles
    style = os.environ.get("STYLE", "clean")
    if os.environ.get("HEDGE_AFTER"):
        styles.HEDGE_AFTER[style] = float(os.environ["HEDGE_AFTER"])
    s = DictationSession(cfg, lang, ["OroCommerce"], on_live=lives.append, on_error=lambda m: print("ERROR", m),
                         on_polished=lambda r: (out.setdefault("r", r), done.set()),
                         on_late=lambda r: (out.setdefault("late", r), late.set()), style=style)
    if len(sys.argv) > 3:
        s.polisher.min_cut_s = float(sys.argv[3])
    s.mic = FileMic(pcm)
    for fn in (s.stream.feed, s.polisher.feed):
        s.mic.subscribe(fn)
    t0 = time.time()
    s.start()
    time.sleep(len(pcm) / 32000 + 1.0)
    print(f"live updates: {len(lives)}; last live: {lives[-1] if lives else ''!r}")
    print("segments:", [round(seg.seconds, 1) for seg in s.polisher._segments])
    t_stop = time.time()
    s.stop()
    done.wait(60)
    r = out.get("r")
    print(f"polish wait after stop: {time.time() - t_stop:.2f}s (total {time.time() - t0:.1f}s)")
    print("ok:", r.ok, "errors:", r.errors, "timed out:", r.timed_out, "source:", r.source)
    print("clean:", r.clean)
    print("verbatim == clean:", r.verbatim == r.clean)
    if r.timed_out or r.gave_up:
        if late.wait(130):
            print(f"late result after {time.time() - t_stop:.1f}s: {out['late'].clean[:80]!r}")
        else:
            print("no late result")


if __name__ == "__main__":
    main()
