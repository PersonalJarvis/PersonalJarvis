"""Contracts for the realtime (full-duplex speech-to-speech) plugin group.

A realtime provider fuses STT + reasoning + TTS + VAD into one stateful
WebSocket session. None of the Brain/STT/TTS protocols can express this, so this
is its own ``jarvis.realtime`` group. Provider modules live under
``jarvis/plugins/realtime/`` and MUST NOT import ``jarvis.*`` at module import.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from jarvis.core.protocols import AudioChunk

RealtimeEventType = Literal[
    "audio_delta",
    "output_transcript_delta",
    "input_transcript",
    "handoff_requested",
    "tool_call",
    "speech_started",
    "interrupted",
    "turn_complete",
    "usage",
    "error",
]


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    """One normalized, provider-neutral event from a duplex session."""

    type: RealtimeEventType
    audio: AudioChunk | None = None          # audio_delta
    text: str | None = None                  # output_transcript_delta / input_transcript
    is_final: bool = False
    # output_transcript_delta only: locally recovered vetting material for a
    # response whose provider transcript lags its audio. The scrub gate judges
    # it like any transcript, but it must never reach the surface or the turn
    # transcript — the provider's own text follows and would double up.
    shadow: bool = False
    # input_transcript finals only: voiced audio duration the local endpointer
    # measured for the utterance. Feeds the session's first-turn language
    # duration gate; 0 means unknown and disables the gate for that final.
    voiced_ms: int = 0
    ms_played: int | None = None             # speech_started: ms of our audio already heard
    error: str | None = None
    # A recoverable provider event reports a rejected operation while the
    # duplex transport remains usable. It must not end voice ownership or
    # trigger the classic pipeline.
    recoverable: bool = False
    # The provider announced it will close this transport soon (e.g. a
    # session-duration GoAway) while the socket still works. The
    # orchestrator should rebuild the transport in place at the next safe
    # boundary instead of waiting for the server-forced close.
    reconnect_advised: bool = False
    item_id: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    # Client-managed provider handoff metadata. A handoff is control flow for
    # the Jarvis supervisor, never a provider tool call or response boundary.
    handoff_id: str | None = None
    provider_turn_id: str | None = None
    # Token/second counters for one finished generation, folded per turn by the
    # orchestrator and forwarded to the recorder. Declared HERE because the
    # shipped API-billed adapters already emit ``type="usage"`` events with this
    # payload: a third-party adapter doing the same against this dataclass used
    # to raise AttributeError inside the receive pump.
    usage: dict[str, int] | None = None
    # True when JARVIS itself caused this event — an ``interrupted`` produced by
    # our own ``interrupt()`` rather than by the user speaking. Without it the
    # orchestrator reads its own cancellation as a barge-in and arms the
    # user-speech state against a user who never said anything.
    self_initiated: bool = False
    # True when the PROVIDER abandoned the generation this ``interrupted``
    # closes and is regenerating the same answer. Distinct from
    # ``self_initiated``: nobody cancelled locally, so nothing has been drained
    # and the orchestrator still owns the cleanup — but the partial reply is
    # dead text the far end will never finish, so playing it out is wrong. A
    # transport whose text inputs double as user turns (Gemini Live) does this
    # to itself whenever a per-turn directive lands on an answer already in
    # flight; without the flag the session heard both halves as two replies.
    superseded: bool = False


@dataclass(frozen=True, slots=True)
class RealtimeSessionConfig:
    """Everything a provider needs to open one duplex session."""

    instructions: str = ""
    language: str = "en"                     # output: bare de/en/es, resolved upstream
    input_language: str = "auto"              # recognition: auto or bare de/en/es
    language_is_pinned: bool = False          # explicit reply-language preference
    model: str = ""                          # provider model id ("" -> the adapter's
                                              # hardcoded default; no regression)
    voice: str = ""
    input_sample_rate: int = 16000
    output_sample_rate: int = 24000
    # Native audio responses already carry a transcript side-channel. OpenAI's
    # GA Realtime schema rejects requesting text and audio simultaneously.
    modalities: tuple[str, ...] = ("audio",)
    turn_detection: str = "server_vad"       # "server_vad" | "semantic_vad"
    # An EXPLICIT fixed silence window for the provider's native VAD. None
    # (the default) sends nothing: the provider commits the user's input on
    # its own timing. This is a raw override for callers that need a fixed
    # window; the user-facing pause travels as ``turn_pause_ms`` below.
    silence_duration_ms: int | None = None
    # How eagerly the provider's native VAD may declare the user finished.
    # "low" = read a pause as a pause; "high" = the provider's own eager
    # default; None — the DEFAULT — = send nothing and inherit whatever the
    # provider ships. End-of-speech sensitivity changes only how a PAUSE is
    # interpreted, so a finished short sentence still commits immediately
    # while a hesitation inside a long order no longer ends the turn (live
    # 2026-08-13 16:46/16:47: a spoken brief for a coding pane was closed
    # twice mid-sentence and delivered truncated). It travels WITH
    # ``turn_pause_ms``: the session sends "low" only when the user has asked
    # for a patience window, because patience without the window is a
    # half-measure and the window without patience is the wait with none of
    # its protection. Providers without the concept ignore it.
    end_of_speech_sensitivity: str | None = None
    # How long the user may PAUSE before their turn is taken — the Settings
    # "Thinking pause" (SpeechConfig.vad_silence_ms), one value for both voice
    # engines since 2026-08-18. Which lever applies is a transport
    # CAPABILITY, never a provider name (AP-21):
    #
    # * A transport that answers on its own boundary
    #   (``creates_responses_automatically``) has exactly one lever — its
    #   native end-of-turn detection — so it folds this window into that
    #   detection (Gemini Live: ``automatic_activity_detection
    #   .silence_duration_ms``). A user who resumes inside the window keeps
    #   the same activity: the words append, nothing is submitted twice.
    # * A transport whose responses Jarvis requests itself must IGNORE this
    #   field: the session applies the same pause locally, on the last
    #   voiced microphone frame, before it calls ``request_response`` — so
    #   the transcription latency counts towards the pause instead of on top
    #   of it, and a final that arrives while the user is still talking
    #   appends to the open turn (one response for the whole request).
    #
    # History: a fixed window on every realtime transport was removed on
    # 2026-07-21 because the extra wait after a clearly finished sentence read
    # as "done speaking but still listening"; on 2026-08-18 the maintainer
    # asked for the opposite — wait for a clear pause, and keep listening
    # while the sentence continues — so the pause is back, as the SAME
    # setting the classic pipeline uses, adjustable in Settings → Voice.
    # None — the DEFAULT — = the provider's own timing: no window is sent and
    # none is waited out locally, so a turn ends exactly when the provider's
    # factory detection says it does. Layering a window on a transport that
    # already endpoints natively made every finished sentence wait twice and
    # the extra wait was audible against the vendor's own client, so the
    # Settings slider now starts at "automatic" and only an explicit value
    # travels (maintainer, 2026-08-23).
    turn_pause_ms: int | None = None
    tools: tuple[dict[str, Any], ...] = ()
    # Bounded transcript of the call so far, oldest first, as
    # ``{"role": "user" | "assistant", "text": ...}`` mappings. A fresh
    # transport opened MID-CALL (in-place rebuild after a provider disconnect,
    # or a cross-family fallback) starts with an empty server-side
    # conversation; seeding this history restores the context the model
    # needs to understand follow-up turns (BUG-088). Empty at the first open
    # of a call. Providers that cannot inject history ignore it.
    history: tuple[dict[str, str], ...] = ()
    # A browser-created WebRTC offer used only by providers whose upstream
    # authentication handshake requires a browser media transport. Most
    # providers ignore it and continue to use their native WebSocket SDK.
    # The offer is deliberately transport data rather than a credential.
    transport_offer_sdp: str = ""


@runtime_checkable
class RealtimeSession(Protocol):
    """A live duplex handle (one connection).

    Optional capability (probed with ``getattr``, never required): a session
    that can seed conversation history into a rebuilt transport may expose
    ``set_history_snapshot(history: tuple[dict[str, str], ...]) -> None``.
    The orchestrator calls it with the current bounded call transcript after
    every completed turn so a provider-internal transport rebuild (e.g. the
    openai_realtime BUG-064 stack) can restore context without a wire call.

    Optional capability (probed with ``getattr``, never required): a session
    whose direct-speech channel renders text VERBATIM may expose
    ``direct_speech_is_authoritative = True``. The orchestrator then clears
    the scrub hold for that audio, because the text it renders was scrubbed
    by Jarvis before it was sent (ADR-0010) and carries no model transcript
    for the gate to vet — without it the whole delegate answer is dropped at
    the turn boundary as "output transcript missing". Providers that do not
    declare it keep failing closed, which is the correct default for anything
    the model itself generates.

    Optional capability (probed with ``getattr``, defaulting to
    ``supports_direct_tools``): ``supports_tool_results``. A transport with no
    native function calling has no wire to carry a ``function_call_output``
    either, so ``send_tool_result`` on it can only raise. Callers probe this
    instead of calling and catching, because a swallowed raise is how a
    dropped tool result becomes invisible (AP-30).

    A former optional ``renders_pinned_voice`` voice-identity capability
    (BUG-086 escalation) was removed 2026-07-21: routing delegate replies
    to the surface TTS produced an audibly different voice on every
    tool-model turn. Delegate replies render natively; the surface TTS is
    only the provider-mute fallback.
    """

    session_id: str
    creates_responses_automatically: bool
    isolates_response_generations: bool

    async def send_audio(self, chunk: AudioChunk) -> None: ...
    def receive(self) -> AsyncIterator[RealtimeEvent]: ...

    async def update_session(
        self,
        *,
        instructions: str | None = None,
        language: str | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
        # Turn-scoped directive, delivered separately so an APPEND-only
        # transport can supersede the previous one whole instead of leaving
        # contradictory "this current turn" texts standing. Adapters that
        # replace their instructions wholesale may simply ``del`` it — the
        # same text is already embedded in ``instructions``. The session
        # retries without it on TypeError for third-party adapters.
        turn_directive: str | None = None,
        # Session-constant discipline the adapter must RE-ASSERT on its
        # working channel every turn (the one-speaker rule with its
        # speak-request exception). Live finding 2026-08-05/06: a rule
        # stated once at open does not hold on ChatGPT-Live — the language
        # pin survives because it is repeated every turn, and this directive
        # needs the same treatment. Wholesale-replace adapters ``del`` it
        # (their full instructions already re-carry it); the session retries
        # without it on TypeError for third-party adapters.
        standing_directive: str | None = None,
    ) -> None: ...

    async def request_response(self, *, required_tool: str | None = None) -> None: ...
    async def send_text(self, text: str) -> None: ...
    async def truncate(self, audio_end_ms: int) -> None: ...
    async def interrupt(
        self,
        *,
        # True ONLY on the delegation/handoff paths: the interrupted
        # utterance's one automatic-response entitlement is withdrawn with
        # the cut. A plain barge-in must never set it — at that moment the
        # input generation already belongs to the user's NEW utterance, and
        # retiring it silences the answer to the question just asked.
        retire_input_entitlement: bool = False,
    ) -> None: ...
    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None: ...
    async def close(self) -> None: ...


class RealtimeUnavailableError(RuntimeError):
    """A provider declined the call and already said why, in plain words.

    Carries a sentence written for the USER, not a stack-trace fragment: the
    handshake summary reaches a toast verbatim, and the internal wording
    ("duplex capability probe reported unavailable") named the mechanism
    instead of the situation (live 2026-08-09). The session prints this
    message WITHOUT its exception class name — that is the whole point of
    the distinct type.
    """


@runtime_checkable
class RealtimeProvider(Protocol):
    """The plugin entry-point class."""

    name: str
    supports_realtime: bool
    input_sample_rate: int
    output_sample_rate: int
    credential_candidates: tuple[tuple[str, str | None], ...]

    #: OPTIONAL capability, deliberately NOT part of this Protocol's required
    #: surface (a data member would tighten every ``isinstance`` check and
    #: break third-party adapters): a provider MAY expose a
    #: ``duplex_unavailable_reason`` string explaining its last ``False``.
    #: The session reads it with ``getattr`` and falls back to a generic
    #: sentence — never a provider-name check (AP-21).
    async def can_open_duplex_session(self) -> bool: ...
    async def open_session(self, cfg: RealtimeSessionConfig) -> RealtimeSession: ...
