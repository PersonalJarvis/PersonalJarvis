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
def registry(
    fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch
) -> ide.Registry:
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
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    await registry.attach("Mika", 80, 24, _noop, _noop_exit)

    term = registry.session.find("Mika")
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
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    term = registry.session.find("Mika")
    term.resume = ResumeHandle(
        kind="claude_session", id="known-id", captured_at=1.0
    )
    existing_conversation("known-id")

    await registry.attach("Mika", 80, 24, _noop, _noop_exit)
    assert _argv(fake_pty)[-2:] == ("--resume", "known-id")
    assert registry.session.find("Mika").resumed is True


async def test_reopening_a_pane_keeps_the_same_conversation(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    """The browser-close case: the pane reconnects and picks up where it was."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    await registry.attach("Mika", 80, 24, _noop, _noop_exit)
    minted = registry.session.find("Mika").resume
    assert minted is not None
    # The pane was used, so the CLI now has a conversation under that id.
    existing_conversation(minted.id)

    registry.detach("Mika")  # the viewer went away, the agent was stopped
    await registry.attach("Mika", 80, 24, _noop, _noop_exit)

    assert _argv(fake_pty)[-2:] == ("--resume", minted.id)
    assert registry.session.find("Mika").resumed is True


async def test_a_pane_running_a_cli_that_cannot_resume_just_starts(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A coding CLI added later must degrade, never break the pane."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    term = registry.session.find("Mika")
    term.agent = "some-future-cli"

    await registry.attach("Mika", 80, 24, _noop, _noop_exit)
    assert _argv(fake_pty) == ("/usr/bin/some-future-cli",)
    assert registry.session.find("Mika").resume is None
    assert registry.session.find("Mika").resumed is False


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
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    term = registry.session.find("Mika")
    term.resume = ResumeHandle(
        kind="claude_session", id="never-written", captured_at=1.0
    )
    # Deliberately no conversation on disk.

    await registry.attach("Mika", 80, 24, _noop, _noop_exit)

    argv = _argv(fake_pty)
    assert "--resume" not in argv, "an id pointing at nothing must not be spent"
    assert "--session-id" in argv, "and the fresh start gets a usable id of its own"
    term = registry.session.find("Mika")
    assert term.resumed is False
    assert term.status == "live", "the pane must come up, not die"
    assert term.resume is not None and term.resume.id != "never-written"


async def test_the_offer_does_not_promise_a_conversation_that_is_not_there(
    tmp_path: Path,
) -> None:
    """What the card would have claimed: twelve conversations, all empty."""
    snapshot = resume_store.Snapshot(
        session_id="ide_old",
        folder=str(tmp_path),
        saved_at=1.0,
        terminals=[
            resume_store.SnapshotTerminal(
                key="mika",
                name="Mika",
                agent="claude",
                resume=ResumeHandle(
                    kind="claude_session", id="never-written", captured_at=1.0
                ),
            )
        ],
    )
    view = resume_store.offer(snapshot, installed={"claude"})
    assert view["terminals"][0]["available"] is True  # the pane comes back
    assert view["terminals"][0]["resumable"] is False  # the conversation does not
    assert view["resumable_count"] == 0


# ------------------------------------------------------------- self-healing
async def test_a_dead_conversation_falls_back_to_a_fresh_agent(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    """The backstop: a conversation that looks present but the CLI rejects."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    term = registry.session.find("Mika")
    term.resume = ResumeHandle(kind="claude_session", id="stale", captured_at=1.0)
    existing_conversation("stale")

    exits: list[int] = []

    async def _record_exit(code: int) -> None:
        exits.append(code)

    await registry.attach("Mika", 80, 24, _noop, _record_exit)
    # The CLI printed "no such conversation" and died right away.
    await fake_pty.spawns[-1]["on_closed"]("fake-pty-1", 1)

    argv = _argv(fake_pty)
    assert "--resume" not in argv, "a stale handle must not be spent twice"
    assert registry.session.find("Mika").resumed is False
    assert registry.session.find("Mika").status == "live"
    # The viewer was never told the pane died — it did not, it restarted.
    assert exits == []


async def test_a_clean_exit_after_a_resume_is_not_second_guessed(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    """Quitting an agent on purpose exits 0 — restarting it would be a bug."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    term = registry.session.find("Mika")
    term.resume = ResumeHandle(kind="claude_session", id="fine", captured_at=1.0)
    existing_conversation("fine")

    exits: list[int] = []

    async def _record_exit(code: int) -> None:
        exits.append(code)

    await registry.attach("Mika", 80, 24, _noop, _record_exit)
    await fake_pty.spawns[-1]["on_closed"]("fake-pty-1", 0)

    assert exits == [0]
    assert registry.session.find("Mika").status == "exited"


async def test_closing_a_resumed_pane_does_not_resurrect_it(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    """The trap the self-healing walks into if it is not told about the kill.

    Stopping a pane kills its agent, and a killed process reports a failure exit
    that looks exactly like a crashed resume. Without knowing the kill was
    deliberate, the recovery would restart an agent the user had just closed —
    and it would then run on with nobody watching, which is precisely what
    stopping it prevents.
    """
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    registry.session.find("Mika").resume = ResumeHandle(
        kind="claude_session", id="fine", captured_at=1.0
    )
    existing_conversation("fine")
    await registry.attach("Mika", 80, 24, _noop, _noop_exit)
    assert registry.session.find("Mika").resumed is True
    spawns_before = len(fake_pty.spawns)

    # The browser tab closed a second after the pane came back.
    registry.detach("Mika")
    await fake_pty.spawns[-1]["on_closed"]("fake-pty-1", 1)

    assert len(fake_pty.spawns) == spawns_before, "the agent must stay stopped"
    assert registry.session.find("Mika").status == "exited"


async def test_a_late_crash_is_reported_as_a_crash(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_conversation,
) -> None:
    """Past the window an exit is just an exit; a restart loop would be worse."""
    monkeypatch.setattr(ide, "RESUME_FAILED_WINDOW_S", 0.0)
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    registry.session.find("Mika").resume = ResumeHandle(
        kind="claude_session", id="fine", captured_at=1.0
    )
    existing_conversation("fine")

    exits: list[int] = []

    async def _record_exit(code: int) -> None:
        exits.append(code)

    await registry.attach("Mika", 80, 24, _noop, _record_exit)
    await fake_pty.spawns[-1]["on_closed"]("fake-pty-1", 3)

    assert exits == [3]
    assert registry.session.find("Mika").status == "exited"


# ------------------------------------------------------------------ restore
def _snapshot(folder: Path) -> resume_store.Snapshot:
    return resume_store.Snapshot(
        session_id="ide_old",
        folder=str(folder),
        saved_at=1.0,
        terminals=[
            resume_store.SnapshotTerminal(
                key="kai",
                name="Kai",
                agent="claude",
                column=1,
                slot=1,
                resume=ResumeHandle(
                    kind="claude_session", id="kai-conv", captured_at=1.0
                ),
                prompts_sent=2,
            ),
            resume_store.SnapshotTerminal(
                key="mika", name="Mika", agent="claude", column=0, slot=0
            ),
        ],
    )


async def test_restore_rebuilds_titles_agents_and_positions(
    registry: ide.Registry, tmp_path: Path
) -> None:
    restored = await registry.restore(_snapshot(tmp_path))

    # Reading order, not snapshot order: left to right, top to bottom.
    assert [t.name for t in restored.terminals] == ["Mika", "Kai"]
    assert [(t.column, t.slot) for t in restored.terminals] == [(0, 0), (1, 0)]
    assert [t.agent for t in restored.terminals] == ["claude", "claude"]
    assert restored.folder == str(tmp_path)
    assert restored.find("Kai").resume.id == "kai-conv"
    assert restored.find("Kai").prompts_sent == 2


async def test_restore_starts_nothing_by_itself(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The grid attaches its panes as it always does — one spawn path only."""
    restored = await registry.restore(_snapshot(tmp_path))
    assert all(t.status == "pending" for t in restored.terminals)
    assert fake_pty.spawns == []


async def test_a_restored_pane_continues_its_conversation_when_it_connects(
    registry: ide.Registry,
    fake_pty: FakePtyManager,
    tmp_path: Path,
    existing_conversation,
) -> None:
    existing_conversation("kai-conv")
    await registry.restore(_snapshot(tmp_path))
    await registry.attach("Kai", 80, 24, _noop, _noop_exit)
    assert _argv(fake_pty)[-2:] == ("--resume", "kai-conv")

    await registry.attach("Mika", 80, 24, _noop, _noop_exit)
    # Mika never had one, so it starts fresh — and says so.
    assert "--resume" not in _argv(fake_pty)
    assert registry.session.find("Mika").resumed is False


async def test_restore_refuses_a_folder_that_is_gone(
    registry: ide.Registry, tmp_path: Path
) -> None:
    with pytest.raises(ide.SessionError, match="no longer"):
        await registry.restore(_snapshot(tmp_path / "deleted"))


async def test_restore_refuses_an_empty_workspace(
    registry: ide.Registry, tmp_path: Path
) -> None:
    empty = resume_store.Snapshot(
        session_id="ide_old", folder=str(tmp_path), saved_at=1.0, terminals=[]
    )
    with pytest.raises(ide.SessionError):
        await registry.restore(empty)


async def test_restore_replaces_a_running_workspace_cleanly(
    registry: ide.Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Old"}])
    await registry.attach("Old", 80, 24, _noop, _noop_exit)
    live = registry.session.find("Old").pty_id

    await registry.restore(_snapshot(tmp_path))
    assert live in fake_pty.closed, "the previous workspace's agent must be stopped"
    assert [t.name for t in registry.session.terminals] == ["Mika", "Kai"]


# ---------------------------------------------------------------- snapshots
async def test_opening_a_workspace_makes_it_resumable(
    registry: ide.Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    saved = resume_store.load()
    assert saved is not None
    assert [t.name for t in saved.terminals] == ["Mika"]
    assert saved.folder == str(tmp_path)


async def test_the_conversation_id_reaches_the_snapshot(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Without this the layout would come back and the conversations would not."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    await registry.attach("Mika", 80, 24, _noop, _noop_exit)

    saved = resume_store.load()
    assert saved is not None and saved.terminals[0].resume is not None
    assert saved.terminals[0].resume.id == registry.session.find("Mika").resume.id


async def test_splitting_and_closing_keep_the_offer_current(
    registry: ide.Registry, tmp_path: Path
) -> None:
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    await registry.add_terminal(anchor="Mika", direction="right", name="Nova")
    saved = resume_store.load()
    assert saved is not None and [t.name for t in saved.terminals] == ["Mika", "Nova"]

    await registry.close_terminal("Nova")
    saved = resume_store.load()
    assert saved is not None and [t.name for t in saved.terminals] == ["Mika"]


async def test_closing_the_workspace_deliberately_withdraws_the_offer(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """An explicit close means "I am done" — re-offering it would be noise."""
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "Mika"}])
    assert resume_store.load() is not None

    await registry.end()
    assert resume_store.load() is None


async def test_opening_another_workspace_does_not_leave_a_gap(
    registry: ide.Registry, tmp_path: Path
) -> None:
    """Replacing a workspace must always leave SOMETHING resumable behind."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    await registry.start(str(first), [{"agent": "claude", "name": "Mika"}])
    await registry.start(str(second), [{"agent": "claude", "name": "Nova"}])

    saved = resume_store.load()
    assert saved is not None and saved.folder == str(second)


async def test_a_broken_snapshot_write_never_breaks_the_workspace(
    registry: ide.Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_snapshot: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(resume_store, "save", _boom)
    workspace = await registry.start(
        str(tmp_path), [{"agent": "claude", "name": "Mika"}]
    )
    assert [t.name for t in workspace.terminals] == ["Mika"]


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
    assert saved is not None and saved.terminals[0].resume == found


async def test_a_lookup_offers_the_ids_other_panes_already_hold(
    registry: ide.Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise two Codex panes in one folder would share a conversation."""
    monkeypatch.setattr(ide, "DISCOVERY_DELAYS_S", (0.0,))
    seen: list[set[str]] = []

    def _spy(_agent: str, _cwd: str, _started: float, taken: set[str]):
        seen.append(set(taken))
        return None

    monkeypatch.setattr(ide, "discover", _spy)
    await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    registry.session.terminals[0].resume = None
    await registry.add_terminal(name="Dana", agent="codex")
    registry.session.find("Cody").resume = ResumeHandle(
        kind="codex_rollout", id="cody-conv", captured_at=1.0
    )

    await registry.attach("Dana", 80, 24, _noop, _noop_exit)
    await asyncio.sleep(0.05)
    assert seen and "cody-conv" in seen[-1]


async def test_closing_the_workspace_stops_pending_lookups(
    registry: ide.Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lookup outliving its workspace would write a snapshot for a ghost."""
    monkeypatch.setattr(ide, "DISCOVERY_DELAYS_S", (5.0,))
    await registry.start(str(tmp_path), [{"agent": "codex", "name": "Cody"}])
    await registry.attach("Cody", 80, 24, _noop, _noop_exit)
    assert registry._lookups

    await registry.end()
    assert not registry._lookups
