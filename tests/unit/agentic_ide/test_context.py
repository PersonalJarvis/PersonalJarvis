"""The focus-mode context block — what Jarvis actually knows while coding mode
is on, and (just as important) that it knows nothing when the mode is off.

The "off" cases are the guard against a feature that quietly widens every prompt
for users who never open the IDE.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.context import focus_context_block
from jarvis.agentic_ide.session import Registry, reset_registry
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def clean_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[Registry, FakePtyManager]:
    fake = FakePtyManager()
    registry = Registry(pty_manager=fake)
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    # context.focus_context_block resolves get_registry from the session module
    # at call time, so patching it there is enough.
    monkeypatch.setattr(session_mod, "get_registry", lambda: registry)
    return registry, fake


def test_no_session_means_no_block() -> None:
    assert focus_context_block() == ""


async def test_open_workspace_without_focus_mode_gets_the_short_block(
    wired: tuple[Registry, FakePtyManager], tmp_path: Path
) -> None:
    """An open workspace is visible to the model even with focus mode off.

    Until 2026-08-13 this case produced nothing, because the block only ever
    served focus mode. It has to produce something now: the addressed-terminal
    fast path stands down on uncertain evidence and lets the MODEL judge whether
    a sentence was aimed at a pane, and a model that cannot see the panes would
    answer that question from the user's own words — the "I have let Alex know"
    failure over an idle terminal, arrived at by a new route.

    It stays SHORT, though: this rides every turn while a workspace is open,
    including the ones that never mention it. The project profile, the branch
    and the generous output tails remain focus mode's.
    """
    registry, _ = wired
    await registry.start(str(tmp_path), [{"agent": "claude"}])

    block = focus_context_block()
    assert "AGENTIC IDE" in block
    assert "T1" in block
    # The tools are named, because naming them is what replaces the regex that
    # used to make this decision without the model.
    assert "agentic-ide-prompt" in block
    # …and the full focus-mode block's expensive parts stay out.
    assert len(block) <= 2000
    assert "focused coding mode is ON" not in block


async def test_focus_mode_block_names_the_folder_and_terminals(
    wired: tuple[Registry, FakePtyManager], tmp_path: Path
) -> None:
    registry, fake = wired
    await registry.start(str(tmp_path), [{"agent": "claude"}, {"agent": "codex"}])
    registry.set_focus_mode(True)
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    on_output = fake.spawns[-1]["on_output"]
    await on_output("pty", "refactoring the router\r\n")

    block = focus_context_block()
    assert "AGENTIC IDE" in block
    assert str(tmp_path) in block
    assert "T1 (Claude Code)" in block
    assert "T2 (Codex)" in block
    assert "refactoring the router" in block


async def test_focus_context_names_the_one_terminal_visible_in_chat_view(
    wired: tuple[Registry, FakePtyManager], tmp_path: Path
) -> None:
    registry, _ = wired
    session = await registry.start(
        str(tmp_path), [{"agent": "claude"}, {"agent": "codex"}]
    )
    registry.set_focus_mode(True)
    assert registry.set_surface_context(
        workspace_id=session.id,
        view="chat",
        on_screen=True,
        terminal="T2",
    )

    block = focus_context_block()

    assert "one visible terminal" in block
    assert "mean T2" in block


async def test_block_is_capped(
    wired: tuple[Registry, FakePtyManager], tmp_path: Path
) -> None:
    registry, fake = wired
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    registry.set_focus_mode(True)
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    on_output = fake.spawns[-1]["on_output"]
    for i in range(500):
        await on_output("pty", f"line number {i} with a fair amount of text\r\n")
    assert len(focus_context_block(max_chars=800)) <= 800


async def test_a_brief_being_written_is_stated_as_not_arrived(
    wired: tuple[Registry, FakePtyManager], tmp_path: Path
) -> None:
    """The one workspace fact that lived nowhere: a brief is ON THE WAY.

    Live 2026-08-13 11:20:12 — "I have prompted T5 to do a deep dive …" was
    spoken 2 s after dispatch, 14 s before that brief's writer had even started,
    for a delivery that then failed. Everything the model could see said a
    prompt HAD been sent to that pane (the receipt count, the last prompt text);
    nothing said the current one was still being written.
    """
    import asyncio

    from jarvis.agentic_ide import fanout

    registry, _ = wired
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    registry.set_focus_mode(True)
    # A pane is only written into once it is live with a PTY.
    await registry.attach("T1", 80, 24, _noop, _noop_exit)
    session = registry.session
    assert session is not None

    composing = asyncio.Event()

    async def compose(_utterance: str, **_kwargs):
        composing.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        fanout.deliver(
            session=session,
            terminals=["T1"],
            utterance="analyse the run",
            compose=compose,
            send=lambda _n, _t: asyncio.sleep(0),
            cancel_on_hangup=True,
        )
    )
    # Bounded: a pane that never reaches the composer must fail the test, not
    # hang the suite.
    await asyncio.wait_for(composing.wait(), timeout=5.0)

    block = focus_context_block()
    assert "STILL BEING WRITTEN" in block
    assert "nothing has reached T1 yet" in block

    fanout.cancel_spoken_deliveries()
    with pytest.raises(asyncio.CancelledError):
        await task
    # …and the pane's marker disappears again once no brief is in flight, so a
    # later turn is not told it is waiting for something that was abandoned.
    # (The rule in the header stays, of course — it is not a pane line.)
    assert "nothing has reached T1 yet" not in focus_context_block()


async def test_turning_focus_off_removes_the_block_again(
    wired: tuple[Registry, FakePtyManager], tmp_path: Path
) -> None:
    """Switching back must be complete — no coding-partner residue.

    What returns is the SHORT block, not nothing: the panes stay open and stay
    addressable, so the model keeps seeing them (2026-08-13). The full block's
    role directive and project profile are what must disappear, and the size
    difference is the proof that they did.
    """
    registry, _ = wired
    await registry.start(str(tmp_path), [{"agent": "claude"}])
    registry.set_focus_mode(True)
    focused = focus_context_block()
    assert "focused coding mode is ON" in focused

    registry.set_focus_mode(False)
    unfocused = focus_context_block()
    assert "focused coding mode is ON" not in unfocused
    assert len(unfocused) < len(focused)
    # Closing the workspace is what removes the block entirely.
    await registry.end()
    assert focus_context_block() == ""


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None
