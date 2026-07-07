"""@overlay_action / overlay_action_scope — Plan §8.4 + §8.5 Contract."""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from jarvis.overlay import (
    ActionKind,
    overlay_action,
    overlay_action_scope,
    overlay_action_scope_sync,
    overlay_action_sync,
    set_overlay,
)


@pytest.fixture()
def fake_bridge() -> mock.MagicMock:
    """Mock bridge with all emit_* methods. Injected into jarvis.overlay via
    set_overlay(), then reset to None afterward."""
    b = mock.MagicMock()
    b.emit_action_started = mock.MagicMock(return_value="action-id-001")
    b.emit_action_ended = mock.MagicMock(return_value=True)
    b.emit_error = mock.MagicMock(return_value=True)
    b.emit_click = mock.MagicMock(return_value=True)
    set_overlay(b)
    yield b
    set_overlay(None)


# -------------------------------------------------------------------------
# Sync-Decorator
# -------------------------------------------------------------------------


def test_sync_decorator_emits_started_then_function_then_ended(
    fake_bridge: mock.MagicMock,
) -> None:
    call_order: list[str] = []

    fake_bridge.emit_action_started.side_effect = lambda *a, **k: (
        call_order.append("started"),
        "id",
    )[1]
    fake_bridge.emit_action_ended.side_effect = lambda *a, **k: call_order.append(
        "ended"
    )

    @overlay_action_sync(ActionKind.CLICK)
    def doit() -> None:
        call_order.append("fn")

    doit()
    assert call_order == ["started", "fn", "ended"]


def test_sync_decorator_passes_kind_to_emit_started(
    fake_bridge: mock.MagicMock,
) -> None:
    @overlay_action_sync(ActionKind.TYPING, duration_hint_ms=500)
    def doit() -> None:
        pass

    doit()
    args, kwargs = fake_bridge.emit_action_started.call_args
    assert args[0] == "type"  # ActionKind.TYPING -> "type"
    assert kwargs.get("duration_hint_ms") == 500


def test_sync_decorator_emits_error_on_exception(
    fake_bridge: mock.MagicMock,
) -> None:
    @overlay_action_sync(ActionKind.CLICK)
    def doit() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        doit()
    fake_bridge.emit_error.assert_called_once()
    # action_ended must STILL be called (finally).
    fake_bridge.emit_action_ended.assert_called_once()
    args, kwargs = fake_bridge.emit_error.call_args
    assert "boom" in args[0]


def test_sync_decorator_no_bridge_is_no_op() -> None:
    """When set_overlay(None), the function runs without crashing."""
    set_overlay(None)
    called = []

    @overlay_action_sync(ActionKind.CLICK)
    def doit() -> None:
        called.append("fn")

    doit()
    assert called == ["fn"]


def test_get_bridge_degrades_to_none_when_optional_dep_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AP-23 W2-C: on a fresh clone without ``python-ulid`` installed, the
    overlay import chain (``jarvis.overlay`` -> ``.integration`` ->
    ``.bridge`` -> ``.schema`` -> ``ulid``) raises ``ModuleNotFoundError``.
    ``_get_bridge`` must degrade honestly to ``None`` (the same no-op path
    already exercised by ``test_sync_decorator_no_bridge_is_no_op``) instead
    of letting the import error escape into a keyboard/mouse tool action.
    """
    import jarvis.overlay as overlay_pkg
    from jarvis.overlay import triggers

    # Force re-resolution through the PEP-562 lazy __getattr__ (a prior test
    # in this session may already have cached the real ``get_overlay`` on
    # the package's __dict__).
    monkeypatch.delattr(overlay_pkg, "get_overlay", raising=False)

    def _boom(name: str) -> Any:
        if name == "get_overlay":
            raise ModuleNotFoundError("No module named 'ulid'", name="ulid")
        raise AttributeError(name)

    monkeypatch.setattr(overlay_pkg, "__getattr__", _boom)

    assert triggers._get_bridge() is None


def test_sync_decorator_runs_fn_when_overlay_dep_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same missing-dependency scenario, exercised through the decorated
    keyboard/mouse call path: the actual action must still run (matching
    the AP-23 fix intent — the decorated tool action degrades to a clean
    no-op for the overlay VISUAL, never a crash for the real action)."""
    import jarvis.overlay as overlay_pkg

    monkeypatch.delattr(overlay_pkg, "get_overlay", raising=False)

    def _boom(name: str) -> Any:
        if name == "get_overlay":
            raise ModuleNotFoundError("No module named 'ulid'", name="ulid")
        raise AttributeError(name)

    monkeypatch.setattr(overlay_pkg, "__getattr__", _boom)

    called = []

    @overlay_action_sync(ActionKind.CLICK)
    def doit() -> None:
        called.append("fn")

    doit()
    assert called == ["fn"]


