from __future__ import annotations

import queue
import threading
import time
import wave
from typing import Callable, Iterator

import numpy as np

from .pulses import EdgeDetector

DetectorFactory = Callable[[int], EdgeDetector]


def input_devices() -> list[tuple[int, str]]:
    import sounddevice as sd
    apis = sd.query_hostapis()
    return [(i, f"{d['name']} [{apis[d['hostapi']]['name']}]")
            for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]


def default_device() -> int | None:
    """Onboard Realtek mic (MME first). That is where the bike sensor usually goes."""
    try:
        devs = input_devices()
    except Exception:
        return None
    mics = [(i, n) for i, n in devs if "Realtek" in n and "Micro" in n]
    return next((i for i, n in mics if "[MME]" in n), mics[0][0] if mics else None)


def read_wav(path: str, channel: int = 0) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        rate, nch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError("only 16-bit PCM WAV is supported")
    x = np.frombuffer(raw, dtype="<i2").reshape(-1, nch)[:, min(channel, nch - 1)]
    return x.astype(np.float32) / 32768.0, rate


def wav_blocks(path: str, channel: int = 0, block: int = 1024) -> tuple[Iterator[np.ndarray], int]:
    x, rate = read_wav(path, channel)
    return (x[i:i + block] for i in range(0, len(x), block)), rate


class WavWriter:
    """Writes int16 mono WAV from a background thread (never block the audio callback)."""

    def __init__(self, path: str, rate: int):
        self._w = wave.open(path, "wb")
        self._w.setnchannels(1)
        self._w.setsampwidth(2)
        self._w.setframerate(rate)
        self._q: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def write(self, x: np.ndarray) -> None:
        self._q.put((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())

    def _run(self) -> None:
        while (b := self._q.get()) is not None:
            self._w.writeframes(b)
        self._w.close()

    def close(self) -> None:
        self._q.put(None)
        self._t.join()


class PulseSource:
    """Mic input stream that pushes pulse times (seconds of stream time) to `events`."""

    def __init__(self, device: int | None, rate: int, detector: EdgeDetector, channel: int = 0,
                 record_path: str | None = None, blocksize: int = 1024):
        self.device = device
        self.rate = rate
        self.channel = channel
        self.detector = detector
        self.blocksize = blocksize
        self.events: queue.SimpleQueue[float] = queue.SimpleQueue()
        self.frames = 0
        self.overflows = 0
        self._max_step = 0.0
        self._rec = WavWriter(record_path, rate) if record_path else None
        self._stream = None
        self._capture: list[np.ndarray] | None = None
        self._capture_left = 0
        self._capture_done = threading.Event()

    @property
    def stream_time(self) -> float:
        return self.frames / self.rate

    def _on_block(self, x: np.ndarray, overflow: bool = False) -> None:
        if overflow:
            self.overflows += 1
        for idx in self.detector.process(x):
            self.events.put(idx / self.rate)
        self._max_step = max(self._max_step, self.detector.last_peak)
        if self._rec:
            self._rec.write(x)
        if self._capture is not None and self._capture_left > 0:
            self._capture.append(x.copy())
            self._capture_left -= len(x)
            if self._capture_left <= 0:
                self._capture_done.set()
        self.frames += len(x)

    def _callback(self, indata, frames, time_info, status) -> None:
        self._on_block(indata[:, self.channel], status.input_overflow)

    def take_max_step(self) -> float:
        """Largest step seen since the last call (for the level meter)."""
        v, self._max_step = self._max_step, 0.0
        return v

    def capture(self, seconds: float) -> np.ndarray:
        """Block until `seconds` of raw signal have been collected."""
        self._capture, self._capture_left = [], int(seconds * self.rate)
        self._capture_done.clear()
        self._capture_done.wait(seconds + 5)
        blocks, self._capture = self._capture, None
        return np.concatenate(blocks) if blocks else np.zeros(0, np.float32)

    def start(self) -> PulseSource:
        import sounddevice as sd
        self._stream = sd.InputStream(device=self.device, samplerate=self.rate,
                                      channels=self.channel + 1, dtype="float32",
                                      blocksize=self.blocksize, callback=self._callback)
        self._stream.start()
        return self

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._rec:
            self._rec.close()
            self._rec = None

    def __enter__(self) -> PulseSource:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


class ReplaySource(PulseSource):
    """Plays a WAV file in a loop, in real time, as if it came from the mic.
    Handy for working on the app without the bike."""

    def __init__(self, wav_path: str, make_detector: DetectorFactory, blocksize: int = 1024):
        x, rate = read_wav(wav_path)
        super().__init__(None, rate, make_detector(rate), blocksize=blocksize)
        self._x = x
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        n, pos, t0 = self.blocksize, 0, time.monotonic()
        while self._running.is_set():
            block = np.take(self._x, np.arange(pos, pos + n), mode="wrap")
            pos = (pos + n) % len(self._x)
            self._on_block(block)
            delay = t0 + self.frames / self.rate - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def start(self) -> ReplaySource:
        self._running.set()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._running.clear()
        if self._thread:
            self._thread.join()
            self._thread = None
