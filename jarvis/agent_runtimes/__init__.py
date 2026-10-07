"""External agent runtimes for society agents: Hermes and OpenClaw.

See ``docs/agent-runtimes.md``. Drivers are created lazily — importing this
package starts nothing and touches no binary (AP-26).
"""

from __future__ import annotations

from typing import Any, Final

#: Chat runner -> runtime name (``society.events.AgentRuntime`` values).
RUNNER_RUNTIMES: Final[dict[str, str]] = {
    "hermes-cli": "hermes",
    "openclaw-cli": "openclaw",
}

#: The runtimes an agent can be switched to, in display order.
RUNTIME_NAMES: Final[tuple[str, ...]] = ("hermes", "openclaw")

_DRIVERS: dict[str, Any] = {}


def driver(name: str) -> Any:
    """The driver singleton for ``name`` (``hermes`` / ``openclaw``)."""
    found = _DRIVERS.get(name)
    if found is not None:
        return found
    if name == "hermes":
        from jarvis.agent_runtimes.hermes import HermesRuntime

        found = HermesRuntime()
    elif name == "openclaw":
        from jarvis.agent_runtimes.openclaw import OpenClawRuntime

        found = OpenClawRuntime()
    else:
        raise KeyError(f"unknown agent runtime {name!r}")
    _DRIVERS[name] = found
    return found


def started_drivers() -> list[Any]:
    """Drivers that exist already (their background processes may be running)."""
    return list(_DRIVERS.values())


async def stop_all() -> None:
    """Stop every runtime's background processes (app shutdown)."""
    for found in started_drivers():
        await found.stop()


__all__ = ["RUNNER_RUNTIMES", "RUNTIME_NAMES", "driver", "started_drivers", "stop_all"]
