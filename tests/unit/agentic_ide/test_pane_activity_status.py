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

from jarvis.agentic_ide import activity as activity_mod
from jarvis.agentic_ide import notifications
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.activity import RESIZE_SHADOW_S, STAMP_FRESH_S, observed
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


def _instruct(term: Any, at: float) -> None:
    """Prove the current process received the work represented by a test."""
    term.last_submit_at = at
    term.submit_generation = term.process_generation


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
    _instruct(term, time.time() - 1)
    term.transcript.feed(screen)

    _watched(registry, term, moves=True)

    assert term.to_dict()["activity"] == "working"


async def test_output_outside_the_fingerprint_still_counts_as_working(
    registry: Registry, tmp_path: Path
) -> None:
    """A pane printing steadily is working, wherever on the screen it shows.

    The reported bug. The fingerprint covers the BOTTOM rows only, so an agent
    streaming an answer into a tall pane — or any CLI whose busy row sits above
    its input box — was a pane working in a blind spot, and the badge said
    "done" over a terminal that was mid-sentence. Fresh output is now a signal
    in its own right: measured on eleven real panes, a resting one had printed
    nothing for between 19 and 69 seconds.
    """
    _session, term = await _pane(registry, tmp_path)
    _instruct(term, time.time() - 1)
    term.transcript.feed(REST_SCREENS["claude"])
    now = time.time()
    _sweep(registry, now - notifications.SWEEP_INTERVAL_S)
    # Bytes arriving, fingerprint deliberately unchanged.
    term.last_output_at = now
    _sweep(registry, now)

    assert term.to_dict()["activity"] == "working"


async def test_a_keystroke_echo_is_still_not_the_agent_working(
    registry: Registry, tmp_path: Path
) -> None:
    """The one exclusion survives the wider signal.

    A terminal echoes what a person types, so a pane being typed into is a pane
    producing output — without this, writing a prompt by hand reads as a busy
    agent, and the moment the user pauses the pane is reported finished.
    """
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed(REST_SCREENS["claude"])
    now = time.time()
    _sweep(registry, now - notifications.SWEEP_INTERVAL_S)
    term.last_output_at = now
    term.last_input_at = now
    _sweep(registry, now)

    assert term.to_dict()["activity"] == "waiting"


async def test_a_resize_repaint_is_not_the_agent_working(
    registry: Registry, tmp_path: Path
) -> None:
    """The other exclusion: a redraw for a new geometry is not work.

    The reported bug (2026-08-01). Switching between the grid and the chat
    view, maximizing, dragging a seam or changing tabs resizes every pane, a
    TUI answers a size change with a full repaint, and that repaint is fresh
    output AND a changed picture — so every FINISHED pane in the workspace
    read as "working" at exactly the moment the user looked at the list.
    """
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed(REST_SCREENS["claude"])
    now = time.time()
    _sweep(registry, now - notifications.SWEEP_INTERVAL_S)
    # The grid re-laid itself out: PTY resized, TUI repainted — both signals.
    term.last_resize_at = now - 0.5
    term.last_output_at = now
    term.transcript.clear()
    term.transcript.feed(REST_SCREENS["claude"] + "\r\n ")
    _sweep(registry, now)

    assert term.to_dict()["activity"] == "waiting"


async def test_output_after_the_resize_shadow_still_counts_as_working(
    registry: Registry, tmp_path: Path
) -> None:
    """The shadow is a window, not a mute button.

    An agent genuinely working through a resize keeps printing after the
    redraw; its next stamp falls outside the shadow and must count again —
    otherwise a resize would hide real work instead of a repaint.
    """
    _session, term = await _pane(registry, tmp_path)
    _instruct(term, time.time() - 1)
    term.transcript.feed(REST_SCREENS["claude"])
    now = time.time()
    _sweep(registry, now - notifications.SWEEP_INTERVAL_S)
    term.last_resize_at = now - RESIZE_SHADOW_S - 1.0
    term.last_output_at = now
    _sweep(registry, now)

    assert term.to_dict()["activity"] == "working"


async def test_a_grid_relayout_stamps_the_resize_shadow(registry: Registry, tmp_path: Path) -> None:
    """The viewer's resize path leaves the stamp the detector reads."""
    _session, term = await _pane(registry, tmp_path)
    assert term.last_resize_at is None

    assert registry.resize("Alex", 120, 40) is True

    assert term.last_resize_at is not None


# --------------------------------------------- the reading reaches a client


