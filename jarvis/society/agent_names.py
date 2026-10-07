"""Find the Jarvis agent a spoken name means — and say when it means a pane.

``Roster.resolve`` stays exact (ids, REST routes and the scheduler depend on
that). This module is the forgiving front door the voice and chat tools use:
it scores a spoken reference against every live agent with
``jarvis.core.spoken_names`` and returns act / ask / none.

Aliases come from three places, all merged here:

* the agent itself — its name, its id spelled as words, its title (unless the
  title is a generic tier word such as "Specialist");
* its role — an agent whose name or title says it codes also answers to
  "coding agent", "code agent", "coder", "Programmierer" ...
  (``ROLE_ALIASES`` — the one list to extend for a new role);
* the user — extra spellings stored per agent in ``society_meta`` under
  ``agent_aliases:<agent_id>`` (``set_custom_aliases``).

It also answers the cross-question that caused the 2026-10-02 miss: a name
that is no Jarvis agent may be a coding pane in the Agentic IDE, and a name a
coding-pane lookup cannot find may be a Jarvis agent. Each side can ask the
other (``coding_pane_hint`` / ``jarvis_agent_hint``), so a tool result can
point the model at the right tool instead of reporting "not found".
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from jarvis.core.spoken_names import (
    NameCandidate,
    NameResolution,
    has_coding_role,
    normalize,
    resolve_name,
)

from .roster import LEAD_AGENT_ID, AgentRecord, AgentState

log = logging.getLogger(__name__)

#: Meta key prefix for the user's own aliases of one agent (JSON list).
ALIAS_META_PREFIX: Final[str] = "agent_aliases:"
_MAX_ALIASES: Final[int] = 20
_MAX_ALIAS_LEN: Final[int] = 60

#: Extra ways to say an agent of a given role. Extend here for a new role.
ROLE_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "coding": (
        # i18n-allow: speech-recognition input vocabulary, not prose
        "coding agent",
        "code agent",
        "coder",
        "programmierer",
        "entwickler",
        "developer",
    ),
}

#: Titles that describe a tier, not one agent; never an alias.
_GENERIC_TITLES: Final[frozenset[str]] = frozenset(
    {"specialist", "assistant", "lead", "orchestrator", "agent", ""}
)

#: The user's aliases as last read, so the synchronous path (the coding-pane
#: tool) sees the same spellings the async tools do.
_alias_cache: dict[str, tuple[str, ...]] = {}


@dataclass(frozen=True)
class AgentLookup:
    """A resolved agent reference: the decision plus what the tools report."""

    heard: str
    resolution: NameResolution
    agent: AgentRecord | None = None
    candidates: tuple[AgentRecord, ...] = ()
    roster: tuple[AgentRecord, ...] = field(default_factory=tuple)

    @property
    def decision(self) -> str:
        return self.resolution.decision

    def available_names(self) -> list[str]:
        return [a.name for a in self.roster if a.state is not AgentState.ARCHIVED]

    def match_info(self) -> dict[str, Any] | None:
        """How the name was matched, when it was not spelled exactly."""
        best = self.resolution.best
        if best is None or best.method == "exact":
            return None
        return {
            "heard": self.heard,
            "matched": best.label,
            "method": best.method,
            "score": round(best.score, 3),
        }


def role_tags(agent: AgentRecord) -> frozenset[str]:
    return frozenset({"coding"}) if has_coding_role(agent.name, agent.title) else frozenset()


def candidate_for(agent: AgentRecord, custom: Iterable[str] = ()) -> NameCandidate:
    roles = role_tags(agent)
    aliases: list[str] = []
    if normalize(agent.title) not in _GENERIC_TITLES:
        aliases.append(agent.title)
    for role in sorted(roles):
        aliases.extend(ROLE_ALIASES.get(role, ()))
    aliases.extend(custom)
    return NameCandidate(
        key=agent.agent_id,
        label=agent.name,
        names=(agent.name, agent.agent_id.replace("-", " ")),
        aliases=tuple(dict.fromkeys(a for a in aliases if a)),
        roles=roles,
    )


def _clean_aliases(values: Iterable[Any]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split())[:_MAX_ALIAS_LEN]
        if text and normalize(text) and text not in out:
            out.append(text)
    return tuple(out[:_MAX_ALIASES])


async def load_custom_aliases(store: Any) -> dict[str, tuple[str, ...]]:
    """Every agent's user-defined aliases; ``{}`` when the store cannot say."""
    try:
        rows = await store.meta_prefix(ALIAS_META_PREFIX)
    except Exception:  # noqa: BLE001 - aliases are optional; matching works without them
        log.warning("agent names: custom aliases could not be read", exc_info=True)
        return dict(_alias_cache)
    aliases: dict[str, tuple[str, ...]] = {}
    for key, raw in rows.items():
        try:
            values = json.loads(raw)
        except ValueError:
            log.warning("agent names: ignoring malformed aliases under %s", key)
            continue
        if isinstance(values, list):
            aliases[key[len(ALIAS_META_PREFIX) :]] = _clean_aliases(values)
    _alias_cache.clear()
    _alias_cache.update(aliases)
    return aliases


async def set_custom_aliases(store: Any, agent_id: str, aliases: Iterable[str]) -> tuple[str, ...]:
    """Replace one agent's user-defined aliases; returns what was stored."""
    clean = _clean_aliases(aliases)
    await store.set_meta(f"{ALIAS_META_PREFIX}{agent_id}", json.dumps(list(clean)))
    _alias_cache[agent_id] = clean
    return clean


