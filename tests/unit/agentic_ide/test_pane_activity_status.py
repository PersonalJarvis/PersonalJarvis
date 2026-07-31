"""What the pane list shows instead of "live".

The badge beside every pane used to report the SOCKET, which is up for a pane
that finished twenty minutes ago, for one sitting on an unanswered question, and
for one grinding through a refactor. These tests are about the claim that
replaced it: is this pane's agent still working, and how does that reach a
client.

Three properties, and each one has an obvious wrong implementation:

* the reading is taken from the terminal SCREEN, so it holds for every coding
  CLI a pane can run — including one connected next year, which is why the cases
  are parametrized over the installed products rather than written against one;
* a request handler gets the SWEEP's reading rather than its own single look,
  because whether a screen is moving cannot be seen from one look at it;
* and a still screen is only called "finished" for a pane that was given
  something to finish.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import notifications
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.activity import STAMP_FRESH_S, observed
from jarvis.agentic_ide.session import PLAIN_TERMINAL, Registry
from tests.fakes.fake_pty_manager import FakePtyManager

# Screens copied off the real products, exactly as the notification tests use
# them: four CLIs that look nothing like each other, read by one rule.
REST_SCREENS = {
    "claude": "\r\n✻ Worked for 13m 27s\r\n>\r\n  📁 probe-repo  🌿 main  Opus 5\r\n",
    "codex": "\r\n• ping\r\n› Improve documentation in @filename\r\n  gpt-5.6-sol xhigh fast\r\n",
    "opencode": "\r\n  ┃  Build · Nano Banana Pro Google\r\n     tab agents  ctrl+p commands\r\n",
    "kimi": '\r\n ✦ Use Kimi K3\r\n   Error: LLM not set, send "/login" to login\r\n │ >  \r\n',
}


@pytest.fixture(autouse=True)
def _clean_store() -> Any:
    notifications.reset()
    yield
    notifications.reset()


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    return Registry(pty_manager=FakePtyManager())


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _pane(registry: Registry, folder: Path, *, name: str = "Alex", agent: str = "claude"):
    session = await registry.start(str(folder), [{"agent": agent, "name": name}])
    term = await registry.attach(name, 100, 30, _noop, _noop_exit)
    term.last_output_at = 0.0
    term.last_input_at = None
    return session, term


def _sweep(registry: Registry, at: float) -> None:
    """One pass of the watcher that publishes what every pane is doing."""
    notifications.watcher().poll(registry, now=at, emit=False)


def _watched(registry: Registry, term: Any, *, moves: bool) -> None:
    """Two sweeps on the REAL clock — one look cannot see movement.

    Wall clock rather than a synthetic timeline, because these cases end at
    ``to_dict``, which is a request handler and reads the clock a request
    actually happens on. A stamp from an invented year is one this module is
    right to distrust (`test_a_reading_nobody_refreshed_is_not_trusted`).
    """
    now = time.time()
    _sweep(registry, now - notifications.SWEEP_INTERVAL_S)
    if moves:
        term.transcript.feed("\r\n· one more line\r\n")
    _sweep(registry, now)


# ------------------------------------------------------ every CLI, one rule


@pytest.mark.parametrize("agent,screen", list(REST_SCREENS.items()))
async def test_a_finished_pane_is_reported_as_waiting_whatever_it_runs(
    registry: Registry, tmp_path: Path, agent: str, screen: str
) -> None:
    """A pane whose picture stands still has stopped — for all four products."""
    _session, term = await _pane(registry, tmp_path, agent=agent, name="Px")
    term.transcript.feed(screen)

    _watched(registry, term, moves=False)

    assert term.to_dict()["activity"] == "waiting"


@pytest.mark.parametrize("agent,screen", list(REST_SCREENS.items()))
async def test_a_moving_pane_is_reported_as_working_whatever_it_runs(
    registry: Registry, tmp_path: Path, agent: str, screen: str
) -> None:
    """And one whose picture keeps changing is still on the job."""
    _session, term = await _pane(registry, tmp_path, agent=agent, name="Px")
    term.transcript.feed(screen)

    _watched(registry, term, moves=True)

    assert term.to_dict()["activity"] == "working"


# --------------------------------------------- the reading reaches a client


async def test_the_state_carries_what_the_sweep_saw(registry: Registry, tmp_path: Path) -> None:
    """A request handler cannot see movement, so it reports what the sweep saw.

    The whole reason the reading is stamped on the pane: one look at a terminal
    cannot tell a still screen from a moving one, and every client of this is a
    single look.
    """
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed("\r\n· Scurrying… (2m 4s)\r\n")
    _sweep(registry, 500.0)
    term.transcript.clear()
    term.transcript.feed("\r\n· Scurrying… (2m 6s)\r\n")
    _sweep(registry, 502.0)

    reading = observed(term, now=503.0)

    assert reading.activity == "working"
    assert reading.since == 502.0


async def test_a_reading_nobody_refreshed_is_not_trusted(
    registry: Registry, tmp_path: Path
) -> None:
    """A stamp that stopped being refreshed means nothing is watching.

    Not a stale word to keep showing: the sweep runs every two seconds for as
    long as a workspace is open, so a stamp minutes old says the sweep died or
    never started. The caller gets a fresh single look instead — which, for a
    pane that has printed nothing in a while, is "waiting".
    """
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed("\r\n· Scurrying…\r\n")
    _sweep(registry, 500.0)
    _sweep(registry, 502.0)
    assert term.activity == "waiting"
    term.activity = "working"  # a word the sweep left behind and never renewed

    assert observed(term, now=502.0 + STAMP_FRESH_S + 1).activity == "waiting"


async def test_a_plain_terminal_is_not_described_as_finished(
    registry: Registry, tmp_path: Path
) -> None:
    """A shell prompt runs no agent, so every word here would be a lie.

    It stands perfectly still for its whole life, which is what "finished" looks
    like — and it has never been given a job to finish. The pane list falls back
    to reporting the connection for it.
    """
    _session, term = await _pane(registry, tmp_path, agent=PLAIN_TERMINAL, name="Shell")
    term.transcript.feed("\r\nC:\\probe-repo> \r\n")
    _watched(registry, term, moves=False)

    assert term.to_dict()["activity"] == ""


# ------------------------------------------- finished, versus never asked


async def test_a_pane_nobody_instructed_is_not_called_finished(
    registry: Registry, tmp_path: Path
) -> None:
    """Same still screen, different news — so the client is told which."""
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed(REST_SCREENS["claude"])
    _watched(registry, term, moves=False)

    state = term.to_dict()

    assert state["activity"] == "waiting"
    assert state["tasked"] is False


async def test_a_pane_that_was_given_a_job_is(registry: Registry, tmp_path: Path) -> None:
    """And a pane somebody typed into carries the evidence for "finished"."""
    _session, term = await _pane(registry, tmp_path)
    term.last_submit_at = time.time() - 60
    term.transcript.feed(REST_SCREENS["claude"])
    _watched(registry, term, moves=False)

    assert term.to_dict()["tasked"] is True


async def test_the_two_readings_of_one_pane_agree(registry: Registry, tmp_path: Path) -> None:
    """The workspace state and the pane-list poll describe the same pane alike.

    They are two different reads on two different clocks, and a pane the grid
    calls working while the list calls it finished is worse than either answer
    on its own — the drift class §5 exists for exactly this.
    """
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed("\r\n· Scurrying…\r\n")
    _watched(registry, term, moves=True)

    state = term.to_dict()
    reading = term.reading()

    assert state["activity"] == reading.activity
    assert state["activity_since"] == reading.since
