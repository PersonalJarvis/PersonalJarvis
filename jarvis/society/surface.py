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

from .agent_tools import MessageAgentTool, ShellTool, WikiNoteTool
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
#: the wiki only through its namespaced note tool and runs commands only
#: through its own contained shell (never the free-cwd shell tools).
_SOCIETY_DENIED: Final[frozenset[str]] = frozenset(
    {"wiki-ingest", "run-shell", "run_shell", "RunCommand"}
)

_ECOSYSTEM_CARD: Final[str] = """\
## The Jarvis ecosystem you work in
- Jarvis is the voice-steered lead of this society; the user talks to Jarvis by voice and to \
you by typed chat. Only Jarvis and orchestrators assign work; a scheduler (not a model) turns \
an assignment into a run under the assignee's identity. You never spawn agents or workers.
- Teammates: send ONE teammate a message with society_message_agent (kinds: say, query, \
answer, propose). Compose it yourself. When you finish work for someone, end with a handoff: \
what is done, where the output is, what evidence you used, what remains open, who owns the \
next step.
- Shell: society_shell runs commands in YOUR workspace folder only (relative paths stay inside it; \
outside paths are refused). Destructive commands ask the user first.
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
    workspace = Path(getattr(session, "cwd", "") or _workspace_fallback(cfg, agent_id))
    tools: dict[str, Tool] = {}
    # The folder tools of the chat surface, contained: on the society surface the
    # kit's tools REPLACE the folder tools (runner_brain.build_override), so the
    # agent would otherwise have no file hands at all; and the plain folder tools
    # accept absolute paths, which its workspace rule forbids.
    tools.update(_contained_folder_tools(workspace, getattr(session, "permission_mode", "")))
    tools.update(
        {
            MessageAgentTool.name: cast(Tool, MessageAgentTool(rt, agent_id)),
            WikiNoteTool.name: cast(Tool, WikiNoteTool(rt, agent_id, vault_root=_vault_root(cfg))),
            ShellTool.name: cast(Tool, ShellTool(rt, agent_id, workspace=workspace)),
        }
    )
    if rt.browser.is_installed():
        from .browser.tool import BrowserTool

        tools[BrowserTool.name] = cast(Tool, BrowserTool(rt, agent_id, rt.browser))
    return tools


class _ContainedTool:
    """A folder tool whose path arguments must stay inside the workspace."""

    _PATH_KEYS = ("file_path", "path", "directory", "cwd")

    def __init__(self, inner: Any, workspace: Path) -> None:
        self._inner = inner
        self._workspace = workspace
        self.name = inner.name
        self.description = inner.description
        self.schema = inner.schema
        self.risk_tier = inner.risk_tier

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    async def execute(self, args: dict[str, Any], ctx: Any) -> Any:
        from jarvis.core.protocols import ToolResult

        from .shell import ContainmentError, resolve_contained

        for key in self._PATH_KEYS:
            raw = args.get(key)
            if isinstance(raw, str) and raw.strip():
                try:
                    resolve_contained(self._workspace, raw)
                except ContainmentError as exc:
                    return ToolResult(success=False, output=None, error=str(exc))
        return await self._inner.execute(args, ctx)


def _contained_folder_tools(workspace: Path, permission_mode: str) -> dict[str, Tool]:
    from jarvis.agent_chat.folder_tools import folder_tools

    stance = "plan" if permission_mode == "plan" else "ask"
    out: dict[str, Tool] = {}
    for name, tool in folder_tools(workspace, stance=stance).items():
        if name == "RunCommand":
            continue  # the agent's own contained shell replaces it
        out[name] = cast(Tool, _ContainedTool(tool, workspace))
    return out


def _workspace_fallback(cfg: Any, agent_id: str) -> Path:
    data_dir = Path(getattr(getattr(cfg, "memory", None), "data_dir", None) or "data")
    return data_dir / "society" / agent_id / "workspace"


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
        own = {
            n: t
            for n, t in tools.items()
            if n.startswith(_OWN_PREFIX) or isinstance(t, _ContainedTool)
        }
        rest = {n: t for n, t in tools.items() if n not in own and n not in _SOCIETY_DENIED}
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
    browser = rt.browser.status_for(agent)
    return build_briefing(agent, catalog, roster, browser=browser)


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
    agent: AgentRecord,
    catalog: list[CapabilityRow],
    roster: list[AgentRecord],
    *,
    browser: dict[str, Any] | None = None,
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

    parts.append(_browser_line(browser))
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


def _browser_line(browser: dict[str, Any] | None) -> str:
    """One byte-stable line about the agent's browser (agent-definition §3)."""
    if not browser or not browser.get("installed"):
        return (
            "## Your browser\nNot set up on this machine yet — the user can install it from your "
            "card. Until then use plugins, CLIs and search-web for the web."
        )
    if browser.get("mode") == "attach":
        return (
            "## Your browser\nsociety_browser drives the user's own running Chrome (attached), "
            "with their logins. One task per call, capped steps."
        )
    logged = (
        "signed-in profile present"
        if browser.get("logged_in_profile")
        else ("no logins yet — ask the user for a login session when a site needs one")
    )
    return (
        "## Your browser\nsociety_browser runs in your own persistent browser profile "
        f"({logged}). One task per call, capped steps; sending, buying, deleting or "
        "publishing asks the user first."
    )


def session_id_for(agent_id: str) -> str:
    return canonical_session_id(agent_id)
