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

from typing import TYPE_CHECKING

from .draft_writer import (
    DraftWriteResult,
    SlugError,
    UnsafeSkillError,
    safe_lint_skill_body,
    write_draft,
)
from .schema import SkillDraft
from .service import (
    SkillAuthoringError,
    SkillAuthoringService,
    SkillCreateRequest,
    body_has_instructions,
    render_skill_md,
    slugify,
)

# ``runner`` imports ``jarvis.core.self_mod``, which participates in a latent
# import cycle (self_mod -> config -> brain -> voice.echo_confirmation ->
# self_mod). Eager-importing it here would drag that cycle into every
# ``import jarvis.skills.authoring`` — including the cycle-free UI Skill Creator
# path (``creator_service`` -> ``authoring.service``). Load the runner lazily
# (PEP 562): the mission pipeline that actually uses it triggers the import on
# first attribute access, by which point the cycle is warm. ``.service``,
# ``.draft_writer`` and ``.schema`` carry no self_mod dependency and stay eager.
_LAZY_RUNNER_EXPORTS = frozenset(
    {"AuthoringFailure", "AuthoringSuccess", "SkillAuthoringRunner"}
)

if TYPE_CHECKING:  # pragma: no cover — type-checker visibility only
    from .runner import (
        AuthoringFailure,
        AuthoringSuccess,
        SkillAuthoringRunner,
    )


def __getattr__(name: str):
    if name in _LAZY_RUNNER_EXPORTS:
        from . import runner

        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AuthoringFailure",
    "AuthoringSuccess",
    "DraftWriteResult",
    "SkillAuthoringError",
    "SkillAuthoringRunner",
    "SkillAuthoringService",
    "SkillCreateRequest",
    "SkillDraft",
    "SlugError",
    "UnsafeSkillError",
    "body_has_instructions",
    "render_skill_md",
    "safe_lint_skill_body",
    "slugify",
    "write_draft",
]