async def test_the_state_carries_what_the_sweep_saw(registry: Registry, tmp_path: Path) -> None:
    """A request handler cannot see movement, so it reports what the sweep saw.

    The whole reason the reading is stamped on the pane: one look at a terminal
    cannot tell a still screen from a moving one, and every client of this is a
    single look.
    """
    _session, term = await _pane(registry, tmp_path)
    _instruct(term, 499.0)
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
    assert state["worked"] is False


async def test_a_pane_that_was_given_a_job_is(registry: Registry, tmp_path: Path) -> None:
    """And a pane somebody typed into carries the evidence for "finished"."""
    _session, term = await _pane(registry, tmp_path)
    term.last_submit_at = time.time() - 60
    term.transcript.feed(REST_SCREENS["claude"])
    _watched(registry, term, moves=False)

    assert term.to_dict()["worked"] is True


async def test_a_resumed_conversation_counts_as_work_behind_the_pane(
    registry: Registry, tmp_path: Path
) -> None:
    """The bug the maintainer reported: a whole workspace labelled "idle".

    Restarting the app gives every pane a fresh process, so the stamp saying
    "somebody typed in here" is gone — and a workspace of agents that had each
    worked for ten minutes came back describing itself as untouched. A resumed
    conversation is proof somebody talked to this pane; nothing else needs to
    survive the restart for the badge to be honest.
    """
    _session, term = await _pane(registry, tmp_path)
    term.last_submit_at = None
    term.prompts_sent = 0
    term.resumed = True
    term.transcript.feed(REST_SCREENS["claude"])
    _watched(registry, term, moves=False)

    assert term.to_dict()["worked"] is True


async def test_the_bell_is_not_widened_with_it(registry: Registry, tmp_path: Path) -> None:
    """The bell keeps the strict rule, and that difference is load-bearing.

    A restored pane repaints its old transcript — which is "was working, then
    stopped" — so accepting a resumed conversation as evidence THERE would file
    a completion for every terminal in the workspace on every restart.
    """
    _session, term = await _pane(registry, tmp_path)
    term.resumed = True

    assert notifications._tasked(term) is False


async def test_the_caption_never_contradicts_the_badge(registry: Registry, tmp_path: Path) -> None:
    """The sentence under the pane and the word beside it describe one pane.

    Reported together with the badge itself: the caption said "Working now" for
    a terminal that had been finished for twenty minutes, one line under a badge
    correctly saying it had stopped. It claimed work from "has this pane ever
    printed anything", which is true of every pane that has ever run.
    """
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed(REST_SCREENS["claude"])
    _watched(registry, term, moves=False)

    state = term.to_dict()

    assert state["activity"] == "waiting"
    assert "Working now" not in state["recap_detail"]
    assert "waiting at its prompt" in state["recap_detail"]


async def test_the_caption_says_working_when_it_is(registry: Registry, tmp_path: Path) -> None:
    """And the other way round, or the wording would just be pessimistic."""
    _session, term = await _pane(registry, tmp_path)
    _instruct(term, time.time() - 1)
    term.transcript.feed(REST_SCREENS["claude"])
    _watched(registry, term, moves=True)

    state = term.to_dict()

    assert state["activity"] == "working"
    assert "Working now" in state["recap_detail"]


# ----------------------------------------------------- the moment after Send


async def test_a_just_submitted_pane_reads_as_working_before_it_paints(
    registry: Registry, tmp_path: Path
) -> None:
    """The blind window the maintainer kept hitting: Send, then a "done" badge.

    Movement proves work, but the agent's first byte can be seconds away and
    movement in the shadow of the submitting keystroke is discounted — so the
    seconds right after a submit read as "waiting" under the user's own just-
    pressed Send. The submission stamp is evidence enough for those seconds.
    """
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed(REST_SCREENS["claude"])
    _watched(registry, term, moves=False)
    assert term.to_dict()["activity"] == "waiting"

    _instruct(term, time.time())

    state = term.to_dict()
    assert state["activity"] == "working"
    # The episode began at the submit, not at the epoch — the tooltip's "For 2s".
    assert state["activity_since"] > 0


async def test_the_submit_grace_expires_into_the_truth(registry: Registry, tmp_path: Path) -> None:
    """A prompt the agent swallowed stops claiming work within one glance."""
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed(REST_SCREENS["claude"])
    _instruct(term, time.time() - activity_mod.SUBMIT_GRACE_S - 1)
    _watched(registry, term, moves=False)

    assert term.to_dict()["activity"] == "waiting"


