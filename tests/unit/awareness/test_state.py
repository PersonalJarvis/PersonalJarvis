"""Tests for jarvis.awareness.state — FrameSnapshot + AwarenessState.

A0 scope: data classes + default behavior. snapshot_for_prompt() is an
A0 placeholder (empty string); real rendering logic arrives in A1.
"""
from __future__ import annotations

import dataclasses
import time

import pytest

from jarvis.awareness.state import AwarenessState, FrameSnapshot

# --- FrameSnapshot ---------------------------------------------------------

def test_framesnapshot_is_frozen() -> None:
    """frozen=True verhindert Re-Assignment."""
    snap = FrameSnapshot(
        timestamp_ns=1,
        active_window_title="x",
        active_process_name="y",
        active_pid=1,
        is_capture_allowed=True,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.active_pid = 2  # type: ignore[misc]


def test_framesnapshot_has_slots() -> None:
    """slots=True → no __dict__, only the declared fields."""
    snap = FrameSnapshot(
        timestamp_ns=1,
        active_window_title="x",
        active_process_name="y",
        active_pid=1,
        is_capture_allowed=True,
    )
    with pytest.raises(AttributeError):
        snap.__dict__  # noqa: B018


def test_framesnapshot_optional_fields_default_none() -> None:
    """git_branch / open_file_hint / idle_since_ns are always None in A0."""
    snap = FrameSnapshot(
        timestamp_ns=1,
        active_window_title="x",
        active_process_name="y",
        active_pid=1,
        is_capture_allowed=True,
    )
    assert snap.git_branch is None
    assert snap.open_file_hint is None
    assert snap.idle_since_ns is None


# --- AwarenessState --------------------------------------------------------

def test_awarenessstate_default_init() -> None:
    """AwarenessState() with no args is valid and has sensible defaults.

    A4 refactor: ``working_set`` is no longer a ``list`` but an
    optional ``WorkingSet | None``. Default = None — set by the
    AwarenessManager constructor.
    """
    state = AwarenessState()
    assert state.current_frame is None
    assert state.last_episode_summary == ""
    assert state.last_episode_id is None
    assert state.is_idle is False
    assert state.working_set is None


def test_awarenessstate_is_mutable() -> None:
    """AwarenessState is NOT frozen — watchers write into it."""
    state = AwarenessState()
    state.is_idle = True
    state.last_episode_summary = "hello"
    assert state.is_idle is True
    assert state.last_episode_summary == "hello"


def test_awarenessstate_working_set_is_per_instance() -> None:
    """A4: ``working_set`` is a WorkingSet reference from the manager (not a
    shared default). We verify that two AwarenessState instances are
    independent of each other when each gets its own WorkingSet.
    """
    from jarvis.awareness.working_set import WorkingSet

    a = AwarenessState()
    b = AwarenessState()
    a.working_set = WorkingSet()
    b.working_set = WorkingSet()
    # Mutating a.working_set must not affect b.
    from jarvis.awareness.context import Context
    a.working_set.observe(Context(project_root="x", task_label=""))
    assert a.working_set.size == 1
    assert b.working_set.size == 0


def test_snapshot_for_prompt_a0_placeholder() -> None:
    """A0: snapshot_for_prompt returns an empty string. A1 replaces this."""
    state = AwarenessState()
    assert state.snapshot_for_prompt() == ""
    assert state.snapshot_for_prompt(max_chars=100) == ""


# --- Freshness guard (anti-stale-context, 2026-06-17) ----------------------
# Root cause of the "BridgeSpace und WhatsApp" overclaim: a current_frame that
# had not updated for ~82 min was rendered verbatim as the present state. The
# snapshot must never present an old frame as the live foreground.


def test_snapshot_fresh_frame_rendered_as_current() -> None:
    """A frame observed just now is presented as the CURRENTLY focused window."""
    state = AwarenessState()
    state.current_frame = FrameSnapshot(
        timestamp_ns=time.time_ns(),
        active_window_title="pipeline.py - Visual Studio Code",
        active_process_name="Code.exe",
        active_pid=4242,
        is_capture_allowed=True,
    )
    snap = state.snapshot_for_prompt()
    assert "Currently focused window" in snap
    assert "pipeline.py - Visual Studio Code" in snap
    assert "Code.exe" in snap
    # A fresh frame must NOT carry the stale marker.
    assert "stale" not in snap.lower()
    assert "Last observed" not in snap


def test_snapshot_old_frame_is_marked_stale_not_current() -> None:
    """A frame from ~90 min ago must be flagged as stale, never as 'current'.

    Regression guard for the 19:38 turn where an 82-min-old frame
    ('WhatsApp'/'BridgeSpace') was narrated as 'aktuell ... aktiv'.
    """
    ninety_min_ns = int(90 * 60 * 1_000_000_000)
    state = AwarenessState()
    state.current_frame = FrameSnapshot(
        timestamp_ns=time.time_ns() - ninety_min_ns,
        active_window_title="WhatsApp",
        active_process_name="WhatsApp.exe",
        active_pid=777,
        is_capture_allowed=True,
    )
    snap = state.snapshot_for_prompt()
    # The window title is still surfaced (honest: this is the LAST thing seen)...
    assert "WhatsApp" in snap
    # ...but it must be explicitly marked stale and NOT presented as current.
    assert "Last observed" in snap
    assert "stale" in snap.lower()
    assert "Currently focused window" not in snap


def test_snapshot_includes_open_window_scope_disclaimer() -> None:
    """The block must state it is focused-window history, not a full window list.

    Stops the brain from overclaiming 'only X and Y are open on your PC'.
    """
    state = AwarenessState()
    state.current_frame = FrameSnapshot(
        timestamp_ns=time.time_ns(),
        active_window_title="Editor",
        active_process_name="Code.exe",
        active_pid=1,
        is_capture_allowed=True,
    )
    snap = state.snapshot_for_prompt()
    assert "not a complete list" in snap.lower()
