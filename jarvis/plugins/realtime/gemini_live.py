"""Gemini Live provider plugin using the Google Gen AI SDK.

The module imports no ``jarvis.*`` modules. Credentials and configuration are
injected by the realtime orchestrator, and the Google SDK remains a lazy import
inside the live methods (AP-26). Gemini Live consumes raw 16-bit little-endian
mono PCM at 16 kHz and emits the same format at 24 kHz.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

_MODEL = "gemini-3.1-flash-live-preview"
_INPUT_RATE = 16_000
_OUTPUT_RATE = 24_000

# Live sessions accumulate audio (~25 tok/s) plus transcripts plus tool
# traffic and re-bill the FULL context on every model turn — without a
# sliding window that is quadratic in call length, and it is also what
# makes Gemini end long calls via GoAway when the context ceiling nears.
# Compression trades verbatim recall of the oldest audio for a bounded
# context; the orchestrator separately keeps a 20-turn text transcript for
# rebuilds, so nothing the user said is lost to the conversation logic.
_COMPRESSION_TRIGGER_TOKENS = 32_000
_COMPRESSION_TARGET_TOKENS = 16_000


def _compression_kwargs(types: Any) -> dict[str, Any]:
    """Sliding-window compression config, or {} on an SDK without it.

    Probed by SDK capability, never a model-name pin (AP-21) — same pattern
    as the HistoryConfig probe above. Without compression the Live API
    re-bills the whole accumulated call (audio in AND out) on every model
    turn AND hard-ends long calls at the context ceiling.
    """
    compression_cls = getattr(types, "ContextWindowCompressionConfig", None)
    sliding_cls = getattr(types, "SlidingWindow", None)
    if compression_cls is None or sliding_cls is None:
        log.info(
            "gemini-live: installed google-genai SDK has no context-window "
            "compression; long calls re-bill their full history each turn"
        )
        return {}
    return {
        "context_window_compression": compression_cls(
            trigger_tokens=_COMPRESSION_TRIGGER_TOKENS,
            sliding_window=sliding_cls(
                target_tokens=_COMPRESSION_TARGET_TOKENS
            ),
        )
    }


_END_SENSITIVITY_MEMBERS = {
    "low": "END_SENSITIVITY_LOW",
    "high": "END_SENSITIVITY_HIGH",
}

# Per-turn steering keys, in the order they are written into one delta.
# ``language`` first: it changes how everything after it is spoken.
_STEERING_ORDER = ("language", "turn", "standing")

# Framing for the steering delta. The session's own one-speaker directive —
# already in this connection's fixed system instruction — declares developer
# messages silent configuration that the model must never acknowledge, with
# exactly one opener as the exception. This header deliberately does NOT use
# that opener (or the model would speak the configuration). It must still
# leave the user's last utterance answerable: on Gemini Live the delta
# travels as a realtime text input at the final-transcript boundary, and
# "do not answer it" made the model close the whole turn in silence
# (live 2026-08-19: every greeting recovered through the Brain chain).
_STEERING_HEADER = (
    "Developer message — silent configuration update. It is NOT something the "
    "user said. Do not acknowledge this message, do not mention it, and do not "
    "treat it as a new request. Apply it from now on. If the user has just "
    "spoken, answer THEM now under these rules."
)


# Inputs this adapter sends ITSELF, as named by ``_note_input_sent``. A text
# on this transport is a user-side realtime input, so any one of them can
# interrupt an answer the server had already started.
_SELF_TEXT_INPUT_KINDS = frozenset({"steering text", "text"})

# How long after one of our own text inputs an ``interrupted`` edge is that
# input rather than a barge-in. Measured on the wire: 0.03 s, 0.09 s and
# 0.17 s in the three cut replies of 2026-08-23 (09:24, 10:05, 10:06). A real
# barge-in cannot land in this window — the user's final transcript arrived
# only milliseconds before the text went out, so nobody had started speaking
# again. Generous enough to cover a slow round trip, far below the ~2 s a new
# utterance needs to reach the server VAD.
_SELF_TEXT_INTERRUPT_WINDOW_S = 1.5


def _end_of_speech_sensitivity(types: Any, preference: str | None) -> Any | None:
    """Resolve the requested end-of-speech patience, or None on an old SDK.

    Probed by SDK capability, never a version pin (AP-21): an SDK without the
    enum — or without the field on AutomaticActivityDetection — opens a
    session on the provider default instead of failing to open at all. The
    degradation is loud, because silently inheriting Gemini's eager default is
    exactly the bug this setting exists to fix.
    """
    wanted = str(preference or "").strip().lower()
    if not wanted:
        return None
    member = _END_SENSITIVITY_MEMBERS.get(wanted)
    if member is None:
        log.warning(
            "gemini-live: unknown end-of-speech sensitivity %r; using the default",
            preference,
        )
        return None
    enum_cls = getattr(types, "EndSensitivity", None)
    detection_cls = getattr(types, "AutomaticActivityDetection", None)
    fields = getattr(detection_cls, "model_fields", None) or {}
    resolved = getattr(enum_cls, member, None) if enum_cls is not None else None
    if resolved is None or "end_of_speech_sensitivity" not in fields:
        log.warning(
            "gemini-live: installed google-genai SDK cannot set the "
            "end-of-speech sensitivity; Gemini's own eager turn detection may "
            "close a turn on a mid-sentence pause"
        )
        return None
    return resolved


def _usage_from_metadata(md: Any) -> dict[str, int] | None:
    """Token counts of one generation as a flat dict, or None when empty.

    Modality detail lists are optional on the wire; totals are authoritative.
    Keys: input_total/output_total plus the text/audio split when reported.
    """
    def _count(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    total_in = _count(getattr(md, "prompt_token_count", None))
    total_out = _count(getattr(md, "response_token_count", None))
    if total_in <= 0 and total_out <= 0:
        return None
    usage = {
        "input_total": total_in,
        "output_total": total_out,
        "input_text": 0,
        "input_audio": 0,
        "output_text": 0,
        "output_audio": 0,
    }
    for attr, text_key, audio_key in (
        ("prompt_tokens_details", "input_text", "input_audio"),
        ("response_tokens_details", "output_text", "output_audio"),
    ):
        for detail in tuple(getattr(md, attr, None) or ()):
            modality = getattr(detail, "modality", None)
            name = str(getattr(modality, "name", None) or modality or "").upper()
            count = _count(getattr(detail, "token_count", None))
            if count <= 0:
                continue
            usage[audio_key if "AUDIO" in name else text_key] += count
    return usage


@dataclass(frozen=True, slots=True)
class _PcmChunk:
    pcm: bytes
    sample_rate: int
    timestamp_ns: int = 0


@dataclass(frozen=True, slots=True)
class _ProviderEvent:
    type: str
    audio: _PcmChunk | None = None
    text: str | None = None
    is_final: bool = False
    ms_played: int | None = None
    error: str | None = None
    recoverable: bool = False
    reconnect_advised: bool = False
    item_id: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    # Token counts of one finished generation (see _usage_from_metadata).
    # The Live channel was previously the only brain path whose spend was
    # invisible to the recorder — 100% of Live-API tokens went unmetered.
    usage: dict[str, int] | None = None
    # This ``interrupted`` closes a generation the SERVER abandoned in favour
    # of its own retry, not one the user talked over. Mirrors the field of the
    # same name on ``jarvis.realtime.protocol.ProviderEvent``, which this
    # adapter may not import (plugins carry no ``jarvis.*`` dependency); the
    # session reads it by attribute, so the two only have to agree by name.
    superseded: bool = False


class _GeminiLiveSession:
    supports_tool_updates = False
    creates_responses_automatically = True
    # The ordered Live stream emits old output before ``interrupted`` and the
    # next input transcript. Output observed after that boundary belongs to
    # the new automatic response generation.
    isolates_response_generations = True
    # Gemini's server drops the Live WebSocket on its own schedule: session
    # limits (GoAway), and abrupt ``1006 abnormal closure`` closes — observed
    # live 2026-07-17 10:44 right after a long surface-TTS fallback during
    # which no traffic flowed, which used to end the whole call with
    # reason=error (BUG-071). This adapter has no in-protocol resume, so the
    # orchestrator may reopen a fresh session in place and continue the call.
    rebuild_on_transport_death = True
    # An answer blocked at the speech boundary cannot be re-requested here:
    # this transport only ever generates on its own VAD boundary, so the
    # orchestrator's plain ``request_response()`` fallback is a no-op and the
    # turn stays silent until the 20 s stall watchdog fires. A developer text
    # turn IS a working trigger on this channel (the same one that carries
    # every delegate readback), so the retry travels that way instead.
    supports_prompted_response_retry = True
    # Gemini's native audio is a GENERATIVE renderer, not a fixed-voice
    # synthesizer: the pinned prebuilt voice is a starting point the model
    # can audibly drift from when the spoken content reads as a performance
    # cue — observed live as gender flips between turns of one call
    # (BUG-086). A former ``renders_pinned_voice = False`` capability made
    # the orchestrator claim every delegate reply for the same-family
    # surface TTS; that escalation was REVERTED (maintainer live verdict
    # 2026-07-21: the flash-TTS rendering of the pinned voice is audibly a
    # DIFFERENT voice from the live model's native one, i.e. a guaranteed
    # voice flip on every tool-model turn). Native readback is primary
    # again; the identity clauses in the injected prompts remain the drift
    # mitigation.

    def __init__(
        self,
        *,
        session: Any,
        connection_cm: Any,
        client: Any,
        session_id: str,
        instructions: str = "",
        language: str = "",
        model: str = "",
    ) -> None:
        self._session = session
        self._connection_cm = connection_cm
        self._client = client
        self.session_id = session_id
        # The id the Live socket was opened with — the orchestrator meters
        # (and prices) usage against it, so it must be the REAL one, not the
        # card's possibly-empty pin.
        self.model = str(model or "")
        self._closed = False
        # Latest usage_metadata snapshot of the CURRENT generation. Emitted
        # once per generation boundary (tool call, turn end, barge-in) so the
        # orchestrator can sum generations without double counting the
        # progressive snapshots some SDK versions repeat mid-generation.
        self._pending_usage: dict[str, int] | None = None
        # The system instruction THIS connection was opened with. It can never
        # be replaced (see update_session), so it is also the baseline: a
        # directive whose exact text already stands in it needs no delivery.
        self._connect_instructions = str(instructions or "")
        # Steering the model has already been told, and steering that still
        # has to travel: key -> (compared value, text to send).
        self._delivered_steering: dict[str, str] = {
            "language": str(language or "")
        }
        self._pending_steering: dict[str, tuple[str, str]] = {}
        # Where the conversation stands, read off the stream this adapter
        # already parses. A steering text is a USER-side realtime input, so it
        # may only travel while the user's turn is open and the model is not
        # generating — anywhere else it would interrupt a reply in flight or
        # open a turn of its own.
        self._user_turn_open = False
        self._model_generating = False
        # Wire forensics (BUG-148). One line per client text/tool input and
        # one per server generation boundary — the minimum that lets a
        # doubled or missing reply be attributed from the log alone. The
        # 2026-08-18 18:40 session (a readback spoken twice, then three
        # answers never rendered) was undiagnosable without it: neither what
        # was sent nor which generation a boundary closed was recorded.
        self._gen_audio_bytes = 0
        self._gen_transcript_chars = 0
        self._gen_function_calls = 0
        self._gen_started_at = 0.0
        self._last_input_kind = "connect"
        self._last_input_at = time.monotonic()
        # A generation that handed out a function call is closed by the server
        # with ``interrupted`` AND ``turn_complete`` — two edges, usually two
        # messages. The ``interrupted`` edge resets the per-generation counters
        # above, so by the time ``turn_complete`` arrives the function-call
        # evidence is gone. This flag carries it across (see ``receive``).
        self._tool_boundary_pending = False
        # Spoken output (audio bytes + transcript chars) produced AFTER the
        # generation's most recent function call. Gemini does not always open
        # a new generation for the answer: when the model keeps the same
        # generation after the tool result and speaks the answer in place, its
        # ``turn_complete`` is the turn's real end — withholding it because the
        # generation once called a tool left the desktop pipeline in
        # JARVIS_SPEAKING with the microphone half-duplexed shut until the user
        # hung up (live 2026-08-23 09:25:41: "turn complete (function call,
        # boundary withheld) — audio=18.6s", no LISTENING edge ever followed;
        # same shape at 09:04:55, 09:23:42 and 09:24:38, escaped only by a
        # shouted barge-in). A boundary is a tool boundary only while this
        # counter is zero.
        self._gen_output_since_tool = 0
        # An ``interrupted`` edge this adapter attributed to its OWN text input
        # (see ``_interrupt_is_our_own_text_input``). The server closes such a
        # generation the way it closes a tool hand-off: the edge, then an empty
        # ``turn_complete``, then — seconds later — the regenerated answer.
        # That middle boundary ends nothing, and forwarding it made the session
        # treat a turn that was still coming as finished: on the 2026-08-23
        # 10:05 call the abandoned half was recorded as a turn of its own and
        # spoken before the real reply ("Alles klar. Wenn du nachher noch was  # i18n-allow
        # brauchst, sag" / "Dann wünsche ich dir einen entspannten Start in  # i18n-allow
        # die neue Woche!"). Carried across exactly like
        # ``_tool_boundary_pending`` and released by the same evidence: spoken
        # output from the generation that replaced it.
        self._superseded_pending = False

    def _note_input_sent(self, kind: str) -> None:
        self._last_input_kind = kind
        self._last_input_at = time.monotonic()

    def _interrupt_is_our_own_text_input(self) -> bool:
        """Whether this ``interrupted`` edge is our own text, not a barge-in.

        A text input on this transport is a user turn, so one that reaches the
        server while a reply is already streaming makes the server abandon
        that reply and answer again. There is no flag for it on the wire: the
        edge is byte-identical to a barge-in. What tells them apart is that a
        barge-in needs the user to speak, and the user's words reach this
        adapter as an input transcript — which is precisely what has NOT
        arrived when the last thing on the wire was our own text, milliseconds
        ago.
        """
        if self._last_input_kind not in _SELF_TEXT_INPUT_KINDS:
            return False
        return (
            time.monotonic() - self._last_input_at
        ) <= _SELF_TEXT_INTERRUPT_WINDOW_S

    def _note_generation_output(
        self, *, audio_bytes: int = 0, transcript_chars: int = 0, calls: int = 0
    ) -> None:
        if not self._gen_started_at:
            self._gen_started_at = time.monotonic()
            log.info(
                "gemini-live: generation started %.2fs after the last %s input",
                self._gen_started_at - self._last_input_at,
                self._last_input_kind,
            )
            if not calls:
                # Spoken output opens a NEW generation: whatever boundary
                # follows it is this generation's own and must reach the
                # session. A tool boundary still marked pending here belongs
                # to a server that skipped the ``turn_complete`` half of the
                # pair; never let it withhold the real end of the answer.
                self._tool_boundary_pending = False
                # Same reasoning for the superseded half: this IS the
                # regenerated answer, so its own boundary is the turn's end.
                self._superseded_pending = False
        self._gen_audio_bytes += audio_bytes
        self._gen_transcript_chars += transcript_chars
        self._gen_function_calls += calls
        if calls:
            # A new tool call opens a fresh "did it answer afterwards" window.
            self._gen_output_since_tool = 0
        else:
            self._gen_output_since_tool += audio_bytes + transcript_chars

    def _boundary_is_tool_boundary(self, function_calls: tuple[Any, ...]) -> bool:
        """Whether a boundary closes a tool hand-off rather than a spoken reply.

        True only when this generation handed out a function call (or the
        evidence was carried across the ``interrupted`` edge) AND nothing has
        been spoken since that call. A generation that called a tool and then
        answered in place owns its boundary like any other.
        """
        called_tool = (
            bool(function_calls)
            or self._gen_function_calls > 0
            or self._tool_boundary_pending
        )
        return called_tool and self._gen_output_since_tool == 0

    def _log_generation_boundary(self, *, kind: str, reason: str = "") -> None:
        started = self._gen_started_at
        log.info(
            "gemini-live: %s — audio=%.1fs transcript=%d chars function_calls=%d "
            "generation=%s; %.2fs after the last %s input%s",
            kind,
            self._gen_audio_bytes / float(_OUTPUT_RATE * 2),
            self._gen_transcript_chars,
            self._gen_function_calls,
            (f"{time.monotonic() - started:.2f}s" if started else "none"),
            time.monotonic() - self._last_input_at,
            self._last_input_kind,
            f" reason={reason}" if reason else "",
        )
        self._gen_audio_bytes = 0
        self._gen_transcript_chars = 0
        self._gen_function_calls = 0
        self._gen_output_since_tool = 0
        self._gen_started_at = 0.0

    async def send_audio(self, chunk: Any) -> None:
        from google.genai import types  # lazy (AP-26)

        sample_rate = int(getattr(chunk, "sample_rate", 0) or 0)
        if sample_rate != _INPUT_RATE:
            raise ValueError(
                f"Gemini Live requires {_INPUT_RATE} Hz PCM; received {sample_rate} Hz"
            )
        pcm = bytes(getattr(chunk, "pcm", b"") or b"")
        if not pcm:
            return
        await self._session.send_realtime_input(
            audio=types.Blob(
                data=pcm,
                mime_type=f"audio/pcm;rate={_INPUT_RATE}",
            )
        )

    async def receive(self) -> AsyncIterator[_ProviderEvent]:
        # ``google.genai.live.AsyncSession.receive()`` intentionally ends after
        # one model turn. The Jarvis provider contract spans the whole call, so
        # re-enter the SDK iterator after every clean turn boundary instead of
        # making the desktop supervisor mistake a completed answer for a dead
        # provider session.
        while not self._closed:
            turn_boundary_seen = False
            async for message in self._session.receive():
                # ``LiveServerMessage.data`` concatenates every inline audio part,
                # including Gemini 3.1 events that carry multiple parts at once.
                data = getattr(message, "data", None)
                if data:
                    self._note_model_output()
                    self._note_generation_output(audio_bytes=len(data))
                    yield _ProviderEvent(
                        type="audio_delta",
                        audio=_PcmChunk(pcm=bytes(data), sample_rate=_OUTPUT_RATE),
                    )

                usage_metadata = getattr(message, "usage_metadata", None)
                if usage_metadata is not None:
                    usage = _usage_from_metadata(usage_metadata)
                    if usage is not None:
                        self._pending_usage = usage

                tool_call = getattr(message, "tool_call", None)
                function_calls = tuple(
                    getattr(tool_call, "function_calls", None) or ()
                )
                if function_calls and self._pending_usage is not None:
                    # A tool call ends this generation; report its usage now so
                    # the follow-up generation's snapshot cannot overwrite it.
                    yield _ProviderEvent(type="usage", usage=self._pending_usage)
                    self._pending_usage = None
                if function_calls:
                    self._note_model_output()
                    self._note_generation_output(calls=len(function_calls))
                    log.info(
                        "gemini-live: function call(s) %s",
                        ", ".join(
                            str(getattr(call, "name", "") or "?")
                            for call in function_calls
                        ),
                    )
                for function_call in function_calls:
                    raw_args = getattr(function_call, "args", None) or {}
                    if hasattr(raw_args, "model_dump"):
                        raw_args = raw_args.model_dump()
                    try:
                        args = dict(raw_args)
                    except (TypeError, ValueError):
                        args = {}
                    yield _ProviderEvent(
                        type="tool_call",
                        call_id=str(getattr(function_call, "id", "") or ""),
                        tool_name=str(getattr(function_call, "name", "") or ""),
                        tool_args=args,
                    )

                content = getattr(message, "server_content", None)
                if content is not None:
                    output_transcription = getattr(
                        content, "output_transcription", None
                    )
                    output_text = str(
                        getattr(output_transcription, "text", "") or ""
                    )
                    if output_text:
                        self._note_model_output()
                        self._note_generation_output(
                            transcript_chars=len(output_text)
                        )
                        yield _ProviderEvent(
                            type="output_transcript_delta", text=output_text
                        )

                    # The interrupted flag is the boundary between the partial
                    # assistant reply above and any new user transcript carried by
                    # the same server-content message. Emit it first so the shared
                    # session closes the old turn before adopting the new words.
                    if bool(getattr(content, "interrupted", False)):
                        if self._pending_usage is not None:
                            # Barge-in ends the generation; its tokens were
                            # still billed.
                            yield _ProviderEvent(
                                type="usage", usage=self._pending_usage
                            )
                            self._pending_usage = None
                        # A barge-in ends the generation, so the channel is
                        # free again for the user turn that just started.
                        self._model_generating = False
                        if self._boundary_is_tool_boundary(function_calls):
                            # NOT a barge-in. The server closes a generation
                            # that handed out a function call with
                            # ``interrupted`` immediately followed by
                            # ``turn_complete`` — observed on every one of the
                            # 7 tool calls of the 2026-08-22 18:39 session,
                            # and on the wire the pair arrives while the tool
                            # is still running (the session reads it right
                            # after it sends the tool result). The boundary
                            # logger below resets the function-call counter,
                            # so the ``turn_complete`` withhold further down —
                            # which keys on that counter — never once fired:
                            # 12 tool calls today, 1 withheld, 7 leaked. Each
                            # leak made the session take the empty boundary
                            # for a mute provider and send its "retry the
                            # speech" text; when the answer generation was
                            # already streaming, that text interrupted it
                            # server-side ("Gerne. Diese Klage von den  # i18n-allow
                            # Bundesstaaten wirft Meta vor, dass sie ihre
                            # Plattformen" — end of reply), and the model's
                            # second answer was then discarded as a stale
                            # generation. Keep the evidence across the edge
                            # and withhold the edge itself: a real barge-in
                            # during a tool run still confirms itself through
                            # the user's input transcript moments later. An
                            # edge AFTER the generation already spoke past
                            # its tool call is not this pair — the model
                            # answered in place — so it stays a barge-in.
                            self._tool_boundary_pending = True
                            self._log_generation_boundary(
                                kind="interrupted (function call, edge withheld)"
                            )
                        else:
                            # Not a tool boundary — but possibly not a barge-in
                            # either. Our own per-turn directive travels as a
                            # realtime text input at the final-transcript
                            # boundary, which is the same millisecond the
                            # server starts answering; when it loses that race
                            # the server abandons the reply it had already
                            # streamed and generates a fresh one. Both halves
                            # used to be spoken, the first cut mid-sentence.
                            # Naming the edge is the whole fix on this side:
                            # the session drops what the far end abandoned.
                            superseded = self._interrupt_is_our_own_text_input()
                            self._log_generation_boundary(
                                kind=(
                                    f"interrupted (our own {self._last_input_kind} "
                                    "input; the generation is superseded)"
                                    if superseded
                                    else "interrupted"
                                )
                            )
                            if superseded:
                                self._superseded_pending = True
                            yield _ProviderEvent(
                                type="interrupted", superseded=superseded
                            )

                    input_transcription = getattr(
                        content, "input_transcription", None
                    )
                    input_text = str(
                        getattr(input_transcription, "text", "") or ""
                    )
                    if input_text:
                        # The user is audibly mid-turn: this is the one window
                        # in which steering may travel. Flush whatever an
                        # earlier update_session could not deliver BEFORE the
                        # orchestrator reacts to the transcript, so the delta
                        # is in context for the reply this turn produces.
                        self._user_turn_open = True
                        # The user's words end every withhold that keys on
                        # "the far end is about to speak again": whatever the
                        # server was regenerating, this turn supersedes it,
                        # and a boundary held for the old one must not close
                        # the new one instead.
                        self._superseded_pending = False
                        await self._flush_steering()
                        yield _ProviderEvent(
                            type="input_transcript", text=input_text, is_final=True
                        )

                    if bool(getattr(content, "turn_complete", False)):
                        turn_boundary_seen = True
                        self._model_generating = False
                        self._user_turn_open = False
                        # Every named TurnCompleteReason except UNSPECIFIED is
                        # an ABNORMAL stop (safety filter, response rejection,
                        # regeneration limit, ...). A natural end leaves the
                        # field unset. Discarding it made a server-truncated
                        # spoken reply indistinguishable from a complete one
                        # (live incident 2026-07-15 17:40: ~10% of the answer
                        # was never spoken, turn_complete looked clean).
                        reason = getattr(content, "turn_complete_reason", None)
                        reason_name = str(
                            getattr(reason, "name", None) or reason or ""
                        )
                        if reason_name and reason_name != (
                            "TURN_COMPLETE_REASON_UNSPECIFIED"
                        ):
                            log.warning(
                                "Gemini Live ended the turn abnormally: "
                                "turn_complete_reason=%s — the spoken reply "
                                "may have been cut short by the server",
                                reason_name,
                            )
                        if self._pending_usage is not None:
                            yield _ProviderEvent(
                                type="usage", usage=self._pending_usage
                            )
                            self._pending_usage = None
                        # A generation that handed out a function call has NOT
                        # finished the turn: Gemini closes it so the tool result
                        # can travel, then opens a NEW generation carrying the
                        # spoken answer. The withhold below has stood since the
                        # adapter was written, but it read the PER-MESSAGE
                        # ``function_calls`` — and the server sends the tool call
                        # and this boundary in SEPARATE messages, so it never
                        # once fired (live 2026-08-20: 39 boundaries emitted, 0
                        # withheld). The session took every one of them for a
                        # mute provider, spoke the direct-tool fallback line
                        # ("Erledigt.") over the user's question, and then
                        # withheld the real answer that arrived two seconds
                        # later. The per-GENERATION counter is the evidence that
                        # survives the message split; it is read here because
                        # ``_log_generation_boundary`` resets it. The OpenAI
                        # adapter has always done this correctly with its
                        # response-scoped ``_response_had_tool_calls`` flag —
                        # this brings Gemini/Vertex Live to the same contract.
                        # If the model never generates again, the session's own
                        # turn-stall watchdog closes the turn honestly; it is
                        # not this adapter's job to fake a boundary.
                        # ``_tool_boundary_pending`` is the same evidence
                        # carried across the ``interrupted`` edge the server
                        # emits first (see above): without it the counter is
                        # already zero here and the boundary leaks.
                        # The converse leak is just as real: the model does
                        # NOT always open a new generation for the answer.
                        # When it keeps the same generation after the tool
                        # result and speaks in place, this boundary IS the
                        # turn's end; withholding it because the generation
                        # once called a tool froze the desktop pipeline in
                        # JARVIS_SPEAKING with the microphone shut (live
                        # 2026-08-23 09:25:41, audio=18.6s, no LISTENING edge
                        # until the user hung up). Spoken output since the
                        # last tool call is what tells the two apart.
                        answered_in_place = (
                            bool(function_calls) or self._gen_function_calls > 0
                        ) and self._gen_output_since_tool > 0
                        tool_generation = self._boundary_is_tool_boundary(
                            function_calls
                        )
                        self._tool_boundary_pending = False
                        # The other half of the superseded pair. A generation
                        # the server abandoned in favour of its own retry is
                        # closed by an EMPTY boundary — the regenerated answer
                        # arrives seconds later under its own. Forwarding this
                        # one ends a turn whose reply has not been spoken yet;
                        # the session then reads the silence as a mute
                        # provider and sends a re-ask text, which is one more
                        # input racing the very generation everyone is waiting
                        # for. Spoken output releases the flag above, so a
                        # boundary that follows the retry is never the one
                        # withheld here, and a second empty boundary passes
                        # unchanged rather than latching the turn open.
                        superseded_boundary = (
                            self._superseded_pending and not tool_generation
                        )
                        self._superseded_pending = False
                        self._log_generation_boundary(
                            kind=(
                                "turn complete (function call, boundary "
                                "withheld)"
                                if tool_generation
                                else (
                                    "turn complete (superseded generation, "
                                    "boundary withheld)"
                                    if superseded_boundary
                                    else (
                                        "turn complete (function call "
                                        "answered in place)"
                                        if answered_in_place
                                        else "turn complete"
                                    )
                                )
                            ),
                            reason=reason_name,
                        )
                        if not tool_generation and not superseded_boundary:
                            yield _ProviderEvent(type="turn_complete")

                go_away = getattr(message, "go_away", None)
                if go_away is not None:
                    retry_ms = getattr(go_away, "time_left", None)
                    suffix = (
                        f" (time_left={retry_ms})" if retry_ms is not None else ""
                    )
                    # GoAway is Gemini's courteous pre-disconnect notice tied
                    # to Live-API session limits, not a wire failure. Treating
                    # it as terminal used to end the session with reason=error
                    # while the current reply was still being spoken, dropping
                    # the buffered tail. Surface it as recoverable AND advise
                    # a proactive rebuild: a client that merely keeps using
                    # the socket is hard-killed with 1008 when the window
                    # expires (live 2026-07-21 11:14 — the forced close raced
                    # the recovery chain and the call ended reason=error).
                    yield _ProviderEvent(
                        type="error",
                        error=f"Gemini Live requested reconnect{suffix}",
                        recoverable=True,
                        reconnect_advised=True,
                    )

            # An iterator that vanishes without a model-turn boundary signals a
            # closed/broken transport. Let the shared session observe that end;
            # retrying it here would spin on an empty iterator forever.
            if not turn_boundary_seen:
                return

    def _note_model_output(self) -> None:
        """The model is producing this turn — the user's turn is over."""
        self._model_generating = True
        self._user_turn_open = False

    def _note_steering(self, key: str, value: str, rendered: str) -> None:
        """Queue one steering key when it differs from what the model knows."""
        value = str(value or "")
        if not value:
            return
        queued = self._pending_steering.get(key)
        known = queued[0] if queued is not None else self._delivered_steering.get(key)
        if known == value:
            # Unchanged since the model was last told: nothing travels. The
            # orchestrator rebuilds these strings every turn even when nothing
            # in them moved, and re-asserting an identical directive is pure
            # cost on a channel that re-bills its whole context per turn.
            return
        if (
            key not in self._delivered_steering
            and queued is None
            and value in self._connect_instructions
        ):
            # First sighting, and this exact text already stands in the fixed
            # system instruction of this connection — the model has it.
            self._delivered_steering[key] = value
            return
        self._pending_steering[key] = (value, rendered)

    async def _flush_steering(self) -> None:
        """Send the queued steering delta, if the channel is safe right now."""
        if not self._pending_steering:
            return
        if self._model_generating or not self._user_turn_open:
            # Outside the user's open turn a text input is a turn of its own:
            # it would interrupt a reply in flight or provoke an unprompted
            # one. The delta waits for the next user utterance instead.
            return
        # ONE ordered pass decides both what travels and what counts as told.
        # Building the text from one sequence and marking delivered from
        # another is how a key silently becomes "the model knows this" without
        # ever leaving the process — the same swallow this method exists to
        # remove, one level deeper. A key outside the known order is appended
        # last instead of vanishing.
        ordered = [key for key in _STEERING_ORDER if key in self._pending_steering]
        ordered += [
            key for key in self._pending_steering if key not in _STEERING_ORDER
        ]
        ordered = [key for key in ordered if self._pending_steering[key][1]]
        if not ordered:
            # Every queued key renders to nothing, so there is nothing to say.
            self._pending_steering.clear()
            return
        body = "\n\n".join(self._pending_steering[key][1] for key in ordered)
        try:
            await self._session.send_realtime_input(
                text=f"{_STEERING_HEADER}\n\n{body}"
            )
        except Exception:  # noqa: BLE001 — steering must not kill the call
            log.warning(
                "gemini-live: delivering the per-turn directive update failed; "
                "the model keeps the previous one and the next user turn "
                "retries",
                exc_info=True,
            )
            return
        for key in ordered:
            self._delivered_steering[key] = self._pending_steering[key][0]
        self._pending_steering.clear()
        self._note_input_sent("steering text")
        # Keys only, never the directive text: this line exists so a call where
        # the model "did not answer the way it normally would" can be checked
        # against what actually reached it — and, since it is a text input of
        # its own on this channel, so a generation it provokes is attributable.
        log.info(
            "gemini-live: delivered a per-turn steering delta (%s; %d chars)",
            ", ".join(ordered),
            len(body),
        )

    async def update_session(
        self,
        *,
        instructions: str | None = None,
        language: str | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
        turn_directive: str | None = None,
        standing_directive: str | None = None,
    ) -> None:
        """Deliver what CHANGED; everything fixed stays at connect time.

        The Live protocol has no mid-session setup message — a
        ``LiveClientMessage`` carries setup, client_content, realtime_input or
        tool_response, and setup is sent once at connect — so this
        connection's system instruction and tool declarations genuinely cannot
        be replaced. That used to mean the orchestrator's per-turn rebuild was
        dropped whole: a delegate directive or a language pin that changed
        mid-call never reached the model, which then answered from a frozen
        connect-time prompt while the session filtered as if the new rules
        were in force.

        The changed part now travels as a short developer text turn on the
        same realtime-input channel that carries every delegate readback.
        Only the DELTA travels: the ~21k instruction block is never re-sent,
        an unchanged directive sends nothing at all, and a newer value
        supersedes an older undelivered one whole.
        """
        # Both are connect-time on this transport. ``tools`` is not silently
        # lost either: the session reads ``supports_tool_updates = False``
        # above and warns that changed declarations stand until the next
        # session. ``instructions`` is the block whose changing parts arrive
        # below as the delta.
        del instructions, tools
        self._note_steering(
            "language",
            str(language or ""),
            "Output language pin: speak every following reply in this "
            f"language only: {language}.",
        )
        turn_text = str(turn_directive or "")
        self._note_steering("turn", turn_text, turn_text)
        standing_text = str(standing_directive or "")
        self._note_steering("standing", standing_text, standing_text)
        await self._flush_steering()

    async def request_response(self, *, required_tool: str | None = None) -> None:
        # Gemini Live creates a response automatically at the VAD turn boundary.
        del required_tool
        return None

    async def send_text(self, text: str) -> None:
        """Send an incremental text turn through the current Gemini 3.1 API."""
        # Gemini 3.1 permits send_client_content only for initial history.
        # Runtime text updates must use the realtime-input text stream.
        payload = str(text)
        log.info(
            "gemini-live: text input sent (%d chars; model_generating=%s "
            "user_turn_open=%s)",
            len(payload),
            self._model_generating,
            self._user_turn_open,
        )
        await self._session.send_realtime_input(text=payload)
        self._note_input_sent("text")

    async def truncate(self, audio_end_ms: int) -> None:
        del audio_end_ms  # Gemini interrupts generation when new audio arrives.

    async def interrupt(self) -> None:
        # The Live API has no separate response-cancel call for this flow.
        return None

    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None:
        from google.genai import types  # lazy (AP-26)

        log.info(
            "gemini-live: tool response sent for %s (model_generating=%s)",
            name or "?",
            self._model_generating,
        )
        await self._session.send_tool_response(
            function_responses=[
                types.FunctionResponse(
                    id=call_id,
                    name=name,
                    response=result,
                )
            ]
        )
        self._note_input_sent("tool response")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._connection_cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            log.debug("gemini-live: session close raised", exc_info=True)
        finally:
            close = getattr(self._client, "close", None)
            if close is not None:
                try:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
                except Exception:  # noqa: BLE001
                    log.debug("gemini-live: client close raised", exc_info=True)


