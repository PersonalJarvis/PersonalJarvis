"""Archived requests remain available without masquerading as the current turn."""

import json

import pytest

from jarvis.live.config import LiveConfig
from jarvis.live.recovery import HISTORY_CONTEXT_RULE, seed_messages


def transcript(messages):
    assert len(messages) == 1
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"][0]["type"] == "output_text"
    header, data = messages[0]["content"][0]["text"].split("\n", 1)
    assert "not new requests" in header
    return json.loads(data)


def test_unanswered_agent_request_is_not_replayed_as_a_user_turn():
    previous = "Prompt the Codex agent in the Computer Use workspace."
    seed = seed_messages([
        {"role": "user", "delta": "When should I post?"},
        {"role": "assistant", "delta": "Try the afternoon."},
        {"role": "user", "delta": previous},
    ], [])
    assert transcript(seed)[-1] == {"role": "user", "text": previous}
    current = {"role": "user", "content": [{"type": "input_text", "text": "What's up?"}]}
    assert [message for message in [*seed, current] if message["role"] == "user"] == [current]


def test_explicit_followup_keeps_previous_facts_speakers_and_tool_receipts():
    seed = seed_messages([
        {"role": "user", "delta": "Use the blue "},
        {"role": "user", "delta": "folder."},
        {"role": "assistant", "delta": "The folder is ready."},
    ], [{"tool": "create_folder", "result": {"success": True}}])
    history = transcript(seed)
    assert history[:2] == [
        {"role": "user", "text": "Use the blue folder."},
        {"role": "assistant", "text": "The folder is ready."},
    ]
    assert "create_folder" in history[-1]["text"]
    assert '"success": true' in history[-1]["text"]


@pytest.mark.parametrize("text", ["x", '\\"\n\t', "\U0001f419\u00e9\u4e2d"])
def test_large_history_preserves_framing_valid_json_and_byte_budget(text):
    fragments = [
        {"role": "user" if i % 2 else "assistant", "delta": text * 4000 + f" end-{i}"}
        for i in range(40)
    ]
    seed = seed_messages(fragments, [])
    history = transcript(seed)
    assert len(seed[0]["content"][0]["text"].encode("utf-8")) <= 5000
    assert history[-1]["text"].endswith("end-39")
    assert all(message["role"] in {"user", "assistant"} for message in history)


def test_empty_history_adds_no_synthetic_turn():
    assert seed_messages([], []) == []


@pytest.mark.parametrize("auth_mode", ["api_key", "chatgpt_subscription"])
def test_voice_and_reasoning_share_current_request_priority(auth_mode):
    config = LiveConfig(
        configured=True, backend_model="test-model", auth_mode=auth_mode,
        subscription_backend_model="test-model",
    )
    voice = config.session_config(language="auto", tools=[])
    backend = config.backend_config(language="auto", tools=[])
    assert HISTORY_CONTEXT_RULE in voice["instructions"]
    assert HISTORY_CONTEXT_RULE in backend["instructions"]
