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

from .approval import ApprovalWorkflow
from .risk_tier import ActionBlocked, RiskTierEvaluator

if TYPE_CHECKING:
    from jarvis.brain.plausibility import PlausibilityDecision
    from jarvis.core.config import BrainPlausibilityConfig


log = logging.getLogger(__name__)


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

        # 2. Proposed-Event (UI kann das als Live-Indikator nutzen)
        await self._bus.publish(ActionProposed(
            trace_id=tid,
            tool_name=tool.name,
            args=args,
            risk_tier=decision.tier,
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
        ))
        return result
