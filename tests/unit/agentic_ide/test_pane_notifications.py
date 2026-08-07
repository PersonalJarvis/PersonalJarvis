"""The bell: which terminals stopped while nobody was looking at them.

A workspace runs a dozen coding agents in postcard-sized panes, and none of them
announces that it is done — the spinner row simply stops being drawn. So the
feature is entirely about the TRANSITION, and every test here is about getting
that edge right rather than about the store around it:

* a pane that was busy and went quiet is reported ONCE,
* a pane that has been idle since it appeared is never reported at all,
* a pane nobody has instructed yet is never reported either, however much its
  starting CLI paints itself across the screen,
* the gap a TUI leaves between two steps does not produce a notification,
* and an entry never outlives the pane it points at — nor the workspace.

Each of those has an obvious wrong implementation that looks correct in a demo
and is unusable in a grid of twelve.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import notifications
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.activity import STILL_S, read_activity
from jarvis.agentic_ide.session import Registry
from tests.fakes.fake_pty_manager import FakePtyManager

# What a coding CLI draws while it is working, and what it draws when it is not.
BUSY_SCREEN = "\r\n✳ Cooking… (12s · ↑ 1.2k tokens · esc to interrupt)\r\n"
IDLE_SCREEN = "\r\n❯ \r\n"
QUESTION_SCREEN = "\r\nDo you want to make this edit to config.py?\r\n❯ 1. Yes\r\n"

# Rows copied VERBATIM off the panes of a running workspace (Claude Code
# 2.1.220, 2026-07-29), because the first version of this detector was written
# against an assumption instead and the assumption was wrong: it looked for
# "esc to interrupt", this build does not draw one in these states, so no pane
# was ever seen working and the bell stayed empty while terminals finished all
# around it. Whatever replaces the rule has to keep answering these correctly.
REAL_WORKING = (
    "\r\n· Scurrying… (2m 4s · ↓ 1.6k tokens)\r\n"
    "  Tip: Use /btw to ask a quick side question without interrupting Claude's current work\r\n"
)
REAL_THINKING = "\r\n· Crystallizing… (1m 54s · thinking)\r\n"
REAL_FINISHED = (
    "\r\n✻ Worked for 13m 27s\r\n"
    ">\r\n"
    "  📁 Personal Jarvis  🌿 main  Opus 5 (1M context)\r\n"
    "  ⏵⏵ auto mode on (shift+tab to cycle) · ⁝ for age…\r\n"
)
# The startup screen of a pane nobody has typed into. Its authentication banner
# sits there for the pane's whole life — treating it as a question would make
# every idle terminal a standing "needs input".
REAL_STARTUP_BANNER = (
    "\r\n           Claude Code v2.1.220\r\n"
    " ▐▛███▜▌   Opus 5 (1M context) with xhigh effort\r\n"
    "▝▜█████▛▘  Claude Max\r\n"
    "  ▘▘ ▝▝    ~\\Desktop\\Personal Jarvis\r\n"
    " ‼ 1 MCP server needs authentication · run /mcp\r\n"
    ">\r\n"
)

# --- EVERY CLI a workspace can open, at rest (measured 2026-07-29) -----------
#
# The detector must hold for all of them, so the cases below are parametrized
# over these rather than written against one product. They look nothing like
# each other — different box drawing, different status lines, different
# languages of chrome — which is the point: the rule reads the terminal, not
# the thing running in it.
#
# Also measured, and the reason the rule is safe: while WAITING, all four
# changed their screen 0 times in 40 samples over 20 seconds; while WORKING,
# the longest gap between changes was 0.5 s.
REST_SCREENS = {
    "claude": (
        "\r\n ‼ 1 MCP server needs authentication · run /mcp\r\n"
        "❯ Reply with exactly one word: ping\r\n"
        "● ping\r\n"
        "✻ Sautéed for 2s\r\n"
        "❯\r\n"
        "  📁 probe-repo  🌿 master  Opus 5 (1M context)\r\n"
    ),
    "codex": (
        "\r\n• You have 1 usage limit reset available. Run /usage to use one.\r\n"
        "• ping\r\n"
        "› Improve documentation in @filename\r\n"
        "  gpt-5.6-sol xhigh fast · ~\\probe-repo\r\n"
    ),
    "opencode": (
        "\r\n  ┃  Build · Nano Banana Pro Google\r\n"
        "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\r\n"
        "                    tab agents  ctrl+p commands\r\n"
    ),
    "kimi": (
        "\r\n ✦ Use Kimi K3 with High thinking effort\r\n"
        '   Error: LLM not set, send "/login" to login\r\n'
        " │ >                                        \r\n"
        " C:\\probe-repo                              \r\n"
    ),
}


@pytest.fixture(autouse=True)
def _clean_store() -> Any:
    """Every test starts with an empty bell and no per-pane memory."""
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
    """One live pane in one workspace, through the real attach path."""
    session = await registry.start(str(folder), [{"agent": agent, "name": name}])
    term = await registry.attach(name, 100, 30, _noop, _noop_exit)
    return session, term


def _draw(term: Any, screen: str) -> None:
    """Put ``screen`` on the pane's replayed terminal, as its agent would."""
    term.transcript.feed(screen)


