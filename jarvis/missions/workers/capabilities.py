"""Explicit external-capability contract shared by every mission worker.

Mission workers all receive the same restricted inventory.  A backend either
consumes that inventory explicitly or reports ``unsupported``; no backend may
silently discover extra app or MCP tools from a machine-global CLI config.

Only the two app commands needed for the durable knowledge workflow are in the
initial allowlist.  Spawn, review, and skill-execution commands are deliberately
absent (AP-5/AP-14).
"""

from __future__ import annotations

import json
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

    The serialized MCP config is excluded from ``repr`` because it can contain
    resolved OAuth tokens.  Public reports expose server ids only.
    """

    _mcp_json: str = field(default="{}", repr=False)
    app_commands: tuple[str, ...] = ()

    @classmethod
    def build(
        cls,
        *,
        mcp_servers: dict[str, Any] | None = None,
        app_commands: tuple[str, ...] = (),
    ) -> WorkerCapabilityInventory:
        commands = tuple(dict.fromkeys(str(name) for name in app_commands if name))
        forbidden = _FORBIDDEN_RECURSIVE_NAMES.intersection(commands)
        if forbidden:
            raise ValueError(
                "recursive tools are forbidden in worker capability inventories: "
                + ", ".join(sorted(forbidden))
            )
        payload = json.dumps(mcp_servers or {}, ensure_ascii=False, sort_keys=True)
        return cls(_mcp_json=payload, app_commands=commands)

    @property
    def mcp_servers(self) -> dict[str, Any]:
        """Return a fresh config copy so one backend cannot mutate another's."""
        value = json.loads(self._mcp_json)
        return value if isinstance(value, dict) else {}

    @property
    def mcp_server_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.mcp_servers))

    def report_for(self, backend: str) -> dict[str, Any]:
        """Public capability report for a concrete worker backend.

        Claude CLI is currently the only backend that can consume the assembled
        Claude-format MCP config without putting resolved secrets on argv.
        App-command execution is intentionally unsupported until the worker
        broker can route it through ToolExecutor and the REST validation chain.
        """
        mcp_requested = bool(self.mcp_server_ids)
        app_requested = bool(self.app_commands)
        mcp_supported = backend == "claude-cli"
        return {
            "backend": backend,
            "mcp": {
                "status": (
                    "available"
                    if mcp_requested and mcp_supported
                    else "unsupported"
                    if mcp_requested
                    else "not_requested"
                ),
                "servers": list(self.mcp_server_ids),
            },
            "app_commands": {
                "status": "unsupported" if app_requested else "not_requested",
                "commands": list(self.app_commands),
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
