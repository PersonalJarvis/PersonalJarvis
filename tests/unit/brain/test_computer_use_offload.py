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
    reply = await mgr._run_local_action_fast_path("öffne chrome")  # i18n-allow
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

    await mgr._run_local_action_fast_path("öffne chrome")  # i18n-allow
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

    await mgr._run_local_action_fast_path("öffne chrome")  # i18n-allow
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
    "Die Aktion am Bildschirm wurde abgebrochen." three times. The cancel is the  # i18n-allow
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
        await mgr._run_local_action_fast_path("öffentlich, dann screenshot")  # i18n-allow
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    completions = [e for e in bus.published if getattr(e, "kind", None) == "completion"]
    assert not completions, (
        f"three hangup-cancelled missions must speak nothing; got {len(completions)}"
    )


# ---------------------------------------------------------------------------
# Conversation continuity: a finished background CU outcome must be written back
# into the LIVE history (BrainManager._history — the model's next-turn context).
#
# Live forensic 2026-06-30: the user asked to open Spotify on screen; the action
# failed (exit 2, vision-provider chain exhausted). The spoken failure readback
# rode AnnouncementRequested(kind="completion") and was NEVER recorded in
# _history. So the model's only "problem" in context was a stale Google-Calendar
# auth error from an earlier turn — and a follow-up "why don't you get one?" was
# answered against the CALENDAR, the wrong subsystem. Grounding the outcome (and
# its real technical reason) in history fixes the subsystem confusion at the root.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cu_failure_outcome_is_written_back_to_history(monkeypatch) -> None:
    bus = _FakeBus()
    # exit 2 here = the vision-provider chain was exhausted (the real 2026-06-30
    # cause); the harness writes the technical reason into stderr.
    output = {
        "harness": "screenshot",
        "exit_code": 2,
        "stdout": "",
        "stderr": (
            "[cu] giving up after 3 model failures (last: ComputerUseLoop "
            "provider chain failed: no vision-capable provider reachable)"
        ),
        "cost_usd": 0.0,
        "duration_ms": 9700,
    }
    executor = _SlowHarnessExecutor(
        output=output, success=False, error="exit 2", delay=0.05
    )
    mgr = _make_manager(executor, bus)
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE, harness="computer-use", prompt="open spotify"
    )
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", lambda _t: plan)

    await mgr._run_local_action_fast_path("öffne mein spotify und spiel das lied")  # i18n-allow
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    # The failed screen action must be grounded in the live history so the model
    # knows it failed (no stale-error confusion on the next turn).
    assert mgr._history, "the failed CU outcome must be recorded in the live history"
    last = mgr._history[-1]
    assert last.role == "assistant", f"unexpected role: {last.role!r}"
    content = str(last.content).lower()
    # carries BOTH the fact it was an on-screen action AND the real technical
    # reason the humanized spoken readback hid.
    assert "screen" in content or "bildschirm" in content, content
    assert "provider chain" in content or "vision" in content, (
        f"the real technical reason must be grounded for a faithful follow-up: {content!r}"
    )


@pytest.mark.asyncio
async def test_cu_success_outcome_is_written_back_to_history(monkeypatch) -> None:
    bus = _FakeBus()
    output = {
        "harness": "screenshot",
        "exit_code": 0,
        "stdout": "[cu] done at step 2.1 (verified: Spotify is open and the song is playing)",
        "stderr": "",
        "cost_usd": 0.0,
        "duration_ms": 4200,
    }
    executor = _SlowHarnessExecutor(output=output, success=True, delay=0.05)
    mgr = _make_manager(executor, bus)
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE, harness="computer-use", prompt="open spotify"
    )
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", lambda _t: plan)

    await mgr._run_local_action_fast_path("öffne spotify und spiel das lied")  # i18n-allow
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    assert mgr._history, "a successful CU outcome must also be recorded in history"
    last = mgr._history[-1]
    assert last.role == "assistant"
    assert "spotify" in str(last.content).lower(), str(last.content)


