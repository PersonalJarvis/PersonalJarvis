"""The chat's folder tools as Jarvis tools — one safety path, not two.

The typed chat has a working folder (the composer's folder chip), and in it
Jarvis gets hands a voice turn does not have: ``Read`` / ``Write`` / ``Edit``
/ ``Ls`` / ``Glob`` / ``Grep`` / ``RunCommand``, the file and shell tools of
:mod:`jarvis.agent_chat.tools`, scoped to that folder. On the Jarvis surface
they are not run by a private loop with its own approval card (the API
runner's way); they are wrapped as :class:`jarvis.core.protocols.Tool`
objects and merged into the brain turn's tool surface, so every call goes
through ``ToolExecutor`` like any other Jarvis tool — risk tiers, blacklist,
whitelist, the audit log and the approval workflow apply exactly as they do
on the voice path (AP-3: only the executor is authorized).

Tiers: the read-only four are ``safe``; ``Write``, ``Edit`` and
``RunCommand`` are ``ask``. What "ask" means for a given chat is decided by
its permission stance in :mod:`jarvis.agent_chat.approval_bridge` (an
``accept-edits`` chat waves ``Write``/``Edit`` through, a ``bypass`` chat
everything, an ``ask`` chat shows the card). The ``[safety.blacklist]``
patterns match ``"RunCommand <command>"`` and the bare command, so the
shipped blacklist protects the chat shell too — in every stance.

Names stay CamelCase, as the coding agents spell them: they do not collide
with any Jarvis tool (the router's set is hyphenated and lower-case), and
the tool-use loop resolves exact names first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from jarvis.agent_chat import tools as folder
from jarvis.core.protocols import ExecutionContext, RiskTier, Tool, ToolResult

#: The stance ids of the Jarvis ladder this module reads (permissions.py).
PLAN_STANCE: Final[str] = "plan"

FOLDER_RISK_TIERS: Final[dict[str, RiskTier]] = {
    "Read": "safe",
    "Ls": "safe",
    "Glob": "safe",
    "Grep": "safe",
    "Write": "ask",
    "Edit": "ask",
    "RunCommand": "ask",
}


class FolderTool:
    """One folder tool as a Jarvis ``Tool``: the spec from ``tools.py``, a cwd, a tier."""

    def __init__(self, spec: dict[str, Any], cwd: Path, risk_tier: RiskTier) -> None:
        self.name: str = str(spec["name"])
        self.description: str = str(spec.get("description") or self.name)
        self.schema: dict[str, Any] = dict(spec.get("input_schema") or {"type": "object"})
        self.risk_tier: RiskTier = risk_tier
        self._cwd = cwd

    @property
    def cwd(self) -> Path:
        return self._cwd

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        output, is_error = await folder.execute_tool(self.name, dict(args or {}), cwd=self._cwd)
        return ToolResult(
            success=not is_error,
            output=output,
            error=output if is_error else None,
        )

    def describe_args(self, args: dict[str, Any]) -> dict[str, str]:
        """The one-line summary the approval card and the timeline show."""
        summary = folder.summarize_call(self.name, dict(args or {}))
        return {"summary": summary} if summary else {}

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"FolderTool({self.name!r}, cwd={str(self._cwd)!r}, tier={self.risk_tier!r})"


def folder_tools(cwd: Path, *, stance: str = "ask") -> dict[str, Tool]:
    """The folder tools for one chat turn, keyed by name.

    In ``plan`` only the read-only tools are offered at all — a plan-mode
    chat gets no writing hands, rather than hands that are refused later.
    """
    read_only = stance == PLAN_STANCE
    out: dict[str, Tool] = {}
    for spec in folder.TOOL_SPECS:
        name = str(spec["name"])
        if read_only and name not in folder.READ_ONLY_TOOLS:
            continue
        out[name] = FolderTool(spec, cwd, FOLDER_RISK_TIERS.get(name, "ask"))
    return out


def plan_filter(tools: dict[str, Tool]) -> dict[str, Tool]:
    """Plan mode's tool surface: whatever only reads.

    Keeps a tool whose static tier is ``safe`` — reads, lookups, recall —
    and drops everything that acts (``monitor`` and up). A tool with a
    per-call tier hook (``risk_tier_for_args``) is treated by its static
    tier: a mixed tool whose default is a read stays, one whose default is
    an action goes.
    """
    return {
        name: tool for name, tool in tools.items() if getattr(tool, "risk_tier", None) == "safe"
    }


__all__ = ["FOLDER_RISK_TIERS", "PLAN_STANCE", "FolderTool", "folder_tools", "plan_filter"]
