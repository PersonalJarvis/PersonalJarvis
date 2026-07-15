"""DragTool — press-and-hold drag routed through the ToolExecutor (audit #13).

The drag gesture used to run inline in the CU loop, bypassing the risk-tier /
blacklist / audit path. It is now a real tool. These cover its arg validation,
the success readback, and graceful failure when the desktop backend is absent —
without driving the real mouse (``_perform_drag`` is monkeypatched).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.plugins.tool import drag as drag_mod
from jarvis.plugins.tool.drag import DragTool


@pytest.fixture
def _no_real_mouse(monkeypatch):
    calls: list[tuple] = []

    def _fake(x1, y1, x2, y2, duration_s):
        calls.append((x1, y1, x2, y2, duration_s))

    monkeypatch.setattr(drag_mod, "_perform_drag", _fake)
    return calls


async def test_drag_success_calls_backend_with_pixels(_no_real_mouse):
    res = await DragTool().execute(
        {"x1": 100, "y1": 200, "x2": 300, "y2": 400, "duration_ms": 500},
        SimpleNamespace(),
    )
    assert res.success is True
    assert "(100,200)->(300,400)" in res.output
    assert _no_real_mouse == [(100, 200, 300, 400, 0.5)]


async def test_drag_defaults_duration_when_omitted(_no_real_mouse):
    await DragTool().execute(
        {"x1": 0, "y1": 0, "x2": 10, "y2": 10}, SimpleNamespace()
    )
    # default 400 ms -> 0.4 s
    assert _no_real_mouse[0][4] == pytest.approx(0.4)


async def test_drag_rejects_non_integer_coords(_no_real_mouse):
    res = await DragTool().execute(
        {"x1": "nope", "y1": 0, "x2": 10, "y2": 10}, SimpleNamespace()
    )
    assert res.success is False
    assert "integer" in (res.error or "")
    assert _no_real_mouse == []  # never touched the mouse on bad input


async def test_drag_missing_coord_fails_cleanly(_no_real_mouse):
    res = await DragTool().execute({"x1": 0, "y1": 0, "x2": 10}, SimpleNamespace())
    assert res.success is False
    assert _no_real_mouse == []


async def test_drag_reports_backend_error(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("display gone")

    monkeypatch.setattr(drag_mod, "_perform_drag", _boom)
    res = await DragTool().execute(
        {"x1": 0, "y1": 0, "x2": 10, "y2": 10}, SimpleNamespace()
    )
    assert res.success is False
    assert "failed" in (res.error or "")


async def test_drag_refuses_engine_capture_from_different_window(
    monkeypatch,
    _no_real_mouse,
):
    monkeypatch.setattr(
        "jarvis.plugins.tool.click._window_signature_matches",
        lambda _expected: False,
    )

    res = await DragTool().execute(
        {
            "x1": 0,
            "y1": 0,
            "x2": 10,
            "y2": 10,
            "_expected_window_signature": ("handle", 11, (0, 0, 800, 600)),
        },
        SimpleNamespace(),
    )

    assert res.success is False
    assert "changed after the screenshot" in (res.error or "")
    assert _no_real_mouse == []


async def test_drag_reports_missing_pyautogui(monkeypatch):
    def _no_pyautogui(*_a, **_k):
        raise ImportError("No module named 'pyautogui'")

    monkeypatch.setattr(drag_mod, "_perform_drag", _no_pyautogui)
    res = await DragTool().execute(
        {"x1": 0, "y1": 0, "x2": 10, "y2": 10}, SimpleNamespace()
    )
    assert res.success is False
    assert "pyautogui" in (res.error or "")


def test_drag_tool_identity():
    t = DragTool()
    assert t.name == "drag"
    assert t.risk_tier == "monitor"
    assert {"x1", "y1", "x2", "y2"} <= set(t.schema["required"])

async def test_drag_reports_actuation_unavailable_verbatim(monkeypatch):
    """Wayland/headless: the actionable capability-probe message must reach
    the model verbatim (no generic 'failed' wrapper around it)."""
    from jarvis.cu.actuate.base import ActuationUnavailable

    def _unavailable(*_a, **_k):
        raise ActuationUnavailable(
            "Cannot control mouse/keyboard on a Wayland session: Wayland "
            "blocks synthetic input for security."
        )

    monkeypatch.setattr(drag_mod, "_perform_drag", _unavailable)
    res = await DragTool().execute(
        {"x1": 0, "y1": 0, "x2": 10, "y2": 10}, SimpleNamespace()
    )
    assert res.success is False
    assert (res.error or "").startswith("Cannot control mouse/keyboard")


def test_perform_drag_posix_routes_through_capability_probe(monkeypatch):
    """Drag must use the capability-probed actuator and verify both endpoints."""
    drags: list[tuple] = []

    class _FakeActuator:
        current = (0, 0)

        def move(self, x, y):
            self.current = (x, y)

        def cursor_pos(self):
            return self.current

        def drag_from_cursor(self, x1, y1, x2, y2, *, duration_s=0.4):
            drags.append((x1, y1, x2, y2, duration_s))
            self.current = (x2, y2)

    monkeypatch.setattr(
        "jarvis.cu.actuate.base.get_actuator", lambda: _FakeActuator()
    )
    drag_mod._perform_drag(1, 2, 3, 4, 0.25)
    assert drags == [(1, 2, 3, 4, 0.25)]
