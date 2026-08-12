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
from typing import Any, Final, Literal
from uuid import UUID, uuid4

from .protocols import HarnessResult, HarnessTask, RiskTier, Transcript
from .turn_language import DEFAULT_LOCALE

# Every supported locale is equal (CLAUDE.md §1): a language field whose
# publisher omitted it must fall back to the SHARED default, never to one
# particular language. These defaults used to be the literal "de", so an event
# published without a language stamped German onto a Spanish or English turn —
# and the consumers that pick a TTS voice from it then spoke German back.
# ``turn_language`` is pure regex/set lookups with no jarvis imports, so this
# cannot introduce an import cycle.
_DEFAULT_EVENT_LANGUAGE: Final[str] = DEFAULT_LOCALE


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
class WakeCandidateDetected(Event):
    """Optimistic, VISUAL-ONLY wake hint — the overlay bar pops on this the
    instant OpenWakeWord fires, *before* the slow STT prefix-verification that
    gates the authoritative ``WakeWordDetected``.

    Carries no session semantics: only the overlay bridge consumes it. It never
    reaches the session recorder, the telemetry turn count, or the brain — so a
    rejected candidate (an OWW false positive) costs only a brief bar flash, not
    a phantom session record. Publishing ``WakeWordDetected`` early instead would
    open a session turn on every false positive; this lightweight sibling exists
    precisely so the *visual* feedback can be instant without that cost.

    ``active=True``  → show the listening bar now (candidate detected).
    ``active=False`` → retract: the prefix-verifier rejected the candidate, so
    hide the bar again unless a real session has meanwhile begun.
    """
    active: bool = True
    keyword: str = ""


@dataclass(frozen=True, slots=True)
class ListeningStarted(Event):
    """Jarvis opens the microphone for an utterance."""
    pass


@dataclass(frozen=True, slots=True)
class UtteranceCaptured(Event):
    audio_ref: str = ""      # content hash for the flight recorder
    duration_ms: int = 0


@dataclass(frozen=True, slots=True)
class TranscriptPartial(Event):
    transcript: Transcript | None = None


@dataclass(frozen=True, slots=True)
class TranscriptFinal(Event):
    transcript: Transcript | None = None
    # True when this finalized utterance will be re-attached to the still-open
    # turn by the continuation-recombine path (the brain is mid-thinking and the
    # window is live). The SessionRecorder reads this to record the coalesced
    # fragments as ONE transcript turn instead of splitting them — so the
    # Transcription view shows the single prompt the brain actually processes.
    continues_previous: bool = False


@dataclass(frozen=True, slots=True)
class TranscriptionUpdate(Event):
    text: str = ""
    is_final: bool = False


@dataclass(frozen=True, slots=True)
class TranscriptPolished(Event):
    """A finished voice turn, re-read and written out as prose.

    The AFTERMATH of a turn, never part of it. The brain has already been given
    ``text`` verbatim and is already answering by the time this fires — that is
    the entire design. The polish pass costs up to its latency ceiling, and
    spending that between the user finishing a sentence and Jarvis starting to
    answer would trade the thing that makes a voice assistant feel alive for a
    comma. So the turn runs unpolished at full speed and this arrives a moment
    later, for the surfaces that DISPLAY and STORE the turn rather than act on
    it: the transcription view, the session record.

    Separate from ``DictationTranscript`` (a dictation is text on its way into a
    document, and it is polished BEFORE delivery because nothing is waiting on
    it) and from ``TranscriptFinal`` (which is the turn itself, and must never
    be delayed). Three events because there are three different deadlines.

    Fail-open like every other part of the pass: when it does not arrive — no
    key, a timeout, a guard that fired — the raw transcript simply stands, which
    is what every consumer already has.
    """

    #: The polished text. Never empty: this event is not published at all when
    #: the pass returned anything other than a usable rewrite.
    text: str = ""
    #: The exact string this replaces, so a consumer can match it to the turn it
    #: belongs to without depending on event ordering, and can keep the original
    #: alongside rather than over-writing it.
    raw_text: str = ""
    #: The pass's own status vocabulary (``jarvis.dictation.polish``), carried
    #: so a recorder can store WHY a turn is unpolished instead of leaving the
    #: absence of an event as the only evidence.
    status: str = ""
    #: The model family that answered, "" when none did.
    provider: str = ""
    #: What the pass cost in wall-clock time. Off the critical path by
    #: construction, and worth measuring precisely because that claim has to
    #: stay true.
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class DictationTranscript(Event):
    """Live transcript of a dictation, as it is recognized.

    Deliberately a SEPARATE event from ``TranscriptionUpdate`` (which rides the
    live voice critical path). Dictation never reaches the brain, so keeping it
    on its own event name means the frontend can route it straight to a text
    field without ever confusing it with a real voice turn, and the voice
    hot-path event stays untouched.

    ``is_final=False`` interim hypotheses overwrite the live tail; the single
    ``is_final=True`` ends the dictation.
    """

    text: str = ""
    is_final: bool = False
    #: Where the pipeline has decided this transcript belongs, already resolved
    #: (never ``auto``): ``insert`` = typed into the foreign application in
    #: front, ``chat`` = handed to Jarvis's own window.
    #:
    #: The UI needs this, and needs it on the event rather than by asking: the
    #: event fires on BOTH routes, so a UI that inserted on every final
    #: transcript would write a dictation meant for another program into
    #: whatever Jarvis field last had focus — invisibly, in a section the user
    #: is not even looking at. Empty on an older backend, which reads as "do not
    #: insert in-app" and leaves that install exactly as it behaved before.
    target: str = ""


