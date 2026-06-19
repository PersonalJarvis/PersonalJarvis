"""Unit tests for the scroll tool.

These patch the native send helper so they run without real mouse input on any
platform (Linux/CI, Mac, Windows). They assert the signed wheel-delta direction
contract, validation behaviour, and coordinate forwarding.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

import jarvis.plugins.tool.scroll as scroll_mod
from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.scroll import ScrollTool, _notch_for


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="test",
        config={},
        memory_read=None,
        approved_by="auto",
    )


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Patch both native paths to record the call instead of moving the mouse.

    The tool dispatches to ``_scroll_windows`` on Windows and ``_scroll_pyautogui``
    elsewhere; patching both keeps the test platform-agnostic.
    """
    calls: list[dict[str, object]] = []

    def _fake(direction: str, amount: int, x: int | None, y: int | None) -> int:
        notch = _notch_for(direction.lower(), amount)
        calls.append({"direction": direction, "amount": amount, "x": x, "y": y, "notch": notch})
        return notch

    monkeypatch.setattr(scroll_mod, "_scroll_windows", _fake)
    monkeypatch.setattr(scroll_mod, "_scroll_pyautogui", _fake)
    return calls


@pytest.mark.asyncio
async def test_scroll_down_produces_negative_wheel_delta(
    captured: list[dict[str, object]],
) -> None:
    result = await ScrollTool().execute({"direction": "down", "amount": 2}, _ctx())

    assert result.success is True
    assert captured[0]["notch"] == -240  # 2 * WHEEL_DELTA, negative for "down"


@pytest.mark.asyncio
async def test_scroll_up_produces_positive_wheel_delta(
    captured: list[dict[str, object]],
) -> None:
    result = await ScrollTool().execute({"direction": "up", "amount": 2}, _ctx())

    assert result.success is True
    assert captured[0]["notch"] == 240  # positive for "up"


@pytest.mark.asyncio
async def test_horizontal_direction_signs(captured: list[dict[str, object]]) -> None:
    await ScrollTool().execute({"direction": "right", "amount": 1}, _ctx())
    await ScrollTool().execute({"direction": "left", "amount": 1}, _ctx())

    assert captured[0]["notch"] == 120   # "right" positive
    assert captured[1]["notch"] == -120  # "left" negative


@pytest.mark.asyncio
async def test_default_amount_is_three_notches(
    captured: list[dict[str, object]],
) -> None:
    result = await ScrollTool().execute({"direction": "up"}, _ctx())

    assert result.success is True
    assert captured[0]["amount"] == 3
    assert captured[0]["notch"] == 360


@pytest.mark.asyncio
async def test_missing_direction_returns_failure(
    captured: list[dict[str, object]],
) -> None:
    result = await ScrollTool().execute({"amount": 3}, _ctx())

    assert result.success is False
    assert "direction" in (result.error or "")
    assert captured == []  # native path must not run on validation failure


@pytest.mark.asyncio
async def test_invalid_direction_returns_failure(
    captured: list[dict[str, object]],
) -> None:
    result = await ScrollTool().execute({"direction": "sideways"}, _ctx())

    assert result.success is False
    assert "direction" in (result.error or "")
    assert captured == []


@pytest.mark.asyncio
async def test_coordinates_are_forwarded(captured: list[dict[str, object]]) -> None:
    result = await ScrollTool().execute(
        {"direction": "down", "amount": 1, "x": 640, "y": 480}, _ctx()
    )

    assert result.success is True
    assert captured[0]["x"] == 640
    assert captured[0]["y"] == 480


@pytest.mark.asyncio
async def test_partial_coordinates_are_ignored(
    captured: list[dict[str, object]],
) -> None:
    """Only x without y (or vice versa) must not forward a coordinate."""
    result = await ScrollTool().execute({"direction": "up", "x": 100}, _ctx())

    assert result.success is True
    assert captured[0]["x"] is None
    assert captured[0]["y"] is None


def test_notch_helper_direction_contract() -> None:
    assert _notch_for("up", 1) == 120
    assert _notch_for("down", 1) == -120
    assert _notch_for("right", 3) == 360
    assert _notch_for("left", 3) == -360


def test_struct_size_is_40_on_windows() -> None:
    """Guard the cbSize bug class: INPUT must be 40 bytes on x64 Windows."""
    if os.name != "nt":
        pytest.skip("struct-size guard is Windows-specific")
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = wintypes.WPARAM

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        )

    class INPUT_UNION(ctypes.Union):
        _fields_ = (("mi", MOUSEINPUT),)

    class INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))

    assert ctypes.sizeof(INPUT) == 40
