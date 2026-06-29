"""Wave 1 — router-tier ``computer_use`` tool.

The router brain must have a first-class, clearly-described tool to drive the
live desktop (open apps, click, type, scroll — anything done with mouse and
keyboard on THIS machine). Before this tool existed the router could only reach
the computer-use harness through the two-level ``dispatch_to_harness`` +
magic-``harness``-string indirection, whose schema description never mentioned
desktop control — so the model picked the wrong tool (or invented one) and the
user heard a refusal for "öffne ein Terminal".

These tests pin the new tool's identity, schema and dispatch behaviour without
an LLM: the tool forwards the goal verbatim to the canonical ``computer-use``
harness.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from jarvis.core.protocols import ExecutionContext, HarnessResult, HarnessTask
from jarvis.plugins.tool.computer_use_tool import _ctx_output_language


def _lang_ctx(user_utterance: str, config: dict) -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid.uuid4(),
        user_utterance=user_utterance,
        config=config,
        memory_read=None,
    )


def test_ctx_output_language_prefers_loop_stamped_language() -> None:
    # The tool-use loop stamps the turn's resolved output language (honors the
    # reply_language pin AND conversation stickiness) into ctx.config. A one-word
    # English "Now" in a German conversation must read back German, not English.
    ctx = _lang_ctx("Now", {"output_language": "de"})
    assert _ctx_output_language(ctx) == "de"


def test_ctx_output_language_falls_back_to_detection_without_stamp() -> None:
    # Tests / minimal wiring: no stamp → detect from the user's own words.
    ctx = _lang_ctx("Mach das Licht an", {})  # i18n-allow: German voice fixture
    assert _ctx_output_language(ctx) == "de"
    assert _ctx_output_language(_lang_ctx("Turn on the lights please now", {})) == "en"


class _FakeHarnessManager:
    """Records ``dispatch(name, task)`` calls and yields one success result."""

    def __init__(self) -> None:
        self.dispatched: list[tuple[str, str]] = []

    async def dispatch(self, name: str, task: HarnessTask) -> AsyncIterator[HarnessResult]:
        self.dispatched.append((name, task.prompt))
        yield HarnessResult(stdout="done", exit_code=0, is_final=True)


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid.uuid4(),
        user_utterance="öffne ein Terminal",
        config={},
        memory_read=None,
    )


def test_tool_identity_and_schema() -> None:
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    tool = ComputerUseTool(manager=_FakeHarnessManager())
    # Underscore name = what the LLM sees (hyphenated "computer-use" is the
    # entry-point/harness name, kept separate per the factory convention).
    assert tool.name == "computer_use"
    assert "goal" in tool.schema["properties"]
    assert tool.schema["required"] == ["goal"]
    # The description must clearly signal LIVE-desktop control so the model
    # selects it over spawn_openclaw / dispatch_to_harness.
    assert "desktop" in tool.description.lower()


async def test_dispatches_goal_to_computer_use_harness() -> None:
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    fake = _FakeHarnessManager()
    tool = ComputerUseTool(manager=fake)

    result = await tool.execute({"goal": "open a terminal"}, _ctx())

    assert result.success
    assert fake.dispatched == [("screenshot", "open a terminal")]


async def test_empty_goal_is_rejected_without_dispatch() -> None:
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    fake = _FakeHarnessManager()
    tool = ComputerUseTool(manager=fake)

    result = await tool.execute({"goal": "   "}, _ctx())

    assert not result.success
    assert fake.dispatched == []


# ---------------------------------------------------------------------------
# Wave 0 (frontier-speed, 2026-06-09): background offload.
#
# Run inline, the mission lives inside the brain turn's task — the speech
# stall guard's task.cancel() (or any turn unwind) beheads a healthy desktop
# mission. With a bus wired (production wiring via factory.py), the tool must
# return an immediate ACK and run the mission as a background task whose
# outcome is ALWAYS announced (AD-OE1/OE5/OE6).
# ---------------------------------------------------------------------------


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


class _SlowHarnessManager(_FakeHarnessManager):
    """Dispatch that takes a moment — long enough to prove the ACK returned
    before the mission finished."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def dispatch(self, name: str, task) -> AsyncIterator[HarnessResult]:
        self.started.set()
        await self.release.wait()
        self.dispatched.append((name, task.prompt))
        yield HarnessResult(stdout="[cu] done (verified)", exit_code=0, is_final=True)