def _quiet_since(term: Any, at: float) -> None:
    """Say this pane last printed at ``at`` and nobody has typed into it.

    Attaching a pane stamps it with the WALL clock, while these tests run on a
    synthetic timeline — so without this a pane is "printing right now" against
    every `now` a test picks, and the third busy signal (see
    `activity.OUTPUT_QUIET_S`) answers before the screen is ever consulted.
    Tests about what is ON the screen say so explicitly.
    """
    term.last_output_at = at
    term.last_input_at = None


def _busy(
    watcher: Any,
    registry: Registry,
    term: Any,
    *,
    start: float,
    sweeps: int = 3,
    tasked: bool = True,
) -> float:
    """Drive a pane through ``sweeps`` sweeps of a MOVING screen.

    A pane is busy because its picture keeps changing, so making one busy in a
    test means changing its picture — repeatedly, since a single change cannot
    be told from a pane that was already still. Returns the moment of the last
    sweep, which is when the screen stopped moving.

    The pane is also stamped as having been GIVEN the job, because that is what
    a working pane is: an agent does not start moving on its own account except
    while it is starting up, which is the case
    `test_a_starting_cli_painting_itself_is_not_a_finished_job` covers.
    """
    if tasked:
        term.last_submit_at = start - 0.1
        term.submit_generation = term.process_generation
    at = start
    for step in range(sweeps):
        at = start + step * notifications.SWEEP_INTERVAL_S
        term.transcript.clear()
        _draw(term, f"\r\n· working, frame {step}\r\n")
        watcher.poll(registry, now=at)
    return at


def _rest(
    watcher: Any, registry: Registry, term: Any, screen: str, *, at: float, emit: bool = True
) -> list:
    """Show ``screen`` and let it stand still long enough to count as settled."""
    term.transcript.clear()
    _draw(term, screen)
    watcher.poll(registry, now=at, emit=emit)
    return watcher.poll(registry, now=at + STILL_S + 1, emit=emit)


# ------------------------------------------------- reading the REAL terminals
#
# One rule, four products, no product knowledge: a screen that MOVES is an agent
# at work, a screen that stands still is a terminal waiting. Each case below
# drives the real rows of a real CLI through the detector.


@pytest.mark.parametrize("agent,screen", list(REST_SCREENS.items()))
async def test_a_still_screen_is_a_waiting_pane(
    registry: Registry, tmp_path: Path, agent: str, screen: str
) -> None:
    """Every CLI at rest, read the same way — measured: idle panes never repaint."""
    _session, term = await _pane(registry, tmp_path, agent=agent, name="Px")
    _draw(term, screen)

    assert read_activity(term, now=1030.0, still_since=1000.0) == "waiting"


@pytest.mark.parametrize("agent,screen", list(REST_SCREENS.items()))
async def test_a_moving_screen_is_a_working_pane(
    registry: Registry, tmp_path: Path, agent: str, screen: str
) -> None:
    """And every CLI while its screen moves, whatever it happens to be drawing."""
    _session, term = await _pane(registry, tmp_path, agent=agent, name="Px")
    term.last_submit_at = 999.0
    term.submit_generation = term.process_generation
    _draw(term, screen)

    assert read_activity(term, now=1000.5, still_since=1000.0) == "working"


async def test_background_output_without_an_instruction_never_reads_as_working(
    registry: Registry, tmp_path: Path
) -> None:
    """An idle MCP warning may repaint, but nobody asked the agent to work."""
    _session, term = await _pane(registry, tmp_path)
    term.transcript.feed("\r\n1 MCP server needs authentication\r\n")
    term.last_output_at = 1000.4

    assert term.last_submit_at is None
    assert read_activity(term, now=1000.5, still_since=1000.0) == "waiting"


