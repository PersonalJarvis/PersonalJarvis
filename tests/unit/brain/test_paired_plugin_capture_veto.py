"""A plugin-paired skill must not capture a turn while its plugin is not
connected (2026-08-18).

Two connectors now serve the music domain — Spotify and YouTube Music both
answer "spiel Musik" — and the trigger channel captures unconditionally, so
without this veto the FIRST music skill would steer the model at a tool that
can only answer "not connected" while the connected sibling sits in the tool
surface. Judged only for catalog plugins (every one of them authenticates), on
a real store read, and never on a fault.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from jarvis.brain.manager import BrainManager
from jarvis.skills import guards


class _Store:
    def __init__(self, tokens: dict[str, Any], *, raise_on: str | None = None) -> None:
        self._tokens = tokens
        self._raise_on = raise_on

    def load(self, plugin_id: str) -> Any:
        if plugin_id == self._raise_on:
            raise RuntimeError("corrupted token blob")
        return self._tokens.get(plugin_id)


def _skill(plugin_id: str | None) -> Any:
    frontmatter = SimpleNamespace(plugin_id=plugin_id)
    return SimpleNamespace(name=f"plugin-{plugin_id}", frontmatter=frontmatter)


def _tokens(access: str | None = "tok", needs_reauth: bool = False) -> Any:
    return SimpleNamespace(access=access, needs_reauth=needs_reauth)


def test_veto_reason_is_in_the_closed_vocabulary() -> None:
    assert guards.VETO_PLUGIN_NOT_CONNECTED in guards.VETO_REASONS


def test_disconnected_catalog_plugin_is_vetoed() -> None:
    store = _Store({})
    assert BrainManager._paired_plugin_disconnected(_skill("spotify"), store=store) is True
    assert BrainManager._paired_plugin_disconnected(_skill("youtube_music"), store=store) is True


def test_connected_plugin_is_not_vetoed() -> None:
    store = _Store({"youtube_music": _tokens()})
    assert BrainManager._paired_plugin_disconnected(_skill("youtube_music"), store=store) is False


def test_reauth_flag_and_empty_access_count_as_disconnected() -> None:
    store = _Store({"spotify": _tokens(needs_reauth=True), "gmail": _tokens(access="")})
    assert BrainManager._paired_plugin_disconnected(_skill("spotify"), store=store) is True
    assert BrainManager._paired_plugin_disconnected(_skill("gmail"), store=store) is True


def test_unpaired_or_unknown_plugin_and_store_faults_never_veto() -> None:
    store = _Store({}, raise_on="spotify")
    assert BrainManager._paired_plugin_disconnected(_skill(None), store=store) is False
    community = _skill("some-community-thing")
    assert BrainManager._paired_plugin_disconnected(community, store=store) is False
    assert BrainManager._paired_plugin_disconnected(_skill("spotify"), store=store) is False


def test_production_path_remembers_the_answer_per_plugin(monkeypatch) -> None:
    """The keyring read sits on the turn path, so it happens once per window,
    not once per matched turn."""
    import jarvis.brain.manager as manager_mod
    from jarvis.marketplace import token_store as token_store_mod

    loads: list[str] = []

    class _CountingStore:
        def load(self, plugin_id: str) -> Any:
            loads.append(plugin_id)
            return None

    monkeypatch.setattr(token_store_mod, "TokenStore", _CountingStore)
    monkeypatch.setattr(manager_mod, "_PAIRED_CONNECTION_CACHE", {})
    assert BrainManager._paired_plugin_disconnected(_skill("youtube_music")) is True
    assert BrainManager._paired_plugin_disconnected(_skill("youtube_music")) is True
    assert BrainManager._paired_plugin_disconnected(_skill("spotify")) is True
    assert loads == ["youtube_music", "spotify"]
