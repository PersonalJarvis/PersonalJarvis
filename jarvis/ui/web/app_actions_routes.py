"""Settings > Jarvis actions: every app action, the person's mode for each, and
what Jarvis recently ran. The brain never reaches these routes — they are
excluded from its action catalog, so Jarvis cannot change its own permissions."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/app-actions", tags=["app-actions"])


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


@router.get("/history")
def app_action_history(limit: int = 50) -> dict[str, Any]:
    from jarvis.app_actions import history
    from jarvis.app_actions.catalog import live_catalog

    catalog = live_catalog()
    rows = []
    for row in history.recent(min(max(limit, 1), 200)):
        entry = catalog.get(str(row.get("action")))
        rows.append({**row, "title": entry.title if entry else row.get("action")})
    return {"history": rows}
