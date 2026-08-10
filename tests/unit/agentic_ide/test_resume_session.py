"""Resuming a workspace: the registry side.

The contract these pin down is that ``attach`` is the ONLY place an agent is
started, and therefore the only place that has to know about continuing a
conversation. Every way a pane can come back — reopening the browser, restoring
a snapshot, restarting a dead pane — goes through it, so all three behave the
same and none of them can drift.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.agentic_ide import resume_store
from jarvis.agentic_ide import session as ide
from jarvis.agentic_ide.agent_sessions import ResumeHandle
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> ide.Registry:
    monkeypatch.setattr(ide, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return ide.Registry(pty_manager=fake_pty)


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


def _argv(fake_pty: FakePtyManager) -> tuple[str, ...]:
    return fake_pty.spawns[-1]["argv"]


# ------------------------------------------------------------------ minting
async def test_a_fresh_pane_is_launched_with_an_id_it_can_be_found_by(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)

    term = registry.session.find("T1")
    assert term.resume is not None and term.resume.kind == "claude_session"
    # The id went to the CLI, which is what makes the conversation findable.
    argv = _argv(fake_pty)
    assert "--session-id" in argv and term.resume.id in argv
    # A first start is not a resume, and must not be reported as one.
    assert term.resumed is False


async def test_a_pane_with_a_handle_continues_instead_of_starting_over(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    term = registry.session.find("T1")
    term.resume = ResumeHandle(kind="claude_session", id="known-id", captured_at=1.0)
    existing_conversation("known-id")

    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    assert _argv(fake_pty)[-2:] == ("--resume", "known-id")
    assert registry.session.find("T1").resumed is True


async def test_reopening_a_pane_keeps_the_same_conversation(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    """After the agent really died, the pane comes back with its conversation."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    minted = registry.session.find("T1").resume
    assert minted is not None
    # The pane was used, so the CLI now has a conversation under that id.
    existing_conversation(minted.id)

    # The agent is gone — quit from inside, machine restarted, process killed.
    # THAT is what makes the next attach a restart rather than a re-join.
    await fake_pty.die(registry.session.find("T1").pty_id, 0)
    await registry.attach("T1", 80, 24, _noop, _noop_exit)

    assert _argv(fake_pty)[-2:] == ("--resume", minted.id)
    assert registry.session.find("T1").resumed is True