@dataclass(frozen=True, slots=True)
class DictationCompleted(Event):
    """A dictation finished — what was produced and where it ended up.

    Separate from ``DictationTranscript`` (which is the live text feed) because
    this carries the OUTCOME, and the outcome is the part the user must not be
    left guessing about: ``inserted`` means it is in their text field,
    ``clipboard_only`` means the OS blocked the paste and the text is one
    Ctrl+V away, ``unavailable`` means neither worked. The UI and the Jarvis Bar
    both surface ``detail`` verbatim, so it is a complete, user-facing sentence.
    """

    #: What was inserted (after cleanup).
    text: str = ""
    #: The transcript exactly as the STT returned it, before cleanup.
    raw_text: str = ""
    #: One of ``jarvis.dictation.outcomes.DICTATION_OUTCOMES``. That tuple is
    #: the single vocabulary for this value; it is imported rather than
    #: restated here because listing it twice is exactly how the five layers
    #: that carry an outcome drifted apart before (AP-4 / BUG-008).
    outcome: str = ""
    #: User-facing explanation; empty when nothing needs explaining.
    detail: str = ""
    #: e.g. ``clipboard+ctrl_v`` / ``type`` — empty when nothing was inserted.
    method: str = ""
    language: str = ""
    duration_s: float = 0.0
    removed_words: int = 0
    #: Why transcription failed, when it did (a provider error, a missing key,
    #: a wedged engine). ``None`` on every path that did not fail. This is what
    #: makes ``outcome="failed"`` distinguishable from ``outcome="empty"``:
    #: before it existed, a provider 401 and plain silence looked identical.
    error: str | None = None
    #: What the generative polish pass did to ``text``. One of
    #: ``jarvis.dictation.polish.POLISH_STATUSES`` — that tuple is the single
    #: vocabulary for this value, imported rather than restated here for the
    #: same reason ``outcome`` is (AP-4 / BUG-008). ``""`` on a completion the
    #: pass never ran for at all (a hangup, a crash before delivery), which is
    #: deliberately distinct from ``"off"`` (the user switched it off) and from
    #: ``"unavailable"`` (nobody on this host holds a key for it).
    #:
    #: This is carried on the EVENT and not only in the history because the
    #: user-visible claim "these are the words you said" changes when the pass
    #: applies: a surface that shows the polished text without being able to say
    #: it was polished cannot offer the raw text back.
    #:
    #: ``"translated"`` is the one status that also changes what ``language``
    #: above describes: it stays the language that was SPOKEN (the one
    #: ``raw_text`` is in), while ``text`` arrives in
    #: ``[dictation].translate_target``. The two fields belong to the two texts,
    #: which is why neither is rewritten to match the other.
    polish_status: str = ""
    #: The credential family that answered (``groq``/``gemini``/...), empty when
    #: none was asked. A family id, never a model id — the model is a detail of
    #: the family and changes without the user doing anything.
    polish_provider: str = ""
    #: Wall-clock cost of the pass, including a fallback hop and a rejected
    #: answer. It is the number that decides whether the feature is worth its
    #: place in the delivery path, so it is reported on every status, not only
    #: on ``applied``.
    polish_latency_ms: int = 0
    #: STT provider ids that produced text during this dictation, in first-use
    #: order. More than one means the runtime fallback crossed families.
    stt_providers: tuple[str, ...] = ()
    #: Effective/requested model ids corresponding to the successful STT calls.
    #: Kept as a set-like ordered tuple because a fallback may use more than one.
    stt_models: tuple[str, ...] = ()
    #: Language tags reported by the recognizer's final windows. The transcript
    #: remains the source of truth; these are diagnostics, never a language lock.
    detected_languages: tuple[str, ...] = ()
    #: Aggregate wall-clock time spent awaiting STT for this dictation.
    stt_latency_ms: int = 0
    #: Number of logical STT uploads, including previews and final attempts.
    #: A provider's internal request-shape downgrade is deliberately not
    #: observable at this layer.
    stt_calls: int = 0
    #: Stable reason codes observed during STT, with duplicates removed.
    stt_errors: tuple[str, ...] = ()
    #: Compact audit facts such as final-pass status, window count, and audio
    #: preprocessing decisions. English machine data, no transcript content.
    stt_audit: tuple[str, ...] = ()
    #: Stable PCM rate delivered to STT after any capture-side resampling.
    audio_sample_rate_hz: int = 0
    #: Whole-recording normalized RMS in the same 0..1 convention as VAD.
    audio_rms: float = 0.0
    #: Share of PCM16 samples at (or immediately below) full scale.
    audio_clipping_ratio: float = 0.0
    #: Discontinuities inferred from capture timestamps / queue overflow counts.
    audio_dropouts: int = 0
    #: Approximate duration missing across timestamp-detected discontinuities.
    audio_dropout_ms: int = 0


