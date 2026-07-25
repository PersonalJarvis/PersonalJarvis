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
    EVENT_CAT_SYSTEM,
    EVENT_CATEGORY_BY_KIND,
    MAX_RAW_EVENTS_PER_TURN,
    OUTCOME_FAILED,
    OUTCOME_PARTIAL,
    OUTCOME_SUCCESS,
    RATIONALE_MODEL,
    RATIONALE_RULE,
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
    RawEvent,
    RunActivity,
    RunAnalytics,
    RunEnvironment,
    RunTurn,
    ToolCall,
    TraceEvent,
    TranscriptLine,
    TurnExtras,
)
from jarvis.sessions.models import VoiceEventRow, VoiceSessionRow

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


def _approval_rationale(by: str) -> str:
    """Honest plain-language reading of a CAPTURED approval source — not a guess."""
    b = (by or "").lower()
    if b == "whitelist":
        return "Auto-approved — this action is on your allow-list."
    if b == "user":
        return "You approved this action."
    if b == "auto":
        return "Auto-approved — a low-risk action."
    return f"Approved ({by})." if by else "Approved."


def build_decision_path(events: list[VoiceEventRow]) -> list[DecisionStep]:
    """The decision path, now carrying the honest "why" per step.

    The route step prefers the model's OWN words (ActionProposed.rationale,
    captured for free) tagged ``model``; every other step gets a deterministic
    ``rule`` explanation built from a captured fact (approval source, denial
    reason, provider, fallback). Nothing here is fabricated."""
    steps: list[DecisionStep] = []
    providers_seen: list[str] = []
    for e in sorted(events, key=lambda x: x.ts_ms):
        p = e.payload
        if e.kind == "IntentClassified":
            intent = p.get("intent", "?")
            risk = p.get("risk_tier", "?")
            steps.append(DecisionStep(
                kind=DECISION_TIER,
                label=f"intent: {intent}",
                detail=f"risk={risk}",
                rationale=f"Recognized as a '{intent}' request (risk tier: {risk}).",
                rationale_source=RATIONALE_RULE,
            ))
        elif e.kind == "ActionProposed":
            tool = p.get("tool_name", "?")
            risk = p.get("risk_tier", "?")
            model_why = str(p.get("rationale", "") or "").strip()
            if model_why:
                rationale, source = model_why, RATIONALE_MODEL
            else:
                rationale, source = f"Chose the {tool} tool (risk: {risk}).", RATIONALE_RULE
            steps.append(DecisionStep(
                kind=DECISION_ROUTE,
                label=f"proposed: {tool}",
                detail=f"risk={risk}",
                rationale=rationale,
                rationale_source=source,
            ))
        elif e.kind == "ActionApproved":
            by = p.get("approved_by", "auto")
            steps.append(DecisionStep(
                kind=DECISION_RISK,
                label=f"approved: {p.get('tool_name', '?')}",
                detail=f"by={by}",
                rationale=_approval_rationale(by),
                rationale_source=RATIONALE_RULE,
            ))
        elif e.kind == "ActionDenied":
            reason = str(p.get("reason", "")).strip()
            steps.append(DecisionStep(
                kind=DECISION_RISK,
                label=f"denied: {p.get('tool_name', '?')}",
                detail=reason or None,
                rationale=(f"Blocked — {reason}." if reason else "Blocked by a safety rule."),
                rationale_source=RATIONALE_RULE,
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
                rationale=(
                    f"Answered by {provider}" + (f" ({model})" if model else "") + "."
                    if provider else "Answered by the configured brain."
                ),
                rationale_source=RATIONALE_RULE,
            ))
        elif e.kind == "JarvisAgentTaskStarted":
            steps.append(DecisionStep(
                kind=DECISION_MISSION,
                label="spawned sub-agent mission",
                detail=str(p.get("model", "")) or None,
                rationale="Handed the task to a background sub-agent.",
                rationale_source=RATIONALE_RULE,
            ))
        elif e.kind == "JarvisAgentTaskCompleted":
            ok = bool(p.get("success"))
            steps.append(DecisionStep(
                kind=DECISION_MISSION,
                label=("sub-agent finished" if ok else "sub-agent FAILED"),
                detail=str(p.get("summary", ""))[:120] or None,
                rationale=(
                    "The background sub-agent returned a result."
                    if ok else
                    f"The background sub-agent failed — {p.get('error', 'no reason recorded')}."
                ),
                rationale_source=RATIONALE_RULE,
            ))
        elif e.kind == "RealtimeSessionReady":
            # The duplex path never emits BrainTurnStarted, so without this the
            # whole decision path of a realtime turn was empty — the single
            # biggest blind spot of the old inspector, since realtime is the
            # default mode.
            provider = str(p.get("provider", ""))
            model = str(p.get("model", ""))
            if provider:
                providers_seen.append(provider)
            steps.append(DecisionStep(
                kind=DECISION_BRAIN,
                label=f"realtime session: {provider or '?'}",
                detail=(f"model={model}" if model else None),
                rationale=(
                    f"A duplex (speech-to-speech) session with {provider}"
                    + (f" ({model})" if model else "")
                    + " accepted the configuration — this turn is answered live, "
                    "not through the classic transcribe → think → speak chain."
                ),
                rationale_source=RATIONALE_RULE,
            ))
        elif e.kind == "LatencySpan" and str(p.get("phase", "")) == "realtime_routing_decision":
            # Honest reading: the span proves WHEN the router decided and how
            # long it took — never WHAT it decided. Do not invent the verdict.
            dur = _fmt_ms(p.get("duration_ms"))
            steps.append(DecisionStep(
                kind=DECISION_ROUTE,
                label="realtime routing decision",
                detail=dur or None,
                rationale=f"The realtime router reached its decision in {dur or 'n/a'}.",
                rationale_source=RATIONALE_RULE,
            ))
        elif e.kind == "BrainProviderSwitched":
            frm = str(p.get("from_provider", "") or "?")
            to = str(p.get("to_provider", "") or "?")
            steps.append(DecisionStep(
                kind=DECISION_FALLBACK,
                label="provider switched",
                detail=f"{frm} -> {to}",
                rationale=(
                    f"The brain moved from {frm} to {to} — the configured provider "
                    "was unavailable, out of quota, or rejected the request, so the "
                    "key-aware fallback chain crossed to the next reachable one."
                ),
                rationale_source=RATIONALE_RULE,
            ))
    # A second distinct provider across the turn means the smart-fallback fired.
    distinct = [p for i, p in enumerate(providers_seen) if p and p not in providers_seen[:i]]
    if len(distinct) > 1 and not any(s.kind == DECISION_FALLBACK for s in steps):
        steps.append(DecisionStep(
            kind=DECISION_FALLBACK,
            label="provider fallback",
            detail=" -> ".join(distinct),
            rationale=f"Switched provider — {distinct[0]} → {distinct[-1]} (a fallback fired).",
            rationale_source=RATIONALE_RULE,
        ))
    return steps


