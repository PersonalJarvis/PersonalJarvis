"""Wave-4 latency fix: Computer-Use runs in the background, off the voice turn.

Previously a "do it on screen" command blocked the spoken turn for up to 31 s
(``await wait_for(harness.execute(...), harness_timeout_s + 1)``). Now the turn
ACKs immediately and the harness runs as a background task; its result is spoken
at the next turn boundary via an ``AnnouncementRequested(kind="completion")``
(AD-OE1 ack-now, AD-OE5 speak-result-later, AD-OE6 zero silent drops).
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from jarvis.brain.local_action_gate import LocalActionMode, LocalActionPlan
from jarvis.brain.manager import BrainManager


class _FakeBus:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, event) -> None:  # noqa: ANN001
        self.published.append(event)


class _SlowHarnessExecutor:
    """tool_executor stand-in whose execute() simulates a slow CU loop."""

    def __init__(self, *, output="Chrome ist offen.", delay=0.5, success=True, error=None) -> None:
        self.output = output
        self.delay = delay
        self.success = success
        self.error = error
        self.called = False

    async def execute(self, tool, args, *, user_utterance, trace_id):  # noqa: ANN001
        self.called = True
        await asyncio.sleep(self.delay)
        return SimpleNamespace(success=self.success, output=self.output, error=self.error)


def _make_manager(executor, bus):
    mgr = BrainManager.__new__(BrainManager)
    mgr._config = SimpleNamespace(
        local_action=SimpleNamespace(enabled=True, harness_timeout_s=30.0, direct_timeout_s=3.0)
    )
    mgr._bus = bus
    mgr._tool_executor = executor
    mgr._local_action_tools = {"dispatch_to_harness": object()}
    mgr._cost_meter = None
    mgr._reply_language = "auto"
    # Live conversation history (RAM): the model's next-turn context. A finished
    # background CU outcome must be grounded here so a follow-up "why didn't it
    # work?" is answered against the real screen failure, not a stale error.
    mgr._history = []
    return mgr


@pytest.mark.asyncio
async def test_computer_use_acks_immediately_not_blocking_on_harness(monkeypatch) -> None:
    bus = _FakeBus()
    executor = _SlowHarnessExecutor(delay=0.5)
    mgr = _make_manager(executor, bus)
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE, harness="computer-use", prompt="open chrome"
    )
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", lambda _t: plan)

    start = time.monotonic()
    reply = await mgr._run_local_action_fast_path("öffne chrome")
    elapsed = time.monotonic() - start

    # The voice turn must NOT block on the 0.5 s harness — it ACKs and returns.
    assert elapsed < 0.3, f"voice turn blocked on Computer-Use for {elapsed:.2f}s"
    assert reply, "must return a spoken ACK (not None — None would re-route to the brain)"
    assert "Chrome ist offen." not in reply, "the ACK must not be the harness result"


@pytest.mark.asyncio
async def test_computer_use_result_announced_when_done(monkeypatch) -> None:
    bus = _FakeBus()
    # dispatch_to_harness ALWAYS returns a DICT (never a bare string); a verified
    # success carries the on-screen observation in stdout's "(verified: ...)"
    # line. That proof is forwarded as the readback — and the raw dict is NEVER
    # str()'d into the turn (regression for the 2026-06-22 dict-leak, fully
    # covered in test_cu_readback_language.test_success_readback_never_leaks_raw_harness_dict).
    output = {
        "harness": "screenshot",
        "exit_code": 0,
        "stdout": "[cu] done at step 3.1 (verified: Chrome ist offen.)",
        "stderr": "",
        "cost_usd": 0.0,
        "duration_ms": 1200,
    }
    executor = _SlowHarnessExecutor(output=output, delay=0.2)
    mgr = _make_manager(executor, bus)
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE, harness="computer-use", prompt="open chrome"
    )
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", lambda _t: plan)

    await mgr._run_local_action_fast_path("öffne chrome")
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    assert executor.called
    completions = [e for e in bus.published if getattr(e, "kind", None) == "completion"]
    assert any("Chrome ist offen." in getattr(e, "text", "") for e in completions), (
        f"the verified observation must be forwarded as the completion; got {bus.published}"
    )
    # The raw harness dict must never leak into the spoken/chat completion.
    assert all(
        "{" not in getattr(e, "text", "") and "exit_code" not in getattr(e, "text", "")
        for e in completions
    ), f"raw harness dict leaked into completion: {bus.published}"


@pytest.mark.asyncio
async def test_computer_use_failure_is_announced_not_dropped(monkeypatch) -> None:
    # AD-OE6: a failed background action must still surface — never silent.
    bus = _FakeBus()
    executor = _SlowHarnessExecutor(success=False, error="harness crashed", delay=0.1)
    mgr = _make_manager(executor, bus)
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE, harness="computer-use", prompt="open chrome"
    )
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", lambda _t: plan)

    await mgr._run_local_action_fast_path("öffne chrome")
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    completions = [e for e in bus.published if getattr(e, "kind", None) == "completion"]
    assert completions, "a failed Computer-Use action must still be announced (AD-OE6)"


@pytest.mark.asyncio
async def test_user_cancel_offload_is_silent_not_announced(monkeypatch) -> None:
    """A user-initiated hangup (cancel token -> harness exit 130) must NOT speak
    a "the action was cancelled" readback.

    The user just triggered the abort themselves ("auflegen" is a hard,
    immediately-silent kill-switch), so the cancel readback is redundant — and
    with N parallel offloaded missions it spams the phrase N times. Live forensic
    2026-06-27: three CU missions all cancelled by one F1+F2 hangup spoke
    "Die Aktion am Bildschirm wurde abgebrochen." three times. The cancel is the
    receipt of an abort the user already made — not a result they are waiting on,
    so AD-OE6's zero-silent-drop (which protects real outcomes) does not apply.
    """
    bus = _FakeBus()
    # The harness reports a user cancel as exit 130 ("[cu] cancelled mid-batch").
    output = {
        "harness": "screenshot",
        "exit_code": 130,
        "stdout": "",
        "stderr": "[cu] cancelled mid-batch\n",
        "cost_usd": 0.0,
        "duration_ms": 6200,
    }
    executor = _SlowHarnessExecutor(
        output=output, success=False, error="exit 130", delay=0.1
    )
    mgr = _make_manager(executor, bus)
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE,
        harness="computer-use",
        prompt="screenshot the repo",
    )
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", lambda _t: plan)

    await mgr._run_local_action_fast_path("mach einen screenshot")
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    assert executor.called
    completions = [e for e in bus.published if getattr(e, "kind", None) == "completion"]
    assert not completions, (
        "a user-initiated cancel (exit 130 / hangup) must not be announced; got "
        f"{[getattr(e, 'text', '') for e in completions]}"
    )


@pytest.mark.asyncio
async def test_three_parallel_cancels_speak_nothing(monkeypatch) -> None:
    """The exact 2026-06-27 forensic: three offloaded CU missions, all cancelled
    by one hangup, must produce ZERO spoken cancel readbacks (not three)."""
    bus = _FakeBus()
    output = {
        "harness": "screenshot",
        "exit_code": 130,
        "stdout": "",
        "stderr": "[cu] cancelled mid-batch\n",
        "cost_usd": 0.0,
        "duration_ms": 5100,
    }
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE, harness="computer-use", prompt="do it"
    )
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", lambda _t: plan)

    # Each turn spawns its own background mission off a shared manager.
    executor = _SlowHarnessExecutor(
        output=output, success=False, error="exit 130", delay=0.05
    )
    mgr = _make_manager(executor, bus)
    for _ in range(3):
        await mgr._run_local_action_fast_path("öffentlich, dann screenshot")
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    completions = [e for e in bus.published if getattr(e, "kind", None) == "completion"]
    assert not completions, (
        f"three hangup-cancelled missions must speak nothing; got {len(completions)}"
    )
