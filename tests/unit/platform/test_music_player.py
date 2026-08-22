"""Unit tests for the background music player client (``jarvis/platform/music_player``).

The host process is replaced by an in-process fake that speaks the same
JSON-line protocol over pipe-shaped objects, so these tests cover the client's
spawn, ready-wait, request/reply matching, error mapping and teardown without
a window, a browser or a display.
"""
from __future__ import annotations

import io
import json
import threading
import time
from typing import Any

import pytest

from jarvis.platform.music_player import MusicPlayer, MusicPlayerError


class _Pipe:
    """A blocking line pipe: ``write`` on one side, iteration on the other."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._lines: list[str] = []
        self._closed = False

    def write(self, text: str) -> None:
        with self._cond:
            self._lines.append(text)
            self._cond.notify_all()

    def flush(self) -> None:
        pass

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def __iter__(self):
        return self

    def __next__(self) -> str:
        with self._cond:
            while not self._lines and not self._closed:
                self._cond.wait(0.05)
            if self._lines:
                return self._lines.pop(0)
            raise StopIteration


class FakeHost:
    """Answers like ``music_player_host``: ready on start, JSON replies per id."""

    def __init__(self, *, ready: bool = True, unavailable: str | None = None) -> None:
        self.stdin = _Pipe()
        self.stdout = _Pipe()
        self.stderr = io.StringIO()
        self.commands: list[dict[str, Any]] = []
        self.state: dict[str, Any] = {"has_video": True, "paused": False, "title": "T"}
        self._alive = True
        self._killed = False
        if unavailable:
            self.stdout.write(json.dumps({"event": "unavailable", "error": unavailable}) + "\n")
        elif ready:
            self.stdout.write(json.dumps({"event": "ready"}) + "\n")
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        for raw in self.stdin:
            msg = json.loads(raw)
            self.commands.append(msg)
            cmd = msg.get("cmd")
            if cmd == "quit":
                self.stdout.write(json.dumps({"id": msg["id"], "ok": True}) + "\n")
                self._alive = False
                self.stdout.close()
                return
            if cmd == "state":
                result: Any = dict(self.state)
            elif cmd == "boom":
                self.stdout.write(
                    json.dumps({"id": msg["id"], "ok": False, "error": "ValueError: nope"}) + "\n"
                )
                continue
            elif cmd == "silent":
                continue  # never answers → the client must time out
            else:
                result = True
            self.stdout.write(json.dumps({"id": msg["id"], "ok": True, "result": result}) + "\n")

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        return 0

    def kill(self) -> None:
        self._killed = True
        self._alive = False
        self.stdout.close()


def _player(host: FakeHost, **kw) -> MusicPlayer:
    return MusicPlayer(
        spawn=lambda argv: host,
        display_present=lambda: True,
        has_webview=lambda: True,
        **kw,
    )


def test_available_reports_missing_display_and_missing_webview():
    no_display = MusicPlayer(display_present=lambda: False, has_webview=lambda: True)
    ok, why = no_display.available()
    assert not ok and "desktop session" in why
    no_webview = MusicPlayer(display_present=lambda: True, has_webview=lambda: False)
    ok, why = no_webview.available()
    assert not ok and "desktop extras" in why


def test_start_load_state_and_commands_round_trip():
    host = FakeHost()
    player = _player(host)
    assert player.start() is True and player.is_running()
    assert player.load("https://music.youtube.com/watch?v=x") is True
    assert player.state()["title"] == "T"
    assert player.pause() and player.play() and player.next() and player.previous()
    assert player.set_volume(35) is True
    assert player.show() and player.hide()
    sent = [(c["cmd"], c.get("url"), c.get("level")) for c in host.commands]
    assert ("load", "https://music.youtube.com/watch?v=x", None) in sent
    assert ("volume", None, 35) in sent
    player.stop()
    for _ in range(50):  # the fake host drains its pipe on its own thread
        if host.commands and host.commands[-1]["cmd"] == "quit":
            break
        time.sleep(0.02)
    assert host.commands[-1]["cmd"] == "quit"
    assert not player.is_running()


def test_start_spawns_lazily_on_first_request():
    host = FakeHost()
    player = _player(host)
    assert not player.is_running()
    assert player.state()["has_video"] is True
    assert player.is_running()
    player.stop()


def test_host_error_and_timeout_surface_as_music_player_error():
    host = FakeHost()
    player = _player(host)
    with pytest.raises(MusicPlayerError, match="nope"):
        player.request("boom")
    with pytest.raises(MusicPlayerError, match="did not answer"):
        player.request("silent", timeout=0.3)
    player.stop()


def test_state_and_show_take_a_per_call_timeout():
    """A confirm loop polls ``state``; it must be able to bound each read
    instead of inheriting the 10 s transport default (live 2026-08-22: 18
    reads × 10 s = one 199 s play request)."""
    host = FakeHost()
    player = _player(host)
    assert player.state(timeout=0.5)["has_video"] is True
    assert player.show(timeout=0.5) is True

    class SilentOnState(FakeHost):
        def _serve(self) -> None:
            for raw in self.stdin:
                msg = json.loads(raw)
                if msg.get("cmd") == "state":
                    continue  # never answers
                self.stdout.write(json.dumps({"id": msg["id"], "ok": True, "result": True}) + "\n")

    silent = SilentOnState()
    player = _player(silent)
    started = time.monotonic()
    try:
        player.state(timeout=0.3)
    except MusicPlayerError as exc:
        assert "did not answer" in str(exc)
    else:  # pragma: no cover — a silent host must surface as a timeout
        raise AssertionError("state() returned although the host never answered")
    assert time.monotonic() - started < 2.0


def test_unavailable_host_makes_the_player_unavailable_with_its_reason():
    host = FakeHost(unavailable="WebViewException: no GTK")
    player = _player(host)
    assert player.start(timeout=1.0) is False
    ok, why = player.available()
    assert not ok and "no GTK" in why


def test_spawn_failure_degrades_instead_of_raising():
    def spawn(argv):
        raise OSError("no such interpreter")

    player = MusicPlayer(spawn=spawn, display_present=lambda: True, has_webview=lambda: True)
    assert player.start() is False
    with pytest.raises(MusicPlayerError):
        player.load("x")


def test_argv_carries_the_window_icon_and_names_the_service():
    """Maintainer, 2026-08-22: the player window showed the Python logo and
    "Personal Jarvis — Music" — an anonymous pipe. The host now gets the
    bundled YouTube-Music-plus-Jarvis icon and a title that names the service."""
    from pathlib import Path

    from jarvis.assets import bundled_music_player_icon
    from jarvis.core.branding import PRODUCT_NAME

    seen: list[list[str]] = []

    def spawn(argv):
        seen.append(list(argv))
        return FakeHost()

    player = MusicPlayer(spawn=spawn, display_present=lambda: True, has_webview=lambda: True)
    assert player.start(timeout=1.0) is True
    argv = seen[0]
    assert argv[argv.index("--title") + 1] == f"YouTube Music — {PRODUCT_NAME}"
    icon = bundled_music_player_icon()
    assert icon is not None and icon.is_file(), "the player icon must ship in the package"
    assert argv[argv.index("--icon") + 1] == str(icon)
    # A real multi-size Windows icon, not a renamed PNG: the 256 px frame is
    # what the taskbar scales from, the 16 px one sits in the title bar.
    from PIL import Image

    with Image.open(icon) as ico:
        sizes = set(getattr(ico, "info", {}).get("sizes", set()))
    assert (256, 256) in sizes and (16, 16) in sizes
    assert Path(argv[argv.index("--icon") + 1]).suffix == ".ico"


def test_argv_skips_the_icon_when_it_is_not_bundled(monkeypatch):
    from jarvis.platform import music_player as mp

    monkeypatch.setattr(mp, "_default_icon", lambda: "")
    seen: list[list[str]] = []

    def spawn(argv):
        seen.append(list(argv))
        return FakeHost()

    player = MusicPlayer(spawn=spawn, display_present=lambda: True, has_webview=lambda: True)
    assert player.start(timeout=1.0) is True
    assert "--icon" not in seen[0]


def test_argv_carries_storage_and_title():
    seen: list[list[str]] = []

    def spawn(argv):
        seen.append(list(argv))
        return FakeHost()

    from pathlib import Path

    player = MusicPlayer(
        storage_dir=Path("C:/tmp/profile"),
        title="Nova — Music",
        spawn=spawn,
        display_present=lambda: True,
        has_webview=lambda: True,
    )
    assert player.start()
    argv = seen[0]
    assert "-m" in argv and "jarvis.platform.music_player_host" in argv
    assert argv[argv.index("--storage") + 1].endswith("profile")
    assert argv[argv.index("--title") + 1] == "Nova — Music"
    player.stop()
