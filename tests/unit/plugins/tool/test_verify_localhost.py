"""Tests for VerifyLocalhostTool's error-path contract and threading.

Two regressions pinned here:
  1. Every error-path ``ToolResult`` construction must pass ``output=`` — the
     dataclass has no default for it, so omitting it raised a ``TypeError``
     from inside ``execute()`` instead of returning an honest failed result.
  2. The synchronous ``httpx.get`` call must be offloaded via
     ``asyncio.to_thread`` so a slow/hung localhost server cannot block the
     event loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.plugins.tool.verify_localhost import VerifyLocalhostTool


@dataclass
class _FakeResponse:
    status_code: int
    text: str = ""


@dataclass
class _CallRecorder:
    calls: list[tuple[Any, tuple, dict]] = field(default_factory=list)


@pytest.mark.asyncio
async def test_zero_port_returns_error_result_without_raising() -> None:
    tool = VerifyLocalhostTool()
    result = await tool.execute({"port": 0}, ctx=None)
    assert result.success is False
    assert result.output is None
    assert "port" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_connection_failure_returns_error_result_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", _raise)

    tool = VerifyLocalhostTool()
    result = await tool.execute({"port": 5173}, ctx=None)

    assert result.success is False
    assert result.output is None
    assert "connection" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_success_reports_http_200(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr(
        httpx, "get", lambda *_a, **_k: _FakeResponse(status_code=200, text="hi")
    )

    tool = VerifyLocalhostTool()
    result = await tool.execute({"port": 5173}, ctx=None)

    assert result.success is True
    assert "200" in (result.output or "")


@pytest.mark.asyncio
async def test_http_call_is_offloaded_via_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The synchronous ``httpx.get`` must run through ``asyncio.to_thread``,
    not directly on the event loop."""
    import asyncio

    import httpx

    recorder = _CallRecorder()
    real_to_thread = asyncio.to_thread

    async def _recording_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        recorder.calls.append((func, args, kwargs))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(
        "jarvis.plugins.tool.verify_localhost.asyncio.to_thread",
        _recording_to_thread,
    )
    monkeypatch.setattr(
        httpx, "get", lambda *_a, **_k: _FakeResponse(status_code=200, text="hi")
    )

    tool = VerifyLocalhostTool()
    result = await tool.execute({"port": 5173}, ctx=None)

    assert result.success is True
    assert len(recorder.calls) == 1
    assert recorder.calls[0][0] is httpx.get