#: Every reason a dictation start can be refused. Declared ONCE here, next to
#: the event that carries it, because this value crosses the pipeline, the bus,
#: the REST/WS surface and the UI — the exact shape of drift that has bitten
#: this repo four times (AP-4 / BUG-008). Consumers import this tuple instead
#: of restating the strings.
#:
#: ``microphone_unavailable``
#:     The local capture gate is closed — the desktop app is not visible, or
#:     microphone permission has not been granted.
#: ``no_stt``
#:     No speech-to-text provider is wired, so nothing could transcribe.
#: ``already_running``
#:     A dictation is already recording (or a handover is in flight); the
#:     second start is a no-op.
#: ``handover_failed``
#:     A live voice conversation owned the microphone, the dictation asked it to
#:     hang up, and the microphone did not come back — the teardown raised, ran
#:     past its bound, or the key was released while it was still running. This
#:     is the ONLY outcome of a collision with a voice session: an explicit
#:     dictation key press is a deliberate user action and now WINS over a
#:     conversation somebody left open, so a plain "a session is running" is no
#:     longer a refusal at all.
#: ``voice_session_active``
#:     LEGACY. A voice conversation (wake word or push-to-talk) owned the
#:     microphone and the dictation gave up instead of taking over. No longer
#:     published — kept in the vocabulary because an older backend can still
#:     send it to a newer UI, and a consumer that dropped the token would then
#:     render an unknown-reason blank.
#: ``pipeline_not_running``
#:     The speech pipeline has no running event loop to host the session.
#:
#: The last three belong to the "insert the last dictation again" key rather
#: than to a recording. It starts nothing, so it can never be refused for a
#: microphone reason — but it CAN do nothing at all, and a key that does
#: nothing in silence is the exact failure this vocabulary exists to prevent.
#:
#: ``nothing_to_paste``
#:     The history holds no dictation to re-insert (nothing recorded yet, or
#:     every entry was discarded).
#: ``history_disabled``
#:     ``[dictation].history_enabled`` is off, so no transcript was kept and
#:     there is nothing this key could paste. Recoverable in the settings.
#: ``paste_unavailable``
#:     The text could not be typed into the focused window — Wayland, a
#:     headless host, an elevated window in front, macOS secure input. The
#:     detail says where the text ended up instead (usually the clipboard).
DICTATION_REFUSAL_REASONS: Final[tuple[str, ...]] = (
    "microphone_unavailable",
    "no_stt",
    "already_running",
    "handover_failed",
    "voice_session_active",
    "pipeline_not_running",
    "nothing_to_paste",
    "history_disabled",
    "paste_unavailable",
)


@dataclass(frozen=True, slots=True)
class DictationStarted(Event):
    """A dictation turn has begun — the counterpart of ``DictationCompleted``.

    Published the moment ``start_dictation()`` commits, BEFORE the first audio
    frame is captured, so a UI surface can show "listening" at key-down speed
    instead of waiting for the first partial transcript (which costs a partial
    interval plus an STT round-trip, and never arrives at all for a short
    press).

    ``target`` is the RAW target the caller asked for (``auto`` | ``insert`` |
    ``chat``); ``auto`` is deliberately resolved when the recording ENDS, not
    here, so the value carried by this event is the request, not the verdict.
    """

    target: str = ""


@dataclass(frozen=True, slots=True)
class DictationTranscribing(Event):
    """Recording stopped; the final transcription is now running.

    Published when the microphone lease is released — the key was let go, the
    hands-free toggle stopped it, the duration cap expired, or a REST/WS stop
    arrived — and before the closing transcription call. It is the honest
    "no longer listening, not yet finished" phase: a UI surface switches from
    a level meter to a working indicator here, and ``DictationCompleted``
    always follows.
    """


@dataclass(frozen=True, slots=True)
class DictationRefused(Event):
    """A dictation could NOT start, and the user is owed an explanation.

    Every refusal used to be a ``log.info`` in a file the desktop app cannot
    show (CLAUDE.md §9: the app is a WebView with no dev tools), so pressing
    the dictation shortcut with a closed microphone gate, no STT provider, or
    a live voice session did *nothing* at all. This event makes each refusal
    observable on the bus so the caller can say why the key did nothing.

    ``reason`` is a stable token from ``DICTATION_REFUSAL_REASONS``; ``detail``
    is a complete, user-facing English sentence, matching the contract of
    ``DictationCompleted.detail``.
    """

    reason: str = ""
    detail: str = ""


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


@dataclass(frozen=True, slots=True)
class UiThemeChanged(Event):
    """Fired when the app's colour theme changes.

    The frontend listens for this over ``/ws`` (wildcard-forwarded) and repaints
    live — no reload. Emitted by the settings endpoint and (indirectly, via
    ``ConfigReloaded``) by the Control API, so ``jarvis api settings
    put-appearance`` reaches an already-open window instead of waiting for a
    restart.
    """
    theme: str = ""  # "dark" | "light" | "system"


# ----------------------------------------------------------------------
# Action-Lifecycle
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ActionProposed(Event):
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    risk_tier: RiskTier = "safe"
    # Session-Decision-Log: the brain's natural-language rationale emitted
    # alongside this tool call (the model's ``text`` block next to the
    # ``tool_use`` block). Captured "for free" — no extra model call — so the
    # Run Inspector + local diary can show *why* Jarvis chose this action.
    # Already redacted + length-capped by the ToolExecutor before publish.
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class ActionApprovalRequired(Event):
    """A concrete tool call is paused until this trace receives a decision.

    ``args_preview`` is redacted and length-capped before publication. Mission
    identifiers are correlation metadata only; the mission itself remains in
    its running state while this individual call waits.
    """

    tool_name: str = ""
    risk_tier: RiskTier = "ask"
    reason: str = "risk_tier"  # "risk_tier" | "plausibility"
    args_preview: str = ""
    expires_at_ns: int = 0
    mission_id: str | None = None
    worker_id: str | None = None


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
    # Session-Decision-Log: a short preview of what the tool returned
    # (``ToolResult.output``). Already redacted + length-capped by the
    # ToolExecutor before publish (``jarvis.core.redact.safe_preview``) so no
    # raw secret reaches the bus / session DB / local diary.
    output_preview: str = ""


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
class SpeechSpoken(Event):
    """Text committed to the user-audible output path.

    ``ResponseGenerated`` describes model output and can precede TTS, filtering,
    staleness checks, or interruption. ``SpeechSpoken`` is the authoritative
    transcript track. Producers emit it only after the corresponding audio was
    accepted by the active playback surface. This includes normal replies,
    timeout and unavailable notices, clarifying questions, privacy acknowledgments,
    mission announcements, progress nudges, preambles, and error readbacks.

    The pipeline publishes this event at every audible-output site so the passive
    ``SessionRecorder`` can persist it into ``voice_events`` and the
    Transcription view can show the full spoken track. ``spoken_kind`` is a
    soft tag from ``jarvis.sessions.constants.SPOKEN_KINDS`` (timeout /
    announcement / clarify / …) used for the UI label.

    Published fire-and-forget; the recorder is a read-only
    wildcard subscriber and never touches the voice hot path (AP-9 / AD-OE2).
    """
    text: str = ""
    language: str = _DEFAULT_EVENT_LANGUAGE
    spoken_kind: str = "other"
    # Optional technical diagnostic that was NOT spoken aloud — e.g. the raw
    # exit code + harness reason behind a failed Computer-Use action. The voice
    # readback is humanized ("…didn't work on screen"), but the Transcription
    # view surfaces this for debugging (user request 2026-06-16). None for the
    # common case: a plain canned phrase has no diagnostic.
    detail: str | None = None
    # Which voice actually spoke this text (user request 2026-07-17): the
    # resolved voice name ("Fenrir", "Charon", "leo", an ElevenLabs voice id)
    # and the speaking family ("gemini-live", "openrouter", "grok-voice").
    # None when the speaking layer cannot tell — consumers must treat that as
    # unknown, never guess from the brain provider (the speaker can differ,
    # e.g. a surface-TTS readback inside a realtime session).
    voice: str | None = None
    voice_provider: str | None = None


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


