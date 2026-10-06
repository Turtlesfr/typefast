"""Microphone capture: 16 kHz mono 16-bit PCM in 50 ms chunks."""

import threading

import numpy as np

# sounddevice is imported lazily: it raises OSError at import time when the
# PortAudio library is missing, and the app checks for that at startup.

SAMPLE_RATE = 16000
CHUNK_MS = 50
BYTES_PER_SECOND = SAMPLE_RATE * 2


def input_devices() -> list[str]:
    try:
        import sounddevice as sd
        return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
    except Exception:
        return []


class Microphone:
    """Pushes each chunk to every subscriber, from PortAudio's thread.

    Subscribers must be quick: they only queue the bytes.
    """

    def __init__(self, device: str | None = None):
        self.device = device or None
        self._subscribers = []
        self._stream = None
        self._lock = threading.Lock()
        self.level = 0.0  # smoothed loudness, 0..1, for the HUD meter

    def subscribe(self, fn) -> None:
        self._subscribers.append(fn)

    def _callback(self, indata, frames, time_info, status):
        chunk = bytes(indata)
        samples = np.frombuffer(chunk, dtype=np.int16)
        if samples.size:
            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) / 32768.0
            # Speech sits around 0.02-0.2 RMS; stretch it to fill the meter.
            self.level = 0.6 * self.level + 0.4 * min(1.0, rms * 6)
        for fn in self._subscribers:
            fn(chunk)

    def start(self) -> None:
        import sounddevice as sd
        with self._lock:
            device = self.device
            if device and device not in input_devices():
                device = None  # the saved mic was unplugged; use the default
            self._stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=SAMPLE_RATE * CHUNK_MS // 1000,
                device=device,
                callback=self._callback,
            )
            self._stream.start()

    def stop(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                finally:
                    self._stream = None