async def _seed_history(session: Any, history: tuple[dict[str, str], ...]) -> None:
    """Restore the call transcript into a freshly connected Live session.

    Gemini fixes conversation state per connection: a mid-call transport
    rebuild (session limit GoAway, 1006 abnormal closure) used to start the
    fresh session with total amnesia, so follow-up questions lost their
    grounding (BUG-088).

    Gemini 3.1 Live accepts ``client_content`` ONLY as declared initial
    history: the connection config must set
    ``history_config.initial_history_in_client_content`` (done in
    ``open_session``) and the server then processes ``client_content``
    messages *until* one arrives with ``turn_complete=True`` — only after
    that boundary is ``realtime_input`` (microphone audio) legal. The first
    seed implementation sent ``turn_complete=False`` without the
    declaration; the server closed every rebuilt connection with ``1007
    invalid argument`` ~70 ms after ready, three rebuilds burned the whole
    recovery window, and the call ended reason=error mid-sentence (live
    incident 2026-07-21 08:35, BUG-104).

    Seeding still fails open: a session without history is exactly the
    pre-BUG-088 behavior and strictly better than no session at all.
    """
    if not history:
        return
    # The whole seed is one guarded unit — including SDK type construction:
    # a types.Content/Part validation error must degrade exactly like a
    # rejected wire call, never fail the provider handshake.
    try:
        from google.genai import types  # lazy (AP-26)

        turns = []
        for message in history:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "") or "")
            text = str(message.get("text", "") or "").strip()
            if not text or role not in {"user", "assistant"}:
                continue
            turns.append(
                types.Content(
                    role="user" if role == "user" else "model",
                    parts=[types.Part(text=text)],
                )
            )
        # turn_complete=True closes the declared initial-history phase even
        # when every entry was filtered out — the connection would otherwise
        # sit in history mode and reject the first microphone frame.
        await session.send_client_content(
            turns=turns or None, turn_complete=True
        )
    except Exception:  # noqa: BLE001 — an amnesiac session beats a dead call
        log.warning(
            "gemini-live: seeding %d prior turns into the fresh session "
            "failed; the conversation continues without in-call context",
            len(history),
            exc_info=True,
        )
        # The connection already declared initial-history mode; exit it even
        # without content, otherwise the first microphone frame is the next
        # invalid argument.
        try:
            await session.send_client_content(turns=None, turn_complete=True)
        except Exception:  # noqa: BLE001, S110 — transport is likely dead
            pass


