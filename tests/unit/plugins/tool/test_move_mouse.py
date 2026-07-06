"""MoveMouseTool non-Windows degradation (§3 honest messages).

The POSIX path must resolve the input backend via the capability probe
(``get_actuator``) so Wayland/headless/missing-deps hosts fail with the
actionable ``ActuationUnavailable`` message instead of a raw pyautogui error.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.cu.actuate.base import ActuationUnavailable
from jarvis.plugins.tool import move_mouse as mm


@pytest.mark.asyncio
async def test_posix_headless_reports_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mm.os, "name", "posix")

    def _unavailable() -> None:
        raise ActuationUnavailable(
            "Cannot control mouse/keyboard: no display is present on this "
            "host (headless). Computer-Use needs a desktop session."
        )

    monkeypatch.setattr("jarvis.cu.actuate.base.get_actuator", _unavailable)

    res = await mm.MoveMouseTool().execute({"x": 10, "y": 20}, SimpleNamespace())

    assert res.success is False
    assert "headless" in (res.error or "")
    assert "desktop session" in (res.error or "")


@pytest.mark.asyncio
async def test_posix_success_moves_via_probed_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mm.os, "name", "posix")
    moves: list[tuple[int, int]] = []

    class _FakeActuator:
        name = "fake-posix"

        def move(self, x: int, y: int) -> None:
            moves.append((x, y))

    monkeypatch.setattr(
        "jarvis.cu.actuate.base.get_actuator", lambda: _FakeActuator()
    )

    res = await mm.MoveMouseTool().execute({"x": 5, "y": 7}, SimpleNamespace())

    assert res.success is True
    assert moves == [(5, 7)]
    assert "fake-posix" in (res.output or "")