async def test_a_previous_process_submission_cannot_arm_replacement_output(
    registry: Registry, tmp_path: Path
) -> None:
    """Only a submission to the live process can turn movement into work."""
    _session, term = await _pane(registry, tmp_path)
    term.last_submit_at = 999.0
    term.submit_generation = term.process_generation
    term.process_generation += 1
    term.last_output_at = 1000.4

    assert read_activity(term, now=1000.5, still_since=1000.0) == "waiting"

    term.last_submit_at = 1000.45
    term.submit_generation = term.process_generation
    assert read_activity(term, now=1000.5, still_since=1000.0) == "working"


async def test_movement_is_the_only_thing_that_counts(registry: Registry, tmp_path: Path) -> None:
    """The rows this build draws while WORKING are not treated as special.

    The point of the rewrite: these are the exact rows that fooled two earlier
    versions — one looked for an interrupt hint they do not contain, the next
    for a bracketed clock that also matched the model banner. Frozen on screen,
    they mean a pane that has stopped, and that is now the answer.
    """
    _session, term = await _pane(registry, tmp_path)
    _draw(term, REAL_WORKING)

    assert read_activity(term, now=1030.0, still_since=1000.0) == "waiting"


async def test_a_pane_whose_screen_keeps_changing_is_working(
    registry: Registry, tmp_path: Path
) -> None:
    """Through the watcher, which is what actually tracks the movement."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    term.last_submit_at = 99.0
    term.submit_generation = term.process_generation

    _draw(term, REAL_WORKING)
    watcher.poll(registry, now=100.0)
    for tick in range(1, 6):
        # A different screen every sweep, exactly as a running agent produces.
        term.transcript.clear()
        _draw(term, f"\r\n· Scurrying… ({tick}m 4s · ↓ {tick}.6k tokens)\r\n")
        assert watcher.poll(registry, now=100.0 + tick * 2) == []
        assert watcher._panes[(_session.id, term.key)].activity == "working"


async def test_the_startup_banner_is_not_a_question(registry: Registry, tmp_path: Path) -> None:
    """It sits there for the pane's whole life — a standing banner, not an ask."""
    _session, term = await _pane(registry, tmp_path)
    _draw(term, REAL_STARTUP_BANNER)

    assert read_activity(term, now=1030.0, still_since=1000.0) == "waiting"


async def test_a_visible_choice_is_told_apart_from_a_finished_pane(
    registry: Registry, tmp_path: Path
) -> None:
    """Content decides the WORD, never whether the pane is busy."""
    _session, term = await _pane(registry, tmp_path)
    _draw(term, QUESTION_SCREEN)

    assert read_activity(term, now=1030.0, still_since=1000.0) == "asking"


async def test_typing_into_a_pane_is_not_the_agent_working(
    registry: Registry, tmp_path: Path
) -> None:
    """A terminal echoes keystrokes, and an echo is not work.

    Without this, writing a prompt by hand reads as a busy agent — and the
    moment the user pauses to think, the pane is reported as finished.
    """
    _session, term = await _pane(registry, tmp_path)
    _draw(term, IDLE_SCREEN)
    term.last_input_at = 1000.0

    assert read_activity(term, now=1000.2, still_since=1000.0) == "waiting"


async def test_movement_after_the_typing_stopped_is_work(
    registry: Registry, tmp_path: Path
) -> None:
    """The other side of the same rule: once the keyboard is quiet, movement counts."""
    _session, term = await _pane(registry, tmp_path)
    _draw(term, IDLE_SCREEN)
    term.last_input_at = 1000.0
    term.last_submit_at = 1005.0
    term.submit_generation = term.process_generation

    assert read_activity(term, now=1010.2, still_since=1010.0) == "working"


