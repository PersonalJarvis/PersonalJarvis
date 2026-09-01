"""What an agent reaches for first — derived from its title and description.

Deterministic keyword matching, no model call (agent-definition §3.2): it has
to work with any single key and cost nothing. The alias table maps the words
people use ("Mails", "inbox", "Termine", "PRs") onto capability ids; catalog
labels, one-liners and MCP server names are matched too, so a freshly
connected MCP server is reachable by its own name without a table edit.

``derive_approval_rules`` reads the same text for an approval boundary
("only after approval", "nur nach Freigabe") and turns it into Grok-style
require-approval patterns on the focus tools' sending verbs.
"""

from __future__ import annotations

import re
from typing import Final

from .capabilities import CapabilityKind, CapabilityRow

__all__ = ["derive_approval_rules", "derive_focus"]

# capability id (or prefix) → words that mean it. Lower-case, matched as whole
# words on a folded text. German and English on purpose: the description is
# product-surface text the person writes in their own language.
_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "plugin:gmail": ("gmail", "mail", "mails", "email", "emails", "e-mail", "inbox", "postfach"),
    "plugin:google-calendar": (
        "calendar",
        "kalender",
        "termin",
        "termine",
        "meeting",
        "meetings",
        "appointment",
        "appointments",
    ),
    "plugin:drive": ("drive", "google drive", "gdrive"),
    "plugin:spotify": ("spotify", "playlist", "playlists", "musik", "music", "song", "songs"),
    "plugin:youtube-music": ("youtube music", "youtube-music"),
    "plugin:home-assistant": ("home assistant", "smart home", "smarthome", "licht", "lights"),
    "plugin:vercel": ("vercel", "deployment", "deployments", "deploy"),
    "cli:gh": ("github", "gh", "pull request", "pull requests", "pr", "prs", "issue", "issues"),
    "cli:git": ("git", "commit", "commits", "branch", "branches"),
    "cli:gcloud": ("gcloud", "google cloud", "gcp"),
    "cli:docker": ("docker", "container", "containers"),
    "cli:npm": ("npm", "node", "package.json"),
    "core:search-web": (
        "web",
        "internet",
        "recherche",
        "research",
        "search",
        "suche",
        "news",
        "nachrichten",
    ),
    "core:wiki-recall": ("wiki", "wissen", "knowledge", "notes", "notizen", "obsidian"),
    "core:computer-use": ("browser", "website", "webseite", "click", "klicken", "screen"),
    "core:run-shell": ("shell", "terminal", "script", "scripts", "command", "befehl"),
    "core:contact-lookup": ("kontakt", "kontakte", "contact", "contacts", "people", "leute"),
    "core:Read": ("files", "dateien", "file", "datei", "ordner", "folder", "code", "repo"),
}

_APPROVAL_PHRASES: Final[tuple[str, ...]] = (
    "only after approval",
    "after approval",
    "with approval",
    "ask before",
    "ask me before",
    "ask first",
    "nur nach freigabe",
    "nach freigabe",
    "erst nach freigabe",
    "mit freigabe",
    "frag vorher",
    "frage vorher",
    "vorher fragen",
    "nur mit erlaubnis",
)

#: Sending verbs per capability kind that an approval boundary should gate.
_SEND_VERBS: Final[dict[str, tuple[str, ...]]] = {
    "plugin:gmail": ("send",),
    "plugin:google-calendar": ("create", "update", "delete"),
    "plugin:spotify": ("play",),
    "plugin:vercel": ("deploy",),
    "cli:gh": ("create", "merge", "close"),
}

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9.\-]*")


def _fold(text: str) -> str:
    text = text.lower()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    return text


def _mentions(text: str, phrase: str) -> bool:
    phrase = _fold(phrase)
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def derive_focus(
    title: str, description: str, catalog: list[CapabilityRow], *, limit: int = 6
) -> list[str]:
    """Capability ids the text points at, strongest first, at most ``limit``.

    Scoring: a title hit weighs 3, a description hit 1 per distinct word;
    alias-table hits and catalog-name hits are both counted. Only ids that
    exist in ``catalog`` (connected or not) come back, so a description about
    Gmail on a box without the plugin yields nothing rather than a dead id.
    """
    folded_title = _fold(title or "")
    folded_desc = _fold(description or "")
    if not folded_title and not folded_desc:
        return []
    by_id = {row.id: row for row in catalog}
    scores: dict[str, int] = {}

    def _bump(cap_id: str, weight: int) -> None:
        if cap_id in by_id:
            scores[cap_id] = scores.get(cap_id, 0) + weight

    for cap_id, words in _ALIASES.items():
        for word in words:
            if _mentions(folded_title, word):
                _bump(cap_id, 3)
            if _mentions(folded_desc, word):
                _bump(cap_id, 1)
    for row in catalog:
        names = {row.label, *row.aliases}
        if row.kind is CapabilityKind.MCP:
            names.add(row.tool_name.split("/", 1)[0])
        for name in names:
            folded = _fold(name)
            if len(folded) < 3:
                continue
            if _mentions(folded_title, folded):
                _bump(row.id, 3)
            if _mentions(folded_desc, folded):
                _bump(row.id, 1)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [cap_id for cap_id, _ in ranked[:limit]]


def derive_approval_rules(description: str, focus: list[str]) -> dict[str, list[str]]:
    """Require-approval patterns when the description draws an approval line."""
    folded = _fold(description or "")
    if not any(_mentions(folded, phrase) for phrase in _APPROVAL_PHRASES):
        return {"require_approval": [], "always_allow": []}
    patterns: list[str] = []
    for cap_id in focus:
        for verb in _SEND_VERBS.get(cap_id, ()):
            patterns.append(f"{cap_id}:{verb}")
    if not patterns:
        patterns = [f"{cap_id}:*" for cap_id in focus if cap_id.startswith(("plugin:", "cli:"))]
    return {"require_approval": sorted(set(patterns)), "always_allow": []}
