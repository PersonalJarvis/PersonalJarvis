"""Which viewer a pane's output goes to when two of them overlap (BUG-113).

Viewers overlap constantly and by design: reloading the page, restarting a pane
or coming back to the section closes one socket and opens another for the SAME
pane in the same breath, and the agent behind it never stops. Which of the two
the server finishes first is a matter of milliseconds — so the rule cannot be
"the last one to speak wins", it has to be "only the viewer that still holds the
slot may release it".

Without that rule a departing viewer cleared a slot the arriving one had just
filled, and the result was the reported symptom: a full workspace of terminals
frozen on screen — sockets open, agents alive and typing into their transcripts,
screens that never moved again — while a freshly opened pane worked perfectly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry, SessionError, SessionNotReady
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def fake_pty() -> FakePtyManager:
    return FakePtyManager()


@pytest.fixture
def registry(fake_pty: FakePtyManager, monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return Registry(pty_manager=fake_pty)


class Viewer:
    """One socket's end of a pane — what it was handed, in order."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.exits: list[int] = []

    async def output(self, text: str) -> None:
        self.seen.append(text)

    async def exit(self, code: int) -> None:
        self.exits.append(code)

    @property
    def screen(self) -> str:
        return "".join(self.seen)


async def _one_pane(registry: Registry, folder: Path):
    session = await registry.start(str(folder), [{"agent": "claude"}])
    return session, session.terminals[0]


