"""Jarvis agents open and steer coding threads (jarvis/society/coding_threads.py).

The chat service is the real one with a real store; only the coding CLI is
replaced by a scripted runner, so no binary runs and nothing is billed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.society import coding_threads
from jarvis.society.coding_threads import (
    MAX_WAKES,
    CodingThreads,
    CodingThreadTool,
    match_model,
    match_provider,
    open_plan,
    open_question,
)

pytestmark = pytest.mark.asyncio


class ScriptedService(AgentChatService):
    """The real service; every turn runs the next scripted step instead of a CLI."""

    def __init__(self, store: AgentChatStore) -> None:
        super().__init__(store)
        self.prompts: list[tuple[str, str]] = []
        self.steps: dict[str, list[Any]] = {}

    async def send(self, session_id: str, text: str, *args: Any, **kwargs: Any) -> str:
        async def runner(handle: Any, prompt: str) -> None:
            self.prompts.append((session_id, prompt))
            queue = self.steps.get(session_id) or []
            step = queue.pop(0) if queue else "done"
            for event in step if isinstance(step, list) else [step]:
                if isinstance(event, str):
                    event = make_event("assistant_text", {"text": event})
                event["payload"].setdefault("turn_id", handle.turn_id)
                await handle.emit(event)
            # A real runner ends its own turn.
            await handle.emit(
                make_event("turn_finished", {"turn_id": handle.turn_id, "status": "done"})
            )

        kwargs.setdefault("control_runner", runner)
        return await super().send(session_id, text, *args, **kwargs)


class Society:
    """The slice of the society runtime the tool and the coordinator use."""

    def __init__(self, service: ScriptedService) -> None:
        self.service = service
        self.halted = False
        self.agents = {
            "nova": SimpleNamespace(agent_id="nova", name="Nova", state="active"),
        }
        self.store = SimpleNamespace(kill_switch=self._kill_switch)
        self.roster = SimpleNamespace(get=self._get)
        self.coding_threads = CodingThreads(self)

    async def _kill_switch(self) -> bool:
        return self.halted

    async def _get(self, agent_id: str) -> Any:
        return self.agents.get(agent_id)

    def chat_service(self) -> ScriptedService:
        return self.service


@pytest.fixture
async def society(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(coding_threads, "SETTLE_S", 0.01)
    monkeypatch.setattr(coding_threads, "RETRY_S", 0.02)

    async def installed() -> dict[str, bool]:
        return {}

    projects: list[str] = []
    monkeypatch.setattr(coding_threads, "_installed_clis", installed)
    monkeypatch.setattr(coding_threads, "_ensure_project", projects.append)
    service = ScriptedService(AgentChatStore(":memory:"))
    rt = Society(service)
    rt.projects = projects
    await rt.coding_threads.start()
    owner = service.create_session(provider="claude-api", cwd=str(tmp_path), surface="agent")
    rt.owner = owner.session_id
    yield rt
    await rt.coding_threads.close()


def tool(rt: Society) -> CodingThreadTool:
    return CodingThreadTool(rt, "nova", session_id=rt.owner)


async def settle(rt: Society, session_id: str) -> None:
    for _ in range(200):
        await asyncio.sleep(0.01)
        task = rt.coding_threads._task
        if not rt.service.is_running(session_id) and (task is None or task.done()):
            if not any(rt.service.is_running(s) for s in rt.service.running_session_ids()):
                return
    raise AssertionError("the chats did not settle")


async def open_thread(rt: Society, folder: Path, prompt: str = "Add a login page") -> str:
    result = await tool(rt).execute(
        {"action": "open", "agent": "Claude Code", "folder": str(folder), "prompt": prompt,
         "model": "Opus 5.5"},
        None,
    )
    assert result.success, result.error
    return str(result.output["thread_id"])


async def test_open_starts_a_thread_the_person_can_watch(society: Society, tmp_path: Path):
    rt = society
    rt_service = rt.service
    thread = await open_thread(rt, tmp_path)
    session = rt_service.store.get_session(thread)
    assert session.surface == "agent"
    folder = await asyncio.to_thread(tmp_path.resolve)
    assert session.cwd == str(folder)
    assert session.model == "claude-opus-5-5"
    assert rt.projects == [str(folder)]
    first = rt_service.store.list_events(thread)[0]
    assert first["kind"] == "user_message"
    # The thread shows the agent's own words and who wrote them.
    assert first["payload"]["text"] == "Add a login page"
    assert session.title == "Add a login page"
    assert first["payload"]["author"] == {"agent_id": "nova", "name": "Nova"}
    owner = rt_service.store.thread_owner(thread)
    assert owner["owner_agent"] == "nova" and owner["owner_session"] == rt.owner
    notices = [e for e in rt_service.store.list_events(rt.owner) if e["kind"] == "notice"]
    assert notices[-1]["payload"]["kind"] == "coding_thread"
    assert notices[-1]["payload"]["thread_id"] == thread


async def test_a_finished_thread_wakes_its_owner_with_the_answer(
    society: Society, tmp_path: Path
):
    rt = society
    thread = await open_thread(rt, tmp_path)
    rt.service.steps[thread] = ["Added login.tsx and its test. All tests pass."]
    await settle(rt, thread)
    owner_prompts = [p for sid, p in rt.service.prompts if sid == rt.owner]
    assert len(owner_prompts) == 1
    assert "Added login.tsx" in owner_prompts[0]
    assert "[coding thread update]" in owner_prompts[0]
    incoming = [e for e in rt.service.store.list_events(rt.owner) if e["kind"] == "agent_message"]
    assert incoming[-1]["payload"]["text"].startswith("Claude Code finished")


async def test_a_turn_the_person_typed_does_not_wake_the_agent(
    society: Society, tmp_path: Path
):
    rt = society
    thread = await open_thread(rt, tmp_path)
    await settle(rt, thread)
    before = len([1 for sid, _ in rt.service.prompts if sid == rt.owner])
    await rt.service.send(thread, "Also rename the button")
    await settle(rt, thread)
    after = len([1 for sid, _ in rt.service.prompts if sid == rt.owner])
    assert after == before
    # The agent writing again takes the thread back.
    result = await tool(rt).execute(
        {"action": "send", "thread_id": thread, "prompt": "Now add the logout"}, None
    )
    assert result.success, result.error
    await settle(rt, thread)
    assert len([1 for sid, _ in rt.service.prompts if sid == rt.owner]) == before + 1


async def test_the_agent_answers_the_threads_question(society: Society, tmp_path: Path):
    rt = society
    question = make_event(
        "question_required",
        {
            "question_id": "q1",
            "deferred": True,
            "questions": [
                {
                    "question": "Which database?",
                    "options": [
                        {"label": "SQLite", "description": ""},
                        {"label": "Postgres", "description": ""},
                    ],
                    "recommended": 0,
                    "recommendation_reason": "simplest",
                }
            ],
        },
    )
    thread = await open_thread(rt, tmp_path)
    rt.service.steps[thread] = [["Which database should I use?", question]]
    await settle(rt, thread)
    owner_prompts = [p for sid, p in rt.service.prompts if sid == rt.owner]
    assert "Which database?" in owner_prompts[-1]
    assert open_question(rt.service.store.list_events(thread))["question_id"] == "q1"

    result = await tool(rt).execute(
        {"action": "answer", "thread_id": thread, "answers": ["postgres"]}, None
    )
    assert result.success, result.error
    await settle(rt, thread)
    events = rt.service.store.list_events(thread)
    assert open_question(events) is None
    answer = [e for e in events if e["kind"] == "user_message"][-1]["payload"]
    assert answer["author"]["agent_id"] == "nova"
    assert "Postgres" in answer["text"]


async def test_plan_cards_close_with_build(society: Society, tmp_path: Path):
    rt = society
    thread = await open_thread(rt, tmp_path)
    rt.service.steps[thread] = [["The plan: 1. schema 2. API", make_event(
        "plan_ready", {"build_mode": "acceptEdits"}
    )]]
    await settle(rt, thread)
    assert open_plan(rt.service.store.list_events(thread)) is not None
    assert "Its plan:" in [p for sid, p in rt.service.prompts if sid == rt.owner][-1]
    result = await tool(rt).execute(
        {"action": "answer", "thread_id": thread, "decision": "build"}, None
    )
    assert result.success, result.error
    await settle(rt, thread)
    assert open_plan(rt.service.store.list_events(thread)) is None
    assert rt.service.store.get_session(thread).permission_mode == "acceptEdits"


async def test_only_the_owner_steers_a_thread(society: Society, tmp_path: Path):
    rt = society
    thread = await open_thread(rt, tmp_path)
    rt.agents["mira"] = SimpleNamespace(agent_id="mira", name="Mira", state="active")
    other = CodingThreadTool(rt, "mira", session_id="society:mira")
    result = await other.execute(
        {"action": "send", "thread_id": thread, "prompt": "delete everything"}, None
    )
    assert not result.success
    assert "not one of your" in (result.error or "")
    listed = await other.execute({"action": "threads"}, None)
    assert listed.output["threads"] == []


async def test_wake_ups_stop_at_the_cap_until_the_person_writes(
    society: Society, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rt = society
    monkeypatch.setattr(coding_threads, "MAX_WAKES", 1)
    thread = await open_thread(rt, tmp_path)
    await settle(rt, thread)
    assert len([1 for sid, _ in rt.service.prompts if sid == rt.owner]) == 1
    await tool(rt).execute({"action": "send", "thread_id": thread, "prompt": "more"}, None)
    await settle(rt, thread)
    assert len([1 for sid, _ in rt.service.prompts if sid == rt.owner]) == 1
    events = rt.service.store.list_events(rt.owner)
    notices = [e["payload"] for e in events if e["kind"] == "notice"]
    assert notices[-1]["kind"] == "coding_thread_paused"
    assert rt.service.store.thread_owner(thread)["state"]["paused"] is True
    # The person speaks in the agent's chat: the budget starts over.
    await rt.service.send(rt.owner, "keep going")
    await settle(rt, rt.owner)
    state = rt.service.store.thread_owner(thread)["state"]
    assert state["wakes"] == 0 and state["paused"] is False
    assert MAX_WAKES > 1


async def test_the_kill_switch_holds_reports(society: Society, tmp_path: Path):
    rt = society
    rt.halted = False
    thread = await open_thread(rt, tmp_path)
    rt.halted = True
    await asyncio.sleep(0.3)
    assert [p for sid, p in rt.service.prompts if sid == rt.owner] == []
    assert rt.service.store.thread_owner(thread)["state"]["outbox"]
    rt.halted = False
    rt.coding_threads._kick()
    await settle(rt, thread)
    assert len([p for sid, p in rt.service.prompts if sid == rt.owner]) == 1


async def test_read_and_files_stay_inside_the_thread(society: Society, tmp_path: Path):
    rt = society
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    thread = await open_thread(rt, tmp_path)
    await settle(rt, thread)
    read = await tool(rt).execute({"action": "read", "thread_id": thread}, None)
    assert read.success
    froms = [entry["from"] for entry in read.output["entries"]]
    assert froms[0] == "Nova (you)" and "coding agent" in froms
    listing = await tool(rt).execute(
        {"action": "files", "thread_id": thread, "op": "ls"}, None
    )
    assert listing.success and "app.py" in listing.output["output"]
    content = await tool(rt).execute(
        {"action": "files", "thread_id": thread, "op": "read", "path": "app.py"}, None
    )
    assert "hello" in content.output["output"]
    outside = await tool(rt).execute(
        {"action": "files", "thread_id": thread, "op": "read", "path": "../secret.txt"}, None
    )
    assert not outside.success


async def test_open_needs_a_real_folder_and_a_known_agent(society: Society, tmp_path: Path):
    rt = society
    missing = await tool(rt).execute(
        {"action": "open", "agent": "claude", "folder": str(tmp_path / "nope"), "prompt": "x"},
        None,
    )
    assert not missing.success and "does not exist" in (missing.error or "")
    unknown = await tool(rt).execute(
        {"action": "open", "agent": "Frobnicator", "folder": str(tmp_path), "prompt": "x"},
        None,
    )
    assert not unknown.success and "Unknown coding agent" in (unknown.error or "")


async def test_a_retried_open_is_the_same_thread(society: Society, tmp_path: Path):
    rt = society
    first = await open_thread(rt, tmp_path, "Build the export")
    second = await open_thread(rt, tmp_path, "Build the export")
    assert first == second


async def test_full_access_asks_the_person_first():
    t = CodingThreadTool(SimpleNamespace(), "nova")
    assert t.risk_tier_for_args({"action": "open", "access": "full"}) == "ask"
    assert t.risk_tier_for_args({"action": "open"}) == "monitor"
    assert t.risk_tier_for_args({"action": "read"}) == "safe"


async def test_names_resolve_to_rows_and_models():
    row = match_provider("Claude Code")
    assert row is not None and row.id == "claude-api"
    assert match_provider("codex").id == "openai-codex"
    assert match_model(row, "Opus 5.5") == "claude-opus-5-5"
    assert match_model(row, "some-future-model") == "some-future-model"


async def test_files_refuse_a_folder_that_is_no_project(
    society: Society, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    rt = society
    monkeypatch.setattr(coding_threads, "_in_known_project", lambda folder: False)
    result = await tool(rt).execute(
        {"action": "files", "folder": str(tmp_path), "op": "ls"}, None
    )
    assert not result.success and "Only project folders" in (result.error or "")


async def test_a_late_chat_service_is_still_picked_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service = ScriptedService(AgentChatStore(":memory:"))
    rt = Society(service)
    available: list[ScriptedService] = []
    rt.chat_service = lambda: available[0] if available else None
    real_sleep = asyncio.sleep
    monkeypatch.setattr(coding_threads.asyncio, "sleep", lambda _s: real_sleep(0.01))
    await rt.coding_threads.start()
    assert rt.coding_threads._service is None
    available.append(service)
    for _ in range(100):
        await real_sleep(0.01)
        if rt.coding_threads._service is service:
            break
    assert rt.coding_threads._service is service
    await rt.coding_threads.close()
