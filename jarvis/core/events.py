"""Event dataclasses for the internal event bus.

All events are immutable (``frozen=True``) so they can be serialised by the
flight recorder and identically reconstructed for debug replay.
The ``trace_id`` correlation key links all events belonging to a single
conversation turn.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from .protocols import HarnessResult, HarnessTask, RiskTier, Transcript


def _now_ns() -> int:
    return time.time_ns()


def _new_trace() -> UUID:
    return uuid4()


# ----------------------------------------------------------------------
# Base
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Event:
    """Base class for all events — carries correlation and timing information."""
    trace_id: UUID = field(default_factory=_new_trace)
    timestamp_ns: int = field(default_factory=_now_ns)
    source_layer: str = ""


# ----------------------------------------------------------------------
# Trigger & Speech
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HotkeyPressed(Event):
    combo: str = ""


@dataclass(frozen=True, slots=True)
class WakeWordDetected(Event):
    keyword: str = ""
    confidence: float = 0.0


@dataclass(frozen=True, slots=True)
class ListeningStarted(Event):
    """Jarvis opens the microphone for an utterance."""
    pass


@dataclass(frozen=True, slots=True)
class UtteranceCaptured(Event):
    audio_ref: str = ""      # Content-Hash für Flight-Recorder
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class TranscriptPartial(Event):
    transcript: Transcript | None = None


@dataclass(frozen=True, slots=True)
class TranscriptFinal(Event):
    transcript: Transcript | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionUpdate(Event):
    text: str = ""
    is_final: bool = False


@dataclass(frozen=True, slots=True)
class DictationTranscript(Event):
    """Live transcript from the chat composer's mic-dictation button.

    Deliberately a SEPARATE event from ``TranscriptionUpdate`` (which rides the
    live voice critical path). Dictation only fills the chat text input — it
    never reaches the brain — so keeping it on its own event name means the
    frontend can route it straight to the textarea without ever confusing it
    with a real voice turn, and the voice hot-path event stays untouched.

    ``is_final=False`` interim hypotheses overwrite the live tail; the single
    ``is_final=True`` is appended to the input box and ends the dictation.
    """

    text: str = ""
    is_final: bool = False


# ----------------------------------------------------------------------
# Intent & Routing
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IntentClassified(Event):
    intent: str = ""         # "ask" | "execute" | "recall" | "interrupt" | "switch_provider"
    risk_tier: RiskTier = "safe"
    entities: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BrainProviderSwitched(Event):
    from_provider: str = ""
    to_provider: str = ""


@dataclass(frozen=True, slots=True)
class FrontierModelSwitched(Event):
    """The main Jarvis provider detected a newer model from the /v1/models endpoint
    and switched to it automatically. The frontend shows a blocking modal that the
    user must confirm with OK — the switch is already live (non-blocking)."""
    provider: str = ""
    tier: str = ""        # "fast" | "deep"
    old_model: str = ""
    new_model: str = ""


@dataclass(frozen=True, slots=True)
class SecretConfigured(Event):
    """Fired after a secret has been saved or deleted in the Credential Manager.

    The UI listens to this event to switch provider cards live between
    "configured" and "not configured" without a page reload. The actual secret
    value is NEVER written into the event.
    """
    key: str = ""
    action: str = "set"  # "set" | "delete"


@dataclass(frozen=True, slots=True)
class UiLanguageChanged(Event):
    """Fired when the interface (display) language changes.

    The frontend listens for this over ``/ws`` (wildcard-forwarded) and switches
    its i18n language live — every label/button/message — without a page reload.
    Emitted by the settings endpoint and (indirectly, via ``ConfigReloaded``) by
    a voice command / the Control API. Distinct from the reply language.
    """
    language: str = ""  # "en" | "de" | "es"


# ----------------------------------------------------------------------
# Action-Lifecycle
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ActionProposed(Event):
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    risk_tier: RiskTier = "safe"


@dataclass(frozen=True, slots=True)
class ActionApproved(Event):
    tool_name: str = ""
    approved_by: str = "auto"  # "auto" | "user" | "whitelist"


@dataclass(frozen=True, slots=True)
class ActionDenied(Event):
    tool_name: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ActionExecuted(Event):
    tool_name: str = ""
    success: bool = False
    duration_ms: int = 0
    error: str | None = None


# ----------------------------------------------------------------------
# Harness Dispatch
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class HarnessDispatched(Event):
    harness: str = ""
    task: HarnessTask | None = None


@dataclass(frozen=True, slots=True)
class HarnessProgress(Event):
    harness: str = ""
    result: HarnessResult | None = None


@dataclass(frozen=True, slots=True)
class HarnessCompleted(Event):
    harness: str = ""
    result: HarnessResult | None = None


# ----------------------------------------------------------------------
# Response & Memory
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ResponseGenerated(Event):
    text: str = ""
    language: str = ""
    audio_ref: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryUpdated(Event):
    namespace: str = ""
    key: str = ""
    operation: str = "put"   # "put" | "forget"


@dataclass(frozen=True, slots=True)
class ProfileUpdated(Event):
    """The Curator wrote a fact to USER.md / people/*.md.

    The UI may render this as a badge "Jarvis learned X about you" —
    transparency is part of the design (the user should never be surprised).
    """
    subject: str = ""           # "user" | "person:laura" | "soul"
    cluster: str = ""           # identity | communication | work_style | ...
    field: str = ""             # z.B. "humor_types" oder "observation"
    operation: str = "set"      # set | append | observation
    confidence: float = 1.0
    evidence: str = ""


# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ConfigReloaded(Event):
    changed_keys: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class SystemStarted(Event):
    version: str = ""


@dataclass(frozen=True, slots=True)
class SystemStopping(Event):
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SystemStateChanged(Event):
    """High-level supervisor state — rendered by the UI as a pulse badge."""
    new_state: str = "IDLE"         # IDLE | LISTENING | THINKING | SPEAKING | ERROR | PAUSED
    previous: str = "IDLE"


@dataclass(frozen=True, slots=True)
class NavigateSidebar(Event):
    """Ask the desktop UI to switch the active sidebar section.

    Emitted by the ``navigate`` router tool so a spoken/typed command
    ("zeig die Socials", "open settings") moves the UI. The frontend
    (``useWebSocket.ts``) listens for event_name ``NavigateSidebar`` and calls
    ``setActiveSection`` when ``section`` is a known ``SectionId``; an unknown
    id is a graceful no-op there. ``section`` mirrors the frontend
    ``SECTION_IDS`` (``store/events.ts``) — kept in sync via the navigate tool's
    parity test.
    """
    section: str = ""


# ----------------------------------------------------------------------
# UI / Chat
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ThreadCreated(Event):
    thread_id: str = ""
    title: str = ""


@dataclass(frozen=True, slots=True)
class MessageSent(Event):
    """User message — originates from the web UI or the voice pipeline."""
    thread_id: str = ""
    role: str = "user"              # "user" | "assistant" | "system"
    text: str = ""


# ----------------------------------------------------------------------
# Terminal (Desktop-App PTY-Bridge)
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TerminalSpawned(Event):
    """A new PTY session was started."""
    terminal_id: str = ""
    shell_id: str = ""
    pid: int = 0


@dataclass(frozen=True, slots=True)
class TerminalOutput(Event):
    """Byte chunk from the PTY — streamed to the UI."""
    terminal_id: str = ""
    data: str = ""


@dataclass(frozen=True, slots=True)
class TerminalClosed(Event):
    """The PTY process has exited (or was closed)."""
    terminal_id: str = ""
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class TerminalCommandExecuted(Event):
    """Audit event — emitted on Enter key press (\\r).

    Heuristic: a line buffer is maintained per session, flushed on \\r or \\n.
    For TUI apps (vim, htop) this may occasionally contain garbage — sufficient
    for pure audit tracking nonetheless.
    """
    terminal_id: str = ""
    shell_id: str = ""
    command: str = ""


# ----------------------------------------------------------------------
# Phase 5 — Kill / Cost / Observation / Task / Admin
# ----------------------------------------------------------------------

# Announcement (CL-3, formerly Sub-Jarvis lifecycle, now OpenClaw)

@dataclass(frozen=True, slots=True)
class AnnouncementRequested(Event):
    """The RouterBrain (or a tool such as `spawn_worker`) wants to deliver a
    short interstitial announcement to the user — e.g. "Starting a sub-agent…".
    Concrete, content-bearing announcements are permitted; empty or generic ACKs
    are suppressed by the pipeline.

    The TTS pipeline listens to this event. ``priority="interrupt"`` interrupts
    ongoing speech; ``priority="normal"`` queues behind it.
    """
    text: str = ""
    # ruff/UP037 suggests using "normal"/"interrupt" as bare names —
    # that is exactly wrong for `Literal[...]`; the strings ARE the values.
    priority: Literal["normal", "interrupt"] = "normal"  # noqa: UP037
    language: str = "de"
    # Discriminator for the new ack_brain Flash-Brain producer. None keeps
    # backwards compatibility with the existing MissionAnnouncer callers
    # that only pass text+priority+language.
    kind: Literal["preamble", "completion", "info"] | None = None  # noqa: UP037


# Voice mute (user-facing toggle, e.g. mascot double-click)

@dataclass(frozen=True, slots=True)
class VoiceMuteToggleRequested(Event):
    """User requested a global voice-mute toggle.

    Publishers: desktop-mascot double-click handler
    (``jarvis/overlay/integration.py``), orb double-click bridge
    (``ui/orb/bus_bridge.py``), future hotkey/voice-pattern surfaces.
    The pipeline owns the actual flip in ``_on_mute_toggle_requested`` —
    callers do not have to know the current state; the handler is
    idempotent (mute → unmute → mute).

    ``source`` is free-form for telemetry / forensic replay
    (e.g. ``"mascot_dblclick"``, ``"orb_dblclick_double"``, ``"hotkey"``).
    """
    source: str = ""


# Show / raise the main desktop window (user-facing gesture, e.g. an overlay
# right-click).

@dataclass(frozen=True, slots=True)
class ShowWindowRequested(Event):
    """User asked to bring the Jarvis desktop window to the foreground.

    Publishers: the overlay right-click gesture for BOTH surfaces — the
    whisper-bar and the mascot orb — wired through ``OrbBusBridge``
    (``ui/orb/bus_bridge.py``). The DesktopApp owns the actual window raise
    in ``_on_show_window_requested`` → ``_safe_window_show`` and is null-safe
    when there is no window (headless / VPS), so an unwired publish is a no-op.

    ``source`` is free-form for telemetry / forensic replay
    (e.g. ``"overlay_rightclick"``).
    """
    source: str = ""


@dataclass(frozen=True, slots=True)
class VoiceMuteChanged(Event):
    """Authoritative broadcast that the global voice-mute state flipped.

    Emitted by the speech pipeline AFTER the flag has been updated in
    memory and in-flight audio has been stopped. UI surfaces (overlay
    mascot, orb, tray badge) subscribe to this event so every mirror
    of the mute icon stays in lock-step with the pipeline — there is
    only ONE writer of mute state, the pipeline, and ONE event everyone
    else listens to. AP-OC-style multi-writer drift is impossible by
    construction.
    """
    muted: bool = False
    source: str = ""


# Kill-Switch (ADR-0004)

@dataclass(frozen=True, slots=True)
class KillRequested(Event):
    """An emergency stop was triggered (hotkey, voice, tray, web UI button)."""
    source: str = ""                    # "hotkey" | "voice" | "tray" | "web"
    reason: str = "user_request"


@dataclass(frozen=True, slots=True)
class KillAcknowledged(Event):
    """A subscriber (CancelToken holder) confirms it has observed the kill signal."""
    holder: str = ""                    # "cu_loop" | "brain_stream" | "task_runner" | ...
    took_ms: int = 0                    # Zeit zwischen KillRequested und Ack


@dataclass(frozen=True, slots=True)
class TaskCancelled(Event):
    """Eine konkrete Task/Operation wurde durch den Kill gestoppt."""
    task_id: str = ""
    reason: str = "kill_switch"


# Cost-Breaker (ADR-0006)

@dataclass(frozen=True, slots=True)
class BudgetWarning(Event):
    """80 % pre-warning. The UI should display this as a banner."""
    scope: str = "task"                 # "task" | "daily"
    spent_eur: float = 0.0
    limit_eur: float = 0.0


@dataclass(frozen=True, slots=True)
class BudgetExceeded(Event):
    """Budget exceeded — the CancelToken has been set."""
    scope: str = "task"
    spent_eur: float = 0.0
    limit_eur: float = 0.0


@dataclass(frozen=True, slots=True)
class CooldownStarted(Event):
    until_ns: int = 0
    reason: str = "budget_daily_exceeded"


@dataclass(frozen=True, slots=True)
class CooldownEnded(Event):
    pass


# Vision / Computer-Use (Capability 1 + 2)

@dataclass(frozen=True, slots=True)
class ObservationCaptured(Event):
    """The vision engine produced a new observation snapshot."""
    source: str = "composite"           # matches VisionSource.kind
    window_title: str = ""
    node_count: int = 0
    screenshot_hash: str = ""
    screenshot_path: str | None = None


@dataclass(frozen=True, slots=True)
class VisionInjected(Event):
    """The RouterBrain injected a screen observation as an image block into the
    user message (permanent vision, router-permanent-vision).

    Emitted by ``RouterBrain.handle()`` immediately before the BrainManager
    call. Telemetry for cost tracking, flight recorder, and debugging.
    """
    screenshot_hash: str = ""
    bytes_size: int = 0                 # Groesse des PNG-Rohdatenblocks
    capture_age_ms: int = 0             # Alter der Observation bei Inject


@dataclass(frozen=True, slots=True)
class ActionPlanned(Event):
    """The CU loop planner proposed the next action (before execution)."""
    action_kind: str = ""               # "click" | "type" | "hotkey" | "wait" | "verify"
    target_hint: str = ""               # z.B. "{role:Button,name:Speichern}"


@dataclass(frozen=True, slots=True)
class ActionVerified(Event):
    """Post-execution verify: did the action produce the expected effect?"""
    action_kind: str = ""
    success: bool = False
    reason: str = ""                    # bei Fail: was hat der Verify-Observer gesehen?


# Task-Queue (Capability 4)

@dataclass(frozen=True, slots=True)
class TaskScheduled(Event):
    task_id: str = ""
    trigger_type: str = ""              # "after_delay" | "at_time" | "on_event"
    due_at_ns: int = 0
    title: str = ""


@dataclass(frozen=True, slots=True)
class TaskStarted(Event):
    task_id: str = ""


@dataclass(frozen=True, slots=True)
class TaskStepRecorded(Event):
    task_id: str = ""
    seq: int = 0
    kind: str = ""                      # "observation" | "action" | "verify" | "log"


@dataclass(frozen=True, slots=True)
class TaskCompleted(Event):
    task_id: str = ""
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class TaskFailed(Event):
    task_id: str = ""
    error: str = ""
    will_retry: bool = False


@dataclass(frozen=True, slots=True)
class TaskInterrupted(Event):
    """Found on app startup: the task was in state 'running'. The plan of record
    is to clean it up on startup (ADR-0003).
    """
    task_id: str = ""


# Admin-Operations (Capability 3)

@dataclass(frozen=True, slots=True)
class AdminOperationRequested(Event):
    op_id: str = ""                     # UUID des Requests
    op_type: str = ""                   # "install_winget" | ...
    destructive: bool = False


@dataclass(frozen=True, slots=True)
class AdminOperationCompleted(Event):
    op_id: str = ""
    op_type: str = ""
    success: bool = False
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class AdminOperationRejected(Event):
    """The user declined the destructive prompt, the HMAC validation failed,
    or the operation type is not on the allowlist.
    """
    op_id: str = ""
    op_type: str = ""
    reason: str = ""                    # "user_declined" | "hmac_invalid" | "not_whitelisted" | ...


# ----------------------------------------------------------------------
# CLI-Integration
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CliStatusChanged(Event):
    """The status of a CLI changed (installed/connected/error)."""
    cli_name: str = ""
    old_status: str = ""          # connected/disconnected/not_installed/error/checking
    new_status: str = ""
    version: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CliInstallProgress(Event):
    """Streaming install output (emitted after every stdout line)."""
    cli_name: str = ""
    job_id: str = ""
    line: str = ""
    done: bool = False
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class CliConnectProgress(Event):
    """Streaming connect output for OAuth flows."""
    cli_name: str = ""
    job_id: str = ""
    line: str = ""
    step: str = ""                # browser_open / polling / done / cancelled / timeout
    done: bool = False


@dataclass(frozen=True, slots=True)
class CliInvoked(Event):
    """The brain or user invoked a CLI (drives the pulse indicator in the UI)."""
    cli_name: str = ""
    caller: str = ""              # brain / user / skill:<name>
    command_preview: str = ""


@dataclass(frozen=True, slots=True)
class CliInvocationFinished(Event):
    """Companion event to ``CliInvoked`` — triggers history invalidation in the UI."""
    cli_name: str = ""
    exit_code: int | None = None
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class BrainToolsChanged(Event):
    """The brain tool set changed at runtime.

    Published when a new CLI is connected/registered (or disconnected) —
    the BrainManager refreshes its tool dict from the factory so the sub-brain
    knows about the new CLI on the next turn without requiring a Jarvis restart.

    ``reason`` is for flight-recorder debugging: which event triggered the
    refresh (``"cli_connected:vercel"``, ``"custom_registered:myapp"``, …).
    """
    reason: str = ""


# ----------------------------------------------------------------------
# Error
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ErrorOccurred(Event):
    layer: str = ""
    error_type: str = ""
    message: str = ""
    recoverable: bool = True



# ----------------------------------------------------------------------
# Workflows (Phase 6 — AI-Agent-Orchestration-Dashboard)
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class WorkflowScheduled(Event):
    """A workflow was scheduled (cron or manual registration).

    The UI uses this to update the ``Next Run`` timestamp in the dashboard card
    without polling.
    """
    workflow_id: str = ""
    next_run_ns: int = 0
    reason: str = "cron_next"       # "cron_next" | "registered" | "toggled_on"


@dataclass(frozen=True, slots=True)
class WorkflowStarted(Event):
    """A workflow run is starting — either manually or triggered by cron."""
    workflow_id: str = ""
    run_id: str = ""
    trigger: str = "manual"         # "manual" | "cron" | "event"
    title: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowStepStarted(Event):
    run_id: str = ""
    step_index: int = 0
    kind: str = ""                  # "brain_prompt" | "harness_dispatch" | "speak" | "tool_call"
    label: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowStepCompleted(Event):
    run_id: str = ""
    step_index: int = 0
    success: bool = False
    duration_ms: int = 0
    output_preview: str = ""        # max 240 Zeichen, fuer UI-Timeline
    error: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowCompleted(Event):
    workflow_id: str = ""
    run_id: str = ""
    success: bool = False
    duration_ms: int = 0
    error: str | None = None


# ----------------------------------------------------------------------
# OpenClaw Task Dashboard (Welle-4-Migration aus Phase 5.5 Sub-Jarvis)
# ----------------------------------------------------------------------
# Wave 4 Sub-Jarvis cleanup (see docs/openclaw-bridge.md §11):
# The Sub-Jarvis tier was replaced by the OpenClaw bridge. Event schemas are
# preserved 1:1 — only the class names changed to OpenClaw*.

@dataclass(frozen=True, slots=True)
class OpenClawTaskStarted(Event):
    parent_trace_id: UUID | None = None
    utterance: str = ""
    context_hints: list[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    max_duration_s: int = 0
    depth: int = 0


@dataclass(frozen=True, slots=True)
class OpenClawReviewTriggered(Event):
    iteration: int = 0


@dataclass(frozen=True, slots=True)
class OpenClawTaskCompleted(Event):
    success: bool = False
    summary: str = ""
    full_log_len: int = 0
    duration_s: float = 0.0
    cost_estimate_usd: float = 0.0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BrainTurnStarted(Event):
    parent_trace_id: UUID | None = None
    provider: str = ""
    model: str = ""
    intent_level: str = ""
    system_prompt_preview: str = ""


@dataclass(frozen=True, slots=True)
class BrainTurnCompleted(Event):
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    text_len: int = 0
    finish_reason: str = ""
    # 2026-04-29 Bug-C-Fix: include provider/model in the Completed event so that
    # the SessionRecorder only writes the SUCCESSFUL provider to voice_turns
    # (not the last fallback attempt). BrainTurnStarted may be published
    # multiple times per turn (fallback chain), but Completed is emitted only
    # when the stream actually delivered tokens.
    provider: str = ""
    model: str = ""


@dataclass(frozen=True, slots=True)
class BrainTTFT(Event):
    """Time-To-First-Token vom Brain.

    ``cache_hit`` aus ``response.usage.cache_read_input_tokens > 0``.
    """
    cache_hit: bool = False
    model: str = ""


@dataclass(frozen=True, slots=True)
class AudioOutFirst(Event):
    """The WASAPI player sent the first sample to the output device.

    Last stage event of a voice turn; marks TTFW = audio audible to the user.
    """
    pass


# ----------------------------------------------------------------------
# Latency instrumentation (Wave 0 — omni-latency suite)
# ----------------------------------------------------------------------

class LatencyPhase(StrEnum):
    """Single source of truth for hot-path latency span names.

    StrEnum members ARE strings, so they serialize cleanly into the
    FlightRecorder JSONL. Adding a phase here is the ONLY place a new phase
    name is defined — the ``LatencySpan.__post_init__`` guard rejects anything
    not listed, which stops the BUG-008 enum-drift class on this wire vocab.
    """

    STT_FINALIZE = "stt_finalize"
    INTENT_DECISION = "intent_decision"
    ACK_FIRST_TOKEN = "ack_first_token"  # noqa: S105 — phase name, not a secret
    ACK_FIRST_AUDIO = "ack_first_audio"
    BRAIN_FIRST_TOKEN = "brain_first_token"  # noqa: S105 — phase name, not a secret
    BRAIN_FIRST_AUDIO = "brain_first_audio"
    TURN_TO_FIRST_AUDIO = "turn_to_first_audio"
    # LATENCY_REPORT_001 t0..t9 diagnostic milestones.
    STT_FIRST_PARTIAL = "stt_first_partial"
    BRAIN_REQUEST_SENT = "brain_request_sent"  # noqa: S105
    BRAIN_LAST_TOKEN = "brain_last_token"  # noqa: S105
    TTS_REQUEST_SENT = "tts_request_sent"  # noqa: S105
    TTS_FIRST_CHUNK = "tts_first_chunk"
    TTS_STREAM_DONE = "tts_stream_done"


_LATENCY_PHASE_VALUES: frozenset[str] = frozenset(p.value for p in LatencyPhase)


@dataclass(frozen=True, slots=True)
class LatencySpan(Event):
    """A single measured interval on the voice hot path.

    ``duration_ms`` is computed from ``perf_counter`` deltas (monotonic) while
    ``timestamp_ns`` (Event base) stays wall-clock for the recorder.
    ``t_start_ns``/``t_end_ns`` are ``perf_counter_ns`` readings for precise
    downstream aggregation (p50/p95).
    """

    phase: str = ""
    duration_ms: float = 0.0
    t_start_ns: int = 0
    t_end_ns: int = 0
    detail: str = ""

    def __post_init__(self) -> None:
        if self.phase not in _LATENCY_PHASE_VALUES:
            raise ValueError(f"unknown latency phase: {self.phase!r}")


@dataclass(frozen=True, slots=True)
class LatencyTurnComplete(Event):
    """All per-turn latency marks have been emitted — writer may flush a row.

    LATENCY_REPORT_001 deliverable. Carries the per-turn anchor + a snapshot
    of stage offsets (ms from anchor) so the JSONL writer never has to race
    against late-arriving LatencySpan events.
    """

    anchor_ns: int = 0
    stages_ms: dict[str, float] = field(default_factory=dict)
    stt_input_audio_ms: float = -1.0
    brain_input_tokens: int = -1
    brain_output_tokens: int = -1
    tts_input_chars: int = -1
    errors: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class VoiceSessionStarted(Event):
    """Wake word detected — a new voice session is starting."""
    session_id: str = ""
    wake_keyword: str = ""
    language: str = "de"


@dataclass(frozen=True, slots=True)
class VoiceTurnStarted(Event):
    """A new turn within the active session is starting."""
    session_id: str = ""
    turn_id: str = ""
    turn_index: int = 0


@dataclass(frozen=True, slots=True)
class VoiceTurnCompleted(Event):
    """Turn complete — Jarvis has replied, pipeline returns to LISTENING."""
    session_id: str = ""
    turn_id: str = ""
    user_text: str = ""
    user_lang: str = "de"
    jarvis_text: str = ""
    jarvis_lang: str = "de"
    tier: str = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class VoiceSessionEnded(Event):
    """Session ended (voice_pattern / hotkey / idle_timeout / shutdown / error)."""
    session_id: str = ""
    hangup_reason: str = ""
    turn_count: int = 0
    duration_s: float = 0.0


@dataclass(frozen=True, slots=True)
class ToolCallStarted(Event):
    parent_trace_id: UUID | None = None
    tool_name: str = ""
    args_preview: str = ""


@dataclass(frozen=True, slots=True)
class ToolCallCompleted(Event):
    success: bool = False
    duration_ms: float = 0.0
    output_preview: str = ""
    error: str | None = None


@dataclass(frozen=True, slots=True)
class OpenClawBackgroundCompleted(Event):
    """A background OpenClaw task finished — TTS should speak proactively.

    Separate from ``OpenClawTaskCompleted`` for pipeline/UI feedback without
    a standardised voice announcement.
    """
    success: bool = False
    utterance: str = ""       # was der User urspruenglich gesagt hatte
    summary: str = ""          # TTS-tauglich, max 120 Tokens
    error: str | None = None
    duration_s: float = 0.0


@dataclass(frozen=True, slots=True)
class OpenClawAnnouncement(Event):
    """OpenClaw spawn start signal for UI/telemetry, without a voice ACK."""
    action: str = ""   # z.B. "eine Flask-App baut"
    target: str = ""   # z.B. "auf Port 8000"


# ----------------------------------------------------------------------
# Board (Phase B) — Achievement-System
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AchievementUnlocked(Event):
    """An achievement was just unlocked — the UI shows a toast.

    Published by the ``AchievementEvaluator`` (jarvis/board/evaluator.py),
    exactly once per achievement — the underlying DB uses ``INSERT OR IGNORE``
    on ``achievements.id`` so double-unlocks do not produce double events.

    ``evidence`` is a JSON-serialisable dict with the causal context
    (e.g. ``trace_id``, ``tool_name``, or a count threshold).
    """
    achievement_id: str = ""
    title: str = ""
    description: str = ""
    tier: str = "mastery"        # "mastery" | "reflection" | "social"
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BioFeedbackRecorded(Event):
    """The user clicked a reaction button under the AI profile.

    Emitted by the ``POST /api/board/bio/feedback`` endpoint. Three kinds:
    ``trifft`` (bio feels accurate), ``trifft_nicht`` (off the mark),
    ``haerter`` (make the next bio more pointed). The signal flows as a
    ``feedback_vector_block`` into the bio prompt for the next generation;
    no immediate regeneration.
    """
    bio_generated_at: str = ""
    kind: str = ""        # "trifft" | "trifft_nicht" | "haerter"


# ----------------------------------------------------------------------
# Awareness Layer (Phase A0+)
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FrameUpdated(Event):
    """A new L1 frame was captured and written to the AwarenessState.

    Emitted by the ``WindowFocusWatcher`` (Phase A1). The PrivacyFilter verdict
    is already applied — when ``is_capture_allowed=False`` the frame was still
    registered (window title + process), but deeper capture (pixels, UIA tree)
    is blocked in later phases.
    """
    window_title: str = ""
    process_name: str = ""
    pid: int = 0
    is_capture_allowed: bool = True


@dataclass(frozen=True, slots=True)
class EpisodeRecorded(Event):
    """An L2 episode was condensed and persisted to SQLite.

    Defined in A0 only; populated by the ``StoryTracker`` in A2.
    ``summary_preview`` is capped at ~80 characters for the UI pulse;
    the full ``summary`` text lives in ``awareness_episodes.summary``.
    """
    episode_id: int = 0
    summary_preview: str = ""
    primary_app: str = ""
    frame_count: int = 0
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class ContextSwitched(Event):
    """Working-set change: a different project/task context was detected.

    Defined in A0 only; populated by ``WorkingSet`` in A4. Fields contain
    ``Context.task_label`` values (e.g. ``"pipeline.py - jarvis"``).
    """
    from_context: str = ""
    to_context: str = ""


@dataclass(frozen=True, slots=True)
class IdleEntered(Event):
    """The user has had no mouse/keyboard input for ``idle_threshold_minutes``.

    On receiving this event the ``StoryTracker`` (A2) flushes the running
    episode so it is not lost — idle == episode boundary.
    """
    idle_since_ns: int = 0


@dataclass(frozen=True, slots=True)
class IdleExited(Event):
    """User input detected again after an idle phase."""
    was_idle_for_ms: int = 0


@dataclass(frozen=True, slots=True)
class AwarenessCaptureBlocked(Event):
    """The PrivacyFilter marked a frame as not capturable.

    ``reason`` is a pattern or default verdict (e.g.
    ``matched_blocked_title:*Banking*`` or ``default_block_for_browser``).
    The frame is NOT emitted as ``FrameUpdated`` — anyone who needs both events
    must use ``subscribe_all()`` (the flight-recorder pattern).
    """
    window_title: str = ""
    process_name: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class FileSaved(Event):
    """Phase A5: the FileSystemWatcher detected a file save in an active project root.

    Emitted by the ``FileSystemProbe`` (watchdog). The ``StoryTracker`` subscribes
    optionally and adds it as a high-salience event to the running builder
    (``SalienceScorer.score_event('FileSaved') = 40``).
    """
    path: str = ""
    process_name: str = ""    # active process at the time, optional
    repo_root: str = ""       # project root that was watched


# ----------------------------------------------------------------------
# Wiki Live-Reload (Phase B3 — Desktop Wiki View, Agent D)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WikiPageChanged(Event):
    """A markdown file inside the wiki vault changed on disk.

    Emitted by :class:`jarvis.memory.wiki.watcher.WikiWatcher` after a
    debounced filesystem event in one of the watched sub-folders
    (``entities/``, ``concepts/``, ``projects/``, ``sessions/``). The
    desktop wiki view's WebSocket endpoint forwards this event to the
    frontend so React Query caches can be invalidated immediately.

    ``path`` is the vault-relative POSIX path (e.g. ``"entities/sam.md"``)
    so the frontend can use the string as-is regardless of the host
    operating system path separator.

    ``kind`` is one of ``"created" | "modified" | "deleted"``.
    """
    slug: str = ""
    path: str = ""
    kind: str = ""


# ----------------------------------------------------------------------
# Visible-Feedback Contract (ADR-0016)
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UserVisibleFeedback(Event):
    """Generalised "did the user actually receive the feedback?" event.

    ADR-0016 contract: every UI surface that the runtime intends the user
    to see (orb, TTS audio, toast, tray balloon) publishes one of these
    after the attempted side-effect, with a post-effect ``observed``
    snapshot the runtime can compare to ``expected``. A flight-recorder
    consumer can compute drift in batch; a live subscriber can react.

    Fields:
      - ``surface``: stable identifier of the UI channel
        (``"orb" | "tts" | "toast" | "tray"`` etc.). Free-form string for
        forward-compatibility; consumers do exact-match dispatch.
      - ``expected``: what the runtime intended to make visible / audible.
        Surface-specific dict. Orb: ``{"mode": "listen", "viewable": True}``.
        TTS: ``{"audible": True, "voice": "..."}``.
      - ``observed``: what was measurable post-effect. Orb:
        ``{"viewable": int, "geometry": "<wxh+x+y>"}``. TTS:
        ``{"audible_ts_ns": int}``.
      - ``correlation_id``: links back to the triggering event
        (``WakeWordDetected.trace_id`` for orb-show on wake, etc.).

    First adopter (this commit): orb. Future adopters MUST publish from
    their actual side-effect site (not the call site that scheduled it),
    so ``expected`` vs ``observed`` truly compares intent vs outcome.
    """
    surface: str = ""
    expected: dict[str, Any] = field(default_factory=dict)
    observed: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""


@dataclass(frozen=True, slots=True)
class OrbResetRequested(Event):
    """User asked to reset the orb to its default anchor (BUG-027 / L2).

    Triggered by the local_action_gate when the user says "Orb zurück",
    "wo bist du", or "reset orb". ``ui.orb.bus_bridge`` subscribes and
    dispatches the actual reset onto the Tk thread. Decouples the voice
    trigger from the Tk-thread mutation — bus stays sync-friendly.
    """
    source: str = ""  # "voice" | "tray" | "test"
