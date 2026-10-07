"""PUT /api/settings/mute-music: the switch is the gesture that may ask for Automation.

The route runs against the REAL ``AudioDuckController`` and ``MacOSScriptDucker`` on a
REAL permission service over ``FakeTCC`` (a stateful model of macOS privacy), so the
response is checked against what the OS was actually asked. The existing keys
(``ok``, ``enabled``, ``persisted``, ``applied_live``) stay exactly as they were; the
permission answer is one additive ``permission`` key.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.audio.ducking.controller import AudioDuckController
from jarvis.audio.ducking.macos import MacOSScriptDucker
from jarvis.platform.permissions import AUTOMATION_TARGETS
from jarvis.ui.web.settings_routes import router
from tests.fakes.fake_tcc import CallKind, DialogPolicy, FakeTCC, install_port, make_non_darwin_port

_MUSIC = "com.apple.Music"
_SPOTIFY = "com.spotify.client"
_LEGACY_KEYS = {"ok", "enabled", "persisted", "applied_live"}
_PLAYER_KEYS = {
    "player",
    "target",
    "outcome",
    "reason",
    "can_open_settings",
    "asked",
    "outside_installed_app",
    "detail",
}


def _osascript(tcc: FakeTCC):
    """Answers the ducker's pure "is it running" queries from the FakeTCC player table."""

    def run(script: str) -> subprocess.CompletedProcess:
        bundle = next(b for _name, b in AUTOMATION_TARGETS if f'"{b}"' in script)
        out = "+" if tcc.player_running(bundle) else "-"
        return subprocess.CompletedProcess(["osascript"], 0, stdout=f"{out}\n", stderr="")

    return run


class _Bus:
    def subscribe(self, _event, _handler) -> None:
        return None


def _app(ducker) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(
        ducking=SimpleNamespace(enabled=False, restore_delay_ms=0, never_mute=[])
    )
    app.state.desktop_app = SimpleNamespace(
        _ducker=AudioDuckController(bus=_Bus(), cfg=app.state.config, ducker=ducker)
    )
    return app


@pytest.fixture(autouse=True)
def _no_toml_write(monkeypatch):
    import jarvis.core.config_writer as cw

    monkeypatch.setattr(cw, "set_mute_music", lambda v, **k: None)


def _mac(monkeypatch, **tcc_kwargs):
    tcc_kwargs.setdefault("installed_players", [_MUSIC, _SPOTIFY])
    tcc_kwargs.setdefault("running_players", [_MUSIC])
    tcc = FakeTCC(**tcc_kwargs)
    install_port(monkeypatch, tcc.port("darwin"))
    client = TestClient(_app(MacOSScriptDucker(run=_osascript(tcc))))
    return tcc, client


def test_switching_on_with_music_running_answers_per_player_and_asks_once(monkeypatch):
    tcc, client = _mac(monkeypatch)
    body = client.put("/api/settings/mute-music", json={"enabled": True}).json()
    assert _LEGACY_KEYS <= set(body)
    assert body["ok"] is True and body["enabled"] is True and body["applied_live"] is True
    permission = body["permission"]
    assert permission["feature"] == "audio_ducking"
    assert permission["checked"] is True and permission["asked"] is True
    assert permission["note"] == "" and permission["not_running"] == ["Spotify"]
    (player,) = permission["players"]
    assert set(player) == _PLAYER_KEYS
    assert player["player"] == "Music" and player["target"] == _MUSIC
    assert player["outcome"] == "granted" and player["reason"] == ""
    assert player["can_open_settings"] is False and player["asked"] is True
    assert [call.target for call in tcc.requests("automation")] == [_MUSIC]


def test_switching_on_with_no_player_running_says_it_is_checked_while_one_runs(monkeypatch):
    tcc, client = _mac(monkeypatch, running_players=[])
    body = client.put("/api/settings/mute-music", json={"enabled": True}).json()
    assert body["applied_live"] is True
    permission = body["permission"]
    assert permission["checked"] is False and permission["asked"] is False
    assert permission["players"] == [] and set(permission["not_running"]) == {"Music", "Spotify"}
    assert "checked while the player is running" in permission["note"]
    assert tcc.calls_of(CallKind.REQUEST) == [] and tcc.implicit_prompts() == []


def test_a_denied_player_comes_back_with_the_way_to_system_settings(monkeypatch):
    tcc, client = _mac(monkeypatch, default_policy=DialogPolicy.DENY)
    body = client.put("/api/settings/mute-music", json={"enabled": True}).json()
    (player,) = body["permission"]["players"]
    assert (player["outcome"], player["reason"]) == ("denied", "denied")
    assert player["can_open_settings"] is True and player["asked"] is True
    assert "Automation access for Music" in player["detail"]

    # A second switch-on does not ask again: macOS keeps the decision.
    again = client.put("/api/settings/mute-music", json={"enabled": True}).json()
    assert again["permission"]["players"][0]["asked"] is False
    assert len(tcc.requests("automation")) == 1 and tcc.ignored_requests() == []


def test_switching_off_asks_nothing_and_adds_no_permission_key(monkeypatch):
    tcc, client = _mac(monkeypatch)
    body = client.put("/api/settings/mute-music", json={"enabled": False}).json()
    assert set(body) == _LEGACY_KEYS and body["enabled"] is False
    tcc.assert_silent()


def test_a_backend_without_an_asking_path_keeps_the_response_exactly_as_it_was(monkeypatch):
    port, tcc = make_non_darwin_port("win32")
    install_port(monkeypatch, port)

    class WindowsLike:  # no prewarm: the Windows ducker has no permission concept
        def mute_others(self, *, own_pid, never):
            return []

        def restore(self, pids):
            return None

    client = TestClient(_app(WindowsLike()))
    body = client.put("/api/settings/mute-music", json={"enabled": True}).json()
    assert body == {"ok": True, "enabled": True, "persisted": True, "applied_live": True}
    tcc.assert_silent()


def test_a_report_that_cannot_be_rendered_never_fails_the_toggle(monkeypatch):
    class BadReport:
        def as_dict(self):
            raise ValueError("not serialisable")

    class Setter:
        async def set_enabled(self, enabled):
            return BadReport()

    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(ducking=SimpleNamespace(enabled=False))
    app.state.desktop_app = SimpleNamespace(_ducker=Setter())
    body = TestClient(app).put("/api/settings/mute-music", json={"enabled": True}).json()
    assert body == {"ok": True, "enabled": True, "persisted": True, "applied_live": True}


def test_get_mute_music_never_asks(monkeypatch):
    tcc, client = _mac(monkeypatch)
    assert client.get("/api/settings/mute-music").json() == {"enabled": False}
    tcc.assert_silent()