@dataclass(frozen=True, slots=True)
class ContactChanged(Event):
    """A contact in the user-curated address book was written or removed.

    Emitted (via ``jarvis.contacts.notify``) after every successful
    ``ContactStore`` write. ``action`` vocabulary is owned by
    ``jarvis.contacts.notify.CONTACT_CHANGE_ACTIONS``:
    ``created`` | ``updated`` | ``deleted``.
    Consumed by the wiki contact mirror (deterministic person-page sync).
    """
    action: str = ""
    slug: str = ""
    name: str = ""


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


@dataclass(frozen=True, slots=True)
class DetachedViewOpened(Event):
    """A section now lives in its own detached desktop window.

    Published by the desktop shell when ``open_detached_window`` succeeds. The
    frontend (``useWebSocket.ts``) mirrors the registry into every connected
    window's ``detachedViews`` store field — the origin window reacts by
    unmounting its own instance of the section (one mounted Agentic IDE
    instance max: a second one steals every pane's output stream) and by
    standing down its realtime broker when the voice surface moved out.
    ``view`` mirrors the frontend ``SECTION_IDS`` (``store/events.ts``).
    """
    view: str = ""


@dataclass(frozen=True, slots=True)
class DetachedViewClosed(Event):
    """A detached desktop window was closed; its section returns to the app.

    Published by the pywebview ``closed`` hook of the detached window. The
    frontend clears the section from ``detachedViews`` and the origin window
    remounts it.
    """
    view: str = ""


@dataclass(frozen=True, slots=True)
class AgenticIdeTerminalsAdded(Event):
    """Panes were added to the Agentic-IDE workspace from outside its view.

    The workspace view loads its state once when it mounts, which is enough as
    long as it is the only thing that can add a pane. Voice and the CLI can too
    ("open five more Claude Code terminals"), and without this event those panes
    exist in the registry while the open view keeps showing the old grid.

    The frontend (``useWebSocket.ts``) turns it into a window event the workspace
    view listens for and re-runs its own fetch — the same shape as
    ``SecretConfigured``. ``names`` is carried so a client can tell the user what
    appeared without a second request; ``folder`` identifies the workspace, since
    a stale event from a session that has since been replaced must not be acted
    on.
    """
    session_id: str = ""
    names: tuple[str, ...] = ()
    agent: str = ""
    folder: str = ""


@dataclass(frozen=True, slots=True)
class AgenticIdeTerminalsClosed(Event):
    """Panes were closed outside the Agentic-IDE workspace view."""

    session_id: str = ""
    names: tuple[str, ...] = ()
    folder: str = ""


@dataclass(frozen=True, slots=True)
class AgenticIdeWorkspaceChanged(Event):
    """A workspace itself was opened, reopened, switched to, or closed.

    The pane-level events above cover changes INSIDE a grid. This is the layer
    above them, and it was missing: opening a workspace, restoring one after a
    restart, switching tabs and closing a tab all happened in the registry with
    nothing said out loud. Any client that was not the one making the change —
    a second window, a browser tab, the app's own view after the backend
    restored a workspace in the background — kept rendering a grid the backend
    no longer had, and the panes of that grid then knocked at a workspace that
    was not there. Nothing recovered by itself; the user had to reload.

    ``reason`` is one of ``opened`` / ``restored`` / ``activated`` / ``closed``
    / ``renamed``, so a client can tell "your tab was switched" from
    "everything is gone" without guessing from the payload. Clients re-read the
    authoritative state on any of them — the event is a trigger, never the
    source of truth.
    """
    session_id: str = ""
    reason: str = ""
    folder: str = ""
    name: str = ""
    open_workspaces: int = 0


@dataclass(frozen=True, slots=True)
class AgenticIdePromptSent(Event):
    """A prompt was typed into one pane by Jarvis rather than by the user.

    The only thing that made a voice-driven prompt visible used to be the
    agent's own echo travelling back over that pane's terminal socket. When
    anything on that path was late or missing, the user watched an unchanged
    screen and concluded the instruction had gone nowhere — with no second
    signal anywhere in the app to say otherwise.

    So the fact is announced in its own right. ``submitted`` carries the honest
    three-way answer from ``send_prompt``: true (it went), false (it is sitting
    in the pane's input box), null (never seen to arrive). ``preview`` is a
    short excerpt, never the whole brief — this is a notification, not a copy
    of the prompt.
    """
    session_id: str = ""
    terminal: str = ""
    agent: str = ""
    submitted: bool | None = None
    preview: str = ""


