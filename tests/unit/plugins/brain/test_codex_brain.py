"""CodexBrain: structured-prompt mode + API-key -> ChatGPT-CLI crossover.

Live 2026-07-18: the maintainer pays for the ChatGPT subscription, yet every
wiki extraction died on the SEPARATE, throttled OpenAI API key (RateLimitError
HTTP 429) — the subscription CLI was never tried because the API key existed.
And even when the CLI ran, the conversational prompt wrapper ("answer in one
to three short sentences, plain text only") made the wiki's JSON contract
unfulfillable by instruction. These tests pin both fixes.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain import codex as codex_module
from jarvis.plugins.brain.codex import CodexBrain


def _wiki_request() -> BrainRequest:
    return BrainRequest(
        messages=(BrainMessage(role="user", content="Source content here."),),
        system="Return ONLY a single JSON array. No prose before or after.",
        max_tokens=900,
        temperature=0.2,
        stream=True,
    )


def test_structured_mode_forwards_the_json_contract_verbatim() -> None:
    brain = CodexBrain(structured_prompts=True)
    prompt = brain._render_prompt(_wiki_request())
    assert "Return ONLY a single JSON array" in prompt
    assert "Source content here." in prompt
    assert "one to three short sentences" not in prompt


def test_voice_mode_keeps_the_conversational_flattening() -> None:
    brain = CodexBrain()
    prompt = brain._render_prompt(_wiki_request())
    assert "one to three short sentences" in prompt
    # The heavy system contract stays out of conversational CLI turns.
    assert "Return ONLY a single JSON array" not in prompt


class _StatusError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status


async def _collect(stream: AsyncIterator[BrainDelta]) -> str:
    chunks: list[str] = []
    async for delta in stream:
        if delta.content:
            chunks.append(delta.content)
    return "".join(chunks)


def _arm_api_and_oauth(
    monkeypatch: pytest.MonkeyPatch, brain: CodexBrain, *, status: int,
) -> list[str]:
    """API path raises ``status``; OAuth is connected; CLI yields 'cli-answer'."""
    monkeypatch.setattr(CodexBrain, "_api_key", lambda self: "sk-test")
    monkeypatch.setattr(CodexBrain, "_ensure_client", lambda self, key: object())
    monkeypatch.setattr(codex_module, "_codex_oauth_connected", lambda: True)

    async def _failing_stream(client: Any, model: str, req: BrainRequest):
        raise _StatusError(status)
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(codex_module, "stream_complete", _failing_stream)

    calls: list[str] = []

    async def _fake_cli(self: CodexBrain, req: BrainRequest):
        calls.append("cli")
        yield BrainDelta(content="cli-answer")
        yield BrainDelta(finish_reason="stop")

    monkeypatch.setattr(CodexBrain, "_complete_via_cli", _fake_cli)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 429])
async def test_throttled_api_key_crosses_over_to_the_subscription_cli(
    monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    brain = CodexBrain()
    calls = _arm_api_and_oauth(monkeypatch, brain, status=status)

    answer = await _collect(brain.complete(_wiki_request()))

    assert answer == "cli-answer"
    assert calls == ["cli"]


@pytest.mark.asyncio
async def test_non_account_errors_still_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brain = CodexBrain()
    calls = _arm_api_and_oauth(monkeypatch, brain, status=500)

    with pytest.raises(_StatusError):
        await _collect(brain.complete(_wiki_request()))
    assert calls == []  # a server error is not an account problem — no crossover


@pytest.mark.asyncio
async def test_no_oauth_means_the_account_error_is_surfaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brain = CodexBrain()
    calls = _arm_api_and_oauth(monkeypatch, brain, status=429)
    monkeypatch.setattr(codex_module, "_codex_oauth_connected", lambda: False)

    with pytest.raises(_StatusError):
        await _collect(brain.complete(_wiki_request()))
    assert calls == []
