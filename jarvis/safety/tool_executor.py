"""ToolExecutor: orchestriert Risk-Eval → Plausibility → Approval → Execute → Event-Log.

Einziger autorisierter Einstiegspunkt für Tool-Calls. Wer `Tool.execute()`
direkt aufruft umgeht Safety — das ist ein Bug.

Phase 4 (Persona-Mandat): Vor jeder Approval-Entscheidung laeuft ein
Plausibilitaets-Check. Wenn die Voice-Pipeline einen ``plausibility_context_fn``
registriert hat, holt der Executor sich Transcript-Confidence + Wake-Age
und entscheidet, ob bei ``ask``-/``monitor``-Tools eine zusaetzliche
Confirmation noetig ist (siehe ``jarvis.brain.plausibility``).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from jarvis.core.bus import EventBus
from jarvis.core.events import ActionDenied, ActionExecuted, ActionProposed
from jarvis.core.protocols import ExecutionContext, Tool, ToolResult, Transcript
from jarvis.core.redact import safe_preview

from .approval import ApprovalWorkflow
from .risk_tier import ActionBlocked, RiskTierEvaluator

if TYPE_CHECKING:
    from jarvis.brain.plausibility import PlausibilityDecision
    from jarvis.core.config import BrainPlausibilityConfig


log = logging.getLogger(__name__)


# Sentinel returned (as ``ToolResult.error``) when a confirmation-requiring tool
# is invoked on a CONVERSATIONAL turn (``config_snapshot["voice_confirm"]``).
# Instead of blocking in ``ApprovalWorkflow.wait()`` for a UI approval no voice/
# chat user can give (which is then beheaded by the 20 s no-first-frame ceiling →
# the misleading "took too long" phrase, forensic 2026-06-18), the executor stashes
# the action and returns this sentinel so the brain SPEAKS a confirmation question
# and ends the turn. The next "ja" re-runs the action via ``execute_confirmed``.
VOICE_CONFIRM_SENTINEL = "__voice_confirm_required__"


# ``plausibility_context_fn`` returns (Transcript | None, wake_age_s | None).
# Die Voice-Pipeline registriert einen Provider, der den letzten User-Turn-
# Transcript und die Sekunden seit dem letzten Wake-Trigger liefert. Bei
# ``None``-Returns laeuft der Executor wie bisher (kein Plausibility-Check).
PlausibilityContextFn = Callable[[], "tuple[Transcript | None, float | None]"]


class ToolExecutor:
    """Pipeline: evaluate → (plausibility) → (approve) → execute → log."""

    def __init__(
        self,
        bus: EventBus,
        evaluator: RiskTierEvaluator,
        approval: ApprovalWorkflow,
        *,
        default_timeout_s: float = 60.0,
        plausibility_config: "BrainPlausibilityConfig | None" = None,
        plausibility_context_fn: PlausibilityContextFn | None = None,
    ) -> None:
        self._bus = bus
        self._evaluator = evaluator
        self._approval = approval
        self._default_timeout_s = default_timeout_s
        self._plausibility_config = plausibility_config
        self._plausibility_context_fn = plausibility_context_fn
        # Two-turn voice/chat confirmation: actions deferred by ``execute`` on a
        # conversational turn, keyed by trace_id, awaiting an ``execute_confirmed``
        # (user said "ja") or ``cancel_pending`` (user said "nein"). The tool +
        # args live here OUT-OF-BAND — never in the serialized ToolResult.output.
        self._pending_voice: dict[UUID, tuple[Tool, dict[str, Any]]] = {}

    def set_plausibility_context_fn(
        self, fn: PlausibilityContextFn | None,
    ) -> None:
        """Spaete Registrierung des Plausibility-Context-Providers.

        Die Voice-Pipeline ruft das nach ihrem ``run()``-Setup auf, weil der
        ToolExecutor frueher in der Bootstrap-Reihenfolge gebaut wird als
        die Pipeline. Idempotent — ``None`` setzt den Hook zurueck.
        """
        self._plausibility_context_fn = fn

    def _evaluate_plausibility(
        self,
        tool: Tool,
        decision: Any,
    ) -> "PlausibilityDecision | None":
        """Holt den aktuellen Plausibility-Context und prueft.

        Returns ``None`` wenn kein Context-Provider registriert ist oder
        das Tool durch Whitelist auf ``safe`` heruntergesetzt wurde —
        Whitelist-Logik ist heilig (Mandat: "Whitelist-downgraded Tools
        laufen weiter ohne Plausibility-Check").
        """
        if self._plausibility_context_fn is None:
            return None
        # Whitelist-Downgrade: Plausibility uebergehen.
        if decision.approved_by == "whitelist":
            return None
        try:
            transcript, wake_age = self._plausibility_context_fn()
        except Exception as exc:  # noqa: BLE001
            log.debug("plausibility_context_fn failed: %s", exc)
            return None
        from jarvis.brain.plausibility import check_plausibility

        return check_plausibility(
            tool_name=tool.name,
            risk_tier=decision.tier,
            transcript=transcript,
            wake_age_s=wake_age,
            config=self._plausibility_config,
        )

    async def execute(
        self,
        tool: Tool,
        args: dict[str, Any],
        *,
        user_utterance: str = "",
        config_snapshot: dict[str, Any] | None = None,
        memory_read: Any | None = None,
        trace_id: UUID | None = None,
        rationale: str = "",
    ) -> ToolResult:
        tid = trace_id or uuid4()
        t_start = time.perf_counter()

        # 1. Evaluate
        try:
            decision = self._evaluator.evaluate(tool, args)
        except ActionBlocked as exc:
            await self._bus.publish(ActionDenied(
                trace_id=tid,
                tool_name=tool.name,
                reason=f"blacklist: {exc.pattern}",
            ))
            return ToolResult(success=False, output=None, error=str(exc))

        # 2. Proposed-Event (UI kann das als Live-Indikator nutzen). The brain's
        # rationale rides along for the Session-Decision-Log — redacted + capped
        # here so no raw secret reaches the bus / session DB / local diary.
        await self._bus.publish(ActionProposed(
            trace_id=tid,
            tool_name=tool.name,
            args=args,
            risk_tier=decision.tier,
            rationale=safe_preview(rationale),
        ))

        # 2.5 Plausibility-Check (Phase 4): zwischen Tier-Decision und
        # Approval. Ergebnis kann ``require_confirmation`` erzwingen, auch
        # wenn der Tier-Workflow das nicht vorsieht (z.B. bei ``monitor``).
        plaus = self._evaluate_plausibility(tool, decision)
        if plaus is not None and plaus.reason != "ok":
            log.info(
                "Plausibility[%s]: tier=%s reason=%s require_confirm=%s",
                tool.name, decision.tier, plaus.reason, plaus.require_confirmation,
            )

        # 3. Approval (nur wenn der Tier-Workflow ODER Plausibility es will)
        approved_by = decision.approved_by or "auto"
        needs_confirm = self._evaluator.needs_user_confirmation(decision) or (
            plaus is not None and plaus.require_confirmation
        )
        if needs_confirm:
            # Two-turn confirmation on a conversational turn: do NOT block on the
            # UI-approval future (no voice/chat user can resolve it within the
            # turn's latency window). Stash the action and return the sentinel so
            # the brain speaks a confirmation question; the user's next "ja" calls
            # ``execute_confirmed`` (AD-OE: the talker never awaits heavy/blocking
            # work on the turn). ``needs_confirm`` already excludes whitelist
            # downgrades, so this fires only for genuinely consequential tools.
            if bool((config_snapshot or {}).get("voice_confirm")):
                self._pending_voice[tid] = (tool, dict(args))
                log.info(
                    "voice-confirm: deferring %s (tier=%s) for two-turn confirmation",
                    tool.name, decision.tier,
                )
                return ToolResult(
                    success=False,
                    output={
                        "tool_name": tool.name,
                        "trace_id": str(tid),
                        "risk_tier": decision.tier,
                    },
                    error=VOICE_CONFIRM_SENTINEL,
                )
            approved, who_or_reason = await self._approval.wait(tid, self._default_timeout_s)
            if not approved:
                await self._bus.publish(ActionDenied(
                    trace_id=tid,
                    tool_name=tool.name,
                    reason=who_or_reason,
                ))
                return ToolResult(success=False, output=None, error=f"approval-denied ({who_or_reason})")
            approved_by = who_or_reason  # "user" oder "auto"

        # 4. Execute
        ctx = ExecutionContext(
            trace_id=tid,
            user_utterance=user_utterance,
            config=config_snapshot or {},
            memory_read=memory_read,
            approved_by=approved_by,
        )
        try:
            result = await tool.execute(args, ctx)
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - t_start) * 1000)
            await self._bus.publish(ActionExecuted(
                trace_id=tid,
                tool_name=tool.name,
                success=False,
                duration_ms=duration_ms,
                error=str(exc),
            ))
            return ToolResult(success=False, output=None, error=str(exc))

        duration_ms = int((time.perf_counter() - t_start) * 1000)
        await self._bus.publish(ActionExecuted(
            trace_id=tid,
            tool_name=tool.name,
            success=result.success,
            duration_ms=duration_ms,
            error=result.error,
            output_preview=safe_preview(result.output),
        ))
        return result

    # ------------------------------------------------------------------
    # Two-turn voice/chat confirmation resume (turn N+1)
    # ------------------------------------------------------------------

    def has_pending_voice_confirm(self, trace_id: UUID) -> bool:
        """True while an action deferred for ``trace_id`` still awaits a yes/no."""
        return trace_id in self._pending_voice

    async def execute_confirmed(
        self,
        trace_id: UUID,
        *,
        user_utterance: str = "",
        config_snapshot: dict[str, Any] | None = None,
        memory_read: Any | None = None,
    ) -> ToolResult:
        """Run the action stashed by a prior voice-confirm deferral ("ja").

        Single-use: the pending entry is popped first, so a repeated "ja" cannot
        double-fire the side effect. ``approved_by="user"`` records that the human
        authorized it. Publishes ``ActionExecuted`` for the audit trail, mirroring
        the normal execute path.
        """
        pending = self._pending_voice.pop(trace_id, None)
        if pending is None:
            return ToolResult(
                success=False,
                output=None,
                error="voice-confirm expired (no pending action for this turn)",
            )
        tool, args = pending
        ctx = ExecutionContext(
            trace_id=trace_id,
            user_utterance=user_utterance,
            config=config_snapshot or {},
            memory_read=memory_read,
            approved_by="user",
        )
        t_start = time.perf_counter()
        try:
            result = await tool.execute(args, ctx)
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - t_start) * 1000)
            await self._bus.publish(ActionExecuted(
                trace_id=trace_id,
                tool_name=tool.name,
                success=False,
                duration_ms=duration_ms,
                error=str(exc),
            ))
            return ToolResult(success=False, output=None, error=str(exc))
        duration_ms = int((time.perf_counter() - t_start) * 1000)
        await self._bus.publish(ActionExecuted(
            trace_id=trace_id,
            tool_name=tool.name,
            success=result.success,
            duration_ms=duration_ms,
            error=result.error,
            output_preview=safe_preview(result.output),
        ))
        return result

    async def cancel_pending(self, trace_id: UUID) -> bool:
        """Drop the action stashed for ``trace_id`` ("nein"). Returns whether one
        existed. Publishes ``ActionDenied`` for the audit trail."""
        pending = self._pending_voice.pop(trace_id, None)
        if pending is None:
            return False
        tool, _args = pending
        await self._bus.publish(ActionDenied(
            trace_id=trace_id,
            tool_name=tool.name,
            reason="voice_vetoed",
        ))
        return True
