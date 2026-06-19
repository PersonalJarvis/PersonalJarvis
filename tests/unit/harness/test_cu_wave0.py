"""Wave 0 of the frontier-speed Computer-Use plan: measure & stabilize.

Covers:
* CUStepProfiled phase events + the CU_PROGRESS_EVENTS heartbeat contract
  (every event type the CU loop publishes must be a registered speech-pipeline
  heartbeat source, so a long think phase can never starve the TTS ceiling).
* Per-phase timeout budgets replacing the silent 12s per-op blanket cap.
* Loop-internal mission deadline: the loop ends cleanly BEFORE the harness
  guillotine.
* Throttled spoken progress announcements (kind="progress").
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

import jarvis.harness.screenshot_only_loop as loop_mod
from jarvis.core.events import (
    CU_PROGRESS_EVENTS,
    ActionPlanned,
    AnnouncementRequested,
    CUStepProfiled,
    ObservationCaptured,
)
from tests.unit.harness.test_cu_loop_robustness import (
    FakeBrain,
    make_ctx,
    run_loop,
)


class FakeBus:
    """Collects published events; mimics EventBus.publish."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)

    def of_type(self, cls: type) -> list[Any]:
        return [e for e in self.events if isinstance(e, cls)]


# ---------------------------------------------------------------------------
# Heartbeat contract
# ---------------------------------------------------------------------------


def test_cu_progress_events_constant_covers_all_loop_event_types() -> None:
    """The pipeline subscribes heartbeats via CU_PROGRESS_EVENTS; the loop must
    never publish a progress-relevant event type outside that tuple."""
    assert ObservationCaptured in CU_PROGRESS_EVENTS
    assert ActionPlanned in CU_PROGRESS_EVENTS
    assert CUStepProfiled in CU_PROGRESS_EVENTS


def test_speech_pipeline_subscribes_via_the_constant() -> None:
    """Contract: the pipeline iterates CU_PROGRESS_EVENTS for its agent-progress
    subscription instead of hardcoding two event types."""
    import inspect

    import jarvis.speech.pipeline as pipeline_mod

    src = inspect.getsource(pipeline_mod)
    assert "CU_PROGRESS_EVENTS" in src


