"""Starter agents (agent-definition §6, maintainer decision 2026-09-02).

Two things happen here:

* ``STARTER_TEAM`` — the first-run seed: one coordinator (``Scout``, an
  orchestrator that researches and delegates) and one specialist
  (``Archivist``, who curates the wiki). Created once per install, guarded by
  a meta flag, adoptable by name like any other agent, archivable by the
  user like any other agent. An empty island reads as broken; two teammates
  read as a team.
* ``propose_seeds`` — Grok's onboarding idea done local-first: teammates
  proposed from CONNECTED capabilities. A mail agent is proposed only when a
  mail plugin is connected, a repo agent only with ``gh``, and so on. Pure
  function over the catalog; the UI shows the proposals, the person picks.
"""

from __future__ import annotations

from typing import Any, Final

from .capabilities import CapabilityRow
from .events import Tier
from .focus import derive_approval_rules, derive_focus
from .roster import Roster

__all__ = [
    "ONBOARDING_KEY",
    "STARTER_TEAM",
    "create_from_proposals",
    "onboarding_done",
    "proposal_for_capability",
    "propose_seeds",
    "seed_first_run",
]

_SEEDED_KEY: Final[str] = "seeded_starter_team"
#: Set once the lead has offered a team in its chat — the offer is made ONCE
#: whether or not the person answers it (agent-definition §6).
ONBOARDING_KEY: Final[str] = "onboarding_done"


async def onboarding_done(store: Any) -> bool:
    return await store.get_meta(ONBOARDING_KEY, "0") == "1"


STARTER_TEAM: Final[tuple[dict[str, Any], ...]] = (
    {
        "name": "Scout",
        "title": "Research coordinator",
        "tier": Tier.ORCHESTRATOR,
        "description": (
            "You research on the web and in the wiki, break bigger requests into small "
            "tasks and hand them to the right teammate. You keep the user in the loop with "
            "short handoffs: what is done, where it is, what is open."
        ),
        "permission_ceiling": "monitor",
        "daily_budget_usd": 3.0,
    },
    {
        "name": "Archivist",
        "title": "Knowledge curator",
        "tier": Tier.SPECIALIST,
        "description": (
            "You keep the shared wiki tidy: you turn results from teammates into short, "
            "well-titled notes in your folder, link related pages and answer questions "
            "from what the wiki already knows. You never invent facts."
        ),
        "permission_ceiling": "safe",
        "daily_budget_usd": 1.0,
    },
)

#: capability id → a proposed teammate. Order = display order.
_PROPOSALS: Final[tuple[tuple[str, dict[str, Any]], ...]] = (
    (
        "plugin:gmail",
        {
            "name": "Mailbox",
            "title": "Mail assistant",
            "description": (
                "You read, sort and summarize my mail and draft replies. "
                "External mail is sent only after approval."
            ),
        },
    ),
    (
        "plugin:google-calendar",
        {
            "name": "Planner",
            "title": "Calendar assistant",
            "description": (
                "You keep my calendar: find free slots, prepare meetings, remind me of what "
                "is coming. Changes to events only after approval."
            ),
        },
    ),
    (
        "cli:gh",
        {
            "name": "Repo",
            "title": "Repository assistant",
            "description": (
                "You watch my GitHub repositories: summarize pull requests and issues, "
                "draft replies and changelogs. Merging or closing only after approval."
            ),
        },
    ),
    (
        "plugin:spotify",
        {
            "name": "DJ",
            "title": "Music assistant",
            "description": "You manage my Spotify playlists and pick music for the moment.",
        },
    ),
    (
        "plugin:home-assistant",
        {
            "name": "Home",
            "title": "Smart-home assistant",
            "description": (
                "You control my smart home through Home Assistant: lights, climate, scenes. "
                "Locks and alarms only after approval."
            ),
        },
    ),
)


def propose_seeds(
    catalog: list[CapabilityRow], existing_names: set[str] | None = None
) -> list[dict[str, Any]]:
    """Teammates worth creating on THIS box: one per connected capability."""
    connected = {row.id for row in catalog if row.connected}
    taken = {n.lower() for n in (existing_names or set())}
    out: list[dict[str, Any]] = []
    for cap_id, proposal in _PROPOSALS:
        if cap_id not in connected or proposal["name"].lower() in taken:
            continue
        focus = derive_focus(proposal["title"], proposal["description"], catalog)
        if cap_id not in focus:
            focus = [cap_id, *focus]
        out.append(
            {
                **proposal,
                "tier": str(Tier.SPECIALIST),
                "focus": focus,
                "approval_rules": derive_approval_rules(proposal["description"], focus),
                "reason": cap_id,
            }
        )
    return out


def proposal_for_capability(cap_id: str, catalog: list[CapabilityRow]) -> dict[str, Any] | None:
    """The teammate proposed for ONE connected capability, ready for ``Roster.create``
    (the quest board forges it when nobody on the roster fits a quest)."""
    if not any(row.id == cap_id and row.connected for row in catalog):
        return None
    for proposal_cap, proposal in _PROPOSALS:
        if proposal_cap != cap_id:
            continue
        focus = derive_focus(proposal["title"], proposal["description"], catalog)
        if cap_id not in focus:
            focus = [cap_id, *focus]
        return {
            **proposal,
            "tier": str(Tier.SPECIALIST),
            "focus": focus,
            "approval_rules": derive_approval_rules(proposal["description"], focus),
        }
    return None


async def create_from_proposals(
    roster: Roster, catalog: list[CapabilityRow], names: list[str] | None = None
) -> list[str]:
    """Create the picked seed proposals (all when ``names`` is empty). Returns
    the ids created. Shared by the seeds route and the onboarding proposal, so
    the two paths cannot drift."""
    taken = {a.name for a in await roster.list(include_archived=True)}
    wanted = {n.strip().lower() for n in (names or []) if n.strip()}
    created: list[str] = []
    for proposal in propose_seeds(catalog, taken):
        if wanted and proposal["name"].lower() not in wanted:
            continue
        fields = {k: v for k, v in proposal.items() if k in ("focus", "approval_rules")}
        agent, was_created = await roster.create(
            name=proposal["name"],
            title=proposal["title"],
            description=proposal["description"],
            tier=proposal["tier"],
            **fields,
        )
        if was_created:
            created.append(agent.agent_id)
    return created


async def seed_first_run(roster: Roster, store: Any) -> list[str]:
    """Create the starter team once. Returns the ids created (empty later)."""
    if await store.get_meta(_SEEDED_KEY, "0") == "1":
        return []
    created: list[str] = []
    for spec in STARTER_TEAM:
        skip = ("name", "title", "description", "tier")
        fields = {k: v for k, v in spec.items() if k not in skip}
        record, was_created = await roster.create(
            name=spec["name"],
            title=spec["title"],
            description=spec["description"],
            tier=spec["tier"],
            **fields,
        )
        if was_created:
            created.append(record.agent_id)
    await store.set_meta(_SEEDED_KEY, "1")
    return created
