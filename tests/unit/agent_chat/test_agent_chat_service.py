"""Agent chat — a whole turn through the service and the routes with a fake brain."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat import runner_api
from jarvis.agent_chat.catalog import rows_for
from jarvis.agent_chat.service import AgentChatService, SessionBusy
from jarvis.agent_chat.store import AgentChatStore
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.ui.web.agent_chat_routes import router


class ScriptedBrain:
    """A brain that answers from a script: each entry is a list of deltas."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self.requests: list[BrainRequest] = []

    script: list[list[BrainDelta]] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        ScriptedBrain.seen.append(req)
        step = (
            ScriptedBrain.script.pop(0) if ScriptedBrain.script else [BrainDelta(content="(done)")]
        )
        for d in step:
            await asyncio.sleep(0)
            yield d

    seen: list[BrainRequest] = []


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch):
    ScriptedBrain.script = []
    ScriptedBrain.seen = []
    monkeypatch.setitem(
        runner_api.BRAIN_BY_PROVIDER,
        "fakeprov",
        ("tests.unit.agent_chat.test_agent_chat_service", "ScriptedBrain"),
    )
    # Catalog has no row for fakeprov; supports_api_runner() still says yes.
    return ScriptedBrain


async def _drain(q: asyncio.Queue, until_kind: str, timeout: float = 5.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(
                f"timed out waiting for {until_kind}; got {[e['kind'] for e in out]}"
            )
        ev = await asyncio.wait_for(q.get(), timeout=remaining)
        out.append(ev)
        if ev["kind"] == until_kind:
            return out


def test_turn_streams_text_runs_a_tool_and_asks_for_approval(tmp_path: Path, scripted):
    ScriptedBrain.script = [
        [
            BrainDelta(content="Let me "),
            BrainDelta(content="write it."),
            BrainDelta(
                tool_call={
                    "id": "c1",
                    "name": "Write",
                    "input": {"file_path": "hello.txt", "content": "hi"},
                }
            ),
            BrainDelta(finish_reason="tool_use", usage={"input_tokens": 3, "output_tokens": 4}),
        ],
        [BrainDelta(content="Written."), BrainDelta(finish_reason="stop")],
    ]

    async def scenario() -> None:
        store = AgentChatStore(":memory:")
        svc = AgentChatService(store, assistant_name=lambda: "Testo")
        session = svc.create_session(
            provider="fakeprov", model="m", effort="high", cwd=str(tmp_path)
        )
        q = svc.subscribe(session.session_id)
        turn_id = await svc.send(session.session_id, "please write hello.txt")
        with pytest.raises(SessionBusy):
            await svc.send(session.session_id, "again")

        events = await _drain(q, "approval_required")
        kinds = [e["kind"] for e in events]
        assert kinds[:2] == ["user_message", "turn_started"]
        assert "text_delta" in kinds and "tool_call" in kinds
        approval = events[-1]["payload"]
        assert approval["name"] == "Write" and approval["summary"] == "hello.txt"
        assert svc.pending_approvals(session.session_id) == [approval["approval_id"]]

        assert svc.resolve_approval(session.session_id, approval["approval_id"], "allow")
        events += await _drain(q, "turn_finished")
        kinds = [e["kind"] for e in events]
        assert "approval_resolved" in kinds and "tool_result" in kinds
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi"
        finished = events[-1]["payload"]
        assert finished["turn_id"] == turn_id and finished["status"] == "done"
        assert finished["usage"] == {"input_tokens": 3, "output_tokens": 4}

        # The persisted log has no transient deltas and the final texts.
        stored = store.list_events(session.session_id)
        stored_kinds = [e["kind"] for e in stored]
        assert "text_delta" not in stored_kinds
        texts = [e["payload"]["text"] for e in stored if e["kind"] == "assistant_text"]
        assert texts == ["Let me write it.", "Written."]
        # The second request carried the tool round back to the brain.
        second = ScriptedBrain.seen[1]
        assert second.reasoning_effort == "high"
        assert second.messages[-1].role == "tool"
        assert second.system and "Testo" in second.system
        assert not svc.is_running(session.session_id)

    asyncio.run(scenario())


def test_deny_feeds_a_denied_result_and_allow_always_flips_the_mode(tmp_path: Path, scripted):
    ScriptedBrain.script = [
        [BrainDelta(tool_call={"id": "c1", "name": "RunCommand", "input": {"command": "echo x"}})],
        [BrainDelta(tool_call={"id": "c2", "name": "RunCommand", "input": {"command": "echo y"}})],
        [BrainDelta(tool_call={"id": "c3", "name": "RunCommand", "input": {"command": "echo z"}})],
        [BrainDelta(content="ok")],
    ]

    async def scenario() -> None:
        svc = AgentChatService(AgentChatStore(":memory:"))
        session = svc.create_session(provider="fakeprov", cwd=str(tmp_path))
        q = svc.subscribe(session.session_id)
        await svc.send(session.session_id, "run things")
        ev = (await _drain(q, "approval_required"))[-1]["payload"]
        svc.resolve_approval(session.session_id, ev["approval_id"], "deny")
        events = await _drain(q, "tool_result")
        assert events[-1]["payload"]["is_error"] and "Denied" in events[-1]["payload"]["output"]
        ev = (await _drain(q, "approval_required"))[-1]["payload"]
        svc.resolve_approval(session.session_id, ev["approval_id"], "allow_always")
        events = await _drain(q, "turn_finished")
        kinds = [e["kind"] for e in events]
        # c2 ran after allow_always, c3 ran with no approval card at all.
        assert kinds.count("approval_required") == 0
        assert kinds.count("tool_result") == 2
        assert svc.store.get_session(session.session_id).permission_mode == "auto"  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_cancel_ends_the_turn(tmp_path: Path, scripted):
    ScriptedBrain.script = [
        [
            BrainDelta(
                tool_call={"id": "c1", "name": "Write", "input": {"file_path": "a", "content": "b"}}
            )
        ],
    ]

    async def scenario() -> None:
        svc = AgentChatService(AgentChatStore(":memory:"))
        session = svc.create_session(provider="fakeprov", cwd=str(tmp_path))
        q = svc.subscribe(session.session_id)
        await svc.send(session.session_id, "go")
        await _drain(q, "approval_required")
        assert await svc.cancel(session.session_id)
        events = await _drain(q, "turn_finished")
        assert events[-1]["payload"]["status"] == "cancelled"
        assert not (tmp_path / "a").exists()

    asyncio.run(scenario())


def test_provider_error_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    class Boom:
        def __init__(self, model=None):
            pass

        async def complete(self, req):
            raise RuntimeError("401 invalid api key")
            yield  # pragma: no cover

    monkeypatch.setitem(runner_api.BRAIN_BY_PROVIDER, "boomprov", (__name__, "Boom"))
    import sys

    sys.modules[__name__].Boom = Boom

    async def scenario() -> None:
        svc = AgentChatService(AgentChatStore(":memory:"))
        session = svc.create_session(provider="boomprov", cwd=str(tmp_path))
        q = svc.subscribe(session.session_id)
        await svc.send(session.session_id, "hi")
        events = await _drain(q, "turn_finished")
        assert events[-1]["payload"]["status"] == "error"
        assert "invalid api key" in events[-1]["payload"]["error"]

    asyncio.run(scenario())


# ------------------------------------------------------------------ routes


def _app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.agent_chat = None
    app.state.agent_chat_factory = lambda: AgentChatService(
        AgentChatStore(tmp_path / "db.sqlite"), default_cwd=lambda: str(tmp_path)
    )
    return app


def test_provider_health_reports_each_row_and_caches_the_sweep(tmp_path: Path, monkeypatch):
    """The picker's live state: which connected seats actually answer.

    The sweep costs one real request per provider, so it must be cached and
    must never lose the whole answer to one slow or raising provider.
    """
    import jarvis.ui.web.agent_chat_routes as routes

    calls: list[str] = []

    async def _fake(cfg, provider_id, *, probe=True):
        calls.append(provider_id)
        table = {
            "claude-api": ("error", "bad_key", "Claude (API-Key): 401"),
            "openrouter": ("error", "no_credits", "OpenRouter: 402"),
            "grok": ("ok", "ok", "xAI Grok: ok"),
            "nvidia": ("needs_setup", "not_configured", "NVIDIA NIM: not connected"),
        }
        if provider_id == "gemini":
            raise RuntimeError("boom")  # one bad row must not lose the sweep
        status, reason, detail = table.get(provider_id, ("ok", "ok", f"{provider_id}: ok"))
        return SimpleNamespace(status=status, reason=reason, detail=detail)

    monkeypatch.setattr("jarvis.ui.web.provider_routes.provider_health", _fake, raising=False)
    monkeypatch.setattr(routes, "_health_cache", {})

    with TestClient(_app(tmp_path)) as client:
        body = client.get("/api/agent-chat/provider-health?surface=jarvis").json()
        rows = {r["provider"]: r for r in body["providers"]}
        assert body["cached"] is False
        # Every row of the surface is reported, none dropped.
        assert set(rows) == {p.id for p in rows_for("jarvis")}
        assert rows["claude-api"]["status"] == "error"
        assert rows["claude-api"]["reason"] == "bad_key"
        assert rows["openrouter"]["reason"] == "no_credits"
        assert rows["grok"]["status"] == "ok"
        assert rows["nvidia"]["status"] == "needs_setup"
        # The raiser is "unknown" — never an error the person did, and never a
        # 500 that would cost the other eight answers.
        assert rows["gemini"]["status"] == "unknown"
        assert rows["gemini"]["reason"] == "check_failed"

        # Second call is served from the sweep, not from nine more requests.
        swept = len(calls)
        again = client.get("/api/agent-chat/provider-health?surface=jarvis").json()
        assert again["cached"] is True and len(calls) == swept
        # …unless the caller asks for a fresh one.
        client.get("/api/agent-chat/provider-health?surface=jarvis&refresh=true")
        assert len(calls) > swept


class _CliStatus:
    def __init__(self, connected: bool, mode: str, message: str = "") -> None:
        self.connected = connected
        self.mode = mode
        self.message = message


def test_cli_login_snapshot_ignores_a_stored_api_key_for_claude(monkeypatch):
    """Claude Code spends the subscription. A stored Anthropic key is a
    different seat — counting it as signed-in is how "Key rejected" landed
    on the CLI row in the IDE picker.
    """
    import jarvis.ui.web.agent_chat_routes as routes

    class _Svc:
        def status(self) -> _CliStatus:
            return _CliStatus(True, "api_key", "Connected via Anthropic API key.")

    monkeypatch.setattr("jarvis.claude_auth.ClaudeAuthService", _Svc)
    status, reason, detail = routes._cli_login_snapshot("claude-cli")
    assert status == "needs_setup"
    assert reason == "not_configured"
    assert "API key" in detail


def test_cli_login_snapshot_treats_a_claude_subscription_as_ok(monkeypatch):
    import jarvis.ui.web.agent_chat_routes as routes

    class _Svc:
        def status(self) -> _CliStatus:
            return _CliStatus(True, "subscription", "Connected via Claude Max (a@b.c).")

    monkeypatch.setattr("jarvis.claude_auth.ClaudeAuthService", _Svc)
    status, reason, detail = routes._cli_login_snapshot("claude-cli")
    assert status == reason == "ok"
    assert "Claude Max" in detail


def test_cli_login_snapshot_codex_and_antigravity_use_the_cli_login(monkeypatch):
    import jarvis.ui.web.agent_chat_routes as routes

    class _Codex:
        def status(self) -> _CliStatus:
            return _CliStatus(True, "chatgpt", "Connected via ChatGPT.")

    class _AgyOff:
        def status(self) -> _CliStatus:
            return _CliStatus(True, "api_key", "Connected via Gemini API key.")

    monkeypatch.setattr("jarvis.codex_auth.CodexAuthService", _Codex)
    monkeypatch.setattr("jarvis.google_cli.auth_service.GoogleCliAuthService", _AgyOff)
    assert routes._cli_login_snapshot("codex-cli")[0] == "ok"
    # A Gemini key is the other picker row, not the Antigravity CLI.
    status, reason, _ = routes._cli_login_snapshot("agy-cli")
    assert status == "needs_setup"
    assert reason == "not_configured"


def test_agent_surface_cli_health_does_not_probe_the_api_key(tmp_path: Path, monkeypatch):
    """The IDE picker lists Claude Code under Coding CLIs.

    Its catalog id is still ``claude-api``, so the API-Keys one-token probe
    would report a revoked Anthropic key as "Key rejected" on that CLI row.
    The sweep must ask the CLI login instead, and never call the key probe
    for a CLI-resolved seat.
    """
    import jarvis.ui.web.agent_chat_routes as routes

    api_calls: list[str] = []

    async def _fake_api(cfg, provider_id, *, probe=True):
        api_calls.append(provider_id)
        return SimpleNamespace(status="error", reason="bad_key", detail=f"{provider_id}: 401")

    monkeypatch.setattr("jarvis.ui.web.provider_routes.provider_health", _fake_api, raising=False)
    monkeypatch.setattr(
        routes,
        "_cli_login_snapshot",
        lambda runner: ("ok", "ok", f"{runner}: signed in"),
    )
    monkeypatch.setattr("jarvis.agent_chat.service._claude_cli_installed", lambda: True)
    monkeypatch.setattr(routes, "_health_cache", {})

    with TestClient(_app(tmp_path)) as client:
        body = client.get("/api/agent-chat/provider-health?surface=agent").json()
    rows = {r["provider"]: r for r in body["providers"]}
    assert rows["claude-api"]["status"] == "ok"
    assert rows["claude-api"]["reason"] == "ok"
    assert "Key rejected" not in rows["claude-api"]["detail"]
    assert "claude-api" not in api_calls
    assert "openai-codex" not in api_calls
    assert "antigravity" not in api_calls
    # API-key rows on the same surface still use the key probe.
    assert "openai" in api_calls
    assert rows["openai"]["reason"] == "bad_key"


def test_jarvis_surface_still_probes_the_claude_api_key(tmp_path: Path, monkeypatch):
    """The front page has no CLI seats: Claude there is the Anthropic key."""
    import jarvis.ui.web.agent_chat_routes as routes

    api_calls: list[str] = []

    async def _fake_api(cfg, provider_id, *, probe=True):
        api_calls.append(provider_id)
        return SimpleNamespace(status="error", reason="bad_key", detail="401")

    monkeypatch.setattr("jarvis.ui.web.provider_routes.provider_health", _fake_api, raising=False)
    monkeypatch.setattr(
        routes,
        "_cli_login_snapshot",
        lambda runner: pytest.fail(f"CLI login checked on jarvis surface: {runner}"),
    )
    monkeypatch.setattr("jarvis.agent_chat.service._claude_cli_installed", lambda: True)
    monkeypatch.setattr(routes, "_health_cache", {})

    with TestClient(_app(tmp_path)) as client:
        body = client.get("/api/agent-chat/provider-health?surface=jarvis").json()
    rows = {r["provider"]: r for r in body["providers"]}
    assert "claude-api" in api_calls
    assert rows["claude-api"]["reason"] == "bad_key"


def test_the_jarvis_catalog_is_api_seats_only(tmp_path: Path):
    """What the front page's composer is handed: endpoints behind a key.

    No CLI row, and the dual Claude row resolved to the brain runner with a
    live model list — whether or not Claude Code is on this machine, which is
    exactly what the previous behaviour turned on.
    """
    with TestClient(_app(tmp_path)) as client:
        cat = client.get("/api/agent-chat/catalog?surface=jarvis").json()
        rows = {p["id"]: p for p in cat["providers"]}
        assert not ({"openai-codex", "antigravity", "grok-build"} & set(rows))
        assert {"claude-api", "openai", "gemini", "ollama"} <= set(rows)
        assert all(p["runner"] == "brain" for p in rows.values()), rows
        assert rows["claude-api"]["models_source"] == "live"
        # No row claims a CLI either way, so the picker greys nothing for a
        # missing binary here.
        assert all(p["cli_installed"] is None for p in rows.values())

        # The IDE's chat is untouched: its CLI rows are still there.
        agent_catalog = client.get("/api/agent-chat/catalog?surface=agent").json()
        agent_rows = {p["id"]: p for p in agent_catalog["providers"]}
        assert {"openai-codex", "antigravity", "grok-build"} <= set(agent_rows)

        # And a CLI seat cannot be talked onto the front page by hand.
        refused = client.post(
            "/api/agent-chat/sessions", json={"provider": "openai-codex", "surface": "jarvis"}
        )
        assert refused.status_code == 400, refused.text
        made = client.post(
            "/api/agent-chat/sessions", json={"provider": "openai", "surface": "jarvis"}
        )
        assert made.status_code == 201, made.text
        moved = client.patch(
            f"/api/agent-chat/sessions/{made.json()['session_id']}",
            json={"provider": "antigravity"},
        )
        assert moved.status_code == 400, moved.text


def test_routes_catalog_sessions_and_websocket_snapshot(tmp_path: Path, scripted):
    ScriptedBrain.script = [[BrainDelta(content="hello from fake")]]
    # One portal for the whole test: a bare TestClient opens a fresh event
    # loop per request, and the turn task spawned by POST /messages would be
    # cancelled the moment that request returns.
    with TestClient(_app(tmp_path)) as client:
        _exercise_routes(client, tmp_path)


def _exercise_routes(client: TestClient, tmp_path: Path) -> None:

    cat = client.get("/api/agent-chat/catalog").json()
    ids = [p["id"] for p in cat["providers"]]
    assert "claude-api" in ids and "openai" in ids and "openai-codex" in ids
    claude = next(p for p in cat["providers"] if p["id"] == "claude-api")
    assert claude["effort_levels"][-1] == "max"
    assert cat["default_cwd"] == str(tmp_path)

    created = client.post(
        "/api/agent-chat/sessions",
        json={"provider": "fakeprov", "model": "m", "effort": "xhigh"},
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    assert created.json()["cwd"] == str(tmp_path)

    bad = client.post(
        "/api/agent-chat/sessions", json={"provider": "fakeprov", "cwd": "/no/such/dir"}
    )
    assert bad.status_code == 400

    patched = client.patch(
        f"/api/agent-chat/sessions/{sid}", json={"title": "My chat", "effort": "low"}
    )
    assert patched.json()["title"] == "My chat" and patched.json()["effort"] == "low"

    with client.websocket_connect(f"/api/agent-chat/sessions/{sid}/ws") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["session"]["session_id"] == sid
        # A session_updated event from the PATCH is already in the log.
        assert [e["kind"] for e in snap["events"]] == ["session_updated"]

        sent = client.post(f"/api/agent-chat/sessions/{sid}/messages", json={"text": "hi"})
        assert sent.status_code == 202, sent.text
        kinds: list[str] = []
        while True:
            frame = ws.receive_json()
            if frame["type"] != "event":
                continue
            kinds.append(frame["event"]["kind"])
            if frame["event"]["kind"] == "turn_finished":
                break
        assert kinds[0] == "user_message" and "assistant_text" in kinds

    detail = client.get(f"/api/agent-chat/sessions/{sid}").json()
    assert detail["session"]["title"] == "My chat"
    assert any(e["kind"] == "assistant_text" for e in detail["events"])

    listed = client.get("/api/agent-chat/sessions").json()["sessions"]
    assert listed[0]["session_id"] == sid

    assert client.post(f"/api/agent-chat/sessions/{sid}/cancel").json()["cancelled"] is False
    assert client.delete(f"/api/agent-chat/sessions/{sid}").json()["ok"]
    assert client.get(f"/api/agent-chat/sessions/{sid}").status_code == 404


def test_routes_answer_503_without_a_service(tmp_path: Path):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/api/agent-chat/sessions").status_code == 503


def test_the_jarvis_chat_starts_in_its_own_folder_not_the_whole_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The front page's chat brings a workspace; the IDE's keeps the fallback.

    The Jarvis surface merges the folder tools into the turn, and the
    read-only four (``Read``/``Ls``/``Glob``/``Grep``) are tier ``safe`` — they
    run without an approval card. Defaulting that to the home directory made
    the person's whole profile readable unprompted, and the composer showed its
    leaf ("Administrator" on Windows) as if it were a setting somebody chose.
    The IDE's chat is the opposite case: a coding agent is pointed at a
    checkout, so it keeps the service fallback and its folder chip.
    """
    workspace = tmp_path / "chat-workspace"
    home = tmp_path / "home"
    monkeypatch.setattr("jarvis.core.paths.chat_workspace_dir", lambda: workspace)
    svc = AgentChatService(AgentChatStore(tmp_path / "db.sqlite"), default_cwd=lambda: str(home))

    assert svc.default_cwd("jarvis") == str(workspace)
    # Created on first use: a CLI seat cannot start in a folder that is not there.
    assert workspace.is_dir()
    assert svc.default_cwd("agent") == str(home)

    session = svc.create_session(provider="claude-api", surface="jarvis")
    assert session.cwd == str(workspace)


def test_an_uncreatable_workspace_falls_back_instead_of_failing_the_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only install still gets a chat, just with the old wider folder."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    home = tmp_path / "home"
    monkeypatch.setattr("jarvis.core.paths.chat_workspace_dir", lambda: blocker / "chat-workspace")
    svc = AgentChatService(AgentChatStore(tmp_path / "db.sqlite"), default_cwd=lambda: str(home))

    assert svc.default_cwd("jarvis") == str(home)
