"""Tests fuer jarvis.awareness.salience — SalienceScorer.

Pure-Function-Tests, keine Bus / kein I/O. Spec: Plan §6
"Salience Scorer (verbindlich, rule-based)".
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
    """Bequemer Constructor — alle Felder mit sinnvollen Defaults."""
    return FrameSnapshot(
        timestamp_ns=timestamp_ns,
        active_window_title=title,
        active_process_name=process,
        active_pid=pid,
        is_capture_allowed=True,
        git_branch=git_branch,
    )


# --- score_frame: prev=None ------------------------------------------------

def test_score_frame_no_prev_returns_neutral() -> None:
    """Erster Frame ohne prev → deterministisch ein Mid-Score-Default."""
    scorer = SalienceScorer()
    frame = _make_frame()

    score = scorer.score_frame(frame, prev=None)

    # Default Base = 50, kein BORING, kein Bonus → 50.
    assert score == 50


def test_score_frame_no_prev_boring_still_penalised() -> None:
    """Auch ohne prev gewinnt die BORING-Penalty."""
    scorer = SalienceScorer()
    frame = _make_frame(process="explorer.exe")

    score = scorer.score_frame(frame, prev=None)

    # 50 (base) - 50 (BORING) = 0.
    assert score == 0


# --- score_frame: deltas ---------------------------------------------------

def test_score_frame_process_switch_adds_20() -> None:
    """Process-Wechsel ⇒ +20 (App-Switch)."""
    scorer = SalienceScorer()
    prev = _make_frame(process="notepad.exe", title="A.txt")
    frame = _make_frame(process="code.exe", title="B.py", timestamp_ns=2_000_000_000)

    score = scorer.score_frame(frame, prev=prev)

    # Process changed = +20. Title ungleich, aber das wird nur gezaehlt
    # wenn Process gleich bleibt — sonst Doppelzaehlung.
    assert score == 20


def test_score_frame_title_switch_same_process_adds_30() -> None:
    """Title-Wechsel bei gleichem Process ⇒ +30 (Datei-/Tab-Wechsel)."""
    scorer = SalienceScorer()
    prev = _make_frame(process="code.exe", title="main.py - VS Code")
    frame = _make_frame(
        process="code.exe", title="utils.py - VS Code", timestamp_ns=2_000_000_000,
    )

    score = scorer.score_frame(frame, prev=prev)

    assert score == 30


def test_score_frame_git_branch_change_adds_20() -> None:
    """Git-Branch-Wechsel ⇒ +20 (beide non-None erforderlich)."""
    scorer = SalienceScorer()
    prev = _make_frame(git_branch="main")
    frame = _make_frame(
        git_branch="feature/x", timestamp_ns=2_000_000_000,
    )

    score = scorer.score_frame(frame, prev=prev)

    # Process+Title gleich ⇒ keine Boni dort, nur Branch +20.
    assert score == 20


def test_score_frame_git_branch_one_none_no_bonus() -> None:
    """git_branch nur in einem Frame gesetzt ⇒ kein Bonus."""
    scorer = SalienceScorer()
    prev = _make_frame(git_branch=None)
    frame = _make_frame(git_branch="main", timestamp_ns=2_000_000_000)

    score = scorer.score_frame(frame, prev=prev)

    assert score == 0


def test_score_frame_long_dwell_adds_10() -> None:
    """Frame >2min nach prev ⇒ +10 (Verweildauer)."""
    scorer = SalienceScorer()
    prev = _make_frame(timestamp_ns=0)
    frame = _make_frame(timestamp_ns=121_000_000_000)    # >2min

    score = scorer.score_frame(frame, prev=prev)

    # Process+Title gleich ⇒ nur Dwell-Bonus.
    assert score == 10


def test_score_frame_short_dwell_no_bonus() -> None:
    """Frame <=2min nach prev ⇒ kein Dwell-Bonus."""
    scorer = SalienceScorer()
    prev = _make_frame(timestamp_ns=0)
    frame = _make_frame(timestamp_ns=120_000_000_000)    # exakt 2min

    score = scorer.score_frame(frame, prev=prev)

    assert score == 0


# --- score_frame: BORING-Penalty ------------------------------------------

def test_score_frame_boring_process_subtracts_50() -> None:
    """Explorer.exe ⇒ -50 Penalty (zieht den Score nach unten)."""
    scorer = SalienceScorer()
    prev = _make_frame(process="code.exe")
    # Process-Switch ⇒ +20, dann -50 ⇒ -30 ⇒ clamped zu 0.
    frame = _make_frame(process="explorer.exe", timestamp_ns=2_000_000_000)

    score = scorer.score_frame(frame, prev=prev)

    assert score == 0


def test_score_frame_case_insensitive_boring() -> None:
    """BORING_PROCESSES matchen case-insensitive."""
    scorer = SalienceScorer()
    prev = _make_frame(process="code.exe")

    # Beide Variants muessen denselben Score erzeugen.
    frame_lower = _make_frame(process="explorer.exe", timestamp_ns=2_000_000_000)
    frame_upper = _make_frame(process="Explorer.exe", timestamp_ns=2_000_000_000)
    frame_mixed = _make_frame(process="ExPlOrEr.ExE", timestamp_ns=2_000_000_000)

    s_lower = scorer.score_frame(frame_lower, prev=prev)
    s_upper = scorer.score_frame(frame_upper, prev=prev)
    s_mixed = scorer.score_frame(frame_mixed, prev=prev)

    assert s_lower == s_upper == s_mixed == 0


def test_score_frame_clamped_to_0_100() -> None:
    """Extreme Combos respektieren die [0, 100]-Klammer."""
    scorer = SalienceScorer()
    # Alle Boni: process+20, branch+20, dwell+10 = +50 (max realistisch).
    # Wir koennen score nicht > 100 forcen via score_frame — daher Test
    # primaer fuer untere Grenze (BORING + No-Boni).
    prev = _make_frame(process="code.exe", git_branch="main")
    frame = _make_frame(
        process="explorer.exe",        # +20 process, -50 BORING
        title="other",
        git_branch="main",             # gleich, kein Bonus
        timestamp_ns=2_000_000_000,    # short dwell
    )

    score = scorer.score_frame(frame, prev=prev)

    # Score muss in [0, 100] liegen, hier konkret 0 (clamped from -30).
    assert 0 <= score <= 100
    assert score == 0


def test_score_frame_max_combo_not_above_100() -> None:
    """Auch der Maximal-Boni-Stack bleibt <= 100."""
    scorer = SalienceScorer()
    prev = _make_frame(
        process="notepad.exe", title="A.txt", git_branch="main", timestamp_ns=0,
    )
    # Process-Wechsel (+20), branch (+20), dwell >2min (+10), kein BORING.
    # Title-Wechsel zaehlt nicht weil Process bereits wechselt.
    frame = _make_frame(
        process="code.exe",
        title="main.py",
        git_branch="feature/x",
        timestamp_ns=200_000_000_000,
    )

    score = scorer.score_frame(frame, prev=prev)

    assert score == 50    # 20 + 20 + 10
    assert 0 <= score <= 100


# --- score_event ------------------------------------------------------------

def test_score_event_known_kinds() -> None:
    """FileSaved=40, BrainTurnCompleted=50, IdleExited=20."""
    scorer = SalienceScorer()

    assert scorer.score_event("FileSaved") == 40
    assert scorer.score_event("BrainTurnCompleted") == 50
    assert scorer.score_event("IdleExited") == 20


def test_score_event_terminal_exit_with_payload() -> None:
    """TerminalExit: exit_code=0 ⇒ 20, sonst ⇒ 60."""
    scorer = SalienceScorer()

    assert scorer.score_event("TerminalExit", {"exit_code": 0}) == 20
    assert scorer.score_event("TerminalExit", {"exit_code": 1}) == 60
    assert scorer.score_event("TerminalExit", {"exit_code": 127}) == 60


def test_score_event_terminal_exit_default_zero_exit_code() -> None:
    """TerminalExit ohne payload ⇒ default exit_code=0 ⇒ 20."""
    scorer = SalienceScorer()

    assert scorer.score_event("TerminalExit") == 20
    assert scorer.score_event("TerminalExit", None) == 20
    assert scorer.score_event("TerminalExit", {}) == 20


def test_score_event_unknown_default_zero() -> None:
    """Unbekannter event_kind ⇒ 0."""
    scorer = SalienceScorer()

    assert scorer.score_event("WhateverElse") == 0
    assert scorer.score_event("") == 0
    assert scorer.score_event("RandomEventName", {"foo": "bar"}) == 0


# --- Module-level constants -------------------------------------------------

def test_threshold_constant_value() -> None:
    """SALIENCE_THRESHOLD ist 30 (Plan §6)."""
    assert SALIENCE_THRESHOLD == 30


def test_boring_processes_lowercase() -> None:
    """Alle BORING_PROCESSES-Eintraege sind lowercase
    (Caller .lower()-vergleicht — sonst silently no-match)."""
    for entry in BORING_PROCESSES:
        assert entry == entry.lower(), f"BORING entry not lowercase: {entry!r}"
