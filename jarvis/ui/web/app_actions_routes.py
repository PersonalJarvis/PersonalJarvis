"""Every app action, the person's mode for each, and what Jarvis recently ran.

The settings page that showed these was retired; the routes stay for the CLI.
The brain never reaches them — they are excluded from its action catalog, so
Jarvis cannot change its own permissions."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/app-actions", tags=["app-actions"])

# Plain ``def`` handlers on purpose: each reads or writes the policy/history
# files, and the first catalog read generates the app's whole OpenAPI schema.
# FastAPI runs them in the threadpool, off the shared event loop; the policy
# and history modules guard their files with a threading lock.


class ModeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    #: ``None`` returns the action to its default.
    mode: Literal["allow", "ask", "block"] | None = None


@router.get("")
def list_app_actions() -> dict[str, Any]:
    from jarvis.app_actions.catalog import live_catalog
    from jarvis.app_actions.policy import effective_tier, load_policy

    policy = load_policy()
    actions = [
        {**entry.summary(), "mode": policy.get(entry.id), "tier": effective_tier(entry, policy)}
        for entry in sorted(live_catalog().values(), key=lambda e: (e.area, e.title))
    ]
    return {
        "actions": actions,
        "areas": sorted({a["area"] for a in actions}),
        "count": len(actions),
    }


@router.put("/{action_id}/mode")
def set_app_action_mode(action_id: str, body: ModeBody) -> dict[str, Any]:
    from jarvis.app_actions.catalog import live_catalog
    from jarvis.app_actions.policy import effective_tier, set_mode

    entry = live_catalog().get(action_id)
    if entry is None:
        raise HTTPException(404, "Unknown action")
    policy = set_mode(action_id, body.mode)
    return {"id": action_id, "mode": policy.get(action_id), "tier": effective_tier(entry, policy)}


def _kind(method: str) -> str:
    """What a call does, in the page's words: reads, changes or deletes."""
    method = method.upper()
    if method == "GET":
        return "read"
    return "delete" if method == "DELETE" else "change"


def _describe(action_id: str, catalog: dict[str, Any]) -> dict[str, Any]:
    """A readable name, area and kind for one history row.

    Rows come from two callers: the generic app-action tool records the
    catalog's operation id, the curated voice commands record their own
    kebab-case id (``tasks-list``). A command is matched to the catalog entry
    behind the same endpoint, so its row links to the permission that governs
    it; without one it still gets the command's own title instead of its id.
    """
    entry = catalog.get(action_id)
    if entry is not None:
        return {
            "title": entry.title,
            "description": entry.description,
            "area": entry.area,
            "kind": _kind(entry.method),
            "catalog_id": entry.id,
        }
    from jarvis.commands.registry import get_command

    command = get_command(action_id)
    if command is None:
        return {"title": action_id, "description": "", "area": "", "kind": "", "catalog_id": None}
    match = next(
        (
            e
            for e in catalog.values()
            if e.method == command.method.upper() and e.path == command.path
        ),
        None,
    )
    return {
        "title": command.title,
        "description": command.description,
        "area": match.area if match else command.ui_section,
        "kind": _kind(command.method),
        "catalog_id": match.id if match else None,
    }


@router.get("/history")
def app_action_history(limit: int = 50) -> dict[str, Any]:
    from jarvis.app_actions import history
    from jarvis.app_actions.catalog import live_catalog

    catalog = live_catalog()
    described: dict[str, dict[str, Any]] = {}
    rows = []
    for row in history.recent(min(max(limit, 1), 200)):
        action_id = str(row.get("action"))
        if action_id not in described:
            described[action_id] = _describe(action_id, catalog)
        rows.append({**row, **described[action_id]})
    return {"history": rows}