async def test_a_real_pane_finishing_files_one_entry(registry: Registry, tmp_path: Path) -> None:
    """End to end on the real rows: working → finished → exactly one entry."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)

    _draw(term, REAL_WORKING)
    term.last_submit_at = 999.0
    term.submit_generation = term.process_generation
    term.last_output_at = 1000.0
    assert watcher.poll(registry, now=1000.5) == []

    term.transcript.clear()
    _draw(term, REAL_FINISHED)
    _quiet_since(term, 1002.0)
    # Still "working" here: it printed half a second ago, and a reply arrives in
    # bursts. The clock is gone but the pane is not done talking.
    assert watcher.poll(registry, now=1002.5) == []
    # Past the output window, so this is where the settle timer actually starts.
    assert watcher.poll(registry, now=1010.0) == []

    filed = watcher.poll(registry, now=1010.0 + notifications.SETTLE_S + 1)

    assert [entry.kind for entry in filed] == ["completed"]


# ------------------------------------------------------------------- reading
# There is deliberately no test here that a BUSY-looking screen reads as
# working: that is the rule the rewrite removed, and it is pinned the other way
# round by `test_movement_is_the_only_thing_that_counts` above. The one that
# used to sit here asserted it, and passed for a reason that had nothing to do
# with its name — attaching a pane stamped `last_output_at` with the wall clock,
# so the fallback in `_moving` called every freshly-attached pane busy no matter
# what was on its screen. Fixing that stamp is what exposed it.
async def test_a_pane_at_its_prompt_reads_as_waiting(registry: Registry, tmp_path: Path) -> None:
    _session, term = await _pane(registry, tmp_path)

    _draw(term, IDLE_SCREEN)
    _quiet_since(term, 1000.0)
    assert read_activity(term, now=1030.0) == "waiting"


async def test_a_visible_question_reads_as_asking(registry: Registry, tmp_path: Path) -> None:
    """Told apart from "finished" because the two want different words."""
    _session, term = await _pane(registry, tmp_path)

    _draw(term, QUESTION_SCREEN)
    _quiet_since(term, 1000.0)
    assert read_activity(term, now=1030.0) == "asking"


async def test_a_pane_with_no_agent_yet_is_not_idle(registry: Registry, tmp_path: Path) -> None:
    """A pane waiting for a cold-start slot has not finished anything."""
    session = await registry.start(str(tmp_path), [{"agent": "claude", "name": "Alex"}])

    assert read_activity(session.terminals[0]) == "starting"


# --------------------------------------------------------------- transitions
async def test_a_pane_that_stops_working_is_reported_once(
    registry: Registry, tmp_path: Path
) -> None:
    """The whole feature, in one test: busy → quiet → exactly one entry."""
    watcher = notifications.watcher()
    session, term = await _pane(registry, tmp_path)

    stopped = _busy(watcher, registry, term, start=100.0)

    # The picture now stops changing. Standing still is not instantly "finished"
    # — a pane between two steps looks exactly the same for a moment.
    assert watcher.poll(registry, now=stopped + 1) == []
    # Still enough to count as settled, but the settle window has not run.
    assert watcher.poll(registry, now=stopped + STILL_S + 1) == []

    filed = watcher.poll(registry, now=stopped + STILL_S + notifications.SETTLE_S + 2)
    assert [entry.kind for entry in filed] == ["completed"]
    assert filed[0].pane == term.name
    assert filed[0].workspace_id == session.id

    # And never again for the same stop, however long the pane sits there.
    assert watcher.poll(registry, now=400.0) == []


async def test_only_observed_work_is_checkpointed_for_restart(
    registry: Registry, tmp_path: Path
) -> None:
    """The Continue badge survives only the state that proves an interruption."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)

    stopped = _busy(watcher, registry, term, start=100.0)
    assert term.resume_continuation_needed is True
    assert watcher.take_resume_dirty() is True
    assert notifications.center().list() == [], "the bell switch does not disable checkpointing"

    # The screen stops moving, but not for long enough yet: a pane between two
    # steps must stay armed, or a restart would not offer to continue it.
    term.transcript.clear()
    _draw(term, IDLE_SCREEN)
    watcher.poll(registry, now=stopped + 1, emit=False)
    assert term.resume_continuation_needed is True, "a repaint gap must stay armed"
    assert watcher.take_resume_dirty() is False

    # Two waits stack, and both are load-bearing: the screen has to stand still
    # long enough to count as settled at all (STILL_S), and only then does the
    # settle window for "this really is over" start running (SETTLE_S).
    settled_at = stopped + STILL_S + 2
    watcher.poll(registry, now=settled_at, emit=False)
    watcher.poll(registry, now=settled_at + notifications.SETTLE_S + 1, emit=False)
    assert term.resume_continuation_needed is False
    assert watcher.take_resume_dirty() is True