def ensure_brain_step(steps: list[DecisionStep], turn: RunTurn) -> list[DecisionStep]:
    """Guarantee the path names WHO answered, even with no brain event captured.

    Some turns (realtime, cached, tool-only) record no ``BrainTurnStarted`` and
    no ``RealtimeSessionReady`` of their own, yet the aggregated turn row does
    know the provider/model. Falling back to that row is honest — it is the
    recorded answer — and keeps every turn from claiming "no decisions"."""
    if any(s.kind == DECISION_BRAIN for s in steps) or not turn.provider:
        return steps
    tier = f"tier={turn.tier}" if turn.tier else ""
    model = f"model={turn.model}" if turn.model else ""
    steps.insert(0, DecisionStep(
        kind=DECISION_BRAIN,
        label=f"brain: {turn.provider}",
        detail=_join([tier, model]) or None,
        rationale=(
            f"Answered by {turn.provider}"
            + (f" ({turn.model})" if turn.model else "")
            + ("" if not turn.tier else f" on the {turn.tier} tier")
            + " — recorded on the turn aggregate; this turn emitted no separate "
            "brain-start event."
        ),
        rationale_source=RATIONALE_RULE,
    ))
    return steps


def attach_tool_io(events: list[VoiceEventRow], tools: list[ToolCall]) -> list[ToolCall]:
    """Surface the already-captured command + result onto the tool rows.

    ``ToolCallStarted`` carries the tool name + ``args_preview`` (the command);
    ``ToolCallCompleted`` carries ``output_preview`` (the result) but no name, so
    it is paired with the most recent Started in chronological order. A tool seen
    only here (no ActionProposed row) is added so its I/O is never silently
    dropped. All fields are already redacted/length-capped upstream."""
    by_name = {t.name: t for t in tools}
    pending: str | None = None
    for e in sorted(events, key=lambda x: x.ts_ms):
        p = e.payload
        if e.kind == "ToolCallStarted":
            name = str(p.get("tool_name") or "")
            cmd = str(p.get("args_preview") or "")
            pending = name or pending
            if name:
                tc = by_name.get(name)
                if tc is None:
                    tc = ToolCall(name=name)
                    tools.append(tc)
                    by_name[name] = tc
                if cmd and not tc.command:
                    tc.command = cmd
        elif e.kind == "ToolCallCompleted":
            out = str(p.get("output_preview") or "")
            if pending and out:
                tc = by_name.get(pending)
                if tc is not None and not tc.output:
                    tc.output = out
            pending = None
    return tools


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
            command=str(getattr(r, "full_command", "") or ""),
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
        if e.kind == "JarvisAgentTaskStarted" and "sub_agent" not in agents:
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


