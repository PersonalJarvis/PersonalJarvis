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
from jarvis.core.response_style import CONVERSATIONAL_RESPONSE_STYLE
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


@pytest.mark.parametrize(
    ("user_text", "reply"),
    [
        ("What is two plus two?", "Four."),
        (
            "Explain all the steps in detail and include the code.",
            "## Steps\n" + "Explain the next step completely.\n" * 100
            + "\n```python\nprint('complete')\n```",
        ),
        (
            "What happened to the upload?",
            "The draft is saved, but the upload failed because the connection expired. "
            "Reconnect the account to finish uploading.",
        ),
    ],
    ids=["brief-answer", "requested-detail", "partial-failure"],
)
async def test_api_stream_preserves_complete_replies_under_the_short_default(
    tmp_path: Path, scripted, user_text: str, reply: str,
) -> None:
    scripted.script = [[BrainDelta(content=reply[:12]), BrainDelta(content=reply[12:])]]
    events = []

    async def emit(event):
        events.append(event)

    async def no_approval(*args):
        pytest.fail("A text-only turn must not request approval")

    store = AgentChatStore(":memory:")
    try:
        session = store.create_session(
            provider="fakeprov", model="m", effort="high", cwd=str(tmp_path)
        )
        handle = runner_api.TurnHandle(
            session=session, turn_id="style-test", emit=emit, request_approval=no_approval,
            cancel=asyncio.Event(), output_language="es",
        )
        await runner_api.run_api_turn(handle, user_text)
        sent = scripted.seen[0]
        assert CONVERSATIONAL_RESPONSE_STYLE in sent.system
        assert "Respond in this language: es" in sent.system
        assert sent.messages[-1].content == user_text
        assert sent.max_tokens == runner_api.MAX_TOKENS
        assert "".join(e["payload"]["text"] for e in events if e["kind"] == "text_delta") == reply
        assert [e["payload"]["text"] for e in events if e["kind"] == "assistant_text"] == [reply]
        assert events[-1]["payload"]["status"] == "done"
    finally:
        store.close()


async def _drain(q: asyncio.Queue, until_kind: str, budget_s: float = 5.0) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s
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
            provider="fakeprov", model="m", effort="high", cwd=str(tmp_path), permission_mode="ask"
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
        session = svc.create_session(provider="fakeprov", cwd=str(tmp_path), permission_mode="ask")
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
        session = svc.create_session(provider="fakeprov", cwd=str(tmp_path), permission_mode="ask")
        q = svc.subscribe(session.session_id)
        turn_id = await svc.send(session.session_id, "go")
        await _drain(q, "approval_required")
        assert not await svc.cancel(session.session_id, expected_turn_id="previous-turn")
        assert svc.is_running(session.session_id)
        assert await svc.cancel(session.session_id, expected_turn_id=turn_id)
        events = await _drain(q, "turn_finished")
        assert events[-1]["payload"]["status"] == "cancelled"
        assert not (tmp_path / "a").exists()

    asyncio.run(scenario())


@pytest.mark.asyncio
async def test_failed_turn_setup_releases_pending_reservation(
    tmp_path: Path, scripted, monkeypatch: pytest.MonkeyPatch,
):
    svc = AgentChatService(AgentChatStore(":memory:"))
    session = svc.create_session(provider="fakeprov", cwd=str(tmp_path))
    original_emit = svc._emit
    fail_once = True

    async def failing_emit(session_id, event):
        nonlocal fail_once
        await original_emit(session_id, event)
        if fail_once and event["kind"] == "turn_started":
            fail_once = False
            raise RuntimeError("start recorder failed")

    monkeypatch.setattr(svc, "_emit", failing_emit)

    async def fake_runner(_handle, _text):
        return None

    with pytest.raises(RuntimeError, match="start recorder failed"):
        await svc.send(session.session_id, "First task", control_runner=fake_runner)
    assert not svc.is_running(session.session_id)
    assert svc.running_session_ids() == []
    assert svc.store.list_events(session.session_id)[-1]["payload"]["status"] == "error"

    await svc.send(session.session_id, "Next task", control_runner=fake_runner)
    await svc.wait_turn(session.session_id)
    assert not svc.is_running(session.session_id)


