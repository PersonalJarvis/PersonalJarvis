"""Pure derivations for the Run Inspector — no I/O, no store access.

Inputs are the rows the loader already fetched (VoiceEventRow / VoiceTurnRow /
UsageRow); outputs are the run model DTOs. Keeping this pure makes the forensic
logic unit-testable without a database."""
from __future__ import annotations

from jarvis.runs.constants import (
    DECISION_BRAIN,
    DECISION_FALLBACK,
    DECISION_MISSION,
    DECISION_RISK,
    DECISION_ROUTE,
    DECISION_TIER,
    SLO_BREACH,
    SLO_OK,
    SLO_WARN,
)
from jarvis.runs.model import (
    DecisionStep,
    ErrorEntry,
    LatencyEntry,
    RunAnalytics,
    RunTurn,
    ToolCall,
    TraceEvent,
    TurnExtras,
)
from jarvis.sessions.models import VoiceEventRow

# Per-phase SLO budget in ms. Phases not listed have no gate (always SLO_OK).
# Budgets mirror the documented voice SLOs: wake->ACK < 1.2s, intent->ACK < 3.0s,
# router decision < 150ms (CLAUDE.md "Optimistic Execution").
_PHASE_SLO_MS: dict[str, float] = {
    "intent_decision": 150.0,
    "ack_first_audio": 1200.0,
    "ack_first_token": 1200.0,
    "brain_first_audio": 3000.0,
    "brain_first_token": 3000.0,
    "turn_to_first_audio": 3000.0,
}
_WARN_FRACTION = 0.8

_SLO_RANK = {SLO_OK: 0, SLO_WARN: 1, SLO_BREACH: 2}


def classify_latency(phase: str, duration_ms: float) -> str:
    budget = _PHASE_SLO_MS.get(phase)
    if budget is None:
        return SLO_OK
    if duration_ms > budget:
        return SLO_BREACH
    if duration_ms >= budget * _WARN_FRACTION:
        return SLO_WARN
    return SLO_OK


def build_latency(events: list[VoiceEventRow]) -> list[LatencyEntry]:
    out: list[LatencyEntry] = []
    for e in events:
        if e.kind != "LatencySpan":
            continue
        phase = str(e.payload.get("phase", ""))
        dur = float(e.payload.get("duration_ms", 0.0) or 0.0)
        if not phase:
            continue
        out.append(LatencyEntry(phase=phase, duration_ms=dur,
                                slo_status=classify_latency(phase, dur)))
    return out


def build_decision_path(events: list[VoiceEventRow]) -> list[DecisionStep]:
    steps: list[DecisionStep] = []
    providers_seen: list[str] = []
    for e in sorted(events, key=lambda x: x.ts_ms):
        p = e.payload
        if e.kind == "IntentClassified":
            steps.append(DecisionStep(
                kind=DECISION_TIER,
                label=f"intent: {p.get('intent', '?')}",
                detail=f"risk={p.get('risk_tier', '?')}",
            ))
        elif e.kind == "ActionProposed":
            steps.append(DecisionStep(
                kind=DECISION_ROUTE,
                label=f"proposed: {p.get('tool_name', '?')}",
                detail=f"risk={p.get('risk_tier', '?')}",
            ))
        elif e.kind == "ActionApproved":
            steps.append(DecisionStep(
                kind=DECISION_RISK,
                label=f"approved: {p.get('tool_name', '?')}",
                detail=f"by={p.get('approved_by', 'auto')}",
            ))
        elif e.kind == "ActionDenied":
            steps.append(DecisionStep(
                kind=DECISION_RISK,
                label=f"denied: {p.get('tool_name', '?')}",
                detail=str(p.get("reason", "")),
            ))
        elif e.kind == "BrainTurnStarted":
            provider = str(p.get("provider", ""))
            model = str(p.get("model", ""))
            if provider:
                providers_seen.append(provider)
            steps.append(DecisionStep(
                kind=DECISION_BRAIN,
                label=f"brain: {provider or '?'}",
                detail=(f"model={model}" if model else None),
            ))
        elif e.kind == "OpenClawTaskStarted":
            steps.append(DecisionStep(
                kind=DECISION_MISSION,
                label="spawned sub-agent mission",
                detail=str(p.get("model", "")) or None,
            ))
    # A second distinct provider across the turn means the smart-fallback fired.
    distinct = [p for i, p in enumerate(providers_seen) if p and p not in providers_seen[:i]]
    if len(distinct) > 1:
        steps.append(DecisionStep(
            kind=DECISION_FALLBACK,
            label="provider fallback",
            detail=" -> ".join(distinct),
        ))
    return steps


def build_errors(events: list[VoiceEventRow]) -> list[ErrorEntry]:
    out: list[ErrorEntry] = []
    for e in events:
        p = e.payload
        if e.kind == "ErrorOccurred":
            out.append(ErrorEntry(
                source="ErrorOccurred",
                layer=str(p.get("layer", "")) or None,
                message=str(p.get("error_type", "")) + ": " + str(p.get("message", "")),
                recoverable=p.get("recoverable"),
            ))
        elif e.kind == "ActionDenied":
            out.append(ErrorEntry(
                source="ActionDenied",
                message=f"{p.get('tool_name', '?')}: {p.get('reason', '')}",
            ))
        elif e.kind == "SpeechSpoken" and p.get("detail"):
            # The non-spoken CU-failure detail track ("exit 5 - <reason>").
            out.append(ErrorEntry(source="cu_failure", message=str(p.get("detail"))))
    return out