async def test_a_leaving_viewer_does_not_blind_the_one_that_replaced_it(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The reload race, in the order that used to freeze the workspace."""
    session, term = await _one_pane(registry, tmp_path)
    old, new = Viewer(), Viewer()
    await registry.attach(term.name, 80, 24, old.output, old.exit)

    # The page reloads: the replacement socket attaches BEFORE the old one has
    # finished closing, which is the common order and not an exotic one.
    await registry.attach(term.name, 80, 24, new.output, new.exit)
    registry.detach(term.key, session.id, viewer=old.output)

    await fake_pty.emit(term.pty_id, "the agent is still working")

    assert "the agent is still working" in new.screen, (
        "the pane on screen went blind — a viewer that had already been "
        "replaced released the slot the live one was using"
    )
    assert term.viewer_output == new.output


async def test_the_viewer_that_still_holds_the_slot_releases_it(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The ordinary case must keep working: nobody watching means nobody fed."""
    session, term = await _one_pane(registry, tmp_path)
    viewer = Viewer()
    await registry.attach(term.name, 80, 24, viewer.output, viewer.exit)

    registry.detach(term.key, session.id, viewer=viewer.output)
    await fake_pty.emit(term.pty_id, "printed to nobody")

    assert term.viewer_output is None
    assert viewer.screen == "", "a pane nobody is watching must not be fed"
    # And the agent is untouched by it — that is the whole lifetime rule.
    assert term.pty_id not in fake_pty.closed


async def test_a_caller_that_names_no_viewer_still_clears_the_slot(
    registry: Registry, tmp_path: Path
) -> None:
    """Teardown paths mean "nobody is watching this pane", full stop."""
    session, term = await _one_pane(registry, tmp_path)
    viewer = Viewer()
    await registry.attach(term.name, 80, 24, viewer.output, viewer.exit)

    registry.detach(term.key, session.id)

    assert term.viewer_output is None


async def test_the_new_viewer_is_handed_the_screen_the_pane_is_showing(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A replacement viewer starts from the current screen, not from blank."""
    session, term = await _one_pane(registry, tmp_path)
    old = Viewer()
    await registry.attach(term.name, 80, 24, old.output, old.exit)
    await fake_pty.emit(term.pty_id, "\x1b[32mbuilding…\x1b[0m")

    new = Viewer()
    await registry.attach(term.name, 80, 24, new.output, new.exit)
    registry.detach(term.key, session.id, viewer=old.output)

    assert "building…" in new.screen


async def test_an_exit_reaches_the_viewer_that_took_over(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """The other half of the slot: a dead agent must be reported on screen."""
    session, term = await _one_pane(registry, tmp_path)
    old, new = Viewer(), Viewer()
    await registry.attach(term.name, 80, 24, old.output, old.exit)
    await registry.attach(term.name, 80, 24, new.output, new.exit)
    registry.detach(term.key, session.id, viewer=old.output)

    await fake_pty.die(term.pty_id, 1)

    assert new.exits == [1]
    assert old.exits == [], "a replaced viewer must not be told anything"


# ------------------------------------------------- not here vs. not yet
async def test_a_pane_of_a_workspace_that_is_not_open_yet_is_told_to_wait(
    registry: Registry, tmp_path: Path
) -> None:
    """The state every pane reconnects into while the app is still starting.

    Answered as a flat refusal it read as "this terminal no longer exists", and
    a whole grid of panes stopped trying for the rest of the session.
    """
    with pytest.raises(SessionNotReady):
        await registry.attach("Alex", 80, 24, Viewer().output, Viewer().exit)


async def test_a_pane_the_open_workspace_does_not_have_is_refused(
    registry: Registry, tmp_path: Path
) -> None:
    """A pane that is genuinely not there stays refused — retrying cannot help."""
    await _one_pane(registry, tmp_path)

    with pytest.raises(SessionError) as refused:
        await registry.attach("Nobody", 80, 24, Viewer().output, Viewer().exit)

    assert not isinstance(refused.value, SessionNotReady)


# ------------------------------------------------- one pane, several screens
async def test_every_attached_viewer_sees_the_agent(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A pane open in two places drives BOTH screens.

    The failure this pins is the one that made a live workspace look dead
    (reported 2026-07-28). A pane fed only its newest viewer, and viewers are
    not always the same window: the desktop app and a browser tab, two windows,
    a leftover page from an earlier session. Whichever attached last took the
    output and every other screen froze — agents working away behind rectangles
    that never changed, and nothing but a reload to bring them back.
    """
    _session, term = await _one_pane(registry, tmp_path)
    app, tab = Viewer(), Viewer()
    await registry.attach(term.name, 80, 24, app.output, app.exit)
    await registry.attach(term.name, 80, 24, tab.output, tab.exit)

    await fake_pty.emit(term.pty_id, "running the tests")

    assert "running the tests" in app.screen, (
        "the window that attached first went blind when a second one arrived"
    )
    assert "running the tests" in tab.screen
    # Ownership is still single, and still the newest: the pseudo-terminal has
    # exactly one size and two windows must not fight over it.
    assert term.viewer_output == tab.output


async def test_the_survivor_keeps_the_pane_when_the_owner_leaves(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Closing the newest window hands the pane back, it does not orphan it."""
    session, term = await _one_pane(registry, tmp_path)
    app, tab = Viewer(), Viewer()
    await registry.attach(term.name, 80, 24, app.output, app.exit)
    await registry.attach(term.name, 80, 24, tab.output, tab.exit)

    registry.detach(term.key, session.id, viewer=tab.output)
    await fake_pty.emit(term.pty_id, "still here")

    assert "still here" in app.screen
    assert "still here" not in tab.screen, "a closed viewer must stop being fed"
    assert term.viewer_output == app.output, (
        "the remaining window has to own the pane, or it can never set the "
        "agent's screen size again"
    )


async def test_an_exit_reaches_every_screen(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Both windows show the agent stopping — not just the newest one."""
    _session, term = await _one_pane(registry, tmp_path)
    app, tab = Viewer(), Viewer()
    await registry.attach(term.name, 80, 24, app.output, app.exit)
    await registry.attach(term.name, 80, 24, tab.output, tab.exit)

    await fake_pty.die(term.pty_id, 3)

    assert app.exits == [3]
    assert tab.exits == [3]


async def test_one_socket_reattaching_is_not_two_viewers(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A viewer that attaches again replaces itself rather than doubling."""
    _session, term = await _one_pane(registry, tmp_path)
    viewer = Viewer()
    await registry.attach(term.name, 80, 24, viewer.output, viewer.exit)
    await registry.attach(term.name, 80, 24, viewer.output, viewer.exit)

    await fake_pty.emit(term.pty_id, "once")

    assert viewer.seen.count("once") == 1, "the pane wrote the same bytes twice"


async def test_background_viewer_watches_without_stealing_geometry(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """A background browser tab must not narrow the foreground desktop pane."""
    _session, term = await _one_pane(registry, tmp_path)
    app, tab = Viewer(), Viewer()
    await registry.attach(term.name, 180, 50, app.output, app.exit)
    fake_pty.resizes.clear()

    await registry.attach(
        term.name,
        60,
        20,
        tab.output,
        tab.exit,
        claim_owner=False,
    )
    await fake_pty.emit(term.pty_id, "visible in both places")

    assert term.viewer_output == app.output
    assert fake_pty.resizes == []
    assert "visible in both places" in app.screen
    assert "visible in both places" in tab.screen


async def test_foreground_viewer_can_reclaim_its_geometry(
    registry: Registry, fake_pty: FakePtyManager, tmp_path: Path
) -> None:
    """Returning to a window restores the PTY to that window's measured size."""
    session, term = await _one_pane(registry, tmp_path)
    app, tab = Viewer(), Viewer()
    await registry.attach(term.name, 180, 50, app.output, app.exit)
    await registry.attach(
        term.name,
        60,
        20,
        tab.output,
        tab.exit,
        claim_owner=False,
    )
    fake_pty.resizes.clear()

    assert registry.claim_viewer(
        term.key,
        60,
        20,
        session.id,
        viewer=tab.output,
    )
    assert term.viewer_output == tab.output
    assert fake_pty.resizes == [(term.pty_id, 60, 20)]

    assert registry.claim_viewer(
        term.key,
        180,
        50,
        session.id,
        viewer=app.output,
    )
    assert term.viewer_output == app.output
    assert fake_pty.resizes[-1] == (term.pty_id, 180, 50)
