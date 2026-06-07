"""``navigate`` — move the desktop UI to a sidebar section by voice/chat.

Router-tier, risk ``safe`` (pure UI navigation — no side effects beyond
switching the active screen). The brain calls it when the user asks to open or
show a section ("zeig die Socials", "open settings", "show the agents"). It
publishes a :class:`~jarvis.core.events.NavigateSidebar` event; the frontend
listener (``useWebSocket.ts``) switches the active section when ``section`` is a
known ``SectionId`` and otherwise no-ops gracefully.

A direct safe-gated UI action, never a spawn — it never enters a worker tool-set
(AP-5/AP-14). ``KNOWN`` mirrors the frontend ``SECTION_IDS`` (store/events.ts);
a parity test (tests/unit/plugins/tool/test_navigate.py) guards against drift.
"""
from __future__ import annotations

from typing import Any

from jarvis.core.events import NavigateSidebar
from jarvis.core.protocols import ToolResult

# Canonical section ids — mirror of SECTION_IDS in
# jarvis/ui/web/frontend/src/store/events.ts (parity-tested).
KNOWN: frozenset[str] = frozenset(
    {
        "chats",
        "agents",
        "skills",
        "plugins",
        "docs",
        "mcps",
        "tasks",
        "sessions",
        "terminal",
        "clis",
        "cli-test-hub",
        "board",
        "languages",
        "profile",
        "memory",
        "apikeys",
        "settings",
        "telephony",
        "debug",
        "outputs",
        "review",
        "socials",
        "taskbar",
        "contacts",
    }
)

# Natural-language aliases (DE + EN) → canonical id. The router usually passes an
# id from the schema enum; this is the safety net for spoken labels/synonyms.
_ALIASES: dict[str, str] = {
    "social": "socials",
    "social media": "socials",
    "soziale medien": "socials",
    "settings": "settings",
    "einstellungen": "settings",
    "config": "settings",
    "konfiguration": "settings",
    "agents": "agents",
    "agenten": "agents",
    "sub-agents": "agents",
    "sub agents": "agents",
    "subagents": "agents",
    "subagenten": "agents",
    "chat": "chats",
    "skill": "skills",
    "fähigkeiten": "skills",
    "faehigkeiten": "skills",
    "plugin": "plugins",
    "documentation": "docs",
    "dokumentation": "docs",
    "dokumente": "docs",
    "doku": "docs",
    "mcp": "mcps",
    "task": "tasks",
    "aufgaben": "tasks",
    "aufgabe": "tasks",
    "transkription": "sessions",
    "transcription": "sessions",
    "session": "sessions",
    "cli": "clis",
    "cli test hub": "cli-test-hub",
    "test hub": "cli-test-hub",
    "testhub": "cli-test-hub",
    "language": "languages",
    "sprache": "languages",
    "sprachen": "languages",
    "profil": "profile",
    "notes": "memory",
    "notizen": "memory",
    "notiz": "memory",
    "wiki": "memory",
    "api keys": "apikeys",
    "api-keys": "apikeys",
    "api key": "apikeys",
    "keys": "apikeys",
    "schlüssel": "apikeys",
    "telefonie": "telephony",
    "telefon": "telephony",
    "phone": "telephony",
    "ausgaben": "outputs",
    "output": "outputs",
    "reviews": "review",
    "task bar": "taskbar",
    "taskleiste": "taskbar",
    "contact": "contacts",
    "kontakt": "contacts",
    "kontakte": "contacts",
    "address book": "contacts",
    "adressbuch": "contacts",
    # "Extensions" is the merged sidebar entry fronting skills + plugins + clis
    # + mcps. The bare name lands on the Skills tab; "tools" lands on the Tools
    # tab (which defaults to Plugins). The underlying section ids are unchanged.
    "extensions": "skills",
    "erweiterungen": "skills",
    "erweiterung": "skills",
    "tools": "plugins",
    "werkzeuge": "plugins",
}


# phrase → canonical id, reused by the brain-side navigation-intent matcher
# (jarvis/brain/navigation_intent.py) so both share one section vocabulary.
SECTION_PHRASES: dict[str, str] = {**{s: s for s in KNOWN}, **_ALIASES}


def _resolve(raw: str) -> str | None:
    """Map a spoken section name/alias to a canonical id, or None if unknown."""
    s = " ".join((raw or "").strip().lower().split())
    if not s:
        return None
    if s in KNOWN:
        return s
    if s in _ALIASES:
        return _ALIASES[s]
    # tolerate hyphen/space spelling variants ("cli test hub" ↔ "cli-test-hub").
    hyphen = s.replace(" ", "-")
    if hyphen in KNOWN:
        return hyphen
    if hyphen in _ALIASES:
        return _ALIASES[hyphen]
    spaced = s.replace("-", " ")
    if spaced in _ALIASES:
        return _ALIASES[spaced]
    return None


class NavigateTool:
    """Switch the desktop app's active sidebar section."""

    name: str = "navigate"
    risk_tier: str = "safe"
    description: str = (
        "Open/switch the desktop app to a sidebar section (the UI screen the user "
        "sees). Use when the user asks to show or open a section — 'zeig die "
        "Socials', 'open settings', 'show the agents', 'geh zu den Aufgaben'. Pass "
        "the section id in 'section'. Do NOT use it for anything other than moving "
        "the UI."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": sorted(KNOWN),
                "description": (
                    "The sidebar section id to open "
                    "(e.g. 'socials', 'settings', 'agents')."
                ),
            }
        },
        "required": ["section"],
    }

    def __init__(self, bus: Any) -> None:
        self._bus = bus

    @classmethod
    def known_sections(cls) -> set[str]:
        return set(KNOWN)

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        section = _resolve(str(args.get("section", "")))
        if section is None:
            return ToolResult(
                success=False,
                output={"requested": args.get("section")},
                error=(
                    f"Unknown section {args.get('section')!r}. Valid sections: "
                    + ", ".join(sorted(KNOWN))
                ),
            )
        await self._bus.publish(
            NavigateSidebar(section=section, source_layer="brain.tool.navigate")
        )
        return ToolResult(
            success=True,
            output={"section": section, "summary": f"Opened the {section} section."},
        )