def build_extras(events: list[VoiceEventRow], *, tokens_in: int = 0) -> TurnExtras:
    extras = TurnExtras(context_tokens=tokens_in or None)
    for e in events:
        p = e.payload
        if e.kind == "BrainTTFT" and "cache_hit" in p:
            extras.cache_hit = bool(p.get("cache_hit"))
        if e.kind == "SpeechSpoken":
            detail = str(p.get("detail", ""))
            if detail.startswith("endpoint="):
                extras.endpoint_reason = detail.split("=", 1)[1]
    return extras


def build_timeline(events: list[VoiceEventRow], *, turn_started_ms: int) -> list[TraceEvent]:
    out: list[TraceEvent] = []
    for e in sorted(events, key=lambda x: x.ts_ms):
        out.append(TraceEvent(
            kind=e.kind,
            ts_ms=e.ts_ms,
            offset_ms=max(0, e.ts_ms - turn_started_ms),
            summary=_summarize(e),
        ))
    return out


def tools_from_usage(usage_rows: list) -> list[ToolCall]:
    """UsageRow list (jarvis.clis.usage_log.UsageRow) -> ToolCall DTOs."""
    out: list[ToolCall] = []
    for r in usage_rows:
        first_err = None
        if r.stderr_preview:
            lines = r.stderr_preview.splitlines()
            first_err = next(
                (ln for ln in lines if "error" in ln.lower()),
                lines[0] if lines else None,
            )
        out.append(ToolCall(
            name=r.cli_name,
            caller=r.caller,
            duration_ms=r.duration_ms,
            exit_code=r.exit_code,
            success=(r.exit_code == 0),
            error_line=first_err,
        ))
    return out


def merge_action_tools(events: list[VoiceEventRow], cli_tools: list[ToolCall]) -> list[ToolCall]:
    """Add non-CLI tool calls (ActionProposed/Approved) so router-tier tools that
    are not CLI invocations still appear, carrying their risk-tier + approval."""
    by_name = {t.name: t for t in cli_tools}
    risk: dict[str, str] = {}
    approval: dict[str, str] = {}
    for e in events:
        p = e.payload
        if e.kind == "ActionProposed" and p.get("tool_name"):
            risk[str(p["tool_name"])] = str(p.get("risk_tier", ""))
        if e.kind == "ActionApproved" and p.get("tool_name"):
            approval[str(p["tool_name"])] = str(p.get("approved_by", ""))
    for name, tier in risk.items():
        if name in by_name:
            by_name[name].risk_tier = tier
            by_name[name].approved_by = approval.get(name)
        else:
            cli_tools.append(ToolCall(name=name, risk_tier=tier,
                                      approved_by=approval.get(name)))
    return cli_tools


def build_analytics(turns: list[RunTurn], *, started_ms: int,
                    ended_ms: int | None) -> RunAnalytics:
    cost_by_provider: dict[str, float] = {}
    tool_counts: dict[str, int] = {}
    worst = SLO_OK
    interruptions = 0
    total_think = total_speak = 0
    for t in turns:
        if t.provider:
            cost_by_provider[t.provider] = cost_by_provider.get(t.provider, 0.0) + t.cost_usd
        for tc in t.tools:
            tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1
        total_think += t.think_ms
        total_speak += t.speak_ms
        if t.extras.interrupted:
            interruptions += 1
        for le in t.latency:
            if _SLO_RANK.get(le.slo_status, 0) > _SLO_RANK.get(worst, 0):
                worst = le.slo_status
    duration_s = ((ended_ms - started_ms) / 1000.0) if ended_ms is not None else None
    return RunAnalytics(
        total_duration_s=duration_s,
        total_think_ms=total_think,
        total_speak_ms=total_speak,
        cost_by_provider=cost_by_provider,
        tool_counts=tool_counts,
        interruptions=interruptions,
        worst_slo_status=worst,
    )


def _summarize(e: VoiceEventRow) -> str:
    p = e.payload
    if e.kind == "TranscriptFinal":
        return str(p.get("text", ""))[:80]
    if e.kind == "ResponseGenerated":
        return str(p.get("text", ""))[:80]
    if e.kind == "IntentClassified":
        return f"{p.get('intent', '')} (risk={p.get('risk_tier', '')})"
    if e.kind in ("ActionProposed", "ActionApproved", "ActionDenied"):
        return str(p.get("tool_name", ""))
    if e.kind == "BrainTurnStarted":
        return f"{p.get('provider', '')}/{p.get('model', '')}"
    if e.kind == "SystemStateChanged":
        return f"{p.get('previous', '')} -> {p.get('new_state', '')}"
    return ""


__all__ = [
    "classify_latency", "build_latency", "build_decision_path", "build_errors",
    "build_extras", "build_timeline", "tools_from_usage", "merge_action_tools",
    "build_analytics",
]
