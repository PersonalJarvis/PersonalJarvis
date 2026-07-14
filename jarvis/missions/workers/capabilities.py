"""Explicit external-capability contract shared by every mission worker.

Mission workers all receive the same restricted inventory and consume it
through a mission-scoped supervisor broker. No backend may silently discover
extra app or MCP tools from a machine-global CLI config.

Only the two app commands needed for the durable knowledge workflow are in the
initial allowlist. Spawn, review, and skill-execution commands are deliberately
absent (AP-5/AP-14).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RESTRICTED_WORKER_APP_COMMANDS: tuple[str, ...] = (
    "session-latest-turn",
    "wiki-ingest",
)

_FORBIDDEN_RECURSIVE_NAMES = frozenset(
    {
        "dispatch-with-review",
        "dispatch_with_review",
        "multi-spawn",
        "multi_spawn",
        "run-skill",
        "run_skill",
        "spawn-worker",
        "spawn_worker",
    }
)


@dataclass(frozen=True)
class WorkerCapabilityInventory:
    """Immutable, secret-safe-to-report inventory for one mission step.

    Only MCP source identifiers are retained. Resolved commands, headers, env,
    OAuth tokens, and API keys never enter the inventory.
    """

    _mcp_server_ids: tuple[str, ...] = ()
    app_commands: tuple[str, ...] = ()
    native_tool_names: tuple[str, ...] = ()
    task_text: str = field(default="", repr=False)

    @classmethod
    def build(
        cls,
        *,
        mcp_servers: dict[str, Any] | None = None,
        mcp_server_ids: tuple[str, ...] = (),
        app_commands: tuple[str, ...] = (),
        native_tool_names: tuple[str, ...] = (),
        task_text: str = "",
    ) -> WorkerCapabilityInventory:
        commands = tuple(dict.fromkeys(str(name) for name in app_commands if name))
        forbidden = _FORBIDDEN_RECURSIVE_NAMES.intersection(commands)
        if forbidden:
            raise ValueError(
                "recursive tools are forbidden in worker capability inventories: "
                + ", ".join(sorted(forbidden))
            )
        server_ids = tuple(
            dict.fromkeys(
                str(name)
                for name in (*tuple((mcp_servers or {}).keys()), *mcp_server_ids)
                if name
            )
        )
        native = tuple(dict.fromkeys(str(name) for name in native_tool_names if name))
        forbidden_native = tuple(name for name in native if name in _FORBIDDEN_RECURSIVE_NAMES)
        if forbidden_native:
            raise ValueError(
                "recursive tools are forbidden in worker capability inventories: "
                + ", ".join(sorted(forbidden_native))
            )
        return cls(
            _mcp_server_ids=server_ids,
            app_commands=commands,
            native_tool_names=native,
            task_text=str(task_text or ""),
        )

    @property
    def mcp_servers(self) -> dict[str, Any]:
        """Compatibility view containing identifiers only, never configuration."""
        return {server_id: {} for server_id in self._mcp_server_ids}

    @property
    def mcp_server_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._mcp_server_ids))

    def bind_broker(
        self,
        *,
        ttl_s: float = 25 * 60.0,
        mission_id: str | None = None,
        worker_id: str | None = None,
    ):  # noqa: ANN201
        """Create the short-lived live grant for this mission, if reachable."""
        from .worker_tool_broker import issue_worker_tool_binding

        return issue_worker_tool_binding(
            task_text=self.task_text,
            mcp_server_ids=self.mcp_server_ids,
            app_commands=self.app_commands,
            native_tool_names=self.native_tool_names,
            ttl_s=ttl_s,
            mission_id=mission_id,
            worker_id=worker_id,
        )

    def report_for(self, backend: str, *, binding: Any | None = None) -> dict[str, Any]:
        """Public capability report for a concrete worker backend.

        Every supported backend consumes the same broker grant.  Source MCP
        configurations are discovery-only; reports never expose their env or
        resolved credentials.
        """
        mcp_requested = bool(self.mcp_server_ids)
        app_requested = bool(self.app_commands)
        available = bool(binding is not None and binding.available)
        tool_names = list(binding.tool_names) if available else []
        mcp_available = available and any(
            name.startswith(f"{server_id}/")
            for name in tool_names
            for server_id in self.mcp_server_ids
        )
        app_available = available and any(name in self.app_commands for name in tool_names)
        native_available = available and any(
            name in self.native_tool_names for name in tool_names
        )
        return {
            "backend": backend,
            "broker": {
                "status": "available" if available else "unavailable",
                "tools": tool_names,
            },
            "mcp": {
                "status": (
                    "available"
                    if mcp_requested and mcp_available
                    else "unavailable"
                    if mcp_requested
                    else "not_requested"
                ),
                "servers": list(self.mcp_server_ids),
            },
            "app_commands": {
                "status": (
                    "available"
                    if app_requested and app_available
                    else "unavailable"
                    if app_requested
                    else "not_requested"
                ),
                "commands": list(self.app_commands),
            },
            "native_tools": {
                "status": (
                    "available"
                    if self.native_tool_names and native_available
                    else "unavailable"
                    if self.native_tool_names
                    else "not_requested"
                ),
                "tools": list(self.native_tool_names),
            },
        }


def restricted_worker_app_commands() -> tuple[str, ...]:
    """Return only allowlisted commands that exist in the live registry."""
    try:
        from jarvis.commands.registry import get_command

        return tuple(
            command_id
            for command_id in RESTRICTED_WORKER_APP_COMMANDS
            if get_command(command_id) is not None
        )
    except Exception:  # noqa: BLE001 - registry drift must not break missions
        return ()


__all__ = [
    "RESTRICTED_WORKER_APP_COMMANDS",
    "WorkerCapabilityInventory",
    "restricted_worker_app_commands",
]
