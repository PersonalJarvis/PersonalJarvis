"""OpenAI-compatible parameter retries for explicit API capability rejections.

Newer OpenAI models 400-reject the legacy ``max_tokens`` ("Use
'max_completion_tokens' instead"); OpenAI-compatible servers often only know
``max_tokens``. Some reasoning/tool models also accept only their default
temperature. The shared base adapts each field only after an explicit rejection
and never retries unrelated errors.
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

_OPENAI_TEMPERATURE_400 = (
    "Error code: 400 - {'error': {'message': \"Unsupported value: 'temperature' "
    "does not support 0 with this model. Only the default (1) value is "
    "supported.\", 'type': 'invalid_request_error', 'param': 'temperature', "
    "'code': 'unsupported_value'}}"
)

_OPENAI_STREAM_OPTIONS_400 = (
    "Error code: 400 - {'error': {'message': \"Unsupported parameter: "
    "'stream_options'.\", 'type': 'invalid_request_error', "
    "'param': 'stream_options', 'code': 'unsupported_parameter'}}"
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


class _SequenceClient:
    def __init__(self, errors: list[Exception]) -> None:
        self.calls: list[dict] = []
        self._errors = list(errors)
        chat = type("Chat", (), {})()
        chat.completions = type("Completions", (), {})()
        chat.completions.create = self._create
        self.chat = chat

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._errors:
            raise self._errors.pop(0)
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


async def test_default_only_temperature_rejection_retries_without_temperature() -> None:
    client = _FakeClient(RuntimeError(_OPENAI_TEMPERATURE_400))
    result = await _create_with_token_param_retry(
        client, {"model": "m", "max_tokens": 8, "temperature": 0.0}
    )

    assert result == "stream"
    assert len(client.calls) == 2
    assert client.calls[0]["temperature"] == 0.0
    assert "temperature" not in client.calls[1]
    assert client.calls[1]["max_tokens"] == 8


async def test_api_rejected_stream_options_retries_without_usage_option() -> None:
    client = _FakeClient(RuntimeError(_OPENAI_STREAM_OPTIONS_400))
    result = await _create_with_token_param_retry(
        client,
        {
            "model": "m",
            "max_tokens": 8,
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    )

    assert result == "stream"
    assert len(client.calls) == 2
    assert "stream_options" not in client.calls[1]


async def test_multiple_explicit_rejections_are_adapted_once_each() -> None:
    client = _SequenceClient(
        [RuntimeError(_OPENAI_400), RuntimeError(_OPENAI_TEMPERATURE_400)]
    )
    result = await _create_with_token_param_retry(
        client, {"model": "m", "max_tokens": 8, "temperature": 0.0}
    )

    assert result == "stream"
    assert len(client.calls) == 3
    assert client.calls[1]["max_completion_tokens"] == 8
    assert client.calls[1]["temperature"] == 0.0
    assert client.calls[2]["max_completion_tokens"] == 8
    assert "temperature" not in client.calls[2]


async def test_unrelated_errors_are_not_retried() -> None:
    client = _FakeClient(RuntimeError("Error code: 401 - invalid api key"))
    with pytest.raises(RuntimeError, match="401"):
        await _create_with_token_param_retry(client, {"model": "m", "max_tokens": 8})
    assert len(client.calls) == 1


async def test_temperature_validation_error_is_not_misread_as_unsupported() -> None:
    client = _FakeClient(RuntimeError("temperature must be between 0 and 2"))
    with pytest.raises(RuntimeError, match="between 0 and 2"):
        await _create_with_token_param_retry(
            client, {"model": "m", "temperature": 3.0}
        )
    assert len(client.calls) == 1


async def test_success_path_calls_once() -> None:
    client = _FakeClient(None)
    assert await _create_with_token_param_retry(client, {"max_tokens": 8}) == "stream"
    assert len(client.calls) == 1
