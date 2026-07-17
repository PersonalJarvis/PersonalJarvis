"""macOS AppleScript audio ducking (tiered: known players, then master volume).

Lowers the app-internal ``sound volume`` of the known media players (Music,
Spotify) via ``osascript`` for the duration of a voice session and restores
the previous volume afterwards. Optionally (opt-in) falls back to lowering the
MASTER output volume when no known player was ducked — note the master
fallback also lowers Jarvis's own TTS voice.

Pure stdlib and safe to import on any OS: the factory only constructs this
class when ``sys.platform == 'darwin'`` and ``osascript`` is on PATH. Every
osascript call is wrapped — a timeout, a non-zero exit (e.g. the Automation
TCC denial ``-1743``), or an unparsable volume degrades to a skipped player,
never an exception out of the runtime path.

CRITICAL script shape: a bare ``tell application ...`` LAUNCHES the app, so
every script guards with ``if application id "..." is running`` INSIDE the
same script and returns ``"-"`` when the player is not running.
"""
from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger("jarvis.audio.ducking")

# Opaque restore tokens — the controller treats them as an opaque list[int]
# of "PIDs", so player tokens and the master token just have to be distinct.
_PLAYERS: dict[int, tuple[str, str]] = {
    1: ("Music", "com.apple.Music"),
    2: ("Spotify", "com.spotify.client"),
}
_MASTER_TOKEN = 100

# Sentinel a script returns when the player is not running.
_NOT_RUNNING = "-"


def _run_osascript(script: str) -> subprocess.CompletedProcess:
    """Default runner: one bounded, windowless osascript invocation."""
    return subprocess.run(  # noqa: S603, S607 — fixed argv, no shell
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=3.0,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def _duck_script(bundle_id: str, target: int) -> str:
    """Read the player volume and duck it — one script, is-running-guarded."""
    return (
        f'if application id "{bundle_id}" is running then\n'
        f'    tell application id "{bundle_id}"\n'
        f"        set prev to sound volume\n"
        f"        if prev > {target} then set sound volume to {target}\n"
        f"        return prev\n"
        f"    end tell\n"
        f"else\n"
        f'    return "{_NOT_RUNNING}"\n'
        f"end if"
    )


def _restore_script(bundle_id: str, volume: int) -> str:
    return (
        f'if application id "{bundle_id}" is running then\n'
        f'    tell application id "{bundle_id}" to set sound volume to {volume}\n'
        f"end if"
    )


def _prewarm_script(bundle_id: str) -> str:
    """Benign guarded query — enough to fire the Automation TCC prompt."""
    return (
        f'if application id "{bundle_id}" is running then\n'
        f'    tell application id "{bundle_id}" to get player state\n'
        f"end if"
    )


def _master_duck_script(target: int) -> str:
    return (
        "set prev to output volume of (get volume settings)\n"
        f"if prev > {target} then set volume output volume {target}\n"
        "return prev"
    )


class MacOSScriptDucker:
    """Tiered AppleScript ducker: known players first, opt-in master fallback."""

    def __init__(
        self,
        *,
        master_fallback: bool = False,
        duck_volume_percent: int = 0,
        run: Callable[[str], subprocess.CompletedProcess] | None = None,
    ) -> None:
        self._master_fallback = bool(master_fallback)
        self._duck = max(0, min(100, int(duck_volume_percent)))
        self._run = run or _run_osascript
        self._saved: dict[int, int] = {}  # token -> previous volume

    @classmethod
    def from_config(cls, cfg: Any | None) -> MacOSScriptDucker:
        try:
            ducking = getattr(cfg, "ducking", None)
            return cls(
                master_fallback=bool(getattr(ducking, "macos_master_fallback", False)),
                duck_volume_percent=int(
                    getattr(ducking, "duck_volume_percent", 0) or 0
                ),
            )
        except Exception:  # noqa: BLE001 — malformed config degrades to defaults
            log.debug("ducking config read failed; using defaults", exc_info=True)
            return cls()

    # ---- protocol ---------------------------------------------------------
    def mute_others(self, *, own_pid: int, never: frozenset[str]) -> list[int]:
        """Duck every running known player; master fallback if none was ducked.

        ``own_pid`` is unused (protocol conformance): the player tier changes
        per-app volumes, so Jarvis's own TTS is never affected by it. Returns
        opaque restore tokens (the controller treats them as PIDs).
        """
        del own_pid  # per-app player volumes never touch our own process
        skip = self._normalized_never(never)
        ducked: list[int] = []
        for token, (name, bundle_id) in _PLAYERS.items():
            if name.lower() in skip:
                continue
            try:
                proc = self._run(_duck_script(bundle_id, self._duck))
                prev = self._parse_volume(proc, name)
                if prev is not None and prev > self._duck:
                    self._saved[token] = prev
                    ducked.append(token)
            except Exception:  # noqa: BLE001 — timeout/TCC denial: skip player
                log.debug("ducking skip (%s)", name, exc_info=True)
        if not ducked and self._master_fallback:
            try:
                proc = self._run(_master_duck_script(self._duck))
                prev = self._parse_volume(proc, "master")
                if prev is not None and prev > self._duck:
                    self._saved[_MASTER_TOKEN] = prev
                    ducked.append(_MASTER_TOKEN)
            except Exception:  # noqa: BLE001
                log.debug("ducking skip (master)", exc_info=True)
        return ducked

    def restore(self, pids: list[int]) -> None:
        """Restore exactly the given tokens. Idempotent; unknown token = no-op."""
        for token in pids:
            prev = self._saved.get(token)
            if prev is None:
                continue
            try:
                if token == _MASTER_TOKEN:
                    self._run(f"set volume output volume {prev}")
                else:
                    _name, bundle_id = _PLAYERS[token]
                    self._run(_restore_script(bundle_id, prev))
                self._saved.pop(token, None)
            except Exception:  # noqa: BLE001
                log.debug("ducking restore skip (token=%s)", token, exc_info=True)

    def prewarm(self) -> None:
        """Fire benign guarded queries so the one-time macOS Automation consent
        prompt appears at enable time rather than mid-session. Best-effort.
        """
        for _token, (name, bundle_id) in _PLAYERS.items():
            try:
                self._run(_prewarm_script(bundle_id))
            except Exception:  # noqa: BLE001
                log.debug("ducking prewarm skip (%s)", name, exc_info=True)

    # ---- internals ---------------------------------------------------------
    @staticmethod
    def _normalized_never(never: frozenset[str]) -> set[str]:
        """Map never-mute entries to player names: case-insensitive, and the
        Windows-style ``.exe`` / macOS ``.app`` suffixes are stripped so one
        allowlist entry ("Spotify.exe") covers both platforms.
        """
        out: set[str] = set()
        for name in never:
            n = name.strip().lower()
            for suffix in (".exe", ".app"):
                if n.endswith(suffix):
                    n = n[: -len(suffix)]
            out.add(n)
        return out

    def _parse_volume(self, proc: Any, target: str) -> int | None:
        """Previous volume from a duck script, or None when not duckable."""
        if getattr(proc, "returncode", 1) != 0:
            log.debug(
                "ducking osascript rc=%s (%s): %s",
                getattr(proc, "returncode", None),
                target,
                (getattr(proc, "stderr", "") or "").strip(),
            )
            return None
        out = (getattr(proc, "stdout", "") or "").strip()
        if not out or out == _NOT_RUNNING:
            return None  # player not running
        return int(float(out))  # ValueError degrades via the caller's except