async def test_with_bus_returns_immediate_ack_and_announces_completion() -> None:
    import asyncio

    from jarvis.core.events import AnnouncementRequested
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    bus = _FakeBus()
    fake = _SlowHarnessManager()
    tool = ComputerUseTool(bus=bus, manager=fake)

    # wait_for: in the pre-fix inline implementation execute() blocks on the
    # never-released dispatch — the test must FAIL fast, not hang.
    result = await asyncio.wait_for(
        tool.execute({"goal": "open chrome"}, _ctx()), timeout=2.0,
    )

    # Immediate ACK — the mission has NOT finished yet.
    assert result.success
    assert fake.dispatched == []
    await asyncio.wait_for(fake.started.wait(), timeout=2.0)

    # Let the mission finish; its outcome must be announced on the bus.
    fake.release.set()
    for _ in range(200):
        if any(isinstance(e, AnnouncementRequested) for e in bus.events):
            break
        await asyncio.sleep(0.01)
    completions = [
        e for e in bus.events
        if isinstance(e, AnnouncementRequested) and e.kind == "completion"
    ]
    assert len(completions) == 1
    assert fake.dispatched == [("screenshot", "open chrome")]


async def test_with_bus_failure_is_announced_not_silent() -> None:
    import asyncio

    from jarvis.core.events import AnnouncementRequested
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    class _FailingManager(_FakeHarnessManager):
        async def dispatch(self, name: str, task) -> AsyncIterator[HarnessResult]:
            self.dispatched.append((name, task.prompt))
            yield HarnessResult(
                stderr="[cu] goal not verifiably achieved", exit_code=5,
                is_final=True,
            )

    bus = _FakeBus()
    tool = ComputerUseTool(bus=bus, manager=_FailingManager())

    result = await tool.execute({"goal": "open chrome"}, _ctx())
    assert result.success  # ACK — outcome arrives via announcement

    for _ in range(200):
        if any(isinstance(e, AnnouncementRequested) for e in bus.events):
            break
        await asyncio.sleep(0.01)
    completions = [
        e for e in bus.events
        if isinstance(e, AnnouncementRequested) and e.kind == "completion"
    ]
    assert len(completions) == 1  # AD-OE6: zero silent drops


async def test_without_bus_keeps_synchronous_contract() -> None:
    """No bus (tests / minimal wiring) → the old inline behaviour stays."""
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    fake = _FakeHarnessManager()
    tool = ComputerUseTool(manager=fake)

    result = await tool.execute({"goal": "open a terminal"}, _ctx())

    assert result.success
    assert fake.dispatched == [("screenshot", "open a terminal")]


# ---------------------------------------------------------------------------
# suppress_response flag + localized ACK (fix for session 71f2d2de, 2026-06-18)
#
# Root cause: ComputerUseTool lacked suppress_response=True, so tool_use_loop
# fed the internal English steering instruction into a second brain iteration
# and Gemini echoed it verbatim as its own assistant bubble. The fix: set the
# flag (tool_use_loop honours it at lines 662-666 / 709-728) and return the
# pre-existing, localized cu_dispatch_ack phrase instead of the English string.
# ---------------------------------------------------------------------------


def test_suppress_response_flag_is_true() -> None:
    """ComputerUseTool must carry suppress_response=True so tool_use_loop skips
    the second brain iteration and takes the output verbatim."""
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    assert ComputerUseTool.suppress_response is True


def _ctx_de() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid.uuid4(),
        user_utterance="öffne ein Terminal",  # i18n-allow: DE language-detection fixture
        config={},
        memory_read=None,
    )


