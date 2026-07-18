from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.brain.tool_call_recovery import extract_leaked_tool_calls
from jarvis.brain.tool_use_loop import _MAX_TOOL_RESULT_CHARS, ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult


class _Tool:
    name = "dispatch_to_harness"
    schema: dict[str, Any] = {}


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def execute(
        self,
        tool: Any,
        args: dict[str, Any],
        **_: Any,
    ) -> ToolResult:
        self.calls.append((tool, args))
        return ToolResult(success=True, output="executed")


class _Brain:
    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(
                tool_call={
                    "id": "call_1",
                    "name": "dispatch_to_harness",
                    "input": {
                        "harness": "computer-use",
                        "prompt": "Wie kann ich bei Windows reinzoomen?",  # i18n-allow: simulated German user utterance under test
                    },
                }
            )
            yield BrainDelta(finish_reason="tool_use")
            return

        yield BrainDelta(content="Mit Windows-Taste plus Pluszeichen zoomst du rein.")
        yield BrainDelta(finish_reason="stop")


class _HugeOutputTool:
    name = "gmail"
    schema: dict[str, Any] = {}


class _ExecHuge:
    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        return ToolResult(success=True, output="X" * 50_000)


class _ToolThenAnswerBrain:
    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(tool_call={"id": "c1", "name": "gmail", "input": {}})
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Zusammengefasst.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_tool_result_is_capped_before_reaching_brain() -> None:
    """Systemic backstop (2026-07-01): no tool may flood the model context with
    an unbounded raw payload (a raw Gmail ``format=full`` message is ~23k chars).
    A ~50k-char tool output must be truncated — with a marker — before it is
    serialized into the tool-role message the brain sees on the next turn."""
    brain = _ToolThenAnswerBrain()
    loop = ToolUseLoop(
        brain,
        {"gmail": _HugeOutputTool()},
        _ExecHuge(),  # type: ignore[arg-type]
    )

    result = await loop.run([], user_utterance="was steht in meinen mails")

    assert "Zusammengefasst" in result.text
    tool_msgs = [
        m for m in brain.requests[1].messages if getattr(m, "role", None) == "tool"
    ]
    assert tool_msgs, "expected a tool-role message in the 2nd request"
    inner = tool_msgs[-1].content[0]["content"]
    assert len(inner) <= _MAX_TOOL_RESULT_CHARS + 200
    assert "truncated" in inner


