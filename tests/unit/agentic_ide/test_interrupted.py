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

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import (
    activity,
    fleet_actions,
    interrupted,
    notifications,
    resume_store,
)
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from jarvis.agentic_ide.session import Registry
from jarvis.ui.web import agentic_ide_routes
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _clean_watcher() -> Any:
    """No pane memory carried in from another test, and none left behind."""
    notifications.reset()
    yield
    notifications.reset()


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
    monkeypatch.setattr(fleet_actions, "READY_POLL_S", 0.01)
    monkeypatch.setattr(fleet_actions, "READY_TIMEOUT_S", 0.12)
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
    continuation_needed: bool = True,
    agent: str = "claude",
):
    """A pane whose agent was spawned onto a conversation that already exists.

    Exactly what a resumed workspace produces: the handle survives in the restore
    point, the CLI's own history really holds the conversation, and attaching
    launches the agent with ``--resume``.
    """
    session = await registry.start(str(folder), [{"agent": agent, "name": name}])
    existing_conversation(conversation, agent=agent)
    kind = "codex_rollout" if agent == "codex" else "claude_session"
    session.terminals[0].resume = ResumeHandle(
        kind=kind, id=conversation, captured_at=0.0 if agent == "codex" else 1.0
    )
    session.terminals[0].resume_continuation_needed = continuation_needed
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


