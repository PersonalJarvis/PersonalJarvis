"""Coverage for the reduced-context drag fallback in Computer-Use."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.cu.actuate import HumanInputTakeover
from jarvis.harness import screenshot_only_loop as loop


def test_macos_inline_drag_fallback_uses_protected_actuator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, int, int, float]] = []

    class _Actuator:
        def drag(
            self,
            x1: int,
            y1: int,
            x2: int,
            y2: int,
            *,
            duration_s: float,
        ) -> None:
            calls.append((x1, y1, x2, y2, duration_s))

    monkeypatch.setattr(loop.sys, "platform", "darwin")
    monkeypatch.setattr("jarvis.cu.actuate.get_actuator", lambda: _Actuator())

    loop._perform_drag(10, 20, 30, 40, 0.25)

    assert calls == [(10, 20, 30, 40, 0.25)]


@pytest.mark.asyncio
async def test_inline_drag_takeover_reaches_outer_pause_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _takeover(*_args, **_kwargs) -> None:
        raise HumanInputTakeover("user took the mouse")

    monkeypatch.setattr(loop, "_perform_drag", _takeover)
    ctx = SimpleNamespace(tools={}, tool_executor=object())

    with pytest.raises(HumanInputTakeover, match="user took the mouse"):
        await loop._execute_action(
            {
                "action": "drag",
                "x": 10,
                "y": 20,
                "x2": 30,
                "y2": 40,
                "duration_ms": 250,
            },
            ctx,
            trace_id=None,
            user_goal="drag safely",
            monitor_geom=(0, 0, 1000, 1000),
        )
