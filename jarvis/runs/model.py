"""Read-only DTOs for the Run Inspector. No SQL, no schema — these compose
existing rows (VoiceSessionRow/VoiceTurnRow) plus fields derived by analyzer.py.

All enum-like fields are plain ``str`` (never Literal) so an unknown value
degrades to a UI fallback instead of an HTTP 500 — see jarvis/runs/constants.py
and the BUG-008 history."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from jarvis.sessions.models import VoiceSessionRow


class RawEvent(BaseModel):
    """One persisted bus event, verbatim — the developer's ground truth.

    Everything else in this module is a *derivation*; this is the source those
    derivations were built from. Surfacing it means a developer can always ask
    "but what actually happened?" and get the recorded answer instead of a
    summary. ``payload`` is the already-redacted dict the recorder persisted
    (whitelist-filtered at capture time — see recorder._payload_for), so no new
    privacy surface is opened by showing it."""
    model_config = ConfigDict(extra="ignore")
    seq: int = 0                # store sequence — stable chronological order
    kind: str
    category: str = "system"    # see RUN_EVENT_CATEGORIES — a UI lane
    ts_ms: int = 0
    offset_ms: int = 0          # relative to the turn's start
    summary: str = ""           # one-line human reading of the payload
    payload: dict[str, Any] = Field(default_factory=dict)


class RunEnvironment(BaseModel):
    """How this run was configured — the "which Jarvis am I looking at" header.

    A forensic report is unusable without it: the same utterance behaves
    differently in realtime vs. pipeline mode, on a different provider, or with
    a different wake source. Every field is READ from the recorded run, never
    from the host's live config (which may have changed since)."""
    model_config = ConfigDict(extra="ignore")
    voice_mode: str = ""            # realtime | pipeline
    surface: str = ""               # desktop | web | channel surface
    wake_source: str = ""           # voice | hotkey | channel:<name>
    wake_keyword: str = ""
    language: str = ""
    hangup_reason: str = ""
    providers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=list)
    voices: list[str] = Field(default_factory=list)
    input_sample_rate: int | None = None
    output_sample_rate: int | None = None


class TraceEvent(BaseModel):
    """Compact timeline row (kind + offset + one-line summary).

    Superseded on the wire by ``RawEvent``, which carries the same information
    plus the verbatim payload and a UI lane — a run no longer ships both, so a
    long Computer-Use turn is not serialized twice. Kept as a public helper
    (``analyzer.build_timeline``) for callers that only want the light shape."""
    model_config = ConfigDict(extra="ignore")
    kind: str
    offset_ms: int = 0          # relative to the turn's start
    ts_ms: int = 0
    summary: str = ""           # short human label derived from payload


class TranscriptLine(BaseModel):
    """One line of the gap-less, untruncated run transcript (see analyzer.build_transcript)."""
    model_config = ConfigDict(extra="ignore")
    role: str                   # see TRANSCRIPT_ROLES (user|jarvis|system|tool|error)
    kind: str                   # the source event kind (ResponseGenerated, SpeechSpoken, …)
    text: str = ""              # FULL text — never truncated, unlike TraceEvent.summary
    offset_ms: int = 0          # relative to the turn's start
    ts_ms: int = 0
    spoken_kind: str | None = None   # SpeechSpoken tag: announcement | clarify | progress | …


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    caller: str = ""            # router_tool | openclaw_worker | ...
    risk_tier: str = ""         # safe | monitor | ask | block
    approved_by: str | None = None  # auto | user | whitelist | None
    duration_ms: int | None = None
    exit_code: int | None = None
    success: bool = True
    error_line: str | None = None   # scrubbed stderr ERROR line
    # The actual command + result — already captured (CLI full_command,
    # ToolCallStarted.args_preview / ToolCallCompleted.output_preview) but never
    # surfaced until now. Both are redacted/length-capped upstream.
    command: str = ""           # what was run (args_preview / full_command)
    output: str = ""            # what came back (output_preview, truncated)


class LatencyEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    phase: str
    duration_ms: float
    slo_status: str = "ok"     # see SLO_STATUSES


