"""Desktop shell: embedded server + native WebView2 window. Closing the window ends the process."""

from __future__ import annotations

import ctypes
import logging
import os
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from .server import create_app

APP_NAME = "ErgoBike"
MUTEX_NAME = "Local\\ErgoBike.SingleInstance"
MB_ICONINFO, MB_TOPMOST = 0x40, 0x40000
ERROR_ALREADY_EXISTS = 183

log = logging.getLogger("ergobike")


def data_dir() -> Path:
    d = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_window() -> int:
    return ctypes.windll.user32.FindWindowW(None, APP_NAME)


def message_box(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(find_window() or None, text, APP_NAME, MB_ICONINFO | MB_TOPMOST)


def acquire_single_instance() -> bool:
    # the handle stays open until the process exits, which is the point
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def focus_existing_window() -> bool:
    hwnd = find_window()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)        # SW_RESTORE
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    return bool(hwnd)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerThread:
    def __init__(self, app, port: int):
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                                    log_config=None, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, name="server", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self, timeout: float = 20.0) -> bool:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return True

    def stop(self) -> None:
        # the lifespan shutdown saves an ongoing ride and closes the audio stream
        self.server.should_exit = True
        self.thread.join(timeout=10)


class DesktopApp:
    def __init__(self, db_path: Path, replay: str | None = None):
        self.app = create_app(db_path, replay)
        self.recorder = self.app.state.recorder
        self.server = ServerThread(self.app, free_port())
        self.window = None
        self._confirmed_close = threading.Event()

    # exposed to JavaScript as window.pywebview.api
    def close_app(self, save: bool = True) -> None:
        self.recorder.finish(save=bool(save))
        self._confirmed_close.set()
        self.window.destroy()

    def _on_closing(self) -> bool:
        # mid-ride: cancel and let the UI ask save / discard / keep riding
        if self.recorder.state != "idle" and not self._confirmed_close.is_set():
            threading.Thread(target=self.window.evaluate_js, args=("ergobikeAskClose()",),
                             daemon=True).start()
            return False
        return True

    def run(self, browser: bool = False) -> int:
        if not self.server.start():
            message_box(f"Não consegui iniciar o servidor interno.\nVeja o log em {data_dir()}")
            return 1
        try:
            if browser:
                webbrowser.open(self.server.url)
                while self.server.thread.is_alive():
                    self.server.thread.join(0.5)
            else:
                self._run_window()
        except KeyboardInterrupt:
            pass
        finally:
            self.server.stop()
        return 0

    def _run_window(self) -> None:
        import webview
        self.window = webview.create_window(
            APP_NAME, self.server.url, width=1280, height=860, min_size=(420, 600),
            background_color="#0d0d0d", js_api=_Api(self))
        self.window.events.closing += self._on_closing
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.start(private_mode=False, storage_path=str(data_dir() / "webview"))


class _Api:
    # pywebview exposes every public attribute, so keep this surface tiny
    def __init__(self, app: DesktopApp):
        self._app = app

    def close_app(self, save: bool = True) -> None:
        self._app.close_app(save)


def main(db: str | None = None, replay: str | None = None, browser: bool = False) -> int:
    ddir = data_dir()
    logging.basicConfig(filename=ddir / "ergobike.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not acquire_single_instance():
        if not focus_existing_window():
            message_box("O ErgoBike já está aberto.")
        return 0
    db_path = Path(db) if db else ddir / "treinos.db"
    log.info("starting, db=%s replay=%s", db_path, replay)
    return DesktopApp(db_path, replay).run(browser=browser)
