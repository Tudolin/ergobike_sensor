"""Local stand-in for the Pulsoid real-time WebSocket.

Sends the same JSON as Pulsoid once per second. Used by the tests and by
`--demo-hr`, so the heart-rate screens can be tried without a watch.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
from typing import Callable

from websockets.sync.server import ServerConnection, serve


def wandering_bpm() -> Callable[[float], int]:
    """Plausible riding heart rate: slow climb, interval-like swings, a bit of noise."""
    rng = random.Random(7)

    def bpm(t: float) -> int:
        base = 95 + 45 * (1 - math.exp(-t / 240))
        swing = 14 * math.sin(t / 45)
        return int(base + swing + rng.gauss(0, 2))
    return bpm


class FakePulsoid:
    def __init__(self, bpm: Callable[[float], int] | None = None, token: str = "demo",
                 interval: float = 1.0):
        self.bpm = bpm or wandering_bpm()
        self.token = token
        self.interval = interval
        self._server = serve(self._handler, "127.0.0.1", 0, process_request=self._auth)
        self.port = self._server.socket.getsockname()[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/api/v1/data/real_time"

    def _auth(self, connection: ServerConnection, request):
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return connection.respond(401, "invalid token\n")
        return None

    def _handler(self, ws: ServerConnection) -> None:
        t0 = time.monotonic()
        try:
            while True:
                t = time.monotonic() - t0
                ws.send(json.dumps({"measured_at": int(time.time() * 1000),
                                    "data": {"heart_rate": self.bpm(t)}}))
                time.sleep(self.interval)
        except Exception:
            pass

    def start(self) -> FakePulsoid:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