def _ctx_en() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid.uuid4(),
        user_utterance="open a terminal",  # English utterance
        config={},
        memory_read=None,
    )


async def _drain_background(bus: _FakeBus, fake: _SlowHarnessManager) -> None:
    """Release the slow fake and wait for its background mission to finish.

    Mirrors the drain in test_with_bus_returns_immediate_ack_and_announces_
    completion so the asyncio.Task spawned by execute() (kept alive by
    tool._background_tasks) completes inside the test — no pending task survives
    to trigger a "Task destroyed while pending" warning under a slower fake or
    eager loop teardown.
    """
    from jarvis.core.events import AnnouncementRequested

    await asyncio.wait_for(fake.started.wait(), timeout=2.0)
    fake.release.set()
    for _ in range(200):
        if any(isinstance(e, AnnouncementRequested) for e in bus.events):
            break
        await asyncio.sleep(0.01)
    assert any(isinstance(e, AnnouncementRequested) for e in bus.events)


async def test_with_bus_ack_is_german_for_german_utterance() -> None:
    """A German-language turn must yield the German cu_dispatch_ack phrase."""
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool
    from jarvis.voice.action_phrases import action_phrase

    bus = _FakeBus()
    fake = _SlowHarnessManager()
    tool = ComputerUseTool(bus=bus, manager=fake)

    result = await tool.execute({"goal": "Terminal öffnen"}, _ctx_de())  # i18n-allow: DE fixture

    assert result.success
    expected = action_phrase("cu_dispatch_ack", "de")
    assert result.output == expected, f"Got: {result.output!r}"

    # Drain the background mission so no pending task survives the test.
    await _drain_background(bus, fake)


async def test_with_bus_ack_is_english_for_english_utterance() -> None:
    """An English-language turn must yield the English cu_dispatch_ack phrase."""
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool
    from jarvis.voice.action_phrases import action_phrase

    bus = _FakeBus()
    fake = _SlowHarnessManager()
    tool = ComputerUseTool(bus=bus, manager=fake)

    result = await tool.execute({"goal": "open a terminal"}, _ctx_en())

    assert result.success
    expected = action_phrase("cu_dispatch_ack", "en")
    assert result.output == expected, f"Got: {result.output!r}"

    # Drain the background mission so no pending task survives the test.
    await _drain_background(bus, fake)


async def test_with_bus_ack_contains_no_internal_english_instruction() -> None:
    """The English internal steering strings must NEVER appear in tool output.

    Before the fix, the output was the raw English instruction the model would
    echo: 'Desktop mission started in the background ... Reply with a brief
    acknowledgement only ...'. This string is an implementation detail — it must
    not reach the user.
    """
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    for goal, ctx in (
        ("Terminal öffnen", _ctx_de()),  # i18n-allow: DE fixture
        ("open a terminal", _ctx_en()),
    ):
        bus = _FakeBus()
        fake = _SlowHarnessManager()
        tool = ComputerUseTool(bus=bus, manager=fake)

        result = await tool.execute({"goal": goal}, ctx)

        assert result.output is not None
        out = str(result.output)
        assert "Reply with a brief acknowledgement" not in out, (
            f"Internal steering instruction leaked into output: {out!r}"
        )
        assert "Desktop mission started in the background" not in out, (
            f"Internal steering instruction leaked into output: {out!r}"
        )

        # Drain the background mission so no pending task survives the test.
        await _drain_background(bus, fake)


# ---------------------------------------------------------------------------
# Success readback surfaces the verifier observation (fix for session 241a1984,
# 2026-06-18). "open the browser and check which tabs I have open" → CU opened
# Chrome, the verifier's observation ("...shows the active tab X") landed in
# stdout, but _run_background discarded it and spoke only "Done." On success the
# completion announcement must carry the observation so an informational request
# is actually answered.
# ---------------------------------------------------------------------------


