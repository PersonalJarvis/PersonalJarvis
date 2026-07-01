"""Skill-authoring pipeline (Phase 7.5).

OpenClaw (frontier worker, Mission-Manager-orchestrated) generates new skills
on voice request. Welle-4 migration: previously Sub-Jarvis (Opus 4.7),
today OpenClaw (see docs/openclaw-bridge.md §11, R-6). Plan-§7.5:
strict-typed output via the `SkillDraft` Pydantic model, `state=draft`
enforcement on write (Plan-§AD-8), validation loop ≤3 retries,
an audit trail per authoring attempt.

Plan-§AP-6 (auto-activation forbidden): a generated skill is ALWAYS
written to the user-skills directory with `state=draft` — even if
the worker explicitly states `active` in the frontmatter.
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
