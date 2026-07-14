"""OpenAI token-parameter retry (field bug: valid key read as "Not working").

Newer OpenAI models 400-reject the legacy ``max_tokens`` ("Use
'max_completion_tokens' instead"); OpenAI-compatible servers often only know
``max_tokens``. The shared base must send the legacy name first and switch
exactly once on the server's explicit rejection — never on unrelated errors.
"""
from __future__ import annotations

import pytest

from jarvis.plugins.brain._openai_base import _create_with_token_param_retry

_OPENAI_400 = (
    "Error code: 400 - {'error': {'message': \"Unsupported parameter: "
    "'max_tokens' is not supported with this model. Use "
    "'max_completion_tokens' instead.\", 'type': 'invalid_request_error', "
    "'param': 'max_tokens', 'code': 'unsupported_parameter'}}"
)


class _FakeClient:
    def __init__(self, first_error: Exception | None) -> None:
        self.calls: list[dict] = []
        self._first_error = first_error
        chat = type("Chat", (), {})()
        chat.completions = type("Completions", (), {})()
        chat.completions.create = self._create
        self.chat = chat

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._first_error is not None and len(self.calls) == 1:
            raise self._first_error
        return "stream"


async def test_max_tokens_rejection_retries_with_max_completion_tokens() -> None:
    client = _FakeClient(RuntimeError(_OPENAI_400))
    result = await _create_with_token_param_retry(
        client, {"model": "m", "max_tokens": 8}
    )
    assert result == "stream"
    assert len(client.calls) == 2
    assert client.calls[0].get("max_tokens") == 8  # legacy first (compat servers)
    assert "max_tokens" not in client.calls[1]
    assert client.calls[1].get("max_completion_tokens") == 8


async def test_unrelated_errors_are_not_retried() -> None:
    client = _FakeClient(RuntimeError("Error code: 401 - invalid api key"))
    with pytest.raises(RuntimeError, match="401"):
        await _create_with_token_param_retry(client, {"model": "m", "max_tokens": 8})
    assert len(client.calls) == 1


async def test_success_path_calls_once() -> None:
    client = _FakeClient(None)
    assert await _create_with_token_param_retry(client, {"max_tokens": 8}) == "stream"
    assert len(client.calls) == 1