async def test_loop_emits_phase_profile_events() -> None:
    """Each step must profile its phases on the bus — instrumentation for
    cu_bench AND the heartbeat that keeps the TTS ceiling suspended during a
    long think."""
    brain = FakeBrain(script=[
        '{"action": "open_app", "name": "chrome"}',
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain)
    bus = FakeBus()
    ctx.bus = bus
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    profiled = bus.of_type(CUStepProfiled)
    phases = {e.phase for e in profiled}
    assert {"observe", "think", "act"} <= phases
    assert all(e.duration_ms >= 0 for e in profiled)
    assert any(e.step_idx == 1 for e in profiled)


# ---------------------------------------------------------------------------
# Per-phase timeout budgets
# ---------------------------------------------------------------------------


def test_no_silent_per_op_blanket_cap() -> None:
    """The 12s blanket `_PER_OP_TIMEOUT_CAP_S` silently overrode the configured
    per_step_timeout_s. Phase budgets replace it."""
    assert not hasattr(loop_mod, "_PER_OP_TIMEOUT_CAP_S")
    assert loop_mod._OBSERVE_TIMEOUT_S <= 4.0
    assert loop_mod._ACT_TIMEOUT_S >= 3.0
    assert loop_mod._THINK_TIMEOUT_CAP_S >= 8.0


def test_think_timeout_respects_configured_step_timeout() -> None:
    ctx = make_ctx(FakeBrain())
    ctx.per_step_timeout_s = 6.0
    assert loop_mod._think_timeout_s(ctx) == 6.0
    ctx.per_step_timeout_s = 60.0
    assert loop_mod._think_timeout_s(ctx) == loop_mod._THINK_TIMEOUT_CAP_S


async def test_observe_uses_observe_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wedged screenshot source must trip the observe budget, not a generic
    12s blanket."""
    class WedgedVision:
        async def observe(self, **_kw: Any) -> Any:
            await asyncio.sleep(5.0)

    monkeypatch.setattr(loop_mod, "_OBSERVE_TIMEOUT_S", 0.05)
    ctx = make_ctx(FakeBrain())
    ctx.vision_engine = WedgedVision()
    t0 = time.monotonic()
    chunks = await run_loop(ctx, "mach irgendwas")
    elapsed = time.monotonic() - t0

    assert chunks[-1].exit_code == 124
    assert elapsed < 2.0


# ---------------------------------------------------------------------------
# Loop-internal mission deadline (clean exit before the guillotine)
# ---------------------------------------------------------------------------


def test_internal_deadline_leaves_slack_before_guillotine() -> None:
    # The loop must finish cleanly before the harness wait_for kills it.
    assert loop_mod._internal_deadline_s(120.0) <= 115.0
    assert loop_mod._internal_deadline_s(120.0) >= 100.0
    # Tiny budgets never go negative.
    assert loop_mod._internal_deadline_s(3.0) > 0.0


async def test_mission_ends_cleanly_at_internal_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the budget runs out the loop must yield a FINAL budget result
    itself instead of being guillotined mid-step by the outer timeout."""
    brain = FakeBrain(handler=lambda s, u: '{"action": "click", "x": 500, "y": 500}')
    ctx = make_ctx(brain, step_budget=50)
    monkeypatch.setattr(loop_mod, "_internal_deadline_s", lambda timeout_s: 0.0)
    chunks = await run_loop(ctx, "mach irgendwas dauerhaftes")

    final = chunks[-1]
    assert final.is_final
    assert final.exit_code == 4  # budget — a clean, explicit exit
    assert "budget" in final.stderr.lower() or "deadline" in final.stderr.lower()


# ---------------------------------------------------------------------------
# Spoken progress (kind="progress", throttled)
# ---------------------------------------------------------------------------


async def test_progress_announcement_after_plan_step_completes() -> None:
    from tests.unit.harness.test_cu_loop_robustness import _CHROME_PLAN, PlanningBrain

    brain = PlanningBrain(
        executor_script=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "done"}',
        ],
        plan_script=[_CHROME_PLAN],
        judge_script=['{"done": true, "proof": "ok"}'],
    )
    ctx = make_ctx(brain, verify=True)
    # Progress narration is opt-in since 2026-06-10 (the default-off contract
    # is pinned in test_cu_runaway_guards.py); this pins the opted-in shape.
    ctx.announce_progress = True
    bus = FakeBus()
    ctx.bus = bus
    chunks = await run_loop(ctx, "oeffne chrome und navigiere zu den einstellungen")  # i18n-allow: German voice-command test fixture

    assert chunks[-1].exit_code == 0
    progress = [
        e for e in bus.of_type(AnnouncementRequested)
        if getattr(e, "kind", None) == "progress"
    ]
    assert len(progress) >= 1
    # Deterministic, short, no LLM call: "Schritt N von M erledigt."
    assert "1" in progress[0].text and "3" in progress[0].text


async def test_progress_announcements_are_throttled() -> None:
    """Several quick plan steps must not produce a barrage of speech — at most
    one progress announcement per throttle window."""
    from tests.unit.harness.test_cu_loop_robustness import _CHROME_PLAN, PlanningBrain

    brain = PlanningBrain(
        executor_script=[
            '{"action": "click", "x": 100, "y": 100}',
            '{"action": "click", "x": 200, "y": 200}',
            '{"action": "done"}',
        ],
        plan_script=[_CHROME_PLAN],
        judge_script=['{"done": true, "proof": "ok"}'],
    )
    ctx = make_ctx(brain, verify=True)
    ctx.announce_progress = True  # opt-in since 2026-06-10; throttle contract
    bus = FakeBus()
    ctx.bus = bus
    chunks = await run_loop(ctx, "oeffne chrome und navigiere zu den einstellungen")  # i18n-allow: German voice-command test fixture

    assert chunks[-1].exit_code == 0
    progress = [
        e for e in bus.of_type(AnnouncementRequested)
        if getattr(e, "kind", None) == "progress"
    ]
    assert len(progress) <= 1