@pytest.mark.asyncio
async def test_how_to_question_blocks_side_effect_tool() -> None:
    brain = _Brain()
    executor = _Executor()
    loop = ToolUseLoop(
        brain,
        {"dispatch_to_harness": _Tool()},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run(
        [],
        user_utterance="Wie kann ich bei Windows reinzoomen?",  # i18n-allow: simulated German user utterance under test
    )

    assert executor.calls == []
    assert "Windows-Taste" in result.text
    assert len(brain.requests) == 2
    tool_message = brain.requests[1].messages[-1].content
    assert "how-to" in str(tool_message).lower()


class _GmailTool:
    name = "gmail/list_messages"
    schema: dict[str, Any] = {}


class _NamedReadTool:
    schema: dict[str, Any] = {}

    def __init__(self, name: str) -> None:
        self.name = name


@pytest.mark.parametrize(
    "serialized",
    [
        (
            "For example, a provider might emit "
            '{"type":"tool_use","name":"gmail","input":{}} in prose.'
        ),
        '{"type":"tool_use","name":"gmail","input":"{bad json"}',
        '{"type":"tool_use","name":"gmail","input":["not","an","object"]}',
        "```json\n{\"type\":\"tool_use\",\"name\":\"gmail\",\"input\":{}}",
    ],
)
def test_leaked_call_recovery_rejects_untrusted_or_malformed_input(
    serialized: str,
) -> None:
    assert extract_leaked_tool_calls(serialized) == []


def test_leaked_call_recovery_accepts_one_strict_fenced_envelope() -> None:
    calls = extract_leaked_tool_calls(
        "```json\n"
        '{"type":"tool_use","name":"gmail","input":{"action":"list"}}'
        "\n```"
    )

    assert len(calls) == 1
    assert calls[0]["name"] == "gmail"
    assert calls[0]["input"] == {"action": "list"}


class _ExecStructuredRead:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def execute(
        self, tool: Any, args: dict[str, Any], **_: Any,
    ) -> ToolResult:
        self.calls.append((tool, args))
        return ToolResult(
            success=True,
            output={
                "from": "alerts@example.com",
                "subject": "Account notice",
                "snippet": "A grounded result from the connected service.",
            },
        )


class _LeakedReadThenAnswerBrain:
    """A provider emits a function call as text before normal synthesis."""

    def __init__(self, tool_name: str) -> None:
        self._tool_name = tool_name
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(
                content=(
                    '[{"type":"tool_use","name":"'
                    f'{self._tool_name}","input":{{"item_id":"m1"}}}}]'
                )
            )
            yield BrainDelta(finish_reason="stop")
            return
        yield BrainDelta(content="The connected service returned a grounded result.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    [
        "gmail",
        "streamline/query",
        "mcp.notebook/search",
    ],
)
async def test_text_leaked_read_call_rejoins_normal_tool_loop(
    tool_name: str,
) -> None:
    """Native, plugin, and MCP calls share exactly-once leak recovery."""
    brain = _LeakedReadThenAnswerBrain(tool_name)
    executor = _ExecStructuredRead()
    loop = ToolUseLoop(
        brain,
        {tool_name: _NamedReadTool(tool_name)},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run(
        [],
        user_utterance="Read the latest connected item.",
    )

    assert len(executor.calls) == 1
    assert executor.calls[0][1] == {"item_id": "m1"}
    assert result.executed_tool_names == {tool_name}
    assert result.text == "The connected service returned a grounded result."
    assert len(brain.requests) == 2
    assert any(message.role == "tool" for message in brain.requests[1].messages)


class _StructuredThenLeakedGmailBrain:
    """Exact incident shape: list call, leaked detail call, final answer."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(
                tool_call={
                    "id": "list-call",
                    "name": "gmail",
                    "input": {"action": "list_messages", "max_results": 10},
                }
            )
            yield BrainDelta(finish_reason="tool_use")
            return
        if len(self.requests) == 2:
            yield BrainDelta(
                content=(
                    '[{"type":"tool_use","name":"gmail","input":'
                    '{"action":"get_message","message_id":"m1"}}]'
                )
            )
            yield BrainDelta(finish_reason="stop")
            return
        yield BrainDelta(
            content="The newest unread message is an important account notice."
        )
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_structured_list_then_leaked_detail_call_synthesizes_once() -> None:
    brain = _StructuredThenLeakedGmailBrain()
    executor = _ExecStructuredRead()
    loop = ToolUseLoop(
        brain,
        {"gmail": _NamedReadTool("gmail")},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run([], user_utterance="Check my important Gmail messages.")

    assert [args["action"] for _, args in executor.calls] == [
        "list_messages",
        "get_message",
    ]
    assert result.executed_tool_names == {"gmail"}
    assert result.text == (
        "The newest unread message is an important account notice."
    )
    assert len(brain.requests) == 3


class _DuplicateEnvelopeBrain:
    def __init__(self, *, with_structured_call: bool) -> None:
        self._with_structured_call = with_structured_call
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            leaked = (
                '{"type":"tool_use","name":"gmail",'
                '"input":{"action":"get_message","message_id":"m1"}}'
            )
            yield BrainDelta(content=f"[{leaked},{leaked}]")
            if self._with_structured_call:
                yield BrainDelta(
                    tool_call={
                        "id": "structured-call",
                        "name": "gmail",
                        "input": {"action": "get_message", "message_id": "m1"},
                    }
                )
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="One grounded answer.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
@pytest.mark.parametrize("with_structured_call", [False, True])
async def test_duplicate_leaked_envelopes_execute_exactly_once(
    with_structured_call: bool,
) -> None:
    brain = _DuplicateEnvelopeBrain(with_structured_call=with_structured_call)
    executor = _ExecStructuredRead()
    loop = ToolUseLoop(
        brain,
        {"gmail": _NamedReadTool("gmail")},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run([], user_utterance="Read message m1.")

    assert len(executor.calls) == 1
    assert result.text == "One grounded answer."
    assert "tool_use" not in result.text


class _RepeatedLeakAcrossRoundsBrain:
    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) <= 2:
            yield BrainDelta(
                content=(
                    '[{"type":"tool_use","name":"gmail","input":'
                    '{"action":"get_message","message_id":"m1"}}]'
                )
            )
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="The result is available and grounded.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_duplicate_leak_across_rounds_executes_once_then_synthesizes() -> None:
    brain = _RepeatedLeakAcrossRoundsBrain()
    executor = _ExecStructuredRead()
    loop = ToolUseLoop(
        brain,
        {"gmail": _NamedReadTool("gmail")},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run([], user_utterance="Read message m1.")

    assert len(executor.calls) == 1
    assert len(brain.requests) == 3
    assert brain.requests[-1].tools == ()
    assert result.text == "The result is available and grounded."


class _ExecOK:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        self.calls.append((tool, args))
        return ToolResult(success=True, output="msg1, msg2, msg3, msg4, msg5")


class _BigContextBrain:
    """First turn: 'Einen Moment.' + a tool call, reporting a HUGE input-token
    count (a long voice session). Second turn: the actual answer."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(content="Einen Moment.")
            yield BrainDelta(tool_call={
                "id": "c1", "name": "gmail/list_messages", "input": {"max": 5},
            })
            # The re-sent prompt (system + tools + whole history) is ~60k tokens.
            yield BrainDelta(usage={"input_tokens": 60_000, "output_tokens": 40})
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Deine letzten 5 Mails: msg1 bis msg5.")
        yield BrainDelta(usage={"input_tokens": 60_000, "output_tokens": 30})
        yield BrainDelta(finish_reason="stop")


class _RunShellTool:
    name = "run_shell"
    schema: dict[str, Any] = {}


class _WikiRecallTool:
    name = "wiki-recall"
    schema: dict[str, Any] = {}


class _AliasCallingBrain:
    """Live incident 2026-07-05 (session 3e27dd8e, 19:49:56): the tool surface
    mixes hyphen names (wiki-recall) and underscore names (run_shell), so the
    model cross-normalized and called 'run-shell'. The exact-match lookup missed
    and the turn ended in the canned 'missing the right tool' refusal."""

    def __init__(self, called_name: str) -> None:
        self.requests: list[BrainRequest] = []
        self._called_name = called_name

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(tool_call={
                "id": "c1", "name": self._called_name, "input": {},
            })
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Hier ist das Ergebnis.")  # i18n-allow: German brain-output fixture under test
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_hyphenated_alias_resolves_to_underscore_tool() -> None:
    """'run-shell' from the model must execute the registered 'run_shell'."""
    brain = _AliasCallingBrain("run-shell")
    executor = _ExecOK()
    loop = ToolUseLoop(
        brain,
        {"run_shell": _RunShellTool(), "wiki-recall": _WikiRecallTool()},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run([], user_utterance="zeig mir den letzten Commit")

    assert executor.calls, "'run-shell' must resolve to the registered 'run_shell'"
    assert executor.calls[0][0].name == "run_shell"
    assert "Werkzeug" not in result.text, "must not speak the missing-tool refusal"
    assert "run_shell" in result.executed_tool_names


@pytest.mark.asyncio
async def test_underscore_alias_resolves_to_hyphenated_tool() -> None:
    """The reverse direction: 'wiki_recall' must execute 'wiki-recall'."""
    brain = _AliasCallingBrain("wiki_recall")
    executor = _ExecOK()
    loop = ToolUseLoop(
        brain,
        {"run_shell": _RunShellTool(), "wiki-recall": _WikiRecallTool()},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run([], user_utterance="such mal im Wiki nach dem Projekt")  # i18n-allow: German utterance fixture under test

    assert executor.calls, "'wiki_recall' must resolve to the registered 'wiki-recall'"
    assert executor.calls[0][0].name == "wiki-recall"
    assert "wiki-recall" in result.executed_tool_names


class _ExecWithDeniedLog(_ExecOK):
    """Executor fake that records guard-denied publications (Task-3 contract)."""

    def __init__(self) -> None:
        super().__init__()
        self.denied: list[tuple[str, str]] = []

    async def publish_guard_denied(self, tool_name, reason, *, trace_id=None):
        self.denied.append((tool_name, reason))


@pytest.mark.asyncio
async def test_unknown_tool_publishes_guard_denied_event() -> None:
    """A model-invented tool name must leave a visible trace (2026-07-06
    audit: the 'run-shell' incident produced ZERO events — the timeline
    could not show why the turn refused)."""
    brain = _AliasCallingBrain("totally_made_up_tool")
    executor = _ExecWithDeniedLog()
    loop = ToolUseLoop(
        brain,
        {"run_shell": _RunShellTool()},
        executor,  # type: ignore[arg-type]
    )

    await loop.run([], user_utterance="zeig mir den letzten Commit")

    assert executor.calls == []
    assert executor.denied, "unknown tool must publish a guard-denied event"
    assert executor.denied[0][0] == "totally_made_up_tool"
    assert "unknown tool name" in executor.denied[0][1]


class _SuccessfulAndUnknownBrain:
    """One valid call plus one stale name must produce a partial result."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(tool_call={"id": "ok", "name": "run_shell", "input": {}})
            yield BrainDelta(tool_call={"id": "stale", "name": "retired_tool", "input": {}})
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="The shell action succeeded; the retired action was skipped.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_unknown_tool_cannot_overwrite_successful_call() -> None:
    brain = _SuccessfulAndUnknownBrain()
    executor = _ExecWithDeniedLog()
    loop = ToolUseLoop(
        brain,
        {"run_shell": _RunShellTool()},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run([], user_utterance="Run the check and the optional follow-up.")

    assert executor.calls and executor.calls[0][0].name == "run_shell"
    assert executor.denied and executor.denied[0][0] == "retired_tool"
    assert "succeeded" in result.text
    assert "missing" not in result.text.lower()
    assert len(brain.requests) == 2


@pytest.mark.asyncio
async def test_howto_guard_publishes_guard_denied_event() -> None:
    brain = _Brain()
    executor = _ExecWithDeniedLog()
    loop = ToolUseLoop(
        brain,
        {"dispatch_to_harness": _Tool()},
        executor,  # type: ignore[arg-type]
    )

    await loop.run(
        [],
        user_utterance="Wie kann ich bei Windows reinzoomen?",  # i18n-allow: simulated German user utterance under test
    )

    assert executor.denied and "how-to" in executor.denied[0][1]


@pytest.mark.asyncio
async def test_ambiguous_alias_is_not_guessed() -> None:
    """If two registered tools collide on the normalized form, an inexact name
    must NOT silently pick one of them — the unknown-tool fallback stays."""

    class _HyphenTwin:
        name = "run-shell"
        schema: dict[str, Any] = {}

    brain = _AliasCallingBrain("Run-Shell")
    executor = _ExecOK()
    loop = ToolUseLoop(
        brain,
        {"run_shell": _RunShellTool(), "run-shell": _HyphenTwin()},
        executor,  # type: ignore[arg-type]
    )

    await loop.run([], user_utterance="zeig mir den letzten Commit")

    assert executor.calls == [], "an ambiguous alias must not execute either twin"


@pytest.mark.asyncio
async def test_tool_executes_despite_huge_per_turn_input_tokens() -> None:
    """Regression (live bug 2026-06-01): in a long conversation a single turn's
    re-sent prompt is ~50-60k tokens. The cumulative token budget must NOT be
    exhausted by that re-sent input — otherwise the loop aborts after the model
    asks for a tool but BEFORE executing it, and the user hears only the bare
    'Einen Moment.' ack (an AD-OE6 silent drop). The tool must run AND the loop
    must do a second turn to report the result."""
    brain = _BigContextBrain()
    executor = _ExecOK()
    loop = ToolUseLoop(
        brain,
        {"gmail/list_messages": _GmailTool()},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run(
        [],
        user_utterance="Kannst du mal die letzten 5 E-Mails sagen?",
    )

    assert executor.calls, "the Gmail tool must execute despite the large prompt"
    assert "msg1 bis msg5" in result.text or "5 Mails" in result.text
    assert len(brain.requests) == 2, "the loop must do a second turn to report"


class _SpawnWorkerTool:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _SpawnThenAnswerBrain:
    """Requests spawn_worker on round 1, answers inline on round 2."""

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if len(self.requests) == 1:
            yield BrainDelta(
                tool_call={
                    "id": "spawn_1",
                    "name": "spawn_worker",
                    "input": {"utterance": "irrelevant", "action": "research"},
                }
            )
            yield BrainDelta(finish_reason="tool_use")
            return
        yield BrainDelta(content="Answering inline instead.")
        yield BrainDelta(finish_reason="stop")


@pytest.mark.asyncio
async def test_conversational_turn_blocks_llm_chosen_spawn() -> None:
    """Explicit-delegation gate (mandate 2026-07-18, voice sessions 08:25 +
    08:29): the model chose spawn_worker on a plain conversational remark —
    the gate must refuse execution and redirect the model to answer inline."""
    from jarvis.brain.spawn_gate import OFFER_WINDOW

    OFFER_WINDOW.disarm()
    brain = _SpawnThenAnswerBrain()
    executor = _Executor()
    loop = ToolUseLoop(
        brain,
        {"spawn_worker": _SpawnWorkerTool()},
        executor,  # type: ignore[arg-type]
    )

    result = await loop.run(
        [],
        user_utterance="Ah, ich will gucken, wo ich als nächstes hinziehe.",  # i18n-allow: live utterance
    )

    assert executor.calls == [], "unrequested spawn must never reach the executor"
    assert "Answering inline" in result.text
    tool_message = str(brain.requests[1].messages[-1].content)
    assert "did not explicitly ask" in tool_message


@pytest.mark.asyncio
async def test_explicit_delegation_request_executes_spawn() -> None:
    from jarvis.brain.spawn_gate import OFFER_WINDOW

    OFFER_WINDOW.disarm()
    brain = _SpawnThenAnswerBrain()
    executor = _Executor()
    loop = ToolUseLoop(
        brain,
        {"spawn_worker": _SpawnWorkerTool()},
        executor,  # type: ignore[arg-type]
    )

    await loop.run(
        [],
        user_utterance="Spawne einen Subagenten und recherchier Umzugsorte.",  # i18n-allow: DE trigger
    )

    assert len(executor.calls) == 1, "an explicit spawn request must execute"