# -------------------------------------------------------------------------
# Async-Decorator
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_decorator_emits_correct_sequence(
    fake_bridge: mock.MagicMock,
) -> None:
    call_order: list[str] = []
    fake_bridge.emit_action_started.side_effect = lambda *a, **k: (
        call_order.append("started"),
        "id-async",
    )[1]
    fake_bridge.emit_action_ended.side_effect = lambda *a, **k: call_order.append(
        "ended"
    )

    @overlay_action(ActionKind.CLICK)
    async def doit() -> None:
        call_order.append("fn")

    await doit()
    assert call_order == ["started", "fn", "ended"]


@pytest.mark.asyncio
async def test_async_decorator_emits_error_on_exception(
    fake_bridge: mock.MagicMock,
) -> None:
    @overlay_action(ActionKind.CLICK)
    async def doit() -> None:
        raise ValueError("async boom")

    with pytest.raises(ValueError, match="async boom"):
        await doit()
    fake_bridge.emit_error.assert_called_once()
    fake_bridge.emit_action_ended.assert_called_once()


# -------------------------------------------------------------------------
# Context-Manager
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_scope_yields_action_id(
    fake_bridge: mock.MagicMock,
) -> None:
    async with overlay_action_scope(ActionKind.TYPING) as aid:
        assert aid == "action-id-001"
    fake_bridge.emit_action_started.assert_called_once()
    fake_bridge.emit_action_ended.assert_called_once()


def test_sync_scope_yields_action_id(fake_bridge: mock.MagicMock) -> None:
    with overlay_action_scope_sync(ActionKind.SCROLL) as aid:
        assert aid == "action-id-001"
    fake_bridge.emit_action_started.assert_called_once()
    fake_bridge.emit_action_ended.assert_called_once()


@pytest.mark.asyncio
async def test_scope_emits_error_on_exception(fake_bridge: mock.MagicMock) -> None:
    with pytest.raises(KeyError):
        async with overlay_action_scope(ActionKind.HOTKEY):
            raise KeyError("scope boom")
    fake_bridge.emit_error.assert_called_once()
    fake_bridge.emit_action_ended.assert_called_once()


# -------------------------------------------------------------------------
# Click-Pre-Emit Order (mouse.py)
# -------------------------------------------------------------------------


def test_mouse_click_emits_click_event_before_pyautogui_call(
    fake_bridge: mock.MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan §14.3 + §8.8 — emit_click must fire BEFORE pyautogui.click."""
    import sys
    import types

    call_order: list[str] = []
    fake_bridge.emit_click.side_effect = lambda *a, **k: call_order.append(
        "emit_click"
    )

    fake_pyautogui = types.ModuleType("pyautogui")
    fake_pyautogui.click = mock.MagicMock(  # type: ignore[attr-defined]
        side_effect=lambda **kwargs: call_order.append("pyautogui.click")
    )
    fake_pyautogui.position = mock.MagicMock(  # type: ignore[attr-defined]
        return_value=(0, 0)
    )
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    from jarvis.control import mouse

    mouse.set_cursor_streamer(None)  # no streamer needed
    mouse.click(x=100, y=200)
    # Decorator emits started; mouse.click emits emit_click; then pyautogui.click.
    # emit_click must come BEFORE pyautogui.click.
    emit_idx = call_order.index("emit_click")
    pyautogui_idx = call_order.index("pyautogui.click")
    assert emit_idx < pyautogui_idx
