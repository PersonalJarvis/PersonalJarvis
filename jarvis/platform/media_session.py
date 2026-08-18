"""OS media-session seam — read and steer whatever is playing on this machine.

Every desktop OS keeps a "now playing" registry that browsers and music apps
feed the same way they feed the keyboard's media keys: Windows exposes it as
``GlobalSystemMediaTransportControlsSessionManager`` (WinRT), Linux as MPRIS
over D-Bus, macOS through the private MediaRemote framework. YouTube Music in
a browser tab publishes title, artist and album there, and accepts play, pause,
next and previous from it. That registry is the only supported way to control
YouTube Music — Google publishes no remote-playback API — so this module is what
turns "pause the music" into an action instead of a shrug.

Shape (AD-5/AD-6): one :func:`make_media_session_controller` factory picks the
per-OS backend from :func:`detect_platform`; each backend degrades to an honest
:class:`MediaSessionCapability` instead of raising, and nothing here imports a
platform-only package at module scope (HN-7) — the WinRT import lives inside
the Windows backend and is guarded, so a headless ``python:3.11-slim`` boots
unaffected.

Per-OS reality, recorded in ``docs/os-parity.md``:

* **Windows** — full read + control via WinRT when the ``winrt-*`` desktop
  extras are installed; without them, control still works through the OS media
  keys (which cannot tell "pause" from "play", so it is a toggle) and reading
  is honestly unavailable.
* **Linux** — read + control through ``playerctl`` (MPRIS) when it is on PATH
  and a session bus exists; a headless box has neither and says so.
* **macOS** — read + control through ``nowplaying-cli`` (Homebrew) when
  installed. Apple ships no public API for this, so without that tool the
  answer is "control it in YouTube Music" rather than a fake success.

Backends take an injectable runner/manager so the unit tests use fakes, never
mocks and never a real media session.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import detect_platform

log = logging.getLogger(__name__)

PlaybackStatus = Literal["playing", "paused", "stopped", "unknown"]

# The Chrome/Edge PWA of YouTube Music registers a per-app model id that ends in
# the extension id below; a plain tab is just "Chrome"/"MSEdge". Knowing the
# PWA lets the picker prefer the app the user actually installed for music.
_YTM_PWA_MARKERS: tuple[str, ...] = ("cinhimbnkkaeohfgghhklpknlkffjgod", "youtube")

# Substrings that identify a web browser's media session on any OS. Order does
# not matter; matching is case-insensitive.
_BROWSER_MARKERS: tuple[str, ...] = (
    "chrome",
    "chromium",
    "msedge",
    "edge",
    "firefox",
    "brave",
    "opera",
    "vivaldi",
    "arc",
    "zen",
    "safari",
)

# Friendly names for the raw per-OS session identifiers, so a spoken answer
# says "in Google Chrome" instead of "in Chrome._crx_cinhi…".
_APP_LABELS: tuple[tuple[str, str], ...] = (
    ("cinhimbnkkaeohfgghhklpknlkffjgod", "YouTube Music app"),
    ("msedge", "Microsoft Edge"),
    ("chromium", "Chromium"),
    ("chrome", "Google Chrome"),
    ("firefox", "Firefox"),
    ("brave", "Brave"),
    ("vivaldi", "Vivaldi"),
    ("opera", "Opera"),
    ("safari", "Safari"),
    ("spotify", "Spotify"),
    ("apple.music", "Apple Music"),
    ("music", "Music"),
    ("vlc", "VLC"),
)

# WinRT GlobalSystemMediaTransportControlsSessionPlaybackStatus values:
# 0 CLOSED, 1 OPENED, 2 CHANGING, 3 STOPPED, 4 PLAYING, 5 PAUSED.
_WINRT_STATUS: dict[int, PlaybackStatus] = {4: "playing", 5: "paused", 3: "stopped"}

# Windows media-key virtual-key codes (used only by the no-WinRT fallback).
_VK_MEDIA_NEXT_TRACK = 0xB0
_VK_MEDIA_PREV_TRACK = 0xB1
_VK_MEDIA_PLAY_PAUSE = 0xB3
_KEYEVENTF_KEYUP = 0x0002

_CLI_TIMEOUT_S = 3.0


@dataclass(frozen=True, slots=True)
class NowPlaying:
    """What the OS says is playing — the fields a spoken answer needs."""

    title: str
    artist: str
    album: str
    app: str
    status: PlaybackStatus
    position_s: float | None = None
    duration_s: float | None = None
    is_browser: bool = False
    is_youtube_music_app: bool = False

    @property
    def is_playing(self) -> bool:
        return self.status == "playing"

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "is_playing": self.is_playing,
            "status": self.status,
            "track": self.title,
            "artist": self.artist,
            "app": self.app,
        }
        if self.album:
            out["album"] = self.album
        if self.position_s is not None:
            out["position_s"] = round(self.position_s)
        if self.duration_s is not None and self.duration_s > 0:
            out["duration_s"] = round(self.duration_s)
        return out


@dataclass(frozen=True, slots=True)
class MediaSessionCapability:
    """What this machine can do, and — when it cannot — what would fix it."""

    can_read: bool
    can_control: bool
    backend: str
    note: str = ""


class MediaSessionController(Protocol):
    """Async per-OS backend. Every method is exception-free by contract."""

    async def capability(self) -> MediaSessionCapability: ...

    async def now_playing(self) -> NowPlaying | None: ...

    async def play(self) -> bool: ...

    async def pause(self) -> bool: ...

    async def toggle(self) -> bool: ...

    async def next(self) -> bool: ...

    async def previous(self) -> bool: ...


# -- shared helpers -----------------------------------------------------------


def app_label(identifier: str) -> str:
    """Friendly app name for a raw OS session identifier (never empty)."""
    low = (identifier or "").lower()
    for marker, label in _APP_LABELS:
        if marker in low:
            return label
    return identifier or "unknown app"


def is_browser_identifier(identifier: str) -> bool:
    low = (identifier or "").lower()
    return any(marker in low for marker in _BROWSER_MARKERS) or is_ytm_app_identifier(
        identifier
    )


def is_ytm_app_identifier(identifier: str) -> bool:
    low = (identifier or "").lower()
    return any(marker in low for marker in _YTM_PWA_MARKERS)


def _rank(entry: NowPlaying, is_current: bool) -> tuple[int, ...]:
    """Higher sorts first. The YouTube Music app beats a browser tab, a browser
    beats another player, playing beats paused, and the OS's own "current"
    session (the one media keys would hit) breaks ties — that is the session
    the user is most likely talking about."""
    return (
        int(entry.is_youtube_music_app),
        int(entry.is_browser),
        int(entry.is_playing),
        int(is_current),
    )


def pick_session(entries: Sequence[tuple[NowPlaying, bool]]) -> NowPlaying | None:
    """Choose the session a music request most plausibly means.

    ``entries`` are ``(now_playing, is_current)`` pairs. Deterministic and pure,
    so the choice is unit-testable without any OS."""
    if not entries:
        return None
    best = max(entries, key=lambda pair: _rank(pair[0], pair[1]))
    return best[0]


def _same_session(a: NowPlaying, b: NowPlaying) -> bool:
    """Two enumerations of one session: same app, same track."""
    return (a.app, a.title, a.artist, a.album) == (b.app, b.title, b.artist, b.album)


def _norm_status(raw: str) -> PlaybackStatus:
    low = (raw or "").strip().lower()
    if low.startswith("play"):
        return "playing"
    if low.startswith("paus"):
        return "paused"
    if low.startswith("stop"):
        return "stopped"
    return "unknown"


CliRunner = Callable[[Sequence[str]], tuple[int, str]]


def _default_cli_runner(argv: Sequence[str]) -> tuple[int, str]:
    """Bounded, windowless, UTF-8 subprocess. Returns (returncode, stdout)."""
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            list(argv),
            capture_output=True,
            timeout=_CLI_TIMEOUT_S,
            check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("media_session: %s failed: %s", argv[0], exc)
        return 127, ""
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


# -- Null backend (headless / unknown) ----------------------------------------


class NullMediaSession:
    """Honest no-op: nothing here can see or steer a player."""

    def __init__(self, note: str) -> None:
        self._note = note

    async def capability(self) -> MediaSessionCapability:
        return MediaSessionCapability(False, False, "none", self._note)

    async def now_playing(self) -> NowPlaying | None:
        return None

    async def play(self) -> bool:
        return False

    async def pause(self) -> bool:
        return False

    async def toggle(self) -> bool:
        return False

    async def next(self) -> bool:
        return False

    async def previous(self) -> bool:
        return False


# -- Windows ------------------------------------------------------------------


def _load_winrt_manager() -> Any | None:
    """Return the WinRT session-manager class, or None when the desktop extras
    are absent. Guarded lazy import (HN-7)."""
    try:
        from winrt.windows.media.control import (  # type: ignore[import-not-found]
            GlobalSystemMediaTransportControlsSessionManager,
        )
    except Exception:  # noqa: BLE001 — ImportError, or a broken WinRT runtime
        return None
    return GlobalSystemMediaTransportControlsSessionManager


class WindowsMediaSession:
    """WinRT ``GlobalSystemMediaTransportControlsSessionManager`` backend.

    ``manager_factory`` is an awaitable returning an object with
    ``get_current_session()`` and ``get_sessions()``; the tests inject a fake.
    ``key_sender`` is the media-key fallback used when WinRT is unavailable.
    """

    def __init__(
        self,
        manager_factory: Callable[[], Any] | None = None,
        key_sender: Callable[[int], bool] | None = None,
    ) -> None:
        self._manager_factory = manager_factory
        self._key_sender = key_sender or _send_media_key
        self._winrt_checked = False
        self._winrt_cls: Any | None = None

    def _resolve_factory(self) -> Callable[[], Any] | None:
        if self._manager_factory is not None:
            return self._manager_factory
        if not self._winrt_checked:
            self._winrt_checked = True
            self._winrt_cls = _load_winrt_manager()
        if self._winrt_cls is None:
            return None
        cls = self._winrt_cls
        return lambda: cls.request_async()

    async def capability(self) -> MediaSessionCapability:
        if self._resolve_factory() is not None:
            return MediaSessionCapability(True, True, "winrt")
        return MediaSessionCapability(
            False,
            True,
            "media-keys",
            "Reading what plays needs the desktop extras "
            "(`pip install \"personal-jarvis[desktop]\"` adds the winrt "
            "packages); until then play/pause is a blind toggle via the media keys.",
        )

    async def _sessions(self) -> list[tuple[NowPlaying, bool, Any]]:
        """Every registered session as ``(entry, is_current, session)``.

        The OS's "current" session (the one the keyboard's media keys reach)
        is listed first and flagged. Chrome registers one session PER TAB, all
        under the same app id, so "current" is matched by what it plays, never
        by app id — otherwise every Chrome tab would look current at once and
        a paused YouTube video could hijack a "pause the music" aimed at
        YouTube Music (seen live 2026-08-18)."""
        factory = self._resolve_factory()
        if factory is None:
            return []
        for attempt in (0, 1):
            try:
                manager = await factory()
                out: list[tuple[NowPlaying, bool, Any]] = []
                current = manager.get_current_session()
                current_entry = await self._describe(current) if current else None
                if current_entry is not None:
                    out.append((current_entry, True, current))
                for session in list(manager.get_sessions()):
                    entry = await self._describe(session)
                    if entry is None or (
                        current_entry is not None and _same_session(entry, current_entry)
                    ):
                        continue
                    out.append((entry, False, session))
                if out or attempt:
                    return out
            except Exception as exc:  # noqa: BLE001 — a WinRT hiccup is a "nothing playing", never a crash
                log.debug("media_session: WinRT enumeration failed: %s", exc)
                if attempt:
                    return []
            # A track change briefly closes and re-opens the session; one short
            # retry turns that blink into an answer instead of "nothing playing".
            await asyncio.sleep(0.25)
        return []

    @staticmethod
    async def _describe(session: Any) -> NowPlaying | None:
        try:
            ident = getattr(session, "source_app_user_model_id", "") or ""
            props = await session.try_get_media_properties_async()
            playback = session.get_playback_info()
            status_val = int(getattr(playback, "playback_status", 0) or 0)
            # WinRT enum: 0 CLOSED, 1 OPENED, 2 CHANGING, 3 STOPPED, 4 PLAYING, 5 PAUSED
            status = _WINRT_STATUS.get(status_val, "unknown")
            position = duration = None
            try:
                timeline = session.get_timeline_properties()
                pos = getattr(timeline, "position", None)
                end = getattr(timeline, "end_time", None)
                if pos is not None and hasattr(pos, "total_seconds"):
                    position = float(pos.total_seconds())
                if end is not None and hasattr(end, "total_seconds"):
                    duration = float(end.total_seconds())
            except Exception as exc:  # noqa: BLE001 — timeline is optional detail
                log.debug("media_session: timeline unavailable: %s", exc)
            return NowPlaying(
                title=str(getattr(props, "title", "") or ""),
                artist=str(getattr(props, "artist", "") or ""),
                album=str(getattr(props, "album_title", "") or ""),
                app=app_label(ident),
                status=status,
                position_s=position,
                duration_s=duration,
                is_browser=is_browser_identifier(ident),
                is_youtube_music_app=is_ytm_app_identifier(ident),
            )
        except Exception as exc:  # noqa: BLE001 — one bad session must not hide the others
            log.debug("media_session: session describe failed: %s", exc)
            return None

    async def _target(self) -> Any | None:
        rows = await self._sessions()
        if not rows:
            return None
        chosen = pick_session([(entry, is_current) for entry, is_current, _ in rows])
        for entry, _, session in rows:
            if entry is chosen:
                return session
        return None

    async def now_playing(self) -> NowPlaying | None:
        rows = await self._sessions()
        return pick_session([(entry, is_current) for entry, is_current, _ in rows])

    async def _command(self, method: str, key: int) -> bool:
        session = await self._target()
        if session is None:
            # No WinRT (or nothing registered): the media key still reaches
            # whatever the OS considers current, so try that before giving up.
            if self._resolve_factory() is None:
                return self._key_sender(key)
            return False
        try:
            return bool(await getattr(session, method)())
        except Exception as exc:  # noqa: BLE001
            log.debug("media_session: %s failed: %s", method, exc)
            return False

    async def play(self) -> bool:
        return await self._command("try_play_async", _VK_MEDIA_PLAY_PAUSE)

    async def pause(self) -> bool:
        return await self._command("try_pause_async", _VK_MEDIA_PLAY_PAUSE)

    async def toggle(self) -> bool:
        return await self._command("try_toggle_play_pause_async", _VK_MEDIA_PLAY_PAUSE)

    async def next(self) -> bool:
        return await self._command("try_skip_next_async", _VK_MEDIA_NEXT_TRACK)

    async def previous(self) -> bool:
        return await self._command("try_skip_previous_async", _VK_MEDIA_PREV_TRACK)


def _send_media_key(vk: int) -> bool:
    """Press-and-release one media key through user32 (Windows only)."""
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("media_session: media key %#x failed: %s", vk, exc)
        return False


# -- Linux (MPRIS via playerctl) ---------------------------------------------

_PLAYERCTL_FORMAT = "{{title}}\x1f{{artist}}\x1f{{album}}\x1f{{position}}\x1f{{mpris:length}}"


class LinuxMediaSession:
    """MPRIS backend through the ``playerctl`` CLI (no D-Bus binding needed)."""

    def __init__(
        self,
        runner: CliRunner | None = None,
        which: Callable[[str], str | None] | None = None,
    ) -> None:
        self._run = runner or _default_cli_runner
        self._which = which or shutil.which

    def _available(self) -> bool:
        return self._which("playerctl") is not None

    async def capability(self) -> MediaSessionCapability:
        if self._available():
            return MediaSessionCapability(True, True, "playerctl")
        return MediaSessionCapability(
            False,
            False,
            "none",
            "Install playerctl (e.g. `sudo apt install playerctl`) so this "
            "machine can read and steer the browser's music session.",
        )

    async def _players(self) -> list[str]:
        rc, out = await asyncio.to_thread(self._run, ["playerctl", "-l"])
        if rc != 0:
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]

    async def _describe(self, player: str) -> NowPlaying | None:
        rc, status_out = await asyncio.to_thread(
            self._run, ["playerctl", "-p", player, "status"]
        )
        status = _norm_status(status_out) if rc == 0 else "unknown"
        rc, meta = await asyncio.to_thread(
            self._run,
            ["playerctl", "-p", player, "metadata", "--format", _PLAYERCTL_FORMAT],
        )
        if rc != 0:
            return None
        parts = (meta.strip("\n") + "\x1f" * 4).split("\x1f")
        title, artist, album, position, length = (p.strip() for p in parts[:5])
        position_s = _to_float(position)
        length_us = _to_float(length)
        return NowPlaying(
            title=title,
            artist=artist,
            album=album,
            app=app_label(player),
            status=status,
            position_s=position_s,
            duration_s=(length_us / 1_000_000) if length_us else None,
            is_browser=is_browser_identifier(player),
            is_youtube_music_app=is_ytm_app_identifier(player),
        )

    async def _rows(self) -> list[tuple[NowPlaying, str]]:
        if not self._available():
            return []
        rows: list[tuple[NowPlaying, str]] = []
        for player in await self._players():
            entry = await self._describe(player)
            if entry is not None:
                rows.append((entry, player))
        return rows

    async def _target(self) -> str | None:
        rows = await self._rows()
        if not rows:
            return None
        chosen = pick_session([(entry, index == 0) for index, (entry, _) in enumerate(rows)])
        for entry, player in rows:
            if entry is chosen:
                return player
        return None

    async def now_playing(self) -> NowPlaying | None:
        rows = await self._rows()
        return pick_session([(entry, index == 0) for index, (entry, _) in enumerate(rows)])

    async def _command(self, verb: str) -> bool:
        player = await self._target()
        if player is None:
            return False
        rc, _ = await asyncio.to_thread(self._run, ["playerctl", "-p", player, verb])
        return rc == 0

    async def play(self) -> bool:
        return await self._command("play")

    async def pause(self) -> bool:
        return await self._command("pause")

    async def toggle(self) -> bool:
        return await self._command("play-pause")

    async def next(self) -> bool:
        return await self._command("next")

    async def previous(self) -> bool:
        return await self._command("previous")


# -- macOS (nowplaying-cli) ---------------------------------------------------


class MacMediaSession:
    """MediaRemote backend through the ``nowplaying-cli`` Homebrew tool."""

    def __init__(
        self,
        runner: CliRunner | None = None,
        which: Callable[[str], str | None] | None = None,
    ) -> None:
        self._run = runner or _default_cli_runner
        self._which = which or shutil.which

    def _available(self) -> bool:
        return self._which("nowplaying-cli") is not None

    async def capability(self) -> MediaSessionCapability:
        if self._available():
            return MediaSessionCapability(True, True, "nowplaying-cli")
        return MediaSessionCapability(
            False,
            False,
            "none",
            "macOS has no public now-playing API. Install nowplaying-cli "
            "(`brew install nowplaying-cli`) so this machine can read and steer "
            "the browser's music session; otherwise control it in YouTube Music.",
        )

    async def now_playing(self) -> NowPlaying | None:
        if not self._available():
            return None
        fields = ["title", "artist", "album", "playbackRate", "elapsedTime", "duration"]
        rc, out = await asyncio.to_thread(self._run, ["nowplaying-cli", "get", *fields])
        if rc != 0:
            return None
        lines = [line.strip() for line in out.splitlines()]
        lines += [""] * (6 - len(lines))
        title, artist, album, rate, elapsed, duration = (
            "" if value == "null" else value for value in lines[:6]
        )
        if not title and not artist:
            return None
        rate_f = _to_float(rate)
        status: PlaybackStatus = "playing" if rate_f and rate_f > 0 else "paused"
        return NowPlaying(
            title=title,
            artist=artist,
            album=album,
            app="the active player",
            status=status,
            position_s=_to_float(elapsed),
            duration_s=_to_float(duration),
            is_browser=False,
        )

    async def _command(self, verb: str) -> bool:
        if not self._available():
            return False
        rc, _ = await asyncio.to_thread(self._run, ["nowplaying-cli", verb])
        return rc == 0

    async def play(self) -> bool:
        return await self._command("play")

    async def pause(self) -> bool:
        return await self._command("pause")

    async def toggle(self) -> bool:
        return await self._command("togglePlayPause")

    async def next(self) -> bool:
        return await self._command("next")

    async def previous(self) -> bool:
        return await self._command("previous")


def _to_float(raw: str | None) -> float | None:
    try:
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):  # a CLI printed "null"/garbage: no number, not an error
        return None


# -- factory ------------------------------------------------------------------


def make_media_session_controller() -> MediaSessionController:
    """Per-OS backend, honest null object where nothing can be done."""
    from jarvis.platform.capabilities import detect_capabilities

    plat = detect_platform()
    if plat == "win32":
        return WindowsMediaSession()
    if not detect_capabilities().display_present:
        return NullMediaSession(
            "This machine has no desktop session, so there is no player to read or steer."
        )
    if plat == "darwin":
        return MacMediaSession()
    return LinuxMediaSession()


__all__ = [
    "LinuxMediaSession",
    "MacMediaSession",
    "MediaSessionCapability",
    "MediaSessionController",
    "NowPlaying",
    "NullMediaSession",
    "WindowsMediaSession",
    "app_label",
    "is_browser_identifier",
    "is_ytm_app_identifier",
    "make_media_session_controller",
    "pick_session",
]
