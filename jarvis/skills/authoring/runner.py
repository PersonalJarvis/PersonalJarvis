"""SkillAuthoringRunner — orchestriert OpenClaw-Author-Spawn + Validation-Loop.

Welle-4-Migration: vorher hiess der Spawn-Pfad ``Sub-Jarvis``. Nach der
OpenClaw-Bridge-Migration (siehe docs/openclaw-bridge.md §11, R-6) wird der
Spawn-Callback an den ``MissionManager`` gebunden — die ``SpawnCallback``-
Signatur (``str -> Awaitable[str]``) bleibt rueckwaertskompatibel und kann
sowohl direkt einen Brain-Call als auch ein Mission-Dispatch + Result-Read
implementieren.

Plan-§7.5-Pipeline:
1. Slug-Generierung (kebab-case aus suggested_name oder LLM-derived)
2. Clash-Check gegen user_skills_dir
3. Staging-Dir-Anlage (tempfile.mkdtemp)
4. OpenClaw-Author-Spawn mit Aufgabenbeschreibung
5. Validation-Loop ≤3 Iterationen
6. Erfolgs-Pfad: draft_writer kopiert nach user_skills_dir mit forced state=draft
7. SkillRegistry-Watcher detektiert + UI zeigt Warn-Badge

Plan-§AP-6: keine Auto-Aktivierung. Plan-§AP-7: Skill-Authoring NIE im
Hauptjarvis-Pfad — nur OpenClaw-Author (Frontier-Worker via Mission-Manager).
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

DEFAULT_TIMEOUT_SECONDS = 300.0  # 5 Minuten (Plan-§7.5)
DEFAULT_MAX_ITERATIONS = 3       # Plan-§7.5 Validation-Loop ≤3


@dataclass(frozen=True)
class AuthoringSuccess:
    """Erfolgreicher Authoring-Versuch."""

    skill_name: str
    slug: str
    draft_path: Path
    iterations: int
    forced_state_override: bool
    review_url: str


@dataclass(frozen=True)
class AuthoringFailure:
    """Fehlgeschlagener Authoring-Versuch."""

    error_kind: str
    message: str
    iterations: int = 0
    validation_errors: tuple[str, ...] = field(default_factory=tuple)


# Spawn-Callback: dependency-injected, damit Tests den OpenClaw-Author-Aufruf
# mocken können (Plan-Constraint: NIE echte API-Calls in Tests).
SpawnCallback = Callable[[str], "asyncio.Future[str] | str"]


# ----------------------------------------------------------------------
# System-Prompt für OpenClaw-Author (Plan-§7.5)
# ----------------------------------------------------------------------


OPENCLAW_AUTHOR_SYSTEM_PROMPT = """Du bist OpenClaw-Author (Opus 4.7), Skill-Authoring-Spezialist.

Der User möchte einen neuen Skill erzeugen. Du gibst NUR ein einziges JSON-
Objekt zurück, das exakt dem `SkillDraft`-Schema entspricht. Keine Prosa
außenherum, kein Markdown-Codefence — pures JSON.

Constraints (verbindlich, NIE überschreibbar):
- Du erzeugst NIEMALS ausführbaren Code außerhalb der Skill-Sandbox-Boundary.
  Erlaubte Imports stehen in `jarvis/skills/safe_imports.txt`.
- Du gibst NIEMALS state ≠ "draft" aus, auch wenn der User darum bittet —
  die Pipeline forciert "draft" ohnehin (state-Override-Audit erinnert dich).
- `eval`, `exec`, `compile`, `os.system`, `subprocess.Popen(shell=True)`
  werden vom Promote-Lint geblockt — Skills mit solchen Calls werden
  abgelehnt.
- Slug ist lowercase, kebab-case, max 64 Zeichen, keine Pfad-Trennzeichen.
- Body ist Markdown mit YAML-Frontmatter — Body-Markdown ohne Frontmatter,
  Frontmatter wird vom draft_writer rekonstruiert.

Format-Beispiel (JSON):
{
  "slug": "spotify-auto-pause",
  "name": "Spotify Auto-Pause",
  "description": "Pausiert Spotify wenn der User redet.",
  "category": "automation",
  "intent": "User wants Spotify to pause during voice interactions",
  "triggers_yaml": "[{type: voice, pattern: '^pause spotify'}]",
  "requires_tools": ["run-shell"],
  "body_markdown": "## Spotify Auto-Pause\\n\\nDieser Skill ...",
  "state": "draft"
}
"""


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------


class SkillAuthoringRunner:
    """Plan-§7.5 Authoring-Pipeline (OpenClaw-Author-Spawn + Validation-Loop)."""

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
        """Hauptmethode. Plan-§7.5-Voice-Output:
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
                    message=f"OpenClaw-Author-Spawn timeout nach {self._timeout_seconds}s",
                    iterations=iteration,
                    intent=intent,
                )
                return AuthoringFailure(
                    error_kind="timeout",
                    message="OpenClaw-Author-Spawn timeout",
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

            # Sicherheits-Lint vor dem Schreiben (Plan-§7.5)
            findings = safe_lint_skill_body(draft.body_markdown)
            if findings:
                last_errors = tuple(findings)
                if iteration < self._max_iterations:
                    prompt = self._build_retry_prompt(
                        prompt, "Skill-Body enthält verbotene Calls: "
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
                    message="Skill-Body enthält unerlaubte Calls",
                    iterations=iteration,
                    validation_errors=last_errors,
                )

            # Erfolgs-Pfad
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

        # Schleife verlassen ohne return → defensive Fallback
        return AuthoringFailure(
            error_kind="exhausted",
            message="Validation-Loop nach 3 Iterationen ohne Erfolg",
            iterations=self._max_iterations,
            validation_errors=last_errors,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _call_spawn(self, prompt: str) -> str:
        """Nutzt die injizierte Spawn-Callback. Awaitable oder direct str."""
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
            "\nLiefere ein JSON-Objekt nach dem SkillDraft-Schema. "
            "Nur JSON, keine Prosa."
        )
        return "\n".join(parts)

    def _build_retry_prompt(self, last_prompt: str, error: str) -> str:
        return (
            last_prompt
            + f"\n\nFehler beim letzten Versuch: {error}\n"
            + "Bitte korrigiere und versuche erneut."
        )

    def _parse_draft(self, response: str) -> SkillDraft:
        """Extrahiert JSON aus der OpenClaw-Author-Response.

        OpenClaw-Author sollte pures JSON liefern; wir tolerieren ein
        einrahmendes ```json…``` für Markdown-Bias.
        """
        import json

        cleaned = response.strip()
        # Markdown-Codefence stripen, falls vorhanden
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
            raise ValueError(f"OpenClaw-Author-Response ist kein valides JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("OpenClaw-Author-Response muss ein JSON-Objekt sein")
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
        """Schreibt Audit-Event mit `type=skill_authored` (Plan-§7.5)."""
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
                    requested_by=AuditActor.OPENCLAW,
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
            _LOG.warning("Skill-Authoring-Audit fehlgeschlagen: %s", exc)