async def test_a_question_clears_the_restart_checkpoint_immediately(
    registry: Registry, tmp_path: Path
) -> None:
    """A question needs the user's answer; "continue" would be the wrong input."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    stopped = _busy(watcher, registry, term, start=100.0)
    watcher.take_resume_dirty()

    _rest(watcher, registry, term, QUESTION_SCREEN, at=stopped + 2)

    assert term.resume_continuation_needed is False
    assert watcher.take_resume_dirty() is True


async def test_a_restored_pane_painting_itself_keeps_its_continue_offer(
    registry: Registry, tmp_path: Path
) -> None:
    """The regression that broke the Continue button outright.

    A restored CLI redraws its banner and its old transcript for several seconds
    before it settles, and this detector reads MOVEMENT — so that burst is
    frame-for-frame an agent at work. Retracting `continuation_pending` for it
    took every restored pane off the Continue list within two sweeps of coming
    back, and the dialog reported "nothing was interrupted" for ever.

    Movement only counts once the pane has been SEEN standing still.
    """
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    # What a restore leaves behind: the pane picked its conversation back up and
    # nobody has told it anything since.
    term.continuation_pending = True
    assert term.idle_seen is False, "a freshly spawned process has settled nothing"

    _busy(watcher, registry, term, start=100.0, tasked=False)

    assert term.continuation_pending is True, "a CLI drawing itself is not the agent working"


async def test_a_new_process_for_the_same_pane_discards_the_old_screen_observation(
    registry: Registry, tmp_path: Path
) -> None:
    """A replacement PTY must not inherit the previous process's still screen."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)

    _rest(watcher, registry, term, IDLE_SCREEN, at=100.0, emit=False)
    assert term.idle_seen is True

    term.continuation_pending = True
    term.resume_continuation_needed = False
    term.process_generation += 1
    term.idle_seen = False

    # A replacement CLI initially redraws the same prompt as its predecessor.
    watcher.poll(registry, now=200.0, emit=False)
    assert term.idle_seen is False
    assert term.continuation_pending is True
    assert term.resume_continuation_needed is False

    term.transcript.clear()
    _draw(term, BUSY_SCREEN)
    watcher.poll(registry, now=200.0 + notifications.SWEEP_INTERVAL_S, emit=False)
    assert term.continuation_pending is True
    assert term.resume_continuation_needed is False


