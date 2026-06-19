"""Tests fuer jarvis.awareness.episode — Episode + EpisodeBuilder.

Spec: Plan §6 + TASKS.md "Slice B".
"""
from __future__ import annotations

import dataclasses
import time

import pytest

from jarvis.awareness.episode import Episode, EpisodeBuilder
from jarvis.awareness.state import FrameSnapshot


def _make_frame(
    *,
    process: str = "code.exe",
    title: str = "main.py",
    pid: int = 1000,
    timestamp_ns: int = 1_000_000_000,
) -> FrameSnapshot:
    """Bequemer Constructor — sinnvolle Defaults."""
    return FrameSnapshot(
        timestamp_ns=timestamp_ns,
        active_window_title=title,
        active_process_name=process,
        active_pid=pid,
        is_capture_allowed=True,
    )


# --- Episode (frozen) -------------------------------------------------------

def test_episode_is_frozen() -> None:
    """Episode ist frozen — kein Re-Assignment moeglich."""
    ep = Episode(
        started_at_ns=1,
        ended_at_ns=2,
        trigger_kind="window_switch",
        summary="hello",
        frame_count=3,
        primary_app="code.exe",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ep.summary = "x"  # type: ignore[misc]


def test_episode_has_slots() -> None:
    """slots=True ⇒ kein __dict__."""
    ep = Episode(
        started_at_ns=1,
        ended_at_ns=2,
        trigger_kind="idle",
        summary="x",
        frame_count=0,
        primary_app="",
    )
    with pytest.raises(AttributeError):
        ep.__dict__  # noqa: B018


def test_episode_token_defaults_zero() -> None:
    """tokens_in / tokens_out default auf 0 (Empty-Episode oder Timeout)."""
    ep = Episode(
        started_at_ns=1,
        ended_at_ns=2,
        trigger_kind="x",
        summary="",
        frame_count=0,
        primary_app="",
    )
    assert ep.tokens_in == 0
    assert ep.tokens_out == 0


# --- EpisodeBuilder lifecycle ----------------------------------------------

def test_builder_starts_empty() -> None:
    """Frischer Builder hat keine Frames/Events."""
    builder = EpisodeBuilder(started_at_ns=1)

    assert builder.frame_count == 0
    assert builder.frames == []
    assert builder.events == []
    assert builder.is_empty() is True


def test_add_frame_increments_count() -> None:
    """add_frame() bumpt frame_count und ist im frames-Property sichtbar."""
    builder = EpisodeBuilder(started_at_ns=1)
    frame = _make_frame()

    builder.add_frame(frame, salience=42)

    assert builder.frame_count == 1
    assert builder.frames == [frame]
    assert builder.is_empty() is False


def test_add_event_makes_builder_non_empty() -> None:
    """Auch ein Event allein reicht ⇒ is_empty=False."""
    builder = EpisodeBuilder(started_at_ns=1)

    builder.add_event("FileSaved", salience=40, payload={"path": "foo.py"})

    assert builder.frame_count == 0
    assert builder.is_empty() is False
    events = builder.events
    assert len(events) == 1
    assert events[0]["kind"] == "FileSaved"
    assert events[0]["salience"] == 40
    assert events[0]["payload"] == {"path": "foo.py"}
    assert "ts_ns" in events[0]


def test_events_returns_independent_copy() -> None:
    """builder.events liefert Kopie — Mutation darf den Builder nicht
    beeinflussen."""
    builder = EpisodeBuilder(started_at_ns=1)
    builder.add_event("FileSaved", salience=40, payload={"path": "x.py"})

    snap1 = builder.events
    snap1.clear()                                  # mutate top-level list
    snap1_again = builder.events
    assert len(snap1_again) == 1                   # Builder unbeeinflusst

    # Ist auch die Payload-Dict eine Kopie? (defensive)
    snap2 = builder.events
    snap2[0]["payload"]["new_key"] = "leaked"
    snap3 = builder.events
    assert "new_key" not in snap3[0]["payload"]


def test_frames_returns_independent_copy() -> None:
    """builder.frames liefert Kopie der Liste."""
    builder = EpisodeBuilder(started_at_ns=1)
    builder.add_frame(_make_frame(), salience=50)

    snap = builder.frames
    snap.clear()
    assert builder.frame_count == 1                # Builder unbeeinflusst


# --- primary_app -----------------------------------------------------------

def test_primary_app_empty_builder() -> None:
    """Leerer Builder ⇒ leerer String."""
    builder = EpisodeBuilder(started_at_ns=1)
    assert builder.primary_app == ""


def test_primary_app_single_frame() -> None:
    """Mit nur 1 Frame: dessen process_name (Dwell ist 0)."""
    builder = EpisodeBuilder(started_at_ns=1)
    builder.add_frame(_make_frame(process="notepad.exe"), salience=50)

    assert builder.primary_app == "notepad.exe"


def test_primary_app_most_dwelltime() -> None:
    """3 Frames: Notepad 5s → Code 30s → Notepad 10s ⇒ 'code.exe' gewinnt
    (groesste kumulative Dwell-Zeit)."""
    builder = EpisodeBuilder(started_at_ns=0)

    # t=0: Notepad startet
    builder.add_frame(
        _make_frame(process="notepad.exe", timestamp_ns=0), salience=30,
    )
    # t=5s: Code startet ⇒ Notepad-Dwell = 5s
    builder.add_frame(
        _make_frame(process="code.exe", timestamp_ns=5_000_000_000), salience=50,
    )
    # t=35s: Notepad startet wieder ⇒ Code-Dwell = 30s
    builder.add_frame(
        _make_frame(process="notepad.exe", timestamp_ns=35_000_000_000), salience=50,
    )
    # t=45s: letzter Frame (zaehlt 0 Beitrag) ⇒ Notepad-Dwell zusaetzlich 10s
    # WAIT: laut Spec wird die Dwell zwischen aufeinanderfolgenden Frames
    # berechnet, der letzte traegt 0 bei. Also Notepad insgesamt 5+10=15s,
    # Code 30s ⇒ Code gewinnt.
    builder.add_frame(
        _make_frame(process="notepad.exe", timestamp_ns=45_000_000_000), salience=30,
    )

    assert builder.primary_app == "code.exe"


def test_primary_app_tie_first_wins() -> None:
    """Bei Gleichstand gewinnt der zuerst eingefuegte Process
    (Insertion-Order)."""
    builder = EpisodeBuilder(started_at_ns=0)

    # Notepad: 10s, Code: 10s ⇒ Tie ⇒ Notepad (zuerst eingefuegt) gewinnt.
    builder.add_frame(
        _make_frame(process="notepad.exe", timestamp_ns=0), salience=30,
    )
    builder.add_frame(
        _make_frame(process="code.exe", timestamp_ns=10_000_000_000), salience=30,
    )
    builder.add_frame(
        _make_frame(process="other.exe", timestamp_ns=20_000_000_000), salience=30,
    )

    assert builder.primary_app == "notepad.exe"


# --- build() ----------------------------------------------------------------

def test_build_returns_immutable_episode() -> None:
    """build() liefert frozen Episode — Mutation crashed."""
    builder = EpisodeBuilder(started_at_ns=1_000)
    builder.add_frame(_make_frame(), salience=50)

    episode = builder.build(ended_at_ns=2_000, summary="test")

    with pytest.raises(dataclasses.FrozenInstanceError):
        episode.summary = "x"  # type: ignore[misc]


def test_build_preserves_all_fields() -> None:
    """Alle 8 Felder landen korrekt in der Episode."""
    builder = EpisodeBuilder(started_at_ns=100)
    builder.add_frame(
        _make_frame(process="code.exe", timestamp_ns=100), salience=50,
    )
    builder.add_frame(
        _make_frame(process="code.exe", timestamp_ns=200), salience=40,
    )

    episode = builder.build(
        ended_at_ns=999,
        summary="Du hast in code.exe gearbeitet.",
        tokens_in=123,
        tokens_out=45,
    )

    assert episode.started_at_ns == 100
    assert episode.ended_at_ns == 999
    assert episode.summary == "Du hast in code.exe gearbeitet."
    assert episode.frame_count == 2
    assert episode.primary_app == "code.exe"
    assert episode.tokens_in == 123
    assert episode.tokens_out == 45
    # trigger_kind wird vom Caller (StoryTracker) gesetzt — Builder kennt
    # ihn nicht und liefert hier den Sentinel-Default leerer String.
    assert episode.trigger_kind == ""


def test_build_token_defaults_zero() -> None:
    """build() ohne tokens_* ⇒ 0 in der Episode (Empty/Timeout-Pfad)."""
    builder = EpisodeBuilder(started_at_ns=1)
    episode = builder.build(ended_at_ns=2, summary="")
    assert episode.tokens_in == 0
    assert episode.tokens_out == 0


# --- duration_ns ------------------------------------------------------------

def test_duration_ns_is_plausible() -> None:
    """duration_ns = time.time_ns() - started_at_ns — positiv und plausibel."""
    started = time.time_ns() - 5_000_000_000    # 5s in der Vergangenheit
    builder = EpisodeBuilder(started_at_ns=started)

    duration = builder.duration_ns

    # >= 5s, aber < 10s (Test laeuft schnell).
    assert 5_000_000_000 <= duration < 10_000_000_000
