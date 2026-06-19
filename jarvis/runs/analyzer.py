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
    OUTCOME_FAILED,
    OUTCOME_PARTIAL,
    OUTCOME_SUCCESS,
    ROLE_ERROR,
    ROLE_JARVIS,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_USER,
    SLO_BREACH,
    SLO_OK,
    SLO_WARN,
)
from jarvis.runs.model import (
    DecisionStep,
    ErrorEntry,
    LatencyEntry,
    RunActivity,
    RunAnalytics,
    RunTurn,
    ToolCall,
    TraceEvent,
    TranscriptLine,
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


def build_transcript(
    events: list[VoiceEventRow], *, turn_started_ms: int = 0
) -> list[TranscriptLine]:
    """The gap-less, UNTRUNCATED transcript of one turn.

    Weaves, in chronological order, everything a human re-reading the run would
    want to see: the user's utterance, every phrase Jarvis voiced (the reply
    plus intermediate/announcement/clarify sentences), state transitions, tool
    and Computer-Use outcomes, and system outputs — including the non-spoken
    failure diagnostics that ride on ``SpeechSpoken.detail`` (e.g. ``exit 5 -
    <reason>``) and denials/errors. Unlike ``build_timeline`` this keeps full
    text (no 80-char cut) and tags each line with a role for styling."""
    out: list[TranscriptLine] = []

    def _emit(e: VoiceEventRow, role: str, text: str, *, spoken_kind: str | None = None) -> None:
        if not text:
            return
        out.append(TranscriptLine(
            role=role,
            kind=e.kind,
            text=text,
            offset_ms=max(0, e.ts_ms - turn_started_ms),
            ts_ms=e.ts_ms,
            spoken_kind=spoken_kind,
        ))

    for e in sorted(events, key=lambda x: x.ts_ms):
        p = e.payload
        if e.kind == "TranscriptFinal":
            _emit(e, ROLE_USER, str(p.get("text", "")).strip())
        elif e.kind == "ResponseGenerated":
            _emit(e, ROLE_JARVIS, str(p.get("text", "")).strip())
        elif e.kind == "SpeechSpoken":
            sk = str(p.get("spoken_kind") or "") or None
            _emit(e, ROLE_JARVIS, str(p.get("text", "")).strip(), spoken_kind=sk)
            detail = str(p.get("detail", "")).strip()
            # detail "endpoint=<reason>" is telemetry, not a system output line.
            if detail and not detail.startswith("endpoint="):
                _emit(e, ROLE_SYSTEM, detail)
        elif e.kind == "SystemStateChanged":
            prev = str(p.get("previous", ""))
            new = str(p.get("new_state", ""))
            _emit(e, ROLE_SYSTEM, f"{prev} -> {new}".strip(" ->"))
        elif e.kind == "ActionExecuted":
            name = str(p.get("tool_name", "?"))
            ok = bool(p.get("success", True))
            err = str(p.get("error", "")).strip()
            _emit(e, ROLE_TOOL,
                  f"{name} ok" if ok else f"{name} failed" + (f": {err}" if err else ""))
        elif e.kind == "ErrorOccurred":
            _emit(e, ROLE_ERROR,
                  f"{p.get('error_type', '')}: {p.get('message', '')}".strip(": "))
        elif e.kind == "ActionDenied":
            _emit(e, ROLE_ERROR, f"{p.get('tool_name', '?')}: {p.get('reason', '')}")
    return out


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
    are not CLI invocations still appear, carrying their risk-tier + approval —
    and fold in the ActionExecuted OUTCOME so a failed tool (e.g. a Computer-Use
    ``open_app`` that could not find the app) is reported as failed, not "ok".
    Without the outcome pass ``ToolCall.success`` stayed at its default True and
    the Tools panel claimed success for actions that actually failed."""
    by_name = {t.name: t for t in cli_tools}
    risk: dict[str, str] = {}
    approval: dict[str, str] = {}
    # name -> (all_ok, first_error). Failure wins: one failed run marks the row.
    executed: dict[str, tuple[bool, str | None]] = {}
    for e in events:
        p = e.payload
        name = str(p.get("tool_name") or "")
        if e.kind == "ActionProposed" and name:
            risk[name] = str(p.get("risk_tier", ""))
        elif e.kind == "ActionApproved" and name:
            approval[name] = str(p.get("approved_by", ""))
        elif e.kind == "ActionExecuted" and name:
            ok = bool(p.get("success", True))
            err = str(p.get("error", "")).strip() or None
            prev_ok, prev_err = executed.get(name, (True, None))
            executed[name] = (prev_ok and ok, prev_err or (err if not ok else None))
    for name, tier in risk.items():
        if name in by_name:
            by_name[name].risk_tier = tier
            by_name[name].approved_by = approval.get(name)
        else:
            tc = ToolCall(name=name, risk_tier=tier, approved_by=approval.get(name))
            cli_tools.append(tc)
            by_name[name] = tc
    for name, (ok, err) in executed.items():
        if name not in by_name:
            tc = ToolCall(name=name, success=ok, error_line=(err if not ok else None))
            cli_tools.append(tc)
            by_name[name] = tc
        elif not ok:
            by_name[name].success = False
            if err and not by_name[name].error_line:
                by_name[name].error_line = err
    return cli_tools


# --- Outcome (functional result, NOT latency) -------------------------------
_OUTCOME_RANK = {OUTCOME_SUCCESS: 0, OUTCOME_PARTIAL: 1, OUTCOME_FAILED: 2}

# Computer-Use action verbs that mark the CU agent as active.
_CU_TOOLS = frozenset({
    "computer_use", "open_app", "click", "click_element", "double_click",
    "right_click", "hotkey", "type_text", "scroll", "screenshot", "move_mouse",
    "key", "drag", "wait", "verify",
})
# Tools whose presence means a background sub-agent / skill ran — surfaced as a
# named agent badge instead of a raw tool chip.
_SUB_AGENT_TOOLS = frozenset({
    "spawn_worker", "spawn-worker", "spawn_openclaw", "spawn-openclaw",
    "dispatch-with-review", "dispatch_with_review",
})
_SKILL_TOOLS = frozenset({
    "run-skill", "run_skill", "spawn-skill-author", "spawn_skill_author",
})


def _agent_for_tool(name: str) -> str | None:
    if name in _CU_TOOLS:
        return "computer_use"
    if name in _SUB_AGENT_TOOLS:
        return "sub_agent"
    if name in _SKILL_TOOLS:
        return "skill"
    return None


def _is_cu_failure_detail(detail: str) -> bool:
    """A SpeechSpoken.detail that carries a Computer-Use failure diagnostic."""
    return detail.strip().lower().startswith(("exit", "[cu", "cu "))


def _decide_outcome(*, answered: bool, hard: bool, soft: bool) -> str:
    """Single source of truth for the outcome traffic light."""
    if hard and not answered:
        return OUTCOME_FAILED
    if hard or soft:
        return OUTCOME_PARTIAL
    return OUTCOME_SUCCESS


def _is_hard_error(e: ErrorEntry) -> bool:
    if e.source in ("MissionFailed", "ActionDenied"):
        return True
    return e.source == "ErrorOccurred" and e.recoverable is False


def turn_outcome(turn: RunTurn) -> str:
    answered = bool((turn.jarvis_text or "").strip()) or any(
        line.role == ROLE_JARVIS for line in turn.transcript
    )
    hard = any(_is_hard_error(e) for e in turn.errors)
    soft = any(not tc.success for tc in turn.tools) or any(
        e.source == "cu_failure" or (e.source == "ErrorOccurred" and e.recoverable is True)
        for e in turn.errors
    )
    return _decide_outcome(answered=answered, hard=hard, soft=soft)


def build_outcome(turns: list[RunTurn]) -> str:
    """Worst turn outcome across the run."""
    worst = OUTCOME_SUCCESS
    for t in turns:
        o = turn_outcome(t)
        if _OUTCOME_RANK.get(o, 0) > _OUTCOME_RANK.get(worst, 0):
            worst = o
    return worst


def outcome_from_events(events: list[VoiceEventRow]) -> str:
    """Lightweight outcome from raw events (used by the run list, no turn build)."""
    answered = any(
        e.kind == "ResponseGenerated"
        or (e.kind == "SpeechSpoken" and str(e.payload.get("text", "")).strip())
        for e in events
    )
    hard = any(
        (e.kind == "ErrorOccurred" and e.payload.get("recoverable") is False)
        or e.kind == "ActionDenied"
        for e in events
    )
    soft = any(
        e.kind == "ActionExecuted" and e.payload.get("success") is False
        for e in events
    ) or any(
        e.kind == "SpeechSpoken" and _is_cu_failure_detail(str(e.payload.get("detail", "")))
        for e in events
    )
    return _decide_outcome(answered=answered, hard=hard, soft=soft)


def build_activity(turns: list[RunTurn]) -> RunActivity:
    tools: list[str] = []
    agents: list[str] = []
    for t in turns:
        for tc in t.tools:
            ag = _agent_for_tool(tc.name)
            if ag:
                if ag not in agents:
                    agents.append(ag)
            elif tc.name and tc.name not in tools:
                tools.append(tc.name)
        for e in t.errors:
            if e.source == "cu_failure" and "computer_use" not in agents:
                agents.append("computer_use")
        for s in t.decision_path:
            if s.kind == DECISION_MISSION and "sub_agent" not in agents:
                agents.append("sub_agent")
    return RunActivity(tools=tools, agents=_ordered_agents(agents))


def feature_tags_from_events(events: list[VoiceEventRow]) -> list[str]:
    """Compact badge set for a run card, derived from raw events."""
    tools: list[str] = []
    agents: list[str] = []
    for e in events:
        name = str(e.payload.get("tool_name") or "")
        if e.kind in ("ActionProposed", "ActionExecuted") and name:
            ag = _agent_for_tool(name)
            if ag:
                if ag not in agents:
                    agents.append(ag)
            elif name not in tools:
                tools.append(name)
        if e.kind == "OpenClawTaskStarted" and "sub_agent" not in agents:
            agents.append("sub_agent")
        if (e.kind == "SpeechSpoken"
                and _is_cu_failure_detail(str(e.payload.get("detail", "")))
                and "computer_use" not in agents):
            agents.append("computer_use")
    tags = _ordered_agents(agents)
    for t in tools:
        if t not in tags:
            tags.append(t)
        if len(tags) >= 4:
            break
    return tags


def _ordered_agents(agents: list[str]) -> list[str]:
    order = ["computer_use", "sub_agent", "skill"]
    return [a for a in order if a in agents] + [a for a in agents if a not in order]


def build_analytics(turns: list[RunTurn], *, started_ms: int,
                    ended_ms: int | None) -> RunAnalytics:
    cost_by_provider: dict[str, float] = {}
    tool_counts: dict[str, int] = {}
    worst = SLO_OK
    interruptions = 0
    total_think = total_speak = 0
    total_tokens_in = total_tokens_out = 0
    for t in turns:
        if t.provider:
            cost_by_provider[t.provider] = cost_by_provider.get(t.provider, 0.0) + t.cost_usd
        for tc in t.tools:
            tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1
        total_think += t.think_ms
        total_speak += t.speak_ms
        total_tokens_in += t.tokens_in
        total_tokens_out += t.tokens_out
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
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
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
    "build_extras", "build_transcript", "build_timeline", "tools_from_usage",
    "merge_action_tools", "build_analytics",
    "turn_outcome", "build_outcome", "outcome_from_events", "build_activity",
    "feature_tags_from_events",
]
