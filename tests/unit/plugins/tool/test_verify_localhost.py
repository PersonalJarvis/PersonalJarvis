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


def _http_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr(
        httpx, "get", lambda *_a, **_k: _FakeResponse(status_code=200, text="hi")
    )


@pytest.mark.asyncio
async def test_screenshot_degrades_honestly_and_never_asks_when_screen_recording_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The screenshot is an optional extra, not a gesture: it reads the state silently.

    A wallpaper-only macOS capture must never be reported as evidence, and the
    degradation carries the prohibitive permission text. The REAL permission
    service runs on ``FakeTCC``; its call log proves nothing was requested.
    """
    from tests.fakes.fake_tcc import FakeTCC, install_port

    _http_ok(monkeypatch)
    tcc = FakeTCC()
    install_port(monkeypatch, tcc.port("darwin"))

    tool = VerifyLocalhostTool()
    result = await tool.execute({"port": 5173, "take_screenshot": True}, ctx=None)

    assert result.success is True
    assert result.artifacts
    error = result.artifacts[0]["screenshot_error"]
    assert error.startswith("[permission_needed:screen_recording] ")
    assert "must not" in error
    assert "screenshot_path" not in result.artifacts[0]
    tcc.assert_no_prompts()


def _install_fake_mss(monkeypatch: pytest.MonkeyPatch, pixels, saved: list[str]) -> None:
    import sys
    import types

    size, rgb = pixels

    class _Raw:
        pass

    raw = _Raw()
    raw.size = size  # type: ignore[attr-defined]
    raw.rgb = rgb  # type: ignore[attr-defined]

    class _Sct:
        monitors = [{"left": 0, "top": 0, "width": size[0], "height": size[1]}] * 2

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def grab(self, _monitor):
            return raw

    def to_png(_rgb, _size, output):
        saved.append(output)

    fake_mss = types.ModuleType("mss")
    fake_mss.mss = _Sct  # type: ignore[attr-defined]
    fake_mss.tools = types.ModuleType("mss.tools")  # type: ignore[attr-defined]
    fake_mss.tools.to_png = to_png  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mss", fake_mss)
    monkeypatch.setitem(sys.modules, "mss.tools", fake_mss.tools)


@pytest.mark.asyncio
async def test_screenshot_is_taken_when_the_grant_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.platform import screen_access
    from tests.fakes.fake_screen_pixels import textured_pixels
    from tests.fakes.fake_tcc import FakeTCC, TccService, install_port

    _http_ok(monkeypatch)
    screen_access.reset_window_evidence_cache()
    tcc = FakeTCC(granted=[TccService.SCREEN_RECORDING])
    install_port(monkeypatch, tcc.port("darwin"))
    monkeypatch.setattr(screen_access, "_window_evidence", lambda: (3, 2))
    saved: list[str] = []
    _install_fake_mss(monkeypatch, textured_pixels((64, 36)), saved)

    result = await VerifyLocalhostTool().execute({"port": 5173, "take_screenshot": True}, ctx=None)

    assert result.artifacts == ({"screenshot_path": "monitor-1.png"},)
    assert saved == ["monitor-1.png"]
    tcc.assert_no_prompts()


@pytest.mark.asyncio
async def test_a_wallpaper_screenshot_is_never_evidence_even_when_the_state_says_granted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grab is pixel-checked: a photographic wallpaper is not a screenshot."""
    from jarvis.platform import screen_access
    from tests.fakes.fake_screen_pixels import photo_wallpaper_pixels
    from tests.fakes.fake_tcc import FakeTCC, TccService, install_port

    _http_ok(monkeypatch)
    screen_access.reset_window_evidence_cache()
    tcc = FakeTCC(granted=[TccService.SCREEN_RECORDING])
    install_port(monkeypatch, tcc.port("darwin"))
    # Other apps' windows are on screen and none has a readable title: the grant
    # is not usable although the preflight says granted.
    monkeypatch.setattr(screen_access, "_window_evidence", lambda: (2, 0))
    saved: list[str] = []
    _install_fake_mss(monkeypatch, photo_wallpaper_pixels((64, 36)), saved)

    result = await VerifyLocalhostTool().execute({"port": 5173, "take_screenshot": True}, ctx=None)

    assert result.artifacts
    assert result.artifacts[0]["screenshot_error"].startswith(
        "[permission_needed:screen_recording] "
    )
    assert saved == []
    tcc.assert_no_prompts()
