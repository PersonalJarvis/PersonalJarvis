"""SkillAuthoringRunner — orchestrates the Jarvis-Agent-Author spawn + validation loop.

Welle-4 migration: the spawn path used to be called ``Sub-Jarvis``. After the
Jarvis-Agent-bridge migration (see docs/jarvis-agents-bridge.md §11, R-6), the
spawn callback is bound to the ``MissionManager`` — the ``SpawnCallback``
signature (``str -> Awaitable[str]``) stays backward-compatible and can
implement either a direct brain call or a mission dispatch + result read.

Plan-§7.5 pipeline:
1. Slug generation (kebab-case from suggested_name or LLM-derived)
2. Clash check against user_skills_dir
3. Staging-dir creation (tempfile.mkdtemp)
4. Jarvis-Agent-Author spawn with a task description
5. Validation loop ≤3 iterations
6. Success path: draft_writer copies to user_skills_dir with forced state=draft
7. SkillRegistry watcher detects it + the UI shows a warn badge

Plan-§AP-6: no auto-activation. Plan-§AP-7: skill authoring NEVER in the
main-Jarvis path — only Jarvis-Agent-Author (frontier worker via Mission-Manager).
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jarvis.core.self_mod import (
    AuditActor,
    AuditEvent,
    AuditSource,
    SelfModAudit,
)

from .draft_writer import (
    DraftWriteResult,
    UnsafeSkillError,
    safe_lint_skill_body,
    write_draft,
)
from .schema import SkillDraft

_LOG = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300.0  # 5 minutes (Plan-§7.5)
DEFAULT_MAX_ITERATIONS = 3       # Plan-§7.5 validation loop ≤3


@dataclass(frozen=True)
class AuthoringSuccess:
    """A successful authoring attempt."""

    skill_name: str
    slug: str
    draft_path: Path
    iterations: int
    forced_state_override: bool
    review_url: str


@dataclass(frozen=True)
class AuthoringFailure:
    """A failed authoring attempt."""

    error_kind: str
    message: str
    iterations: int = 0
    validation_errors: tuple[str, ...] = field(default_factory=tuple)


# Spawn callback: dependency-injected, so tests can mock the Jarvis-Agent-Author
# call (plan constraint: NEVER real API calls in tests).
SpawnCallback = Callable[[str], "asyncio.Future[str] | str"]


# ----------------------------------------------------------------------
# System prompt for Jarvis-Agent-Author (Plan-§7.5)
# ----------------------------------------------------------------------


JARVIS_AGENT_AUTHOR_SYSTEM_PROMPT = """\
You are Jarvis-Agent-Author (Opus 4.7), a skill-authoring specialist.

The user wants to create a new skill. You return ONLY a single JSON
object that exactly matches the `SkillDraft` schema. No surrounding
prose, no Markdown codefence — pure JSON.

Constraints (binding, NEVER overridable):
- You NEVER produce executable code outside the skill-sandbox boundary.
  Allowed imports are listed in `jarvis/skills/safe_imports.txt`.
- You NEVER output state != "draft", even if the user asks for it —
  the pipeline forces "draft" anyway (a state-override audit reminds you).
- `eval`, `exec`, `compile`, `os.system`, `subprocess.Popen(shell=True)`
  are blocked by the promote lint — skills with such calls are
  rejected.
- Slug is lowercase, kebab-case, max 64 characters, no path separators.
- Body is Markdown with a YAML frontmatter — body Markdown without frontmatter,
  the frontmatter is reconstructed by draft_writer.

