"""Background music player — parent side of the companion host.

Talks to ``jarvis.platform.music_player_host`` (a hidden pywebview window in
its own process with a persistent browser profile) over stdin/stdout JSON
lines: load a URL, read the player state, pause / play / next / previous /
volume, show or hide the window. This is what lets "play Radiohead" happen
without a browser tab appearing or the focus moving — and what gives volume
control, which no OS media session offers.

Capability, honestly: the player needs a display and the ``pywebview``
package (the ``[desktop]`` extra); a headless host, or a Linux box without a
GTK/Qt WebKit backend, gets ``available() == (False, why)`` and the caller
falls back to the system browser. The host process dies with its parent
(stdin EOF), so a crashed Jarvis leaves no ghost player.

Synchronous by design — one pipe round-trip per call, bounded by a timeout —
callers on the event loop wrap it in ``asyncio.to_thread``. The process spawn
is injectable so the unit tests drive a fake host in-process.
"""
from __future__ import annotations

import atexit
import importlib.util
import json
import logging
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

_HOST_MODULE = "jarvis.platform.music_player_host"
_READY_TIMEOUT_S = 20.0
_REQUEST_TIMEOUT_S = 10.0


class MusicPlayerError(RuntimeError):
    """The host is not there, did not answer in time, or refused a command."""


def _has_webview() -> bool:
    try:
        return importlib.util.find_spec("webview") is not None
    except (ImportError, ValueError):  # a broken parent package reads as "not installed"
        return False


def _default_storage_dir() -> Path:
    from jarvis.core.config import DATA_DIR

    return Path(DATA_DIR) / "music_player"


def _default_title() -> str:
    """The window's title: the service first, then who drives it.

    "Personal Jarvis — Music" next to the interpreter's Python logo read as an
    anonymous pipe (maintainer, 2026-08-22); the title now names the service
    the window actually shows.
    """
    from jarvis.core.branding import PRODUCT_NAME

    return f"YouTube Music — {PRODUCT_NAME}"


def _default_icon() -> str:
    """Absolute path of the player's window icon, or ``""`` when not bundled.

    Resolved from the package (``jarvis.assets``), never from the repo root, so
    a wheel install finds it too (the same lesson as ``bundled_app_icon``).
    """
    try:
        from jarvis.assets import bundled_music_player_icon

        path = bundled_music_player_icon()
    except Exception as exc:  # noqa: BLE001 — an icon is a nicety, never a blocker
        log.debug("music player icon unresolved: %s", exc)
        return ""
    return str(path) if path is not None else ""


