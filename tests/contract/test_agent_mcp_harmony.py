"""New ecosystem capabilities must be decided FOR or AGAINST the MCP surface.

The failure this prevents is drift, not a crash. Somebody adds
`POST /api/society/agents/{id}/vacation`, the app grows a button, and every MCP
client silently stays a version behind — nobody notices, because nothing breaks.
Six months later the surface is a partial mirror nobody trusts.

So the society's REST surface is enumerated here and every route must be
accounted for: either it has a tool, or it is listed as deliberately not
exposed WITH a reason. A new route fails this test until someone makes that
call. Answering "not exposed, because …" takes one line — the point is that the
decision is made, not that everything is exposed.
"""

from __future__ import annotations

from typing import Final

from jarvis.mcp.agents import TOOLS
from jarvis.ui.web.society_routes import router as society_router
from jarvis.ui.web.society_browser_routes import router as browser_router

#: REST route -> the MCP tool that covers it.
COVERED: Final[dict[str, str]] = {
    "GET /api/society/agents": "agents_list",
    "POST /api/society/agents": "agent_create",
    "GET /api/society/agents/{agent_id}": "agent_get",
    "POST /api/society/agents/{agent_id}/message": "agent_message",
    "POST /api/society/agents/{agent_id}/assign": "agent_assign",
    "GET /api/society/agents/{agent_id}/inbox": "agent_inbox",
    "GET /api/society/events": "board_events",
    "GET /api/society/capabilities": "capabilities_list",
    "GET /api/society/rooms": "rooms_list",
    "POST /api/society/rooms": "room_open",
    "POST /api/society/rooms/{room_id}/say": "room_say",
    "POST /api/society/rooms/{room_id}/settle": "room_settle",
    "GET /api/society/approvals": "approvals_list",
    "POST /api/society/approvals/{approval_id}/resolve": "approval_resolve",
    "GET /api/society/quests": "quests_list",
    "POST /api/society/quests": "quest_post",
    "GET /api/society/quests/{quest_id}": "quest_get",
    "POST /api/society/quests/{quest_id}/cancel": "quest_cancel",
    "POST /api/society/quests/{quest_id}/retry": "quest_retry",
    "GET /api/society/status": "ecosystem_status",
    "POST /api/society/kill-switch/release": "kill_switch",
    "POST /api/society/kill-switch": "kill_switch",
}

