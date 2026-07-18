"""Tool registry — the routing *data* contract (plain, dependency-free).

Owned by the orchestrator so the routing logic (router.py) and the tool
behaviour (tools.py / worker.py) share one catalog without importing each other.

Routing rule encoded here: DUMB tools are matched before SMART tools, so a local
action (music, volume) can never accidentally escalate to the heavy worker
(AD-OE3, metric M5 false-spawn = 0).
"""
from __future__ import annotations

from dataclasses import dataclass

from optimistic.events import RouteKind


@dataclass(frozen=True, slots=True)
class ToolDef:
    """One catalog entry. `triggers` are lowercase substrings matched against the
    (lowercased) command. Frozen + hashable so it can live in sets."""

    name: str
    kind: RouteKind
    triggers: tuple[str, ...]
    description: str = ""


# --- Dumb tools: local scripts, fired in-process in milliseconds ----------------
DUMB_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="adjusties",
        kind=RouteKind.DUMB_TOOL,
        triggers=("adjusties", "adjust", "justier"),
        description="local UI/system tweak script",
    ),
    ToolDef(
        name="play_music",
        kind=RouteKind.DUMB_TOOL,
        triggers=("spiel", "play", "musik", "spotify"),
        description="local media control",
    ),
    ToolDef(
        name="volume",
        kind=RouteKind.DUMB_TOOL,
        triggers=("lauter", "leiser", "lautstaerke", "lautstärke", "volume"),  # i18n-allow: speech input vocabulary DE
        description="local volume control",
    ),
)

# --- Smart tools: complex MCP calls, delegated to the background worker ----------
SMART_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="gmail",
        kind=RouteKind.SMART_TOOL,
        triggers=("mail", "email", "e-mail", "schreib", "maile"),
        description="compose + send an email via the Gmail MCP server",
    ),
    ToolDef(
        name="calendar",
        kind=RouteKind.SMART_TOOL,
        triggers=("termin", "kalender", "meeting", "calendar"),
        description="schedule an event via the Calendar MCP server",
    ),
    ToolDef(
        name="drive",
        kind=RouteKind.SMART_TOOL,
        triggers=("dokument", "drive", "hochladen", "upload"),
        description="file operations via the Drive MCP server",
    ),
)

ALL_TOOLS: tuple[ToolDef, ...] = DUMB_TOOLS + SMART_TOOLS

# Smalltalk allowlist — these never trigger a tool and never wake the worker.
SMALLTALK_TRIGGERS: tuple[str, ...] = (
    "hallo",
    "hi",
    "hey",
    "guten morgen",
    "guten tag",
    "wie geht",
    "danke",
    "alles klar",
    "erzaehl",
    "erzähl",  # i18n-allow: speech input vocabulary DE
    "witz",
)

# Action-verb markers — an unknown command containing one of these is treated as
# a SMART task (worker spawn) rather than smalltalk. Mirrors the production
# force-spawn heuristic in BrainManager._should_force_spawn.
ACTION_VERBS: tuple[str, ...] = (
    "lies",
    "baue",
    "installier",
    "oeffne",
    "öffne",  # i18n-allow: speech input vocabulary DE
    "mach",
    "zeig",
    "erstell",
    "such",
    "find",
    "sende",
    "buche",
)


def match_tool(command: str) -> ToolDef | None:
    """Return the first tool whose trigger appears in the command.

    Dumb tools are scanned first so a local action never escalates to the worker.
    """
    low = command.lower()
    for tool in DUMB_TOOLS:
        if any(trigger in low for trigger in tool.triggers):
            return tool
    for tool in SMART_TOOLS:
        if any(trigger in low for trigger in tool.triggers):
            return tool
    return None