@dataclass(frozen=True, slots=True)
class AgenticIdeComposeProgress(Event):
    """One beat of a task brief being written for one pane.

    Writing a brief is 10-30 s of real model work, and the typed prompt bar
    used to show a silent spinner for all of it — a working composer and a
    wedged one looked identical from the outside. The composer already
    narrates its progress (``jarvis.agentic_ide.prompt_composer``, the
    ``STAGE_*`` beats); this event carries each line to every client on the
    app socket they already hold, the same route ``AgenticIdePromptSent``
    takes for the delivery itself.

    ``stage`` is one of the composer's ``STAGE_*`` constants (``start``,
    ``thinking``, ``drafting``, ``retry``, ``hedge``, ``ready``,
    ``fallback``, ``sent``). ``message`` is the finished English line the
    composer wrote, including which writer is working — clients display it,
    they never parse it. ``kind`` is the detected task kind so a client can
    style a review differently from a build; empty when unknown.
    """

    session_id: str = ""
    terminal: str = ""
    stage: str = ""
    message: str = ""
    kind: str = ""


@dataclass(frozen=True, slots=True)
class AgenticIdeCodingModeChanged(Event):
    """The focused coding mode was switched on or off.

    Coding mode is not a property of the workspace view — it changes how Jarvis
    answers EVERY turn, on every screen. So the one surface that must never be
    wrong about it is the app shell, and the shell does not mount the workspace
    view. Without this event the global indicator would only learn about a
    switch by being on the screen where the switch happened, which is precisely
    the screen where it is not needed.

    Carries the EFFECTIVE mode, not merely the flag: ``enabled`` is true only
    when a workspace is actually open AND its focus mode is on — the same
    predicate the routing side asks. A client must be able to render the badge
    straight from this payload without re-deriving that rule and drifting away
    from it.

    ``folder`` and ``workspace`` let a client name the workspace the mode
    belongs to; both are empty when the mode went off.
    """

    session_id: str = ""
    enabled: bool = False
    folder: str = ""
    workspace: str = ""


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

# Announcement (CL-3, Jarvis-Agent lifecycle; renamed twice, see git history)

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
    language: str = _DEFAULT_EVENT_LANGUAGE
    # Discriminator for the new ack_brain Flash-Brain producer. None keeps
    # backwards compatibility with the existing MissionAnnouncer callers
    # that only pass text+priority+language. "progress" (2026-06-09, CU
    # frontier-speed Wave 0) marks throttled mid-mission milestone updates
    # from the Computer-Use loop ("Schritt 2 von 5 erledigt.") — spoken like
    # "info" but droppable when stale.
    kind: Literal["preamble", "completion", "subagent", "info", "progress"] | None = None  # noqa: UP037
    # Optional technical diagnostic forwarded to the transcript's spoken track
    # (never spoken). A failed Computer-Use readback rides kind="subagent"
    # with detail="exit 5 · <harness reason>" so the log shows the exit code
    # while the voice stays humanized. Mirrors ``SpeechSpoken.detail``.
    detail: str | None = None


# Mission completion — bridged from the per-mission MissionBus to drive When-Then rules

@dataclass(frozen=True, slots=True)
class MissionCompleted(Event):
    """Terminal mission outcome, bridged from the isolated Phase-6 ``MissionBus``
    onto the global ``EventBus`` by ``MissionEventBridge``.

    Phase-6 mission lifecycle events (``MissionApproved`` / ``MissionFailed`` /
    ``MissionCancelled`` / ``MissionTimedOut``) live on the per-mission
    ``MissionBus`` and never reach the global bus. The Tasks scheduler — which
    drives the When-Then automation rules — is a global-``EventBus`` subscriber,
    so on its own it can never see a mission finishing. This event is that bridge:
    one flat, global signal per terminal mission outcome that a ``TriggerOnEvent``
    rule matches by name (``event_name="MissionCompleted"``) and filters by field
    (``filter_expr="status == 'approved'"``).

    All fields are flat (no nested dicts) on purpose: the scheduler's safe-AST
    ``filter_expr`` evaluator builds its namespace from ``__dataclass_fields__``
    and only compares top-level names. ``result_uri`` is the approved artifact's
    path (empty for non-approved outcomes); ``reason`` carries the failure/cancel
    cause. This is the machine-readable trigger signal — distinct from the spoken
    ``AnnouncementRequested`` readback the ``MissionAnnouncer`` emits for the same
    mission, so the two never collide.
    """
    mission_id: str = ""
    status: Literal["approved", "failed", "cancelled", "timed_out"] = "approved"  # noqa: UP037
    summary_de: str = ""
    summary_en: str = ""
    result_uri: str = ""
    reason: str = ""


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
    jarvis-bar and the mascot orb — wired through ``OrbBusBridge``
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
    """A concrete task/operation was stopped by the kill signal."""
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
    bytes_size: int = 0                 # size of the raw PNG data block
    capture_age_ms: int = 0             # age of the observation at inject time


