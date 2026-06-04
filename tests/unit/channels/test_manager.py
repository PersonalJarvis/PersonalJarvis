# === F-FRIENDS [F0] · feature/friends-section · alex-2026-04-30 ===
"""Unit-Tests fuer :class:`jarvis.channels.manager.ChannelManager`."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest

from jarvis.channels.base import ChannelMessage, ChannelSession
from jarvis.channels.manager import (
    ChannelContext,
    ChannelManager,
    ChannelStartError,
)
from jarvis.core.bus import EventBus
from jarvis.core.events import Event


class _BusOnlyChannel:
    name = "bus_only"

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_message(self, msg: ChannelMessage) -> None: ...
    async def broadcast_event(self, event: Event) -> None: ...

    async def messages(self) -> AsyncIterator[ChannelMessage]:  # pragma: no cover
        if False:
            yield  # type: ignore[unreachable]

    async def sessions(self) -> list[ChannelSession]:
        return []


class _ContextAwareChannel:
    name = "ctx_aware"

    def __init__(self, bus: EventBus, registry_marker: Any, config_marker: Any) -> None:
        self.bus = bus
        self.registry_marker = registry_marker
        self.config_marker = config_marker
        self.started = False
        self.stopped = False

    @classmethod
    def from_context(cls, ctx: ChannelContext) -> "_ContextAwareChannel":
        return cls(
            bus=ctx.bus,
            registry_marker=ctx.friend_registry,
            config_marker=ctx.config.get("token", "unset"),
        )

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_message(self, msg: ChannelMessage) -> None: ...
    async def broadcast_event(self, event: Event) -> None: ...

    async def messages(self) -> AsyncIterator[ChannelMessage]:  # pragma: no cover
        if False:
            yield  # type: ignore[unreachable]

    async def sessions(self) -> list[ChannelSession]:
        return []


class _BrokenStartChannel(_BusOnlyChannel):
    name = "broken_start"

    async def start(self) -> None:
        raise RuntimeError("Token fehlt")


@dataclass
class _FakeEntryPoint:
    name: str
    target: Any
    raise_on_load: Exception | None = None

    def load(self) -> Any:
        if self.raise_on_load is not None:
            raise self.raise_on_load
        return self.target


def _patch_entry_points(monkeypatch: pytest.MonkeyPatch, eps: list[_FakeEntryPoint]) -> None:
    def _fake_entry_points(*, group: str) -> list[_FakeEntryPoint]:
        if group != "jarvis.channel":
            return []
        return eps

    monkeypatch.setattr("jarvis.channels.manager.entry_points", _fake_entry_points)


def test_discovery_lazy_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    eps = [
        _FakeEntryPoint(name="bus_only", target=_BusOnlyChannel),
        _FakeEntryPoint(name="ctx_aware", target=_ContextAwareChannel),
    ]
    _patch_entry_points(monkeypatch, eps)

    mgr = ChannelManager(ChannelContext(bus=EventBus()))
    assert mgr.available() == ["bus_only", "ctx_aware"]
    assert mgr.failed() == {}


def test_discovery_failed_load_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    eps = [
        _FakeEntryPoint(name="bus_only", target=_BusOnlyChannel),
        _FakeEntryPoint(
            name="broken_load",
            target=None,
            raise_on_load=ImportError("fake import error"),
        ),
    ]
    _patch_entry_points(monkeypatch, eps)

    mgr = ChannelManager(ChannelContext(bus=EventBus()))
    assert mgr.available() == ["bus_only"]
    failed = mgr.failed()
    assert "broken_load" in failed
    assert "ImportError" in failed["broken_load"]


def test_get_unknown_channel_raises_keyerror(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [])
    mgr = ChannelManager(ChannelContext(bus=EventBus()))
    with pytest.raises(KeyError, match="not.+verfuegbar|nicht verfuegbar"):
        mgr.get("nonexistent")


def test_bus_only_fallback_constructor(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint(name="bus_only", target=_BusOnlyChannel)]
    )
    bus = EventBus()
    mgr = ChannelManager(ChannelContext(bus=bus))
    inst = mgr.get("bus_only")
    assert isinstance(inst, _BusOnlyChannel)
    assert inst.bus is bus


def test_from_context_hook_used_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint(name="ctx_aware", target=_ContextAwareChannel)]
    )
    bus = EventBus()
    sentinel_registry = object()
    ctx = ChannelContext(
        bus=bus, friend_registry=sentinel_registry, config={"token": "abc123"}  # type: ignore[arg-type]
    )
    mgr = ChannelManager(ctx)
    inst = mgr.get("ctx_aware")
    assert isinstance(inst, _ContextAwareChannel)
    assert inst.bus is bus
    assert inst.registry_marker is sentinel_registry
    assert inst.config_marker == "abc123"


def test_get_caches_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint(name="bus_only", target=_BusOnlyChannel)]
    )
    mgr = ChannelManager(ChannelContext(bus=EventBus()))
    a = mgr.get("bus_only")
    b = mgr.get("bus_only")
    assert a is b


@pytest.mark.asyncio
async def test_start_all_collects_errors_per_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_entry_points(
        monkeypatch,
        [
            _FakeEntryPoint(name="bus_only", target=_BusOnlyChannel),
            _FakeEntryPoint(name="broken_start", target=_BrokenStartChannel),
        ],
    )
    mgr = ChannelManager(ChannelContext(bus=EventBus()))
    errors = await mgr.start_all()
    assert "broken_start" in errors
    assert "Token fehlt" in errors["broken_start"]
    assert "bus_only" not in errors
    assert "bus_only" in mgr.started()
    assert "broken_start" not in mgr.started()


@pytest.mark.asyncio
async def test_stop_all_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint(name="bus_only", target=_BusOnlyChannel)]
    )
    mgr = ChannelManager(ChannelContext(bus=EventBus()))
    await mgr.start("bus_only")
    inst = mgr.get("bus_only")
    assert inst.started is True
    await mgr.stop_all()
    assert inst.stopped is True
    assert mgr.started() == []
    await mgr.stop_all()  # no-crash
    assert mgr.started() == []


@pytest.mark.asyncio
async def test_start_single_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint(name="broken_start", target=_BrokenStartChannel)]
    )
    mgr = ChannelManager(ChannelContext(bus=EventBus()))
    with pytest.raises(ChannelStartError, match="Token fehlt"):
        await mgr.start("broken_start")
    assert "broken_start" in mgr.start_errors()


@pytest.mark.asyncio
async def test_start_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(
        monkeypatch, [_FakeEntryPoint(name="bus_only", target=_BusOnlyChannel)]
    )
    mgr = ChannelManager(ChannelContext(bus=EventBus()))
    await mgr.start("bus_only")
    inst = mgr.get("bus_only")
    inst.started = False
    await mgr.start("bus_only")
    assert inst.started is False
