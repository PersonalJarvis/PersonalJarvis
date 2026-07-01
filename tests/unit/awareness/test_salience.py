"""Tests for jarvis.awareness.salience — SalienceScorer.

Pure function tests, no bus / no I/O. Spec: plan §6
"Salience Scorer (binding, rule-based)".
"""
from __future__ import annotations

from jarvis.awareness.salience import (
    BORING_PROCESSES,
    SALIENCE_THRESHOLD,
    SalienceScorer,
)
from jarvis.awareness.state import FrameSnapshot


def _make_frame(
    *,
    process: str = "code.exe",
    title: str = "main.py - VS Code",
    pid: int = 1000,
    timestamp_ns: int = 1_000_000_000,
    git_branch: str | None = None,
) -> FrameSnapshot:
    """Convenient constructor — all fields with sensible defaults."""
    return FrameSnapshot(
        timestamp_ns=timestamp_ns,
        active_window_title=title,
        active_process_name=process,
        active_pid=pid,
        is_capture_allowed=True,
        git_branch=git_branch,
    )


# --- score_frame: prev=None --------------------------------------------------

def test_score_frame_no_prev_returns_neutral() -> None:
    """First frame with no prev → deterministically a mid-score default."""
    scorer = SalienceScorer()
    frame = _make_frame()

    score = scorer.score_frame(frame, prev=None)

    # Default base = 50, no BORING, no bonus → 50.
    assert score == 50


def test_score_frame_no_prev_boring_still_penalised() -> None:
    """Even without prev, the BORING penalty still applies."""
    scorer = SalienceScorer()
    frame = _make_frame(process="explorer.exe")

    score = scorer.score_frame(frame, prev=None)

    # 50 (base) - 50 (BORING) = 0.
    assert score == 0


# --- score_frame: deltas ------------------------------------------------------

def test_score_frame_process_switch_adds_20() -> None:
    """Process switch ⇒ +20 (app switch)."""
    scorer = SalienceScorer()
    prev = _make_frame(process="notepad.exe", title="A.txt")
    frame = _make_frame(process="code.exe", title="B.py", timestamp_ns=2_000_000_000)

    score = scorer.score_frame(frame, prev=prev)

    # Process changed = +20. Title differs, but that's only counted
    # when the process stays the same — otherwise it's double-counted.
    assert score == 20


def test_score_frame_title_switch_same_process_adds_30() -> None:
    """Title switch with the same process ⇒ +30 (file/tab switch)."""
    scorer = SalienceScorer()
    prev = _make_frame(process="code.exe", title="main.py - VS Code")
    frame = _make_frame(
        process="code.exe", title="utils.py - VS Code", timestamp_ns=2_000_000_000,
    )

    score = scorer.score_frame(frame, prev=prev)

    assert score == 30


def test_score_frame_git_branch_change_adds_20() -> None:
    """Git branch switch ⇒ +20 (both non-None required)."""
    scorer = SalienceScorer()
    prev = _make_frame(git_branch="main")
    frame = _make_frame(
        git_branch="feature/x", timestamp_ns=2_000_000_000,
    )

    score = scorer.score_frame(frame, prev=prev)

    # Process+title same ⇒ no bonuses there, just branch +20.
    assert score == 20


def test_score_frame_git_branch_one_none_no_bonus() -> None:
    """git_branch set in only one frame ⇒ no bonus."""
    scorer = SalienceScorer()
    prev = _make_frame(git_branch=None)
    frame = _make_frame(git_branch="main", timestamp_ns=2_000_000_000)

    score = scorer.score_frame(frame, prev=prev)

    assert score == 0


def test_score_frame_long_dwell_adds_10() -> None:
    """Frame >2min after prev ⇒ +10 (dwell time)."""
    scorer = SalienceScorer()
    prev = _make_frame(timestamp_ns=0)
    frame = _make_frame(timestamp_ns=121_000_000_000)    # >2min

    score = scorer.score_frame(frame, prev=prev)

    # Process+title same ⇒ only the dwell bonus.
    assert score == 10


def test_score_frame_short_dwell_no_bonus() -> None:
    """Frame <=2min after prev ⇒ no dwell bonus."""
    scorer = SalienceScorer()
    prev = _make_frame(timestamp_ns=0)
    frame = _make_frame(timestamp_ns=120_000_000_000)    # exactly 2min

    score = scorer.score_frame(frame, prev=prev)

    assert score == 0


# --- score_frame: BORING penalty ---------------------------------------------