def _fmt_ms(value: object) -> str:
    try:
        ms = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    return f"{ms / 1000:.2f}s" if ms >= 1000 else f"{ms:.0f}ms"


def _join(parts: list[str]) -> str:
    return " · ".join(p for p in parts if p)


def summarize_event(e: VoiceEventRow) -> str:
    """A one-line human reading of ANY recorded event.

    Before this, only six kinds had a summary and every other row in the
    timeline rendered as a bare kind name with an empty right-hand column — in
    a typical realtime run that was ~70% of all rows, which is exactly why the
    timeline read as noise.

    The contract now is a hard one: **a row is never blank while its event
    carries a payload.** A kind-specific reading is tried first; when that comes
    out empty — an unknown kind, or a known kind whose expected fields are
    absent because the publisher changed — the payload itself is rendered
    compactly instead of nothing."""
    specific = _specific_summary(e)
    if specific:
        return specific
    return _join([f"{key}={_short(val)}" for key, val in list(e.payload.items())[:4]])


def _specific_summary(e: VoiceEventRow) -> str:
    p = e.payload
    k = e.kind

    # --- speech / transcript -----------------------------------------
    if k in ("TranscriptFinal", "TranscriptPartial", "ResponseGenerated"):
        return str(p.get("text", ""))[:160]
    if k == "TranscriptionUpdate":
        final = " (final)" if p.get("is_final") else ""
        return f"{str(p.get('text', ''))[:120]}{final}"
    if k == "SpeechSpoken":
        kind = str(p.get("spoken_kind", "") or "spoken")
        voice = str(p.get("voice", "") or "")
        head = f"[{kind}]" + (f" {voice}" if voice else "")
        return _join([head, str(p.get("text", ""))[:120], str(p.get("detail", ""))[:80]])
    if k == "AudioOutFirst":
        return "first audio frame reached the speaker"
    if k == "ListeningStarted":
        return "microphone open"
    if k in ("WakeWordDetected", "HotkeyPressed"):
        conf = p.get("confidence")
        return _join([
            str(p.get("keyword", "") or k),
            f"confidence {float(conf):.2f}" if isinstance(conf, int | float) else "",
        ])

    # --- brain --------------------------------------------------------
    if k == "IntentClassified":
        return _join([str(p.get("intent", "")), f"risk={p.get('risk_tier', '')}"])
    if k == "BrainTurnStarted":
        return _join([
            f"{p.get('provider', '?')}/{p.get('model', '?')}",
            f"tier={p.get('intent_level', '')}" if p.get("intent_level") else "",
        ])
    if k == "BrainTurnCompleted":
        return _join([
            f"{p.get('provider', '')}/{p.get('model', '')}".strip("/"),
            f"{p.get('tokens_in', 0)}+{p.get('tokens_out', 0)} tok",
            f"${float(p.get('cost_usd', 0) or 0):.4f}",
            f"finish={p.get('finish_reason', '')}" if p.get("finish_reason") else "",
        ])
    if k == "BrainTTFT":
        return _join([
            str(p.get("model", "")),
            "prompt cache HIT" if p.get("cache_hit") else "prompt cache miss",
        ])
    if k == "BrainProviderSwitched":
        return f"{p.get('from_provider', '?')} → {p.get('to_provider', '?')}"
    if k == "FrontierModelSwitched":
        return _join([
            str(p.get("provider", "")),
            f"{p.get('old_model', '?')} → {p.get('new_model', '?')}",
        ])
    if k == "BrainToolsChanged":
        return str(p.get("reason", "") or "tool set refreshed")

    # --- tools --------------------------------------------------------
    if k == "ActionProposed":
        return _join([
            str(p.get("tool_name", "?")),
            f"action={p.get('action', '')}" if p.get("action") else "",
            f"risk={p.get('risk_tier', '')}" if p.get("risk_tier") else "",
            str(p.get("args_preview", ""))[:80],
        ])
    if k in ("ActionApproved", "ActionApprovalRequired"):
        return _join([str(p.get("tool_name", "?")), f"by={p.get('approved_by', '')}"])
    if k == "ActionDenied":
        return _join([str(p.get("tool_name", "?")), str(p.get("reason", ""))])
    if k == "ActionExecuted":
        ok = "ok" if p.get("success", True) else "FAILED"
        return _join([str(p.get("tool_name", "?")), ok, str(p.get("error", ""))[:80]])
    if k == "ToolCallStarted":
        return _join([str(p.get("tool_name", "?")), str(p.get("args_preview", ""))[:100]])
    if k == "ToolCallCompleted":
        ok = "ok" if p.get("success", True) else "FAILED"
        return _join([ok, _fmt_ms(p.get("duration_ms")),
                      str(p.get("output_preview", ""))[:100]])
    if k == "CliInvoked":
        return _join([str(p.get("cli_name", "?")), f"by {p.get('caller', '')}",
                      str(p.get("command_preview", ""))[:80]])
    if k == "CliInvocationFinished":
        return _join([str(p.get("cli_name", "?")), f"exit {p.get('exit_code')}",
                      _fmt_ms(p.get("duration_ms"))])

    # --- agent / mission ----------------------------------------------
    if k == "JarvisAgentTaskStarted":
        return _join([f"{p.get('provider', '')}/{p.get('model', '')}".strip("/"),
                      str(p.get("utterance", ""))[:100]])
    if k == "JarvisAgentReviewTriggered":
        return f"critic loop iteration {p.get('iteration', '?')}"
    if k == "JarvisAgentTaskCompleted":
        ok = "success" if p.get("success") else "FAILED"
        return _join([ok, _fmt_ms(float(p.get("duration_s", 0) or 0) * 1000),
                      str(p.get("summary", ""))[:100], str(p.get("error", "") or "")[:80]])
    if k == "JarvisAgentAnnouncement":
        return _join([str(p.get("action", "")), str(p.get("target", ""))])
    if k in ("HarnessDispatched", "HarnessCompleted"):
        return str(p.get("harness", "") or k)
    if k == "MissionCompleted":
        return _join([str(p.get("status", "")), str(p.get("summary_en", ""))[:100],
                      str(p.get("reason", ""))[:80]])

    # --- vision / Computer-Use ----------------------------------------
    if k == "ObservationCaptured":
        return _join([str(p.get("source", "")), str(p.get("window_title", ""))[:60],
                      f"{p.get('node_count', 0)} UI nodes"])
    if k == "VisionInjected":
        return _join([f"{int(p.get('bytes_size', 0) or 0) // 1024} KB screenshot",
                      f"age {_fmt_ms(p.get('capture_age_ms'))}"])
    if k == "ActionPlanned":
        return _join([str(p.get("action_kind", "")), str(p.get("target_hint", ""))[:80]])
    if k == "ActionVerified":
        ok = "verified" if p.get("success") else "VERIFY FAILED"
        return _join([str(p.get("action_kind", "")), ok, str(p.get("reason", ""))[:80]])
    if k == "CUStepProfiled":
        return _join([f"step {p.get('step_idx', '?')}", str(p.get("phase", "")),
                      _fmt_ms(p.get("duration_ms")), str(p.get("engine", ""))])
    if k == "CUControlStarted":
        return "took control of mouse + keyboard"
    if k == "CUControlEnded":
        return f"released control ({p.get('reason', '')})"

    # --- lifecycle / system -------------------------------------------
    if k == "SystemStateChanged":
        return f"{p.get('previous', '')} → {p.get('new_state', '')}"
    if k == "VoiceSessionStarted":
        return _join([str(p.get("wake_keyword", "")), str(p.get("language", ""))])
    if k == "VoiceSessionEnded":
        return _join([f"hangup: {p.get('hangup_reason', '')}",
                      f"{p.get('turn_count', 0)} turns"])
    if k == "VoiceTurnStarted":
        return f"turn {int(p.get('turn_index', 0) or 0) + 1} opened"
    if k == "VoiceTurnCompleted":
        return _join([str(p.get("tier", "")), _fmt_ms(p.get("latency_total_ms")),
                      str(p.get("voice", ""))])
    if k == "RealtimeSessionReady":
        return _join([f"{p.get('provider', '')}/{p.get('model', '')}".strip("/"),
                      str(p.get("surface", "")),
                      f"{p.get('input_sample_rate', 0)}→{p.get('output_sample_rate', 0)} Hz"])

    # --- latency / errors ---------------------------------------------
    if k == "LatencySpan":
        # The realtime path stamps the session id into ``detail``; repeating it
        # on every span would bury the phase name under a UUID. The full
        # payload stays one click away in the raw view.
        detail = str(p.get("detail", ""))
        if detail.startswith("session_id="):
            detail = ""
        return _join([str(p.get("phase", "")), _fmt_ms(p.get("duration_ms")), detail[:60]])
    if k == "ErrorOccurred":
        recov = "recoverable" if p.get("recoverable") else "UNRECOVERABLE"
        return _join([f"{p.get('layer', '')}: {p.get('error_type', '')}".strip(": "),
                      str(p.get("message", ""))[:100], recov])

    # No kind-specific reading — summarize_event falls back to the payload.
    return ""


