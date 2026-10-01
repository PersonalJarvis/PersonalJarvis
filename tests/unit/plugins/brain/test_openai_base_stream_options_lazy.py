"""The OpenAI SDK capability probe runs only when a provider is used."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain import _openai_base


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        async def _empty_stream() -> Any:
            if False:  # pragma: no cover - marks this as an async generator
                yield None

        return _empty_stream()


async def _drain(client: _Client) -> None:
    req = BrainRequest(messages=(BrainMessage(role="user", content="hello"),))
    async for _ in _openai_base.stream_complete(client, "test-model", req):
        pass


def test_module_import_does_not_import_openai_sdk() -> None:
    code = (
        "import sys; "
        "import jarvis.plugins.brain._openai_base as module; "
        "assert 'openai' not in sys.modules; "
        "assert callable(module._stream_options_supported)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=NO_WINDOW_CREATIONFLAGS,
        check=False,
    )
    assert result.returncode == 0, result.stderr


async def test_provider_call_uses_concrete_client_capability() -> None:
    client = _Client()

    await _drain(client)
    await _drain(client)

    assert [call["stream_options"] for call in client.calls] == [
        {"include_usage": True},
        {"include_usage": True},
    ]


async def test_unsupported_sdk_omits_usage_option() -> None:
    client = _Client()

    async def _old_create(
        *,
        model: str,
        messages: list[Any],
        max_tokens: int,
        temperature: float,
        stream: bool,
    ) -> Any:
        client.calls.append(
            {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": stream,
            }
        )

        async def _empty_stream() -> Any:
            if False:  # pragma: no cover - marks this as an async generator
                yield None

        return _empty_stream()

    client.chat.completions.create = _old_create

    await _drain(client)

    assert "stream_options" not in client.calls[0]
