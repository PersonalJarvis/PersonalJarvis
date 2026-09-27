"""Agents on subscriptions: CLI seats, per-agent accounts, model switching."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent_chat import runner_cli
from jarvis.agent_chat.catalog import offers, rows_for
from jarvis.agent_chat.service import resolve_runner
from jarvis.agent_chat.store import AgentChatStore
from jarvis.agent_chat.surface_kits import kit_for
from jarvis.society.chat_binding import ensure_session
from jarvis.society.runtime import SocietyRuntime


def test_society_surface_seats_clis_and_apis(monkeypatch):
    kit = kit_for("society")
    assert kit.cli_seats and kit.brain_runner
    ids = {row.id for row in rows_for("society")}
    assert {"openai", "gemini", "grok", "ollama"} <= ids
    assert offers("society", "openai-codex") or "openai-codex" not in ids
    # The dual Claude row: the subscription CLI when installed, else the API.
    monkeypatch.setattr("jarvis.agent_chat.service._claude_cli_installed", lambda: True)
    assert resolve_runner("claude-api", surface="society") == "claude-cli"
    monkeypatch.setattr("jarvis.agent_chat.service._claude_cli_installed", lambda: False)
    assert resolve_runner("claude-api", surface="society") == "brain"
    assert resolve_runner("openai", surface="society") == "brain"
    if "openai-codex" in ids:
        assert resolve_runner("openai-codex", surface="society") == "codex-cli"


def test_chat_store_carries_the_account(tmp_path: Path):
    store = AgentChatStore(tmp_path / "chat.db")
    s = store.create_session(
        provider="claude-api", model="", effort="", cwd="", surface="society", account_id="acc-1"
    )
    assert s.account_id == "acc-1"
    store.update_session(s.session_id, account_id="acc-2")
    assert store.get_session(s.session_id).account_id == "acc-2"
    store.close()
    # An older database without the column migrates on open.
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript(
        "CREATE TABLE agent_chat_sessions (session_id TEXT PRIMARY KEY, title TEXT NOT NULL "
        "DEFAULT '', provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', effort "
        "TEXT NOT NULL DEFAULT '', cwd TEXT NOT NULL DEFAULT '', permission_mode TEXT NOT NULL "
        "DEFAULT 'ask', vendor_session TEXT, created_ms INTEGER NOT NULL, updated_ms INTEGER NOT "
        "NULL, message_count INTEGER NOT NULL DEFAULT 0, preview TEXT NOT NULL DEFAULT '', "
        "surface TEXT NOT NULL DEFAULT 'agent');"
        "INSERT INTO agent_chat_sessions (session_id, provider, created_ms, updated_ms) "
        "VALUES ('x', 'openai', 1, 1);"
    )
    conn.commit()
    conn.close()
    migrated = AgentChatStore(old)
    assert migrated.get_session("x").account_id == ""
    migrated.close()


def test_account_env_prefers_the_pinned_seat(monkeypatch, tmp_path: Path):
    calls: list[tuple[str, str]] = []

    class FakeAccounts:
        @staticmethod
        def resolve(account_id):
            if account_id == "claude-second":
                return SimpleNamespace(id="claude-second", platform="claude")
            if account_id == "codex-x":
                return SimpleNamespace(id="codex-x", platform="codex")
            return None

        @staticmethod
        def active_account(platform):
            return SimpleNamespace(id=f"{platform}-active", platform=platform)

        @staticmethod
        def spawn_env(platform, account_id, base=None):
            calls.append((platform, account_id))
            return {"SEAT": account_id}

    import jarvis

    monkeypatch.setattr(jarvis, "agent_accounts", FakeAccounts, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "jarvis.agent_accounts", FakeAccounts)
    token = runner_cli.ACCOUNT_OVERRIDE.set("claude-second")
    try:
        assert runner_cli._account_env("claude")["SEAT"] == "claude-second"  # noqa: SLF001
        # A pin for another platform never leaks: codex still uses its active seat.
        assert runner_cli._account_env("codex")["SEAT"] == "codex-active"  # noqa: SLF001
    finally:
        runner_cli.ACCOUNT_OVERRIDE.reset(token)
    assert runner_cli._account_env("claude")["SEAT"] == "claude-active"  # noqa: SLF001


async def test_identity_override_replaces_jarvis_layers():
    from jarvis.agent_chat.jarvis_harness import SYSTEM_PREAMBLE, identity_prompt

    text = await identity_prompt(
        user_text="hi",
        history=[{"kind": "user_message", "payload": {"text": "earlier"}}],
        resume=None,
        prompt_override="## You are Scout — Research scout",
    )
    assert text.startswith("## You are Scout")
    assert SYSTEM_PREAMBLE in text
    assert "Jarvis" not in text.split(SYSTEM_PREAMBLE)[0] or "Scout" in text


class FakeService:
    def __init__(self, store: AgentChatStore) -> None:
        self.store = store

    def is_running(self, session_id: str) -> bool:
        return False


async def test_ensure_session_follows_account_and_effort(tmp_path: Path):
    rt = SocietyRuntime(tmp_path, seed_starter_team=False)
    await rt.ensure_started()
    svc = FakeService(AgentChatStore(tmp_path / "chat.db"))
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    try:
        scout, _ = await rt.roster.create(
            name="Scout", provider="openai", model="gpt-5", account_id="", effort="high"
        )
        first = ensure_session(svc, cfg, scout)
        assert first.account_id == "" and first.effort == "high"
        moved = await rt.roster.update(
            "scout", {"provider": "claude-api", "model": "claude-opus-5", "account_id": "seat-2"}
        )
        again = ensure_session(svc, cfg, moved)
        assert again.session_id == first.session_id
        assert again.provider == "claude-api" and again.model == "claude-opus-5"
        assert again.account_id == "seat-2"
    finally:
        await rt.close()


@pytest.fixture
def client(tmp_path: Path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from jarvis.ui.web.society_routes import router

    svc = FakeService(AgentChatStore(tmp_path / "chat.db"))
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    runtime = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )
    app = FastAPI()
    app.include_router(router)
    app.state.society_factory = lambda: runtime
    with TestClient(app) as c:
        yield c, svc


def test_providers_and_model_switch_over_rest(client, monkeypatch):
    c, svc = client
    providers = c.get("/api/society/providers").json()["providers"]
    by_id = {p["id"]: p for p in providers}
    assert "openai" in by_id and by_id["openai"]["subscription"] is False
    assert by_id["ollama"]["keyless"] is True
    if "openai-codex" in by_id:
        assert by_id["openai-codex"]["subscription"] is True
        assert by_id["openai-codex"]["platform"] == "codex"
        assert isinstance(by_id["openai-codex"]["accounts"], list)

    c.post("/api/society/agents", json={"name": "Scout", "provider": "openai", "model": "gpt-5"})
    res = c.post(
        "/api/society/agents/scout/model",
        json={"provider": "gemini", "model": "gemini-3-pro", "effort": "medium", "account_id": ""},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["agent"]["provider"] == "gemini" and body["agent"]["model"] == "gemini-3-pro"
    assert body["runner"] == "brain"
    assert body["reseated"] == "society:scout"
    session = svc.store.get_session("society:scout")
    assert session.provider == "gemini" and session.model == "gemini-3-pro"
    bad = c.post("/api/society/agents/scout/model", json={"provider": "no-such-provider"})
    assert bad.status_code == 422
    listed = c.get("/api/society/agents/scout").json()["agent"]
    assert listed["account_id"] == ""
