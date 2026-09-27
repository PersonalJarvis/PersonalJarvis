"""Copy a creating agent's seat and permission setup onto a new roster row.

When a society agent creates a teammate — typed chat or a subscription CLI
calling ``society-create-agent`` — the new row starts on the creator's model
seat (provider, model, effort, subscription account) and may not exceed the
creator's permission ceiling, grant mode, grants or denies. The Agents UI
sends those fields itself, so an explicit choice still wins, and a blank
create from the UI (no society session) keeps the usual defaults.
"""

from __future__ import annotations

from typing import Any

from .events import GrantMode, PermissionCeiling
from .roster import AgentRecord
from .surface import agent_id_of

__all__ = [
    "caller_session_id",
    "inherit_creator_fields",
    "session_agent_id",
]

_CEILING_RANK: dict[str, int] = {
    str(PermissionCeiling.SAFE): 0,
    str(PermissionCeiling.MONITOR): 1,
    str(PermissionCeiling.ASK): 2,
}

_SEAT_FIELDS: tuple[str, ...] = ("provider", "model", "effort", "account_id")


def session_agent_id(session_id: str | None) -> str | None:
    """The roster id behind a society chat session, else ``None``."""
    if not session_id:
        return None
    return agent_id_of(str(session_id).strip())


def caller_session_id() -> str | None:
    """The chat session of the in-flight tool call, if one is running.

    MCP seats stamp ``CHAT_SESSION_REF`` from ``X-Jarvis-Chat-Session``.
    Brain-runner seats stamp ``current_chat_turn``. Either is enough to
    know which agent is creating a teammate.
    """
    try:
        from jarvis.mcp.jarvis_tools_server import CHAT_SESSION_REF

        stamped = CHAT_SESSION_REF.get()
        if stamped:
            return str(stamped)
    except Exception:  # noqa: BLE001 — optional; a missing MCP server is not a create failure
        pass
    try:
        from jarvis.core.chat_turn import current_chat_turn

        turn = current_chat_turn.get()
        if turn is not None and turn.session_id:
            return str(turn.session_id)
    except Exception:  # noqa: BLE001 — optional; a missing turn is not a create failure
        pass
    return None


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _capped_ceiling(requested: str, creator: str) -> str:
    """Never raise a child above the creator's unattended ceiling."""
    want = _CEILING_RANK.get(requested, _CEILING_RANK[str(PermissionCeiling.MONITOR)])
    cap = _CEILING_RANK.get(creator, _CEILING_RANK[str(PermissionCeiling.MONITOR)])
    rank = min(want, cap)
    for name, value in _CEILING_RANK.items():
        if value == rank:
            return name
    return creator


def inherit_creator_fields(fields: dict[str, Any], creator: AgentRecord) -> dict[str, Any]:
    """Fill unset create fields from ``creator`` and cap any explicit raise.

    Mutates and returns ``fields``. Role-specific fields (name, title,
    description, focus, skills, budget, avatar) stay with the request.
    """
    if _blank(fields.get("parent_agent_id")):
        fields["parent_agent_id"] = creator.agent_id
    for key in _SEAT_FIELDS:
        if _blank(fields.get(key)):
            value = str(getattr(creator, key) or "")
            if value:
                fields[key] = value

    requested_ceiling = str(fields.get("permission_ceiling") or "").strip()
    creator_ceiling = str(creator.permission_ceiling)
    fields["permission_ceiling"] = (
        _capped_ceiling(requested_ceiling, creator_ceiling)
        if requested_ceiling
        else creator_ceiling
    )

    creator_mode = str(creator.grant_mode)
    requested_mode = str(fields.get("grant_mode") or "").strip()
    if creator_mode == str(GrantMode.ALLOWLIST):
        allowed = set(creator.grants)
        fields["grant_mode"] = str(GrantMode.ALLOWLIST)
        if "grants" in fields and isinstance(fields["grants"], list):
            fields["grants"] = [str(item) for item in fields["grants"] if str(item) in allowed]
        else:
            fields["grants"] = list(creator.grants)
    elif not requested_mode:
        fields["grant_mode"] = creator_mode
        if "grants" not in fields and creator.grants:
            fields["grants"] = list(creator.grants)

    creator_denies = [str(item) for item in creator.denies]
    if "denies" not in fields:
        if creator_denies:
            fields["denies"] = creator_denies
    else:
        existing = [str(item) for item in (fields.get("denies") or [])]
        merged: list[str] = []
        for item in (*creator_denies, *existing):
            if item and item not in merged:
                merged.append(item)
        fields["denies"] = merged
    return fields
