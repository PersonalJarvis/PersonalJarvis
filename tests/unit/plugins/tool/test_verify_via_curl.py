"""Tests for VerifyViaCurlTool's error-path contract, timeout clamp, and
threading.

Three regressions pinned here:
  1. Every error-path ``ToolResult`` construction must pass ``output=`` — the
     dataclass has no default for it, so omitting it raised a ``TypeError``
     from inside ``execute()`` instead of returning an honest failed result.
  2. ``timeout_s`` is model-controlled and must be clamped to a sane range —
     an unbounded value would let a single call wedge the caller for
     arbitrarily long; a zero/negative value would hang forever.
  3. The synchronous ``httpx.get`` call must be offloaded via
     ``asyncio.to_thread`` so an unreachable host cannot block the event loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.plugins.tool.verify_via_curl import VerifyViaCurlTool


@dataclass
class _FakeResponse:
    status_code: int
    text: str = ""


@dataclass
class _CallRecorder:
    calls: list[tuple[Any, tuple, dict]] = field(default_factory=list)


@pytest.mark.asyncio
async def test_empty_url_returns_error_result_without_raising() -> None:
    tool = VerifyViaCurlTool()
    result = await tool.execute({"url": ""}, ctx=None)
    assert result.success is False
    assert result.output is None
    assert "url" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_request_failure_returns_error_result_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "get", _raise)

    tool = VerifyViaCurlTool()
    result = await tool.execute({"url": "https://example.invalid"}, ctx=None)

    assert result.success is False
    assert result.output is None
    assert "request failed" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_success_reports_http_200(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr(
        httpx, "get", lambda *_a, **_k: _FakeResponse(status_code=200, text="hi")
    )

    tool = VerifyViaCurlTool()
    result = await tool.execute({"url": "https://example.com"}, ctx=None)

    assert result.success is True
    assert "200" in (result.output or "")


@pytest.mark.asyncio
async def test_timeout_is_clamped_to_lower_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    recorder = _CallRecorder()

    def _capture(*args: Any, **kwargs: Any) -> Any:
        recorder.calls.append((None, args, kwargs))
        return _FakeResponse(status_code=200, text="hi")

    monkeypatch.setattr(httpx, "get", _capture)

    tool = VerifyViaCurlTool()
    # 0 is falsy and would trigger the "or 5.0" default before ever reaching
    # the clamp, so use a truthy below-floor value to exercise the min-bound.
    await tool.execute({"url": "https://example.com", "timeout_s": 0.1}, ctx=None)

    assert recorder.calls[0][2]["timeout"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_timeout_is_clamped_to_upper_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    recorder = _CallRecorder()

    def _capture(*args: Any, **kwargs: Any) -> Any:
        recorder.calls.append((None, args, kwargs))
        return _FakeResponse(status_code=200, text="hi")

    monkeypatch.setattr(httpx, "get", _capture)

    tool = VerifyViaCurlTool()
    await tool.execute(
        {"url": "https://example.com", "timeout_s": 999_999}, ctx=None
    )

    assert recorder.calls[0][2]["timeout"] == pytest.approx(30.0)


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
        "jarvis.plugins.tool.verify_via_curl.asyncio.to_thread",
        _recording_to_thread,
    )
    monkeypatch.setattr(
        httpx, "get", lambda *_a, **_k: _FakeResponse(status_code=200, text="hi")
    )

    tool = VerifyViaCurlTool()
    result = await tool.execute({"url": "https://example.com"}, ctx=None)

    assert result.success is True
    assert len(recorder.calls) == 1
    assert recorder.calls[0][0] is httpx.get