async def test_delivered_incoming_receipt_releases_pre_admission_reservation(
    tmp_path: Path, scripted,
):
    from jarvis.society.delivery import IncomingMessage

    svc = AgentChatService(AgentChatStore(":memory:"))
    session = svc.create_session(provider="fakeprov", cwd=str(tmp_path))
    incoming = IncomingMessage(
        message_id="message-1", sender_id="scout", sender_name="Scout",
        sender_kind="agent", text="Already delivered", prompt="Already delivered",
        trace_id="trace-1", status="delivered", turn_id="prior-turn",
    )

    assert await svc.send(session.session_id, "Duplicate", incoming=incoming) == "prior-turn"
    assert not svc.is_running(session.session_id)
    assert svc.running_session_ids() == []

    async def fake_runner(_handle, _text):
        return None

    await svc.send(session.session_id, "New task", control_runner=fake_runner)
    await svc.wait_turn(session.session_id)
    assert not svc.is_running(session.session_id)


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

    The sweep reads key presence, CLI logins and the passive health record;
    the login reads are not free, so it is cached, and it must never lose the
    whole answer to one slow or raising row.
    """
    import jarvis.ui.web.agent_chat_routes as routes

    calls: list[str] = []
    cli_calls: list[str] = []

    def _fake_cli(runner):
        cli_calls.append(runner)
        return "ok", "ok", f"{runner}: signed in"

    async def _fake(cfg, provider_id):
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
    monkeypatch.setattr("jarvis.agent_chat.service._claude_cli_installed", lambda: False)
    monkeypatch.setattr(routes, "_cli_login_snapshot", _fake_cli)
    monkeypatch.setattr(routes, "_health_cache", {})

    with TestClient(_app(tmp_path)) as client:
        body = client.get("/api/agent-chat/provider-health?surface=jarvis").json()
        rows = {r["provider"]: r for r in body["providers"]}
        assert body["cached"] is False
        # Every row of the surface is reported, none dropped.
        assert set(rows) == {p.id for p in rows_for("jarvis")}
        assert rows["claude-api"]["status"] == "error"
        assert rows["claude-api"]["reason"] == "bad_key"
        assert rows["openai-codex"]["status"] == "ok"
        assert "codex-cli" in cli_calls and "claude-cli" not in cli_calls
        assert rows["openrouter"]["reason"] == "no_credits"
        assert rows["grok"]["status"] == "ok"
        assert rows["nvidia"]["status"] == "needs_setup"
        # The raiser is "unknown" — never an error the person did, and never a
        # 500 that would cost the other eight answers.
        assert rows["gemini"]["status"] == "unknown"
        assert rows["gemini"]["reason"] == "check_failed"

        # The cache covers both API probes and CLI login snapshots.
        swept = (len(calls), len(cli_calls))
        again = client.get("/api/agent-chat/provider-health?surface=jarvis").json()
        assert again["cached"] is True
        assert (len(calls), len(cli_calls)) == swept
        # …unless the caller asks for a fresh one.
        client.get("/api/agent-chat/provider-health?surface=jarvis&refresh=true")
        assert len(calls) > swept[0] and len(cli_calls) > swept[1]


def test_provider_health_sweep_sends_nothing_to_any_provider(tmp_path: Path, monkeypatch):
    """Every open of the chat used to spend one paid completion per keyed
    provider (live 2026-09-30). The composer now reads the passive record of
    real calls: zero provider requests, and a new real outcome drops the
    cached sweep so the next open shows it."""
    import jarvis.ui.web.agent_chat_routes as routes
    from jarvis.brain import provider_health_ledger as ledger
    from jarvis.ui.web import provider_routes
    from tests.fakes.fake_provider_calls import ProviderCallRecorder

    recorder = ProviderCallRecorder().install(monkeypatch)
    monkeypatch.setattr(provider_routes, "_is_credential_present", lambda *args: True)
    monkeypatch.setattr(
        routes, "_cli_login_snapshot", lambda runner: ("ok", "ok", f"{runner}: signed in")
    )
    monkeypatch.setattr("jarvis.agent_chat.service._claude_cli_installed", lambda: False)
    monkeypatch.setattr(routes, "_health_cache", {})

    with TestClient(_app(tmp_path)) as client:
        first = client.get("/api/agent-chat/provider-health?surface=jarvis").json()
        rows = {r["provider"]: r for r in first["providers"]}
        assert rows["grok"]["status"] == "unknown"  # keyed, never used: no dot
        assert rows["grok"]["reason"] == provider_routes.UNVERIFIED_REASON

        ledger.get_ledger().record("grok", ledger.MODALITY_BRAIN, "no_credits")
        second = client.get("/api/agent-chat/provider-health?surface=jarvis").json()

    assert second["cached"] is False
    rows = {r["provider"]: r for r in second["providers"]}
    assert rows["grok"]["status"] == "error"
    assert rows["grok"]["reason"] == "no_credits"
    assert recorder.calls == []


def test_provider_health_sweep_is_dropped_when_any_key_changes(tmp_path: Path, monkeypatch):
    """A key saved or deleted — through the API-Keys page, the Control API, a
    CLI connect flow — must show on the next open, not after five minutes,
    even when the health record had nothing for that provider."""
    import jarvis.ui.web.agent_chat_routes as routes
    from jarvis.core import config as cfg_mod
    from jarvis.ui.web import provider_routes

    present = {"grok": False}
    monkeypatch.setattr(
        provider_routes, "_is_credential_present", lambda spec, *_: present.get(spec.id, True)
    )
    monkeypatch.setattr(
        routes, "_cli_login_snapshot", lambda runner: ("ok", "ok", f"{runner}: signed in")
    )
    monkeypatch.setattr("jarvis.agent_chat.service._claude_cli_installed", lambda: False)
    monkeypatch.setattr(routes, "_health_cache", {})

    with TestClient(_app(tmp_path)) as client:
        first = client.get("/api/agent-chat/provider-health?surface=jarvis").json()
        present["grok"] = True
        cfg_mod._mark_secret_changed("grok_api_key")  # what set_secret does after a write
        second = client.get("/api/agent-chat/provider-health?surface=jarvis").json()

    before = {r["provider"]: r for r in first["providers"]}
    after = {r["provider"]: r for r in second["providers"]}
    assert before["grok"]["status"] == "needs_setup"
    assert second["cached"] is False
    assert after["grok"]["status"] == "unknown"


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


@pytest.mark.parametrize("surface", ["agent", "jarvis"])
def test_cli_surface_health_does_not_probe_the_api_key(tmp_path: Path, monkeypatch, surface):
    """Both chat pickers offer Claude Code as a coding CLI.

    Its catalog id is still ``claude-api``, so the API-Keys one-token probe
    would report a revoked Anthropic key as "Key rejected" on that CLI row.
    The sweep must ask the CLI login instead, and never call the key probe
    for a CLI-resolved seat.
    """
    import jarvis.ui.web.agent_chat_routes as routes

    api_calls: list[str] = []

    async def _fake_api(cfg, provider_id):
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
        body = client.get(f"/api/agent-chat/provider-health?surface={surface}").json()
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


def test_jarvis_surface_probes_the_claude_api_key_when_cli_is_uninstalled(
    tmp_path: Path, monkeypatch
):
    """Without Claude Code, its dual row uses the API while other CLIs keep their logins."""
    import jarvis.ui.web.agent_chat_routes as routes

    api_calls: list[str] = []
    cli_calls: list[str] = []

    async def _fake_api(cfg, provider_id):
        api_calls.append(provider_id)
        return SimpleNamespace(status="error", reason="bad_key", detail="401")

    def _fake_cli(runner):
        cli_calls.append(runner)
        return "ok", "ok", f"{runner}: signed in"

    monkeypatch.setattr("jarvis.ui.web.provider_routes.provider_health", _fake_api, raising=False)
    monkeypatch.setattr(routes, "_cli_login_snapshot", _fake_cli)
    monkeypatch.setattr("jarvis.agent_chat.service._claude_cli_installed", lambda: False)
    monkeypatch.setattr(routes, "_health_cache", {})

    with TestClient(_app(tmp_path)) as client:
        body = client.get("/api/agent-chat/provider-health?surface=jarvis").json()
    rows = {r["provider"]: r for r in body["providers"]}
    assert "claude-api" in api_calls
    assert rows["claude-api"]["reason"] == "bad_key"
    assert "claude-cli" not in cli_calls
    assert "codex-cli" in cli_calls


@pytest.mark.parametrize("claude_installed", [True, False])
def test_the_jarvis_catalog_offers_api_and_cli_seats(tmp_path: Path, monkeypatch, claude_installed):
    """Jarvis offers coding CLIs and API seats, with Claude's installed-state fallback."""
    import jarvis.ui.web.agent_chat_routes as routes

    async def _fake_live_models():
        return {}

    monkeypatch.setattr("jarvis.agent_chat.service._claude_cli_installed", lambda: claude_installed)
    monkeypatch.setattr(routes, "_live_cli_models", _fake_live_models)
    monkeypatch.setattr(
        routes, "_cli_installed", lambda runner: runner == "claude-cli" and claude_installed
    )
    with TestClient(_app(tmp_path)) as client:
        cat = client.get("/api/agent-chat/catalog?surface=jarvis").json()
        rows = {p["id"]: p for p in cat["providers"]}
        assert {"openai-codex", "antigravity", "grok-build"} <= set(rows)
        assert {"claude-api", "openai", "gemini", "ollama"} <= set(rows)
        for provider in ("openai", "gemini", "ollama"):
            assert rows[provider]["runner"] == "brain"
            assert rows[provider]["cli_installed"] is None
        for provider, runner in (
            ("openai-codex", "codex-cli"),
            ("antigravity", "agy-cli"),
            ("grok-build", "grok-cli"),
        ):
            assert rows[provider]["runner"] == runner
            assert rows[provider]["cli_installed"] is False
        assert rows["claude-api"]["runner"] == ("claude-cli" if claude_installed else "brain")
        assert rows["claude-api"]["models_source"] == ("curated" if claude_installed else "live")
        assert rows["claude-api"]["cli_installed"] is (True if claude_installed else None)

        # The IDE's chat is untouched: its CLI rows are still there.
        agent_catalog = client.get("/api/agent-chat/catalog?surface=agent").json()
        agent_rows = {p["id"]: p for p in agent_catalog["providers"]}
        assert {"openai-codex", "antigravity", "grok-build"} <= set(agent_rows)

        # Catalog availability agrees with both session creation and provider changes.
        created = client.post(
            "/api/agent-chat/sessions", json={"provider": "openai-codex", "surface": "jarvis"}
        )
        assert created.status_code == 201, created.text
        assert created.json()["provider"] == "openai-codex"
        made = client.post(
            "/api/agent-chat/sessions", json={"provider": "openai", "surface": "jarvis"}
        )
        assert made.status_code == 201, made.text
        moved = client.patch(
            f"/api/agent-chat/sessions/{made.json()['session_id']}",
            json={"provider": "antigravity"},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["provider"] == "antigravity"


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


def test_allow_always_on_a_kit_that_handles_it_does_not_flip_the_mode(
    tmp_path: Path, scripted, monkeypatch: pytest.MonkeyPatch
):
    """A surface with its own memory for "always allow" (a society agent's
    approval rules) keeps the session's stance; the default surfaces still
    flip to auto (the test above)."""
    from dataclasses import replace

    from jarvis.agent_chat import service as service_mod

    remembered: list[tuple[str, str, dict[str, Any]]] = []

    async def hook(session: Any, name: str, args: dict[str, Any]) -> bool:
        remembered.append((session.session_id, name, dict(args)))
        return True

    real_kit_for = service_mod.kit_for
    monkeypatch.setattr(
        service_mod,
        "kit_for",
        lambda surface: replace(real_kit_for(surface), session_always_allow=hook),
    )
    ScriptedBrain.script = [
        [BrainDelta(tool_call={"id": "c1", "name": "RunCommand", "input": {"command": "echo x"}})],
        [BrainDelta(content="ok")],
    ]

    async def scenario() -> None:
        svc = AgentChatService(AgentChatStore(":memory:"))
        session = svc.create_session(provider="fakeprov", cwd=str(tmp_path), permission_mode="ask")
        q = svc.subscribe(session.session_id)
        await svc.send(session.session_id, "run things")
        ev = (await _drain(q, "approval_required"))[-1]["payload"]
        svc.resolve_approval(session.session_id, ev["approval_id"], "allow_always")
        events = await _drain(q, "turn_finished")
        assert "session_updated" not in [e["kind"] for e in events]
        assert svc.store.get_session(session.session_id).permission_mode != "auto"  # type: ignore[union-attr]
        assert remembered == [(session.session_id, "RunCommand", {"command": "echo x"})]

    asyncio.run(scenario())


def test_a_card_nothing_waits_on_closes_when_clicked(tmp_path: Path) -> None:
    """A click on a card whose asker is gone closes it for good; nothing runs."""

    async def scenario() -> None:
        svc = AgentChatService(AgentChatStore(":memory:"))
        session = svc.store.create_session(
            provider="claude-api", model="m", effort="medium", cwd=str(tmp_path)
        )
        sid = session.session_id
        payload = {"turn_id": "t1", "approval_id": "gone", "call_id": "c1", "name": "Bash"}
        svc.store.append_event(sid, {"kind": "turn_started", "ts_ms": 1, "payload": {"turn_id": "t1"}})
        svc.store.append_event(sid, {"kind": "approval_required", "ts_ms": 2, "payload": payload})

        assert not svc.resolve_approval(sid, "gone", "allow")
        assert await svc.close_stale_approval(sid, "gone")
        last = svc.store.list_events(sid)[-1]
        assert last["kind"] == "approval_resolved"
        assert last["payload"] == {"turn_id": "t1", "approval_id": "gone", "decision": "expired"}
        # Closed once; an id the chat never showed is not invented into it.
        assert not await svc.close_stale_approval(sid, "gone")
        assert not await svc.close_stale_approval(sid, "never-shown")
        assert len(svc.store.list_events(sid)) == 3

    asyncio.run(scenario())


def test_the_approval_route_answers_410_for_an_expired_card(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        svc = app.state.agent_chat_factory()
        app.state.agent_chat = svc
        sid = svc.store.create_session(
            provider="claude-api", model="m", effort="medium", cwd=str(tmp_path)
        ).session_id
        payload = {"turn_id": "t1", "approval_id": "gone", "call_id": "c1", "name": "Bash"}
        svc.store.append_event(sid, {"kind": "approval_required", "ts_ms": 2, "payload": payload})
        expired = client.post(f"/api/agent-chat/sessions/{sid}/approvals/gone", json={"decision": "allow"})
        assert expired.status_code == 410
        unknown = client.post(f"/api/agent-chat/sessions/{sid}/approvals/nope", json={"decision": "allow"})
        assert unknown.status_code == 404


def test_a_new_process_closes_turns_a_restart_left_open(tmp_path: Path) -> None:
    """A turn whose runner died with the old process ends as failed, not Working for ever."""
    db = tmp_path / "agent_chat.db"
    store = AgentChatStore(db)
    open_chat = store.create_session(
        provider="claude-api", model="m", effort="medium", cwd=str(tmp_path)
    )
    done_chat = store.create_session(
        provider="claude-api", model="m", effort="medium", cwd=str(tmp_path)
    )
    for sid, ts in ((open_chat.session_id, 1_000), (done_chat.session_id, 1_000)):
        store.append_event(sid, {
            "kind": "turn_started", "ts_ms": ts, "payload": {"turn_id": f"t-{sid}"},
        })
        store.append_event(sid, {
            "kind": "tool_call", "ts_ms": ts + 500,
            "payload": {"turn_id": f"t-{sid}", "call_id": "c"},
        })
    store.append_event(
        done_chat.session_id,
        {"kind": "turn_finished", "ts_ms": 2_000,
         "payload": {"turn_id": f"t-{done_chat.session_id}", "status": "done"}},
    )

    AgentChatService(AgentChatStore(db))

    sealed = store.list_events(open_chat.session_id)[-1]
    assert sealed["kind"] == "turn_finished"
    assert sealed["payload"]["turn_id"] == f"t-{open_chat.session_id}"
    assert sealed["payload"]["status"] == "error"
    assert sealed["payload"]["error"]
    # Stamped with the last thing the chat heard, so the thread list keeps its order.
    assert sealed["ts_ms"] == 1_500
    assert sealed["payload"]["duration_ms"] == 500
    finished = [e for e in store.list_events(done_chat.session_id) if e["kind"] == "turn_finished"]
    assert len(finished) == 1
    assert store.open_turns() == []