# Gemini function_declarations accept only an OpenAPI-style schema subset.
# Standard JSON-schema keys like additionalProperties, $ref/$defs, or
# oneOf/anyOf/allOf make the handshake fail — which silently drops the whole
# provider to the fallback family. Sanitizing is this adapter's wire-format
# translation; the bridge declarations (and the OpenAI path) keep the full
# schema.
_GEMINI_SCHEMA_KEYS = frozenset(
    {
        "type",
        "description",
        "enum",
        "properties",
        "required",
        "items",
        "nullable",
        "minimum",
        "maximum",
        "default",
    }
)


def _sanitize_schema_for_gemini(schema: Any, *, tool_name: str = "") -> Any:
    if not isinstance(schema, dict):
        return schema
    sanitized: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _GEMINI_SCHEMA_KEYS:
            # Drop unsupported keys but keep their siblings: the tool stays
            # usable with a permissive schema instead of bricking the session.
            log.debug(
                "gemini-live: dropping unsupported schema key %r (tool=%s)",
                key,
                tool_name or "unknown",
            )
            continue
        if key == "properties" and isinstance(value, dict):
            sanitized[key] = {
                name: _sanitize_schema_for_gemini(sub, tool_name=tool_name)
                for name, sub in value.items()
            }
        elif key == "items":
            sanitized[key] = _sanitize_schema_for_gemini(value, tool_name=tool_name)
        else:
            sanitized[key] = value
    return sanitized


