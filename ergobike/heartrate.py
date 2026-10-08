"""Heart rate from Pulsoid (Wear OS watch -> phone app -> Pulsoid cloud -> here).

Pulsoid streams `{"measured_at": ms, "data": {"heart_rate": bpm}}` over a
WebSocket. A personal token comes from https://pulsoid.net/ui/keys and needs the
`data:heart_rate:read` scope.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

PULSOID_WS = "wss://dev.pulsoid.net/api/v1/data/real_time"
PULSOID_VALIDATE = "https://dev.pulsoid.net/api/v1/token/validate"
SCOPE = "data:heart_rate:read"
STALE_S = 10.0

log = logging.getLogger(__name__)


def validate_token(token: str, url: str = PULSOID_VALIDATE, timeout: float = 10.0) -> dict:
    """Ask Pulsoid whether the token works. Returns {"ok", "message", "scopes"}."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                               "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "scopes": [],
                "message": "Token inválido ou expirado" if e.code in (401, 403) else f"Erro HTTP {e.code}"}
    except OSError as e:
        return {"ok": False, "scopes": [], "message": f"Sem conexão com o Pulsoid ({e})"}
    scopes = data.get("scopes", [])
    if SCOPE not in scopes:
        return {"ok": False, "scopes": scopes, "message": f"O token não tem o escopo {SCOPE}"}
    return {"ok": True, "scopes": scopes, "message": "Token válido"}


class HeartRateMonitor:
    """Keeps the Pulsoid WebSocket open in a thread and exposes the latest bpm."""

    def __init__(self, token: str, url: str = PULSOID_WS):
        self.token = token
        self.url = url
        self.status = "connecting"          # connecting | connected | waiting | unauthorized | error
        self.error: str | None = None
        self._bpm: int | None = None
        self._received_at = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="pulsoid", daemon=True)

    @property
    def bpm(self) -> int | None:
        """Latest reading, or None if nothing arrived for STALE_S seconds."""
        if self._bpm is None or time.monotonic() - self._received_at > STALE_S:
            return None
        return self._bpm

    def start(self) -> HeartRateMonitor:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def _handle(self, raw) -> None:
        try:
            msg = json.loads(raw)
            bpm = int(msg["data"]["heart_rate"]) if isinstance(msg, dict) else int(msg)
        except (ValueError, KeyError, TypeError):
            return
        if 25 <= bpm <= 250:
            self._bpm = bpm
            self._received_at = time.monotonic()
            self.status = "connected"

    def _run(self) -> None:
        from websockets.exceptions import InvalidStatus
        from websockets.sync.client import connect

        backoff = 1.0
        while not self._stop.is_set():
            try:
                with connect(self.url, additional_headers={"Authorization": f"Bearer {self.token}"},
                             open_timeout=10, close_timeout=2) as ws:
                    self.status, self.error, backoff = "waiting", None, 1.0
                    while not self._stop.is_set():
                        try:
                            self._handle(ws.recv(timeout=1.0))
                        except TimeoutError:
                            if self.status == "connected" and self.bpm is None:
                                self.status = "waiting"     # socket up, watch not sending
            except InvalidStatus as e:
                code = e.response.status_code
                if code in (401, 403):
                    self.status, self.error = "unauthorized", "Token recusado pelo Pulsoid"
                    log.warning("pulsoid rejected the token (%s)", code)
                    self._stop.wait(60)
                    continue
                self.status, self.error = "error", f"HTTP {code}"
            except Exception as e:
                self.status, self.error = "error", str(e) or type(e).__name__
            if not self._stop.is_set():
                log.info("pulsoid: %s, retrying in %.0fs", self.error, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)