def test_score_frame_boring_process_subtracts_50() -> None:
    """Explorer.exe ⇒ -50 penalty (pulls the score down)."""
    scorer = SalienceScorer()
    prev = _make_frame(process="code.exe")
    # Process switch ⇒ +20, then -50 ⇒ -30 ⇒ clamped to 0.
    frame = _make_frame(process="explorer.exe", timestamp_ns=2_000_000_000)

    score = scorer.score_frame(frame, prev=prev)

    assert score == 0


def test_score_frame_case_insensitive_boring() -> None:
    """BORING_PROCESSES match case-insensitively."""
    scorer = SalienceScorer()
    prev = _make_frame(process="code.exe")

    # Both variants must produce the same score.
    frame_lower = _make_frame(process="explorer.exe", timestamp_ns=2_000_000_000)
    frame_upper = _make_frame(process="Explorer.exe", timestamp_ns=2_000_000_000)
    frame_mixed = _make_frame(process="ExPlOrEr.ExE", timestamp_ns=2_000_000_000)

    s_lower = scorer.score_frame(frame_lower, prev=prev)
    s_upper = scorer.score_frame(frame_upper, prev=prev)
    s_mixed = scorer.score_frame(frame_mixed, prev=prev)

    assert s_lower == s_upper == s_mixed == 0


def test_score_frame_clamped_to_0_100() -> None:
    """Extreme combos respect the [0, 100] clamp."""
    scorer = SalienceScorer()
    # All bonuses: process+20, branch+20, dwell+10 = +50 (realistic max).
    # We can't force score > 100 via score_frame — so this test is
    # primarily for the lower bound (BORING + no bonuses).
    prev = _make_frame(process="code.exe", git_branch="main")
    frame = _make_frame(
        process="explorer.exe",        # +20 process, -50 BORING
        title="other",
        git_branch="main",             # same, no bonus
        timestamp_ns=2_000_000_000,    # short dwell
    )

    score = scorer.score_frame(frame, prev=prev)

    # Score must lie in [0, 100], here concretely 0 (clamped from -30).
    assert 0 <= score <= 100
    assert score == 0


def test_score_frame_max_combo_not_above_100() -> None:
    """Even the max bonus stack stays <= 100."""
    scorer = SalienceScorer()
    prev = _make_frame(
        process="notepad.exe", title="A.txt", git_branch="main", timestamp_ns=0,
    )
    # Process switch (+20), branch (+20), dwell >2min (+10), no BORING.
    # Title switch doesn't count because the process already switched.
    frame = _make_frame(
        process="code.exe",
        title="main.py",
        git_branch="feature/x",
        timestamp_ns=200_000_000_000,
    )

    score = scorer.score_frame(frame, prev=prev)

    assert score == 50    # 20 + 20 + 10
    assert 0 <= score <= 100


# --- score_event ---------------------------------------------------------------

def test_score_event_known_kinds() -> None:
    """FileSaved=40, BrainTurnCompleted=50, IdleExited=20."""
    scorer = SalienceScorer()

    assert scorer.score_event("FileSaved") == 40
    assert scorer.score_event("BrainTurnCompleted") == 50
    assert scorer.score_event("IdleExited") == 20


def test_score_event_terminal_exit_with_payload() -> None:
    """TerminalExit: exit_code=0 ⇒ 20, otherwise ⇒ 60."""
    scorer = SalienceScorer()

    assert scorer.score_event("TerminalExit", {"exit_code": 0}) == 20
    assert scorer.score_event("TerminalExit", {"exit_code": 1}) == 60
    assert scorer.score_event("TerminalExit", {"exit_code": 127}) == 60


def test_score_event_terminal_exit_default_zero_exit_code() -> None:
    """TerminalExit without a payload ⇒ default exit_code=0 ⇒ 20."""
    scorer = SalienceScorer()

    assert scorer.score_event("TerminalExit") == 20
    assert scorer.score_event("TerminalExit", None) == 20
    assert scorer.score_event("TerminalExit", {}) == 20


def test_score_event_unknown_default_zero() -> None:
    """Unknown event_kind ⇒ 0."""
    scorer = SalienceScorer()

    assert scorer.score_event("WhateverElse") == 0
    assert scorer.score_event("") == 0
    assert scorer.score_event("RandomEventName", {"foo": "bar"}) == 0


# --- Module-level constants ---------------------------------------------------

def test_threshold_constant_value() -> None:
    """SALIENCE_THRESHOLD is 30 (plan §6)."""
    assert SALIENCE_THRESHOLD == 30


def test_boring_processes_lowercase() -> None:
    """All BORING_PROCESSES entries are lowercase
    (the caller compares with .lower() — otherwise a silent no-match)."""
    for entry in BORING_PROCESSES:
        assert entry == entry.lower(), f"BORING entry not lowercase: {entry!r}"
