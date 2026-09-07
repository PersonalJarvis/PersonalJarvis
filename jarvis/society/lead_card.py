"""The lead's team card: what Jarvis knows about its society, every turn.

Jarvis is the harness itself and the one lead of the agent society. Every
society agent gets a briefing per turn (``surface.build_briefing``) that
names its teammates; until 2026-09-03 the lead got nothing. Asked "which
agents do you have?", Jarvis answered from the retired sub-agent (mission
worker) system its prompt still described and never from the roster — with
a freshly created Gmail agent standing on the island.

The card closes that gap. It is a deterministic, byte-stable block rendered
from the roster snapshot and the capability catalog — no model call, no
database read on the hot path — that:

* the brain appends to its system prompt (``BrainManager._build_system_prompt``),
  so the typed front-page chat and every delegated voice turn see it;
* the realtime session condenses into a per-turn directive
  (``RealtimeVoiceSession._society_directive``) so the live model routes a
  named agent to its action function instead of "I do not know who that is";
* the turn planner reads as agent names (``plan_turn(agent_names=...)``).

It changes exactly when the roster changes: ``Roster`` refreshes its
snapshot on every write, so an agent created in the Agents section is on the
very next turn's card without a restart, a hook or a model call.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Sequence
from typing import Final

from .capabilities import CapabilityRow
from .roster import LEAD_AGENT_ID, AgentRecord
from .runtime import current_runtime

log = logging.getLogger(__name__)

__all__ = [
    "CARD_TITLE",
    "lead_card_section",
    "render_lead_card",
    "roster_epoch",
    "society_agent_names",
]

CARD_TITLE: Final[str] = "## Your agent society (the team you lead)"

#: How much of an agent's standing instructions the card quotes.
_BRIEF_CHARS: Final[int] = 140
#: How many granted hands an allowlist agent shows before "…".
_HANDS_MAX: Final[int] = 6


def _rule_block(lead_name: str) -> str:
    return (
        f"You are {lead_name}, the lead of the user's agent society: the named agents "
        "listed below live in the Agents section, each with its own chat, its own tools "
        "and its own standing instructions. When the user says \"agent\", \"my agents\" "
        "or \"the team\", they mean THESE agents.\n"
        "- Send internal messages with message_agent (target, text). A message TO an email "
        "specialist is not email: never call gmail for this. Internal messages need no extra "
        "confirmation. Report the actual queued/delivered/failed receipt.\n"
        "- Hand work to an agent with delegate_to_agent (the agent's name and the task in "
        "full). The agent works in the background; you acknowledge now and its result "
        "comes back to this conversation.\n"
        "- Answer \"which agents do you have\", \"who is on the team\", \"what is X "
        "doing\", \"is X done\" from this card or with society_status — never from the "
        "retired sub-agent or mission-worker system.\n"
        "- A task that fits an agent's hands (mail to a mail agent, research to a "
        "research agent) goes to that agent when the user asks for the team or names it; "
        "when no agent fits, do it yourself. spawn_worker is only for heavy background "
        "work the user explicitly asked to run in the background that no agent covers.\n"
        "- Coding terminals in the Agentic IDE (Claude Code, Codex and the like) are NOT "
        "agents of this society; they are reached through the workspace tools."
    )


def roster_epoch(agents: Sequence[AgentRecord]) -> str:
    """A fingerprint of the team — changes when an agent is added, edited or archived."""
    digest = hashlib.sha256(
        "\n".join(f"{a.agent_id}:{a.updated_ms}:{a.state}" for a in agents).encode()
    ).hexdigest()
    return digest[:12]


def _state_word(agent: AgentRecord, running: int) -> str:
    state = str(agent.state)
    if state != "active":
        return state
    return f"busy ({running} run{'s' if running != 1 else ''})" if running > 0 else "idle"


def _hands(agent: AgentRecord, by_id: dict[str, CapabilityRow]) -> str:
    denied = set(agent.denies)
    focus = [by_id[c].label for c in agent.focus if c in by_id and c not in denied]
    if focus:
        return "hands: " + ", ".join(focus)
    if str(agent.grant_mode) == "allowlist":
        granted = [by_id[c].label for c in agent.grants if c in by_id and c not in denied]
        if not granted:
            return "hands: none granted yet"
        shown = ", ".join(granted[:_HANDS_MAX])
        return "hands: " + shown + (", …" if len(granted) > _HANDS_MAX else "")
    return "hands: every connected tool"


def _brief(description: str) -> str:
    text = " ".join(description.split())
    if not text:
        return ""
    if len(text) > _BRIEF_CHARS:
        text = text[: _BRIEF_CHARS - 1].rstrip() + "…"
    return f' · brief: "{text}"'


def render_lead_card(
    agents: Sequence[AgentRecord],
    catalog: Sequence[CapabilityRow],
    *,
    lead_name: str = "Jarvis",
    lead_id: str = LEAD_AGENT_ID,
    busy: Callable[[str], int] | None = None,
    terminals: Sequence[str] = (),
) -> str:
    """The lead's system-prompt block.

    Pure and deterministic: the same roster + catalog + terminals render the
    same bytes, so the provider prompt cache stays warm between roster
    changes. Archived agents are left out; paused ones are listed as paused
    so the lead does not promise work to an agent that will not take it.
    """
    team = sorted(
        (a for a in agents if a.agent_id != lead_id and str(a.state) != "archived"),
        key=lambda a: a.name.casefold(),
    )
    by_id = {row.id: row for row in catalog if row.connected}
    parts: list[str] = [CARD_TITLE, _rule_block(lead_name)]
    if team:
        lines = ["Agents (name — title · tier · state · hands):"]
        for agent in team:
            running = busy(agent.agent_id) if busy is not None else 0
            line = f"- {agent.name}"
            if agent.title.strip():
                line += f" — {agent.title.strip()}"
            line += f" · {agent.tier} · {_state_word(agent, running)} · {_hands(agent, by_id)}"
            line += _brief(agent.description)
            lines.append(line)
        parts.append("\n".join(lines))
    else:
        parts.append(
            "No other agents yet. The user creates them in the Agents section; when a "
            "task would suit a standing agent, offer to set one up there — never invent "
            "an agent that is not on this card."
        )
    names = [str(t).strip() for t in terminals if str(t or "").strip()]
    if names:
        parts.append(
            "Coding terminals open in the Agentic IDE right now (not society agents): "
            + ", ".join(names)
            + "."
        )
    parts.append(f"Roster epoch: {roster_epoch(team)}")
    return "\n\n".join(parts)


def _terminal_names() -> tuple[str, ...]:
    """Call-signs of the open Agentic-IDE terminals — in-memory, never fatal."""
    try:
        from jarvis.agentic_ide.session import running_call_signs

        return tuple(running_call_signs())
    except Exception:  # noqa: BLE001 — optional surface, never breaks a prompt build
        return ()


def society_agent_names() -> tuple[str, ...]:
    """Names of the live, non-lead agents — for the realtime directive and the planner.

    Synchronous and IO-free (the roster snapshot); ``()`` without a society.
    """
    rt = current_runtime()
    if rt is None:
        return ()
    try:
        agents = rt.roster.snapshot()
    except Exception:  # noqa: BLE001 — a broken snapshot costs the names, never the turn
        log.debug("society: roster snapshot unavailable", exc_info=True)
        return ()
    return tuple(
        sorted(
            (a.name for a in agents if a.agent_id != rt.lead_id and str(a.state) == "active"),
            key=str.casefold,
        )
    )


def lead_card_section(*, lead_name: str = "Jarvis") -> str:
    """The card for THIS process's society, or ``""`` when there is none.

    Read by ``BrainManager._build_system_prompt`` on every prompt build; it
    must therefore stay synchronous and cheap: a roster snapshot, the
    in-memory catalog, the scheduler's run counters.
    """
    rt = current_runtime()
    if rt is None:
        return ""
    try:
        agents = rt.roster.snapshot()
        catalog = rt.catalog()
        busy = rt.scheduler.active_runs
    except Exception:  # noqa: BLE001 — the card is a convenience; the turn must run without it
        log.warning("society: lead card unavailable", exc_info=True)
        return ""
    return render_lead_card(
        agents,
        catalog,
        lead_name=lead_name,
        lead_id=rt.lead_id,
        busy=busy,
        terminals=_terminal_names(),
    )
