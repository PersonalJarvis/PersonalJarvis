"""Single source of truth for Run-Inspector wire-format enums.

Same anti-drift contract as jarvis/sessions/constants.py (BUG-008 class): these
values cross Python -> Pydantic -> TypeScript -> UI. They are carried as plain
``str`` on the wire (never Pydantic ``Literal``); the parity tests in
tests/unit/runs/test_constants_parity.py and the frontend runEnumParity test
fail on drift between the layers.
"""
from __future__ import annotations

from typing import Final

# --- SLO traffic-light status (latency waterfall) ---------------------
SLO_OK: Final[str] = "ok"
SLO_WARN: Final[str] = "warn"        # >= 80% of the phase budget
SLO_BREACH: Final[str] = "breach"    # > 100% of the phase budget

SLO_STATUSES: Final[tuple[str, ...]] = (SLO_OK, SLO_WARN, SLO_BREACH)

# --- Decision-path step kinds -----------------------------------------
DECISION_TIER: Final[str] = "tier"          # routing tier chosen
DECISION_ROUTE: Final[str] = "route"        # force-spawn / direct heuristic
DECISION_RISK: Final[str] = "risk"          # risk evaluation + approval
DECISION_BRAIN: Final[str] = "brain"        # provider/model that answered
DECISION_MISSION: Final[str] = "mission"    # sub-agent mission spawned
DECISION_FALLBACK: Final[str] = "fallback"  # provider fallback fired

RUN_DECISION_KINDS: Final[tuple[str, ...]] = (
    DECISION_TIER, DECISION_ROUTE, DECISION_RISK,
    DECISION_BRAIN, DECISION_MISSION, DECISION_FALLBACK,
)

# --- Rationale provenance (the honest "why") --------------------------
# Where a DecisionStep's rationale came from. NEVER fabricated:
#   "model" — the brain's own natural-language text emitted next to a tool_use
#             block (ActionProposed.rationale), captured for free.
#   "rule"  — a deterministic, honest explanation the analyzer builds from a
#             CAPTURED fact (approval source, denial reason, provider fallback).
# An empty source means no rationale was available; the UI says so plainly
# rather than guessing. This crosses Python -> Pydantic -> TS -> UI, so it is
# parity-guarded like the other run enums.
RATIONALE_MODEL: Final[str] = "model"
RATIONALE_RULE: Final[str] = "rule"

RATIONALE_SOURCES: Final[tuple[str, ...]] = (RATIONALE_MODEL, RATIONALE_RULE)

# --- Run outcome (distinct from SLO latency) --------------------------
# The functional result of a run, NOT its speed. A slow-but-answered run is a
# success; only a genuine failure (unrecovered error, no answer, denied action)
# is "failed". This is what the run-list status dot colors by — latency lives in
# its own SLO chip so a slow run no longer reads as broken.
OUTCOME_SUCCESS: Final[str] = "success"   # answered, no failures
OUTCOME_PARTIAL: Final[str] = "partial"   # answered, but a tool/action hiccuped
OUTCOME_FAILED: Final[str] = "failed"     # genuine failure / no answer

RUN_OUTCOMES: Final[tuple[str, ...]] = (OUTCOME_SUCCESS, OUTCOME_PARTIAL, OUTCOME_FAILED)

# --- Full-transcript line roles ---------------------------------------
# The gap-less transcript (build_transcript) tags each line with who/what
# produced it. Drives UI styling only; an unknown value degrades to a neutral
# style, never an error.
ROLE_USER: Final[str] = "user"        # the user's transcribed utterance
ROLE_JARVIS: Final[str] = "jarvis"    # every phrase Jarvis voiced (reply + intermediate)
ROLE_SYSTEM: Final[str] = "system"    # state/status + non-spoken diagnostics (exit codes)
ROLE_TOOL: Final[str] = "tool"        # a tool / Computer-Use action outcome
ROLE_ERROR: Final[str] = "error"      # an error / denial surfaced inline

TRANSCRIPT_ROLES: Final[tuple[str, ...]] = (
    ROLE_USER, ROLE_JARVIS, ROLE_SYSTEM, ROLE_TOOL, ROLE_ERROR,
)

# --- Raw-event lanes (the developer event stream) ---------------------
# The Run Inspector's raw stream shows EVERY persisted bus event of a turn.
# A flat list of 200 rows is unreadable, so each event kind is assigned one
# lane the UI can colour and filter by. An unknown kind degrades to
# EVENT_CAT_SYSTEM — never an error (BUG-008 string contract).
EVENT_CAT_LIFECYCLE: Final[str] = "lifecycle"  # session/turn boundaries, wake, state
EVENT_CAT_SPEECH: Final[str] = "speech"        # STT in, TTS out, audio marks
EVENT_CAT_BRAIN: Final[str] = "brain"          # provider/model/tokens/routing
EVENT_CAT_TOOL: Final[str] = "tool"            # tool + CLI calls and their approval
EVENT_CAT_AGENT: Final[str] = "agent"          # Jarvis-Agent / harness / mission
EVENT_CAT_VISION: Final[str] = "vision"        # Computer-Use observe/plan/act/verify
EVENT_CAT_LATENCY: Final[str] = "latency"      # measured spans
EVENT_CAT_ERROR: Final[str] = "error"          # errors, denials, budget stops
EVENT_CAT_SYSTEM: Final[str] = "system"        # everything else

