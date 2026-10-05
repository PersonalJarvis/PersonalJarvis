"""Lazy frontier auto-refresh contract for BrainManager."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.brain import frontier_autoswitch, frontier_resolver
from jarvis.brain.manager import BrainManager


def _manager(*, enabled: bool, override: object | None = None) -> BrainManager:
    manager = BrainManager.__new__(BrainManager)
    manager._config = SimpleNamespace(
        brain=SimpleNamespace(frontier_auto_apply=enabled),
    )
    manager._bus = object()
    manager._frontier_auto_apply_lock = asyncio.Lock()
    manager._frontier_auto_apply_done = False
    return manager


@pytest.mark.asyncio
async def test_frontier_auto_refresh_is_disabled_without_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(enabled=False)
    calls = 0

    async def apply(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(frontier_autoswitch, "apply_frontier_resolution", apply)

    await manager._maybe_apply_frontier_auto_switch()

    assert calls == 0
    assert manager._frontier_auto_apply_done is False


@pytest.mark.asyncio
async def test_frontier_auto_refresh_runs_once_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(enabled=True)
    calls = 0

    async def apply(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return []

    class Resolver:
        def __init__(self) -> None:
            pass

    monkeypatch.setattr(frontier_autoswitch, "apply_frontier_resolution", apply)
    monkeypatch.setattr(frontier_resolver, "FrontierResolver", Resolver)

    await asyncio.gather(
        manager._maybe_apply_frontier_auto_switch(),
        manager._maybe_apply_frontier_auto_switch(),
        manager._maybe_apply_frontier_auto_switch(),
    )

    assert calls == 1
    assert manager._frontier_auto_apply_done is True


@pytest.mark.asyncio
async def test_frontier_auto_refresh_does_not_run_under_turn_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(enabled=True)
    calls = 0

    async def apply(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr(frontier_autoswitch, "apply_frontier_resolution", apply)
    from jarvis.brain.manager import _TURN_OVERRIDE

    token = _TURN_OVERRIDE.set(SimpleNamespace(provider="openai", model="gpt-5.5"))
    try:
        await manager._maybe_apply_frontier_auto_switch()
    finally:
        _TURN_OVERRIDE.reset(token)

    assert calls == 0
    assert manager._frontier_auto_apply_done is False
