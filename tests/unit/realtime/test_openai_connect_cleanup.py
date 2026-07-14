"""OpenAI Realtime cleans partially opened connection contexts."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
from jarvis.realtime.protocol import RealtimeSessionConfig


class _BlockingConnectContext:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.exit_args: tuple[object, object, object] | None = None

    async def __aenter__(self) -> object:
        self.entered.set()
        await self.release.wait()
        return object()

    async def __aexit__(self, *args: object) -> None:
        self.exit_args = args


class _ErrorConnectContext:
    def __init__(self) -> None:
        self.exit_args: tuple[object, object, object] | None = None

    async def __aenter__(self) -> object:
        raise RuntimeError("connect handshake failed")

    async def __aexit__(self, *args: object) -> None:
        self.exit_args = args


class _FakeOpenAIClient:
    def __init__(self, context: Any) -> None:
        self.context = context
        self.closed = False
        self.realtime = self

    def connect(self, *, model: str) -> Any:
        return self.context

    async def close(self) -> None:
        self.closed = True


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, context: Any
) -> dict[str, _FakeOpenAIClient]:
    holder: dict[str, _FakeOpenAIClient] = {}

    def make_client(*, api_key: str | None = None) -> _FakeOpenAIClient:
        client = _FakeOpenAIClient(context)
        holder["client"] = client
        return client

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", make_client)
    return holder


@pytest.mark.asyncio
async def test_cancel_during_connection_enter_closes_context_and_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _BlockingConnectContext()
    holder = _patch_client(monkeypatch, context)
    task = asyncio.create_task(
        OpenAIRealtimeProvider(api_key="test-key").open_session(
            RealtimeSessionConfig()
        )
    )
    await context.entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert context.exit_args is not None
    assert context.exit_args[0] is asyncio.CancelledError
    assert holder["client"].closed is True


@pytest.mark.asyncio
async def test_error_during_connection_enter_closes_context_and_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _ErrorConnectContext()
    holder = _patch_client(monkeypatch, context)

    with pytest.raises(RuntimeError, match="connect handshake failed"):
        await OpenAIRealtimeProvider(api_key="test-key").open_session(
            RealtimeSessionConfig()
        )

    assert context.exit_args is not None
    assert context.exit_args[0] is RuntimeError
    assert holder["client"].closed is True