@dataclass(frozen=True, slots=True)
class ScreenCaptureAnnounced(Event):
    """A one-off Screen Context capture is about to happen.

    Published BEFORE any pixels are grabbed, so the on-screen bar (and any
    other subscriber) can show the capture indicator while there is still
    something to indicate. Announcing afterwards would leave a window in which
    a capture happened with no visible sign — which is the one thing this
    feature must never do.

    ``target_kind`` is ``"monitor"`` or ``"window"``; ``target_label`` is a
    privacy-safe generic surface label and never contains app or window titles.
    """
    target_kind: str = "monitor"
    target_label: str = ""
    #: Why this surface: cursor_monitor | bar_monitor | primary_monitor | ...
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ScreenCaptureGrabbed(Event):
    """Raw pixels were grabbed for a one-off Screen Context request.

    Published immediately after the platform capture returns, before redaction
    and encoding. Carries dimensions only: pixels and captured text never enter
    the event bus. A later privacy recheck may still discard the frame.
    """

    width: int = 0
    height: int = 0


@dataclass(frozen=True, slots=True)
class ScreenCaptureCompleted(Event):
    """A Screen Context capture finished — the receipt.

    Carries only metadata, never pixels and never captured text: this event
    reaches the flight recorder, and a recorder that stores screen content
    would quietly defeat the feature's retention promise.
    """
    target_kind: str = "monitor"
    target_label: str = ""
    width: int = 0
    height: int = 0
    bytes_size: int = 0
    redaction_count: int = 0
    degradation_count: int = 0
    ui_text_source: str = "none"


@dataclass(frozen=True, slots=True)
class ActionPlanned(Event):
    """The CU loop planner proposed the next action (before execution)."""
    action_kind: str = ""               # "click" | "type" | "hotkey" | "wait" | "verify"
    target_hint: str = ""               # e.g. "{role:Button,name:Save}"


@dataclass(frozen=True, slots=True)
class ActionVerified(Event):
    """Post-execution verify: did the action produce the expected effect?"""
    action_kind: str = ""
    success: bool = False
    reason: str = ""                    # on fail: what did the verify observer see?


@dataclass(frozen=True, slots=True)
class CUStepProfiled(Event):
    """One Computer-Use loop phase finished (2026-06-09 frontier-speed Wave 0).

    Dual purpose: (a) per-phase latency instrumentation for cu_bench (where
    does the step's wall-clock go: observe / uia / plan / think / act /
    verify / settle), and (b) a liveness heartbeat for the speech pipeline —
    a long think phase emits no ObservationCaptured/ActionPlanned, so without
    this event the TTS no-first-frame ceiling could behead a working mission.
    """
    phase: Literal[
        "observe", "uia", "plan", "think", "act", "verify", "settle",
    ] = "observe"  # noqa: UP037
    duration_ms: int = 0
    step_idx: int = 0
    engine: str = "v1"
    cache_read_tokens: int = 0


@dataclass(frozen=True, slots=True)
class CUControlStarted(Event):
    """A Computer-Use mission took control of the local mouse/keyboard.

    Published by ``ComputerUseHarness.invoke()`` the moment the mission's
    cancel token is registered — exactly when "Jarvis is controlling this
    computer" begins. Drives user-facing control indicators (the yellow
    screen border in ``jarvis.cu.indicator``). Concurrent missions each
    publish their own Started/Ended pair; subscribers refcount.
    """
    mission_id: str = ""


@dataclass(frozen=True, slots=True)
class CUControlEnded(Event):
    """A Computer-Use mission released control of the local mouse/keyboard.

    Always published in the ``finally`` of ``ComputerUseHarness.invoke()``
    — on success, timeout, cancel, and crash alike. ``reason`` is a short
    machine-readable tag: "finished" | "cancelled" | "timeout" | "error".
    """
    mission_id: str = ""
    reason: str = "finished"


#: Heartbeat contract (2026-06-09): every event type the Computer-Use loop
#: publishes as a liveness signal. The speech pipeline subscribes its
#: ``_on_agent_progress`` handler to EXACTLY this tuple — extending the loop
#: with a new progress event means adding it here, and the contract test in
#: tests/unit/harness/test_cu_wave0.py keeps both sides honest.
#: (CUControlStarted/Ended are deliberately NOT part of this tuple — they
#: fire once per mission, not per step, so they carry no liveness signal.)
CU_PROGRESS_EVENTS: tuple[type, ...] = (
    ObservationCaptured,
    ActionPlanned,
    CUStepProfiled,
)


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
    output_preview: str = ""        # max 240 characters, for the UI timeline
    error: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowCompleted(Event):
    workflow_id: str = ""
    run_id: str = ""
    success: bool = False
    duration_ms: int = 0
    error: str | None = None