async def test_a_new_submission_after_settle_retracts_the_offer(
    registry: Registry, tmp_path: Path
) -> None:
    """A generation-stamped task, not prior stillness, proves resumed work."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    term.continuation_pending = True

    # It comes up, paints itself, and settles at its prompt.
    _rest(watcher, registry, term, IDLE_SCREEN, at=100.0, emit=False)
    assert term.idle_seen is True
    assert term.continuation_pending is True, "sitting at a prompt is what waiting looks like"

    _busy(watcher, registry, term, start=100.0 + STILL_S + 2)

    assert term.continuation_pending is False


async def test_a_pane_that_was_never_busy_is_never_reported(
    registry: Registry, tmp_path: Path
) -> None:
    """Otherwise every workspace would ring its own bell the moment it opened.

    The failure this pins is not hypothetical for a "pane is idle" rule: a
    freshly attached pane sits at its prompt for the seconds before its first
    instruction, which under that rule is a finished job per pane per open.
    """
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    _draw(term, IDLE_SCREEN)

    for moment in (100.0, 110.0, 200.0, 900.0):
        assert watcher.poll(registry, now=moment) == []


async def test_a_starting_cli_painting_itself_is_not_a_finished_job(
    registry: Registry, tmp_path: Path
) -> None:
    """The bug this rule exists for, in the form the user actually hit.

    Open a workspace and the CLIs in it draw themselves: a logo, a model line,
    an authentication banner. That is a screen moving and then stopping, which
    is frame for frame what an agent finishing a job looks like — so the bell
    filed "Finished and waiting at its prompt" for every pane in a workspace
    nobody had typed a word into, and twice for the panes whose startup drawing
    arrived in two bursts.

    Movement alone can never mean this again: the pane has to have been GIVEN
    something to do first.
    """
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    watcher.poll(registry, now=100.0)

    # The banner arrives in two bursts, exactly as a real cold start does.
    for step, chunk in enumerate(("\r\n           Claude Code v2.1.220\r\n", REAL_STARTUP_BANNER)):
        term.transcript.clear()
        _draw(term, chunk)
        assert watcher.poll(registry, now=102.0 + step * 2) == []

    # And then it stands still at its prompt, for as long as anybody looks.
    for moment in (110.0, 120.0, 600.0):
        assert watcher.poll(registry, now=moment) == []
    assert notifications.center().list() == []


async def test_a_prompt_typed_by_hand_arms_the_report(registry: Registry, tmp_path: Path) -> None:
    """The other side of that rule — and the half a `send_prompt` check misses.

    A pane driven by a person pressing Enter in it never goes through Jarvis'
    injection path, so a rule that only counted prompts Jarvis sent would leave
    the bell permanently silent for anybody who types their own.
    """
    watcher = notifications.watcher()
    session, term = await _pane(registry, tmp_path)
    watcher.poll(registry, now=100.0)
    assert term.prompts_sent == 0, "the point: nothing went through send_prompt"

    registry.write(term.key, "run the tests\r", workspace_id=session.id)

    stopped = _busy(watcher, registry, term, start=200.0)
    watcher.poll(registry, now=stopped + STILL_S + 1)
    filed = watcher.poll(registry, now=stopped + STILL_S + notifications.SETTLE_S + 2)

    assert [entry.kind for entry in filed] == ["completed"]


async def test_arrow_keys_and_half_typed_lines_are_not_an_instruction(
    registry: Registry, tmp_path: Path
) -> None:
    """Typing is not submitting, or reading a prompt back would arm the bell."""
    watcher = notifications.watcher()
    session, term = await _pane(registry, tmp_path)
    watcher.poll(registry, now=100.0)

    registry.write(term.key, "run the te", workspace_id=session.id)

    assert term.last_submit_at is None
    stopped = _busy(watcher, registry, term, start=200.0)
    # `_busy` stamps the pane, so undo that here — this test is about the pane
    # NOT having been given anything, with a screen that moved regardless.
    term.last_submit_at = None
    watcher._panes[(session.id, term.key)].tasked = False
    watcher.poll(registry, now=stopped + STILL_S + 1)

    assert watcher.poll(registry, now=stopped + STILL_S + notifications.SETTLE_S + 2) == []


async def test_a_repaint_gap_does_not_file_a_notification(
    registry: Registry, tmp_path: Path
) -> None:
    """A TUI drops its hint for a moment between steps — that is not "finished".

    Without the settle window this fires several times a minute for a pane that
    is working normally, which is the version of this feature nobody would leave
    switched on.
    """
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)

    _draw(term, BUSY_SCREEN)
    watcher.poll(registry, now=100.0)
    # The hint is gone for one sweep...
    term.transcript.clear()
    _draw(term, IDLE_SCREEN)
    assert watcher.poll(registry, now=101.0) == []
    # ...and back before the settle window elapses.
    term.transcript.clear()
    _draw(term, BUSY_SCREEN)
    assert watcher.poll(registry, now=102.0) == []
    assert watcher.poll(registry, now=100.0 + notifications.SETTLE_S + 5) == []


async def test_a_lone_printout_into_an_instructed_pane_files_no_finished_bell(
    registry: Registry, tmp_path: Path
) -> None:
    """The other half of the /recap fix. The badge learned to sit still for a
    single burst, but the bell still rang "finished" for a pane whose agent did
    nothing: the raw reading flipped waiting->working->waiting and `worked`
    latched on the way down. One burst is not a confirmed episode of work — for
    either of them."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    # Instructed long ago — far outside the submit wake — and long finished.
    term.last_submit_at = 500.0
    term.submit_generation = term.process_generation
    _quiet_since(term, 999.0)
    _draw(term, IDLE_SCREEN)
    watcher.poll(registry, now=1000.0)
    watcher.poll(registry, now=1002.0)

    # One printout: /recap output, a menu, anything changing the picture once.
    term.transcript.clear()
    _draw(term, "\r\nRecap: three files changed, tests green\r\n")
    term.last_output_at = 1004.0
    watcher.poll(registry, now=1004.0)
    assert term.activity == "waiting", "the badge half of the fix still holds"

    # The burst's tail dies, the pane settles, the settle window passes.
    watcher.poll(registry, now=1004.0 + STILL_S + 1)
    filed = watcher.poll(registry, now=1004.0 + STILL_S + notifications.SETTLE_S + 2)

    assert filed == []


