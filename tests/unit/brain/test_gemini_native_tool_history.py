"""Gemini tool-loop history must ride as NATIVE function parts.

Live forensic 2026-07-17 (delegated research turns, and turn af736681
rounds 4-5): the old conversion JSON-dumped an assistant tool_use turn into
plain text, so from round 2 of a tool loop Gemini saw its OWN prior calls as
JSON prose and mimicked that format — every later "tool call" leaked as text
("tool_use_loop: recovered 1 text-serialized tool call(s)") and had to be
repaired by the lossy leak-recovery parser, costing decode time and rounds.

Gemini 3 thinking models stamp every native functionCall with a
``thought_signature`` and 400 on a replayed call WITHOUT it — so native
replay applies exactly to signature-carrying calls (all Gemini-originated
ones; the stream loop captures the signature), while signature-less calls
(leak-recovered, cross-provider history) keep the proven text form.

Contract under test:
  1. An assistant tool_use part WITH a thought_signature converts to a
     native ``functionCall`` part (never a JSON text dump), wire-sanitized
     name, signature attached verbatim.
  2. A tool-result message converts to a ``functionResponse`` carrying the
     unwrapped payload text and the same sanitized name.
  3. A signature-less tool_use part keeps the legacy text serialization
     (valid wire format; native replay would 400).
  4. Plain text turns and user messages keep their previous shape.
"""
from __future__ import annotations

import json

from jarvis.core.protocols import BrainMessage
from jarvis.plugins.brain.gemini import _to_gemini_contents

_NAME_MAP = {"github/search": "github_search"}


def _tool_loop_history() -> tuple[BrainMessage, ...]:
    return (
        BrainMessage(role="user", content="find the repo"),
        BrainMessage(
            role="assistant",
            content=[
                {"type": "text", "text": "Looking it up."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "github/search",
                    "input": {"query": "personal jarvis"},
                    "thought_signature": "c2lnbmF0dXJl",
                },
            ],
        ),
        BrainMessage(
            role="tool",
            content=[{
                "type": "tool_result",
                "tool_use_id": "call_1",
                "content": '{"success": true, "output": "1 repo found"}',
            }],
            tool_call_id="call_1",
            name="github/search",
        ),
    )


def test_assistant_tool_use_becomes_native_function_call() -> None:
    contents = _to_gemini_contents(_tool_loop_history(), _NAME_MAP)

    model_turn = contents[1]
    assert model_turn["role"] == "model"
    assert {"text": "Looking it up."} in model_turn["parts"]
    call_parts = [p for p in model_turn["parts"] if "functionCall" in p]
    assert call_parts, "tool_use must convert to a functionCall part"
    call = call_parts[0]["functionCall"]
    assert call["name"] == "github_search"  # wire-sanitized, matches declaration
    assert call["args"] == {"query": "personal jarvis"}
    # Gemini 3 validation: the original signature must ride on the part.
    assert call_parts[0]["thought_signature"] == "c2lnbmF0dXJl"
    # The leak trigger: no part may contain the JSON-dumped tool_use text.
    for part in model_turn["parts"]:
        assert "tool_use" not in json.dumps(part.get("text", ""))


def test_tool_result_becomes_unwrapped_function_response() -> None:
    contents = _to_gemini_contents(_tool_loop_history(), _NAME_MAP)

    tool_turn = contents[2]
    assert tool_turn["role"] == "user"
    response = tool_turn["parts"][0]["functionResponse"]
    assert response["name"] == "github_search"
    # Unwrapped payload — not the serialized envelope list.
    assert response["response"]["result"] == (
        '{"success": true, "output": "1 repo found"}'
    )
    assert "tool_result" not in response["response"]["result"]


def test_signature_less_tool_use_keeps_text_form() -> None:
    contents = _to_gemini_contents(
        (
            BrainMessage(
                role="assistant",
                content=[{
                    "type": "tool_use",
                    "id": "call_x",
                    "name": "search_web",
                    "input": {"query": "divo"},
                }],
            ),
        ),
    )

    parts = contents[0]["parts"]
    assert all("functionCall" not in p for p in parts), (
        "a signature-less call must NOT replay natively — Gemini 3 rejects "
        "functionCall parts without their thought_signature (400)"
    )
    assert any("tool_use" in str(p.get("text", "")) for p in parts)


def test_plain_turns_keep_their_shape() -> None:
    contents = _to_gemini_contents(
        (
            BrainMessage(role="user", content="hello"),
            BrainMessage(role="assistant", content="hi there"),
        ),
    )

    assert contents == [
        {"role": "user", "parts": [{"text": "hello"}]},
        {"role": "model", "parts": [{"text": "hi there"}]},
    ]
