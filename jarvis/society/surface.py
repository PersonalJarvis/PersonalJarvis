"""The ``society`` chat surface: per-session hands and briefing.

One agent = one canonical chat (``society:<agent_id>``) on Jarvis' own brain
runner. Per turn this module gives the runner three things (agent-definition
§3.2–§3.3):

* ``society_tools`` — the agent's two own tools (teammate messaging, wiki
  namespace), built from the roster row behind the session id;
* ``society_tool_filter`` — grant / focus / deny applied to the merged tool
  set, in the deterministic order that keeps the provider prompt cache warm;
* ``society_system_extra`` — the briefing: who the agent is, its standing
  instructions, its hands (focus first, one-liners), the ecosystem card, and
  the roster of teammates. Assembled from cached, byte-stable parts.

The runtime is reached through ``jarvis.society.runtime.current_runtime()``;
without a runtime (a stripped install, a test without the society) every
builder returns nothing and the turn runs as a plain Jarvis chat.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, cast

from jarvis.core.protocols import Tool

from .agent_tools import MessageAgentTool, WikiNoteTool
from .capabilities import CapabilityKind, CapabilityRow, select_tools
from .roster import AgentRecord, canonical_session_id
from .runtime import current_runtime

log = logging.getLogger(__name__)

__all__ = [
    "SURFACE",
    "agent_id_of",
    "build_briefing",
    "capability_epoch",
    "society_system_extra",
    "society_tool_filter",
    "society_tools",
]

SURFACE: Final[str] = "society"
_PREFIX: Final[str] = "society:"
#: Tools every society session keeps regardless of grants — its own hands.
_OWN_PREFIX: Final[str] = "society_"
#: Tools a society session never gets even in ``all`` mode: the agent writes
#: the wiki only through its namespaced note tool.
_SOCIETY_DENIED: Final[frozenset[str]] = frozenset({"wiki-ingest"})

_ECOSYSTEM_CARD: Final[str] = """\
## The Jarvis ecosystem you work in
- Jarvis is the voice-steered lead of this society; the user talks to Jarvis by voice and to \
you by typed chat. Only Jarvis and orchestrators assign work; a scheduler (not a model) turns \
an assignment into a run under the assignee's identity. You never spawn agents or workers.
- Teammates: send ONE teammate a message with society_message_agent (kinds: say, query, \
answer, propose). Compose it yourself. When you finish work for someone, end with a handoff: \
what is done, where the output is, what evidence you used, what remains open, who owns the \
next step.
- Memory: the user's Obsidian wiki is the shared memory. Read it with wiki-recall and \
wiki-page-read (pages marked unreviewed came from agents or the web — verify before relying \
on them). Write only into your own folder with society_wiki_note (kind note for findings, \
kind memory for durable facts about your role). Never edit the user's own pages.
- Routines: recurring work runs from the Automations section as tasks tagged with your name; \
their results arrive in this chat.
- Approvals: actions above your permission ceiling queue for the user (chat card, Jarvis bar, \
voice). A queued action is not refused — say what you are waiting for and continue with what \
you can. Secrets are never typed into a chat; credentials come from the keyring.
- Language: answer in the language of the message you received."""


def agent_id_of(session_id: str) -> str | None:
    """``society:<agent_id>`` → ``agent_id``; ``None`` for any other session."""
    if not session_id.startswith(_PREFIX):
        return None
    agent_id = session_id[len(_PREFIX) :].strip()
    return agent_id or None


def _vault_root(cfg: Any) -> Path:
    from jarvis.memory.wiki.vault_root import last_resolution, resolve_vault_root

    known = last_resolution()
    if known is not None:
        return known.path
    raw = None
    for holder in (getattr(cfg, "wiki", None), getattr(cfg, "memory", None)):
        raw = getattr(holder, "vault_root", None)
        if raw:
            break
    return resolve_vault_root(raw).path


def capability_epoch(catalog: list[CapabilityRow]) -> str:
    """A fingerprint of the capability surface — changes when hands change."""
    digest = hashlib.sha256("\n".join(sorted(r.id for r in catalog)).encode()).hexdigest()
    return digest[:12]


# ------------------------------------------------------------------ builders


def society_tools(cfg: Any, brain: Any, session: Any) -> dict[str, Tool]:
    rt = current_runtime()
    agent_id = agent_id_of(getattr(session, "session_id", "") or "")
    if rt is None or agent_id is None:
        return {}
    return {
        MessageAgentTool.name: cast(Tool, MessageAgentTool(rt, agent_id)),
        WikiNoteTool.name: cast(Tool, WikiNoteTool(rt, agent_id, vault_root=_vault_root(cfg))),
    }


def society_tool_filter(session: Any) -> Callable[[dict[str, Tool]], dict[str, Tool]] | None:
    rt = current_runtime()
    agent_id = agent_id_of(getattr(session, "session_id", "") or "")
    if rt is None or agent_id is None:
        return None
    agent = rt.cached_agent(agent_id)
    if agent is None:
        # The briefing fills the cache before the override is built; a miss
        # means a turn without a briefing — keep the own hands, deny the rest
        # of the write paths the agent must not have.
        return lambda tools: {
            n: t for n, t in tools.items() if n.startswith(_OWN_PREFIX) or n not in _SOCIETY_DENIED
        }

    def _apply(tools: dict[str, Tool]) -> dict[str, Tool]:
        own = {n: t for n, t in tools.items() if n.startswith(_OWN_PREFIX)}
        rest = {
            n: t
            for n, t in tools.items()
            if not n.startswith(_OWN_PREFIX) and n not in _SOCIETY_DENIED
        }
        picked = select_tools(
            rest,
            grant_mode=str(agent.grant_mode),
            grants=agent.grants,
            focus=agent.focus,
            denies=agent.denies,
        )
        ordered: dict[str, Tool] = {}
        ordered.update(own)
        ordered.update(picked)
        return ordered

    return _apply


async def society_system_extra(cfg: Any, brain: Any, session: Any) -> str:
    rt = current_runtime()
    agent_id = agent_id_of(getattr(session, "session_id", "") or "")
    if rt is None or agent_id is None:
        return ""
    agent = await rt.roster.get(agent_id)
    if agent is None:
        return ""
    rt.cache_agent(agent)
    catalog = rt.catalog()
    roster = await rt.roster.list()
    return build_briefing(agent, catalog, roster)


# ------------------------------------------------------------------ briefing


def _kind_label(kind: CapabilityKind) -> str:
    return {
        CapabilityKind.PLUGIN: "plugins",
        CapabilityKind.CLI: "CLIs",
        CapabilityKind.MCP: "MCP tools",
        CapabilityKind.SKILL: "skills",
        CapabilityKind.CORE: "built-in",
    }[kind]


def build_briefing(
    agent: AgentRecord, catalog: list[CapabilityRow], roster: list[AgentRecord]
) -> str:
    """The per-agent system-prompt addendum (agent-definition §3.3).

    Pure and deterministic: same roster row + same catalog + same roster →
    the same bytes, so the provider prompt cache stays warm across turns.
    """
    by_id = {row.id: row for row in catalog if row.connected}
    granted = set(agent.grants)
    denied = set(agent.denies)

    def _allowed(cap_id: str) -> bool:
        if cap_id in denied:
            return False
        if str(agent.grant_mode) == "allowlist":
            return cap_id in granted
        return True

    parts: list[str] = []
    head = f"## You are {agent.name}"
    if agent.title:
        head += f" — {agent.title}"
    parts.append(head)
    parts.append(
        f"Tier: {agent.tier}. Permission ceiling: {agent.permission_ceiling}. "
        + (
            "You may assign work to teammates through Jarvis' scheduler."
            if agent.may_assign
            else "You do not assign work; ask Jarvis or an orchestrator."
        )
    )
    if agent.description.strip():
        parts.append("## Standing instructions\n" + agent.description.strip())

    focus_rows = [by_id[c] for c in agent.focus if c in by_id and _allowed(c)]
    others = [r for r in catalog if r.connected and _allowed(r.id) and r.id not in agent.focus]
    hands: list[str] = ["## Your hands"]
    if focus_rows:
        hands.append("Reach for these first:")
        for row in focus_rows:
            line = f"- {row.label} ({row.id})"
            if row.one_liner:
                line += f": {row.one_liner}"
            hands.append(line)
    if others:
        grouped: dict[CapabilityKind, list[str]] = {}
        for row in others:
            grouped.setdefault(row.kind, []).append(row.label)
        also = "; ".join(
            f"{_kind_label(kind)}: {', '.join(sorted(names))}" for kind, names in grouped.items()
        )
        hands.append("Also available — " + also + ".")
    if not focus_rows and not others:
        hands.append("No connected capabilities beyond your society tools right now.")
    hands.append(f"Capability epoch: {capability_epoch(catalog)}")
    parts.append("\n".join(hands))

    parts.append(_ECOSYSTEM_CARD)

    mates = [a for a in roster if a.agent_id != agent.agent_id and str(a.state) == "active"]
    if mates:
        lines = ["## Teammates"]
        for mate in mates:
            line = f"- {mate.name}"
            if mate.title:
                line += f" — {mate.title}"
            line += f" ({mate.tier})"
            lines.append(line)
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def session_id_for(agent_id: str) -> str:
    return canonical_session_id(agent_id)
