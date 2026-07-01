"""Pre-mission gate for destructive patterns.

Called BEFORE `mission_manager.dispatch()`. If the prompt looks destructive
(rm -rf, drop table, force-push, etc.), the endpoint returns HTTP 409 with
`requires_confirm: true` — the UI shows an AlertDialog, the user clicks
confirm, then re-POSTs with `confirmed: true`.

Voice path (optional, Phase 7): synchronous "wait for yes" voice mode
is not implemented in the existing speech pipeline. The Phase-5 MVP
uses UI-only confirm.

Patterns are intentionally narrow — only unambiguous destructive keywords.
False positives on "delete unused imports" must NOT trigger.
"""
from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, ConfigDict


class DestructiveDetection(BaseModel):
    """Treffer eines destruktiven Patterns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern_id: str
    matched_text: str
    target_hint: str  # "what gets destroyed" hint extracted from the pattern


# (pattern_id, regex, target_extraction_group)
DESTRUCTIVE_PATTERNS: Final[list[tuple[str, re.Pattern[str], str]]] = [
    (
        "rm_rf",
        re.compile(
            r"\brm\s+-rf?\s+(?P<target>[/~\.\w\-./\\$:]+)",
            re.IGNORECASE,
        ),
        "target",
    ),
    (
        "powershell_remove_recurse",
        re.compile(
            r"\bRemove-Item\s+(?:-Force\s+)?-Recurse\s+(?P<target>[\w\-./\\:$]+)",
            re.IGNORECASE,
        ),
        "target",
    ),
    (
        "drop_table",
        re.compile(
            r"\bdrop\s+(?P<target>(table|database|schema|index)\s+\w+)",
            re.IGNORECASE,
        ),
        "target",
    ),
    (
        "truncate_table",
        re.compile(
            r"\btruncate\s+table\s+(?P<target>\w+)",
            re.IGNORECASE,
        ),
        "target",
    ),
    (
        "git_force_push",
        re.compile(
            r"\bgit\s+push\s+(?:--force|-f)(?:\s+(?P<target>[\w./-]+))?",
            re.IGNORECASE,
        ),
        "target",
    ),
    (
        "git_reset_hard",
        re.compile(
            r"\bgit\s+reset\s+--hard(?:\s+(?P<target>[\w./-]+))?",
            re.IGNORECASE,
        ),
        "target",
    ),
    (
        "git_clean_force",
        re.compile(
            r"\bgit\s+clean\s+-f[dx]*(?:\s+(?P<target>[\w./-]+))?",
            re.IGNORECASE,
        ),
        "target",
    ),
    (
        "format_disk",
        re.compile(
            r"\b(?:format|wipe|erase)\s+(?P<target>[\w:/\\]+)",
            re.IGNORECASE,
        ),
        "target",
    ),
    (
        "delete_all_files",
        # "alle Files loeschen" / "delete all files" — word-based (DE+EN)  # i18n-allow
        re.compile(
            r"\b(?:delete|remove|loesche|loeschen|loesch)\s+(?:all|alle|gesamten?)\s+"  # i18n-allow
            r"(?P<target>(?:files?|dateien?|ordner|directories?|verzeichnisse?))",  # i18n-allow
            re.IGNORECASE,
        ),
        "target",
    ),
    (
        "drop_database_de",
        # German variant: "datenbank loeschen", "tabelle dropp(en)"  # i18n-allow
        re.compile(
            r"\b(?:datenbank|tabelle|schema)\s+(?P<target>\w+)\s+"  # i18n-allow
            r"(?:loeschen|droppen|loesche)",  # i18n-allow
            re.IGNORECASE,
        ),
        "target",
    ),
]


def is_destructive(prompt: str) -> tuple[bool, DestructiveDetection | None]:
    """Returns (True, DestructiveDetection) if the prompt looks destructive.

    Args:
        prompt: The user's mission prompt.

    Returns:
        Tuple `(found, detection)`. On multiple matches: the first one (we
        have no severity ordering — all are equally blocking).
    """
    if not prompt:
        return (False, None)
    for pattern_id, regex, target_group in DESTRUCTIVE_PATTERNS:
        match = regex.search(prompt)
        if match is None:
            continue
        target_hint = ""
        try:
            target_hint = (match.group(target_group) or "").strip()
        except (IndexError, re.error):
            target_hint = ""
        if not target_hint:
            target_hint = match.group(0)
        return (
            True,
            DestructiveDetection(
                pattern_id=pattern_id,
                matched_text=match.group(0)[:200],
                target_hint=target_hint[:120],
            ),
        )
    return (False, None)


__all__ = [
    "DESTRUCTIVE_PATTERNS",
    "DestructiveDetection",
    "is_destructive",
]
