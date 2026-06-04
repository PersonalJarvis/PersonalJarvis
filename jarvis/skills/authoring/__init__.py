"""Skill-Authoring-Pipeline (Phase 7.5).

OpenClaw (Frontier-Worker, Mission-Manager-orchestriert) generiert auf
Voice-Auftrag neue Skills. Welle-4-Migration: vorher Sub-Jarvis (Opus 4.7),
heute OpenClaw (siehe docs/openclaw-bridge.md §11, R-6). Plan-§7.5:
strict-typed Output via `SkillDraft`-Pydantic-Modell, `state=draft`-
Forcierung beim Schreiben (Plan-§AD-8), Validation-Loop ≤3 Retries,
Audit-Spur pro Authoring-Versuch.

Plan-§AP-6 (Auto-Aktivierung verboten): generierter Skill wird IMMER
mit `state=draft` ins User-Skills-Verzeichnis geschrieben — auch wenn
der Worker explizit `active` im Frontmatter angibt.
"""
from __future__ import annotations

from .draft_writer import (
    DraftWriteResult,
    SlugError,
    UnsafeSkillError,
    safe_lint_skill_body,
    write_draft,
)
from .runner import (
    AuthoringFailure,
    AuthoringSuccess,
    SkillAuthoringRunner,
)
from .schema import SkillDraft

__all__ = [
    "AuthoringFailure",
    "AuthoringSuccess",
    "DraftWriteResult",
    "SkillAuthoringRunner",
    "SkillDraft",
    "SlugError",
    "UnsafeSkillError",
    "safe_lint_skill_body",
    "write_draft",
]
