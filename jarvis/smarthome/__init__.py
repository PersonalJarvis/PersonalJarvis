"""Smart Home — one house, however many platforms it is spread across.

Layout:

* :mod:`~jarvis.smarthome.models`      the unified device vocabulary
* :mod:`~jarvis.smarthome.provider`    the contract a platform implements
* :mod:`~jarvis.smarthome.providers`   one module per platform
* :mod:`~jarvis.smarthome.registry`    aggregation + addressing
* :mod:`~jarvis.smarthome.ecosystems`  the honest reachability map

Nothing here imports the UI or the router; the section, the REST routes and the
assistant tool all sit on top of :class:`~jarvis.smarthome.registry.SmartHomeRegistry`.
"""

from __future__ import annotations

from jarvis.smarthome.models import (
    Capability,
    Command,
    CommandName,
    CommandResult,
    Device,
    DeviceKind,
    Room,
)
from jarvis.smarthome.provider import ConnectionState, ProviderStatus, SmartHomeProvider
from jarvis.smarthome.registry import SmartHomeRegistry, overview

__all__ = [
    "Capability",
    "Command",
    "CommandName",
    "CommandResult",
    "ConnectionState",
    "Device",
    "DeviceKind",
    "ProviderStatus",
    "Room",
    "SmartHomeProvider",
    "SmartHomeRegistry",
    "overview",
]
