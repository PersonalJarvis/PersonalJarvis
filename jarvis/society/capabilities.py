"""ONE catalog over everything a society agent could be handed.

Plugins (``jarvis.tool`` entry points, marketplace included), connected CLIs
(``cli_<name>`` tools), MCP servers (``<server>/<tool>`` adapters), skills
(``active`` lifecycle only) and the built-in core tools all already exist in
their own registries. This module is a read-only view that gives each of
them one id (``plugin:gmail``, ``cli:gh``, ``mcp:github/create_issue``,
``skill:daily-brief``, ``core:search-web``) so a roster row can grant, focus
or deny them, and the model card can list them grouped by kind.

Never granted, structurally (AP-5/AP-14): every dispatch tool and every app
control tool. They are filtered out here, so no later layer can hand them to
an agent by accident.

Pure functions over the objects passed in — no registry is imported at
module import time (AP-26); the runtime wires the live sources.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

__all__ = [
    "CapabilityKind",
    "CapabilityRow",
    "NEVER_GRANTED",
    "build_catalog",
    "capability_id_for_tool",
    "select_tools",
    "tool_name_for_capability",
]


class CapabilityKind(StrEnum):
    PLUGIN = "plugin"
    CLI = "cli"
    MCP = "mcp"
    SKILL = "skill"
    CORE = "core"


#: Tool names that never enter any society tool set. Dispatch is the
#: scheduler's privilege; app control stays with Jarvis.
NEVER_GRANTED: Final[frozenset[str]] = frozenset(
    {
        "spawn-worker",
        "spawn-subagents",
        "multi-spawn",
        "dispatch-to-harness",
        "dispatch-to-admin",
        "dispatch-with-review",
        "create-artifact",
        "navigate",
        "switch-provider",
        "manage-mcp-server",
        "app-command",
        "create-skill",
        "reveal-key-preview",
        "profile-update",
        "society_message_agent",
    }
)

#: Built-in hands that are not "a plugin someone connected".
_CORE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "search-web",
        "search-backends",
        "run-shell",
        "screen-snapshot",
        "computer-use",
        "remember",
        "whoami",
        "wiki-recall",
        "wiki-ingest",
        "wiki-list",
        "wiki-page-read",
        "awareness-recall",
        "awareness-snapshot",
        "run-skill",
        "inspect-pointer",
        "open-app",
        "type-text",
        "hotkey",
        "click",
        "click-element",
        "scroll",
        "drag",
        "move-mouse",
        "switch-window",
        "read-visible-ui-state",
        "wait-for-ui-state",
        "wait-for-element",
        "describe-app-settings",
        "contact-lookup",
        "contact-upsert",
        "start-preview-server",
        "verify-localhost",
        "verify-via-curl",
        "Read",
        "Write",
        "Edit",
        "Ls",
        "Glob",
        "Grep",
        "RunCommand",
    }
)


@dataclass(frozen=True, slots=True)
class CapabilityRow:
    id: str
    kind: CapabilityKind
    label: str
    one_liner: str
    risk_tier: str
    connected: bool
    #: The tool name in the brain's registry (empty for skills).
    tool_name: str
    #: Extra words the focus matcher may use (server name, aliases).
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": str(self.kind),
            "label": self.label,
            "one_liner": self.one_liner,
            "risk_tier": self.risk_tier,
            "connected": self.connected,
            "tool_name": self.tool_name,
            "aliases": list(self.aliases),
        }


def capability_id_for_tool(tool_name: str) -> str | None:
    """The catalog id of a brain tool name; ``None`` for never-granted tools."""
    if tool_name in NEVER_GRANTED:
        return None
    if tool_name.startswith("cli_"):
        return f"cli:{tool_name[4:]}"
    if "/" in tool_name:
        return f"mcp:{tool_name}"
    if tool_name in _CORE_TOOLS:
        return f"core:{tool_name}"
    return f"plugin:{tool_name}"


def tool_name_for_capability(capability_id: str) -> str | None:
    """Inverse of :func:`capability_id_for_tool`; ``None`` for skills."""
    kind, _, rest = capability_id.partition(":")
    if kind == "cli":
        return f"cli_{rest}"
    if kind in ("mcp", "core", "plugin"):
        return rest
    return None


def _one_liner(text: str, limit: int = 140) -> str:
    first = " ".join((text or "").strip().split())
    # MCP descriptions carry the adapter's warning banner; keep the server's words.
    if first.startswith("[ACTION-ONLY"):
        close = first.find("]")
        if close > 0:
            first = first[close + 1 :].strip()
    if "." in first[:limit]:
        first = first[: first.index(".") + 1]
    return first[:limit]


def _label(tool_name: str, kind: CapabilityKind) -> str:
    if kind is CapabilityKind.CLI:
        return tool_name[4:]
    if kind is CapabilityKind.MCP:
        return tool_name
    return tool_name.replace("_", "-")


def _skill_fields(skill: Any) -> tuple[str, str, str]:
    """``(slug, label, one_liner)`` from a Skill or a look-alike."""
    fm = getattr(skill, "frontmatter", None)
    slug = getattr(skill, "slug", None) or getattr(fm, "name", None) or getattr(skill, "name", None)
    if not slug:
        path = getattr(skill, "path", None)
        slug = getattr(path, "stem", None) or "skill"
    label = getattr(fm, "title", None) or getattr(fm, "name", None) or str(slug)
    desc = getattr(fm, "description", None) or getattr(skill, "description", "") or ""
    return str(slug), str(label), _one_liner(str(desc))


def _skill_is_active(skill: Any) -> bool:
    state = getattr(skill, "state", None)
    value = getattr(state, "value", state)
    return str(value).lower() == "active"


def build_catalog(
    tools: Mapping[str, Any] | None,
    skills: Iterable[Any] | None = None,
    *,
    connected: Callable[[str, CapabilityKind], bool] | None = None,
) -> list[CapabilityRow]:
    """The catalog for a brain tool registry plus a skill registry.

    ``connected(tool_name, kind)`` may report a plugin whose credential is
    missing; by default everything the brain loaded counts as connected.
    Rows come back connected-first within each kind, kinds in the order the
    card shows them (plugin, cli, mcp, skill, core).
    """
    rows: list[CapabilityRow] = []
    for name, tool in (tools or {}).items():
        cap_id = capability_id_for_tool(name)
        if cap_id is None:
            continue
        kind = CapabilityKind(cap_id.split(":", 1)[0])
        aliases: tuple[str, ...] = ()
        if kind is CapabilityKind.MCP:
            server = name.split("/", 1)[0]
            aliases = (server,)
        is_connected = True if connected is None else bool(connected(name, kind))
        rows.append(
            CapabilityRow(
                id=cap_id,
                kind=kind,
                label=_label(name, kind),
                one_liner=_one_liner(getattr(tool, "description", "") or ""),
                risk_tier=str(getattr(tool, "risk_tier", "monitor") or "monitor"),
                connected=is_connected,
                tool_name=name,
                aliases=aliases,
            )
        )
    for skill in skills or ():
        if not _skill_is_active(skill):
            continue
        slug, label, one_liner = _skill_fields(skill)
        rows.append(
            CapabilityRow(
                id=f"skill:{slug}",
                kind=CapabilityKind.SKILL,
                label=label,
                one_liner=one_liner,
                risk_tier="monitor",
                connected=True,
                tool_name="",
            )
        )
    order = {k: i for i, k in enumerate(CapabilityKind)}
    rows.sort(key=lambda r: (order[r.kind], not r.connected, r.label.lower()))
    return rows


def select_tools(
    tools: Mapping[str, Any],
    *,
    grant_mode: str,
    grants: Iterable[str],
    focus: Iterable[str],
    denies: Iterable[str],
) -> dict[str, Any]:
    """The agent's tool dict per agent-definition §3.2, in deterministic order.

    1. start from everything (``all``) or the allow-list;
    2. drop denies and every never-granted tool;
    3. order focus first, the rest alphabetically — prompt-cache safe because
       the order depends only on the roster row.
    """
    granted = set(grants)
    denied = set(denies)
    focus_list = [f for f in focus]
    keep: dict[str, Any] = {}
    for name, tool in tools.items():
        cap_id = capability_id_for_tool(name)
        if cap_id is None or cap_id in denied:
            continue
        if grant_mode == "allowlist" and cap_id not in granted:
            continue
        keep[cap_id] = (name, tool)
    ordered: dict[str, Any] = {}
    for cap_id in focus_list:
        if cap_id in keep:
            name, tool = keep[cap_id]
            ordered[name] = tool
    for cap_id in sorted(keep):
        name, tool = keep[cap_id]
        if name not in ordered:
            ordered[name] = tool
    return ordered
