"""Lossless tool-output transport: fewer tokens without limiting model evidence."""

import json
from types import SimpleNamespace

import pytest

from jarvis.brain.tool_use_loop import ToolUseLoop
from jarvis.core.protocols import BrainDelta, BrainMessage, ToolResult
from jarvis.live.session import LiveVoiceSession
from jarvis.plugins.brain._openai_base import (
    _chat_messages_to_responses_input,
    _to_openai_messages,
)


def _message(content, call_id="call_1"):
    return BrainMessage(role="tool", content=content, tool_call_id=call_id)


def _envelope(content):
    return [{"type": "tool_result", "tool_use_id": "call_1", "content": content}]


@pytest.mark.parametrize(
    "content",
    ["", "  first\n\nsecond  ", "repeat " * 40_000],
    ids=["empty", "whitespace", "large-result"],
)
def test_canonical_output_is_verbatim_without_a_new_size_limit(content):
    wire = _to_openai_messages((_message(_envelope(content)),), None)
    assert wire == [{"role": "tool", "content": content, "tool_call_id": "call_1"}]
    _, responses = _chat_messages_to_responses_input(wire)
    assert responses == [{"type": "function_call_output", "call_id": "call_1", "output": content}]


def test_json_result_is_not_encoded_twice_or_stripped_of_failure_evidence():
    payload = {
        "success": False,
        "output": {"items": [1, 1, None], "text": '日本語 • "quoted"\n  indentation'},
        "error": "denied",
        "confirmation_required": True,
    }
    original = json.dumps(payload, ensure_ascii=False)
    wire = _to_openai_messages((_message(_envelope(original)),), None)[0]
    assert wire["content"] == original
    assert json.loads(wire["content"]) == payload
    assert len(wire["content"]) < len(json.dumps(_envelope(original)))


@pytest.mark.parametrize(
    "content,call_id",
    [
        (_envelope("a") + _envelope("b"), "call_1"),
        (_envelope("do not discard mismatched evidence"), "different_call"),
        (_envelope("no outer call id"), None),
        ([{"type": "text", "text": "keep every block"}], "call_1"),
        ([{**_envelope("failed")[0], "is_error": True}], "call_1"),
        ([{**_envelope("keep metadata")[0], "annotations": {"source": "original"}}], "call_1"),
    ],
)
def test_noncanonical_blocks_and_metadata_round_trip_in_full(content, call_id):
    wire = _to_openai_messages((_message(content, call_id),), None)[0]
    assert json.loads(wire["content"]) == content
    assert wire["tool_call_id"] == (call_id or "")


def test_user_and_assistant_text_and_tool_arguments_are_not_rewritten():
    args = {"text": '  café\n日本語 "value"  ', "repeat": [1, 1, 2]}
    original = (
        BrainMessage(role="user", content="  Keep my exact wording.\n"),
        BrainMessage(role="assistant", content="  Same here.\n"),
        BrainMessage(
            role="assistant",
            content=[
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "read_data",
                    "input": args,
                }
            ],
        ),
    )
    wire = _to_openai_messages(original, "Keep every fact.")
    assert wire[1]["content"] == original[0].content
    assert wire[2]["content"] == original[1].content
    assert json.loads(wire[3]["tool_calls"][0]["function"]["arguments"]) == args


def test_plain_tool_strings_remain_opaque():
    content = '[{"type":"tool_result","content":"literal user document"}]'
    wire = _to_openai_messages((_message(content),), None)
    assert wire[0]["content"] == content


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_tool_loop_compacts_structure_but_preserves_all_evidence(success):
    payload = {"items": [{"text": "  café\n日本語  ", "index": i} for i in range(40)]}

    class Brain:
        requests = []

        async def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                yield BrainDelta(tool_call={"id": "call_1", "name": "read_data", "input": {}})
                yield BrainDelta(finish_reason="tool_use")
            else:
                yield BrainDelta(content="Verified response.")
                yield BrainDelta(finish_reason="stop")

    class Executor:
        async def execute(self, *args, **kwargs):
            return ToolResult(success=success, output=payload, error=None if success else "denied")

    brain = Brain()
    loop = ToolUseLoop(
        brain, {"read_data": SimpleNamespace(name="read_data", schema={})}, Executor()
    )
    await loop.run([], user_utterance="Read the dataset.")
    messages = brain.requests[-1].messages
    wire = next(m for m in _to_openai_messages(messages, None) if m["role"] == "tool")
    expected = {"success": success, "output": payload, "error": None if success else "denied"}
    assert json.loads(wire["content"]) == expected
    assert len(wire["content"]) < len(json.dumps(expected, ensure_ascii=False))


@pytest.mark.asyncio
async def test_live_result_keeps_unicode_and_every_list_item():
    sent = []
    payload = {"success": True, "output": ["  日本語\n  café " for _ in range(400)]}

    async def execute(*args):
        return dict(payload)

    async def send(event):
        sent.append(event)

    session = LiveVoiceSession(
        session_id="s",
        send_binary=send,
        send_json=send,
        providers=[SimpleNamespace(name="test")],
        config=SimpleNamespace(brain=SimpleNamespace(reply_language="en")),
    )
    session._tools = SimpleNamespace(execute=execute, end_requested=False)
    session._connection = SimpleNamespace(send=send)
    await session._run_calls([{"call_id": "call_1", "name": "read_data", "arguments": "{}"}], 0)
    output = sent[0]["item"]["output"]
    assert json.loads(output) == {**payload, "artifacts": []}
    assert "日本語" in output
    assert sent[-1] == {"type": "response.create"}
