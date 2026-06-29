"""Phase A1 — System-Prompt-Injection des Awareness-Snapshots.

Plan §5 'Files to Modify' verlangt, dass ``BrainManager._build_system_prompt``
einen kompakten Live-Snapshot aus ``AwarenessManager.state`` injiziert —
als Fallback fuer den Fall dass das LLM das ``awareness-snapshot``-Tool
NICHT ruft.

AC-5 (Plan-Verifier): Hauptjarvis (Router) bekommt das Tool im Schema und
hat den State auch passiv im Prompt; Sub-Jarvis bekommt KEIN
``awareness_manager`` (stateless).
"""
from __future__ import annotations

import time

from jarvis.awareness.config import AwarenessConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.awareness.state import FrameSnapshot
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


def _make_brain(awareness_manager: AwarenessManager | None) -> BrainManager:
    # These tests assert the snapshot lands in the cached SYSTEM prompt. The
    # default `cache_optimized_prompt=True` deliberately moves the snapshot into
    # the per-turn user message instead (Wave-2 omni-latency), so pin the legacy
    # in-system-prompt path here to test exactly that contract.
    config = JarvisConfig()
    config.performance.cache_optimized_prompt = False
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={},
        awareness_manager=awareness_manager,
    )


def test_no_awareness_manager_no_snapshot_in_prompt() -> None:
    """Backward-Compat: ohne ``awareness_manager`` darf NICHTS injiziert werden."""
    brain = _make_brain(awareness_manager=None)
    prompt = brain._build_system_prompt()
    assert "AKTUELLER KONTEXT" not in prompt


def test_awareness_manager_with_empty_state_no_snapshot() -> None:
    """``state.snapshot_for_prompt()`` returnt '' wenn ``current_frame is None``.

    In dem Fall darf der Injection-Block KEIN leeres ``AKTUELLER KONTEXT:``-
    Marker emittieren — der Prompt soll sauber bleiben.
    """
    mgr = AwarenessManager(AwarenessConfig.default())
    assert mgr.state.current_frame is None
    brain = _make_brain(awareness_manager=mgr)
    prompt = brain._build_system_prompt()
    assert "AKTUELLER KONTEXT" not in prompt


def test_awareness_manager_with_frame_injects_snapshot() -> None:
    """Ein gesetzter ``current_frame`` muss als ``AKTUELLER KONTEXT`` im Prompt landen."""
    mgr = AwarenessManager(AwarenessConfig.default())
    mgr.state.current_frame = FrameSnapshot(
        timestamp_ns=time.time_ns(),
        active_window_title="Editor — main.py",
        active_process_name="Code.exe",
        active_pid=12345,
        is_capture_allowed=True,
    )

    brain = _make_brain(awareness_manager=mgr)
    prompt = brain._build_system_prompt()

    assert "AKTUELLER KONTEXT" in prompt
    assert "Editor — main.py" in prompt
    assert "Code.exe" in prompt
    assert "pid=12345" in prompt


def test_snapshot_failure_does_not_break_prompt_build() -> None:
    """Defensive try/except: ein kaputter ``snapshot_for_prompt`` darf den
    System-Prompt-Build nicht kippen."""
    mgr = AwarenessManager(AwarenessConfig.default())

    class _BadState:
        def snapshot_for_prompt(self, max_chars: int = 600) -> str:  # noqa: ARG002
            raise RuntimeError("simulated state failure")

    mgr._state = _BadState()  # type: ignore[assignment]

    brain = _make_brain(awareness_manager=mgr)
    # Darf nicht raisen
    prompt = brain._build_system_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 0
    assert "AKTUELLER KONTEXT" not in prompt
