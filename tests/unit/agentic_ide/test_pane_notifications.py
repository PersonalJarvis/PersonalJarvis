"""The bell: which terminals stopped while nobody was looking at them.

A workspace runs a dozen coding agents in postcard-sized panes, and none of them
announces that it is done — the spinner row simply stops being drawn. So the
feature is entirely about the TRANSITION, and every test here is about getting
that edge right rather than about the store around it:

* a pane that was busy and went quiet is reported ONCE,
* a pane that has been idle since it appeared is never reported at all,
* the gap a TUI leaves between two steps does not produce a notification,
* and an entry never outlives the workspace whose pane it points at.

Each of those has an obvious wrong implementation that looks correct in a demo
and is unusable in a grid of twelve.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.agentic_ide import notifications
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.activity import read_activity
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


async def _pane(registry: Registry, folder: Path, *, name: str = "Alex"):
    """One live pane in one workspace, through the real attach path."""
    session = await registry.start(str(folder), [{"agent": "claude", "name": name}])
    term = await registry.attach(name, 100, 30, _noop, _noop_exit)
    return session, term


def _draw(term: Any, screen: str) -> None:
    """Put ``screen`` on the pane's replayed terminal, as its agent would."""
    term.transcript.feed(screen)


# ------------------------------------------------- reading the REAL terminals
async def test_a_real_working_pane_is_seen_working(
    registry: Registry, tmp_path: Path
) -> None:
    """The row this build actually draws while it works — no interrupt hint in it.

    This is the regression that made the feature do nothing on the machine it
    was built for: the pane is plainly busy and the detector called it idle.
    """
    _session, term = await _pane(registry, tmp_path)
    _draw(term, REAL_WORKING)
    term.last_output_at = 1000.0

    assert read_activity(term, now=1001.0) == "working"


async def test_a_real_thinking_pane_is_seen_working(
    registry: Registry, tmp_path: Path
) -> None:
    """No tokens, no hint — just the clock. Still working."""
    _session, term = await _pane(registry, tmp_path)
    _draw(term, REAL_THINKING)
    term.last_output_at = 1000.0

    assert read_activity(term, now=1001.0) == "working"


async def test_a_real_finished_pane_is_seen_waiting(
    registry: Registry, tmp_path: Path
) -> None:
    """"Worked for 13m 27s" states a duration; it does not tick."""
    _session, term = await _pane(registry, tmp_path)
    _draw(term, REAL_FINISHED)
    term.last_output_at = 1000.0

    assert read_activity(term, now=1001.0) == "waiting"


async def test_a_stale_clock_is_not_a_running_one(
    registry: Registry, tmp_path: Path
) -> None:
    """A bracketed duration on a screen that stopped moving is text, not a clock.

    Without this pairing, a frozen last frame — or an agent quoting "(3s)" in
    its own answer — would read as an agent that is still working, and the pane
    would never be reported at all.
    """
    _session, term = await _pane(registry, tmp_path)
    _draw(term, REAL_WORKING)
    term.last_output_at = 1000.0

    assert read_activity(term, now=1000.0 + 60) == "waiting"


async def test_the_startup_banner_is_not_a_question(
    registry: Registry, tmp_path: Path
) -> None:
    """It sits there for the pane's whole life — a standing banner, not an ask."""
    _session, term = await _pane(registry, tmp_path)
    _draw(term, REAL_STARTUP_BANNER)
    term.last_output_at = 1000.0

    assert read_activity(term, now=1001.0) == "waiting"


