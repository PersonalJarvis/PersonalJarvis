"""Composer catalog, persisted receipts and per-turn hands share one contract."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat import runner_brain, tool_catalog
from jarvis.agent_chat.folder_tools import plan_filter
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.agent_chat.tool_catalog import (
    Category,
    ToolChoice,
    build_catalog,
    resolve_choices,
    search_catalog,
)
from jarvis.core.protocols import BrainDelta
from jarvis.ui.web.agent_chat_routes import router
from tests.fakes.fake_brain_manager import FakeBrainManager


def tool(name, description="Read saved information", risk="safe"):
    return SimpleNamespace(name=name, description=description, risk_tier=risk)


def inventory():
    tools = {
        "gmail": tool("gmail", "Read inbox and send email", "ask"),
        "notes/find": tool("notes/find", "Find notes"),
        "wiki-recall": tool("wiki-recall"),
        "run-skill": tool("run-skill", risk="ask"),
        "blocked": tool("blocked", risk="block"),
    }
    plugin = SimpleNamespace(
        id="gmail", display_name="Gmail", description="Email", native_tool="gmail"
    )
    offline = SimpleNamespace(
        id="offline", display_name="Offline", description="Documents", native_tool=None
    )
    skill = SimpleNamespace(value="daily-brief", label="Daily brief", hint="Summarize the day")
    return tools, build_catalog(tools, [plugin, offline], [skill], {"gmail"})


def test_inventory_covers_categories_and_connected_state():
    _, rows = inventory()
    by_id = {row.id: row for row in rows}
    assert by_id["plugin:gmail"].brand == "gmail"
    assert by_id["plugin:gmail"].tool_names == ("gmail",)
    assert not by_id["plugin:offline"].available
    assert by_id["tool:notes/find"].category == "mcp"
    assert by_id["mcp:notes"].tool_names == ("notes/find",)
    assert by_id["tool:wiki-recall"].category == "memory"
    assert by_id["skill:daily-brief"].skill == "daily-brief"
    assert "tool:blocked" not in by_id
    assert len(rows) == len(by_id)


def test_plugin_row_hides_operations_and_bundled_skills():
    tools = {
        "github/create_issue": tool("github/create_issue", "Open an issue"),
        "github/list_issues": tool("github/list_issues", "List issues"),
        "run-skill": tool("run-skill", risk="ask"),
    }
    plugin = SimpleNamespace(
        id="github", display_name="GitHub", description="Repos", native_tool=None
    )
    skill = SimpleNamespace(value="plugin-github", label="GitHub skill", hint="Use GitHub")
    extra = SimpleNamespace(value="daily-brief", label="Daily brief", hint="Summarize")
    rows = build_catalog(tools, [plugin], [skill, extra], {"github"})
    by_id = {row.id: row for row in rows}
    assert by_id["plugin:github"].tool_names == ("github/create_issue", "github/list_issues")
    assert "tool:github/create_issue" not in by_id
    assert "mcp:github" not in by_id
    assert "skill:plugin-github" not in by_id
    assert "skill:daily-brief" in by_id


@pytest.mark.parametrize("ids", [["invented"], ["plugin:offline"], ["plugin:gmail"] * 25])
def test_invalid_choices_fail_closed(ids):
    with pytest.raises(ValueError):
        resolve_choices(ids, inventory()[1])


def test_selection_is_deduplicated_and_plan_still_filters():
    tools, rows = inventory()
    chosen = resolve_choices(["plugin:gmail", "plugin:gmail"], rows)
    assert len(chosen) == 1
    extra = tool_catalog.selection_tools(chosen, tools)
    assert "gmail" in extra
    assert "gmail" not in plan_filter(extra)
    with pytest.raises(ValueError, match="disconnected"):
        tool_catalog.selection_tools(chosen, {})


class Ranker:
    def __init__(self, output):
        self.output = output
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        yield BrainDelta(content=self.output)


async def test_semantics_can_find_no_word_overlap_without_tools():
    _, rows = inventory()
    ranker = Ranker('{"plugin:gmail": 0.95, "fabricated-id": 1}')
    found, mode = await search_catalog(rows, "Posteingang aufräumen", ranker)  # i18n-allow
    assert mode == "semantic"
    assert [row.id for row in found] == ["plugin:gmail"]
    assert ranker.requests[0].tools == ()
    assert len(ranker.requests[0].messages) == 1
    assert "untrusted data" in ranker.requests[0].system


@pytest.mark.parametrize("output", ["bad json", "[]", '{"plugin:gmail": false}'])
async def test_broken_ranker_preserves_exact_match(output):
    found, mode = await search_catalog(inventory()[1], "Gmail", Ranker(output))
    assert found[0].id == "plugin:gmail"
    if output != '{"plugin:gmail": false}':
        assert mode == "text"


async def test_browse_does_not_call_model_and_text_fallback_is_honest():
    ranker = Ranker("must never be used")
    rows = inventory()[1]
    assert await search_catalog(rows, "", ranker) == (rows, "browse")
    assert not ranker.requests
    found, mode = await search_catalog(rows, "notes")
    assert mode == "text" and any(r.id == "mcp:notes" for r in found)


@pytest.mark.parametrize("provider", ["openai", "gemini", "openrouter"])
async def test_one_key_turn_reaches_override_and_survives_reopen(tmp_path, monkeypatch, provider):
    tools, rows = inventory()
    fake = FakeBrainManager()
    fake._tools = tools
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: fake)
    monkeypatch.setattr(runner_brain, "_agent_secret", lambda _r, _p: "one-test-key")
    monkeypatch.setattr(tool_catalog, "live_catalog", lambda *_a, **_k: rows)
    path = tmp_path / "fresh.sqlite"
    svc = AgentChatService(AgentChatStore(path))
    session = svc.create_session(
        provider=provider, model="test", cwd=str(tmp_path), surface="jarvis", permission_mode="ask"
    )
    queue = svc.subscribe(session.session_id)
    await svc.send(session.session_id, "Help me with this", tool_choices=["plugin:gmail"])
    async with asyncio.timeout(5):
        while (await queue.get())["kind"] != "turn_finished":
            pass
    override = fake.calls[0][1]["turn_override"]
    assert override.tools_extra["gmail"] is tools["gmail"]
    assert "gmail" in override.system_extra
    assert override.tool_context["approval_surface"] == "interactive"
    assert fake.seen_key == "one-test-key"
    await svc.send(session.session_id, "A different question")
    async with asyncio.timeout(5):
        while (await queue.get())["kind"] != "turn_finished":
            pass
    assert "gmail" not in fake.calls[1][1]["turn_override"].tools_extra
    svc.store.close()
    reopened = AgentChatStore(path)
    receipts = [
        e["payload"]
        for e in reopened.list_events(session.session_id)
        if e["kind"] == "user_message"
    ]
    assert receipts[0]["tool_choices"][0]["id"] == "plugin:gmail"
    assert "tool_choices" not in receipts[1]
    reopened.close()


def test_http_rejects_stale_selection_before_recording_message(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    svc = AgentChatService(AgentChatStore(":memory:"))
    app.state.agent_chat = svc
    session = svc.create_session(provider="openai", cwd=str(tmp_path), surface="jarvis")
    monkeypatch.setattr(tool_catalog, "live_catalog", lambda *_a, **_k: [])
    with TestClient(app) as client:
        response = client.post(
            f"/api/agent-chat/sessions/{session.session_id}/messages",
            json={"text": "Hello", "tool_choices": ["plugin:gmail"]},
        )
    assert response.status_code == 400
    assert not svc.store.list_events(session.session_id)


def test_wire_category_and_fields_match_typescript():
    root = Path(__file__).resolve().parents[2]
    source = (root / "jarvis/ui/web/frontend/src/components/agentchat/toolChoices.ts").read_text()
    union = re.search(r"export type ToolCategory\s*=\s*([^;]+);", source).group(1)
    assert set(re.findall(r'"([a-z]+)"', union)) == set(get_args(Category))
    interface = source.split("export interface ToolChoice {", 1)[1].split("}", 1)[0]
    assert set(re.findall(r"^\s*(\w+):", interface, re.M)) == set(ToolChoice.model_fields)
    for locale in ("en", "de", "es"):
        data = json.loads(
            (root / f"jarvis/ui/web/frontend/src/i18n/locales/{locale}.json").read_text(
                encoding="utf-8"
            )
        )
        assert set(get_args(Category)) <= data["chat_tools"].keys()


async def test_discovery_uses_chat_default_model_and_own_client_scope(monkeypatch):
    ranker = Ranker('{"plugin:gmail": 0.95}')
    calls = []

    def get_brain(provider, model, *, scope):
        calls.append((provider, model, scope))
        return ranker

    fake = SimpleNamespace(_get_brain=get_brain, _fast_model=lambda _provider: "chosen-model")
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: fake)
    monkeypatch.setattr(tool_catalog, "live_catalog", lambda *_a, **_k: inventory()[1])
    from jarvis.core import config

    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda _provider: None)
    result = await tool_catalog.discover(
        provider="openai", model="", query="Inbox", category="plugins", cwd="", stance="ask"
    )
    assert result["mode"] == "semantic"
    assert calls == [("openai", "chosen-model", "composer-search")]
    assert all(r["category"] == "plugins" for r in result["items"])
    assert not tool_catalog._ranking_busy


def test_discovery_route_serves_catalog_and_openapi_metadata(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    svc = AgentChatService(AgentChatStore(":memory:"), default_cwd=lambda: str(tmp_path))
    app.state.agent_chat = svc
    monkeypatch.setattr(tool_catalog, "live_catalog", lambda *_a, **_k: inventory()[1])
    with TestClient(app) as client:
        result = client.get("/api/agent-chat/tools", params={"category": "memory"})
        assert result.status_code == 200
        assert result.json()["mode"] == "browse"
        assert result.json()["items"][0]["id"] == "tool:wiki-recall"
        assert (
            client.get("/api/agent-chat/tools", params={"provider": "fabricated"}).status_code
            == 400
        )
    operation = app.openapi()["paths"]["/api/agent-chat/tools"]["get"]
    assert operation["tags"] and operation["summary"] and operation["x-jarvis-readonly"]
