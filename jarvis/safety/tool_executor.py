"""ToolExecutor: orchestrates risk-eval → plausibility → approval → execute → event log.

The only authorized entry point for tool calls. Calling `Tool.execute()`
directly bypasses safety — that is a bug.

Phase 4 (persona mandate): a plausibility check runs before every approval
decision. If the voice pipeline has registered a ``plausibility_context_fn``,
the executor fetches transcript confidence + wake age and decides whether
``ask``/``monitor`` tools need an extra confirmation (see
``jarvis.brain.plausibility``).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ActionApprovalRequired,
    ActionDenied,
    ActionExecuted,
    ActionProposed,
)
from jarvis.core.protocols import (
    CancelToken,
    ExecutionContext,
    Tool,
    ToolResult,
    Transcript,
)
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
# The voice pipeline registers a provider that supplies the last user-turn
# transcript and the seconds since the last wake trigger. On
# ``None`` returns the executor behaves as before (no plausibility check).
PlausibilityContextFn = Callable[[], "tuple[Transcript | None, float | None]"]


def _optional_string(value: Any) -> str | None:
    """Normalize optional correlation metadata without inventing identifiers."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


class ToolExecutor:
    """Pipeline: evaluate → (plausibility) → (approve) → execute → log."""

    def __init__(
        self,
        bus: EventBus,
        evaluator: RiskTierEvaluator,
        approval: ApprovalWorkflow,
        *,
        default_timeout_s: float = 60.0,
        plausibility_config: BrainPlausibilityConfig | None = None,
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
        """Late registration of the plausibility-context provider.

        The voice pipeline calls this after its own ``run()`` setup, because
        the ToolExecutor is built earlier in the bootstrap order than
        the pipeline. Idempotent — ``None`` resets the hook.
        """
        self._plausibility_context_fn = fn

    def _evaluate_plausibility(
        self,
        tool: Tool,
        decision: Any,
    ) -> PlausibilityDecision | None:
        """Fetches the current plausibility context and checks it.

        Returns ``None`` if no context provider is registered, or the
        tool was downgraded to ``safe`` via whitelist — whitelist logic
        is sacred (mandate: "whitelist-downgraded tools keep running
        without a plausibility check").
        """
        if self._plausibility_context_fn is None:
            return None
        # Whitelist downgrade: skip plausibility.
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

    async def publish_guard_denied(
        self,
        tool_name: str,
        reason: str,
        *,
        trace_id: UUID | None = None,
    ) -> None:
        """Surface a tool call refused BEFORE reaching :meth:`execute`.

        The tool-use loop's deterministic guards (how-to question, research
        intent, STT hallucination, unknown tool name, …) block a call without
        ever entering the executor — so no ActionProposed/ActionExecuted event
        fires and the session timeline shows NO trace of why a turn refused
        (2026-07-06 audit: the unknown-tool 'run-shell' incident left zero
        events). Publishing the same ``ActionDenied`` the blacklist path uses
        makes the refusal visible; the recorder already subscribes to it.
        Best-effort: observability must never break tool execution.
        """
        try:
            await self._bus.publish(ActionDenied(
                trace_id=trace_id or uuid4(),
                tool_name=tool_name,
                reason=reason,
            ))
        except Exception:  # noqa: BLE001
            log.debug("publish_guard_denied failed", exc_info=True)

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
        cancel_token: CancelToken | None = None,
    ) -> ToolResult:
        tid = trace_id or uuid4()
        t_start = time.perf_counter()

        if cancel_token is not None and cancel_token.is_cancelled():
            return ToolResult(
                success=False,
                output=None,
                error=f"cancelled ({cancel_token.reason or 'requested'})",
            )

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

        # 2. Plausibility check (Phase 4): the result can force confirmation
        # even when the tier workflow does not (for example, ``monitor``).
        plaus = self._evaluate_plausibility(tool, decision)
        if plaus is not None and plaus.reason != "ok":
            log.info(
                "Plausibility[%s]: tier=%s reason=%s require_confirm=%s",
                tool.name, decision.tier, plaus.reason, plaus.require_confirmation,
            )

        # 3. Arm approval BEFORE publishing ActionProposed. Subscribers such as
        # TaskAutoApprover may answer synchronously from that event; registering
        # afterward loses the answer and turns a valid grant into a timeout.
        approved_by = decision.approved_by or "auto"
        needs_confirm = self._evaluator.needs_user_confirmation(decision) or (
            plaus is not None and plaus.require_confirmation
        )
        voice_confirm = bool((config_snapshot or {}).get("voice_confirm"))
        approval_ticket = None
        if needs_confirm and not voice_confirm:
            approval_ticket = self._approval.arm(tid)

        # 3.5 Proposed event (the UI can use this as a live indicator). The
        # rationale is redacted and capped so no raw secret reaches the bus.
        try:
            await self._bus.publish(ActionProposed(
                trace_id=tid,
                tool_name=tool.name,
                args=args,
                risk_tier=decision.tier,
                rationale=safe_preview(rationale),
            ))
        except BaseException:
            if approval_ticket is not None:
                approval_ticket.close()
            raise

        if approval_ticket is not None:
            reason = (
                "plausibility"
                if plaus is not None and plaus.require_confirmation
                else "risk_tier"
            )
            await self._bus.publish(
                ActionApprovalRequired(
                    trace_id=tid,
                    tool_name=tool.name,
                    risk_tier=decision.tier,
                    reason=reason,
                    args_preview=safe_preview(args),
                    expires_at_ns=time.time_ns()
                    + int(self._default_timeout_s * 1_000_000_000),
                    mission_id=_optional_string(
                        (config_snapshot or {}).get("mission_id")
                    ),
                    worker_id=_optional_string(
                        (config_snapshot or {}).get("worker_id")
                    ),
                )
            )

        if needs_confirm:
            # Two-turn confirmation on a conversational turn: do NOT block on the
            # UI-approval future (no voice/chat user can resolve it within the
            # turn's latency window). Stash the action and return the sentinel so
            # the brain speaks a confirmation question; the user's next "ja" calls
            # ``execute_confirmed`` (AD-OE: the talker never awaits heavy/blocking
            # work on the turn). ``needs_confirm`` already excludes whitelist
            # downgrades, so this fires only for genuinely consequential tools.
            if voice_confirm:
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
            try:
                approved, who_or_reason = await self._approval.wait(
                    tid, self._default_timeout_s
                )
            finally:
                if approval_ticket is not None:
                    approval_ticket.close()
            if not approved:
                await self._bus.publish(ActionDenied(
                    trace_id=tid,
                    tool_name=tool.name,
                    reason=who_or_reason,
                ))
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"approval-denied ({who_or_reason})",
                )
            approved_by = who_or_reason  # "user" or "auto"

        if cancel_token is not None and cancel_token.is_cancelled():
            await self._bus.publish(ActionDenied(
                trace_id=tid,
                tool_name=tool.name,
                reason=f"cancelled: {cancel_token.reason or 'requested'}",
            ))
            return ToolResult(
                success=False,
                output=None,
                error=f"cancelled ({cancel_token.reason or 'requested'})",
            )

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