async def test_a_finished_conversation_is_not_called_interrupted(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """Conversation history is not evidence that its last turn was cut off."""
    await _restarted_pane(
        registry,
        tmp_path,
        existing_conversation,
        continuation_needed=False,
    )

    assert interrupted.scan(registry) == []


async def test_delayed_output_does_not_hide_a_resumed_pane_from_continue(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """A settled restore still needs Continue until this process gets a task."""
    _session, term = await _restarted_pane(registry, tmp_path, existing_conversation)
    term.idle_seen = True
    term.last_output_at = time.time()

    found = interrupted.scan(registry)

    assert [pane.name for pane in found] == [term.name]
    assert term.reading().activity == "waiting"
    assert term.continuation_pending is True


async def test_resumed_startup_output_without_a_submission_is_still_offered(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """Startup repainting has no current-process submission, so it is not work.

    A restored CLI redraws its banner and its old transcript for several seconds
    before it settles at a prompt. Treating that movement as work filtered every
    restored pane out of this list in exactly the seconds somebody pressed the
    button. The absent generation-stamped submission keeps the offer intact.
    """
    _session, term = await _restarted_pane(registry, tmp_path, existing_conversation)
    assert term.idle_seen is False, "a freshly spawned process has settled nothing"
    term.last_output_at = time.time()

    assert [pane.name for pane in interrupted.scan(registry)] == [term.name]


async def test_a_resumed_pane_asking_a_question_is_not_offered_continue(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """A question needs an answer, not a blind "continue".

    The one place content is still consulted, and only ever to make a settled
    pane MORE specific — so this one does belong on the screen.
    """
    _session, term = await _restarted_pane(registry, tmp_path, existing_conversation)
    term.transcript.clear()
    term.transcript.feed("\r\nDo you want to continue?\r\n❯ 1. Yes\r\n")

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
    # That write is what a pane RECEIVING this screen does, and it stamps the
    # pane as having just moved. The state under test is the one after it came
    # to rest: the text is sitting on the input line and nothing else happens.
    term.last_output_at = time.time() - activity.STILL_S - 1

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


# ------------------------------------------------------ pressing twice, big grids
# Three live complaints, one test group. All of them are about a workspace with
# more than a couple of panes, which is the only size where any of them shows up.


async def test_pressing_continue_twice_sends_it_once(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path, existing_conversation: Any
) -> None:
    """A second press while the first is in flight must not repeat the word.

    Delivering a prompt takes seconds — the submit is verified against the pane's
    own screen — and the pane used to stay on the waiting list for all of it. So
    two presses put "continue" into the agent twice and three put it in three
    times, which is what the user saw.
    """
    fake_pty.tui_echo = True
    _session, term = await _restarted_pane(registry, tmp_path, existing_conversation)

    first, second = await asyncio.gather(
        interrupted.continue_panes(registry),
        interrupted.continue_panes(registry),
    )

    assert fake_pty.typed.count(interrupted.CONTINUE_PROMPT) == 1, fake_pty.typed
    assert sorted([*first.continued, *second.continued]) == [term.name]
    # The press that lost the race says nothing happened rather than claiming it
    # continued a pane it never touched.
    assert not (first.failed or second.failed)


async def test_a_pane_still_starting_is_continued_when_it_comes_up(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path, existing_conversation: Any
) -> None:
    """The big-workspace bug: cold starts are staggered, so most panes are `pending`.

    Pressing Continue in that window used to reach only the handful that had
    already started — reported as "some terminals are skipped". The instruction
    is now held and delivered when the pane's agent appears.
    """
    fake_pty.tui_echo = True
    session = await registry.start(
        str(tmp_path), [{"agent": "claude", "name": "Alex"}, {"agent": "claude", "name": "Blake"}]
    )
    existing_conversation("conv-late")
    late = session.terminals[1]
    late.resume = ResumeHandle(kind="claude_session", id="conv-late", captured_at=1.0)
    late.resume_continuation_needed = True
    late.continuation_pending = True  # what a restore establishes before any spawn

    report = await interrupted.continue_panes(registry)

    assert report.queued == ["Blake"], "a pane that has not started yet is queued, not skipped"
    assert report.continued == []
    assert late.continue_when_ready is True

    # Now the grid gets round to it — the pane connects and its agent starts.
    await registry.attach("Blake", 100, 30, _noop, _noop_exit)
    await _settle(session)

    assert fake_pty.typed.count(interrupted.CONTINUE_PROMPT) == 1, fake_pty.typed
    assert late.continue_when_ready is False
    assert interrupted.scan(registry) == []


async def test_continue_queues_a_live_codex_until_its_input_line_appears(
    registry: Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pending claim survives spawn and the whole live-but-booting phase."""
    monkeypatch.setattr(fleet_actions, "READY_TIMEOUT_S", 1.0)
    fake_pty.tui_echo = True
    session = await registry.start(
        str(tmp_path), [{"agent": "codex", "name": "Cody"}]
    )
    existing_conversation("conv-codex", agent="codex")
    term = session.terminals[0]
    term.resume = ResumeHandle(kind="codex_rollout", id="conv-codex", captured_at=0.0)
    term.resume_continuation_needed = True
    term.continuation_pending = True

    report = await interrupted.continue_panes(registry)

    assert report.queued == ["Cody"]
    assert report.continued == []
    assert fake_pty.typed == []

    mounting = asyncio.create_task(
        registry.attach("Cody", 100, 30, _noop, _noop_exit)
    )
    for _ in range(50):
        if term.status == "live":
            break
        await asyncio.sleep(0.01)
    assert term.status == "live"
    second_press = await interrupted.continue_panes(registry)
    assert second_press.to_dict() == {
        "ok": True,
        "continued": [],
        "queued": [],
        "unconfirmed": [],
        "failed": [],
    }

    await fake_pty.emit(
        term.pty_id,
        "\x1b[2J\x1b[H\u203a Ask Codex anything\x1b[1;3H\x1b[?25h",
    )
    await asyncio.wait_for(mounting, timeout=1.0)
    await _settle(session)

    assert fake_pty.typed.count(interrupted.CONTINUE_PROMPT) == 1
    assert term.submitted is True
    assert interrupted.scan(registry) == []


async def test_a_restored_pane_is_listed_before_its_agent_starts(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """A restore knows what has work to continue — it does not need the spawn.

    This is the other half of the same bug: the list itself used to fill up only
    as the panes started, so a user who pressed the button early was shown three
    of twelve and told that was all of them.
    """
    existing_conversation("conv-restored")
    existing_conversation("conv-finished")
    resume_store.save(
        resume_store.Snapshot(
            saved_at=100.0,
            workspaces=[
                resume_store.SnapshotWorkspace(
                    session_id="ide_old",
                    folder=str(tmp_path),
                    terminals=[
                        resume_store.SnapshotTerminal(
                            key="alex",
                            name="Alex",
                            agent="claude",
                            resume=ResumeHandle(
                                kind="claude_session", id="conv-restored", captured_at=1.0
                            ),
                            prompts_sent=2,
                            continuation_needed=True,
                        ),
                        # No handle at all: nothing to continue, so it must NOT
                        # be offered — that would be a promise with nothing
                        # behind it.
                        resume_store.SnapshotTerminal(key="blake", name="Blake", agent="claude"),
                        # A valid history whose previous turn was already
                        # settled: resumable, but not interrupted.
                        resume_store.SnapshotTerminal(
                            key="casey",
                            name="Casey",
                            agent="claude",
                            resume=ResumeHandle(
                                kind="claude_session", id="conv-finished", captured_at=1.0
                            ),
                            prompts_sent=1,
                            continuation_needed=False,
                        ),
                    ],
                )
            ],
        )
    )

    await registry.restore(resume_store.load())

    waiting = interrupted.scan(registry)
    assert [pane.name for pane in waiting] == ["Alex"]
    assert waiting[0].status == "pending", "listed before anything spawned"
    assert waiting[0].continuable is True
    assert waiting[0].starting is True


async def test_a_restored_pane_survives_its_own_start_up_burst(
    registry: Registry, tmp_path: Path, existing_conversation: Any
) -> None:
    """The whole chain, end to end, through the sweep that used to break it.

    A restored CLI repaints its banner, its model line and its old transcript
    for several seconds before it settles — and the detector under all of this
    reads MOVEMENT, so that burst is frame-for-frame an agent at work. Both
    readers drew that conclusion: the notification sweep retracted the pane's
    `continuation_pending` and the scan filtered it out, within two sweeps of
    every resumed pane coming back. The dialog then said "nothing was
    interrupted" over a grid of panes sitting at their prompts, for ever.

    So the burst is driven here for real, on both clocks the two readers use.
    """
    existing_conversation("conv-restored")
    resume_store.save(
        resume_store.Snapshot(
            saved_at=100.0,
            workspaces=[
                resume_store.SnapshotWorkspace(
                    session_id="ide_old",
                    folder=str(tmp_path),
                    terminals=[
                        resume_store.SnapshotTerminal(
                            key="alex",
                            name="Alex",
                            agent="claude",
                            resume=ResumeHandle(
                                kind="claude_session", id="conv-restored", captured_at=1.0
                            ),
                            prompts_sent=2,
                            continuation_needed=True,
                        )
                    ],
                )
            ],
        )
    )
    await registry.restore(resume_store.load())
    term = await registry.attach("Alex", 100, 30, _noop, _noop_exit)
    assert term.resumed is True, "the fixture must reproduce a real resume"

    watcher = notifications.watcher()
    for frame in range(3):
        term.transcript.clear()
        term.transcript.feed(f"\r\n  replaying the old conversation, frame {frame}\r\n")
        # The scan reads the wall clock through the byte fallback, the sweep
        # reads its own timeline through the screen digest. Both have to see a
        # pane that is busy drawing itself.
        term.last_output_at = time.time()
        watcher.poll(registry, now=100.0 + frame * notifications.SWEEP_INTERVAL_S, emit=False)

    assert term.continuation_pending is True, "drawing itself is not carrying on"
    assert [pane.name for pane in interrupted.scan(registry)] == ["Alex"]


async def _settle(session: Any) -> None:
    """Wait only for this session's deferred work, never the whole test loop."""
    pending = tuple(session.lookups)
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending), timeout=2.0)


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