#: REST route -> why an MCP client does not get it. Each line is a decision.
WITHHELD: Final[dict[str, str]] = {
    "PATCH /api/society/agents/{agent_id}": (
        "editing a roster row (model, ceiling, focus) is configuration the owner "
        "does at their own keyboard, not something a remote client should change"
    ),
    "DELETE /api/society/agents/{agent_id}": (
        "archiving a teammate is destructive and irreversible from outside"
    ),
    "POST /api/society/agents/{agent_id}/kill": (
        "kill_switch stops the whole house; killing ONE agent's runs needs the "
        "context of watching them, which a remote client does not have"
    ),
    "POST /api/society/agents/{agent_id}/model": (
        "provider/model choice spends the owner's subscription — theirs to make"
    ),
    "GET /api/society/proposals": (
        "configuration proposals are decided on the card in the agent's chat by the "
        "person who owns the agent; a remote client has no such card"
    ),
    "POST /api/society/proposals/{proposal_id}/resolve": (
        "confirming a proposal changes an agent's rules, routines or skills — the "
        "owner's decision at their own keyboard, never a remote client's"
    ),
    "POST /api/society/onboarding/start": (
        "the one-time team offer is onboarding UI in the lead's chat, not an API verb"
    ),
    "GET /api/society/seeds": "starter-team proposals are onboarding UI, not an API verb",
    "POST /api/society/seeds/apply": (
        "onboarding, and it creates several agents at once — quest_post forges "
        "the one agent a job actually needs instead"
    ),
    "GET /api/society/providers": (
        "the provider/account picker is app UI; agent_get already reports the "
        "model an agent runs on"
    ),
    "GET /api/society/agents/{agent_id}/skills": (
        "agent_get already returns the skill list, so a second tool would only "
        "spend a client's tool budget"
    ),
    "POST /api/society/agents/{agent_id}/skills/{slug}/promote": (
        "promoting a learned skill into the global registry is a review step the "
        "owner does in the app, where they can read the skill first"
    ),
    "GET /api/society/browser/status": "browser install state is local setup, not ecosystem state",
    "POST /api/society/browser/install": "installs software on the machine — never from outside",
    "POST /api/society/browser/repair": "repairs host software; local owner setup",
    "POST /api/society/agents/{agent_id}/browser/cancel": "local browser control belongs to the owner UI",
    "POST /api/society/agents/{agent_id}/browser/session": (
        "prepares the host browser environment; owner UI, not an external agent command"
    ),
    "GET /api/society/agents/{agent_id}/browser": (
        "per-agent browser install state — local machine setup, like browser/status"
    ),
    "POST /api/society/agents/{agent_id}/browser/login": (
        "opens an interactive login window on the owner's screen; meaningless remotely"
    ),
    "POST /api/society/agents/{agent_id}/browser/login/done": (
        "the second half of the interactive login flow; useless without the window"
    ),
    "GET /api/society/agents/{agent_id}/routines": (
        "routines are scheduled spend; listing is harmless but pairs with creation, "
        "and neither belongs on a surface a client can drive unattended"
    ),
    "POST /api/society/agents/{agent_id}/routines": "creates recurring spend — owner-only",
    "POST /api/society/approvals": (
        "an MCP client asks for approval by USING a tool that needs one; it has no "
        "reason to park an action it invented"
    ),
    "POST /api/society/approvals/resurface": (
        "app-focus plumbing: it re-asks parked items when the window comes "
        "forward, which has no meaning for a remote client"
    ),
    "POST /api/society/agents/{agent_id}/chat": (
        "seats an agent's chat session without sending anything — a UI "
        "preparation step. agent_chat does it as part of talking"
    ),
    "GET /api/society/memory": (
        "the Memory House has its own surface; not part of this contract yet"
    ),
    "POST /api/society/memory/recall": (
        "recall reads an agent's private knowledge; the Memory House contract "
        "is not settled, and half a memory API is worse than none"
    ),
    "POST /api/society/memory/{knowledge_id}/promote": (
        "promoting private knowledge to shared is a privacy decision the owner "
        "makes after reading it — approvals_list surfaces it when an agent asks"
    ),
    "POST /api/society/memory/{knowledge_id}/dismiss": (
        "the other half of that review; it belongs with the reading, in the app"
    ),
}


def _routes() -> set[str]:
    """Every society REST route as ``METHOD /path``."""
    found: set[str] = set()
    for route in (*society_router.routes, *browser_router.routes):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path:
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            found.add(f"{method} {path}")
    return found


def test_every_society_route_is_decided() -> None:
    """A new route is neither exposed nor withheld until somebody says which."""
    routes = _routes()
    decided = set(COVERED) | set(WITHHELD)
    undecided = routes - decided
    assert not undecided, (
        "These society routes are new to the MCP surface and nobody has decided "
        f"about them yet: {sorted(undecided)}.\n"
        "Add a tool in jarvis/mcp/agents/tools.py and list it in COVERED, or list "
        "it in WITHHELD with one line saying why a remote client does not get it."
    )


def test_no_stale_decisions() -> None:
    """A route that disappeared must not leave a decision behind."""
    routes = _routes()
    stale = (set(COVERED) | set(WITHHELD)) - routes
    assert not stale, (
        f"These routes no longer exist but are still listed here: {sorted(stale)}. "
        "Drop them from COVERED / WITHHELD."
    )


def test_covered_routes_name_real_tools() -> None:
    published = {t.name for t in TOOLS}
    for route, tool in COVERED.items():
        assert tool in published, f"{route} claims tool {tool!r}, which does not exist"


def test_every_withheld_route_states_a_reason() -> None:
    for route, reason in WITHHELD.items():
        assert len(reason) > 25, f"{route} is withheld without a real reason"