async def _collect_completion(bus: _FakeBus):
    from jarvis.core.events import AnnouncementRequested

    for _ in range(200):
        if any(isinstance(e, AnnouncementRequested) for e in bus.events):
            break
        await asyncio.sleep(0.01)
    completions = [
        e
        for e in bus.events
        if isinstance(e, AnnouncementRequested) and e.kind == "completion"
    ]
    return completions


async def test_success_readback_surfaces_verifier_observation() -> None:
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool
    from jarvis.voice.action_phrases import action_phrase

    class _ProofManager(_FakeHarnessManager):
        async def dispatch(self, name, task) -> AsyncIterator[HarnessResult]:
            self.dispatched.append((name, task.prompt))
            yield HarnessResult(
                stdout="[cu] done at step 2.1 (verified: The browser is open showing tab 'Gmail')",
                exit_code=0,
                is_final=True,
            )

    bus = _FakeBus()
    tool = ComputerUseTool(bus=bus, manager=_ProofManager())
    result = await tool.execute({"goal": "open chrome and check my tabs"}, _ctx_en())
    assert result.success  # immediate ACK

    completions = await _collect_completion(bus)
    assert len(completions) == 1
    text = completions[0].text
    assert "Gmail" in text, f"verifier observation must be spoken, got: {text!r}"
    assert text != action_phrase("cu_done", "en"), "must not be the content-free Done phrase"


# ---------------------------------------------------------------------------
# Output-language threading to the verifier (fix 2026-06-27). The verifier's
# spoken `proof` was English even in a German turn ("Erledigt — The file explorer
# window is open ..."). The resolved turn output language must reach the harness
# via task.env so the verifier writes proof in the user's language. This pins the
# whole chain: tool → dispatch_to_harness → HarnessTask.env.
# ---------------------------------------------------------------------------


class _EnvCapturingManager(_FakeHarnessManager):
    def __init__(self) -> None:
        super().__init__()
        self.task_envs: list[dict] = []

    async def dispatch(self, name, task) -> AsyncIterator[HarnessResult]:
        self.task_envs.append(dict(getattr(task, "env", None) or {}))
        self.dispatched.append((name, task.prompt))
        yield HarnessResult(stdout="[cu] done at step 1", exit_code=0, is_final=True)


async def test_tool_threads_output_language_into_harness_env() -> None:
    from jarvis.harness.screenshot_only_loop import _OUTPUT_LANGUAGE_ENV_KEY
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool

    bus = _FakeBus()
    fake = _EnvCapturingManager()
    tool = ComputerUseTool(bus=bus, manager=fake)

    # A German conversation (loop-stamped output language) must arrive at the
    # harness as env["JARVIS_OUTPUT_LANGUAGE"] = "de".
    ctx = ExecutionContext(
        trace_id=uuid.uuid4(),
        user_utterance="öffne den explorer",  # i18n-allow: DE fixture
        config={"output_language": "de"},
        memory_read=None,
    )
    result = await tool.execute({"goal": "öffne den explorer"}, ctx)  # i18n-allow: DE fixture
    assert result.success

    await _collect_completion(bus)
    assert fake.task_envs, "harness was never dispatched"
    assert fake.task_envs[0].get(_OUTPUT_LANGUAGE_ENV_KEY) == "de"


async def test_success_readback_falls_back_to_done_without_proof() -> None:
    from jarvis.plugins.tool.computer_use_tool import ComputerUseTool
    from jarvis.voice.action_phrases import action_phrase

    class _NoProofManager(_FakeHarnessManager):
        async def dispatch(self, name, task) -> AsyncIterator[HarnessResult]:
            self.dispatched.append((name, task.prompt))
            yield HarnessResult(stdout="[cu] done at step 1", exit_code=0, is_final=True)

    bus = _FakeBus()
    tool = ComputerUseTool(bus=bus, manager=_NoProofManager())
    result = await tool.execute({"goal": "open chrome"}, _ctx_en())
    assert result.success

    completions = await _collect_completion(bus)
    assert len(completions) == 1
    assert completions[0].text == action_phrase("cu_done", "en")
