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

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.skills import guards
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.skill_context import SkillContext, set_skill_context


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


# -- preferred music service (2026-08-18) ---------------------------------------


class _Registry:
    def __init__(self, skills: dict[str, Any]) -> None:
        self._skills = skills

    def get(self, name: str) -> Any:
        return self._skills.get(name)


def _manager_with(preferred: str, connected: set[str]) -> Any:
    """A BrainManager stand-in: only what `_prefer_music_service` touches."""
    music = SimpleNamespace(preferred_service=preferred)
    mgr = SimpleNamespace(_config=SimpleNamespace(music=music))
    mgr._plugin_disconnected = lambda pid, store=None: pid not in connected
    mgr._prefer_music_service = BrainManager._prefer_music_service.__get__(mgr)
    return mgr


def test_generic_music_request_is_swapped_to_the_preferred_connected_service() -> None:
    spotify, ytm = _skill("spotify"), _skill("youtube_music")
    reg = _Registry({"plugin-spotify": spotify, "plugin-youtube_music": ytm})
    mgr = _manager_with("youtube_music", {"spotify", "youtube_music"})
    assert mgr._prefer_music_service(spotify, "spiel mal musik", reg) is ytm
    # a named service still wins over the preference
    named = "spiel das auf spotify"  # i18n-allow: spoken-input sample
    assert mgr._prefer_music_service(ytm, named, reg) is spotify


def test_only_connected_service_wins_under_auto() -> None:
    spotify, ytm = _skill("spotify"), _skill("youtube_music")
    reg = _Registry({"plugin-spotify": spotify, "plugin-youtube_music": ytm})
    mgr = _manager_with("auto", {"youtube_music"})
    assert mgr._prefer_music_service(spotify, "spiel musik", reg) is ytm


def test_capture_rematches_before_the_disconnected_veto() -> None:
    """Live 2026-08-19 17:13: plugin-spotify won, the veto aborted, YouTube
    Music never captured. Rematch first, then veto the winner."""
    spotify, ytm = _skill("spotify"), _skill("youtube_music")
    reg = _Registry({"plugin-spotify": spotify, "plugin-youtube_music": ytm})
    mgr = _manager_with("auto", {"youtube_music"})
    rematched = mgr._prefer_music_service(spotify, "spiel musik", reg)  # i18n-allow
    store = _Store({"youtube_music": _tokens()})
    assert rematched is ytm
    assert BrainManager._paired_plugin_disconnected(rematched, store=store) is False
    assert BrainManager._paired_plugin_disconnected(spotify, store=store) is True


class _StubRunner:
    def render_instructions(self, skill: Any, *, args: dict | None = None) -> str:
        return f"# {skill.name}\nPlay."


def _write_music_skill(root: Path, name: str, plugin_id: str, pattern: str) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        "---\n"
        'schema_version: "1"\n'
        f"name: {name}\n"
        f"description: Play music on {plugin_id}.\n"
        f"plugin_id: {plugin_id}\n"
        "triggers:\n"
        "  - type: voice\n"
        f'    pattern: "{pattern}"\n'
        "    language: [de, en]\n"
        "risk_policy:\n"
        "  default_tier: monitor\n"
        "---\n"
        "# Demo\nPlay the song.\n",
        encoding="utf-8",
    )


def test_match_skill_for_turn_swaps_disconnected_spotify(tmp_path: Path) -> None:
    """The live 17:13 veto-before-rematch: Spotify wins the trigger, is not
    connected, capture must still return YouTube Music."""
    _write_music_skill(tmp_path, "plugin-spotify", "spotify", "(musik|music)")
    _write_music_skill(
        tmp_path, "plugin-youtube_music", "youtube_music", "(youtube music)"
    )
    registry = SkillRegistry(root=tmp_path)
    registry.reload_sync()
    set_skill_context(SkillContext(registry=registry, runner=_StubRunner()))  # type: ignore[arg-type]
    try:
        mgr = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
        mgr._plugin_disconnected = (  # type: ignore[method-assign]
            lambda pid, store=None: pid != "youtube_music"
        )
        matched = mgr._match_skill_for_turn("spiel mal musik")  # i18n-allow
        assert matched is not None
        assert matched.name == "plugin-youtube_music"
    finally:
        set_skill_context(None)


def test_no_swap_when_the_match_already_fits_or_the_sibling_is_missing() -> None:
    spotify, ytm = _skill("spotify"), _skill("youtube_music")
    reg = _Registry({"plugin-spotify": spotify, "plugin-youtube_music": ytm})
    mgr = _manager_with("spotify", {"spotify", "youtube_music"})
    assert mgr._prefer_music_service(spotify, "spiel musik", reg) is spotify
    # non-music skills are never touched
    gmail = _skill("gmail")
    assert mgr._prefer_music_service(gmail, "spiel musik", reg) is gmail
    # sibling absent from the registry → keep the match
    lonely = _Registry({"plugin-spotify": spotify})
    mgr = _manager_with("youtube_music", {"spotify", "youtube_music"})
    assert mgr._prefer_music_service(spotify, "spiel musik", lonely) is spotify
