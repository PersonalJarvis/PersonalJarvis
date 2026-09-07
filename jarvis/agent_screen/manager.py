"""Allocates, reuses and reaps agent screens.

The manager is the only place that decides WHICH provider runs on this host
and HOW MANY screens may exist. Two rules shape it:

* **One screen per owner, refcounted.** An errand doing ten desktop steps must
  pay one sandbox boot, not ten. The screen dies when the last lease for that
  owner is released, never earlier.
* **Never silently fall back to the user's screen.** When no isolated provider
  is available the acquisition FAILS with the provider's own reason and
  remedy. Quietly handing back the physical desktop is the exact behaviour
  this subsystem exists to prevent, and it is the failure mode a future
  refactor is most likely to reintroduce.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any

from jarvis.agent_screen.protocol import (
    AgentScreenUnavailable,
    ProbeResult,
    ScreenLease,
    ScreenProvider,
    ScreenSession,
    ScreenSpec,
)
from jarvis.agent_screen.providers import (
    AttachedProvider,
    WindowsSandboxProvider,
    XvfbProvider,
)

logger = logging.getLogger(__name__)

#: Providers ``auto`` may pick, best first. ``attached`` is deliberately
#: absent: it drives the user's real screen and must always be chosen by name.
_AUTO_ORDER: tuple[str, ...] = ("windows-sandbox", "xvfb")


@dataclass(frozen=True, slots=True)
class ScreenSettings:
    """The knobs :mod:`jarvis.core.config` feeds in, with safe defaults."""

    enabled: bool = True
    provider: str = "auto"
    max_screens: int = 2
    width: int = 1280
    height: int = 800
    memory_mb: int = 4096
    networking: bool = True
    vgpu: bool = False

    @classmethod
    def from_config(cls, config: Any) -> ScreenSettings:
        block = getattr(config, "agent_screen", None)
        if block is None:
            return cls()
        return cls(
            enabled=bool(getattr(block, "enabled", True)),
            provider=str(getattr(block, "provider", "auto") or "auto"),
            max_screens=max(1, int(getattr(block, "max_screens", 2) or 2)),
            width=max(320, int(getattr(block, "width", 1280) or 1280)),
            height=max(240, int(getattr(block, "height", 800) or 800)),
            memory_mb=max(1024, int(getattr(block, "memory_mb", 4096) or 4096)),
            networking=bool(getattr(block, "networking", True)),
            vgpu=bool(getattr(block, "vgpu", False)),
        )


class AgentScreenManager:
    """Process-wide owner of every live agent screen."""

    def __init__(
        self,
        *,
        settings: ScreenSettings | None = None,
        bus: Any = None,
        providers: dict[str, ScreenProvider] | None = None,
    ) -> None:
        self._settings = settings or ScreenSettings()
        self._bus = bus
        self._providers: dict[str, ScreenProvider] = providers or self._build_providers()
        self._sessions: dict[str, ScreenSession] = {}   # owner -> session
        self._refcounts: dict[str, int] = {}            # owner -> live leases
        self._by_id: dict[str, ScreenSession] = {}
        # Guards the maps AND serialises boots: two concurrent acquisitions
        # for one owner must share a screen, not race two sandboxes up.
        self._lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    def _build_providers(self) -> dict[str, ScreenProvider]:
        return {
            "windows-sandbox": WindowsSandboxProvider(
                memory_mb=self._settings.memory_mb,
                networking=self._settings.networking,
                vgpu=self._settings.vgpu,
            ),
            "xvfb": XvfbProvider(),
            "attached": AttachedProvider(),
        }

    # -- capability --------------------------------------------------------

    def probe(self) -> list[ProbeResult]:
        """Probe every provider. Never raises; used by the REST/CLI surface."""
        results: list[ProbeResult] = []
        for name in ("windows-sandbox", "xvfb", "attached"):
            provider = self._providers.get(name)
            if provider is None:
                continue
            try:
                results.append(provider.probe())
            except Exception as exc:  # noqa: BLE001 — a broken probe is a "no"
                logger.debug("[agent-screen] probe of %s failed", name, exc_info=True)
                results.append(
                    ProbeResult(
                        kind=provider.kind,
                        available=False,
                        reason=f"probe failed: {exc}",
                    ),
                )
        return results

    def select_provider(self) -> tuple[ScreenProvider | None, str]:
        """``(provider, reason_when_none)`` for this host and configuration."""
        if not self._settings.enabled:
            return (None, "agent screens are disabled ([agent_screen].enabled = false)")
        configured = self._settings.provider.strip().lower()
        if configured not in ("", "auto"):
            provider = self._providers.get(configured)
            if provider is None:
                return (
                    None,
                    f"[agent_screen].provider = {configured!r} is not a known "
                    f"provider (known: {', '.join(sorted(self._providers))})",
                )
            result = provider.probe()
            if not result.available:
                return (None, f"{result.reason}. {result.remedy}".strip())
            return (provider, "")
        reasons: list[str] = []
        for name in _AUTO_ORDER:
            provider = self._providers.get(name)
            if provider is None:
                continue
            result = provider.probe()
            if result.available:
                return (provider, "")
            if result.reason:
                reasons.append(
                    f"{name}: {result.reason}"
                    + (f" — {result.remedy}" if result.remedy else ""),
                )
        return (
            None,
            "no isolated screen is available on this host. "
            + " | ".join(reasons),
        )

    def available(self) -> bool:
        provider, _reason = self.select_provider()
        return provider is not None

    def unavailable_reason(self) -> str:
        _provider, reason = self.select_provider()
        return reason

    # -- leasing -----------------------------------------------------------

    async def acquire(
        self,
        owner: str,
        *,
        purpose: str = "",
        require_isolated: bool = True,
    ) -> ScreenLease:
        """Lease a screen for ``owner``, booting one only when needed."""
        owner_key = str(owner or "").strip() or "anonymous"
        async with self._lock:
            existing = self._sessions.get(owner_key)
            if existing is not None:
                alive = await asyncio.to_thread(existing.alive)
                if alive:
                    self._refcounts[owner_key] = self._refcounts.get(owner_key, 0) + 1
                    return ScreenLease(session=existing, owner=owner_key)
                logger.warning(
                    "[agent-screen] %s died under owner %s — booting a fresh one",
                    existing.screen_id,
                    owner_key,
                )
                await self._drop_locked(owner_key, reason="died")

            provider, reason = self.select_provider()
            if provider is None:
                raise AgentScreenUnavailable(reason)

            cap = min(
                self._settings.max_screens,
                int(getattr(provider, "max_screens", self._settings.max_screens)),
            )
            if len(self._sessions) >= cap:
                raise AgentScreenUnavailable(
                    f"all {cap} agent screens are in use on this host "
                    f"(owners: {', '.join(sorted(self._sessions))}). Wait for one "
                    "to finish, or raise [agent_screen].max_screens.",
                )

            spec = ScreenSpec(
                owner=owner_key,
                purpose=purpose,
                width=self._settings.width,
                height=self._settings.height,
            )
            # Booting a session is seconds of blocking work (a sandbox boots a
            # whole Windows); it must never run on the event loop.
            session = await asyncio.to_thread(provider.start, spec)
            if require_isolated and not session.isolated:
                await asyncio.to_thread(session.close)
                raise AgentScreenUnavailable(
                    f"the {session.kind!r} screen is not isolated from the "
                    "user's session, so it must not be handed to a background "
                    "agent. Configure an isolated provider instead.",
                )
            self._sessions[owner_key] = session
            self._by_id[session.screen_id] = session
            self._refcounts[owner_key] = 1
            await self._publish_started(session)
            logger.info(
                "[agent-screen] %s (%s) leased to %s%s",
                session.screen_id,
                session.kind,
                owner_key,
                "" if session.hidden else " — VISIBLE on this host",
            )
            return ScreenLease(session=session, owner=owner_key)

    async def release(self, lease: ScreenLease) -> None:
        """Give a lease back; the screen dies with the last one."""
        if lease is None or lease._released:  # noqa: SLF001 — the flag is the lease's own
            return
        lease._released = True  # noqa: SLF001
        async with self._lock:
            remaining = self._refcounts.get(lease.owner, 0) - 1
            if remaining > 0:
                self._refcounts[lease.owner] = remaining
                return
            await self._drop_locked(lease.owner, reason="finished")

    async def _drop_locked(self, owner: str, *, reason: str) -> None:
        session = self._sessions.pop(owner, None)
        self._refcounts.pop(owner, None)
        if session is None:
            return
        self._by_id.pop(session.screen_id, None)
        await asyncio.to_thread(session.close)
        await self._publish_ended(session, reason)
        logger.info("[agent-screen] %s closed (%s)", session.screen_id, reason)

    async def shutdown(self) -> None:
        """Close every screen. Safe to call more than once."""
        async with self._lock:
            for owner in list(self._sessions):
                await self._drop_locked(owner, reason="shutdown")

    # -- inspection --------------------------------------------------------

    def get(self, screen_id: str) -> ScreenSession | None:
        with self._sync_lock:
            return self._by_id.get(str(screen_id))

    def list_screens(self) -> list[dict[str, Any]]:
        with self._sync_lock:
            sessions = list(self._by_id.values())
            refcounts = dict(self._refcounts)
        return [
            {
                "screen_id": session.screen_id,
                "kind": session.kind,
                "owner": session.owner,
                "purpose": session.purpose,
                "isolated": session.isolated,
                "hidden": session.hidden,
                "leases": refcounts.get(session.owner, 0),
            }
            for session in sessions
        ]

    # -- events ------------------------------------------------------------

    async def _publish_started(self, session: ScreenSession) -> None:
        if self._bus is None:
            return
        try:
            from jarvis.core.events import AgentScreenStarted  # noqa: PLC0415

            await self._bus.publish(
                AgentScreenStarted(
                    screen_id=session.screen_id,
                    kind=str(session.kind),
                    owner=session.owner,
                    purpose=session.purpose,
                    hidden=session.hidden,
                ),
            )
        except Exception:  # noqa: BLE001 — telemetry never breaks a mission
            logger.debug("AgentScreenStarted publish failed", exc_info=True)

    async def _publish_ended(self, session: ScreenSession, reason: str) -> None:
        if self._bus is None:
            return
        try:
            from jarvis.core.events import AgentScreenEnded  # noqa: PLC0415

            await self._bus.publish(
                AgentScreenEnded(screen_id=session.screen_id, reason=reason),
            )
        except Exception:  # noqa: BLE001
            logger.debug("AgentScreenEnded publish failed", exc_info=True)


# ---------------------------------------------------------------------------
# Process-wide handle
# ---------------------------------------------------------------------------

_MANAGER: AgentScreenManager | None = None
_MANAGER_LOCK = threading.Lock()


def set_agent_screen_manager(manager: AgentScreenManager | None) -> None:
    """Install (or clear) the process-wide manager. Called once at boot."""
    global _MANAGER
    with _MANAGER_LOCK:
        _MANAGER = manager


def peek_agent_screen_manager() -> AgentScreenManager | None:
    """Non-raising read — ``None`` before boot wired one."""
    return _MANAGER


def get_agent_screen_manager() -> AgentScreenManager:
    """The manager, building a config-backed default if boot did not wire one.

    The lazy default keeps the CLI and tests usable without a full app boot,
    and mirrors how the Computer-Use context degrades elsewhere in the tree.
    """
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            settings = ScreenSettings()
            try:
                from jarvis.core.config import load_config  # noqa: PLC0415

                settings = ScreenSettings.from_config(load_config())
            except Exception:  # noqa: BLE001 — defaults are a valid answer
                logger.debug("agent-screen config read failed", exc_info=True)
            _MANAGER = AgentScreenManager(settings=settings)
        return _MANAGER


__all__ = [
    "AgentScreenManager",
    "ScreenSettings",
    "get_agent_screen_manager",
    "peek_agent_screen_manager",
    "set_agent_screen_manager",
]
