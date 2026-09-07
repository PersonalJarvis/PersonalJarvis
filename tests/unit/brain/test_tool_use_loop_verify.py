"""The verification pass: where the loop would finish, an answer is checked
against what actually ran, and a revision is one more round WITH tools."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from jarvis.brain.loop_control import LoopControl, VerifyOutcome, VerifyRequest
from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest, ToolResult


class _Tool:
    name = "gmail"
    schema: dict[str, Any] = {}
    description = "mail"


class _Executor:
    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        return ToolResult(success=True, output="two unread")


class _Answers:
    """Answers a script of texts, calling the tool once at the start."""

    def __init__(self, texts: list[str], *, tool_first: bool = True) -> None:
        self.requests: list[BrainRequest] = []
        self._texts = list(texts)
        self._tool_first = tool_first

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        if self._tool_first and len(self.requests) == 1:
            yield BrainDelta(tool_call={"id": "c1", "name": "gmail", "input": {}})
            yield BrainDelta(finish_reason="tool_use")
            return
        text = self._texts.pop(0) if self._texts else "(done)"
        yield BrainDelta(content=text)
        yield BrainDelta(finish_reason="stop")


def _loop(brain: Any, control: LoopControl) -> ToolUseLoop:
    return ToolUseLoop(brain, {"gmail": _Tool()}, _Executor(), loop_control=control)


async def test_a_revise_verdict_runs_one_more_round_with_tools():
    seen: list[VerifyRequest] = []

    async def verify(req: VerifyRequest, ask: Any) -> VerifyOutcome:
        seen.append(req)
        if req.attempt == 0:
            return VerifyOutcome(accepted=False, instruction="Name the senders.")
        return VerifyOutcome(accepted=True)

    brain = _Answers(["Two unread.", "Two unread, from Ana and Ben."])
    out = await _loop(brain, LoopControl(verify=verify)).run(
        [BrainMessage(role="user", content="check my mail")]
    )
    # The revision IS the answer, not draft + revision glued together.
    assert out.text == "Two unread, from Ana and Ben."
    assert len(brain.requests) == 3
    assert brain.requests[2].tools  # a revision may look again
    assert any("Name the senders." in str(m.content) for m in brain.requests[2].messages)
    # The verifier sees what really ran, not what the answer claims.
    assert [r.name for r in seen[0].tool_log] == ["gmail"]
    assert seen[0].tool_log[0].ok is True
    assert seen[0].user_text == "check my mail"


async def test_verify_stops_at_the_cap():
    async def always_revise(req: VerifyRequest, ask: Any) -> VerifyOutcome:
        return VerifyOutcome(accepted=False, instruction="again")

    brain = _Answers(["a", "b", "c", "d", "e"])
    control = LoopControl(verify=always_revise, max_verify=2)
    out = await _loop(brain, control).run([BrainMessage(role="user", content="hi")])
    # one tool round + the first answer + exactly two revisions
    assert len(brain.requests) == 4
    assert out.text == "c"


async def test_the_verifier_is_asked_through_the_turns_own_brain():
    asked: list[tuple[str, str]] = []

    async def verify(req: VerifyRequest, ask: Any) -> VerifyOutcome:
        answer = await ask("be strict", "does it hold?")
        asked.append(("verdict", answer))
        return VerifyOutcome(accepted=True)

    brain = _Answers(["Two unread.", "yes"], tool_first=False)
    await _loop(brain, LoopControl(verify=verify)).run(
        [BrainMessage(role="user", content="hi")]
    )
    assert asked == [("verdict", "yes")]
    # The verifier's own question carries no tools: it judges, it never acts.
    assert brain.requests[-1].tools == ()
    assert brain.requests[-1].system == "be strict"


async def test_a_verifier_that_raises_accepts_the_turn():
    async def boom(req: VerifyRequest, ask: Any) -> VerifyOutcome:
        raise RuntimeError("verifier is down")

    brain = _Answers(["Two unread."])
    out = await _loop(brain, LoopControl(verify=boom)).run(
        [BrainMessage(role="user", content="check my mail")]
    )
    assert out.text == "Two unread."


async def test_an_accepted_verdict_ends_the_turn():
    async def ok(req: VerifyRequest, ask: Any) -> VerifyOutcome:
        return VerifyOutcome(accepted=True)

    brain = _Answers(["Two unread."])
    out = await _loop(brain, LoopControl(verify=ok)).run(
        [BrainMessage(role="user", content="check my mail")]
    )
    assert out.text == "Two unread." and len(brain.requests) == 2