class DecisionStep(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: str                  # see RUN_DECISION_KINDS
    label: str
    detail: str | None = None
    # Session-Decision-Log: the honest "why" for this step. ``rationale`` is the
    # plain-language reason; ``rationale_source`` tags its provenance — see
    # RATIONALE_SOURCES ("model" = the brain's own words, "rule" = a
    # deterministic explanation from a captured fact, "" = none available).
    # Never fabricated; an empty source surfaces as "no rationale recorded".
    rationale: str = ""
    rationale_source: str = ""


class ErrorEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str                # ErrorOccurred | ActionDenied | MissionFailed | cu_failure
    layer: str | None = None
    message: str = ""
    recoverable: bool | None = None


class TurnExtras(BaseModel):
    model_config = ConfigDict(extra="ignore")
    interrupted: bool = False
    cache_hit: bool | None = None
    endpoint_reason: str | None = None   # silence | max_utterance | stt_stable
    context_tokens: int | None = None    # prompt size if known (tokens_in)


class MissionRef(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mission_id: str
    status: str = ""
    summary: str = ""


class RunActivity(BaseModel):
    """Which tools and high-level agents/features were active in a run."""
    model_config = ConfigDict(extra="ignore")
    tools: list[str] = Field(default_factory=list)      # distinct tool/CLI names
    agents: list[str] = Field(default_factory=list)     # computer_use | sub_agent | …


class RunTurn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    idx: int
    trace_id: str
    outcome: str = "success"    # see RUN_OUTCOMES — functional result of this turn
    user_text: str = ""
    jarvis_text: str = ""
    tier: str = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    think_ms: int = 0
    speak_ms: int = 0
    transcript: list[TranscriptLine] = Field(default_factory=list)
    latency: list[LatencyEntry] = Field(default_factory=list)
    decision_path: list[DecisionStep] = Field(default_factory=list)
    tools: list[ToolCall] = Field(default_factory=list)
    errors: list[ErrorEntry] = Field(default_factory=list)
    extras: TurnExtras = Field(default_factory=TurnExtras)
    activity: RunActivity = Field(default_factory=RunActivity)  # what THIS turn triggered
    # The raw, verbatim event stream of this turn + its per-lane histogram.
    # ``events_truncated`` is set when the turn exceeded MAX_RAW_EVENTS_PER_TURN
    # so the UI can say so instead of implying it showed everything.
    events: list[RawEvent] = Field(default_factory=list)
    event_counts: dict[str, int] = Field(default_factory=dict)
    events_truncated: bool = False
    # False when NO usage was recorded for this turn (realtime turns billed at
    # session level emit no BrainTurnCompleted). Lets the UI distinguish
    # "cost zero" from "not measured" instead of printing a misleading 0.
    usage_recorded: bool = False


class RunAnalytics(BaseModel):
    model_config = ConfigDict(extra="ignore")
    total_duration_s: float | None = None
    total_think_ms: int = 0
    total_speak_ms: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    cost_by_provider: dict[str, float] = Field(default_factory=dict)
    tool_counts: dict[str, int] = Field(default_factory=dict)
    interruptions: int = 0
    worst_slo_status: str = "ok"


class RunListItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: str
    started_ms: int
    ended_ms: int | None = None
    duration_s: float | None = None
    hangup_reason: str = ""
    wake_source: str = ""       # voice | hotkey | channel:<name>
    turn_count: int = 0
    total_cost_usd: float = 0.0
    error_count: int = 0
    outcome: str = "success"    # see RUN_OUTCOMES — colors the status dot
    slo_status: str = "ok"      # worst latency across turns (separate perf signal)
    feature_tags: list[str] = Field(default_factory=list)  # computer_use | sub_agent | tool names
    preview: str = ""


class Run(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session: VoiceSessionRow
    outcome: str = "success"    # see RUN_OUTCOMES — worst across turns
    turns: list[RunTurn] = Field(default_factory=list)
    missions: list[MissionRef] = Field(default_factory=list)
    activity: RunActivity = Field(default_factory=RunActivity)
    analytics: RunAnalytics = Field(default_factory=RunAnalytics)
    environment: RunEnvironment = Field(default_factory=RunEnvironment)
    # Session-scoped events (those the recorder stored without a turn id) plus
    # the run-wide event-kind histogram.
    session_events: list[RawEvent] = Field(default_factory=list)
    event_counts: dict[str, int] = Field(default_factory=dict)


__all__ = [
    "RawEvent", "RunEnvironment",
    "TraceEvent", "TranscriptLine", "ToolCall", "LatencyEntry", "DecisionStep",
    "ErrorEntry", "TurnExtras", "MissionRef", "RunActivity", "RunTurn",
    "RunAnalytics", "RunListItem", "Run",
]
