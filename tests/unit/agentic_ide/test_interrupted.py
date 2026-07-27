"""Panes a restart left standing still, and the nudge that starts them again.

Resuming a workspace reconnects each pane to its conversation and stops there: a
coding CLI launched on an old transcript reads it and waits at its prompt. So
twelve panes come back holding twelve half-finished jobs and none of them move,
which on screen is indistinguishable from twelve panes that finished.

The tests below drive the REAL attach path rather than setting the flag by hand,
because the flag is not the feature — where it is raised and what clears it is.
Getting either edge wrong is a live failure: a missed pane sits dead forever, and
a false one offers to type "continue" behind a prompt the user is still writing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import interrupted
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from jarvis.agentic_ide.session import Registry
from jarvis.ui.web import agentic_ide_routes
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    # The submit verification watches the screen; keep its windows short but
    # non-zero, so the fake's repaint still gets a turn on the event loop.
    monkeypatch.setattr(session_mod, "_SUBMIT_POLL_S", 0.01)
    monkeypatch.setattr(session_mod, "_SUBMIT_WINDOW_S", 0.04)
    monkeypatch.setattr(session_mod, "_SUBMIT_RETRY_AFTER_S", 0.02)
    monkeypatch.setattr(session_mod, "_ARRIVAL_POLL_S", 0.01)
    monkeypatch.setattr(session_mod, "_ARRIVAL_WINDOW_S", 0.04)
    return Registry(pty_manager=fake_pty)


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _restarted_pane(
    registry: Registry,
    folder: Path,
    existing_conversation: Any,
    *,
    name: str = "Alex",
    conversation: str = "conv-1",
):
    """A pane whose agent was spawned onto a conversation that already exists.

    Exactly what a resumed workspace produces: the handle survives in the restore
    point, the CLI's own history really holds the conversation, and attaching
    launches the agent with ``--resume``.
    """
    session = await registry.start(str(folder), [{"agent": "claude", "name": name}])
    existing_conversation(conversation)
    session.terminals[0].resume = ResumeHandle(
        kind="claude_session", id=conversation, captured_at=1.0
    )
    term = await registry.attach(name, 100, 30, _noop, _noop_exit)
    assert term.resumed is True, "the fixture must reproduce a real resume"
    return session, term


# ------------------------------------------------------------------ the scan
async def test_a_resumed_pane_is_waiting_to_be_continued(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """It holds the whole conversation and nobody has told it anything since."""
    session, term = await _restarted_pane(registry, tmp_path, existing_conversation)

    waiting = interrupted.scan(registry)

    assert [pane.name for pane in waiting] == [term.name]
    assert waiting[0].continuable is True
    assert waiting[0].blocked_reason == ""
    assert waiting[0].workspace_id == session.id
    assert waiting[0].folder == session.folder


async def test_a_pane_that_started_fresh_is_not_waiting(
    registry: Registry, tmp_path: Path
) -> None:
    """No conversation was continued, so there is nothing to carry on with."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Alex"}])
    await registry.attach("Alex", 100, 30, _noop, _noop_exit)

    assert interrupted.scan(registry) == []


