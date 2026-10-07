"""The agents' provider choices: several seats on at once, models hidden,
Claude set to its key — the on/off and the key choice narrowing the agents'
surface only, the hidden models every model picker."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agent_chat import agent_provider_prefs as prefs_mod
from jarvis.agent_chat import service


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(prefs_mod, "_cache", None)
    return tmp_path


def test_nothing_saved_means_every_provider_on() -> None:
    prefs = prefs_mod.load()
    assert prefs.enabled("claude-api")
    assert prefs.enabled("openai-codex")
    assert prefs.hidden("openai-codex") == ()


def test_saved_choices_round_trip() -> None:
    prefs_mod.save(
        prefs_mod.parse(
            {
                "disabled": ["grok-build"],
                "api_only": ["claude-api"],
                "hidden_models": {"openai-codex": ["gpt-5.5", "gpt-5.5"], "empty": []},
            }
        )
    )
    prefs = prefs_mod.load()
    assert not prefs.enabled("grok-build")
    assert prefs.enabled("openai-codex")
    assert prefs.hidden("openai-codex") == ("gpt-5.5",)
    assert "empty" not in prefs.to_dict()["hidden_models"]


def test_a_broken_file_reads_as_nothing_saved(data_dir: Path) -> None:
    path = data_dir / "Jarvis" / "agent_chat" / "agent_providers.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert prefs_mod.load() == prefs_mod.AgentProviderPrefs()
    assert prefs_mod.parse(["disabled"]) == prefs_mod.AgentProviderPrefs()


def test_claude_on_its_key_changes_the_agents_surface_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_claude_cli_installed", lambda: True)
    assert service.resolve_runner("claude-api", surface="society") == "claude-cli"

    prefs_mod.save(prefs_mod.parse({"api_only": ["claude-api"]}))

    assert service.resolve_runner("claude-api", surface="society") not in ("claude-cli", "unknown")
    assert service.resolve_runner("claude-api", surface="agent") == "claude-cli"


def test_hidden_models_reach_every_surface_but_the_switch_only_the_agents() -> None:
    from jarvis.ui.web.agent_chat_routes import _catalog_rows

    surfaces = ("agent", "jarvis", "society")
    before = {s: {r["id"]: r["curated_models"] for r in _catalog_rows(s, {})} for s in surfaces}

    prefs_mod.save(
        prefs_mod.parse({"disabled": ["openai"], "hidden_models": {"claude-api": ["opusplan"]}})
    )

    for surface in surfaces:
        rows = {row["id"]: row for row in _catalog_rows(surface, {})}
        assert rows["claude-api"]["hidden_models"] == ["opusplan"], surface
        assert rows["openai"]["hidden_models"] == [], surface
        # The list itself stays whole: the picker filters, and the API Keys
        # page (which reads the same rows) still shows the switched-off model.
        assert {k: r["curated_models"] for k, r in rows.items()} == before[surface], surface
    agents = {row["id"]: row for row in _catalog_rows("society", {})}
    assert agents["openai"]["enabled"] is False
    assert all("enabled" not in row for row in _catalog_rows("agent", {}))


def test_coding_panes_leave_hidden_models_out_but_still_launch_on_them() -> None:
    from jarvis.workspace import launch_picks

    every = [m["id"] for m in launch_picks.offered("claude")["models"]]
    assert "opusplan" in every

    prefs_mod.save(prefs_mod.parse({"hidden_models": {"claude-api": ["opusplan"]}}))

    offered = [m["id"] for m in launch_picks.offered("claude")["models"]]
    assert offered == [m for m in every if m != "opusplan"]
    # A pane already on the model reopens on it: hiding only shortens the list.
    assert launch_picks.normalize_model("claude", "opusplan") == "opusplan"


def test_the_page_route_merges_and_refuses_unknown_ids() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from jarvis.ui.web.society_routes import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    assert client.get("/api/society/provider-prefs").json()["disabled"] == []
    saved = client.put("/api/society/provider-prefs", json={"disabled": ["grok-build"]}).json()
    assert saved["disabled"] == ["grok-build"]
    merged = client.put(
        "/api/society/provider-prefs", json={"hidden_models": {"openai-codex": ["gpt-5.5"]}}
    ).json()
    assert merged["disabled"] == ["grok-build"]
    assert merged["hidden_models"] == {"openai-codex": ["gpt-5.5"]}
    refused = client.put("/api/society/provider-prefs", json={"disabled": ["no-such-provider"]})
    assert refused.status_code == 422
    assert prefs_mod.load().disabled == frozenset({"grok-build"})


def test_agents_are_offered_only_seats_the_agents_tab_can_switch() -> None:
    from jarvis.ui.web.agent_chat_routes import _catalog_rows

    agents = {row["id"]: row for row in _catalog_rows("society", {})}
    # Switched on by default on the Agents tab: offered.
    assert agents["claude-api"]["enabled"] is True and agents["openai-codex"]["enabled"] is True
    # A coding CLI has no switch there, so nobody turned it on for the agents.
    for coding_cli in ("cursor", "opencode"):
        if coding_cli in agents:
            assert agents[coding_cli]["enabled"] is False, coding_cli
    # The coding panes keep every seat.
    assert all("enabled" not in row for row in _catalog_rows("agent", {}))