async def test_a_real_pane_finishing_files_one_entry(
    registry: Registry, tmp_path: Path
) -> None:
    """End to end on the real rows: working → finished → exactly one entry."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)

    _draw(term, REAL_WORKING)
    term.last_output_at = 1000.0
    assert watcher.poll(registry, now=1000.5) == []

    term.transcript.clear()
    _draw(term, REAL_FINISHED)
    term.last_output_at = 1002.0
    assert watcher.poll(registry, now=1002.5) == []

    filed = watcher.poll(registry, now=1002.5 + notifications.SETTLE_S + 1)

    assert [entry.kind for entry in filed] == ["completed"]


# ------------------------------------------------------------------- reading
async def test_the_interrupt_hint_is_what_marks_a_pane_busy(
    registry: Registry, tmp_path: Path
) -> None:
    """The one signal every coding TUI draws while — and only while — it works."""
    _session, term = await _pane(registry, tmp_path)

    _draw(term, BUSY_SCREEN)
    assert read_activity(term) == "working"


async def test_a_pane_at_its_prompt_reads_as_waiting(
    registry: Registry, tmp_path: Path
) -> None:
    _session, term = await _pane(registry, tmp_path)

    _draw(term, IDLE_SCREEN)
    assert read_activity(term) == "waiting"


async def test_a_visible_question_reads_as_asking(registry: Registry, tmp_path: Path) -> None:
    """Told apart from "finished" because the two want different words."""
    _session, term = await _pane(registry, tmp_path)

    _draw(term, QUESTION_SCREEN)
    assert read_activity(term) == "asking"


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

    _draw(term, BUSY_SCREEN)
    assert watcher.poll(registry, now=100.0) == []

    # A real TUI repaints the row it drew the hint on; the fake screen is told
    # to do the same rather than letting the old row scroll along underneath.
    term.transcript.clear()
    _draw(term, IDLE_SCREEN)
    # Seen as settled, but not yet for long enough to be believed.
    assert watcher.poll(registry, now=101.0) == []

    filed = watcher.poll(registry, now=100.0 + notifications.SETTLE_S + 2)
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

    _draw(term, BUSY_SCREEN)
    watcher.poll(registry, now=100.0, emit=False)
    assert term.resume_continuation_needed is True
    assert watcher.take_resume_dirty() is True
    assert notifications.center().list() == [], "the bell switch does not disable checkpointing"

    term.transcript.clear()
    _draw(term, IDLE_SCREEN)
    watcher.poll(registry, now=101.0, emit=False)
    assert term.resume_continuation_needed is True, "a repaint gap must stay armed"
    assert watcher.take_resume_dirty() is False

    watcher.poll(registry, now=101.0 + notifications.SETTLE_S + 1, emit=False)
    assert term.resume_continuation_needed is False
    assert watcher.take_resume_dirty() is True


async def test_a_question_clears_the_restart_checkpoint_immediately(
    registry: Registry, tmp_path: Path
) -> None:
    """A question needs the user's answer; "continue" would be the wrong input."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    _draw(term, BUSY_SCREEN)
    watcher.poll(registry, now=100.0)
    watcher.take_resume_dirty()

    term.transcript.clear()
    _draw(term, QUESTION_SCREEN)
    watcher.poll(registry, now=101.0)

    assert term.resume_continuation_needed is False
    assert watcher.take_resume_dirty() is True


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


async def test_a_question_is_reported_even_though_it_never_worked(
    registry: Registry, tmp_path: Path
) -> None:
    """A CLI that asks something at startup has never been busy — and still needs you."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)
    _draw(term, IDLE_SCREEN)
    watcher.poll(registry, now=100.0)

    term.transcript.clear()
    _draw(term, QUESTION_SCREEN)
    watcher.poll(registry, now=101.0)
    filed = watcher.poll(registry, now=101.0 + notifications.SETTLE_S + 1)

    assert [entry.kind for entry in filed] == ["needs_input"]


async def test_going_back_to_work_re_arms_the_report(
    registry: Registry, tmp_path: Path
) -> None:
    """A pane given a second job reports its second stop too."""
    watcher = notifications.watcher()
    _session, term = await _pane(registry, tmp_path)

    for round_start in (100.0, 500.0):
        term.transcript.clear()
        _draw(term, BUSY_SCREEN)
        watcher.poll(registry, now=round_start)
        term.transcript.clear()
        _draw(term, IDLE_SCREEN)
        watcher.poll(registry, now=round_start + 1)
        filed = watcher.poll(registry, now=round_start + notifications.SETTLE_S + 2)
        assert [entry.kind for entry in filed] == ["completed"], round_start


async def test_a_dead_agent_is_reported_without_waiting(
    registry: Registry, tmp_path: Path
) -> None:
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


async def test_closing_a_workspace_takes_its_notifications_with_it(
    registry: Registry, tmp_path: Path
) -> None:
    """Each entry is a "jump to this pane" button, and the panes have just died."""
    watcher = notifications.watcher()
    session, term = await _pane(registry, tmp_path)
    _draw(term, BUSY_SCREEN)
    watcher.poll(registry, now=100.0)
    term.transcript.clear()
    _draw(term, IDLE_SCREEN)
    watcher.poll(registry, now=101.0)
    watcher.poll(registry, now=100.0 + notifications.SETTLE_S + 2)
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
