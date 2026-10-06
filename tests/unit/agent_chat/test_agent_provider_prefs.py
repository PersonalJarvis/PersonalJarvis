"""The agents' provider choices: several seats on at once, models hidden,
Claude set to its key — and only the agents' surface narrowed by them."""

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
