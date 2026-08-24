"""The chat surface end to end: a typed turn, run on Jarvis' brain.

The surface used to run a coding agent — a vendor CLI or an in-process tool
loop, with approval cards of its own. Since 2026-08-24 it runs the assistant:
``BrainManager.generate``, with the picked provider and model applied to the
live brain. So these tests fake the BRAIN, not a provider adapter, and there
are no approval cards here — a consequential action is confirmed inside the
answer (``allow_voice_confirm``), the same two-turn flow voice has.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agent_chat.service import AgentChatService, SessionBusy
from jarvis.agent_chat.store import AgentChatStore
from jarvis.core import runtime_refs
from jarvis.ui.web.agent_chat_routes import router


class FakeBrain:
    """A BrainManager stand-in: streams a scripted answer, records the pick."""

    def __init__(self, answer: str = "Two meetings today.", *, fail: str = "") -> None:
        self.answer = answer
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.switched: list[str] = []
        self.models: list[tuple[str, str]] = []
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None

    # -- the pick ---------------------------------------------------------
    async def switch(self, provider: str, *, persist: bool = False) -> None:
        self.switched.append(provider)

    def apply_provider_model(self, provider: str, model: str) -> bool:
        self.models.append((provider, model))
        return True

    # -- the turn ---------------------------------------------------------
    async def generate(self, text: str, **kwargs: Any) -> str:
        self.calls.append((text, kwargs))
        self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError(self.fail)
        consumer = kwargs.get("text_consumer")
        if callable(consumer):
            for piece in self.answer.split(" "):
                consumer(piece + " ")
                await asyncio.sleep(0)
        return self.answer


@pytest.fixture
def brain(monkeypatch: pytest.MonkeyPatch) -> FakeBrain:
    fake = FakeBrain()
    monkeypatch.setattr(runtime_refs, "get_brain_manager", lambda: fake)
    return fake


async def _drain(
    q: asyncio.Queue, until_kind: str, budget_s: float = 5.0
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            seen = [e['kind'] for e in out]
            raise AssertionError(f"timed out waiting for {until_kind}; got {seen}")
        ev = await asyncio.wait_for(q.get(), timeout=remaining)
        out.append(ev)
        if ev["kind"] == until_kind:
            return out


def test_a_typed_turn_runs_on_the_brain_with_the_picked_model(tmp_path: Path, brain: FakeBrain):
    async def scenario() -> None:
        store = AgentChatStore(":memory:")
        svc = AgentChatService(store, assistant_name=lambda: "Testo")
        session = svc.create_session(provider="grok", model="grok-4.3", cwd=str(tmp_path))
        q = svc.subscribe(session.session_id)
        turn_id = await svc.send(session.session_id, "was steht heute an")
        with pytest.raises(SessionBusy):
            await svc.send(session.session_id, "again")

        events = await _drain(q, "turn_finished")
        kinds = [e["kind"] for e in events]
        assert kinds[:2] == ["user_message", "turn_started"]
        assert "text_delta" in kinds, "the answer must stream as it is written"
        assert "assistant_text" in kinds

        started = events[1]["payload"]
        assert started["runner"] == "brain", "the assistant answers, not a coding CLI"
        assert started["provider"] == "grok" and started["model"] == "grok-4.3"

        finished = events[-1]["payload"]
        assert finished["turn_id"] == turn_id
        assert finished["status"] == "done" and finished["error"] is None

        # The pick reached the live brain, both halves of it.
        assert brain.switched == ["grok"]
        assert brain.models == [("grok", "grok-4.3")]

        # It really is the brain's own turn: present-user confirmation on, and
        # the conversation keyed to this session so follow-ups keep context.
        _, kwargs = brain.calls[0]
        assert kwargs["allow_voice_confirm"] is True
        assert kwargs["conversation_id"] == session.session_id
        assert callable(kwargs["text_consumer"])

        # The persisted log keeps the answer, never the token dust.
        stored = [e["kind"] for e in store.list_events(session.session_id)]
        assert "text_delta" not in stored and "assistant_text" in stored
        assert not svc.is_running(session.session_id)

    asyncio.run(scenario())


def test_the_brain_runner_refuses_a_coding_subagent_in_words(brain: FakeBrain):
    """The last line of defence, if a sub-agent ever reaches the brain runner.

    Those rows run their own CLI, so they never get here in practice — but a
    stale session or a hand-made request must be told, not answered as
    somebody else.
    """

    async def scenario() -> None:
        from jarvis.agent_chat.runner_brain import apply_pick

        why = await apply_pick(brain, "antigravity", "")
        assert "coding sub-agent" in why
        assert brain.switched == [], "a sub-agent must never become the brain"

    asyncio.run(scenario())


def test_a_brain_that_is_not_up_yet_is_reported_instead_of_spinning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runtime_refs, "get_brain_manager", lambda: None)

    async def scenario() -> None:
        svc = AgentChatService(AgentChatStore(":memory:"))
        session = svc.create_session(provider="openai", cwd=str(tmp_path))
        q = svc.subscribe(session.session_id)
        await svc.send(session.session_id, "hi")
        events = await _drain(q, "turn_finished")
        assert events[-1]["payload"]["status"] == "error"
        assert "starting up" in events[-1]["payload"]["error"]

    asyncio.run(scenario())


def test_a_failing_turn_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake = FakeBrain(fail="401 invalid api key")
    monkeypatch.setattr(runtime_refs, "get_brain_manager", lambda: fake)

    async def scenario() -> None:
        svc = AgentChatService(AgentChatStore(":memory:"))
        session = svc.create_session(provider="openai", cwd=str(tmp_path))
        q = svc.subscribe(session.session_id)
        await svc.send(session.session_id, "hi")
        events = await _drain(q, "turn_finished")
        assert events[-1]["payload"]["status"] == "error"
        assert "invalid api key" in events[-1]["payload"]["error"]

    asyncio.run(scenario())


def test_cancel_ends_the_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake = FakeBrain()

    async def scenario() -> None:
        fake.release = asyncio.Event()
        monkeypatch.setattr(runtime_refs, "get_brain_manager", lambda: fake)
        svc = AgentChatService(AgentChatStore(":memory:"))
        session = svc.create_session(provider="openai", cwd=str(tmp_path))
        q = svc.subscribe(session.session_id)
        await svc.send(session.session_id, "go")
        await asyncio.wait_for(fake.started.wait(), timeout=5)
        cancelled = asyncio.create_task(svc.cancel(session.session_id))
        fake.release.set()
        assert await asyncio.wait_for(cancelled, timeout=5)
        events = await _drain(q, "turn_finished")
        assert events[-1]["payload"]["status"] in {"cancelled", "done"}

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


def test_routes_catalog_sessions_and_websocket_snapshot(tmp_path: Path, brain: FakeBrain):
    # One portal for the whole test: a bare TestClient opens a fresh event
    # loop per request, and the turn task spawned by POST /messages would be
    # cancelled the moment that request returns.
    with TestClient(_app(tmp_path)) as client:
        _exercise_routes(client, tmp_path)


def _exercise_routes(client: TestClient, tmp_path: Path) -> None:
    cat = client.get("/api/agent-chat/catalog").json()
    ids = [p["id"] for p in cat["providers"]]
    # Both kinds are offered: the API keys and the subscription seats.
    assert {"claude-api", "openai", "openai-codex", "antigravity"} <= set(ids)
    codex = next(p for p in cat["providers"] if p["id"] == "openai-codex")
    assert codex["runner"] == "codex-cli"
    openai = next(p for p in cat["providers"] if p["id"] == "openai")
    assert openai["runner"] == "brain"
    # An API-key row is not a CLI question — reporting False there greyed every
    # brain out as "not installed" (live 2026-08-24).
    assert openai["cli_installed"] is None
    claude = next(p for p in cat["providers"] if p["id"] == "claude-api")
    assert claude["effort_levels"][-1] == "max"
    assert cat["default_cwd"] == str(tmp_path)

    created = client.post(
        "/api/agent-chat/sessions",
        json={"provider": "openai", "model": "gpt-5.5", "effort": "xhigh"},
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    assert created.json()["cwd"] == str(tmp_path)

    bad = client.post(
        "/api/agent-chat/sessions", json={"provider": "openai", "cwd": "/no/such/dir"}
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