def _short(v: object) -> str:
    s = str(v)
    return s if len(s) <= 40 else s[:37] + "…"


# Back-compat alias — the module previously exposed the private name.
_summarize = summarize_event


def event_category(kind: str) -> str:
    """UI lane for a raw event kind; unknown kinds degrade to "system"."""
    return EVENT_CATEGORY_BY_KIND.get(kind, EVENT_CAT_SYSTEM)


def build_raw_events(
    events: list[VoiceEventRow], *, turn_started_ms: int = 0,
    limit: int = MAX_RAW_EVENTS_PER_TURN,
) -> tuple[list[RawEvent], bool]:
    """The verbatim event stream + whether it had to be truncated.

    Returns ``(rows, truncated)``. Truncation keeps the FIRST ``limit`` events
    (chronological), because a run's opening is where routing/setup decisions
    live; the caller reports the cut instead of hiding it."""
    ordered = sorted(events, key=lambda x: (x.ts_ms, x.seq or 0))
    truncated = len(ordered) > limit
    rows = [
        RawEvent(
            seq=e.seq or 0,
            kind=e.kind,
            category=event_category(e.kind),
            ts_ms=e.ts_ms,
            offset_ms=max(0, e.ts_ms - turn_started_ms),
            summary=summarize_event(e),
            payload=dict(e.payload),
        )
        for e in ordered[:limit]
    ]
    return rows, truncated