async def test_a_question_on_screen_outranks_the_submit_grace(
    registry: Registry, tmp_path: Path
) -> None:
    """A CLI that answers a submit with a permission prompt needs the USER.

    Hiding that behind a grace-period spinner would be the worst of both: the
    pane sits on a question while its badge promises progress.
    """
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed("\r\nDo you want to allow this command? (y/n)\r\n")
    _watched(registry, term, moves=False)
    _instruct(term, time.time())

    assert term.to_dict()["activity"] == "asking"


async def test_a_submit_to_a_replaced_process_earns_no_grace(
    registry: Registry, tmp_path: Path
) -> None:
    """Only the LIVE process's submission counts, exactly as for movement."""
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed(REST_SCREENS["claude"])
    _instruct(term, time.time())
    term.process_generation += 1  # the agent was respawned since that Send
    _watched(registry, term, moves=False)

    assert term.to_dict()["activity"] == "waiting"


async def test_the_bell_keeps_the_strict_movement_rule(registry: Registry, tmp_path: Path) -> None:
    """The grace lives in `observed`, never in the sweep's own reading.

    Otherwise a swallowed prompt would count as a working episode, and its
    grace expiring would file a "finished" bell for a pane that did nothing.
    """
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed(REST_SCREENS["claude"])
    _instruct(term, time.time())

    reading = activity_mod.read_activity(term, now=time.time())

    assert reading == "waiting"


async def test_a_single_printout_does_not_spin_the_badge(
    registry: Registry, tmp_path: Path
) -> None:
    """The slot machine (maintainer report 2026-08-07, with screenshot).

    ``/recap`` printed its answer into a pane whose agent was waiting for
    input, and the badge spun for the whole freshness tail of that one burst —
    as it did for every menu, slash command and user-caused redraw. One
    printout is not an agent at work: real work repaints continuously, a burst
    dies within ``STILL_S``, and the stamped word now demands the difference.
    """
    _session, term = await _pane(registry, tmp_path)
    _instruct(term, 100.0)  # instructed long ago — no submit wake, no grace
    term.transcript.feed(REST_SCREENS["claude"])
    _sweep(registry, 500.0)
    _sweep(registry, 502.0)
    assert observed(term, now=502.0).activity == "waiting"

    # A local command prints once — fresh bytes AND a changed picture.
    term.transcript.feed("\r\nrecap: shipped the status badge fix\r\n")
    term.last_output_at = 504.0
    for at in (504.0, 506.0, 508.0):
        _sweep(registry, at)
        assert observed(term, now=at).activity == "waiting"


async def test_sustained_repainting_still_reads_as_working(
    registry: Registry, tmp_path: Path
) -> None:
    """And the confirmation window costs real work only its first seconds.

    An agent that is genuinely busy repaints at least twice a second for as
    long as it runs, so its movement episode outlives the burst tail and the
    badge spins — a few seconds late without a fresh submit, instantly with
    one (the wake rule, covered below).
    """
    _session, term = await _pane(registry, tmp_path)
    _instruct(term, 100.0)  # outside the submit wake: the strict path
    term.transcript.feed(REST_SCREENS["claude"])
    _sweep(registry, 500.0)
    _sweep(registry, 502.0)

    for at in (504.0, 506.0, 508.0, 510.0):
        term.transcript.feed(f"\r\n· step at {at}\r\n")
        term.last_output_at = at
        _sweep(registry, at)

    assert observed(term, now=510.0).activity == "working"


async def test_movement_in_a_submits_wake_confirms_instantly(
    registry: Registry, tmp_path: Path
) -> None:
    """Output right after a Send is the agent starting, not a stray printout.

    The seam between the submit grace (a still pane may claim working for ten
    seconds after Send) and observed movement: without this, a slow first
    paint landing late in the grace would flap the badge back to "done" while
    the confirmation window ran. Movement whose episode begins in the wake of
    a confirmed submission needs no further proof.
    """
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed(REST_SCREENS["claude"])
    _sweep(registry, 500.0)
    _sweep(registry, 502.0)

    _instruct(term, 503.0)  # Send
    term.transcript.feed("\r\n· Scurrying…\r\n")
    term.last_output_at = 504.0  # first paint, one second later
    _sweep(registry, 504.0)

    assert observed(term, now=504.0).activity == "working"


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