@pytest.mark.asyncio
async def test_cu_user_cancel_writes_nothing_to_history(monkeypatch) -> None:
    """A user-initiated cancel (exit 130 / "auflegen") speaks nothing AND must
    not pollute the history — there is no outcome the user is waiting on."""
    bus = _FakeBus()
    output = {
        "harness": "screenshot",
        "exit_code": 130,
        "stdout": "",
        "stderr": "[cu] cancelled mid-batch\n",
        "cost_usd": 0.0,
        "duration_ms": 6200,
    }
    executor = _SlowHarnessExecutor(
        output=output, success=False, error="exit 130", delay=0.05
    )
    mgr = _make_manager(executor, bus)
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE, harness="computer-use", prompt="do it"
    )
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", lambda _t: plan)

    await mgr._run_local_action_fast_path("mach einen screenshot")
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    assert mgr._history == [], (
        f"a silent user cancel must not write to history; got {mgr._history!r}"
    )


# ---------------------------------------------------------------------------
# The router-tier `computer_use` TOOL path (computer_use_tool.py) runs in its own
# module with NO _history access. It tags its completion announcement with a CU
# source_layer; the BrainManager mirrors that tagged outcome into the live
# history so a text-chat / router-picked desktop action is grounded for the next
# turn too — same fix as the voice fast-path, the other CU entry point.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_mirrors_cu_tool_completion_into_history() -> None:
    from jarvis.core.events import AnnouncementRequested
    from jarvis.voice.action_phrases import CU_TOOL_OUTCOME_LAYER

    mgr = _make_manager(_SlowHarnessExecutor(), _FakeBus())
    await mgr._on_cu_tool_completion(AnnouncementRequested(
        text="That didn't work on screen.",
        kind="completion",
        source_layer=CU_TOOL_OUTCOME_LAYER,
        detail="exit 2 · provider chain failed: no vision-capable provider reachable",
    ))

    assert mgr._history, "a tool-path CU outcome must be mirrored into history"
    content = str(mgr._history[-1].content).lower()
    assert "screen" in content
    assert "provider chain" in content


@pytest.mark.asyncio
async def test_cu_tool_completion_grounds_history_end_to_end() -> None:
    """Full chain: attach_to_bus wires the subscriber, and a tool-tagged
    completion published on the REAL bus lands in the live history."""
    from jarvis.core.bus import EventBus
    from jarvis.core.events import AnnouncementRequested
    from jarvis.voice.action_phrases import CU_TOOL_OUTCOME_LAYER

    bus = EventBus()
    mgr = _make_manager(_SlowHarnessExecutor(), bus)
    mgr.attach_to_bus(bus)

    await bus.publish(AnnouncementRequested(
        text="That didn't work on screen.",
        kind="completion",
        source_layer=CU_TOOL_OUTCOME_LAYER,
        detail="exit 3 · no vision-capable provider reachable",
    ))
    await asyncio.sleep(0.01)  # allow async dispatch to run

    assert mgr._history, "the tool-path outcome must reach history via the bus"
    assert "screen" in str(mgr._history[-1].content).lower()


@pytest.mark.asyncio
async def test_manager_ignores_non_cu_tool_completion() -> None:
    """A mission / worker / other completion must NOT be mirrored — only a
    CU-tool-tagged one — so an unrelated background readback never pollutes the
    desktop-action grounding."""
    from jarvis.core.events import AnnouncementRequested

    mgr = _make_manager(_SlowHarnessExecutor(), _FakeBus())
    await mgr._on_cu_tool_completion(AnnouncementRequested(
        text="Your sub-agent finished.",
        kind="completion",
        source_layer="missions.announcer",
    ))

    assert mgr._history == []


@pytest.mark.asyncio
async def test_manager_retains_signed_mission_result_for_follow_up() -> None:
    from jarvis.core.events import AnnouncementRequested

    mgr = _make_manager(_SlowHarnessExecutor(), _FakeBus())
    await mgr._on_cu_tool_completion(AnnouncementRequested(
        text="The research report is ready.",
        kind="subagent",
        source_layer="missions.voice.announcer",
        detail=(
            '{"mission_id":"019f5ca2-e30f",'
            '"result_uri":"mission://019f5ca2-e30f"}'
        ),
    ))

    assert mgr._history
    content = str(mgr._history[-1].content)
    assert "Jarvis-Agent mission result" in content
    assert "019f5ca2-e30f" in content
    assert "result_uri" in content


