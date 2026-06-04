"""dispatch_with_review-Tool: vom Hauptjarvis aufrufbar (Phase 8.4).

Plan-Referenz: §6.4, §AD-6 (selektive Aktivierung). Dieses Tool ist die
einzige Schaltstelle, die die Quality-Gate-Pipeline aktiviert. Hauptjarvis
ruft es explizit auf, wenn die Aufgabe es rechtfertigt — Code-Generierung,
Skill-Authoring, Datei-Mutation, Multi-Schritt-Research. Konversation und
Smalltalk laufen NIE durch dieses Tool (Plan §AD-6 — Self-Critique-
Paradox).

Tool-Beschreibung ist 1:1 aus Plan §6.4 — sie ist die einzige Heuristik,
die das LLM zur Aktivierung sieht (Plan-Architektur).
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
from jarvis.core.review.pipeline import ReviewPipeline
from jarvis.core.review.spawns import ReviewerSpawner, WorkerSpawner
from jarvis.core.review.state import PipelineOutcome, PipelineResult
from jarvis.harness.manager import HarnessManager

# AD-14: Voice-Phrasen sind hier hartcodiert + werden in den Smoke-Tests
# byte-genau matched. Änderungen erfordern Plan-Update.
VOICE_HOLDING_PHRASE_DE = "Lass mich kurz an der Aufgabe arbeiten."
VOICE_OUTCOME_TEMPLATES_DE = {
    "success": "Erledigt — {summary}",
    "cap_fired": "Mein bestes Ergebnis liegt vor, mit einer Einschränkung: {top_issue}",
    "fail": "Das funktioniert so nicht — {summary}",
    "precheck_fail": "Die Aufgabe ist zu kurz oder unklar — versuch's nochmal mit mehr Kontext.",
}


class DispatchWithReviewTool:
    """Tool-Wrapper über `ReviewPipeline` für den Hauptjarvis."""

    name: str = "dispatch_with_review"
    risk_tier: str = "monitor"
    description: str = (
        "Führt eine OpenClaw-Aufgabe mit Review-Quality-Gate aus. "
        "NUTZE diesen Tool wenn das Ergebnis user-irreversibel ist (Code-"
        "Generierung, Datei-Mutation, Skill-Authoring), Multi-Schritt-"
        "Synthese verlangt (Research, Aggregation), oder schwer rückgängig "
        "zu machen ist. NUTZE NICHT für Konversation, Smalltalk, einfache "
        "Tool-Calls (Calendar lesen, Wetter abfragen) — diese laufen über "
        "dispatch_to_harness oder direkt im Hauptjarvis."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task": {
                "type": "string",
                "minLength": 20,
                "description": (
                    "Vollständige Task-Beschreibung für OpenClaw. "
                    "Zwingend, mindestens 20 Zeichen."
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
                    "Welche Bewertungs-Rubric der Reviewer durchläuft. "
                    "`default` für allgemeine Aufgaben, `code_generation` "
                    "für Code+Tests, `skill_authoring` für SKILL.md-Generierung, "
                    "`research` für Faktenrecherche."
                ),
            },
            "max_iterations": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "default": 3,
                "description": (
                    "Maximale Worker→Reviewer-Iterationen bevor Cap-Fire-"
                    "Fallback (best candidate) ausgeliefert wird."
                ),
            },
        },
        "required": ["task"],
        "strict": True,
    }
    # Beispiele aus Plan-§AD-9-Pattern (Anthropic 2026): erhöht Tool-Trigger-
    # Genauigkeit + reduziert Schema-Verletzungen.
    input_examples: list[dict[str, Any]] = [
        {
            "task": (
                "Schreibe ein Python-Script in scripts/rename_notes.py das "
                "alle .md-Files in ~/notes nach 'YYYY-MM-DD_<slug>.md' "
                "umbenennt, mit pytest-Tests."
            ),
            "rubric_id": "code_generation",
        },
        {
            "task": (
                "Erstelle einen neuen Skill der Spotify pausiert wenn ich "
                "rede, basierend auf dem Skill-Creator-Pattern."
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
        # Defaults aus Plan §6.4 — Override per ctor-Argument für Tests.
        self._runs_root: Path = Path(runs_root or "data/review/runs")
        self._audit = ReviewAudit(
            path=Path(audit_log_path or "data/review.log")
        )
        self._max_iterations = max_iterations
        self._hard_ceiling = hard_ceiling

        # Spawner und Pipeline werden lazy gebaut (oder durch Tests injiziert).
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
        del ctx  # ExecutionContext aktuell nicht benötigt (Phase-8.4-Scope)
        task = (args.get("task") or "").strip()
        rubric_id = args.get("rubric_id") or "default"
        # max_iterations als Per-Run-Override; sonst Default aus ctor.
        max_iter_arg = args.get("max_iterations")

        if not task or len(task) < 20:
            return ToolResult(
                success=False,
                output=None,
                error="task fehlt oder ist zu kurz (mindestens 20 Zeichen)",
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
                error=f"unbekannte rubric_id: {rubric_id!r}",
            )

        pipeline = self._ensure_pipeline()
        # AD-14: Holding-Phrase EINMAL pro Run — vor dem await pipeline.run().
        # Bus-Publish ist Best-Effort; falls kein Bus injiziert, kein Side-Effect.
        await self._announce_holding_phrase()
        try:
            result = await pipeline.run(
                task,
                rubric_id=rubric_id,
                max_iterations=int(max_iter_arg) if max_iter_arg else None,
            )
        except Exception as exc:  # noqa: BLE001 — Tool darf NIE crashen
            return ToolResult(
                success=False,
                output=None,
                error=f"{type(exc).__name__}: {exc}",
            )

        return self._serialize_result(result)

    async def _announce_holding_phrase(self) -> None:
        """AD-14: Voice-Holding-Phrase einmal pro Run publishen.

        Bus-Publish failt nie hart — wenn kein Bus injiziert, no-op. Wenn
        Bus crash't beim publish, schlucken wir das (Pipeline darf NICHT
        wegen TTS-Bus-Fehler abbrechen).
        """
        if self._bus is None:
            return
        try:
            await self._bus.publish(
                AnnouncementRequested(
                    text=VOICE_HOLDING_PHRASE_DE,
                    priority="normal",
                    language="de",
                )
            )
        except Exception:  # noqa: BLE001, S110 — Voice-Side-Effect darf nicht crashen
            pass

    # ------------------------------------------------------------------
    # Result-Serialization
    # ------------------------------------------------------------------

    def _serialize_result(self, result: PipelineResult) -> ToolResult:
        # `success` umfasst Cap-Fire (best-of) — der User bekommt ein Ergebnis.
        # PRECHECK_FAIL und FAIL sind harte Fehler.
        success = result.outcome in (
            PipelineOutcome.SUCCESS,
            PipelineOutcome.CAP_FIRED,
        )

        warnings: list[str] = []
        if result.cap_fired and result.final_verdict is not None:
            # Top-Issue für TTS-Anzeige (Plan §AD-7)
            warnings.append(result.final_verdict.summary)
            for issue in result.final_verdict.issues[:3]:  # max 3
                warnings.append(f"[{issue.severity}] {issue.description}")

        # AD-14: Voice-Outcome-Phrase im Output. Brain rendert das als TTS,
        # nachdem der Tool-Call abgeschlossen ist.
        voice_phrase = self._build_voice_outcome_phrase(result)

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
    def _build_voice_outcome_phrase(result: PipelineResult) -> str:
        """AD-14: Outcome-Phrase basierend auf PipelineOutcome.

        Templates sind hartcodiert (Plan-§Verboten: keine Mutation via
        Voice/Config). Tests matchen byte-genau auf die Phrase-Anfänge.
        """
        outcome = result.outcome
        if outcome is PipelineOutcome.SUCCESS:
            summary = (
                result.final_verdict.summary
                if result.final_verdict is not None
                else "fertig"
            )
            return VOICE_OUTCOME_TEMPLATES_DE["success"].format(summary=summary)
        if outcome is PipelineOutcome.CAP_FIRED:
            top_issue = "kleine Restbedenken"
            if result.final_verdict is not None:
                if result.final_verdict.issues:
                    top_issue = result.final_verdict.issues[0].description
                else:
                    top_issue = result.final_verdict.summary
            return VOICE_OUTCOME_TEMPLATES_DE["cap_fired"].format(top_issue=top_issue)
        if outcome is PipelineOutcome.FAIL:
            summary = (
                result.final_verdict.summary
                if result.final_verdict is not None
                else "Architektur-Defekt"
            )
            return VOICE_OUTCOME_TEMPLATES_DE["fail"].format(summary=summary)
        # PRECHECK_FAIL
        return VOICE_OUTCOME_TEMPLATES_DE["precheck_fail"]