Format example (JSON):
{
  "slug": "spotify-auto-pause",
  "name": "Spotify Auto-Pause",
  "description": "Pauses Spotify when the user speaks.",
  "category": "automation",
  "intent": "User wants Spotify to pause during voice interactions",
  "triggers_yaml": "[{type: voice, pattern: '^pause spotify'}]",
  "requires_tools": ["run-shell"],
  "body_markdown": "## Spotify Auto-Pause\\n\\nThis skill ...",
  "state": "draft"
}
"""


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------


class SkillAuthoringRunner:
    """Plan-§7.5 authoring pipeline (Jarvis-Agent-Author spawn + validation loop)."""

    def __init__(
        self,
        *,
        spawn_callback: SpawnCallback,
        audit: SelfModAudit,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        user_skills_root: Path | None = None,
    ) -> None:
        self._spawn = spawn_callback
        self._audit = audit
        self._max_iterations = max_iterations
        self._timeout_seconds = timeout_seconds
        self._user_skills_root = user_skills_root

    async def author(
        self,
        intent: str,
        *,
        suggested_name: str | None = None,
        trigger_hint: str | None = None,
    ) -> AuthoringSuccess | AuthoringFailure:
        """Main method. Plan-§7.5 voice output:
        SUCCESS / VALIDATION_FAIL / TIMEOUT.
        """
        prompt = self._build_user_prompt(
            intent=intent,
            suggested_name=suggested_name,
            trigger_hint=trigger_hint,
        )
        last_errors: tuple[str, ...] = ()

        for iteration in range(1, self._max_iterations + 1):
            try:
                response = await asyncio.wait_for(
                    self._call_spawn(prompt),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError:
                self._record_audit(
                    error_kind="author_timeout",
                    message=f"Jarvis-Agent-Author spawn timeout after {self._timeout_seconds}s",
                    iterations=iteration,
                    intent=intent,
                )
                return AuthoringFailure(
                    error_kind="timeout",
                    message="Jarvis-Agent-Author-Spawn timeout",
                    iterations=iteration,
                )

            try:
                draft = self._parse_draft(response)
            except (ValueError, ValidationError) as exc:
                last_errors = (str(exc),)
                if iteration < self._max_iterations:
                    prompt = self._build_retry_prompt(prompt, str(exc))
                    continue
                self._record_audit(
                    error_kind="author_failed_parse",
                    message=str(exc),
                    iterations=iteration,
                    intent=intent,
                )
                return AuthoringFailure(
                    error_kind="parse_failed",
                    message=str(exc),
                    iterations=iteration,
                    validation_errors=last_errors,
                )

            # Security lint before writing (Plan-§7.5)
            findings = safe_lint_skill_body(draft.body_markdown)
            if findings:
                last_errors = tuple(findings)
                if iteration < self._max_iterations:
                    prompt = self._build_retry_prompt(
                        prompt, "Skill body contains forbidden calls: "
                        + ", ".join(findings)
                    )
                    continue
                self._record_audit(
                    error_kind="author_failed_unsafe",
                    message="; ".join(findings),
                    iterations=iteration,
                    intent=intent,
                )
                return AuthoringFailure(
                    error_kind="unsafe",
                    message="Skill body contains disallowed calls",
                    iterations=iteration,
                    validation_errors=last_errors,
                )

            # Success path
            try:
                result: DraftWriteResult = write_draft(
                    draft, user_skills_root=self._user_skills_root
                )
            except (OSError, UnsafeSkillError) as exc:
                self._record_audit(
                    error_kind="author_failed_write",
                    message=str(exc),
                    iterations=iteration,
                    intent=intent,
                )
                return AuthoringFailure(
                    error_kind="write_failed",
                    message=str(exc),
                    iterations=iteration,
                )

            self._record_audit(
                error_kind=None,  # success
                message="skill_authored",
                iterations=iteration,
                intent=intent,
                slug=result.slug,
                draft_path=str(result.draft_path),
                forced_state_override=result.forced_state_override,
            )
            return AuthoringSuccess(
                skill_name=draft.name,
                slug=result.slug,
                draft_path=result.draft_path,
                iterations=iteration,
                forced_state_override=result.forced_state_override,
                review_url=f"/api/skills/{result.slug}",
            )

        # Loop exited without returning → defensive fallback
        return AuthoringFailure(
            error_kind="exhausted",
            message="Validation loop after 3 iterations without success",
            iterations=self._max_iterations,
            validation_errors=last_errors,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _call_spawn(self, prompt: str) -> str:
        """Uses the injected spawn callback. Awaitable or direct str."""
        result = self._spawn(prompt)
        if asyncio.iscoroutine(result):
            return await result
        if asyncio.isfuture(result):
            return await result
        return str(result)

    def _build_user_prompt(
        self,
        *,
        intent: str,
        suggested_name: str | None,
        trigger_hint: str | None,
    ) -> str:
        parts = [f"Intent: {intent}"]
        if suggested_name:
            parts.append(f"Suggested name: {suggested_name}")
        if trigger_hint:
            parts.append(f"Trigger hint: {trigger_hint}")
        parts.append(
            "\nReturn a JSON object matching the SkillDraft schema. "
            "JSON only, no prose."
        )
        return "\n".join(parts)

    def _build_retry_prompt(self, last_prompt: str, error: str) -> str:
        return (
            last_prompt
            + f"\n\nError on the last attempt: {error}\n"
            + "Please correct it and try again."
        )

    def _parse_draft(self, response: str) -> SkillDraft:
        """Extracts JSON from the Jarvis-Agent-Author response.

        Jarvis-Agent-Author should deliver pure JSON; we tolerate an
        enclosing ```json…``` for Markdown bias.
        """
        import json

        cleaned = response.strip()
        # Strip a Markdown codefence, if present
        if cleaned.startswith("```"):
            match = re.match(
                r"```(?:json)?\s*\n?(.*?)```",
                cleaned,
                re.DOTALL | re.IGNORECASE,
            )
            if match:
                cleaned = match.group(1).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Jarvis-Agent-Author response is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Jarvis-Agent-Author response must be a JSON object")
        return SkillDraft.model_validate(data)

    def _record_audit(
        self,
        *,
        error_kind: str | None,
        message: str,
        iterations: int,
        intent: str,
        slug: str | None = None,
        draft_path: str | None = None,
        forced_state_override: bool = False,
    ) -> None:
        """Writes an audit event with `type=skill_authored` (Plan-§7.5)."""
        extras: dict[str, Any] = {
            "type": "skill_authored",
            "intent": intent,
            "iterations": iterations,
        }
        if slug is not None:
            extras["skill_name"] = slug
        if draft_path is not None:
            extras["draft_path"] = draft_path
        extras["forced_state_override"] = forced_state_override

        try:
            self._audit.record(
                AuditEvent(
                    source=AuditSource.VOICE,
                    requested_by=AuditActor.JARVIS_AGENT,
                    path=f"skills.{slug or 'unknown'}",
                    old_value=None,
                    new_value=draft_path,
                    ok=error_kind is None,
                    rolled_back=False,
                    error=error_kind,
                    **extras,
                )
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Skill-authoring audit failed: %s", exc)