def build_event_counts(events: list[VoiceEventRow]) -> dict[str, int]:
    """Event-kind histogram — the fastest read on "what dominated this run"."""
    counts: dict[str, int] = {}
    for e in events:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def build_environment(
    session: VoiceSessionRow, events: list[VoiceEventRow], turns: list[RunTurn]
) -> RunEnvironment:
    """The run's configuration, read back from what was RECORDED.

    Deliberately never consults the live config: a run inspected tomorrow must
    show the setup it actually ran under, not today's settings."""
    env = RunEnvironment(
        voice_mode=session.voice_mode or "",
        wake_source=wake_source(session.wake_keyword),
        wake_keyword=session.wake_keyword or "",
        language=session.language or "",
        hangup_reason=session.hangup_reason or "",
        providers=list(session.providers_used),
    )
    for e in sorted(events, key=lambda x: x.ts_ms):
        p = e.payload
        if e.kind == "RealtimeSessionReady":
            env.surface = str(p.get("surface", "") or env.surface)
            env.input_sample_rate = int(p.get("input_sample_rate", 0) or 0) or None
            env.output_sample_rate = int(p.get("output_sample_rate", 0) or 0) or None
        voice = str(p.get("voice", "") or "")
        if voice and voice not in env.voices:
            env.voices.append(voice)
    for t in turns:
        if t.provider and t.provider not in env.providers:
            env.providers.append(t.provider)
        if t.model and t.model not in env.models:
            env.models.append(t.model)
        if t.tier and t.tier not in env.tiers:
            env.tiers.append(t.tier)
    return env


def wake_source(wake_keyword: str) -> str:
    """voice | hotkey | channel:<name> — derived from the recorded keyword."""
    kw = (wake_keyword or "").lower()
    if "hotkey" in kw:
        return "hotkey"
    if kw.startswith("channel:"):
        return kw
    if kw in ("telegram", "discord", "web"):
        return f"channel:{kw}"
    return "voice"


__all__ = [
    "classify_latency", "build_latency", "build_decision_path", "build_errors",
    "build_extras", "build_transcript", "build_timeline", "tools_from_usage",
    "merge_action_tools", "attach_tool_io", "build_analytics",
    "turn_outcome", "build_outcome", "outcome_from_events", "build_activity",
    "feature_tags_from_events",
    "summarize_event", "event_category", "build_raw_events", "build_event_counts",
    "build_environment", "ensure_brain_step", "wake_source",
]
