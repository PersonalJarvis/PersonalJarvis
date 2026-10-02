"""Name the JavaScript that hung the desktop window, before it is recovered.

The blank-window watchdog (``jarvis/ui/window_watchdog.py``) heals a hung page
by ending its renderer and reloading. That brings the window back, but it
destroys the only evidence of WHY it hung: a renderer spinning one core in a
script cannot answer ``evaluate_js``, cannot report its own long task, and the
desktop app has no dev tools. Live 2026-10-02 the window hung three times in
one afternoon and every trace was lost with the renderer.

What still reaches a page whose main thread never yields is the DevTools
protocol's ``Debugger.pause``: Chromium delivers it on the IO thread and V8
interrupts the running script, wherever it is. So the probe keeps one
DevTools session on the main page with the debugger enabled (that part needs
the main thread, so it happens while the page is healthy), and on a hang
pauses the page a few times, logs the call frames with a slice of the source
around each, and lets it go again. The renderer is ended right after anyway.

Windows/WebView2 only: the session goes through Chromium's remote-debugging
endpoint, opened on a random local port (``--remote-debugging-port=0``) whose
number Chromium writes into the profile directory. Elsewhere — WKWebView,
WebKitGTK, Qt — nothing is opened and :meth:`HangStackProbe.capture` returns
an empty list. The endpoint listens on loopback only and is reachable only by
someone who can read this user's profile directory, which already holds the
app's own session state.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

from loguru import logger

from jarvis.platform.webview_accessibility import WEBVIEW2_ARGS_ENV

#: Asks WebView2 for a DevTools endpoint on a free loopback port.
REMOTE_DEBUGGING_FLAG = "--remote-debugging-port=0"

#: How many times the hung page is paused; several samples show the loop.
SAMPLES = 3

#: Frames logged per sample — the loop sits at the top, its caller below.
MAX_FRAMES = 12

#: Characters of source shown on each side of a frame's position.
SNIPPET_RADIUS = 90


def enable_webview_remote_debugging(
    *, env: MutableMapping[str, str] | None = None, platform: str | None = None
) -> bool:
    """Open the DevTools endpoint the probe needs, before the WebView starts.

    Appended to the shared WebView2 argument variable (never assigned), like
    the accessibility flag. Returns whether the flag is in place.
    """
    if (platform or sys.platform) != "win32":
        return False
    target = os.environ if env is None else env
    existing = target.get(WEBVIEW2_ARGS_ENV, "")
    if "--remote-debugging-port" in existing:
        return True
    target[WEBVIEW2_ARGS_ENV] = f"{existing} {REMOTE_DEBUGGING_FLAG}".strip()
    return True


def _active_port(profile_dir: Path) -> int | None:
    """The port Chromium chose, from ``DevToolsActivePort`` in the profile."""
    for candidate in (
        profile_dir / "DevToolsActivePort",
        profile_dir / "EBWebView" / "DevToolsActivePort",
    ):
        try:
            first = candidate.read_text(encoding="utf-8").splitlines()[0].strip()
            return int(first)
        except (OSError, IndexError, ValueError):
            continue
    return None


class _Session:
    """One DevTools websocket to a page, with a reader thread."""

    def __init__(self, ws_url: str) -> None:
        from websockets.sync.client import connect

        self._ws = connect(ws_url, open_timeout=5, max_size=None, origin=None)
        self._next_id = 0
        self._send_lock = threading.Lock()
        self._cv = threading.Condition()
        self._replies: dict[int, dict[str, Any]] = {}
        self._paused: list[dict[str, Any]] = []
        self._scripts: dict[str, str] = {}
        self.alive = True
        threading.Thread(target=self._read, name="webview-hang-probe", daemon=True).start()

    def _read(self) -> None:
        try:
            for raw in self._ws:
                msg = json.loads(raw)
                with self._cv:
                    if "id" in msg:
                        self._replies[msg["id"]] = msg
                    elif msg.get("method") == "Debugger.scriptParsed":
                        params = msg.get("params", {})
                        self._scripts[params.get("scriptId", "")] = params.get("url", "")
                    elif msg.get("method") == "Debugger.paused":
                        self._paused.append(msg.get("params", {}))
                    self._cv.notify_all()
        except Exception as exc:  # noqa: BLE001 — a closed page ends the session
            logger.debug("webview hang probe: session closed ({})", exc)
        finally:
            with self._cv:
                self.alive = False
                self._cv.notify_all()

    def send(self, method: str, params: dict[str, Any] | None = None) -> int:
        with self._send_lock:
            self._next_id += 1
            mid = self._next_id
            self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        return mid

    def call(
        self, method: str, params: dict[str, Any] | None = None, *, wait: float = 5.0
    ) -> dict[str, Any] | None:
        mid = self.send(method, params)
        deadline = time.monotonic() + wait
        with self._cv:
            while mid not in self._replies and self.alive:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cv.wait(left)
            return self._replies.pop(mid, None)

    def wait_paused(self, wait: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + wait
        with self._cv:
            while not self._paused and self.alive:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cv.wait(left)
            return self._paused.pop(0) if self._paused else None

    def script_url(self, script_id: str) -> str:
        with self._cv:
            return self._scripts.get(script_id, "")

    def close(self) -> None:
        self.alive = False
        try:
            self._ws.close()
        except Exception as exc:  # noqa: BLE001 — closing a dead socket is best effort
            logger.debug("webview hang probe: close failed ({})", exc)


class HangStackProbe:
    """Keeps the debugger armed on the main page; names the hung code on demand."""

    def __init__(self, profile_dir: Path | None, page_url: str) -> None:
        self._profile_dir = profile_dir
        self._page_prefix = page_url.split("?", 1)[0].rstrip("/")
        self._session: _Session | None = None
        self._lock = threading.Lock()
        self._warned = False

    def arm(self) -> None:
        """Attach and enable the debugger while the page is healthy. Never raises."""
        with self._lock:
            if self._session is not None and self._session.alive:
                return
            try:
                self._session = self._attach()
            except Exception as exc:  # noqa: BLE001 — diagnostics must never cost the window
                self._session = None
                if not self._warned:
                    self._warned = True
                    logger.info("webview hang probe unavailable: {}", exc)

    def _attach(self) -> _Session | None:
        if self._profile_dir is None:
            return None
        port = _active_port(self._profile_dir)
        if port is None:
            return None
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as resp:
            targets = json.load(resp)
        page = next(
            (
                t
                for t in targets
                if t.get("type") == "page"
                and str(t.get("url", "")).split("?", 1)[0].rstrip("/") == self._page_prefix
            ),
            None,
        )
        if page is None:
            return None
        session = _Session(page["webSocketDebuggerUrl"])
        if session.call("Debugger.enable", wait=10) is None:
            session.close()
            return None
        return session

    def capture(self) -> list[str]:
        """Pause the hung page a few times and describe where it was. Never raises."""
        with self._lock:
            session = self._session
        if session is None or not session.alive:
            return []
        lines: list[str] = []
        try:
            for sample in range(SAMPLES):
                session.send("Debugger.pause")
                paused = session.wait_paused(5.0)
                if paused is None:
                    lines.append(f"sample {sample + 1}: the page did not pause")
                    break
                frames = paused.get("callFrames", [])[:MAX_FRAMES]
                lines.append(f"sample {sample + 1}: {len(frames)} frame(s)")
                for index, frame in enumerate(frames):
                    lines.append(
                        "  " + self._describe(session, index, frame, with_source=sample == 0)
                    )
                session.call("Debugger.resume", wait=5)
                time.sleep(0.3)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"capture stopped: {exc}")
        return lines

    @staticmethod
    def _describe(
        session: _Session, index: int, frame: dict[str, Any], *, with_source: bool
    ) -> str:
        loc = frame.get("location", {})
        script_id = loc.get("scriptId", "")
        line = int(loc.get("lineNumber", 0))
        col = int(loc.get("columnNumber", 0))
        url = session.script_url(script_id) or frame.get("url", "")
        name = frame.get("functionName") or "<anonymous>"
        text = f"#{index} {name} {url.rsplit('/', 1)[-1]}:{line + 1}:{col + 1}"
        if not with_source or index > 3:
            return text
        reply = session.call("Debugger.getScriptSource", {"scriptId": script_id}, wait=5)
        source = ((reply or {}).get("result") or {}).get("scriptSource", "")
        rows = source.split("\n")
        if line < len(rows):
            row = rows[line]
            snippet = row[max(0, col - SNIPPET_RADIUS) : col + SNIPPET_RADIUS]
            text += f"\n      {snippet!r}"
        return text

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.close()
            self._session = None


__all__ = ["HangStackProbe", "REMOTE_DEBUGGING_FLAG", "enable_webview_remote_debugging"]