def _sanitize_declarations(tools: tuple[Any, ...]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for declaration in tools:
        if not isinstance(declaration, dict):
            continue
        entry = dict(declaration)
        name = str(entry.get("name", "") or "")
        if isinstance(entry.get("parameters"), dict):
            entry["parameters"] = _sanitize_schema_for_gemini(
                entry["parameters"], tool_name=name
            )
        sanitized.append(entry)
    return sanitized


class GeminiLiveProvider:
    """Structural provider entry point for the Gemini Live family."""

    name = "gemini-live"
    #: Live model used when the card pins none. A class attribute, not the bare
    #: module constant, because the AI Studio and Vertex catalogues do NOT share
    #: their Live ids — see VertexLiveProvider.
    default_model = _MODEL
    #: Prebuilt voice used when the card pins none. Always sent as
    #: ``PrebuiltVoiceConfig`` so the Live socket and the same-family
    #: surface TTS share one identity. Google's unpinned native-audio
    #: default is undocumented; leaving the field empty made every
    #: progress / fallback line speak Charon (BUG-155). Kore is the
    #: current Live API example voice. A per-card pin still overrides it.
    default_voice = "Kore"
    # Optional provider capability consumed by the shared session fallback.
    # Every adapter that draws from the same account quota must expose the
    # same value so a terminal billing/auth failure is not retried through an
    # alias backed by the very same credential family (AP-22).
    credential_family = "gemini"
    supports_realtime = True
    implicit_usage_fallback_allowed = True
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE
    credential_candidates = (
        ("realtime_gemini_api_key", "JARVIS_REALTIME_GEMINI_API_KEY"),
        ("gemini_api_key", "GEMINI_API_KEY"),
        ("google_aistudio_api_key", "GOOGLE_AIStudio_API_KEY"),
        ("google_api_key", "GOOGLE_API_KEY"),
    )

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = (api_key or "").strip()

    async def can_open_duplex_session(self) -> bool:
        return bool(self._api_key)

    async def _build_client(self) -> Any:
        """Routed builder: AI Studio or Vertex express, decided per key.

        The async twin keeps a first-ever key's routing probe off the event
        loop; every later session open hits the process-wide cache. importlib,
        not a literal ``from jarvis...``: the plugin-module contract (no jarvis
        imports, AST-checked) counts lazy imports too.

        Overridden by :class:`VertexLiveProvider`, which knows its endpoint.
        """
        import importlib  # lazy (AP-26)

        google_genai = importlib.import_module("jarvis.core.google_genai")
        return await google_genai.build_genai_client_async(self._api_key)

    def _unconfigured_message(self) -> str:
        return "Gemini Live API key is not configured"

    @staticmethod
    async def warm_transport(cfg: Any = None) -> None:
        """Build the shared TLS trust store at boot, off the first handshake.

        Every ``genai.Client`` used to build three SSL contexts of its own —
        each parsing the certifi bundle, ~1.3 s per client on the maintainer
        box, on the event loop for a Live open. The process now shares ONE
        context (``jarvis.core.google_genai``); warming it here means the first
        session of the call pays for the socket alone. Best-effort by contract
        — a warm that did not happen only costs the latency it was meant to
        save. Overridden by :class:`VertexLiveProvider`, which additionally
        resolves its Cloud credentials.
        """
        del cfg  # nothing session-specific about a trust store
        import asyncio  # lazy (AP-26)
        import importlib

        google_genai = importlib.import_module("jarvis.core.google_genai")
        await asyncio.to_thread(google_genai.warm_shared_transport)

    async def open_session(self, cfg: Any) -> _GeminiLiveSession:
        if not await self.can_open_duplex_session():
            raise RuntimeError(self._unconfigured_message())

        from google.genai import types  # lazy (AP-26)

        client = await self._build_client()
        voice = str(getattr(cfg, "voice", "") or "").strip() or str(
            getattr(self, "default_voice", "") or ""
        ).strip()
        if not str(getattr(cfg, "voice", "") or "").strip() and voice:
            log.info(
                "%s: no card voice pinned; using adapter default %r",
                self.name,
                voice,
            )
        speech_config: dict[str, Any] = {}
        if voice:
            speech_config["voice_config"] = types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice
                )
            )
        # This transport answers on its own activity boundary
        # (``creates_responses_automatically``): Jarvis cannot hold a reply
        # back once Gemini has closed the user's turn, so the ONE lever for
        # "wait for a clear pause before you take the turn" is Gemini's own
        # silence window. The user's Thinking pause (``turn_pause_ms``, the
        # same Settings value the classic pipeline endpoints on) is folded in
        # here; an explicit ``silence_duration_ms`` override still wins, and
        # with neither set Gemini keeps deciding turn ends on its own timing.
        # A user who resumes inside the window continues the SAME activity —
        # the words append, nothing is submitted twice (2026-08-18).
        silence_ms = getattr(cfg, "silence_duration_ms", None) or getattr(
            cfg, "turn_pause_ms", None
        )
        # End-of-speech SENSITIVITY is a different knob and stays: Gemini's
        # native default reads an ordinary mid-sentence pause as the end of
        # the turn. Live 2026-08-13 16:46/16:47 — one spoken brief for a
        # coding pane was committed twice while the microphone still carried
        # the user's voice, and the pane was handed a quarter of the sentence.
        # LOW reads a pause as a pause; the silence window above then says how
        # long that pause may be.
        aad_kwargs: dict[str, Any] = {}
        if silence_ms:
            aad_kwargs["silence_duration_ms"] = int(silence_ms)
        sensitivity = _end_of_speech_sensitivity(
            types, getattr(cfg, "end_of_speech_sensitivity", None)
        )
        if sensitivity is not None:
            aad_kwargs["end_of_speech_sensitivity"] = sensitivity
        # Gemini 3.1 rejects client_content (1007, the whole connection dies)
        # unless the setup declares it as initial history — so the declaration
        # and the seed travel together, and neither happens without the other
        # (BUG-104). Probed by SDK capability, never a model-name pin (AP-21):
        # an SDK without HistoryConfig cannot seed legally, so it opens an
        # amnesiac session instead of a doomed one.
        history = tuple(getattr(cfg, "history", ()) or ())
        history_config_cls = getattr(types, "HistoryConfig", None)
        seed_declared = bool(history) and history_config_cls is not None
        if history and not seed_declared:
            log.warning(
                "gemini-live: installed google-genai SDK has no HistoryConfig; "
                "opening the rebuilt session without the %d-turn conversation "
                "seed",
                len(history),
            )
        live_config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=str(getattr(cfg, "instructions", "") or "") or None,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            **(
                {
                    "realtime_input_config": types.RealtimeInputConfig(
                        automatic_activity_detection=types.AutomaticActivityDetection(
                            disabled=False,
                            **aad_kwargs,
                        )
                    )
                }
                if aad_kwargs
                else {}
            ),
            **(
                {
                    "tools": [
                        {
                            "function_declarations": _sanitize_declarations(
                                tuple(getattr(cfg, "tools", ()) or ())
                            )
                        }
                    ]
                }
                if tuple(getattr(cfg, "tools", ()) or ())
                else {}
            ),
            **(
                {"speech_config": types.SpeechConfig(**speech_config)}
                if speech_config
                else {}
            ),
            **(
                {
                    "history_config": history_config_cls(
                        initial_history_in_client_content=True
                    )
                }
                if seed_declared
                else {}
            ),
            **_compression_kwargs(types),
        )
        connect_model = str(getattr(cfg, "model", "") or self.default_model)
        connection_cm = client.aio.live.connect(
            model=connect_model,
            config=live_config,
        )
        try:
            session = await connection_cm.__aenter__()
        except BaseException:
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
            raise
        if seed_declared:
            await _seed_history(session, history)
        return _GeminiLiveSession(
            session=session,
            connection_cm=connection_cm,
            client=client,
            session_id=str(uuid4()),
            # The baseline for the per-turn steering delta: whatever already
            # stands in this connection's fixed instruction never has to be
            # re-delivered (see update_session).
            instructions=str(getattr(cfg, "instructions", "") or ""),
            language=str(getattr(cfg, "language", "") or ""),
            model=connect_model,
        )