# ---------------------------------------------------------------------------
# Conversation context in the Computer-Use goal. The deterministic local-action
# gate ships the RAW current utterance as the mission goal. A correction or
# follow-up turn carries no task of its own — without the turns that defined the
# task, the mission verifier passes against a vacuous goal. Live forensic
# 2026-07-15 07:59: after a failed Discord-announcement mission the user said
# "Ihr macht es doch mit Computer-Use."; that bare sentence became the whole  # i18n-allow
# goal, the loop opened Discord's Friends view, and the verifier announced
# success. The goal must lead with the latest instruction and carry a bounded
# context block of the recent turns.
# ---------------------------------------------------------------------------


class _RecordingExecutor:
    """tool_executor stand-in that records the harness dispatch arguments."""

    def __init__(self) -> None:
        self.args: dict | None = None

    async def execute(self, tool, args, *, user_utterance, trace_id):  # noqa: ANN001
        self.args = args
        return SimpleNamespace(
            success=True,
            output={
                "harness": "screenshot",
                "exit_code": 0,
                "stdout": "[cu] done at step 1.1 (verified: ok)",
                "stderr": "",
                "cost_usd": 0.0,
                "duration_ms": 10,
            },
            error=None,
        )


@pytest.mark.asyncio
async def test_cu_goal_carries_recent_conversation_context(monkeypatch) -> None:
    """A delegated follow-up turn inherits the task from the turn history."""
    from jarvis.brain.manager import _TURN_HISTORY_OVERRIDE
    from jarvis.core.protocols import BrainMessage

    bus = _FakeBus()
    executor = _RecordingExecutor()
    mgr = _make_manager(executor, bus)
    utterance = "Ihr macht es doch mit Computer-Use."  # i18n-allow: German speech-input fixture
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE, harness="computer-use", prompt=utterance
    )
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", lambda _t: plan)

    token = _TURN_HISTORY_OVERRIDE.set((
        BrainMessage(
            role="user",
            content=(
                "Öffne meinen Discord-Server Personal Jarvis und kündige an, "  # i18n-allow: German speech-input fixture
                "dass übermorgen ein Live-Event stattfindet."  # i18n-allow: German speech-input fixture
            ),
        ),
        BrainMessage(role="assistant", content="Alles klar, ich kümmere mich darum."),  # i18n-allow: German voice fixture
    ))
    try:
        await mgr._run_local_action_fast_path(utterance)
    finally:
        _TURN_HISTORY_OVERRIDE.reset(token)
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    prompt = (executor.args or {}).get("prompt", "")
    assert prompt.startswith(utterance), prompt
    assert "Personal Jarvis" in prompt, (
        f"the goal must inherit the task from the recent turns: {prompt!r}"
    )
    assert "Live-Event" in prompt, prompt


@pytest.mark.asyncio
async def test_cu_goal_without_history_stays_bare(monkeypatch) -> None:
    """A first-turn command has no context to add — the goal stays unchanged."""
    bus = _FakeBus()
    executor = _RecordingExecutor()
    mgr = _make_manager(executor, bus)
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE, harness="computer-use", prompt="open chrome"
    )
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", lambda _t: plan)

    await mgr._run_local_action_fast_path("öffne chrome")  # i18n-allow
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    assert (executor.args or {}).get("prompt") == "open chrome"


@pytest.mark.asyncio
async def test_cu_goal_context_falls_back_to_live_history(monkeypatch) -> None:
    """Classic-pipeline turns (no override) use the manager's own history."""
    from jarvis.core.protocols import BrainMessage

    bus = _FakeBus()
    executor = _RecordingExecutor()
    mgr = _make_manager(executor, bus)
    mgr._history = [
        BrainMessage(role="user", content="Open the Personal Jarvis server."),
        BrainMessage(role="assistant", content="On it."),
    ]
    utterance = "Du bist nicht im richtigen Server."  # i18n-allow: German speech-input fixture
    plan = LocalActionPlan(
        mode=LocalActionMode.COMPUTER_USE, harness="computer-use", prompt=utterance
    )
    monkeypatch.setattr("jarvis.brain.manager.match_local_action", lambda _t: plan)

    await mgr._run_local_action_fast_path(utterance)
    await asyncio.gather(*getattr(mgr, "_cu_background_tasks", set()))

    prompt = (executor.args or {}).get("prompt", "")
    assert prompt.startswith(utterance)
    assert "Personal Jarvis" in prompt, prompt
