"""Pydantic-Modelle für die Skill-Authoring-Pipeline (Phase 7.5)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SkillDraft(BaseModel):
    """Strict-typed OpenClaw-Author-Output.

    Welle-4-Migration: vorher Sub-Jarvis-Output. Heute OpenClaw-Worker
    (siehe docs/openclaw-bridge.md §11). Schema bleibt 1:1.

    Worker liefert eine vollständige Skill-Spezifikation. Plan-§7.5:
    bei Parse-Fehler scheitert der Authoring-Versuch und schreibt ein
    `author_failed_parse`-Audit-Event. Alle Felder sind required im
    strict-Mode (Plan-§AD-9).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=400)
    category: str = "general"
    intent: str = Field(min_length=1)
    triggers_yaml: str = Field(default="[]")  # YAML-Array-Literal
    requires_tools: list[str] = Field(default_factory=list)
    body_markdown: str = Field(min_length=1)
    # Der Worker darf hier `state` setzen — der `draft_writer` ignoriert
    # das aber unconditional und forciert "draft" (Plan-§AD-8). Das
    # explizite Field hier macht das Modell parsbar wenn das LLM `active`
    # ausgibt; der Override wird im Audit als `forced_state_override=True`
    # vermerkt.
    state: Literal["draft", "validated", "active", "disabled"] = "draft"

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        """Slug-Hardening (Plan-§AP-10 + Path-Traversal-Schutz):
        - lowercase ASCII, nur a-z, 0-9, Bindestrich
        - max 64 Zeichen
        - keine Path-Traversal-Sequenzen (`..`, `/`, `\\`)
        """
        if not v:
            raise ValueError("slug darf nicht leer sein")
        normalized = v.strip().lower()
        if any(c in normalized for c in ("/", "\\", "..", " ")):
            raise ValueError(f"slug enthält Path-Traversal-Zeichen: {v!r}")
        for ch in normalized:
            if not (ch.isalnum() or ch in "-_"):
                raise ValueError(
                    f"slug enthält ungültige Zeichen: {ch!r} (nur a-z, 0-9, '-', '_')"
                )
        return normalized
