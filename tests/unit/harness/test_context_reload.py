"""Hot-reload of the Computer-Use context singleton (2026-05-30).

The context is a process-wide singleton built once at boot. A voice-tunable
write to ``computer_use.step_budget`` dispatches a ``ConfigReloaded`` event;
the subscription here must refresh the live singleton's scalar knobs IN PLACE
so the next mission picks up the new ceiling without an app restart.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import jarvis.harness.computer_use_context as ctx_mod
from jarvis.harness.computer_use_context import (
    ComputerUseContext,
    _refresh_context_from_config,
    set_computer_use_context,
    subscribe_context_reload,
)


def _make_ctx(step_budget: int = 100) -> ComputerUseContext:
    # vision_engine / brain_manager / tool_executor are only type-Any deps;
    # the reload path never touches them, so sentinels are fine.
    return ComputerUseContext(
        vision_engine=object(),
        brain_manager=object(),
        tool_executor=object(),
        step_budget=step_budget,
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test starts and ends with a clean singleton + bus subscription."""
    set_computer_use_context(None)
    ctx_mod._subscribed_bus_id = None
    yield
    set_computer_use_context(None)
    ctx_mod._subscribed_bus_id = None


def test_refresh_is_noop_without_context(monkeypatch):
    # No context wired yet -> must not raise even if load_config is broken.
    def _boom():
        raise RuntimeError("config blew up")

    monkeypatch.setattr("jarvis.core.config.load_config", _boom)
    _refresh_context_from_config()  # no exception == pass


def test_refresh_updates_scalar_knobs(monkeypatch):
    ctx = _make_ctx(step_budget=100)
    set_computer_use_context(ctx)

    fake_cfg = SimpleNamespace(
        computer_use=SimpleNamespace(
            step_budget=400,
            per_step_timeout_s=45.0,
            max_replans=5,
            verify_after_each_step=False,
        )
    )
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: fake_cfg)

    _refresh_context_from_config()

    # The SAME singleton object is mutated in place; deps are untouched.
    assert ctx.step_budget == 400
    assert ctx.per_step_timeout_s == 45.0
    assert ctx.max_replans == 5
    assert ctx.verify_after_each_step is False


def test_refresh_survives_config_failure(monkeypatch):
    ctx = _make_ctx(step_budget=100)
    set_computer_use_context(ctx)
    monkeypatch.setattr(
        "jarvis.core.config.load_config",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    _refresh_context_from_config()  # must not raise
    assert ctx.step_budget == 100  # unchanged


def test_subscribe_is_noop_without_bus():
    # No bus -> nothing to subscribe, no crash.
    subscribe_context_reload(None)
    assert ctx_mod._subscribed_bus_id is None


class _FakeBus:
    """Minimal EventBus stand-in: records subscribers, publish fans out."""

    def __init__(self) -> None:
        self.subscribers: list = []

    def subscribe_all(self, cb) -> None:
        self.subscribers.append(cb)

    async def publish(self, event) -> None:
        for cb in list(self.subscribers):
            await cb(event)


def test_subscribe_is_idempotent_per_bus():
    bus = _FakeBus()
    subscribe_context_reload(bus)
    subscribe_context_reload(bus)
    assert len(bus.subscribers) == 1  # subscribed at most once per bus


def test_config_reloaded_event_refreshes_live_context(monkeypatch):
    from jarvis.core.events import ConfigReloaded

    ctx = _make_ctx(step_budget=100)
    set_computer_use_context(ctx)

    bus = _FakeBus()
    subscribe_context_reload(bus)

    fake_cfg = SimpleNamespace(
        computer_use=SimpleNamespace(
            step_budget=250,
            per_step_timeout_s=30.0,
            max_replans=2,
            verify_after_each_step=True,
        )
    )
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: fake_cfg)

    # Simulate the Self-Mod writer firing ConfigReloaded after a voice-tunable
    # write to computer_use.step_budget.
    asyncio.run(bus.publish(ConfigReloaded(changed_keys=("computer_use.step_budget",))))

    assert ctx.step_budget == 250  # live mission would now see the new ceiling