async def test_a_confirmed_episode_is_stamped_from_when_movement_began(
    registry: Registry, tmp_path: Path
) -> None:
    """The badge's "For Ns" must count the episode, not the gate. Confirmation
    spends the first WORK_CONFIRM_S of every non-wake episode; stamping the
    hand-over as a fresh transition told the user work "just started" about a
    pane that had visibly been repainting for six seconds."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    term.last_submit_at = 500.0
    term.submit_generation = term.process_generation
    _quiet_since(term, 999.0)
    _draw(term, IDLE_SCREEN)
    watcher.poll(registry, now=996.0)

    for step, at in enumerate((1000.0, 1002.0, 1004.0, 1006.0)):
        term.transcript.clear()
        _draw(term, f"\r\n· working, frame {step}\r\n")
        term.last_output_at = at
        watcher.poll(registry, now=at)

    assert term.activity == "working"
    assert term.activity_since == 1000.0, "the episode began with the movement, not the gate"


async def test_a_tool_step_gap_does_not_demand_reconfirmation(
    registry: Registry, tmp_path: Path
) -> None:
    """A coding TUI pauses between tool steps. Movement resuming right after a
    confirmed episode is the same job continuing — demanding a fresh
    WORK_CONFIRM_S turned one long job into repeated false "waiting" dips."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    term.last_submit_at = 500.0
    term.submit_generation = term.process_generation
    _quiet_since(term, 999.0)
    _draw(term, IDLE_SCREEN)
    watcher.poll(registry, now=996.0)

    for step, at in enumerate((1000.0, 1002.0, 1004.0, 1006.0, 1008.0)):
        term.transcript.clear()
        _draw(term, f"\r\n· working, frame {step}\r\n")
        term.last_output_at = at
        watcher.poll(registry, now=at)
    assert term.activity == "working", "sustained movement confirms"

    # A quiet tool step: the screen stands still past its freshness tail.
    watcher.poll(registry, now=1008.0 + STILL_S + 1)
    assert term.activity == "waiting"

    # Work resumes. The badge must follow on the SAME sweep, not five later.
    term.transcript.clear()
    _draw(term, "\r\n· working again after the tool call\r\n")
    term.last_output_at = 1015.0
    watcher.poll(registry, now=1015.0)

    assert term.activity == "working", "a resuming job needs no re-confirmation"


async def test_a_question_is_reported_even_though_it_never_worked(
    registry: Registry, tmp_path: Path
) -> None:
    """A CLI that asks something at startup has never been busy — and still needs you."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    _draw(term, IDLE_SCREEN)
    watcher.poll(registry, now=100.0)

    # The question APPEARING is not work, so its own settle window starts now.
    term.transcript.clear()
    _draw(term, QUESTION_SCREEN)
    watcher.poll(registry, now=101.0)
    filed = watcher.poll(registry, now=101.0 + STILL_S + 1)

    assert [entry.kind for entry in filed] == ["needs_input"]


async def test_going_back_to_work_re_arms_the_report(registry: Registry, tmp_path: Path) -> None:
    """A pane given a second job reports its second stop too."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)

    for round_start in (100.0, 500.0):
        stopped = _busy(watcher, registry, term, start=round_start)
        watcher.poll(registry, now=stopped + STILL_S + 1)
        filed = watcher.poll(registry, now=stopped + STILL_S + notifications.SETTLE_S + 2)
        assert [entry.kind for entry in filed] == ["completed"], round_start


async def test_a_dead_agent_is_reported_without_waiting(registry: Registry, tmp_path: Path) -> None:
    """There is no repaint gap to sit out — the state cannot flip back by itself."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    watcher.poll(registry, now=100.0)

    term.status = "exited"
    term.exit_code = 1
    filed = watcher.poll(registry, now=100.5)

    assert [entry.kind for entry in filed] == ["exited"]
    assert "1" in filed[0].title


async def test_the_first_sweep_never_files_history(registry: Registry, tmp_path: Path) -> None:
    """A pane that has been finished for an hour is not news on the first look."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    _draw(term, IDLE_SCREEN)

    assert watcher.poll(registry, now=100.0) == []
    assert notifications.center().list() == []


# --------------------------------------------------------------------- store
def test_reading_only_stops_the_count_not_the_list() -> None:
    center = notifications.NotificationCenter()
    center.add(_entry("n1"))
    center.add(_entry("n2"))
    assert center.unread == 2

    changed = center.mark_read()

    assert changed == 2
    assert center.unread == 0
    assert len(center.list()) == 2, "the entries stay — only the badge goes quiet"


def test_the_list_is_newest_first() -> None:
    center = notifications.NotificationCenter()
    center.add(_entry("n1"))
    center.add(_entry("n2"))

    assert [entry.id for entry in center.list()] == ["n2", "n1"]


