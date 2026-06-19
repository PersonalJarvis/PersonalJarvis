"""Read-only DTOs for the Run Inspector. No SQL, no schema — these compose
existing rows (VoiceSessionRow/VoiceTurnRow) plus fields derived by analyzer.py.

All enum-like fields are plain ``str`` (never Literal) so an unknown value
degrades to a UI fallback instead of an HTTP 500 — see jarvis/runs/constants.py
and the BUG-008 history."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from jarvis.sessions.models import VoiceSessionRow


class TraceEvent(BaseModel):
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
    timeline: list[TraceEvent] = Field(default_factory=list)
    latency: list[LatencyEntry] = Field(default_factory=list)
    decision_path: list[DecisionStep] = Field(default_factory=list)
    tools: list[ToolCall] = Field(default_factory=list)
    errors: list[ErrorEntry] = Field(default_factory=list)
    extras: TurnExtras = Field(default_factory=TurnExtras)
    activity: RunActivity = Field(default_factory=RunActivity)  # what THIS turn triggered


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


__all__ = [
    "TraceEvent", "TranscriptLine", "ToolCall", "LatencyEntry", "DecisionStep",
    "ErrorEntry", "TurnExtras", "MissionRef", "RunActivity", "RunTurn",
    "RunAnalytics", "RunListItem", "Run",
]