async def test_a_pane_re_joining_a_running_agent_is_not_waiting(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """Switching workspaces is not an interruption — that agent never stopped.

    The common case once several workspaces are open, and the one a naive
    "was it resumed?" test would report wrongly on every tab switch.
    """
    await _restarted_pane(registry, tmp_path, existing_conversation)
    await interrupted.continue_panes(registry)
    assert interrupted.scan(registry) == []

    registry.detach("alex")
    await registry.attach("Alex", 100, 30, _noop, _noop_exit)

    assert interrupted.scan(registry) == [], "re-joining a live agent restarts nothing"


async def test_a_prompt_takes_a_pane_off_the_list(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """Somebody is driving it again — whatever the prompt happened to say."""
    _session, term = await _restarted_pane(registry, tmp_path, existing_conversation)

    await registry.send_prompt(term.name, "actually, do the other thing")

    assert interrupted.scan(registry) == []


async def test_typing_into_the_pane_yourself_takes_it_off_the_list(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """A line the user submitted by hand is an instruction too."""
    _session, term = await _restarted_pane(registry, tmp_path, existing_conversation)

    registry.write(term.key, "keep going")
    assert len(interrupted.scan(registry)) == 1, "half a typed line is not an instruction"

    registry.write(term.key, "\r")
    assert interrupted.scan(registry) == []


async def test_a_dead_pane_is_listed_but_cannot_be_continued(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """Still interrupted work — it just cannot be typed into until it runs again."""
    _session, term = await _restarted_pane(registry, tmp_path, existing_conversation)
    term.status = "exited"
    term.exit_code = 1
    term.pty_id = None

    waiting = interrupted.scan(registry)

    assert len(waiting) == 1
    assert waiting[0].continuable is False
    assert "restart" in waiting[0].blocked_reason.lower()


async def test_panes_from_every_open_workspace_are_reported(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """A restart interrupts every workspace at once, not just the front one."""
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    session_a, _ = await _restarted_pane(
        registry, first, existing_conversation, name="Alex", conversation="conv-a"
    )
    session_b, _ = await _restarted_pane(
        registry, second, existing_conversation, name="Blake", conversation="conv-b"
    )

    waiting = interrupted.scan(registry)

    assert {pane.workspace_id for pane in waiting} == {session_a.id, session_b.id}
    assert {pane.name for pane in waiting} == {"Alex", "Blake"}


# -------------------------------------------------------------- the continue
async def test_continue_sends_the_word_and_clears_the_list(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path, existing_conversation: Any
) -> None:
    fake_pty.tui_echo = True  # a pane that draws what it is given
    _session, term = await _restarted_pane(registry, tmp_path, existing_conversation)

    report = await interrupted.continue_panes(registry)

    assert fake_pty.typed[0] == interrupted.CONTINUE_PROMPT
    assert report.continued == [term.name]
    assert report.ok is True
    assert interrupted.scan(registry) == []


async def test_a_caller_may_send_its_own_wording(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path, existing_conversation: Any
) -> None:
    fake_pty.tui_echo = True
    await _restarted_pane(registry, tmp_path, existing_conversation)

    await interrupted.continue_panes(registry, prompt="carry on where you left off")

    assert fake_pty.typed[0] == "carry on where you left off"


async def test_a_pane_that_never_submitted_is_reported_separately(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path, existing_conversation: Any
) -> None:
    """Typed in but not provably sent is its own answer, never "it is running"."""
    _session, term = await _restarted_pane(registry, tmp_path, existing_conversation)
    # Make the screen look like the input line kept the text.
    on_output = fake_pty.spawns[-1]["on_output"]
    await on_output("pty", f"\x1b[2J\x1b[H❯ {interrupted.CONTINUE_PROMPT}\r\n")

    report = await interrupted.continue_panes(registry)

    assert report.continued == []
    assert report.unconfirmed == [term.name]


async def test_only_the_named_panes_are_continued(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path, existing_conversation: Any
) -> None:
    fake_pty.tui_echo = True
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    await _restarted_pane(registry, first, existing_conversation, name="Alex", conversation="c-a")
    await _restarted_pane(registry, second, existing_conversation, name="Blake", conversation="c-b")

    report = await interrupted.continue_panes(registry, names=["Blake"])

    assert report.continued == ["Blake"]
    assert [pane.name for pane in interrupted.scan(registry)] == ["Alex"]


async def test_an_unknown_name_is_refused_rather_than_ignored(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """A caller told "four panes are running" when three are has been misled."""
    await _restarted_pane(registry, tmp_path, existing_conversation)

    report = await interrupted.continue_panes(registry, names=["Nobody"])

    assert report.continued == []
    assert [name for name, _ in report.failed] == ["Nobody"]


async def test_a_dead_pane_fails_with_its_reason(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    _session, term = await _restarted_pane(registry, tmp_path, existing_conversation)
    term.status = "error"
    term.error = "claude is not on PATH."
    term.pty_id = None

    report = await interrupted.continue_panes(registry)

    assert report.continued == []
    assert report.failed == [(term.name, "claude is not on PATH.")]


# ----------------------------------------------------------------- the routes
# The CLI-first contract: everything the button does is an endpoint, so the same
# action is reachable from `jarvis api agentic-ide ...` and from a coding agent.


@pytest.fixture
def api(registry: Registry, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The router with the fake-PTY registry behind it, instead of the real one."""
    from fastapi import FastAPI

    monkeypatch.setattr(session_mod, "_REGISTRY", registry)
    app = FastAPI()
    app.include_router(agentic_ide_routes.router)
    return app


def _client(app: Any) -> Any:
    import httpx

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_the_route_answers_empty_with_nothing_open(api: Any) -> None:
    """A fresh app has nothing to continue — an empty answer, never an error."""
    async with _client(api) as client:
        body = (await client.get("/api/agentic-ide/interrupted")).json()

    assert body["count"] == 0
    assert body["panes"] == []
    assert body["prompt"] == interrupted.CONTINUE_PROMPT


async def test_continuing_with_nothing_open_is_a_conflict(api: Any) -> None:
    async with _client(api) as client:
        refused = await client.post("/api/agentic-ide/interrupted/continue", json={})

    assert refused.status_code == 409


async def test_the_route_lists_a_waiting_pane_and_continues_it(
    api: Any,
    registry: Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation: Any,
) -> None:
    fake_pty.tui_echo = True
    session, term = await _restarted_pane(registry, tmp_path, existing_conversation)

    async with _client(api) as client:
        listed = (await client.get("/api/agentic-ide/interrupted")).json()
        continued = (
            await client.post("/api/agentic-ide/interrupted/continue", json={})
        ).json()

    assert listed["count"] == 1
    assert listed["continuable_count"] == 1
    assert listed["panes"][0]["name"] == term.name
    assert listed["panes"][0]["workspace_id"] == session.id

    assert continued["ok"] is True
    assert continued["continued"] == [term.name]
    assert continued["remaining"] == 0