# ----------------------------------------------------------------------
# Jarvis-Agent Task Dashboard
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class JarvisAgentTaskStarted(Event):
    parent_trace_id: UUID | None = None
    utterance: str = ""
    context_hints: list[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    max_duration_s: int = 0
    depth: int = 0


@dataclass(frozen=True, slots=True)
class JarvisAgentReviewTriggered(Event):
    iteration: int = 0


@dataclass(frozen=True, slots=True)
class JarvisAgentTaskCompleted(Event):
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
    # Realtime duplex voice mode (browser/OpenAI). REALTIME_INPUT_COMMITTED is
    # the per-turn anchor + stall-guard reset point; FIRST_TRANSCRIPT is the
    # BrainTTFT-equivalent; FIRST_AUDIO is the first provider audio delta
    # received (pre scrub-hold). AudioOutFirst still marks the first audible,
    # post-hold sample.
    REALTIME_INPUT_COMMITTED = "realtime_input_committed"
    REALTIME_ROUTING_DECISION = "realtime_routing_decision"
    REALTIME_FIRST_TRANSCRIPT = "realtime_first_transcript"
    REALTIME_FIRST_AUDIO = "realtime_first_audio"
    REALTIME_DELEGATE_STARTED = "realtime_delegate_started"
    REALTIME_DELEGATE_COMPLETED = "realtime_delegate_completed"
    REALTIME_TOOL_COMPLETED = "realtime_tool_completed"
    REALTIME_SCRUB_CANCEL = "realtime_scrub_cancel"
    REALTIME_CANCEL = "realtime_cancel"
    REALTIME_TURN_COMPLETE = "realtime_turn_complete"


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
class VoiceBootStatus(Event):
    """Voice warm-up readiness signal — drives the UI "voice starting" badge.

    Emitted by the speech pipeline's two-phase warm-up: ``ready=False`` at the
    very start, then ``ready=True`` once the critical listening path (audio
    device, VAD, wake-word, TTS client) is live — *before* the background
    confirmation-audio pre-render finishes. The frontend listens for event_name
    ``VoiceBootStatus`` and reads ``GET /api/voice/status`` on a late mount
    (WS events are not persistent).

    Two degraded recovery paths also set ``ready=True`` solely to release the
    web UI from a permanent loading screen. ``voice_usable`` is the stricter
    product contract for affordances that promise the user can speak now.
    """
    ready: bool = False
    detail: str = ""

    @property
    def voice_usable(self) -> bool:
        """Whether this event truthfully confirms a usable local voice path."""
        return self.ready and self.detail not in {
            "voice_unavailable",
            "watchdog_timeout",
        }


@dataclass(frozen=True, slots=True)
class VoiceSessionStarted(Event):
    """Wake word detected — a new voice session is starting."""
    session_id: str = ""
    wake_keyword: str = ""
    language: str = _DEFAULT_EVENT_LANGUAGE


@dataclass(frozen=True, slots=True)
class RealtimeSessionReady(Event):
    """A duplex provider accepted the effective session configuration."""

    session_id: str = ""
    provider: str = ""
    model: str = ""
    surface: str = ""
    input_sample_rate: int = 0
    output_sample_rate: int = 0
    #: The call's output language as a bare tag ("de" / "en" / "es" / any
    #: future supported locale), resolved by the ONE turn-language resolver.
    #: ``VoiceSessionStarted`` carries the same value but is published for the
    #: browser surface only, so on desktop this was the language nothing ever
    #: told the UI. Consumers render it; they never re-derive it.
    language: str = ""


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
    user_lang: str = _DEFAULT_EVENT_LANGUAGE
    jarvis_text: str = ""
    jarvis_lang: str = _DEFAULT_EVENT_LANGUAGE
    tier: str = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_total_ms: int = 0
    tool_calls: tuple[str, ...] = ()
    # Voice that actually spoke the reply (name + speaking family), when the
    # publisher knows it. See SpeechSpoken.voice for the semantics.
    voice: str | None = None
    voice_provider: str | None = None


@dataclass(frozen=True, slots=True)
class VoiceSessionEnded(Event):
    """Session ended (voice_pattern / hotkey / idle_timeout / shutdown / error)."""
    session_id: str = ""
    hangup_reason: str = ""
    turn_count: int = 0
    duration_s: float = 0.0


@dataclass(frozen=True, slots=True)
class RealtimeSessionPostmortem(Event):
    """Health counters of one finished realtime session, for forensics.

    Published by ``RealtimeVoiceSession.end()`` on every teardown and picked
    up by the wildcard flight recorder — the one place a live call's transport
    health survives the call. Counters only, never transcript text: telemetry
    must stay content-free. This event never crosses to the UI, so the
    five-layer enum-parity rule does not apply to its fields.

    All counters are zero in a healthy call except ``turns_completed`` (and,
    until the real v3 terminal item is confirmed live,
    ``quiescence_boundary_turns`` — every ChatGPT-Live turn currently ends on
    the local silence backstop).
    """

    session_id: str = ""
    provider: str = ""
    surface: str = ""
    hangup_reason: str = ""
    duration_ms: int = 0
    #: audio_start → provider handshake complete (RealtimeSessionReady).
    ready_ms: int = 0
    #: audio_start → first provider audio frame emitted to the surface.
    first_audio_ms: int = 0
    #: First user FINAL of the call → first AUDIBLE provider frame after it.
    #: This is the user-perceived answer wait; ``first_audio_ms`` stays for
    #: continuity but counts from session start and therefore includes the
    #: user's own listening/speaking time (a codex call measured 8 311 ms
    #: from start while the wait after the utterance was 923 ms, 2026-08-08).
    #: 0 means "never measured" (no final, or no audible audio after one).
    first_final_to_first_audio_ms: int = 0
    turns_completed: int = 0
    #: In-place transport rebuilds this session survived (BUG-071 machinery).
    rebuilds: int = 0
    #: Full open() retries over a STUN server after host candidates failed.
    stun_retries: int = 0
    #: Server user captions dropped for lacking local microphone energy.
    ungrounded_captions_dropped: int = 0
    #: Automatic provider responses refused by the grounding gate.
    ungrounded_responses_refused: int = 0
    #: Responses authorized by a trusted injection (announcement/readback).
    trusted_permit_responses: int = 0
    #: Turns closed by the local quiescence backstop (no protocol boundary).
    quiescence_boundary_turns: int = 0
    #: Turns closed by a confirmed terminal response item.
    terminal_item_turns: int = 0
    #: Back-to-back responses that spliced into one playback stream (<1.5 s).
    response_splices: int = 0
    #: Splices converted into clean local boundaries by the sequencing drain.
    sequenced_boundaries: int = 0
    #: Bounded local-STT passes started before a provider transcript arrived.
    output_shadow_recovery_attempts: int = 0
    #: Early local-STT passes that produced gate-only vetting text.
    output_shadow_recovery_successes: int = 0
    #: Responses that exhausted their bounded early local-STT attempts.
    output_shadow_recovery_exhausted: int = 0
    #: Authoritative local-STT passes started at a response boundary.
    output_terminal_recovery_attempts: int = 0
    #: Terminal passes that recovered the missing provider transcript.
    output_terminal_recovery_successes: int = 0
    #: Local-STT output recovery attempts that failed or produced no text.
    output_transcript_recovery_failures: int = 0
    #: Late/mismatched response events dropped before text could clear PCM.
    response_identity_drops: int = 0
    #: Responses a local watchdog retired whose late audio was still played.
    late_response_readoptions: int = 0
    #: Provider responses cancelled by the fail-closed output gate.
    unsafe_output_cancellations: int = 0
    #: Required public-fact searches started through the supervisor gateway.
    public_fact_grounding_attempts: int = 0
    #: Required public-fact searches that yielded a grounded localized answer.
    public_fact_grounding_successes: int = 0
    #: Required public-fact turns that degraded to honest uncertainty.
    public_fact_grounding_failures: int = 0
    #: Provider outputs rejected for using the wrong resolved turn language.
    output_language_mismatches: int = 0
    #: One-shot provider retries requested after a language mismatch.
    output_language_retries: int = 0
    #: Language retries that still ended in the localized safe fallback.
    output_language_failures: int = 0
    #: Stable delegate result delivery claims made during this session.
    delegate_delivery_claims: int = 0
    #: Delegate results confirmed by audible PCM or the completion channel.
    delegate_deliveries_completed: int = 0
    #: Completed delegate results recovered through AnnouncementRequested.
    delegate_delivery_recoveries: int = 0
    #: Duplicate delegate delivery attempts suppressed by the stable ledger.
    delegate_delivery_duplicates_suppressed: int = 0
    #: In-flight delegates transferred from socket to process lifetime.
    delegate_deliveries_detached: int = 0
    #: Opening responses cut at the cap because no user question existed yet.
    opening_responses_bounded: int = 0
    #: Self-dialogue verdicts that forced a transport replacement (BUG-124).
    self_dialogue_rebuilds: int = 0
    #: Planner-confirmed action turns on a provider without native tools.
    handoff_action_turns: int = 0
    #: Provider handoff control events received during the call.
    handoff_requests: int = 0
    #: Deterministic delegate jobs actually started on that transport.
    handoff_delegate_dispatches: int = 0
    #: Handoffs declined because no usable request/delegate was available.
    handoff_declines: int = 0
    #: Action turns where the provider omitted its required handoff event.
    handoff_obligation_misses: int = 0
    #: Delegate-by-default dispatches on a tool-less transport: finals the
    #: planner routed natively but whose assistant-tasking shape made the
    #: session delegate anyway rather than let the far end answer unaided.
    #: Distinct from ``handoff_delegate_dispatches`` (all deterministic
    #: dispatches) and ``handoff_requests`` (model-initiated handoffs).
    handoff_ambiguous_delegations: int = 0
    #: Half-duplex mutes released by the emergency timer, not a turn boundary.
    mute_emergency_releases: int = 0
    #: Microphone sender wall-clock resyncs (mic audio lost to a stall).
    sender_pacing_resyncs: int = 0
    #: Stale outgoing microphone frames shed by the elastic backlog cap.
    sender_shed_frames: int = 0
    #: Silent frames skipped while the sender drained its backlog.
    sender_catchup_dropped_frames: int = 0
    #: Provider audio frames dropped on a full receive queue.
    recv_dropped_frames: int = 0
    #: Worst event-loop scheduling stall observed during the session.
    max_loop_stall_ms: int = 0
    #: Output-language changes after the first resolution.
    language_flips: int = 0
    #: False when teardown abandoned a provider socket or the stream failed.
    close_clean: bool = True


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
class JarvisAgentBackgroundCompleted(Event):
    """A background Jarvis-Agent task finished — TTS should speak proactively.

    Separate from ``JarvisAgentTaskCompleted`` for pipeline/UI feedback without
    a standardised voice announcement.
    """
    success: bool = False
    utterance: str = ""       # what the user originally said
    summary: str = ""          # TTS-tauglich, max 120 Tokens
    error: str | None = None
    duration_s: float = 0.0


@dataclass(frozen=True, slots=True)
class JarvisAgentAnnouncement(Event):
    """Jarvis-Agent spawn start signal for UI/telemetry, without a voice ACK."""
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
    ``trifft`` means the bio feels accurate.  # i18n-allow: API contract identifier
    ``trifft_nicht`` means it is off the mark.  # i18n-allow: API contract identifier
    ``haerter`` asks for a more pointed bio.  # i18n-allow: API contract identifier
    The signal flows as a
    ``feedback_vector_block`` into the bio prompt for the next generation;
    no immediate regeneration.
    """
    bio_generated_at: str = ""
    # API/DB identifiers matched in logic.  # i18n-allow
    kind: str = ""  # "trifft" | "trifft_nicht" | "haerter"  # i18n-allow


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

    Emitted directly by :class:`jarvis.memory.wiki.curator.WikiCurator`
    after an app-owned atomic write, or by
    :class:`jarvis.memory.wiki.watcher.WikiWatcher` after a debounced
    external filesystem event. The desktop wiki view's WebSocket endpoint
    forwards this event to the frontend so React Query caches can be
    invalidated immediately.

    ``path`` is the vault-relative POSIX path (e.g. ``"entities/harald.md"``)
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

    Triggered by the local action gate for these literal voice phrases:
    "Orb zurück",  # i18n-allow: German voice-trigger phrase matched in logic
    "wo bist du",  # i18n-allow: German voice-trigger phrase matched in logic
    or "reset orb". ``ui.orb.bus_bridge`` subscribes and
    dispatches the actual reset onto the Tk thread. Decouples the voice
    trigger from the Tk-thread mutation — bus stays sync-friendly.
    """
    source: str = ""  # "voice" | "tray" | "test"
