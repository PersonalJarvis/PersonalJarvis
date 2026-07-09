"""REST surface for the Command Registry (``jarvis/commands/registry.py``).

``GET /api/commands`` serves the one machine-readable catalog of user-facing
app commands to the desktop UI, the ``jarvis`` CLI (through the dynamic
OpenAPI layer), and any external agent. The registry itself is plain lazy
in-process data (AP-26: nothing here runs at boot), so this route is cheap.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/commands", tags=["commands"])


@router.get("")
async def list_commands() -> dict[str, Any]:
    """The full command catalog: id, endpoint, params schema, danger flag,
    UI section, and voice-alias examples per command."""
    from jarvis.commands.registry import registry_as_dicts

    commands = registry_as_dicts()
    return {"commands": commands, "count": len(commands)}


@router.get("/{command_id}")
async def get_command(command_id: str) -> dict[str, Any]:
    """One command by id (404 when unknown)."""
    from fastapi import HTTPException

    from jarvis.commands.registry import get_command as _lookup

    cmd = _lookup(command_id)
    if cmd is None:
        raise HTTPException(status_code=404, detail=f"Unknown command id: {command_id}")
    return cmd.as_dict()