def _default_spawn(argv: list[str]) -> Any:
    return subprocess.Popen(  # noqa: S603 — fixed argv, own interpreter
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


class MusicPlayer:
    """Client for one background player process (lazy: spawned on first use)."""

    def __init__(
        self,
        *,
        storage_dir: Path | None = None,
        title: str | None = None,
        spawn: Callable[[list[str]], Any] | None = None,
        display_present: Callable[[], bool] | None = None,
        has_webview: Callable[[], bool] | None = None,
    ) -> None:
        self._storage_dir = storage_dir
        self._title = title
        self._spawn = spawn or _default_spawn
        self._display_present = display_present
        self._has_webview = has_webview or _has_webview
        self._proc: Any | None = None
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._replies: dict[int, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._ready = threading.Event()
        self._unavailable_reason = ""
        self._next_id = 0

    # -- capability -----------------------------------------------------------

    def available(self) -> tuple[bool, str]:
        """(can run here, why not). Never raises."""
        try:
            if self._display_present is not None:
                display = self._display_present()
            else:
                from jarvis.platform.capabilities import detect_capabilities

                display = bool(detect_capabilities().display_present)
        except Exception:  # noqa: BLE001 — a probe fault reads as "no display"
            display = False
        if not display:
            return False, "This machine has no desktop session, so there is no player to run."
        if not self._has_webview():
            return (
                False,
                "The background player needs the desktop extras "
                "(`pip install \"personal-jarvis[desktop]\"` adds pywebview).",
            )
        if self._unavailable_reason:
            return False, self._unavailable_reason
        return True, ""

    def is_running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None and self._ready.is_set()

    # -- lifecycle ------------------------------------------------------------

    def start(self, timeout: float = _READY_TIMEOUT_S) -> bool:
        """Spawn the host if needed and wait for its ready line."""
        with self._lock:
            if self.is_running():
                return True
            ok, why = self.available()
            if not ok:
                log.info("music player unavailable: %s", why)
                return False
            self._ready.clear()
            self._replies.clear()
            self._events.clear()
            storage = self._storage_dir or _default_storage_dir()
            title = self._title or _default_title()
            argv = [sys.executable, "-m", _HOST_MODULE, "--storage", str(storage), "--title", title]
            icon = _default_icon()
            if icon:
                argv += ["--icon", icon]
            try:
                self._proc = self._spawn(argv)
            except Exception as exc:  # noqa: BLE001 — degrade to the browser, never raise
                log.warning("music player spawn failed: %s", exc)
                self._proc = None
                return False
            threading.Thread(
                target=self._pump, args=(self._proc,), name="music-player-pump", daemon=True
            ).start()
            if getattr(self._proc, "stderr", None) is not None:
                threading.Thread(
                    target=self._pump_stderr,
                    args=(self._proc,),
                    name="music-player-stderr",
                    daemon=True,
                ).start()
            if not self._ready.wait(timeout):
                if self._unavailable_reason:
                    log.info("music player host unavailable: %s", self._unavailable_reason)
                else:
                    log.warning("music player host not ready within %.0fs", timeout)
                self.stop()
                return False
            atexit.register(self.stop)
            return True

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        self._ready.clear()
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    proc.stdin.write(json.dumps({"id": 0, "cmd": "quit"}) + "\n")
                    proc.stdin.flush()
                except Exception as exc:  # noqa: BLE001 — a closed pipe means it is already going
                    log.debug("music player quit write: %s", exc)
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001 — then it gets killed
                    proc.kill()
        except Exception as exc:  # noqa: BLE001 — teardown must never raise
            log.debug("music player stop: %s", exc)

    # -- transport --------------------------------------------------------------

    def _pump(self, proc: Any) -> None:
        try:
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:  # a stray non-protocol line from the host: skip it
                    continue
                if "event" in msg:
                    if msg["event"] == "ready":
                        self._ready.set()
                    elif msg["event"] == "unavailable":
                        self._unavailable_reason = (
                            "The background player could not start here: "
                            f"{msg.get('error') or 'no GUI backend'}."
                        )
                    with self._cond:
                        self._events.append(msg)
                        self._cond.notify_all()
                    continue
                with self._cond:
                    self._replies[int(msg.get("id") or 0)] = msg
                    self._cond.notify_all()
        except Exception as exc:  # noqa: BLE001 — a dead pipe ends the pump, nothing else
            log.debug("music player pump ended: %s", exc)
        finally:
            self._ready.clear()
            with self._cond:
                self._cond.notify_all()

    @staticmethod
    def _pump_stderr(proc: Any) -> None:
        try:
            for raw in proc.stderr:
                line = raw.rstrip()
                if line:
                    log.debug("music player host: %s", line)
        except Exception as exc:  # noqa: BLE001 — a dead pipe ends the pump
            log.debug("music player stderr pump ended: %s", exc)

    def request(self, cmd: str, *, timeout: float = _REQUEST_TIMEOUT_S, **kw: Any) -> Any:
        """One command → its result. Raises :class:`MusicPlayerError`."""
        if not self.is_running() and not self.start():
            ok, why = self.available()
            raise MusicPlayerError(why or self._unavailable_reason or "player did not start")
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
        payload = json.dumps({"id": req_id, "cmd": cmd, **kw}, ensure_ascii=False)
        proc = self._proc
        if proc is None:
            raise MusicPlayerError("player is not running")
        try:
            proc.stdin.write(payload + "\n")
            proc.stdin.flush()
        except Exception as exc:  # noqa: BLE001 — pipe gone → the host is gone
            self.stop()
            raise MusicPlayerError(f"player went away: {exc}") from exc
        # One fixed deadline: the condition is shared and notified on EVERY
        # reply, so a per-wait timeout would restart on unrelated traffic.
        deadline = time.monotonic() + timeout
        with self._cond:
            while req_id not in self._replies:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._cond.wait(remaining):
                    if req_id in self._replies:
                        break
                    raise MusicPlayerError(f"player did not answer '{cmd}' in {timeout:.0f}s")
                if not self._ready.is_set() and req_id not in self._replies:
                    raise MusicPlayerError("player exited")
            reply = self._replies.pop(req_id)
        if not reply.get("ok"):
            raise MusicPlayerError(str(reply.get("error") or f"'{cmd}' failed"))
        return reply.get("result")

    # -- commands ---------------------------------------------------------------

    def load(self, url: str) -> bool:
        return bool(self.request("load", url=url))

    def state(self, *, timeout: float = _REQUEST_TIMEOUT_S) -> dict[str, Any]:
        """The page state; ``timeout`` bounds the wait for a host that is busy
        (a confirm loop polls this and must not inherit a 10 s wait per read)."""
        result = self.request("state", timeout=timeout)
        return result if isinstance(result, dict) else {}

    def play(self) -> bool:
        return bool(self.request("play"))

    def pause(self) -> bool:
        return bool(self.request("pause"))

    def toggle(self) -> bool:
        return bool(self.request("toggle"))

    def next(self) -> bool:
        return bool(self.request("next"))

    def previous(self) -> bool:
        return bool(self.request("previous"))

    def set_volume(self, level: int) -> bool:
        return bool(self.request("volume", level=int(level)))

    def show(self, *, timeout: float = _REQUEST_TIMEOUT_S) -> bool:
        return bool(self.request("show", timeout=timeout))

    def hide(self) -> bool:
        return bool(self.request("hide"))


_PLAYER: MusicPlayer | None = None
_PLAYER_LOCK = threading.Lock()


def get_music_player() -> MusicPlayer:
    """The process-wide player (one hidden window per Jarvis)."""
    global _PLAYER
    with _PLAYER_LOCK:
        if _PLAYER is None:
            _PLAYER = MusicPlayer()
        return _PLAYER


def background_player_available() -> bool:
    return get_music_player().available()[0]


__all__ = [
    "MusicPlayer",
    "MusicPlayerError",
    "background_player_available",
    "get_music_player",
]