def test_the_store_is_bounded() -> None:
    """A long day must not grow the process."""
    center = notifications.NotificationCenter(limit=3)
    for index in range(10):
        center.add(_entry(f"n{index}"))

    assert len(center.list()) == 3
    assert [entry.id for entry in center.list()] == ["n9", "n8", "n7"]


async def _file_one(watcher: Any, registry: Registry, term: Any, *, start: float) -> None:
    """Put exactly one ``completed`` entry in the bell for ``term``."""
    stopped = _busy(watcher, registry, term, start=start)
    watcher.poll(registry, now=stopped + STILL_S + 1)
    filed = watcher.poll(registry, now=stopped + STILL_S + notifications.SETTLE_S + 2)
    assert [entry.pane for entry in filed] == [term.name]


async def test_closing_a_pane_takes_its_notifications_with_it(
    registry: Registry, tmp_path: Path
) -> None:
    """An entry is a "jump to this pane" button and the pane has just gone.

    The workspace stays open, so the entry used to stay with it — the bell kept
    counting terminals the user could no longer see, and pressing one of them
    did nothing at all.
    """
    watcher = notifications.watcher()
    session = await registry.start(
        str(tmp_path),
        [{"agent": "claude", "name": "T1"}, {"agent": "claude", "name": "T2"}],
    )
    first = await registry.attach("T1", 100, 30, _noop, _noop_exit)
    second = await registry.attach("T2", 100, 30, _noop, _noop_exit)
    await _file_one(watcher, registry, first, start=100.0)
    await _file_one(watcher, registry, second, start=500.0)
    assert {entry.pane for entry in notifications.center().list()} == {"T1", "T2"}

    await registry.close_terminal("T2")

    assert [entry.pane for entry in notifications.center().list()] == ["T1"]
    assert session.id


async def test_a_new_pane_never_inherits_the_closed_ones_entries(
    registry: Registry, tmp_path: Path
) -> None:
    """Call-signs are handed back out, and with them the pane KEY.

    So an entry left behind by a closed T2 does not merely dangle — it
    re-attaches itself to whatever opens as T2 next, and "jump to pane" lands
    somewhere plausible and wrong. This is why the close path clears them
    itself instead of leaving it to the next sweep.
    """
    watcher = notifications.watcher()
    await registry.start(str(tmp_path), [{"agent": "claude", "name": "T1"}])
    first = await registry.attach("T1", 100, 30, _noop, _noop_exit)
    await _file_one(watcher, registry, first, start=100.0)

    await registry.close_terminal("T1")
    await registry.add_terminal(agent="claude", name="T1")
    reborn = await registry.attach("T1", 100, 30, _noop, _noop_exit)

    assert reborn.key == first.key, "the premise: the key came back with the name"
    assert notifications.center().list() == []


async def test_an_entry_follows_the_pane_when_it_is_renamed(
    registry: Registry, tmp_path: Path
) -> None:
    """Otherwise it names a terminal that is not on screen under that name.

    The rename is applied to the pane directly rather than through whichever
    route offers it, because the claim under test belongs to the sweep: an
    entry carries a LABEL, and a label is refreshed from the pane rather than
    frozen at the moment it was filed.
    """
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path, name="T1")
    await _file_one(watcher, registry, term, start=100.0)

    term.name = "Reviewer"
    watcher.poll(registry, now=900.0)

    assert [entry.pane for entry in notifications.center().list()] == ["Reviewer"]


async def test_closing_a_workspace_takes_its_notifications_with_it(
    registry: Registry, tmp_path: Path
) -> None:
    """Each entry is a "jump to this pane" button, and the panes have just died."""
    watcher = notifications.watcher()
    session, term = await _pane(registry, tmp_path)
    stopped = _busy(watcher, registry, term, start=100.0)
    watcher.poll(registry, now=stopped + STILL_S + 1)
    watcher.poll(registry, now=stopped + STILL_S + notifications.SETTLE_S + 2)
    assert notifications.center().list() != []

    await registry.end(session.id)

    assert notifications.center().list() == []


def _entry(entry_id: str) -> notifications.Notification:
    return notifications.Notification(
        id=entry_id,
        kind="completed",
        workspace_id="ws1",
        workspace="Demo",
        pane_key="t1",
        pane="T1",
        agent="claude",
        display_name="Claude Code",
        title="Finished and waiting at its prompt",
        detail="",
        created_at=1.0,
    )