async def test_letting_go_of_a_pane_does_not_stop_its_agent(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A viewer leaving is not an instruction to stop working.

    Panes are released constantly — switching workspace, reloading the page,
    walking over to the chat view. An agent runs until its WORKSPACE is closed,
    so none of those may cost the work in progress.
    """
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    pty_id = registry.session.find("T1").pty_id

    registry.detach("T1")

    assert pty_id not in fake_pty.closed, "the agent must still be running"
    assert registry.session.find("T1").status == "live"
    assert registry.session.find("T1").pty_id == pty_id


async def test_a_pane_running_a_cli_that_cannot_resume_just_starts(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A coding CLI added later must degrade, never break the pane."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    term = registry.session.find("T1")
    term.agent = "some-future-cli"

    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    assert _argv(fake_pty) == ("/usr/bin/some-future-cli",)
    assert registry.session.find("T1").resume is None
    assert registry.session.find("T1").resumed is False


async def test_a_handle_with_no_conversation_behind_it_starts_fresh(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The failure that took down twelve real panes at once.

    Being handed an id at launch does not create a conversation — the CLI files
    one only when it has content. So a pane that was opened and never given an
    instruction holds an id that points at nothing, and asking the CLI to resume
    it makes it print "no conversation found" and exit. Every one of twelve
    panes came back dead that way.

    The pointer has to be dereferenced before it is spent.
    """
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    term = registry.session.find("T1")
    term.resume = ResumeHandle(kind="claude_session", id="never-written", captured_at=1.0)
    # Deliberately no conversation on disk.

    await registry.attach("T1", 80, 24, _noop, _noop_exit)

    argv = _argv(fake_pty)
    assert "--resume" not in argv, "an id pointing at nothing must not be spent"
    assert "--session-id" in argv, "and the fresh start gets a usable id of its own"
    term = registry.session.find("T1")
    assert term.resumed is False
    assert term.status == "live", "the pane must come up, not die"
    assert term.resume is not None and term.resume.id != "never-written"


async def test_the_offer_does_not_promise_a_conversation_that_is_not_there(
    tmp_path: Path,
) -> None:
    """What the card would have claimed: twelve conversations, all empty."""
    snapshot = resume_store.Snapshot(
        saved_at=1.0,
        workspaces=[
            resume_store.SnapshotWorkspace(
                session_id="ide_old",
                folder=str(tmp_path),
                terminals=[
                    resume_store.SnapshotTerminal(
                        key="t1",
                        name="T1",
                        agent="claude",
                        resume=ResumeHandle(
                            kind="claude_session", id="never-written", captured_at=1.0
                        ),
                    )
                ],
            )
        ],
    )
    view = resume_store.offer(snapshot, installed={"claude"})
    panes = view["workspaces"][0]["terminals"]
    assert panes[0]["available"] is True  # the pane comes back
    assert panes[0]["resumable"] is False  # the conversation does not
    assert view["resumable_count"] == 0


# ------------------------------------------------------------- self-healing
async def test_a_dead_conversation_falls_back_to_a_fresh_agent(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    """The backstop: a conversation that looks present but the CLI rejects."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    term = registry.session.find("T1")
    term.resume = ResumeHandle(kind="claude_session", id="stale", captured_at=1.0)
    existing_conversation("stale")

    exits: list[int] = []

    async def _record_exit(code: int) -> None:
        exits.append(code)

    await registry.attach("T1", 80, 24, _noop, _record_exit)
    # The CLI printed "no such conversation" and died right away.
    await fake_pty.spawns[-1]["on_closed"]("fake-pty-1", 1)

    argv = _argv(fake_pty)
    assert "--resume" not in argv, "a stale handle must not be spent twice"
    assert registry.session.find("T1").resumed is False
    assert registry.session.find("T1").status == "live"
    # The viewer was never told the pane died — it did not, it restarted.
    assert exits == []


async def test_a_clean_exit_after_a_resume_is_not_second_guessed(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    """Quitting an agent on purpose exits 0 — restarting it would be a bug."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    term = registry.session.find("T1")
    term.resume = ResumeHandle(kind="claude_session", id="fine", captured_at=1.0)
    existing_conversation("fine")

    exits: list[int] = []

    async def _record_exit(code: int) -> None:
        exits.append(code)

    await registry.attach("T1", 80, 24, _noop, _record_exit)
    await fake_pty.spawns[-1]["on_closed"]("fake-pty-1", 0)

    assert exits == [0]
    assert registry.session.find("T1").status == "exited"


async def test_closing_a_resumed_pane_does_not_resurrect_it(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    """The trap the self-healing walks into if it is not told about the kill.

    Closing a pane kills its agent, and a killed process reports a failure exit
    that looks exactly like a crashed resume. Without knowing the kill was
    deliberate, the recovery would restart an agent the user had just closed —
    and it would then run on with nobody watching, which is precisely what
    closing it prevents.
    """
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    registry.session.find("T1").resume = ResumeHandle(
        kind="claude_session", id="fine", captured_at=1.0
    )
    existing_conversation("fine")
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    assert registry.session.find("T1").resumed is True
    spawns_before = len(fake_pty.spawns)

    # The user closed the pane a second after it came back, and the kill is
    # reported by the PTY as a failure exit.
    await registry.close_terminal("T1")
    await fake_pty.spawns[-1]["on_closed"]("fake-pty-1", 1)

    assert len(fake_pty.spawns) == spawns_before, "the agent must stay stopped"
    assert registry.session.find("T1") is None, "and its pane must be gone"


async def test_closing_the_workspace_does_not_resurrect_a_resumed_pane(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    """Same trap, reached by closing the whole workspace instead of one pane."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    registry.session.find("T1").resume = ResumeHandle(
        kind="claude_session", id="fine", captured_at=1.0
    )
    existing_conversation("fine")
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    term = registry.session.find("T1")
    spawns_before = len(fake_pty.spawns)

    await registry.end()
    await fake_pty.spawns[-1]["on_closed"]("fake-pty-1", 1)

    assert len(fake_pty.spawns) == spawns_before, "the agent must stay stopped"
    assert term.status != "live"


async def test_a_late_crash_is_reported_as_a_crash(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_conversation,
) -> None:
    """Past the window an exit is just an exit; a restart loop would be worse."""
    monkeypatch.setattr(ide, "RESUME_FAILED_WINDOW_S", 0.0)
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    registry.session.find("T1").resume = ResumeHandle(
        kind="claude_session", id="fine", captured_at=1.0
    )
    existing_conversation("fine")

    exits: list[int] = []

    async def _record_exit(code: int) -> None:
        exits.append(code)

    await registry.attach("T1", 80, 24, _noop, _record_exit)
    await fake_pty.spawns[-1]["on_closed"]("fake-pty-1", 3)

    assert exits == [3]
    assert registry.session.find("T1").status == "exited"


# ------------------------------------------------------------------ restore
def _snapshot(*folders: Path) -> resume_store.Snapshot:
    """A restore point holding one workspace per folder."""
    return resume_store.Snapshot(
        saved_at=1.0,
        workspaces=[_workspace(f) for f in folders],
    )


def _workspace(folder: Path) -> resume_store.SnapshotWorkspace:
    return resume_store.SnapshotWorkspace(
        session_id="ide_old",
        folder=str(folder),
        terminals=[
            resume_store.SnapshotTerminal(
                key="t4",
                name="T4",
                agent="claude",
                column=1,
                slot=1,
                resume=ResumeHandle(kind="claude_session", id="t4-conv", captured_at=1.0),
                prompts_sent=2,
            ),
            resume_store.SnapshotTerminal(
                key="t1", name="T1", agent="claude", column=0, slot=0
            ),
        ],
    )


async def test_restore_rebuilds_titles_agents_and_positions(
    registry: ide.Registry, tmp_path: Path
) -> None:
    restored = (await registry.restore(_snapshot(tmp_path))).sessions[0]

    # Reading order, not snapshot order: left to right, top to bottom.
    assert [t.name for t in restored.terminals] == ["T1", "T4"]
    assert [(t.column, t.slot) for t in restored.terminals] == [(0, 0), (1, 0)]
    assert [t.agent for t in restored.terminals] == ["claude", "claude"]
    assert restored.folder == str(tmp_path)
    assert restored.find("T4").resume.id == "t4-conv"
    assert restored.find("T4").prompts_sent == 2


async def test_restore_starts_nothing_by_itself(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The grid attaches its panes as it always does — one spawn path only."""
    restored = (await registry.restore(_snapshot(tmp_path))).sessions[0]
    assert all(t.status == "pending" for t in restored.terminals)
    assert fake_pty.spawns == []


async def test_a_restored_pane_continues_its_conversation_when_it_connects(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    existing_conversation("t4-conv")
    await registry.restore(_snapshot(tmp_path))
    await registry.attach("T4", 80, 24, _noop, _noop_exit)
    assert _argv(fake_pty)[-2:] == ("--resume", "t4-conv")

    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    # T1 never had one, so it starts fresh — and says so.
    assert "--resume" not in _argv(fake_pty)
    assert registry.session.find("T1").resumed is False


async def test_restore_refuses_a_folder_that_is_gone(
    registry: ide.Registry, tmp_path: Path
) -> None:
    with pytest.raises(ide.SessionError, match="no longer"):
        await registry.restore(_snapshot(tmp_path / "deleted"))


async def test_restore_refuses_an_empty_workspace(registry: ide.Registry, tmp_path: Path) -> None:
    empty = resume_store.Snapshot(saved_at=1.0, workspaces=[])
    with pytest.raises(ide.SessionError):
        await registry.restore(empty)


async def test_restoring_a_folder_that_is_still_open_adds_the_remembered_workspace(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Two remembered pane groups may intentionally share one folder."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Old"}])
    await registry.attach("Old", 80, 24, _noop, _noop_exit)
    live = registry.session.find("Old").pty_id

    restored = (await registry.restore(_snapshot(tmp_path))).sessions[0]

    assert live not in fake_pty.closed, "the running agent must survive"
    assert [t.name for t in restored.terminals] == ["T1", "T4"]
    assert len(registry.sessions) == 2


async def test_a_folder_from_an_earlier_session_is_not_reopened_beside_todays(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """The reported bug, end to end: yesterday's folder must stay put.

    The store remembers a folder you closed days ago on purpose, so opening one
    new workspace cannot erase it. Reopening that archive wholesale is what made
    a restart come back with old folders beside the current one — and, because
    every workspace draws call-signs from the same pool, with a screen full of
    "T1" and "T1 2".
    """
    old, today = tmp_path / "last-week", tmp_path / "today"
    old.mkdir()
    today.mkdir()
    await registry.start(str(old), [{"agent": "claude"}, {"agent": "claude"}])
    await registry.end()
    await registry.start(str(today), [{"agent": "claude"}])

    stored = resume_store.load()
    assert stored is not None
    # Both are still remembered — the offer keeps them.
    assert len(stored.workspaces) == 2

    fresh = ide.Registry(pty_manager=FakePtyManager())
    result = await fresh.restore(stored)

    assert [s.folder for s in result.sessions] == [str(today)]
    assert [t.name for s in result.sessions for t in s.terminals] == ["T1"]


async def test_workspaces_closed_one_by_one_still_come_back_together(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Closing for the day is not "an earlier session" — all of it comes back."""
    folders = [tmp_path / "one", tmp_path / "two", tmp_path / "three"]
    for folder in folders:
        folder.mkdir()
        await registry.start(str(folder), [{"agent": "claude"}])
    while await registry.end():
        pass

    stored = resume_store.load()
    assert stored is not None
    fresh = ide.Registry(pty_manager=FakePtyManager())
    result = await fresh.restore(stored)

    assert {s.folder for s in result.sessions} == {str(f) for f in folders}


async def test_restoring_the_same_restore_point_twice_opens_nothing_new(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """A stale offer card in a second window must not duplicate the workspace."""
    await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "claude"}])

    stored = resume_store.load()
    assert stored is not None
    fresh = ide.Registry(pty_manager=FakePtyManager())
    await fresh.restore(stored)
    names = [t.name for t in fresh.sessions[0].terminals]

    # Once more, with the file as it stands after the first restore rewrote it.
    with pytest.raises(ide.SessionError, match="already open"):
        await fresh.restore(resume_store.load())
    # And once more with the file as the second window still remembers it.
    with pytest.raises(ide.SessionError, match="already open"):
        await fresh.restore(stored)

    assert len(fresh.sessions) == 1
    assert [t.name for t in fresh.sessions[0].terminals] == names


async def test_restoring_another_folder_opens_it_beside_the_running_one(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Old"}])
    await registry.attach("Old", 80, 24, _noop, _noop_exit)
    live = registry.session.find("Old").pty_id

    await registry.restore(_snapshot(other))

    assert live not in fake_pty.closed, "the first workspace must keep running"
    assert [t.name for t in registry.session.terminals] == ["T1", "T4"]
    assert len(registry.sessions) == 2


# ---------------------------------------------------------------- snapshots
async def test_opening_a_workspace_makes_it_resumable(
    registry: ide.Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    saved = resume_store.load()
    assert saved is not None
    assert [t.name for t in saved.workspaces[0].terminals] == ["T1"]
    assert saved.workspaces[0].folder == str(tmp_path)


async def test_the_conversation_id_reaches_the_snapshot(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Without this the layout would come back and the conversations would not."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.attach("T1", 80, 24, _noop, _noop_exit)

    saved = resume_store.load()
    assert saved is not None and saved.workspaces[0].terminals[0].resume is not None
    assert saved.workspaces[0].terminals[0].resume.id == registry.session.find("T1").resume.id


async def test_splitting_and_closing_keep_the_offer_current(
    registry: ide.Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.add_terminal(anchor="T1", direction="right", name="T2")
    saved = resume_store.load()
    assert saved is not None and [t.name for t in saved.workspaces[0].terminals] == [
        "T1",
        "T2",
    ]

    await registry.close_terminal("T2")
    saved = resume_store.load()
    assert saved is not None and [t.name for t in saved.workspaces[0].terminals] == ["T1"]


async def test_closing_the_workspace_keeps_it_resumable(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Closing for the day and picking it up tomorrow is the point of this.

    An earlier version withdrew the offer on an explicit close, reasoning that
    re-proposing something somebody just shut down is noise. The maintainer
    reported the opposite: they close the workspace, come back, and want it back.
    Only asking to start fresh discards a restore point.
    """
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    assert resume_store.load() is not None

    await registry.end()

    saved = resume_store.load()
    assert saved is not None, "closing must not erase what the user wants back"
    assert saved.workspaces[0].folder == str(tmp_path)
    assert [t.name for t in saved.workspaces[0].terminals] == ["T1"]


async def test_closing_every_workspace_still_keeps_them_resumable(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Shutting everything down for the evening is the main case, not an edge."""
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    await registry.start(str(first), [{"agent": "claude", "name": "T1"}])
    await registry.start(str(second), [{"agent": "claude", "name": "T2"}])

    assert await registry.close_all() == 2

    saved = resume_store.load()
    assert saved is not None
    assert {w.folder for w in saved.workspaces} == {str(first), str(second)}


async def test_closing_workspaces_one_by_one_keeps_all_of_them_on_offer(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """The bug that survived the first fix, measured live.

    Closing used to re-write the restore point, so shutting four workspaces down
    one at a time narrowed the offer with every click and left a single workspace
    behind. The restore point is refreshed by activity, never by closing.
    """
    folders = []
    for index in range(3):
        folder = tmp_path / f"repo{index}"
        folder.mkdir()
        folders.append(folder)
        await registry.start(
            str(folder), [{"agent": "claude", "name": f"Pane{index}"}]
        )

    # One at a time, the way a person closes tabs.
    while await registry.end():
        pass

    saved = resume_store.load()
    assert saved is not None
    assert {w.folder for w in saved.workspaces} == {str(f) for f in folders}


async def test_a_new_workspace_does_not_erase_the_folders_you_closed(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """The reported loss, in miniature.

    Work twelve panes in one folder, close them, then open a single pane
    somewhere to check one thing — and the twelve used to be gone for good,
    because a save replaced the file outright. A save UPDATES: the folder that is
    open now overwrites its own record, every other remembered folder is left
    alone.
    """
    big, quick = tmp_path / "big", tmp_path / "quick"
    big.mkdir()
    quick.mkdir()
    await registry.start(
        str(big), [{"agent": "claude"} for _ in range(12)]
    )
    await registry.end()

    await registry.start(str(quick), [{"agent": "claude", "name": "Solo"}])

    saved = resume_store.load()
    assert saved is not None
    folders = {w.folder: len(w.terminals) for w in saved.workspaces}
    assert folders == {str(quick): 1, str(big): 12}


async def test_reopening_the_same_folder_replaces_its_own_record(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """The newest arrangement of a folder is the truth about that folder."""
    await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(5)])
    await registry.end()

    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Solo"}])

    saved = resume_store.load()
    assert saved is not None
    assert [len(w.terminals) for w in saved.workspaces] == [1]


async def test_the_closed_workspace_history_stays_bounded(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """A restore point is a screen, not an archive."""
    for index in range(resume_store.MAX_REMEMBERED_WORKSPACES + 4):
        folder = tmp_path / f"repo{index}"
        folder.mkdir()
        await registry.start(str(folder), [{"agent": "claude"}])
        await registry.end()

    saved = resume_store.load()
    assert saved is not None
    # The last live snapshot is retained alongside the bounded older history.
    assert len(saved.workspaces) == resume_store.MAX_REMEMBERED_WORKSPACES + 1
    # The newest survive; the oldest history falls off.
    assert any("repo13" in w.folder for w in saved.workspaces)
    assert not any(w.folder.endswith("repo0") for w in saved.workspaces)


async def test_open_workspaces_are_never_trimmed_by_the_history_budget(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """The closed-folder archive cap must not become an open-workspace cap."""
    count = resume_store.MAX_REMEMBERED_WORKSPACES + 4
    for index in range(count):
        folder = tmp_path / f"open{index}"
        folder.mkdir()
        await registry.start(str(folder), [{"agent": "claude"}])

    saved = resume_store.load()
    assert saved is not None
    assert len(saved.workspaces) == count


async def test_only_starting_fresh_discards_the_restore_point(
    registry: ide.Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    await registry.end()
    assert resume_store.load() is not None

    assert resume_store.clear() is True
    assert resume_store.load() is None


async def test_every_open_workspace_is_remembered_in_tab_order(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """All of them, arranged as the bar was, with the working tab named.

    An earlier version stored only the front workspace, on the reasoning that
    restoring all would relaunch a folder's worth of agents per tab. Restoring
    starts nothing, so that was wrong on both counts — and somebody with two
    folders open wants two back. The order is the BAR's, because that is the
    arrangement that has to come back; which tab was being worked in is recorded
    separately rather than implied by position.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    one = await registry.start(str(first), [{"agent": "claude", "name": "T1"}])
    two = await registry.start(str(second), [{"agent": "claude", "name": "T2"}])

    saved = resume_store.load()
    assert saved is not None
    assert [w.folder for w in saved.workspaces] == [str(first), str(second)]
    assert saved.active_session_id == two.id

    # Working in the other tab must not renumber the bar.
    await registry.activate(one.id)
    saved = resume_store.load()
    assert saved is not None
    assert [w.folder for w in saved.workspaces] == [str(first), str(second)]
    assert saved.active_session_id == one.id


async def test_closing_one_of_two_leaves_both_on_offer(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Closing must never leave a restore point pointing at nothing.

    It holds both: the survivor because it is still open, and the closed one
    because closing does not rewrite the offer. Reopening one workspace too many
    is trivially undone; losing one is not.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    one = await registry.start(str(first), [{"agent": "claude", "name": "T1"}])
    two = await registry.start(str(second), [{"agent": "claude", "name": "T2"}])

    await registry.end(two.id)

    saved = resume_store.load()
    assert saved is not None
    assert {w.folder for w in saved.workspaces} == {str(first), str(second)}
    assert registry.active_id == one.id


async def test_a_broken_snapshot_write_never_breaks_the_workspace(
    registry: ide.Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_snapshot: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(resume_store, "save", _boom)
    workspace = await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    assert [t.name for t in workspace.terminals] == ["T1"]


# ------------------------------------------------------------------ lookups
async def test_a_cli_that_cannot_be_told_its_id_gets_looked_up(
    registry: ide.Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex writes its session file after launching, so we ask a moment later."""
    monkeypatch.setattr(ide, "DISCOVERY_DELAYS_S", (0.0,))
    found = ResumeHandle(kind="codex_rollout", id="found-it", captured_at=2.0)
    monkeypatch.setattr(ide, "discover", lambda *_args, **_kw: found)

    await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    # Nothing is known at launch — Codex chooses the id, and only afterwards.
    await registry.attach("Cody", 80, 24, _noop, _noop_exit)
    assert "resume" not in " ".join(registry._pty.spawns[-1]["argv"])

    await asyncio.sleep(0.05)
    assert registry.session.find("Cody").resume == found
    saved = resume_store.load()
    assert saved is not None and saved.workspaces[0].terminals[0].resume == found


async def test_a_lookup_offers_the_ids_other_panes_already_hold(
    registry: ide.Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise two Codex panes in one folder would share a conversation."""
    monkeypatch.setattr(ide, "DISCOVERY_DELAYS_S", (0.0,))
    seen: list[set[str]] = []

    def _spy(_agent: str, _cwd: str, _started: float, taken: set[str], _home=None):
        seen.append(set(taken))
        return None

    monkeypatch.setattr(ide, "discover", _spy)
    await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    registry.session.terminals[0].resume = None
    await registry.add_terminal(name="T4", agent="codex")
    registry.session.find("Cody").resume = ResumeHandle(
        kind="codex_rollout", id="cody-conv", captured_at=1.0
    )

    await registry.attach("T4", 80, 24, _noop, _noop_exit)
    await asyncio.sleep(0.05)
    assert seen and "cody-conv" in seen[-1]


async def test_closing_the_workspace_stops_pending_lookups(
    registry: ide.Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lookup outliving its workspace would write a snapshot for a ghost."""
    monkeypatch.setattr(ide, "DISCOVERY_DELAYS_S", (5.0,))
    session = await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    await registry.attach("Cody", 80, 24, _noop, _noop_exit)
    assert session.lookups

    await registry.end()
    assert not session.lookups


async def test_closing_one_workspace_leaves_the_others_lookups_alone(
    registry: ide.Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lookups belong to a workspace, not to the process.

    A shared set would mean closing one tab cancels the pending conversation-id
    discovery of every other one — and those panes would silently lose the only
    thing that lets them be resumed later.
    """
    monkeypatch.setattr(ide, "DISCOVERY_DELAYS_S", (5.0,))
    other = tmp_path / "second"
    other.mkdir()
    keep = await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    await registry.attach("Cody", 80, 24, _noop, _noop_exit)
    close_me = await registry.start(str(other), [{"agent": "codex"}])
    await registry.attach(close_me.terminals[0].name, 80, 24, _noop, _noop_exit)
    assert keep.lookups and close_me.lookups

    await registry.end(close_me.id)

    assert not close_me.lookups
    assert keep.lookups, "the surviving workspace must keep looking"


async def test_a_discovered_conversation_id_is_not_overwritten_by_an_older_save(
    registry: ide.Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race that silently cost a Codex pane its conversation.

    Saving reads the state and then writes it. A pane connecting starts that,
    and the background lookup that finds a Codex conversation id can land its own
    save in the gap — after which the connecting pane's older reading lands on
    top and erases the id. It only ever showed up under a shuffled test order,
    which is exactly how a race announces itself.

    Build-and-write is one step now, so whatever is written last was read last.
    """
    monkeypatch.setattr(ide, "DISCOVERY_DELAYS_S", (0.0,))
    found = ResumeHandle(kind="codex_rollout", id="found-it", captured_at=2.0)
    monkeypatch.setattr(ide, "discover", lambda *_args, **_kw: found)

    await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    # Attach and the lookup both persist; the order they finish in must not
    # decide whether the id survives.
    await registry.attach("Cody", 80, 24, _noop, _noop_exit)
    for _ in range(20):
        await asyncio.sleep(0.01)

    assert registry.session.find("Cody").resume == found
    saved = resume_store.load()
    assert saved is not None
    stored = saved.workspaces[0].terminals[0].resume
    assert stored == found, "the discovered id must survive every other save"


# ------------------------------------------ looking again once there is one
class _LateConversation:
    """A CLI that files its session only once the conversation has a message.

    Which is what Codex, OpenCode and Kimi all do — and the whole reason a
    lookup timed from the pane's LAUNCH kept coming back empty. Measured on a
    real Codex pane: launched 15:17:44, rollout file written 15:19:32, the
    instant its first brief was submitted.
    """

    def __init__(self) -> None:
        self.handle = ResumeHandle(kind="codex_rollout", id="written-late", captured_at=9.0)
        self.spoken_to = False
        self.calls = 0

    def __call__(self, *_args: object, **_kwargs: object) -> ResumeHandle | None:
        self.calls += 1
        return self.handle if self.spoken_to else None


@pytest.fixture
def late_cli(monkeypatch: pytest.MonkeyPatch) -> _LateConversation:
    cli = _LateConversation()
    monkeypatch.setattr(ide, "discover", cli)
    monkeypatch.setattr(ide, "DISCOVERY_DELAYS_S", (0.0,))
    monkeypatch.setattr(ide, "CONVERSATION_DELAYS_S", (0.0,))
    # Both rounds are instant here, so the real cooldown — which exists to keep
    # a leaning-on-Enter user from re-crawling the history — would swallow the
    # very trigger under test. The one test that IS about the cooldown puts a
    # real value back.
    monkeypatch.setattr(ide, "LOOKUP_COOLDOWN_S", 0.0)
    # The prompt path watches the pane's screen; keep those waits short enough
    # that a test is not paced by them.
    monkeypatch.setattr(ide, "_ARRIVAL_POLL_S", 0.01)
    monkeypatch.setattr(ide, "_ARRIVAL_WINDOW_S", 0.04)
    monkeypatch.setattr(ide, "_SUBMIT_POLL_S", 0.01)
    monkeypatch.setattr(ide, "_SUBMIT_WINDOW_S", 0.04)
    monkeypatch.setattr(ide, "_SUBMIT_RETRY_AFTER_S", 0.02)
    return cli


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0.01)


async def test_a_conversation_that_starts_late_is_still_found(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path, late_cli: _LateConversation
) -> None:
    """The bug: a pane prompted after the start window kept no handle at all.

    Nothing is wrong with the search — it is asked at the wrong moment. Launching
    one of these CLIs writes no session, so both attempts after the spawn find
    nothing and the old code then gave up for good. The pane went on to work for
    hours, was snapshotted with ``resume: null``, and came back empty.

    So the search is asked again when the pane's conversation actually BEGINS.
    """
    fake_pty.tui_echo = True
    await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    term = await registry.attach("Cody", 80, 24, _noop, _noop_exit)
    await _settle()
    assert registry.session.find("Cody").resume is None, "nothing exists to find yet"
    assert late_cli.calls, "the pane still looks right after starting"

    # The pane is given its first instruction, which is what makes the CLI write
    # its session file.
    late_cli.spoken_to = True
    await fake_pty.emit(
        term.pty_id,
        "\x1b[2J\x1b[H› Ask Codex anything\x1b[1;3H\x1b[?25h",
    )
    await registry.send_prompt("Cody", "review the pipeline")
    await _settle()

    term = registry.session.find("Cody")
    assert term.resume == late_cli.handle, "the id must be captured once it exists"
    saved = resume_store.load()
    assert saved is not None
    assert saved.workspaces[0].terminals[0].resume == late_cli.handle


async def test_a_line_the_user_typed_themselves_also_starts_the_search(
    registry: ide.Registry, tmp_path: Path, late_cli: _LateConversation
) -> None:
    """A pane driven by hand never goes through ``send_prompt``.

    Most panes are typed into directly at least once, and that keystroke starts
    the conversation exactly like an injected brief does — so it has to be worth
    the same look, or resuming would work only for panes Jarvis drove.
    """
    await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    await registry.attach("Cody", 80, 24, _noop, _noop_exit)
    await _settle()
    assert registry.session.find("Cody").resume is None

    late_cli.spoken_to = True
    registry.write("Cody", "run the tests")  # typing alone is not a conversation
    await _settle()
    assert registry.session.find("Cody").resume is None, "a half-typed line is not a message"

    registry.write("Cody", "\r")
    await _settle()
    assert registry.session.find("Cody").resume == late_cli.handle


async def test_a_pane_does_not_start_a_new_search_for_every_keystroke(
    registry: ide.Registry,
    tmp_path: Path,
    late_cli: _LateConversation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each round opens up to 400 session files; Enter is pressed far more often.

    A pane whose CLI genuinely has nothing to find (offline, uninstalled, a
    conversation the user abandoned) would otherwise crawl its history again for
    every line submitted into it.
    """
    monkeypatch.setattr(ide, "LOOKUP_COOLDOWN_S", 30.0)
    await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    await registry.attach("Cody", 80, 24, _noop, _noop_exit)
    await _settle()
    after_start = late_cli.calls

    for _ in range(5):
        registry.write("Cody", "\r")
        await _settle()

    assert late_cli.calls - after_start <= 1, "the cooldown has to hold the rest back"


async def test_a_pane_that_already_knows_its_conversation_never_looks_again(
    registry: ide.Registry, tmp_path: Path, late_cli: _LateConversation
) -> None:
    """The handle is the answer; asking again could only replace it with another."""
    late_cli.spoken_to = True
    await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    await registry.attach("Cody", 80, 24, _noop, _noop_exit)
    await _settle()
    assert registry.session.find("Cody").resume == late_cli.handle
    settled = late_cli.calls

    registry.write("Cody", "\r")
    await _settle()
    assert late_cli.calls == settled