def lookup_in(
    agents: Sequence[AgentRecord],
    spoken: str,
    *,
    aliases: dict[str, tuple[str, ...]] | None = None,
    context: str = "",
    surface: str = "",
    include_lead: bool = False,
) -> AgentLookup:
    """Resolve ``spoken`` against ``agents`` (no I/O; any thread)."""
    custom = _alias_cache if aliases is None else aliases
    live = tuple(
        a
        for a in agents
        if a.state is not AgentState.ARCHIVED and (include_lead or a.agent_id != LEAD_AGENT_ID)
    )
    resolution = resolve_name(
        spoken,
        [candidate_for(a, custom.get(a.agent_id, ())) for a in live],
        context=context,
        surface=surface,
    )
    by_id = {a.agent_id: a for a in live}
    agent = by_id.get(resolution.key or "")
    candidates = tuple(by_id[m.key] for m in resolution.candidates if m.key in by_id)
    return AgentLookup(
        heard=str(spoken or "").strip(),
        resolution=resolution,
        agent=agent,
        candidates=candidates,
        roster=live,
    )


async def lookup_agent(
    runtime: Any,
    spoken: str,
    *,
    context: str = "",
    surface: str = "",
    include_lead: bool = False,
) -> AgentLookup:
    """Resolve a spoken agent reference against the live roster.

    An exact id, name or slug wins first, exactly as ``Roster.resolve`` always
    did — so nothing that worked before changes. Only a reference that names
    no agent exactly (or only an archived one) goes through the scoring.
    """
    exact = await runtime.roster.resolve(spoken)
    if exact is not None and exact.state is not AgentState.ARCHIVED:
        resolution = resolve_name(
            spoken, [candidate_for(exact)], surface=surface
        )  # logs the exact hit with the same line shape as every other resolution
        return AgentLookup(
            heard=spoken.strip(),
            resolution=resolution,
            agent=exact,
            candidates=(exact,),
            roster=tuple(runtime.roster.snapshot()),
        )
    agents = await runtime.roster.list()
    aliases = await load_custom_aliases(runtime.store)
    found = lookup_in(
        agents,
        spoken,
        aliases=aliases,
        context=context,
        surface=surface,
        include_lead=include_lead,
    )
    if found.decision == "none" and exact is not None:
        # Only an archived agent carries this exact name: keep the old
        # behavior and hand it back, the caller decides what archived means.
        return AgentLookup(
            heard=found.heard,
            resolution=found.resolution,
            agent=exact,
            candidates=(exact,),
            roster=found.roster,
        )
    return found


def jarvis_agent_hint(spoken: str, *, context: str = "") -> dict[str, Any] | None:
    """The Jarvis agent a name a coding-pane lookup could not place means.

    Synchronous and I/O-free (the roster snapshot and the alias cache), so the
    coding-pane tool can call it on its own result without a second hop.
    """
    try:
        from .runtime import current_runtime

        runtime = current_runtime()
        agents = runtime.roster.snapshot() if runtime is not None else []
    except Exception:  # noqa: BLE001 - a missing society only means no hint
        log.warning("agent names: roster snapshot unavailable for a hint", exc_info=True)
        return None
    if not agents or not str(spoken or "").strip():
        return None
    found = lookup_in(agents, spoken, context=context, surface="workspace-orchestrate:hint")
    if found.decision == "act" and found.agent is not None:
        names, certain = [found.agent.name], True
    elif found.decision == "ask" and found.candidates:
        names, certain = [a.name for a in found.candidates], False
    else:
        return None
    return {"heard": found.heard, "agents": names, "certain": certain}


def coding_pane_hint(spoken: str) -> dict[str, Any] | None:
    """The Agentic-IDE coding pane a name no Jarvis agent carries means, if any."""
    try:
        from jarvis.agentic_ide.names import position_of
        from jarvis.agentic_ide.orchestration import get_orchestrator

        graph = get_orchestrator().graph()
    except Exception:  # noqa: BLE001 - no IDE (headless, stripped install): no hint
        log.debug("agent names: coding-pane graph unavailable for a hint", exc_info=True)
        return None
    panes: list[NameCandidate] = []
    where: dict[str, dict[str, str]] = {}
    for project in graph.get("projects", []):
        for workspace in project.get("workspaces", []):
            for pane in workspace.get("agents", []):
                name = str(pane.get("name") or "")
                if not name or position_of(name) is not None or not pane.get("accepts_tasks"):
                    continue
                key = str(pane.get("id") or name)
                panes.append(NameCandidate(key=key, label=name, names=(name,)))
                where[key] = {"agent": name, "workspace": str(workspace.get("name") or "")}
    if not panes:
        return None
    resolution = resolve_name(spoken, panes, surface="delegate_to_agent:pane-hint")
    if resolution.decision == "none":
        return None
    return {
        "heard": str(spoken or "").strip(),
        "panes": [where[m.key] for m in resolution.candidates if m.key in where],
        "certain": resolution.decision == "act",
    }


__all__ = [
    "ALIAS_META_PREFIX",
    "ROLE_ALIASES",
    "AgentLookup",
    "candidate_for",
    "coding_pane_hint",
    "jarvis_agent_hint",
    "load_custom_aliases",
    "lookup_agent",
    "lookup_in",
    "role_tags",
    "set_custom_aliases",
]
