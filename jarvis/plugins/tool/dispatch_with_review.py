"""dispatch_with_review tool: callable by the main Jarvis (Phase 8.4).

Plan reference: §6.4, §AD-6 (selective activation). This tool is the ONLY
switch that activates the quality-gate pipeline. The main Jarvis calls it
explicitly when the task justifies it — code generation, skill authoring,
file mutation, multi-step research. Conversation and small talk NEVER run
through this tool (Plan §AD-6 — self-critique paradox).

The tool description is verbatim from Plan §6.4 — it's the only heuristic
the LLM sees for activation (plan architecture).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.core.review.audit import ReviewAudit
from jarvis.core.review.checks import (
    PostCheckRunner,
    PreCheckRunner,
    output_not_empty,
    task_not_empty,
)
from jarvis.core.review.errors import HarnessUnavailable
from jarvis.core.review.pipeline import ReviewPipeline
from jarvis.core.review.spawns import ReviewerSpawner, WorkerSpawner
from jarvis.core.review.state import PipelineOutcome, PipelineResult
from jarvis.harness.manager import HarnessManager

# AD-14: voice phrases are hardcoded here (never generated, never mutable via
# voice/config) and byte-exact matched in the smoke tests. Each phrase exists
# once per supported reply language; the German wording is the original AD-14
# text and stays unchanged. The tool speaks the turn's output language
# (``ctx.config["output_language"]``, decided once per turn by
# ``jarvis/core/turn_language.py``), else the ambient answer language.
VOICE_HOLDING_PHRASES: dict[str, str] = {
    "de": "Lass mich kurz an der Aufgabe arbeiten.",  # i18n-allow: German TTS phrase
    "en": "Let me work on that task for a moment.",
    "es": "Déjame trabajar un momento en la tarea.",
    "pt": "Deixa-me trabalhar um pouco na tarefa.",
}
VOICE_OUTCOME_TEMPLATES: dict[str, dict[str, str]] = {
    "de": {
        "success": "Erledigt — {summary}",  # i18n-allow: German TTS phrase
        "cap_fired": "Mein bestes Ergebnis liegt vor, mit einer Einschränkung: {top_issue}",  # i18n-allow: German TTS phrase
        "fail": "Das funktioniert so nicht — {summary}",  # i18n-allow: German TTS phrase
        "precheck_fail": "Die Aufgabe ist zu kurz oder unklar — versuch's nochmal mit mehr Kontext.",  # i18n-allow: German TTS phrase
    },
    "en": {
        "success": "Done — {summary}",
        "cap_fired": "Here is my best result, with one caveat: {top_issue}",
        "fail": "That does not work like this — {summary}",
        "precheck_fail": "The task is too short or unclear — try again with more context.",
    },
    "es": {
        "success": "Hecho — {summary}",
        "cap_fired": "Aquí está mi mejor resultado, con una salvedad: {top_issue}",
        "fail": "Así no funciona — {summary}",
        "precheck_fail": (
            "La tarea es demasiado corta o poco clara — vuelve a intentarlo con más contexto."
        ),
    },
    "pt": {
        "success": "Feito — {summary}",
        "cap_fired": "Aqui está o meu melhor resultado, com uma ressalva: {top_issue}",
        "fail": "Assim não funciona — {summary}",
        "precheck_fail": (
            "A tarefa é demasiado curta ou pouco clara — tenta outra vez com mais contexto."
        ),
    },
}
#: Fallback fillers when the reviewer left no summary / issue to quote.
VOICE_FALLBACK_FILLERS: dict[str, dict[str, str]] = {
    "de": {
        "success": "fertig",  # i18n-allow: German TTS phrase fallback
        "cap_fired": "kleine Restbedenken",  # i18n-allow: German TTS phrase fallback
        "fail": "Architektur-Defekt",  # i18n-allow: German TTS phrase fallback
    },
    "en": {
        "success": "finished",
        "cap_fired": "a few minor remaining concerns",
        "fail": "an architectural defect",
    },
    "es": {
        "success": "terminado",
        "cap_fired": "algunas pequeñas dudas pendientes",
        "fail": "un defecto de arquitectura",
    },
    "pt": {
        "success": "concluído",
        "cap_fired": "algumas pequenas dúvidas pendentes",
        "fail": "um defeito de arquitetura",
    },
}

# Back-compat aliases for callers that imported the German-only names.
VOICE_HOLDING_PHRASE_DE = VOICE_HOLDING_PHRASES["de"]
VOICE_OUTCOME_TEMPLATES_DE = VOICE_OUTCOME_TEMPLATES["de"]


def _voice_language(ctx: Any) -> str:
    """The turn's output language for the spoken phrases (de/en/es/pt).

    ``ctx.config["output_language"]`` as the tool-use loop stamps it wins;
    without one, the ambient answer language (reply pin, else the default
    locale). This layer never re-derives a language from the task text.
    """
    config = getattr(ctx, "config", None)
    value = ""
    if isinstance(config, dict):
        value = str(config.get("output_language") or "").strip().lower()
    if not value:
        try:
            from jarvis.voice.action_phrases import resolve_ambient_language  # noqa: PLC0415

            value = resolve_ambient_language()
        except Exception:  # noqa: BLE001 — a spoken fallback beats a crash on the tool path
            value = "en"
    return value if value in VOICE_HOLDING_PHRASES else "en"


class DispatchWithReviewTool:
    """Tool wrapper over `ReviewPipeline` for the main Jarvis."""

    name: str = "dispatch_with_review"
    risk_tier: str = "monitor"
    description: str = (
        "Runs a Jarvis-Agent task with a review quality gate. "
        "USE this tool when the result is user-irreversible (code "
        "generation, file mutation, skill authoring), requires multi-step "
        "synthesis (research, aggregation), or is hard to undo. DO NOT USE "
        "for conversation, small talk, or simple tool calls (reading the "
        "calendar, checking the weather) — those go through "
        "dispatch_to_harness or run directly in the main Jarvis."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task": {
                "type": "string",
                "minLength": 20,
                "description": (
                    "Complete task description for the Jarvis-Agent. "
                    "Required, at least 20 characters."
                ),
            },
            "rubric_id": {
                "type": "string",
                "enum": [
                    "default",
                    "code_generation",
                    "skill_authoring",
                    "research",
                ],
                "default": "default",
                "description": (
                    "Which grading rubric the reviewer runs. "
                    "`default` for general tasks, `code_generation` "
                    "for code+tests, `skill_authoring` for SKILL.md generation, "
                    "`research` for fact-finding."
                ),
            },
            "max_iterations": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "default": 3,
                "description": (
                    "Maximum worker→reviewer iterations before the cap-fire "
                    "fallback (best candidate) is delivered."
                ),
            },
        },
        "required": ["task"],
        "strict": True,
    }
    # Examples from the Plan-§AD-9 pattern (Anthropic 2026): improves
    # tool-trigger accuracy + reduces schema violations.
    input_examples: list[dict[str, Any]] = [
        {
            "task": (
                "Write a Python script at scripts/rename_notes.py that "
                "renames every .md file in ~/notes to 'YYYY-MM-DD_<slug>.md', "
                "with pytest tests."
            ),
            "rubric_id": "code_generation",
        },
        {
            "task": (
                "Create a new skill that pauses Spotify when I'm talking, "
                "based on the skill-creator pattern."
            ),
            "rubric_id": "skill_authoring",
            "max_iterations": 3,
        },
    ]

    def __init__(
        self,
        *,
        bus: EventBus | None = None,
        harness_manager: HarnessManager | None = None,
        runs_root: Path | str | None = None,
        audit_log_path: Path | str | None = None,
        max_iterations: int = 3,
        hard_ceiling: int = 5,
        worker_spawner: WorkerSpawner | None = None,
        reviewer_spawner: ReviewerSpawner | None = None,
        pipeline: ReviewPipeline | None = None,
    ) -> None:
        self._bus = bus
        self._manager = harness_manager or HarnessManager(bus=bus)
        # Defaults from Plan §6.4 — override via ctor argument for tests.
        self._runs_root: Path = Path(runs_root or "data/review/runs")
        self._audit = ReviewAudit(
            path=Path(audit_log_path or "data/review.log")
        )
        self._max_iterations = max_iterations
        self._hard_ceiling = hard_ceiling

        # Spawner and pipeline are lazily built (or injected by tests).
        self._worker_spawner = worker_spawner
        self._reviewer_spawner = reviewer_spawner
        self._pipeline = pipeline

    # ------------------------------------------------------------------
    # Lazy-Construction
    # ------------------------------------------------------------------

    def _ensure_pipeline(self) -> ReviewPipeline:
        if self._pipeline is not None:
            return self._pipeline
        if self._worker_spawner is None:
            self._worker_spawner = WorkerSpawner(
                harness_manager=self._manager,
                runs_root=self._runs_root,
            )
        if self._reviewer_spawner is None:
            self._reviewer_spawner = ReviewerSpawner(
                harness_manager=self._manager,
                runs_root=self._runs_root,
            )
        self._pipeline = ReviewPipeline(
            worker_spawn=self._worker_spawner.spawn,
            reviewer_spawn=self._reviewer_spawner.spawn,
            prechecks=PreCheckRunner([task_not_empty]),
            postchecks=PostCheckRunner([output_not_empty]),
            audit=self._audit,
            max_iterations=self._max_iterations,
            hard_ceiling=self._hard_ceiling,
        )
        return self._pipeline

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:
        language = _voice_language(ctx)
        task = (args.get("task") or "").strip()
        rubric_id = args.get("rubric_id") or "default"
        # max_iterations as a per-run override; otherwise the ctor default.
        max_iter_arg = args.get("max_iterations")

        if not task or len(task) < 20:
            return ToolResult(
                success=False,
                output=None,
                error="task is missing or too short (at least 20 characters)",
            )
        if rubric_id not in {
            "default",
            "code_generation",
            "skill_authoring",
            "research",
        }:
            return ToolResult(
                success=False,
                output=None,
                error=f"unknown rubric_id: {rubric_id!r}",
            )

        pipeline = self._ensure_pipeline()
        # AD-14: holding phrase ONCE per run — before the await pipeline.run().
        # Bus publish is best-effort; if no bus is injected, no side effect.
        await self._announce_holding_phrase(language)
        try:
            result = await pipeline.run(
                task,
                rubric_id=rubric_id,
                max_iterations=int(max_iter_arg) if max_iter_arg else None,
            )
        except HarnessUnavailable:
            # AP-23 wave-2 finding 5: no registered harness backs the
            # worker/reviewer spawn on this install (e.g. every install
            # today — Welle-4 removed the old Jarvis-Agents subprocess bridge).
            # Honest, install-neutral message — never the raw KeyError from
            # HarnessManager.get(), never the dead internal "openclaw" name.
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "review gate unavailable: no worker harness is "
                    "registered on this install"
                ),
            )
        except Exception as exc:  # noqa: BLE001 — the tool must NEVER crash
            return ToolResult(
                success=False,
                output=None,
                error=f"{type(exc).__name__}: {exc}",
            )

        return self._serialize_result(result, language)

    async def _announce_holding_phrase(self, language: str = "en") -> None:
        """AD-14: publish the voice holding phrase once per run.

        Bus publish never fails hard — if no bus is injected, it's a no-op.
        If the bus crashes on publish, we swallow it (the pipeline must NOT
        abort because of a TTS-bus error).
        """
        if self._bus is None:
            return
        try:
            await self._bus.publish(
                AnnouncementRequested(
                    text=VOICE_HOLDING_PHRASES.get(language, VOICE_HOLDING_PHRASES["en"]),
                    priority="normal",
                    language=language,
                )
            )
        except Exception:  # noqa: BLE001, S110 — the voice side effect must not crash
            pass

    # ------------------------------------------------------------------
    # Result-Serialization
    # ------------------------------------------------------------------

    def _serialize_result(
        self, result: PipelineResult, language: str = "en"
    ) -> ToolResult:
        # `success` includes cap-fire (best-of) — the user gets a result.
        # PRECHECK_FAIL and FAIL are hard errors.
        success = result.outcome in (
            PipelineOutcome.SUCCESS,
            PipelineOutcome.CAP_FIRED,
        )

        warnings: list[str] = []
        if result.cap_fired and result.final_verdict is not None:
            # Top issue for TTS display (Plan §AD-7)
            warnings.append(result.final_verdict.summary)
            for issue in result.final_verdict.issues[:3]:  # max 3
                warnings.append(f"[{issue.severity}] {issue.description}")

        # AD-14: voice outcome phrase in the output. The brain renders this
        # as TTS after the tool call has completed.
        voice_phrase = self._build_voice_outcome_phrase(result, language)

        output: dict[str, Any] = {
            "run_id": result.run_id,
            "outcome": result.outcome.value,
            "cap_fired": result.cap_fired,
            "iterations_total": len(result.iterations),
            "final_artifact": (
                result.final_artifact[:4000]
                if result.final_artifact
                else None
            ),
            "final_artifact_truncated": (
                result.final_artifact is not None
                and len(result.final_artifact) > 4000
            ),
            "final_verdict": (
                {
                    "status": result.final_verdict.status.value,
                    "summary": result.final_verdict.summary,
                    "score": result.final_verdict.score,
                    "issue_count": len(result.final_verdict.issues),
                }
                if result.final_verdict is not None
                else None
            ),
            "warnings": warnings,
            "voice_completion_phrase": voice_phrase,
        }
        if result.precheck_failure is not None:
            failed = result.precheck_failure.failed
            output["precheck_failure"] = {
                "name": failed.name if failed else None,
                "message": failed.message if failed else None,
            }

        error: str | None = None
        if result.outcome is PipelineOutcome.PRECHECK_FAIL:
            error = "pre-check failed"
        elif result.outcome is PipelineOutcome.FAIL:
            error = "reviewer returned status=fail (architectural defect)"

        return ToolResult(success=success, output=output, error=error)

    @staticmethod
    def _build_voice_outcome_phrase(
        result: PipelineResult, language: str = "en"
    ) -> str:
        """AD-14: outcome phrase based on PipelineOutcome, in ``language``.

        Templates are hardcoded (Plan-§Forbidden: no mutation via
        voice/config). Tests match byte-exact on the phrase prefixes.
        """
        templates = VOICE_OUTCOME_TEMPLATES.get(language, VOICE_OUTCOME_TEMPLATES["en"])
        fillers = VOICE_FALLBACK_FILLERS.get(language, VOICE_FALLBACK_FILLERS["en"])
        outcome = result.outcome
        if outcome is PipelineOutcome.SUCCESS:
            summary = (
                result.final_verdict.summary
                if result.final_verdict is not None
                else fillers["success"]
            )
            return templates["success"].format(summary=summary)
        if outcome is PipelineOutcome.CAP_FIRED:
            top_issue = fillers["cap_fired"]
            if result.final_verdict is not None:
                if result.final_verdict.issues:
                    top_issue = result.final_verdict.issues[0].description
                else:
                    top_issue = result.final_verdict.summary
            return templates["cap_fired"].format(top_issue=top_issue)
        if outcome is PipelineOutcome.FAIL:
            summary = (
                result.final_verdict.summary
                if result.final_verdict is not None
                else fillers["fail"]
            )
            return templates["fail"].format(summary=summary)
        # PRECHECK_FAIL
        return templates["precheck_fail"]