class VertexLiveProvider(GeminiLiveProvider):
    """The Gemini Live duplex socket opened on Google Cloud Vertex AI.

    Same wire protocol, same session machinery, same event translation — the
    difference is which account pays and which host answers. It is a distinct
    ``credential_family`` on purpose: that value is what stops the shared
    session fallback from retrying a terminal billing or auth failure through an
    alias backed by the very same credential (AP-22). A Cloud project and an AI
    Studio account fail independently, so crossing between them is a real
    fallback rather than a doomed retry.

    Authentication, measured 2026-08-17 against a live Cloud project: Vertex
    accepts an API key ONLY in express mode. A standard Google Cloud API key —
    even one restricted to ``aiplatform.googleapis.com`` — is refused on every
    Vertex surface with "API keys are not supported by this API. Expected OAuth2
    access token or other authentication credentials that assert a principal",
    and the Live socket closes with 1008 carrying that same text. So the Cloud
    project path (``[google].vertex_project`` + Application Default
    Credentials) is not a nicety here; for a normal project it is the only way
    in. The card says so.

    If a given key or project cannot open the duplex socket, the handshake fails
    and the realtime factory crosses to the next credential-ready provider — and
    finally to the classic pipeline. Nothing here claims a capability it has not
    proven.
    """

    name = "vertex-live"
    #: Vertex publishes its OWN Live model ids; the AI Studio id this class
    #: would otherwise inherit does not exist there, so realtime 404'd on the
    #: model even once authentication succeeded. Verified present in both probed
    #: regions (europe-west4 and us-central1, live publisher catalogue
    #: 2026-08-17). A per-card model pin still overrides it.
    default_model = "gemini-live-2.5-flash-native-audio"
    #: Measured 2026-08-17 on an idle machine: 3.7-6.5 s per open when the
    #: client resolves Application Default Credentials itself (a ``gcloud``
    #: subprocess plus the OAuth exchange, 5.3-8.5 s on a busy box) before the
    #: socket is even attempted — a cost the API-key providers never pay. The
    #: shared 12 s ceiling divided by the candidate count handed this adapter
    #: 6.0 s, i.e. the measured worst case plus half a second, so a busy
    #: machine timed out the handshake and dropped the call to the pipeline.
    #: With the process-wide credentials from ``warm_transport`` an open costs
    #: 1.1-1.3 s; the budget stays generous for the cold path (no warm yet, a
    #: login that changed) — declaring the real need is a capability, never a
    #: provider-name check (AP-21), and it is what the session's deadline
    #: stretch exists for.
    handshake_budget_s = 20.0
    credential_family = "vertex"
    credential_candidates = (
        ("realtime_vertex_api_key", "JARVIS_REALTIME_VERTEX_API_KEY"),
        ("vertex_api_key", "VERTEX_API_KEY"),
        ("google_vertex_api_key", "GOOGLE_VERTEX_API_KEY"),
    )

    def _unconfigured_message(self) -> str:
        return (
            "Vertex AI is not configured. Store a Vertex AI API key "
            "(VERTEX_API_KEY / the Vertex AI card in the API-Keys view), or set "
            "[google].vertex_project for the Google Cloud project path."
        )

    @staticmethod
    async def warm_transport(cfg: Any = None) -> None:
        """Resolve the ADC and mint the first token at boot, off the first turn.

        Measured 2026-08-17: resolving Application Default Credentials costs
        5.3-8.5 s on a gcloud-login host (google-auth spawns ``gcloud config
        config-helper`` for a project id) and the OAuth exchange another
        0.7-1.9 s — and both used to happen INSIDE every handshake, because
        each session built a client that resolved auth on its own (5.7-12.2 s
        to "session ready"). ``warm_vertex_credentials`` loads ONE process-wide
        credentials object and mints its token; every later client build is
        handed that object, so a session open pays for the socket alone
        (measured 1.1-1.3 s).

        Best-effort by contract — the factory swallows failures, and a warm that
        did not happen only costs the latency it was meant to save.
        """
        del cfg  # the credential state is read from config, not the session
        import asyncio  # lazy (AP-26)
        import importlib

        google_genai = importlib.import_module("jarvis.core.google_genai")
        if not VertexLiveProvider.external_login_ready(None):
            return
        # Off the event loop: the credential resolution and the token exchange
        # are blocking calls (a subprocess and an HTTPS round-trip).
        ready = await asyncio.to_thread(google_genai.warm_vertex_credentials)
        if ready:
            log.info(
                "vertex-live: Application Default Credentials warmed — token "
                "minted, later session opens pay for the socket alone."
            )
        else:
            log.info(
                "vertex-live: Application Default Credentials not warmed — the "
                "first handshake resolves auth itself."
            )

    @staticmethod
    def external_login_ready(cfg: Any = None) -> bool:
        """Whether a credential OUTSIDE this app can sign Vertex requests.

        The factory hands API providers a resolved key and skips the ones with
        none; a provider that authenticates by some other means declares this
        capability instead. It was written for subscription CLI logins, and a
        Google Cloud project reached through Application Default Credentials is
        the same shape of fact: a credential that lives on the machine, not in
        the keyring. Declaring it is what makes the project path selectable at
        all — and the factory only ever honours it for a provider the user
        EXPLICITLY chose, so an ambient Cloud login is never spent by accident.
        """
        del cfg  # the answer comes from the credential state, not the session
        import importlib  # lazy (AP-26)

        try:
            config = importlib.import_module("jarvis.core.config")
            return bool(config.vertex_credential_configured())
        except Exception:  # noqa: BLE001 — an unreadable config is an unset one
            return False

    async def can_open_duplex_session(self) -> bool:
        """A key OR a configured Cloud project makes this transport eligible.

        The factory hands every API provider a resolved key and treats an empty
        one as "skip". Vertex breaks that assumption legitimately: the project
        path authenticates via Application Default Credentials and stores no
        key, so without this the documented production setup would never become
        a candidate at all.
        """
        if self._api_key:
            return True
        import importlib  # lazy (AP-26)

        config = importlib.import_module("jarvis.core.config")
        return bool(config.vertex_credential_configured())

    async def _build_client(self) -> Any:
        """Pinned to Vertex, and pointed at the endpoint that serves Live.

        ``realtime=True`` matters: the ``global`` endpoint a Vertex brain wants
        (it is where the current Gemini generation lives) opens no Live session
        whatsoever, so the socket resolves its own region.
        """
        import importlib  # lazy (AP-26)

        google_genai = importlib.import_module("jarvis.core.google_genai")
        return await google_genai.build_vertex_client_async(
            self._api_key, realtime=True
        )
