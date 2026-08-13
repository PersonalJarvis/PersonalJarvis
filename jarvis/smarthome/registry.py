"""One home out of many platforms: discovery, aggregation, and addressing.

Everything above this file — the REST routes, the section UI, the assistant tool
— talks to the registry and never to a provider. That is what makes "turn off
the lights" work across a hub and a bridge at the same time: the registry owns
the namespaced device id, so it knows which platform a device belongs to without
anyone above having to care.

Failure policy: **one broken platform must never blank the house.** Providers
are queried concurrently and a provider that raises contributes nothing to the
list while the others still show. A hub that is switched off is a common
Tuesday, not an outage of the section.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jarvis.smarthome.models import Command, CommandName, CommandResult, Device, Room
from jarvis.smarthome.provider import ConnectionState, ProviderStatus, SmartHomeProvider

log = logging.getLogger(__name__)


def _demo_enabled() -> bool:
    """Is the simulated house switched on in config?

    Read at call time rather than cached: the Connections tab flips it, and a
    toggle that needs a restart to take effect is a toggle people report as
    broken.
    """
    try:
        from jarvis.core.config import load_config

        return bool(load_config().smarthome.demo_mode)
    except Exception:  # noqa: BLE001 — config problems must not hide the section
        log.debug("smarthome: could not read demo_mode", exc_info=True)
        return False


def default_providers() -> list[SmartHomeProvider]:
    """The providers this installation should use, in display order.

    Home Assistant is always present — even unconfigured, because its card is
    how someone connects it. The demo home appears only when switched on.
    """
    from jarvis.smarthome.providers.home_assistant import HomeAssistantProvider

    providers: list[SmartHomeProvider] = [HomeAssistantProvider()]
    if _demo_enabled():
        from jarvis.smarthome.providers.demo import DemoProvider

        providers.append(DemoProvider())
    return providers


class SmartHomeRegistry:
    """The aggregated view of every connected platform."""

    def __init__(self, providers: list[SmartHomeProvider] | None = None) -> None:
        self._providers = providers if providers is not None else default_providers()

    @property
    def providers(self) -> list[SmartHomeProvider]:
        return list(self._providers)

    def provider(self, provider_id: str) -> SmartHomeProvider | None:
        return next((p for p in self._providers if p.id == provider_id), None)

    # -- reads --------------------------------------------------------------

    async def statuses(self) -> list[ProviderStatus]:
        results = await asyncio.gather(
            *(p.status() for p in self._providers), return_exceptions=True
        )
        out: list[ProviderStatus] = []
        for provider, result in zip(self._providers, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("smarthome: %s status failed: %s", provider.id, result)
                out.append(
                    ProviderStatus(
                        provider=provider.id,
                        display_name=provider.display_name,
                        state=ConnectionState.UNREACHABLE,
                        detail="This platform did not answer. Check its connection.",
                    )
                )
            else:
                out.append(result)
        return out

    async def devices(self) -> list[Device]:
        results = await asyncio.gather(
            *(p.devices() for p in self._providers), return_exceptions=True
        )
        devices: list[Device] = []
        for provider, result in zip(self._providers, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("smarthome: %s device list failed: %s", provider.id, result)
                continue
            devices.extend(result)
        devices.sort(key=lambda d: (d.room or "￿", d.name.casefold()))
        return devices

    async def rooms(self) -> list[Room]:
        results = await asyncio.gather(
            *(p.rooms() for p in self._providers), return_exceptions=True
        )
        rooms: list[Room] = []
        for result in results:
            if isinstance(result, BaseException):
                continue
            rooms.extend(result)
        rooms.sort(key=lambda r: r.name.casefold())
        return rooms

    async def device(self, device_id: str) -> Device | None:
        provider_id, _, native_id = device_id.partition(":")
        provider = self.provider(provider_id)
        if provider is None or not native_id:
            return None
        try:
            devices = await provider.devices()
        except Exception:  # noqa: BLE001
            log.debug("smarthome: lookup failed for %s", device_id, exc_info=True)
            return None
        return next((d for d in devices if d.native_id == native_id), None)

    # -- writes -------------------------------------------------------------

    async def execute(self, device_id: str, command: Command) -> CommandResult:
        """Send one command to whichever platform owns the device.

        Rejects a command the device cannot honour BEFORE it hits the network:
        a plain "this blind has no colour" is worth more than a vendor 500 the
        user has no way to read.
        """
        provider_id, _, native_id = device_id.partition(":")
        provider = self.provider(provider_id)
        if provider is None or not native_id:
            return CommandResult(
                ok=False,
                device_id=device_id,
                command=command.name,
                error=f"No connected platform called {provider_id!r}.",
            )
        device = await self.device(device_id)
        if device is not None and not device.supports(command.name):
            return CommandResult(
                ok=False,
                device_id=device_id,
                command=command.name,
                error=(
                    f"{device.name} cannot do that — it supports "
                    f"{', '.join(str(c) for c in device.capabilities) or 'nothing'}."
                ),
            )
        try:
            return await provider.execute(native_id, command)
        except Exception:  # noqa: BLE001 — a provider bug is not a 500 for the UI
            log.exception("smarthome: %s command failed", provider_id)
            return CommandResult(
                ok=False,
                device_id=device_id,
                command=command.name,
                error="That platform did not accept the command.",
            )


async def gather_home(
    registry: SmartHomeRegistry,
) -> tuple[list[ProviderStatus], list[Device], list[Room]]:
    """One fan-out to every platform, answering all three questions at once.

    Split out from :func:`overview` so a caller that needs the RAW objects —
    the room layer resolves live devices against the user's layout — can reuse
    the same single round trip instead of asking the house all over again.
    """
    return await asyncio.gather(  # type: ignore[return-value]
        registry.statuses(), registry.devices(), registry.rooms()
    )


def build_overview(
    statuses: list[ProviderStatus], devices: list[Device], rooms: list[Room]
) -> dict[str, Any]:
    """Shape an already-gathered house for the wire. Pure, no network."""
    by_provider: dict[str, int] = {}
    for device in devices:
        by_provider[device.provider] = by_provider.get(device.provider, 0) + 1
    return {
        "providers": [
            {
                **status.to_json(),
                "device_count": by_provider.get(status.provider, status.device_count),
            }
            for status in statuses
        ],
        "devices": [d.to_json() for d in devices],
        "rooms": [
            {
                "id": r.id,
                "name": r.name,
                "provider": r.provider,
                "device_count": r.device_count,
            }
            for r in rooms
        ],
        "connected": any(s.state is ConnectionState.CONNECTED for s in statuses),
        "commands": [str(c) for c in CommandName],
    }


async def overview(registry: SmartHomeRegistry) -> dict[str, Any]:
    """Everything the section's landing page needs, in one round trip.

    Deliberately one call rather than three: each of these already fans out to
    every platform, and three separate fetches would make a hub answer the same
    question three times over the home network.

    ``rooms`` here are the PLATFORMS' areas. The REST layer replaces them with
    the user's own rooms (:mod:`jarvis.smarthome.home_layout`) before the
    section sees them; this function stays the raw, layout-free view.
    """
    return build_overview(*await gather_home(registry))
