"""Pydantic models for the skill-authoring pipeline (Phase 7.5)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SkillDraft(BaseModel):
    """Strict-typed Jarvis-Agent-Author-Output.

    Wave-4 migration: previously Sub-Jarvis output. Now Jarvis-Agent worker
    (see docs/jarvis-agents-bridge.md §11). Schema stays 1:1.

    The worker delivers a complete skill specification. Plan §7.5: on a
    parse error the authoring attempt fails and writes an
    `author_failed_parse` audit event. All fields are required in
    strict mode (Plan §AD-9).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=400)
    category: str = "general"
    intent: str = Field(min_length=1)
    triggers_yaml: str = Field(default="[]")  # YAML array literal
    requires_tools: list[str] = Field(default_factory=list)
    body_markdown: str = Field(min_length=1)
    # The worker is allowed to set `state` here — but `draft_writer`
    # unconditionally ignores it and forces "draft" (Plan §AD-8). The
    # explicit field here keeps the model parsable if the LLM emits `active`;
    # the override is recorded in the audit trail as
    # `forced_state_override=True`.
    state: Literal["draft", "validated", "active", "disabled"] = "draft"

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, v: str) -> str:
        """Slug hardening (Plan §AP-10 + path-traversal protection):
        - lowercase ASCII, only a-z, 0-9, hyphen
        - max 64 characters
        - no path-traversal sequences (`..`, `/`, `\\`)
        """
        if not v:
            raise ValueError("slug must not be empty")
        normalized = v.strip().lower()
        if any(c in normalized for c in ("/", "\\", "..", " ")):
            raise ValueError(f"slug contains path-traversal characters: {v!r}")
        for ch in normalized:
            if not (ch.isalnum() or ch in "-_"):
                raise ValueError(
                    f"slug contains invalid characters: {ch!r} (only a-z, 0-9, '-', '_')"
                )
        return normalized
