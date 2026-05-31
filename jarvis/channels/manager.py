# === F-FRIENDS [F0] · feature/friends-section · alex-2026-04-30 ===
"""ChannelManager: discovery + lifecycle for all registered channels.

Analogous to :class:`jarvis.harness.manager.HarnessManager`, but for channels.
Channels require more dependencies (EventBus + optional FriendRegistry +
platform-specific config), so the bus and dependencies are bundled in
a :class:`ChannelContext` and passed to plugins.

Discovery:
    Lazy-load via ``importlib.metadata.entry_points(group="jarvis.channel")``.
    Failed loads end up in ``self._failed_load`` and do not block the others.

Instantiation:
    Channels may optionally implement ``classmethod from_context(ctx)``
    to pull arbitrary dependencies from the :class:`ChannelContext`.
    If the method is absent, ``cls(ctx.bus)`` is used as a fallback
    — this keeps the existing ``WebChannel`` compatible.

Lifecycle:
    ``start_all()`` starts all channels but collects errors individually.
    ``stop_all()`` is symmetric and idempotent.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from jarvis.core.bus import EventBus
from jarvis.core.protocols import ChannelAdapter

if TYPE_CHECKING:  # pragma: no cover
    from jarvis.friends.registry import FriendRegistry

log = logging.getLogger(__name__)

PLUGIN_GROUP = "jarvis.channel"


@dataclass(frozen=True)
class ChannelContext:
    """Dependencies that channels can receive upon instantiation."""

    bus: EventBus
    friend_registry: "FriendRegistry | None" = None
    config: dict[str, Any] = field(default_factory=dict)


class ChannelManagerError(RuntimeError):
    """Base class for ChannelManager-specific errors."""


class ChannelStartError(ChannelManagerError):
    """Channel could not be started (token missing, connection refused)."""


class ChannelManager:
    """Discovery + lifecycle for all channels (Web, Telegram, ...)."""

    def __init__(self, context: ChannelContext) -> None:
        self._ctx = context
        self._classes: dict[str, type] = {}
        self._failed_load: dict[str, str] = {}
        self._instances: dict[str, ChannelAdapter] = {}
        self._start_errors: dict[str, str] = {}
        self._loaded = False
        self._started: set[str] = set()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _load_classes(self) -> None:
        if self._loaded:
            return
        for ep in entry_points(group=PLUGIN_GROUP):
            try:
                cls = ep.load()
                self._classes[ep.name] = cls
            except Exception as exc:  # noqa: BLE001
                self._failed_load[ep.name] = f"{type(exc).__name__}: {exc}"
                log.warning("Channel '%s' load failed: %s", ep.name, exc)
        self._loaded = True

    def available(self) -> list[str]:
        self._load_classes()
        return sorted(self._classes.keys())

    def failed(self) -> dict[str, str]:
        self._load_classes()
        return dict(self._failed_load)

    def start_errors(self) -> dict[str, str]:
        return dict(self._start_errors)

    def started(self) -> list[str]:
        return sorted(self._started)

    # ------------------------------------------------------------------
    # Instantiation
    # ------------------------------------------------------------------

    def get(self, name: str) -> ChannelAdapter:
        self._load_classes()
        if name in self._instances:
            return self._instances[name]
        if name not in self._classes:
            raise KeyError(
                f"Channel '{name}' nicht verfuegbar. "
                f"Bekannt: {self.available()}. Failed: {list(self._failed_load)}."
            )
        cls = self._classes[name]
        instance = self._instantiate(cls)
        self._instances[name] = instance
        return instance

    def _instantiate(self, cls: type) -> ChannelAdapter:
        from_context = getattr(cls, "from_context", None)
        if callable(from_context):
            return from_context(self._ctx)
        return cls(self._ctx.bus)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, name: str) -> None:
        if name in self._started:
            return
        try:
            channel = self.get(name)
            await channel.start()
            self._started.add(name)
            log.info("Channel '%s' gestartet", name)
        except Exception as exc:  # noqa: BLE001
            self._start_errors[name] = f"{type(exc).__name__}: {exc}"
            log.error("Channel '%s' start failed: %s", name, exc)
            raise ChannelStartError(f"Channel '{name}' konnte nicht starten: {exc}") from exc

    async def start_all(self) -> dict[str, str]:
        names = self.available()
        if not names:
            return {}
        results = await asyncio.gather(*(self._start_safe(n) for n in names))
        return {n: err for n, err in zip(names, results, strict=True) if err is not None}

    async def _start_safe(self, name: str) -> str | None:
        try:
            await self.start(name)
            return None
        except ChannelStartError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001
            return f"{type(exc).__name__}: {exc}"

    async def stop(self, name: str) -> None:
        if name not in self._started:
            return
        instance = self._instances.get(name)
        if instance is None:
            self._started.discard(name)
            return
        try:
            await instance.stop()
        except Exception as exc:  # noqa: BLE001
            log.warning("Channel '%s' stop raised: %s", name, exc)
        finally:
            self._started.discard(name)
            log.info("Channel '%s' gestoppt", name)

    async def stop_all(self) -> None:
        names = list(self._started)
        if not names:
            return
        await asyncio.gather(
            *(self.stop(n) for n in names), return_exceptions=True
        )