RUN_EVENT_CATEGORIES: Final[tuple[str, ...]] = (
    EVENT_CAT_LIFECYCLE, EVENT_CAT_SPEECH, EVENT_CAT_BRAIN, EVENT_CAT_TOOL,
    EVENT_CAT_AGENT, EVENT_CAT_VISION, EVENT_CAT_LATENCY, EVENT_CAT_ERROR,
    EVENT_CAT_SYSTEM,
)

# kind -> lane. Kinds absent here fall back to EVENT_CAT_SYSTEM.
EVENT_CATEGORY_BY_KIND: Final[dict[str, str]] = {
    # lifecycle
    "VoiceSessionStarted": EVENT_CAT_LIFECYCLE,
    "VoiceSessionEnded": EVENT_CAT_LIFECYCLE,
    "VoiceTurnStarted": EVENT_CAT_LIFECYCLE,
    "VoiceTurnCompleted": EVENT_CAT_LIFECYCLE,
    "RealtimeSessionReady": EVENT_CAT_LIFECYCLE,
    "WakeWordDetected": EVENT_CAT_LIFECYCLE,
    "HotkeyPressed": EVENT_CAT_LIFECYCLE,
    "ListeningStarted": EVENT_CAT_LIFECYCLE,
    "SystemStateChanged": EVENT_CAT_LIFECYCLE,
    # speech
    "TranscriptFinal": EVENT_CAT_SPEECH,
    "TranscriptionUpdate": EVENT_CAT_SPEECH,
    "UtteranceCaptured": EVENT_CAT_SPEECH,
    "SpeechSpoken": EVENT_CAT_SPEECH,
    "ResponseGenerated": EVENT_CAT_SPEECH,
    "AudioOutFirst": EVENT_CAT_SPEECH,
    # brain
    "IntentClassified": EVENT_CAT_BRAIN,
    "BrainTurnStarted": EVENT_CAT_BRAIN,
    "BrainTurnCompleted": EVENT_CAT_BRAIN,
    "BrainTTFT": EVENT_CAT_BRAIN,
    "BrainProviderSwitched": EVENT_CAT_BRAIN,
    "FrontierModelSwitched": EVENT_CAT_BRAIN,
    "BrainToolsChanged": EVENT_CAT_BRAIN,
    # tool
    "ActionProposed": EVENT_CAT_TOOL,
    "ActionApprovalRequired": EVENT_CAT_TOOL,
    "ActionApproved": EVENT_CAT_TOOL,
    "ActionExecuted": EVENT_CAT_TOOL,
    "ToolCallStarted": EVENT_CAT_TOOL,
    "ToolCallCompleted": EVENT_CAT_TOOL,
    "CliInvoked": EVENT_CAT_TOOL,
    "CliInvocationFinished": EVENT_CAT_TOOL,
    # agent
    "JarvisAgentTaskStarted": EVENT_CAT_AGENT,
    "JarvisAgentReviewTriggered": EVENT_CAT_AGENT,
    "JarvisAgentTaskCompleted": EVENT_CAT_AGENT,
    "JarvisAgentAnnouncement": EVENT_CAT_AGENT,
    "HarnessDispatched": EVENT_CAT_AGENT,
    "HarnessCompleted": EVENT_CAT_AGENT,
    "MissionCompleted": EVENT_CAT_AGENT,
    # vision / Computer-Use
    "ObservationCaptured": EVENT_CAT_VISION,
    "VisionInjected": EVENT_CAT_VISION,
    "ActionPlanned": EVENT_CAT_VISION,
    "ActionVerified": EVENT_CAT_VISION,
    "CUStepProfiled": EVENT_CAT_VISION,
    "CUControlStarted": EVENT_CAT_VISION,
    "CUControlEnded": EVENT_CAT_VISION,
    # latency
    "LatencySpan": EVENT_CAT_LATENCY,
    # error
    "ErrorOccurred": EVENT_CAT_ERROR,
    "ActionDenied": EVENT_CAT_ERROR,
    "BudgetWarning": EVENT_CAT_ERROR,
    "BudgetExceeded": EVENT_CAT_ERROR,
    "TaskCancelled": EVENT_CAT_ERROR,
}

# Hard cap on raw events surfaced per turn. A long Computer-Use turn can emit
# thousands of steps; the payload stays bounded and the UI states plainly that
# it truncated (never a silent cut).
MAX_RAW_EVENTS_PER_TURN: Final[int] = 500

__all__ = [
    "SLO_OK", "SLO_WARN", "SLO_BREACH", "SLO_STATUSES",
    "DECISION_TIER", "DECISION_ROUTE", "DECISION_RISK",
    "DECISION_BRAIN", "DECISION_MISSION", "DECISION_FALLBACK",
    "RUN_DECISION_KINDS",
    "RATIONALE_MODEL", "RATIONALE_RULE", "RATIONALE_SOURCES",
    "OUTCOME_SUCCESS", "OUTCOME_PARTIAL", "OUTCOME_FAILED", "RUN_OUTCOMES",
    "ROLE_USER", "ROLE_JARVIS", "ROLE_SYSTEM", "ROLE_TOOL", "ROLE_ERROR",
    "TRANSCRIPT_ROLES",
    "EVENT_CAT_LIFECYCLE", "EVENT_CAT_SPEECH", "EVENT_CAT_BRAIN",
    "EVENT_CAT_TOOL", "EVENT_CAT_AGENT", "EVENT_CAT_VISION",
    "EVENT_CAT_LATENCY", "EVENT_CAT_ERROR", "EVENT_CAT_SYSTEM",
    "RUN_EVENT_CATEGORIES", "EVENT_CATEGORY_BY_KIND",
    "MAX_RAW_EVENTS_PER_TURN",
]
