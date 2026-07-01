"""Tests for jarvis.awareness.episode — Episode + EpisodeBuilder.

Spec: plan §6 + TASKS.md "Slice B".
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
    """Convenient constructor — sensible defaults."""
    return FrameSnapshot(
        timestamp_ns=timestamp_ns,
        active_window_title=title,
        active_process_name=process,
        active_pid=pid,
        is_capture_allowed=True,
    )


# --- Episode (frozen) -------------------------------------------------------

def test_episode_is_frozen() -> None:
    """Episode is frozen — no re-assignment possible."""
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
    """slots=True ⇒ no __dict__."""
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
    """tokens_in / tokens_out default to 0 (empty episode or timeout)."""
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
    """A fresh builder has no frames/events."""
    builder = EpisodeBuilder(started_at_ns=1)

    assert builder.frame_count == 0
    assert builder.frames == []
    assert builder.events == []
    assert builder.is_empty() is True


def test_add_frame_increments_count() -> None:
    """add_frame() bumps frame_count and is visible in the frames property."""
    builder = EpisodeBuilder(started_at_ns=1)
    frame = _make_frame()

    builder.add_frame(frame, salience=42)

    assert builder.frame_count == 1
    assert builder.frames == [frame]
    assert builder.is_empty() is False


def test_add_event_makes_builder_non_empty() -> None:
    """An event alone is enough ⇒ is_empty=False."""
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
    """builder.events returns a copy — mutation must not affect the
    builder."""
    builder = EpisodeBuilder(started_at_ns=1)
    builder.add_event("FileSaved", salience=40, payload={"path": "x.py"})

    snap1 = builder.events
    snap1.clear()                                  # mutate top-level list
    snap1_again = builder.events
    assert len(snap1_again) == 1                   # builder unaffected

    # Is the payload dict also a copy? (defensive)
    snap2 = builder.events
    snap2[0]["payload"]["new_key"] = "leaked"
    snap3 = builder.events
    assert "new_key" not in snap3[0]["payload"]


def test_frames_returns_independent_copy() -> None:
    """builder.frames returns a copy of the list."""
    builder = EpisodeBuilder(started_at_ns=1)
    builder.add_frame(_make_frame(), salience=50)

    snap = builder.frames
    snap.clear()
    assert builder.frame_count == 1                # builder unaffected


# --- primary_app -----------------------------------------------------------

def test_primary_app_empty_builder() -> None:
    """Empty builder ⇒ empty string."""
    builder = EpisodeBuilder(started_at_ns=1)
    assert builder.primary_app == ""


def test_primary_app_single_frame() -> None:
    """With only 1 frame: its process_name (dwell is 0)."""
    builder = EpisodeBuilder(started_at_ns=1)
    builder.add_frame(_make_frame(process="notepad.exe"), salience=50)

    assert builder.primary_app == "notepad.exe"


def test_primary_app_most_dwelltime() -> None:
    """3 frames: Notepad 5s → Code 30s → Notepad 10s ⇒ 'code.exe' wins
    (largest cumulative dwell time)."""
    builder = EpisodeBuilder(started_at_ns=0)

    # t=0: Notepad starts
    builder.add_frame(
        _make_frame(process="notepad.exe", timestamp_ns=0), salience=30,
    )
    # t=5s: Code starts ⇒ Notepad dwell = 5s
    builder.add_frame(
        _make_frame(process="code.exe", timestamp_ns=5_000_000_000), salience=50,
    )
    # t=35s: Notepad starts again ⇒ Code dwell = 30s
    builder.add_frame(
        _make_frame(process="notepad.exe", timestamp_ns=35_000_000_000), salience=50,
    )
    # t=45s: last frame (contributes 0) ⇒ Notepad dwell +10s additionally
    # WAIT: per spec, dwell is computed between consecutive frames,
    # the last one contributes 0. So Notepad totals 5+10=15s,
    # Code 30s ⇒ Code wins.
    builder.add_frame(
        _make_frame(process="notepad.exe", timestamp_ns=45_000_000_000), salience=30,
    )

    assert builder.primary_app == "code.exe"


def test_primary_app_tie_first_wins() -> None:
    """On a tie, the first-inserted process wins
    (insertion order)."""
    builder = EpisodeBuilder(started_at_ns=0)

    # Notepad: 10s, Code: 10s ⇒ tie ⇒ Notepad (inserted first) wins.
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
    """build() returns a frozen Episode — mutation crashes."""
    builder = EpisodeBuilder(started_at_ns=1_000)
    builder.add_frame(_make_frame(), salience=50)

    episode = builder.build(ended_at_ns=2_000, summary="test")

    with pytest.raises(dataclasses.FrozenInstanceError):
        episode.summary = "x"  # type: ignore[misc]


def test_build_preserves_all_fields() -> None:
    """All 8 fields land correctly in the Episode."""
    builder = EpisodeBuilder(started_at_ns=100)
    builder.add_frame(
        _make_frame(process="code.exe", timestamp_ns=100), salience=50,
    )
    builder.add_frame(
        _make_frame(process="code.exe", timestamp_ns=200), salience=40,
    )

    episode = builder.build(
        ended_at_ns=999,
        summary="You worked in code.exe.",
        tokens_in=123,
        tokens_out=45,
    )

    assert episode.started_at_ns == 100
    assert episode.ended_at_ns == 999
    assert episode.summary == "You worked in code.exe."
    assert episode.frame_count == 2
    assert episode.primary_app == "code.exe"
    assert episode.tokens_in == 123
    assert episode.tokens_out == 45
    # trigger_kind is set by the caller (StoryTracker) — the builder doesn't
    # know it and returns the sentinel default empty string here.
    assert episode.trigger_kind == ""


def test_build_token_defaults_zero() -> None:
    """build() without tokens_* ⇒ 0 in the Episode (empty/timeout path)."""
    builder = EpisodeBuilder(started_at_ns=1)
    episode = builder.build(ended_at_ns=2, summary="")
    assert episode.tokens_in == 0
    assert episode.tokens_out == 0


# --- duration_ns ------------------------------------------------------------

def test_duration_ns_is_plausible() -> None:
    """duration_ns = time.time_ns() - started_at_ns — positive and plausible."""
    started = time.time_ns() - 5_000_000_000    # 5s in the past
    builder = EpisodeBuilder(started_at_ns=started)

    duration = builder.duration_ns

    # >= 5s, but < 10s (test runs fast).
    assert 5_000_000_000 <= duration < 10_000_000_000
