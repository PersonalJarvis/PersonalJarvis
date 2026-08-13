"""OpenAI Realtime provider plugin.

The module is structurally compatible with the realtime protocol but imports
no ``jarvis.*`` modules. Credentials and configuration are injected by the
orchestrator. The OpenAI SDK stays lazy and is imported only when a session is
opened, keeping the provider off the startup path (AP-26).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

log = logging.getLogger(__name__)

_MODEL = "gpt-realtime"
_INPUT_RATE = 24_000
_OUTPUT_RATE = 24_000
_HANDSHAKE_TIMEOUT_S = 12.0
_RESPONSE_REQUEST_METADATA_KEY = "jarvis_request_id"
# BUG-064: an unsolicited response is proof the server no longer honors the
# manual-response contract (``create_response: false``). One xAI Grok live
# session additionally stopped emitting input transcription events after a
# legitimate barge-in ``response.cancel`` — the session stayed connected but
# went permanently deaf. Re-sending the full session payload restores both
# halves of the contract; the cooldown keeps a burst of unsolicited responses
# from turning into a session.update storm.
_CONTRACT_REARM_COOLDOWN_S = 5.0
# BUG-064 escalation (grok-realtime 2026-07-16 09:23): the re-arm demonstrably
# ran and the server STILL never delivered another input transcript — the call
# sat in LISTENING until manual hang-up. Once the server has provably heard a
# user turn (an input_audio_buffer commit, or an auto-created response it is
# forbidden to create), an input transcript is owed under the session
# contract. If none arrives within this window while no response lifecycle is
# active, the transcription side of the session is dead beyond what a
# session.update can repair, and the transport itself is rebuilt in place.
_TRANSCRIPT_OVERDUE_S = 6.0
# A suppressed auto-response that follows a transcript almost immediately is
# the benign duplicate race seen on openai-realtime 2026-07-15 (our
# response.create crossing the server's), not deafness — only arm the
# transcript deadline when the last transcript is comfortably in the past.
_SUPPRESS_ARM_MIN_QUIET_S = 2.0
# BUG-064 recurrence #3 (grok-realtime 2026-07-16 10:51, session 1fd3fa38):
# the client accepted its own requested response, a local barge-in dropped its
# output, and the server never sent that response's ``response.done`` — so
# ``_response_idle`` stayed CLEAR forever. Every deaf-wedge defense gates on
# idle ("with a response in flight no transcript is owed"), so adoption, the
# transcript deadline, and the transport rebuild were ALL disarmed at once and
# the session sat silent until manual hang-up. Once material output begins, a
# healthy response streams every few tens of milliseconds; silence for this
# long then proves the lifecycle is wedged. Waiting for the first output is a
# separate provider capability because local inference can take much longer.
_RESPONSE_STALL_S = 8.0
_RESPONSE_OUTPUT_EVENTS = frozenset(
    {
        "response.output_audio.delta",
        "response.output_audio.done",
        "response.output_audio_transcript.delta",
        "response.output_audio_transcript.done",
        "response.output_text.delta",
        "response.output_text.done",
        "response.function_call_arguments.delta",
        "response.function_call_arguments.done",
    }
)
# Benign response-lifecycle races (BUG-053/BUG-056): both sides of the same
# boundary. ``conversation_already_has_active_response`` = our response.create
# arrived while one was still running; ``response_cancel_not_active`` = our
# response.cancel arrived after the response already finished — the outcome the
# cancel wanted has already happened, so it is an idempotent no-op, never a
# broken connection. Labeling either terminal ended healthy live calls with
# hangup_reason=error (barge-in 09:04 and scrub-cancel 15:13, 2026-07-14).
_RECOVERABLE_ERROR_CODES = frozenset(
    {
        "conversation_already_has_active_response",
        "response_cancel_not_active",
    }
)
# xAI Grok reports the SAME benign races under the generic code
# ``invalid_request_error``, so the code set alone cannot recognize them.
# Observed live (grok-realtime 2026-07-16 10:23): the cancel of a suppressed
# unsolicited response raced the response's own completion, the server
# answered "Cancellation failed: no active response found", and the generic
# code made a healthy session end with hangup_reason=error. Match the
# lifecycle shape in the message instead — both markers describe an outcome
# that already happened, never a broken connection.
_RECOVERABLE_ERROR_MESSAGE_MARKERS = (
    "no active response",
    "already has an active response",
)


def _error_is_recoverable(event: Any) -> bool:
    if _error_code(event) in _RECOVERABLE_ERROR_CODES:
        return True
    error = getattr(event, "error", None)
    message = str(getattr(error, "message", "") or "").casefold()
    return any(marker in message for marker in _RECOVERABLE_ERROR_MESSAGE_MARKERS)


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
    item_id: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    # Token counts of one finished response (see _usage_from_response) —
    # previously discarded, leaving 100% of Realtime-API spend unmetered.
    usage: dict[str, int] | None = None


def _usage_from_response(response: Any) -> dict[str, int] | None:
    """Token counts of one response.done payload, or None when empty.

    ``input_cached`` rides along because OpenAI bills cached input at a
    tenth of the text rate — pricing it as fresh input would overstate the
    dominant share of a long call.
    """
    usage_obj = getattr(response, "usage", None)
    if usage_obj is None:
        return None

    def _count(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            # A usage field a provider omits or sends oddly counts as zero;
            # metering must never break a live call.
            return 0

    total_in = _count(getattr(usage_obj, "input_tokens", None))
    total_out = _count(getattr(usage_obj, "output_tokens", None))
    if total_in <= 0 and total_out <= 0:
        return None
    in_details = getattr(usage_obj, "input_token_details", None)
    out_details = getattr(usage_obj, "output_token_details", None)
    return {
        "input_total": total_in,
        "output_total": total_out,
        "input_text": _count(getattr(in_details, "text_tokens", None)),
        "input_audio": _count(getattr(in_details, "audio_tokens", None)),
        "input_cached": _count(getattr(in_details, "cached_tokens", None)),
        "output_text": _count(getattr(out_details, "text_tokens", None)),
        "output_audio": _count(getattr(out_details, "audio_tokens", None)),
    }


def _error_code(event: Any) -> str:
    error = getattr(event, "error", None)
    return str(getattr(error, "code", "") or "").strip()


def _normalize_history(history: Any) -> tuple[dict[str, str], ...]:
    """Keep only well-formed user/assistant text turns from a history seed."""
    normalized: list[dict[str, str]] = []
    for message in tuple(history or ()):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "") or "")
        text = str(message.get("text", "") or "").strip()
        if text and role in {"user", "assistant"}:
            normalized.append({"role": role, "text": text})
    return tuple(normalized)


def _error_message(event: Any) -> str:
    error = getattr(event, "error", None)
    code = _error_code(event)
    message = str(getattr(error, "message", "") or "").strip()
    if code and message:
        return f"{code}: {message}"[:800]
    return (message or code or "OpenAI Realtime session error")[:800]


def _response_status_error(event: Any) -> str:
    """Describe a terminal response status that did not complete normally."""
    response = getattr(event, "response", None)
    raw_status = getattr(response, "status", "")
    status = str(getattr(raw_status, "value", raw_status) or "").strip().lower()
    if "." in status:
        status = status.rsplit(".", 1)[-1]
    # Cancellation is expected during barge-in. The session-level output guard
    # still catches an unexpected cancelled response that leaves a user turn
    # empty, without surfacing a false provider warning on normal interruption.
    if status in {"", "completed", "cancelled"}:
        return ""

    details = getattr(response, "status_details", None)
    error = getattr(details, "error", None)
    code = str(getattr(error, "code", "") or "").strip()
    message = str(getattr(error, "message", "") or "").strip()
    reason = str(getattr(details, "reason", "") or "").strip()
    detail = ": ".join(part for part in (code, message or reason) if part)
    summary = f"OpenAI Realtime response ended with status {status}"
    return f"{summary}: {detail}"[:800] if detail else summary


def _session_payload(
    cfg: Any, *, transcription_model: str | None = "gpt-4o-mini-transcribe"
) -> dict[str, Any]:
    """Build the current GA ``session.update`` payload.

    Audio output already includes a transcript side-channel, so the Realtime
    API accepts ``[\"audio\"]`` only; requesting text and audio together is
    invalid. PCM input and output are both explicitly declared as 24 kHz.

    ``transcription_model`` is ``None`` for a self-hosted server: a hosted
    OpenAI model id is meaningless there and naming one would have the server
    reject the whole session for a field the user never chose. The server then
    transcribes with whatever it ships.
    """
    transcription: dict[str, Any] = {"model": transcription_model} if transcription_model else {}
    input_language = str(getattr(cfg, "input_language", "auto") or "auto")
    input_language = input_language.strip().lower().replace("_", "-").split("-", 1)[0]
    if input_language in {"de", "en", "es"}:
        transcription["language"] = input_language

    turn_detection = str(getattr(cfg, "turn_detection", "server_vad") or "server_vad")
    if turn_detection not in {"server_vad", "semantic_vad"}:
        turn_detection = "server_vad"

    output: dict[str, Any] = {
        "format": {"type": "audio/pcm", "rate": _OUTPUT_RATE},
    }
    voice = str(getattr(cfg, "voice", "") or "").strip()
    # "auto" is the picker's way of saying "no preference" (the only honest
    # entry a self-hosted card can offer); sending it as a voice NAME would
    # have the server reject a voice it does not have.
    if voice and voice != "auto":
        output["voice"] = voice

    turn_detection_config: dict[str, Any] = {
        "type": turn_detection,
        # Jarvis requests the response only after the final input transcript
        # has passed the single turn-language resolver.
        "create_response": False,
        # Jarvis also owns barge-in explicitly. Keeping both flags false is
        # OpenAI's documented manual-response VAD mode.
        "interrupt_response": False,
    }
    # An unset window (None/0) keeps OpenAI's native server-VAD default so the
    # realtime model decides the turn end itself; only an explicit override is
    # forwarded.
    silence_ms = getattr(cfg, "silence_duration_ms", None)
    if turn_detection == "server_vad" and silence_ms:
        turn_detection_config["silence_duration_ms"] = int(silence_ms)
    # OpenAI's name for end-of-speech sensitivity is semantic-VAD "eagerness":
    # low = let the user take their time before the turn is called over. Plain
    # server_vad has no equivalent knob at all, so a session configured for it
    # honestly keeps the provider default rather than faking patience with a
    # fixed silence window (which would tax every short utterance — the very
    # trade the 2026-07-21 directive rejected).
    sensitivity = str(getattr(cfg, "end_of_speech_sensitivity", "") or "").lower()
    if turn_detection == "semantic_vad" and sensitivity in {"low", "high"}:
        turn_detection_config["eagerness"] = sensitivity

    payload: dict[str, Any] = {
        "type": "realtime",
        "instructions": str(getattr(cfg, "instructions", "") or ""),
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": _INPUT_RATE},
                "transcription": transcription,
                "turn_detection": turn_detection_config,
            },
            "output": output,
        },
    }
    tools = tuple(getattr(cfg, "tools", ()) or ())
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": str(tool.get("name", "")),
                "description": str(tool.get("description", "")),
                "parameters": tool.get("parameters") or {"type": "object"},
            }
            for tool in tools
            if isinstance(tool, dict) and tool.get("name")
        ]
        payload["tool_choice"] = "auto"
    return payload


class _OpenAIRealtimeSession:
    supports_tool_updates = True
    creates_responses_automatically = False
    isolates_response_generations = True

    def __init__(
        self,
        *,
        connection: Any,
        connection_cm: Any,
        client: Any,
        session_id: str,
        session_payload: dict[str, Any] | None = None,
        connect_model: str = "",
        history_seed: tuple[dict[str, str], ...] = (),
        rebuild_on_transport_death: bool = False,
        response_start_timeout_s: float | None = None,
        disconnect_before_rebuild: bool = False,
        rebuild_retry_window_s: float = 0.0,
        rebuild_retry_step_s: float = 1.0,
        prompted_response_retry: bool = False,
        renders_surface_fallback: bool = False,
        owns_client: bool = True,
    ) -> None:
        self._conn = connection
        self._connection_cm = connection_cm
        self._client = client
        # False when the provider CACHES the client across sessions (the
        # self-hosted card): building a fresh AsyncOpenAI costs ~230 ms per
        # call (httpx + SSL-context setup, measured 2026-08-08), so the local
        # provider reuses one and close() must then leave it alive.
        self._owns_client = bool(owns_client)
        # Capability consumed by the session pump (BUG-071): may a transport
        # that died mid-call be reopened in place? Hosted cards keep today's
        # deliberate terminal semantics (the BUG-064 stack self-heals
        # internally and a credential/quota death must not loop). A
        # SELF-HOSTED server is different: its process can crash and come
        # back seconds later (live 2026-08-06 19:57 — the local server died
        # silently mid-turn and the whole call ended reason=error although
        # the server was healthy again within a minute), so its sessions
        # opt in and the existing budgeted rebuild machinery does the rest.
        self.rebuild_on_transport_death = bool(rebuild_on_transport_death)
        # ``response.created`` only acknowledges a request. Hosted models
        # normally produce their first delta inside the strict stall window;
        # self-hosted models declare a larger first-token budget so legitimate
        # inference is not mistaken for a dead transport.
        self._response_start_timeout_s = max(
            0.0,
            float(
                _RESPONSE_STALL_S if response_start_timeout_s is None else response_start_timeout_s
            ),
        )
        # A capacity-one server must release the old pipeline before opening a
        # replacement. Hosted endpoints retain make-before-break; self-hosted
        # endpoints can opt into serial replacement plus bounded drain retries.
        self._disconnect_before_rebuild = bool(disconnect_before_rebuild)
        self._rebuild_retry_window_s = max(0.0, float(rebuild_retry_window_s))
        self._rebuild_retry_step_s = max(0.0, float(rebuild_retry_step_s))
        # Retry contract for an answer blocked at the speech boundary. This
        # transport keeps the cancelled response's text in the server-side
        # conversation, so a bare response.create regenerates against a
        # history that already contains the blocked answer — a small
        # self-hosted brain then reads the turn as answered and returns an
        # empty completion (live 2026-08-10 17:08: the one language retry
        # came back as a single token and the call went silent). Opting in
        # routes the retry through send_text(), which appends the explicit
        # retry request as a fresh user item the model must actually answer.
        self.supports_prompted_response_retry = bool(prompted_response_retry)
        # Whether the SESSION is the only path to this provider's voice. A
        # self-hosted server exposes no sibling TTS endpoint and has one
        # pipeline slot, so a scrub-cancelled turn can only become audible
        # again through the live session itself; the orchestrator then sends
        # its safety-net phrase here instead of the (voiceless) surface.
        self.renders_surface_fallback = bool(renders_surface_fallback)
        self._connection_is_open = True
        self._events = connection.__aiter__()
        self.session_id = session_id
        # Model the transport was opened with — required to rebuild the
        # connection in place when the server goes deaf (BUG-064 escalation).
        self._connect_model = str(connect_model or "")
        # Bounded call transcript for context restoration (BUG-088). Seeded
        # from the open-time config and kept current by the orchestrator via
        # set_history_snapshot after every completed turn, so a BUG-064
        # transport rebuild can hand the fresh connection the conversation
        # it would otherwise lose entirely.
        self._history_seed = _normalize_history(history_seed)
        self._last_transcript_at = float("-inf")
        self._transcript_deadline: float | None = None
        self._rebuild_task: asyncio.Task[None] | None = None
        # The full session contract as sent at open. Kept current by
        # update_session() so a BUG-064 re-arm never reverts live
        # instructions or tool declarations to their session-start values.
        self._session_contract = session_payload
        self._last_contract_rearm = float("-inf")
        # Sequence marker, not a timestamp: has ANY input transcript arrived
        # since the last contract re-arm actually went out? Windows'
        # time.monotonic() ticks at ~16 ms, so two adjacent events can carry
        # the SAME timestamp and an ordering comparison silently lies.
        self._transcript_heard_since_rearm = True
        # Separate the silent model-thinking phase from a stream that stopped
        # after material output began. The first uses the provider's declared
        # start budget; the second uses the strict stream-stall threshold.
        now = time.monotonic()
        self._response_started_at = now
        self._last_response_activity = now
        self._response_output_started = False
        self._last_item_id = ""
        # Whether THIS response's transcript arrived as a delta stream. Servers
        # split here: OpenAI streams it token by token, a self-hosted stack that
        # already holds the finished line sends only the closing ``.done``.
        # Reset per response so one streaming turn cannot mute the next
        # whole-line one.
        self._output_transcript_streamed = False
        self._response_had_tool_calls = False
        self._tool_response_done_seen = False
        self._pending_tool_call_ids: set[str] = set()
        # Every client response.create carries a unique marker. Only the first
        # response.created lifecycle that consumes one pending marker may emit
        # audio. This is a transport boundary, not transcript-text deduplication:
        # a provider-side duplicate or unsolicited response is cancelled before
        # any PCM reaches the speaker.
        self._pending_response_markers: set[str] = set()
        self._accepted_response_ids: set[str] = set()
        # OpenAI accepts only one active response per conversation. Every
        # local response request (native reply, tool continuation, trusted
        # update) passes this lifecycle boundary so concurrent callers cannot
        # race two response.create operations onto the same session.
        self._response_create_lock = asyncio.Lock()
        self._response_idle = asyncio.Event()
        self._response_idle.set()
        # Evidence that the server heard the user speak (speech_started /
        # committed buffer / a local barge-in) WITHOUT a subsequent input
        # transcript. An unsolicited response.created is adopted as the
        # genuine answer to that heard-but-untranscribed turn ONLY while this
        # is True. An input transcript clears it: from there the manual flow
        # requests its own response, so a crossing auto-response is a
        # duplicate and stays suppressed (BUG-064, benign race 2026-07-15).
        self._server_heard_user_since_response = False
        # One-shot: an adopted auto-response already answers the current user
        # turn. If that turn's input transcript arrives merely DELAYED (not
        # lost), the orchestrator will request its own response for it —
        # honoring that request would speak a second, independent answer to
        # the same utterance. Cleared by new speech evidence or by consuming
        # exactly one skipped request.
        self._auto_adopted_unanswered_input = False
        self._closed = False

    async def wait_until_ready(self) -> None:
        """Reject a connection unless the server confirms our effective schema."""

        async def _wait() -> None:
            while True:
                event = await anext(self._events)
                event_type = str(getattr(event, "type", "") or "")
                if event_type == "session.updated":
                    return
                if event_type == "error":
                    raise RuntimeError(_error_message(event))

        await asyncio.wait_for(_wait(), timeout=_HANDSHAKE_TIMEOUT_S)

    async def send_audio(self, chunk: Any) -> None:
        sample_rate = int(getattr(chunk, "sample_rate", 0) or 0)
        if sample_rate != _INPUT_RATE:
            raise ValueError(
                f"OpenAI Realtime requires {_INPUT_RATE} Hz PCM; received {sample_rate} Hz"
            )
        pcm = bytes(getattr(chunk, "pcm", b"") or b"")
        if not pcm:
            return
        # The microphone pump runs even when a deaf server emits no events at
        # all, so it is the one place guaranteed to notice an overdue
        # transcript and start the transport rebuild (BUG-064 escalation).
        self._maybe_begin_rebuild()
        try:
            await self._conn.input_audio_buffer.append(audio=base64.b64encode(pcm).decode("ascii"))
        except Exception:
            if self._rebuild_task is not None and not self._rebuild_task.done():
                # The dying transport is being replaced; this frame is lost
                # either way and must not end the whole voice session.
                return
            raise

    async def receive(self) -> AsyncIterator[_ProviderEvent]:
        # One while-iteration per transport: a BUG-064 rebuild replaces
        # ``self._events`` mid-call, and the pump must hop onto the fresh
        # iterator instead of treating the old transport's end as the end of
        # the whole voice session.
        while True:
            events = self._events
            try:
                async for event in events:
                    # Runs before dispatch so it also fires on the event after
                    # a ``continue`` branch; the send_audio pump covers the
                    # no-events-at-all case.
                    self._maybe_begin_rebuild()
                    async for out in self._dispatch_event(event):
                        yield out
            except Exception:
                if not await self._transport_was_rebuilt(events):
                    raise
            else:
                if not await self._transport_was_rebuilt(events):
                    return
            yield _ProviderEvent(
                type="error",
                error=(
                    "Realtime transport rebuilt after the provider stopped "
                    "making progress; the in-flight utterance was interrupted"
                ),
                recoverable=True,
            )

    async def _dispatch_event(self, event: Any) -> AsyncIterator[_ProviderEvent]:
        event_type = str(getattr(event, "type", "") or "")
        if event_type == "response.created":
            await self._handle_response_created(event)
            return
        if event_type.startswith("response."):
            if not self._response_is_accepted(event):
                return
            # Only an ACCEPTED response's events are liveness for the
            # lifecycle we are waiting on. Unsolicited strays must not feed
            # this clock: the wedged Grok server auto-created strays every
            # ~7.8 s (2026-07-16 11:23), keeping a dead lifecycle looking
            # alive just under the 8 s stall threshold forever.
            self._last_response_activity = time.monotonic()
            if event_type in _RESPONSE_OUTPUT_EVENTS:
                self._response_output_started = True
        if event_type == "response.output_audio.delta":
            self._last_item_id = str(getattr(event, "item_id", "") or "")
            yield _ProviderEvent(
                type="audio_delta",
                audio=_PcmChunk(
                    pcm=base64.b64decode(getattr(event, "delta", "")),
                    sample_rate=_OUTPUT_RATE,
                ),
            )
        elif event_type == "response.output_audio_transcript.delta":
            self._output_transcript_streamed = True
            yield _ProviderEvent(
                type="output_transcript_delta",
                text=str(getattr(event, "delta", "") or ""),
            )
        elif event_type == "response.output_audio_transcript.done":
            # A server that has the whole line at once sends ONLY this, with no
            # preceding deltas — the self-hosted stack does, because its TTS is
            # handed finished text rather than a token stream. Without it the
            # turn reached the scrub gate carrying audio and no transcript, and
            # the gate correctly refuses to let unvetted speech out (ADR-0010):
            # a complete, audible answer was cancelled at the turn boundary
            # every single time (field report 2026-08-06).
            #
            # Guarded on the streaming flag so a provider that sends BOTH does
            # not deliver its line twice, once in pieces and once whole.
            if not self._output_transcript_streamed:
                text = str(getattr(event, "transcript", "") or "")
                if text:
                    yield _ProviderEvent(type="output_transcript_delta", text=text)
        elif event_type == "conversation.item.input_audio_transcription.completed":
            self._note_input_transcript()
            yield _ProviderEvent(
                type="input_transcript",
                text=str(getattr(event, "transcript", "") or ""),
                is_final=True,
                item_id=str(getattr(event, "item_id", "") or "") or None,
            )
        elif event_type == "conversation.item.input_audio_transcription.failed":
            # The model still has the committed audio in conversation
            # context. Let the orchestrator fail open to a spoken response,
            # while withholding tools because no auditable text exists.
            # A FAILED transcript settles the per-turn contract debt but is
            # NOT proof the transcription side works again — treating it as
            # restored hearing kept re-arming a deaf Grok session forever
            # (2026-07-16 11:23: the wedge emitted failed events, so the
            # stray-after-unheeded-re-arm escalation never fired).
            self._note_input_transcript(restored_hearing=False)
            yield _ProviderEvent(
                type="input_transcript",
                text="",
                is_final=True,
                error=_error_message(event),
                item_id=str(getattr(event, "item_id", "") or "") or None,
            )
        elif event_type == "input_audio_buffer.committed":
            # The server sealed a user turn; the session contract now owes an
            # input transcript (completed or failed). If none arrives, the
            # transcription half of the contract is dead (BUG-064).
            self._server_heard_user_since_response = True
            self._arm_transcript_deadline(require_recent_quiet=False)
        elif event_type == "input_audio_buffer.speech_started":
            self._server_heard_user_since_response = True
            # New speech means a NEW user turn: an earlier adopted
            # auto-response no longer answers what comes next.
            self._auto_adopted_unanswered_input = False
            yield _ProviderEvent(type="speech_started")
        elif event_type == "response.function_call_arguments.done":
            call_id = str(getattr(event, "call_id", "") or "")
            self._response_had_tool_calls = True
            if call_id:
                self._pending_tool_call_ids.add(call_id)
            raw_arguments = str(getattr(event, "arguments", "") or "{}")
            try:
                arguments = json.loads(raw_arguments)
            except (TypeError, ValueError):
                # Malformed tool arguments are treated as none given; the
                # tool layer then reports the missing field honestly.
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            yield _ProviderEvent(
                type="tool_call",
                call_id=call_id,
                tool_name=str(getattr(event, "name", "") or ""),
                tool_args=arguments,
            )
        elif event_type == "response.done":
            response_id = self._event_response_id(event)
            if response_id:
                self._accepted_response_ids.discard(response_id)
            elif len(self._accepted_response_ids) == 1:
                self._accepted_response_ids.pop()
            self._response_idle.set()
            self._response_output_started = False
            usage = _usage_from_response(getattr(event, "response", None))
            if usage is not None:
                # Every response bills its own pass over the session context,
                # including tool-call generations that never reach
                # turn_complete — report each one.
                yield _ProviderEvent(type="usage", usage=usage)
            status_error = _response_status_error(event)
            if status_error:
                # response.done is emitted for completed, failed, and
                # incomplete generations. Preserve the lifecycle boundary,
                # but do not mislabel a provider failure as clean success.
                yield _ProviderEvent(
                    type="error",
                    error=status_error,
                    recoverable=True,
                )
            if self._response_had_tool_calls:
                self._tool_response_done_seen = True
                await self._continue_after_tools_if_ready()
            else:
                yield _ProviderEvent(type="turn_complete")
        elif event_type == "error":
            yield _ProviderEvent(
                type="error",
                error=_error_message(event),
                recoverable=_error_is_recoverable(event),
            )

    def set_history_snapshot(self, history: tuple[dict[str, str], ...]) -> None:
        """Refresh the transcript a transport rebuild would restore (BUG-088).

        Local state only — never a wire call. The orchestrator pushes the
        bounded call transcript here after every completed turn.
        """
        self._history_seed = _normalize_history(history)

    async def _seed_conversation_history(self, connection: Any) -> None:
        """Recreate the call transcript as conversation items on a connection.

        Used when a fresh transport replaces one that held the conversation
        server-side (open with a mid-call seed after a cross-family fallback,
        or the BUG-064 in-place rebuild). Fails open: an amnesiac session is
        exactly the pre-BUG-088 behavior and strictly better than no session.
        """
        for message in self._history_seed:
            role = message["role"]
            # GA literals: user content is "input_text", assistant content is
            # "output_text". The hosted endpoint tolerates the legacy "text",
            # but a strictly-validating self-hosted server rejects the whole
            # item with it — every rebuild seed died as "Unknown or invalid
            # event" on the local stack (live 2026-08-06 20:29).
            content_type = "input_text" if role == "user" else "output_text"
            try:
                await connection.conversation.item.create(
                    item={
                        "type": "message",
                        "role": role,
                        "content": [{"type": content_type, "text": message["text"]}],
                    }
                )
            except Exception:  # noqa: BLE001 — degrade to an amnesiac session
                log.warning(
                    "OpenAI Realtime history seeding failed part-way; the "
                    "session continues with partial in-call context",
                    exc_info=True,
                )
                return

    async def update_session(
        self,
        *,
        instructions: str | None = None,
        language: str | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
        turn_directive: str | None = None,
        standing_directive: str | None = None,
    ) -> None:
        del language  # Input transcription stays provider-inferred and multilingual.
        # This transport REPLACES its instructions wholesale, and the turn
        # and standing directives are already embedded in them. Accepting the
        # keywords keeps the per-turn update off the session's TypeError-retry
        # path.
        del turn_directive, standing_directive
        update: dict[str, Any] = {"type": "realtime"}
        if instructions is not None:
            update["instructions"] = instructions
        if tools is not None:
            update["tools"] = [
                {
                    "type": "function",
                    "name": str(tool.get("name", "")),
                    "description": str(tool.get("description", "")),
                    "parameters": tool.get("parameters") or {"type": "object"},
                }
                for tool in tools
                if isinstance(tool, dict) and tool.get("name")
            ]
            update["tool_choice"] = "auto" if update["tools"] else "none"
        if self._session_contract is not None:
            # The orchestrator rebuilds its ~20k-character instruction block
            # every turn even when nothing in it changed. Re-sending an
            # identical value is pure cost: it invalidates the provider's
            # prompt-cache prefix and re-bills the block at the fresh-input
            # rate. Only differences travel.
            for key in ("instructions", "tools", "tool_choice"):
                if key in update and self._session_contract.get(key) == update[key]:
                    del update[key]
        if len(update) > 1:
            if self._session_contract is not None:
                for key in ("instructions", "tools", "tool_choice"):
                    if key in update:
                        self._session_contract[key] = update[key]
            await self._conn.session.update(session=update)

    async def request_response(self, *, required_tool: str | None = None) -> None:
        if self._auto_adopted_unanswered_input and required_tool is None:
            # The adopted auto-response already answers this turn; its input
            # transcript arrived delayed, not lost. Creating another response
            # would speak a second answer to the same utterance. One-shot: a
            # required_tool request still goes through (the adopted response
            # cannot satisfy an explicit tool demand).
            self._auto_adopted_unanswered_input = False
            log.info(
                "OpenAI Realtime skipping response.create — an adopted "
                "auto-response already answers the current user turn"
            )
            return
        tool_choice: Any = None
        if required_tool:
            tool_choice = {"type": "function", "name": str(required_tool)}
        await self._create_response(tool_choice=tool_choice)

    async def send_text(self, text: str) -> None:
        """Add one trusted text turn and ask the live model for audio output."""
        await self._conn.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": str(text)}],
            }
        )
        await self._create_response(tool_choice="none")

    async def truncate(self, audio_end_ms: int) -> None:
        if self._last_item_id:
            await self._conn.conversation.item.truncate(
                item_id=self._last_item_id,
                content_index=0,
                audio_end_ms=max(0, int(audio_end_ms)),
            )

    async def interrupt(self) -> None:
        # Invalidate the cancelled generation before awaiting the wire. Late
        # audio/transcript/done events keep their old response id and are then
        # suppressed by ``_response_is_accepted`` even if they race the next
        # user turn.
        # A barge-in is local proof the user is speaking again: if the server
        # then auto-answers that speech under a dropped manual-response
        # contract, the response must be adopted, not suppressed. It also
        # opens a NEW turn, so any earlier adopted response is history.
        self._server_heard_user_since_response = True
        self._auto_adopted_unanswered_input = False
        self._pending_response_markers.clear()
        self._accepted_response_ids.clear()
        self._response_had_tool_calls = False
        self._tool_response_done_seen = False
        self._pending_tool_call_ids.clear()
        self._last_item_id = ""
        # BUG-053 correction 2: with no response lifecycle in flight there is
        # nothing to cancel — skip the wire call that could only ever produce
        # the benign ``response_cancel_not_active`` error. The recoverable
        # classification above stays necessary regardless: the provider can
        # still finish between this local check and the wire operation.
        if self._response_idle.is_set():
            return
        try:
            await self._conn.response.cancel()
        finally:
            self._response_idle.set()
            self._response_output_started = False

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        del name
        await self._conn.conversation.item.create(
            item={
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result, ensure_ascii=False, default=str),
            }
        )
        self._pending_tool_call_ids.discard(call_id)
        await self._continue_after_tools_if_ready()

    async def _continue_after_tools_if_ready(self) -> None:
        if (
            not self._response_had_tool_calls
            or not self._tool_response_done_seen
            or self._pending_tool_call_ids
        ):
            return
        self._response_had_tool_calls = False
        self._tool_response_done_seen = False
        await self._create_response()

    async def _create_response(self, *, tool_choice: Any = None) -> None:
        async with self._response_create_lock:
            await self._response_idle.wait()
            self._response_idle.clear()
            now = time.monotonic()
            self._response_started_at = now
            self._last_response_activity = now
            self._response_output_started = False
            marker = uuid4().hex
            self._pending_response_markers.add(marker)
            response: dict[str, Any] = {
                "metadata": {_RESPONSE_REQUEST_METADATA_KEY: marker},
            }
            if tool_choice is not None:
                response["tool_choice"] = tool_choice
            try:
                await self._conn.response.create(response=response)
            except BaseException:
                self._pending_response_markers.discard(marker)
                self._response_idle.set()
                self._response_output_started = False
                raise

    @staticmethod
    def _event_response_id(event: Any) -> str:
        direct = str(getattr(event, "response_id", "") or "")
        if direct:
            return direct
        response = getattr(event, "response", None)
        return str(getattr(response, "id", "") or "")

    async def _handle_response_created(self, event: Any) -> None:
        response = getattr(event, "response", None)
        response_id = self._event_response_id(event)
        if response_id and response_id in self._accepted_response_ids:
            return

        metadata = getattr(response, "metadata", None) or {}
        if hasattr(metadata, "model_dump"):
            metadata = metadata.model_dump()
        marker = (
            str(metadata.get(_RESPONSE_REQUEST_METADATA_KEY, "") or "")
            if isinstance(metadata, dict)
            else ""
        )

        if marker and marker in self._pending_response_markers:
            self._pending_response_markers.discard(marker)
        elif not marker and self._pending_response_markers:
            # Compatibility for a server/SDK that omits echoed metadata. The
            # pending allowance is still consumed, preserving the exactly-one
            # response invariant even if an automatic response races our own.
            self._pending_response_markers.pop()
            log.warning(
                "OpenAI Realtime response.created omitted Jarvis request metadata; "
                "accepted one pending response by lifecycle order"
            )
        else:
            if (
                response_id
                and self._response_idle.is_set()
                and self._server_heard_user_since_response
            ):
                # The server dropped the manual-response contract and
                # auto-answered a user turn it audibly heard (speech_started /
                # committed buffer / barge-in since our last response).
                # Cancelling would discard the only answer this turn will
                # ever get — observed live on grok-realtime 2026-07-16:
                # barge-in cancel → contract dropped → the genuine reply was
                # suppressed as unsolicited → Jarvis stayed silent until a
                # manual hang-up. Adopt the response; the re-arm below
                # restores the contract for the following turns.
                log.warning(
                    "OpenAI Realtime adopting unsolicited response %s as the "
                    "answer to a heard user turn (server dropped the "
                    "manual-response contract)",
                    response_id,
                )
                self._response_idle.clear()
                self._accepted_response_ids.add(response_id)
                now = time.monotonic()
                self._response_started_at = now
                self._last_response_activity = now
                self._response_output_started = False
                self._server_heard_user_since_response = False
                self._auto_adopted_unanswered_input = True
                await self._rearm_session_contract()
                return
            log.warning(
                "OpenAI Realtime suppressed unsolicited response %s",
                response_id or "<unknown>",
            )
            if response_id:
                try:
                    await self._conn.response.cancel(response_id=response_id)
                except Exception:  # noqa: BLE001 -- suppression remains fail-closed
                    log.debug(
                        "OpenAI Realtime unsolicited response cancel failed",
                        exc_info=True,
                    )
            # BUG-064 recurrence #2 (grok-realtime 2026-07-16 10:23): a
            # FURTHER unsolicited response, arriving well after the previous
            # contract re-arm with not a single input transcript in between,
            # is proof the re-arm never restored the server's hearing. That
            # session's first stray fell inside the benign-race quiet window
            # (1.9 s after the turn's transcript), so no transcript deadline
            # was armed — and the deaf server then emitted nothing for 16 s,
            # leaving the deadline path without a second chance. The cooldown
            # bound keeps a same-instant burst of strays (one server hiccup,
            # re-arm still unassessed) on the cheap re-arm path.
            if (
                not self._transcript_heard_since_rearm
                and time.monotonic() - self._last_contract_rearm >= _CONTRACT_REARM_COOLDOWN_S
            ):
                self._begin_rebuild(
                    "unsolicited response after an unheeded contract re-arm "
                    "(no input transcript since)"
                )
                return
            # BUG-064: under the manual-response contract an unsolicited
            # response should be impossible — its arrival means the server
            # dropped the session configuration (observed live on grok-realtime
            # 2026-07-16 08:07: after a barge-in cancel, input transcription
            # stopped and server VAD auto-created responses; the session sat
            # deaf until manual hang-up). Re-assert the full contract so the
            # session hears again; on a healthy server this is an idempotent
            # no-op.
            await self._rearm_session_contract()
            # The auto-response proves the server heard a user turn, yet no
            # transcript preceded it. If none follows either, the re-arm did
            # not restore hearing and the transport must be rebuilt (the
            # 2026-07-16 09:23 recurrence, where exactly that happened).
            self._arm_transcript_deadline(require_recent_quiet=True)
            return

        if not response_id:
            log.warning(
                "OpenAI Realtime response.created had no response id; "
                "response events remain suppressed"
            )
            return
        self._accepted_response_ids.add(response_id)
        self._last_response_activity = time.monotonic()
        # A fresh lifecycle carries a fresh transcript, whichever shape it
        # arrives in — so the whole-line fallback is armed again for this one.
        self._output_transcript_streamed = False
        # This lifecycle now answers the pending user turn; only NEW speech
        # evidence may qualify a later unsolicited response for adoption.
        self._server_heard_user_since_response = False

    async def _rearm_session_contract(self) -> None:
        """Re-send the full session payload after an unsolicited response.

        Restores input transcription and ``create_response: false`` when the
        server silently dropped them (BUG-064). Throttled so a burst of
        unsolicited responses re-arms once, and fail-safe so a rejected
        session.update can never take down the receive pump.
        """
        if self._session_contract is None or self._closed:
            return
        now = time.monotonic()
        if now - self._last_contract_rearm < _CONTRACT_REARM_COOLDOWN_S:
            return
        self._last_contract_rearm = now
        self._transcript_heard_since_rearm = False
        log.warning(
            "OpenAI Realtime re-arming the session contract (input "
            "transcription + manual-response mode) after an unsolicited "
            "response — the server may have dropped session state"
        )
        try:
            await self._conn.session.update(session=self._session_contract)
        except Exception:  # noqa: BLE001 -- the pump must survive a failed re-arm
            log.debug(
                "OpenAI Realtime session contract re-arm failed",
                exc_info=True,
            )

    def _note_input_transcript(self, *, restored_hearing: bool = True) -> None:
        """An input transcript settles the per-turn contract debt.

        Only a COMPLETED transcript additionally proves the transcription
        side works (``restored_hearing``); a failed one merely shows the
        event pipeline is alive while the session may still be deaf.
        """
        self._last_transcript_at = time.monotonic()
        self._transcript_deadline = None
        if restored_hearing:
            self._transcript_heard_since_rearm = True
        # The manual flow answers transcribed turns itself; a crossing
        # auto-response is now a duplicate, not a salvageable answer.
        self._server_heard_user_since_response = False

    def _arm_transcript_deadline(self, *, require_recent_quiet: bool) -> None:
        # Only arm while the session is at rest: with a response lifecycle in
        # flight the assistant is speaking and no transcript is owed yet.
        if self._transcript_deadline is not None or not self._response_idle.is_set():
            return
        now = time.monotonic()
        if require_recent_quiet and now - self._last_transcript_at < _SUPPRESS_ARM_MIN_QUIET_S:
            return
        self._transcript_deadline = now + _TRANSCRIPT_OVERDUE_S

    def _transcript_overdue(self) -> bool:
        if self._closed or self._session_contract is None:
            return False
        if self._transcript_deadline is None or not self._response_idle.is_set():
            return False
        return time.monotonic() >= self._transcript_deadline

    def _response_lifecycle_stalled(self) -> bool:
        """Whether an in-flight response exceeded its current phase budget.

        BUG-064 recurrence #3 (grok-realtime 2026-07-16 10:51): the server
        never sent ``response.done`` for an accepted response whose output a
        local barge-in had dropped, so ``_response_idle`` stayed clear and
        every idle-gated defense (adoption, transcript deadline, rebuild)
        was disarmed at once. After material output starts, continuous silence
        for ``_RESPONSE_STALL_S`` is proof the stream will not finish. Before
        that point, ``response.created`` is only an acknowledgement, so the
        provider's first-output budget applies instead.
        """
        if self._closed or self._session_contract is None:
            return False
        if self._response_idle.is_set():
            return False
        if self._response_output_started:
            return time.monotonic() - self._last_response_activity >= _RESPONSE_STALL_S
        return time.monotonic() - self._response_started_at >= self._response_start_timeout_s

    def _maybe_begin_rebuild(self) -> None:
        if self._response_lifecycle_stalled():
            if self._response_output_started:
                reason = (
                    "in-flight response stopped producing events for "
                    f"{_RESPONSE_STALL_S:.0f} s after output began — "
                    "response.done is not coming"
                )
            else:
                reason = (
                    "in-flight response produced no material output for "
                    f"{self._response_start_timeout_s:.0f} s"
                )
            self._begin_rebuild(reason)
            return
        if not self._transcript_overdue():
            return
        self._begin_rebuild(
            "input transcript overdue for a heard user turn despite a session-contract re-arm"
        )

    def _begin_rebuild(self, reason: str) -> None:
        if self._closed or self._session_contract is None:
            return
        if self._rebuild_task is not None and not self._rebuild_task.done():
            return
        log.warning("OpenAI Realtime transport rebuild triggered: %s", reason)
        self._transcript_deadline = None
        self._rebuild_task = asyncio.create_task(
            self._rebuild_transport(),
            name="openai-realtime-transport-rebuild",
        )

    async def _transport_was_rebuilt(self, old_events: Any) -> bool:
        """True when the receive pump should hop onto a fresh transport."""
        task = self._rebuild_task
        if task is not None and not task.done():
            try:
                await task
            except Exception:  # noqa: BLE001 — a failed rebuild closes the session
                log.debug(
                    "OpenAI Realtime transport rebuild await failed",
                    exc_info=True,
                )
        return not self._closed and self._events is not old_events

    async def _open_rebuild_connection(self, *, handshake_timeout_s: float) -> tuple[Any, Any, Any]:
        """Open and fully handshake one replacement transport."""
        connection_cm: Any | None = None
        try:
            connection_cm = self._client.realtime.connect(model=self._connect_model or _MODEL)
            connection = await connection_cm.__aenter__()
            await connection.session.update(session=self._session_contract)
            events = connection.__aiter__()

            async def _wait_ready() -> None:
                while True:
                    event = await anext(events)
                    event_type = str(getattr(event, "type", "") or "")
                    if event_type == "session.updated":
                        return
                    if event_type == "error":
                        raise RuntimeError(_error_message(event))

            await asyncio.wait_for(
                _wait_ready(),
                timeout=max(0.001, float(handshake_timeout_s)),
            )
            return connection_cm, connection, events
        except BaseException as exc:
            if connection_cm is not None:
                try:
                    await connection_cm.__aexit__(type(exc), exc, exc.__traceback__)
                except BaseException:  # noqa: BLE001 — preserve the root cause
                    log.debug(
                        "OpenAI Realtime rebuild cleanup after failed handshake failed",
                        exc_info=True,
                    )
            raise

    async def _rebuild_transport(self) -> None:
        """Replace a transport that no longer satisfies its live contract.

        Hosted endpoints use make-before-break. A capacity-one self-hosted
        endpoint declares break-before-make: the old WebSocket is retired
        first, then replacement handshakes are retried while the server drains
        that pipeline. Either path restores the bounded call transcript before
        the receive pump can continue on the new connection.
        """
        log.warning("OpenAI Realtime rebuilding a transport that stopped making progress")
        old_cm = self._connection_cm
        old_retired = False
        if self._disconnect_before_rebuild:
            try:
                await old_cm.__aexit__(None, None, None)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — never risk opening a second slot
                log.warning(
                    "OpenAI Realtime could not retire the old transport before "
                    "a serial rebuild; closing the session",
                    exc_info=True,
                )
                await self.close()
                return
            self._connection_is_open = False
            old_retired = True

        deadline = time.monotonic() + self._rebuild_retry_window_s
        attempt = 0
        while True:
            attempt += 1
            remaining = deadline - time.monotonic()
            handshake_timeout_s = _HANDSHAKE_TIMEOUT_S
            if self._disconnect_before_rebuild and self._rebuild_retry_window_s > 0.0:
                handshake_timeout_s = min(
                    _HANDSHAKE_TIMEOUT_S,
                    max(0.001, remaining),
                )
            try:
                connection_cm, connection, events = await self._open_rebuild_connection(
                    handshake_timeout_s=handshake_timeout_s
                )
                break
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — bounded recovery, final failure is terminal
                remaining = deadline - time.monotonic()
                if not self._disconnect_before_rebuild or remaining <= 0.0:
                    log.warning(
                        "OpenAI Realtime transport rebuild failed; closing the dead session",
                        exc_info=True,
                    )
                    await self.close()
                    return
                log.info(
                    "OpenAI Realtime replacement handshake attempt %d failed; "
                    "the retired server slot may still be draining (%.1f s left)",
                    attempt,
                    remaining,
                )
                await asyncio.sleep(min(self._rebuild_retry_step_s, remaining))

        # Restore the call transcript before any new turn flows (BUG-088).
        # The user can hang up while this await is running. Until the swap
        # below, close() still owns the old transport, so this candidate must
        # clean itself up on cancellation or it would keep the only local
        # server slot occupied after the call ended.
        try:
            await self._seed_conversation_history(connection)
        except BaseException as exc:
            try:
                await connection_cm.__aexit__(type(exc), exc, exc.__traceback__)
            except BaseException:  # noqa: BLE001 — preserve cancellation/root cause
                log.debug(
                    "OpenAI Realtime rebuild candidate cleanup failed",
                    exc_info=True,
                )
            raise
        self._conn = connection
        self._connection_cm = connection_cm
        self._connection_is_open = True
        self._events = events
        self._pending_response_markers.clear()
        self._accepted_response_ids.clear()
        self._response_had_tool_calls = False
        self._tool_response_done_seen = False
        self._pending_tool_call_ids.clear()
        self._last_item_id = ""
        self._transcript_deadline = None
        self._last_contract_rearm = float("-inf")
        self._transcript_heard_since_rearm = True
        now = time.monotonic()
        self._response_started_at = now
        self._last_response_activity = now
        self._response_output_started = False
        self._server_heard_user_since_response = False
        self._auto_adopted_unanswered_input = False
        self._response_idle.set()
        if not old_retired:
            try:
                await old_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 — the dead transport may already be gone
                log.debug(
                    "OpenAI Realtime old transport close failed after rebuild",
                    exc_info=True,
                )

    def _response_is_accepted(self, event: Any) -> bool:
        response_id = self._event_response_id(event)
        if response_id:
            return response_id in self._accepted_response_ids
        # Current GA response events carry response_id (or response.id for
        # response.done). This fallback keeps older SDK event shapes usable
        # only when their lifecycle is otherwise unambiguous.
        return len(self._accepted_response_ids) == 1

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        rebuild = self._rebuild_task
        if rebuild is not None and not rebuild.done() and rebuild is not asyncio.current_task():
            rebuild.cancel()
        self._pending_response_markers.clear()
        self._accepted_response_ids.clear()
        self._response_idle.set()
        self._response_output_started = False
        try:
            if self._connection_is_open:
                try:
                    await self._connection_cm.__aexit__(None, None, None)
                finally:
                    self._connection_is_open = False
        finally:
            if self._owns_client:
                close = getattr(self._client, "close", None)
                if close is not None:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result


async def _open_realtime_session(
    client: Any,
    cfg: Any,
    *,
    model: str,
    transcription_model: str | None = "gpt-4o-mini-transcribe",
    rebuild_on_transport_death: bool = False,
    response_start_timeout_s: float | None = None,
    disconnect_before_rebuild: bool = False,
    rebuild_retry_window_s: float = 0.0,
    rebuild_retry_step_s: float = 1.0,
    prompted_response_retry: bool = False,
    renders_surface_fallback: bool = False,
    owns_client: bool = True,
) -> _OpenAIRealtimeSession:
    """Open, configure and hand back a live session on ``client``.

    Shared by the hosted OpenAI card and the self-hosted one: they differ only
    in which endpoint the client points at, and every hard-won detail below —
    the cleanup after a failed ``__aenter__``, the session payload, the
    mid-call history seed — must stay identical for both, so it lives here
    once rather than in two drifting copies.
    """
    connection_cm = client.realtime.connect(model=model)
    try:
        connection = await connection_cm.__aenter__()
    except BaseException as exc:
        # ``__aenter__`` may allocate the WebSocket before failing or being
        # cancelled. Python does not call ``__aexit__`` for a failed enter,
        # so close both layers explicitly and preserve the original error.
        try:
            await connection_cm.__aexit__(type(exc), exc, exc.__traceback__)
        except BaseException:  # noqa: BLE001 - cleanup must not mask root cause
            log.debug(
                "Realtime connection cleanup after failed enter failed",
                exc_info=True,
            )
        if owns_client:
            try:
                close = getattr(client, "close", None)
                if close is not None:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
            except BaseException:  # noqa: BLE001 - preserve failure/cancellation
                log.debug("Realtime client cleanup after failed enter failed", exc_info=True)
        raise
    payload = _session_payload(cfg, transcription_model=transcription_model)
    session = _OpenAIRealtimeSession(
        connection=connection,
        connection_cm=connection_cm,
        client=client,
        session_id=str(uuid4()),
        session_payload=payload,
        connect_model=model,
        history_seed=tuple(getattr(cfg, "history", ()) or ()),
        rebuild_on_transport_death=rebuild_on_transport_death,
        response_start_timeout_s=response_start_timeout_s,
        disconnect_before_rebuild=disconnect_before_rebuild,
        rebuild_retry_window_s=rebuild_retry_window_s,
        rebuild_retry_step_s=rebuild_retry_step_s,
        prompted_response_retry=prompted_response_retry,
        renders_surface_fallback=renders_surface_fallback,
        owns_client=owns_client,
    )
    try:
        await connection.session.update(session=payload)
        await session.wait_until_ready()
    except BaseException:
        await session.close()
        raise
    # A mid-call open (cross-family fallback after another provider's transport
    # died) carries the call transcript; restore it so the conversation survives
    # the provider crossing (BUG-088).
    await session._seed_conversation_history(connection)
    return session


class OpenAIRealtimeProvider:
    """Structural provider entry point for the OpenAI Realtime family."""

    name = "openai-realtime"
    # Optional provider capability consumed by the shared session fallback.
    # This is account/quota metadata, not a provider-name feature gate.
    credential_family = "openai"
    supports_realtime = True
    implicit_usage_fallback_allowed = True
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE
    credential_candidates = (
        ("realtime_openai_api_key", "JARVIS_REALTIME_OPENAI_API_KEY"),
        ("openai_api_key", "OPENAI_API_KEY"),
    )

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = (api_key or "").strip()

    async def can_open_duplex_session(self) -> bool:
        return bool(self._api_key)

    async def open_session(self, cfg: Any) -> _OpenAIRealtimeSession:
        if not self._api_key:
            raise RuntimeError("OpenAI Realtime API key is not configured")

        from openai import AsyncOpenAI  # lazy (AP-26)

        client = AsyncOpenAI(api_key=self._api_key)
        connect_model = str(getattr(cfg, "model", "") or _MODEL)
        return await _open_realtime_session(client, cfg, model=connect_model)


# ---------------------------------------------------------------------------
# Self-hosted realtime — the same protocol, the user's own endpoint
# ---------------------------------------------------------------------------

#: Connect timeout for the model probe against a self-hosted server. Short on
#: purpose: a server that is not there must not delay the fallback to whatever
#: else the install has.
_LOCAL_PROBE_TIMEOUT_S = 3.0

#: How long a connect to the self-hosted server keeps retrying when Jarvis
#: itself can revive the server (a ``launch_command`` is configured). A local
#: stack needs real time to come back — measured cold revives on the dev box:
#: 52 s with the CPU TTS (2026-08-06 drill), ~90 s with the GPU Qwen3-TTS
#: warm-up incl. CUDA graph capture (2026-08-07) — and the mid-call transport
#: rebuild (BUG-071) lands here, so giving up early turns every server crash
#: into a dead call.
_LOCAL_CONNECT_PATIENT_WINDOW_S = 120.0
# The pinned local brain produced a healthy first token after more than eight
# seconds during a delegated turn (live 2026-08-08). Its response.created
# acknowledgement must not start the post-output stream-stall clock.
_LOCAL_RESPONSE_START_TIMEOUT_S = 90.0
#: The window when NOBODY revives the server (no launch command): nothing is
#: going to change, so only ride out a brief hiccup instead of holding the
#: call hostage.
_LOCAL_CONNECT_SHORT_WINDOW_S = 8.0
_LOCAL_CONNECT_RETRY_STEP_S = 2.0
#: A cold spawn may spend up to 30 seconds preparing its bounded Ollama model
#: before the child even exists. Interactive readiness waits only this long;
#: the same supervisor operation then continues on its worker thread.
_LOCAL_MANAGED_PREFLIGHT_WAIT_S = 0.75
#: The cheap proof that a RUNNING server can take this call. Deliberately its
#: own budget rather than the tail of the revive above: a healthy pool answers
#: in milliseconds, while a cold revive spends ~1 s preparing its bounded
#: Ollama model before it even spawns. Sharing one window made a running
#: server inherit the cold server's bill and lose the call to it.
_LOCAL_POOL_PROBE_TIMEOUT_S = 0.35
#: Why the managed server cannot take a call, in the words the USER needs.
#: This text reaches a toast (and the handshake summary) verbatim, so it names
#: the situation and the next step — the internal "duplex capability probe
#: reported unavailable" told nobody what to do (live 2026-08-09 11:50:47).
#: English on purpose: these are backend strings, and the spoken failure
#: sentence is localized separately by the session's language resolver.
#: The starting sentence is only the fallback: when the supervisor can see
#: the boot stage and a measured ETA, ``_local_starting_reason`` upgrades it
#: to "loading the speaking voice, about 40 seconds left" (the static
#: "about a minute" undersold a 65-second model switch, live 2026-08-10
#: 20:16:49).
_LOCAL_REASON_STARTING = (
    "The local voice server is not answering yet — it is starting in the "
    "background. Try the call again in about a minute."
)
_LOCAL_REASON_BUSY = (
    "The local voice server is still serving another call — its pipeline pool has no free slot."
)
_LOCAL_REASON_NO_CAPACITY = (
    "The local voice server has no usable pipeline — it is still loading "
    "its models, or the pool is wedged and needs a restart."
)
#: Phrased around the existing "needs a server url" marker so the shared
#: classifier lands on NOT_CONFIGURED (a setup gap) instead of a red error.
_LOCAL_REASON_NO_ADDRESS = (
    "The local realtime provider needs a server URL — enter the address on its provider card."
)
#: A managed pool that just reported an idle slot should accept the first
#: interactive handshake promptly. If that readiness becomes stale, bound the
#: race here instead of falling back to the 120-second recovery window. Long
#: patience remains inside the accepted session's rebuild path, where it
#: preserves a conversation instead of holding a new call on "Connecting".
_LOCAL_MANAGED_INTERACTIVE_OPEN_S = 3.0
#: Never respawn the server more often than this — a crash-looping server
#: must not be hammered back up in a tight loop (AP-24 doctrine: mark it bad,
#: do not thrash).
_LOCAL_LAUNCH_MIN_INTERVAL_S = 60.0


def _launch_command_target_state(command: str) -> str:
    """Whether the program a launch command names still exists on this machine.

    ``"missing"`` means the command's first token clearly names something that
    is gone — a managed install whose tree was deleted while its command stayed
    in jarvis.toml previously sat out the full patient reconnect window against
    a spawn that could never succeed (live 2026-08-08: 120 s of "Connecting…"
    ending in a silent idle). ``"present"`` means the target resolves today.
    Everything ambiguous returns ``"unknown"`` and behaves exactly as before —
    fail-open, so a bring-your-own command is never misjudged. Stdlib only.
    """
    text = (command or "").strip()
    if not text:
        return "unknown"
    import shlex
    import shutil
    from pathlib import Path

    head = ""
    if text.startswith('"'):
        # The managed installer always quotes its entry point; a quoted head
        # is unambiguous on every platform.
        end = text.find('"', 1)
        if end > 1:
            head = text[1:end]
    elif os.name == "nt":
        # Raw-string spawn: take the first whitespace token. An UNQUOTED path
        # containing spaces is handled below (fail-open) because CreateProcess
        # prefix probing makes it genuinely ambiguous.
        head = text.split()[0]
    else:
        try:
            head = (shlex.split(text) or [""])[0]
        except ValueError:
            # An unbalanced quote makes the command unparsable — never judge
            # a launch command this function could not read (fail-open).
            return "unknown"
    if not head:
        return "unknown"
    looks_pathlike = "/" in head or "\\" in head or (os.name == "nt" and ":" in head[:3])
    if looks_pathlike:
        if Path(head).exists():
            return "present"
        if os.name == "nt" and not text.startswith('"') and " " in text:
            # Unquoted Windows command with spaces: the naive first token may
            # have cut a spaced path short — cannot tell, so do not judge.
            return "unknown"
        return "missing"
    # A bare program name resolves through PATH. Found → present; not found →
    # still only "unknown": PATH resolution has too many shells and edge cases
    # to declare a user-authored command dead on its account.
    return "present" if shutil.which(head) else "unknown"


def _normalize_local_root(url: str) -> str:
    """Normalize a user-entered server address to an ``…/v1`` API root.

    Whatever the user pastes has to work, because what they paste is whatever
    their server's README printed. Realtime servers advertise a WEBSOCKET
    endpoint — ``ws://localhost:8765/v1/realtime`` is the address the common
    self-hosted stack prints on startup — while the SDK wants the HTTP API root
    and derives the socket URL itself. Handing it the pasted ws:// address
    would fail on a copy-paste the user had every reason to trust.

    So: ``ws``/``wss`` map to ``http``/``https``, a trailing ``/realtime`` is
    dropped, a bare ``host:port`` gains a scheme, and ``0.0.0.0`` (a server
    BIND address, unusable as a client target) maps to loopback — the same
    normalization the local brain cards apply, kept local to this module
    because a plugin does not reach into the rest of the tree.

    ``localhost`` is pinned to ``127.0.0.1`` deliberately: the OS resolver
    tries ``::1`` first while the common self-hosted server binds IPv4 only,
    and that dead IPv6 attempt cost 2,050 ms per connect on the dev box
    (measured 2026-08-08: ws open via "localhost" 2,093 ms, via "127.0.0.1"
    2.1 ms — it WAS the user-felt connect delay). A genuinely IPv6-only
    server stays reachable by entering ``[::1]`` explicitly.
    """
    root = (url or "").strip().rstrip("/")
    if not root:
        return ""
    if "://" not in root:
        root = f"http://{root}"
    if root.startswith("ws://"):
        root = f"http://{root[len('ws://') :]}"
    elif root.startswith("wss://"):
        root = f"https://{root[len('wss://') :]}"
    root = root.replace("://0.0.0.0", "://127.0.0.1")
    root = root.replace("://localhost", "://127.0.0.1")
    if root.endswith("/realtime"):
        root = root[: -len("/realtime")].rstrip("/")
    if root.endswith("/v1"):
        return root
    return f"{root}/v1"


class LocalRealtimeProvider:
    """A self-hosted server that speaks the OpenAI Realtime WebSocket protocol.

    Realtime was the one tier with no local option at all: every card in it
    billed a hosted account, so an install running its brain, its recognizer
    and its voice on its own hardware still had to leave the machine for the
    low-latency voice mode — or give that mode up entirely.

    Deliberately protocol-shaped rather than product-shaped: it names no
    project and bundles no server, it speaks to whatever endpoint the user
    points it at, on this machine or on another box on the LAN.

    Keyless by design — the optional key is attached when the user stored one
    (a reverse proxy, a shared GPU host) and its absence is normal. The card is
    only ever a candidate once the user selected it AND configured a server
    URL: an unconfigured endpoint must never join the ambient fallback chain
    and swallow a turn.
    """

    name = "local-realtime"
    supports_realtime = True
    # Never an implicit stand-in: a self-hosted endpoint is a deliberate choice,
    # and quietly routing a call into one the user did not pick is the opposite
    # of what a local card is for.
    implicit_usage_fallback_allowed = False
    # Eagerly warmed even when configured as a FALLBACK. This card is a local
    # process: warming costs no account round-trip and no metered tokens,
    # while its cold start is the longest of any transport (45-90 s of model
    # loading). Live 2026-08-10: with a subscription primary whose token had
    # expired, the un-warmed local fallback was still stone cold when the
    # first call arrived — the call died with "try again in about a minute"
    # on a machine that could have answered it. The GPU-oversubscription
    # caveat behind opt-in fallback warming targets stacking MULTIPLE native
    # model stacks; this is the single local stack the user explicitly chose.
    eager_warm_as_fallback = True
    # Small self-hosted brains prefill the whole instruction block EVERY turn;
    # the full ~24k-char profile cost 7.8 s of LLM time per answer against
    # qwen2.5:7b (live 2026-08-07). The session builder honors this capability
    # with the distilled persona and a prefix-cache-friendly static-first
    # ordering (AP-21: a declared capability, never a provider-name check).
    prefers_compact_instructions = True
    # Small/local realtime models must not answer concrete public facts from
    # unaided recall.  The session reads this capability once when accepting
    # the provider and performs one bounded ToolExecutor-backed web lookup.
    requires_public_fact_grounding = True
    # A delegate readback on this card is rendered by the server's own LLM +
    # TTS (4-8 s live on the dev box), not by a hosted realtime model that
    # starts audio in under a second. The shared 2.5 s readback window
    # guaranteed the surface fallback fired first — and this card has no
    # realtime-scoped surface TTS, so that fallback is TEXT-ONLY and then
    # withheld the real audio answer arriving seconds later: the user heard
    # nothing at all (live 2026-08-08 15:24). Declared budget, same AP-21
    # doctrine as handshake_budget_s.
    readback_render_budget_s = 12.0
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE
    # EMPTY, not absent. The keyless path is chosen by this tuple being empty
    # (the factory then builds the provider through ``external_login_ready`` +
    # ``from_runtime_config`` instead of handing it a key), but the RealtimeProvider
    # protocol requires the attribute to EXIST. Leaving it off made the class fail
    # the runtime protocol check, so the loader rejected it, the factory produced no
    # candidate at all, and a call on this card sat on "connecting" forever with
    # nothing ever reaching the server.
    credential_candidates: tuple[tuple[str, str | None], ...] = ()

    # Last server spawn across ALL provider instances: the factory rebuilds
    # providers freely, and a per-instance stamp would let every rebuild spawn
    # another server.
    _last_launch_at: float = float("-inf")
    # One SDK client per endpoint, shared across sessions AND provider
    # instances: building a fresh AsyncOpenAI costs ~230 ms per call (httpx +
    # SSL-context setup, measured 2026-08-08) — a quarter of the whole
    # user-felt connect. The client is a stateless connection factory, so a
    # server restart never stales it; sessions opened on it carry
    # owns_client=False so their close() leaves it alive.
    _client_cache: dict[tuple[str, str], Any] = {}
    # Served-model probe results per endpoint, ``{base_url: (model, expires)}``:
    # the probe builds its own httpx client per call — the SAME ~200 ms
    # SSL-context bill as above, paid on EVERY connect while the card's model
    # sits on "auto". A server's roster changes only when its operator
    # reconfigures it, so five minutes of reuse is honest; the prewarm fills
    # the cache so the first real call never pays the probe either.
    _model_cache: dict[str, tuple[str, float]] = {}
    _MODEL_CACHE_TTL_S = 300.0
    _background_start_tasks: set[asyncio.Task[bool]] = set()

    def __init__(
        self,
        *,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        launch_command: str = "",
    ) -> None:
        self._base_url = _normalize_local_root(base_url)
        self._api_key = (api_key or "").strip()
        self._model = (model or "").strip()
        self._launch_command = (launch_command or "").strip()
        self._managed_interactive_preflight = False
        self._duplex_unavailable_reason = ""

    # -- factory wiring ----------------------------------------------------

    @staticmethod
    def _provider_config(cfg: Any) -> Any:
        """The card's stored settings, read defensively off the config object.

        Attribute access only — no imports from the rest of the tree, which is
        what keeps this module a plugin rather than a dependency.
        """
        providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
        getter = getattr(providers, "get", None)
        return getter("local-realtime") if callable(getter) else None

    @classmethod
    def external_login_ready(cls, cfg: Any = None) -> bool:
        """Whether a server address is configured. Synchronous and offline.

        The factory calls this on an audio loop, so it must never touch the
        network: the question here is "is there an endpoint to try at all",
        and ``open_session`` is what finds out whether it answers.
        """
        provider_cfg = cls._provider_config(cfg)
        return bool(_normalize_local_root(str(getattr(provider_cfg, "base_url", "") or "")))

    @classmethod
    def from_runtime_config(cls, cfg: Any) -> LocalRealtimeProvider:
        provider_cfg = cls._provider_config(cfg)
        return cls(
            base_url=str(getattr(provider_cfg, "base_url", "") or ""),
            # ENV only — a credential never belongs in jarvis.toml (AP-12), and
            # most self-hosted servers need none at all. A user who put one
            # behind a proxy or started vLLM with --api-key exports it once.
            api_key=os.environ.get("JARVIS_LOCAL_REALTIME_API_KEY", ""),
            model=str(getattr(provider_cfg, "model", "") or ""),
            launch_command=str(getattr(provider_cfg, "launch_command", "") or ""),
        )

    # -- session -----------------------------------------------------------

    @property
    def duplex_unavailable_reason(self) -> str:
        """Why the last probe said no, in a sentence a user can act on.

        Optional capability, read defensively by the session (AP-21: never a
        provider-name check). Empty whenever the last probe said yes.
        """
        return self._duplex_unavailable_reason

    @staticmethod
    def _local_starting_reason(supervisor: Any) -> str:
        """The boot refusal, upgraded with the live stage and honest ETA.

        Falls back to the static sentence whenever the supervisor cannot
        prove a stage or a measured remaining time — a progress hint must
        never invent one.
        """
        try:
            boot = supervisor.boot_snapshot()
            if not isinstance(boot, dict) or not boot.get("starting"):
                return _LOCAL_REASON_STARTING
            label = boot.get("stage_label")
            remaining = boot.get("remaining_s")
            if isinstance(label, str) and label:
                if (
                    isinstance(remaining, (int, float))
                    and not isinstance(remaining, bool)
                    and remaining > 0
                ):
                    # Tens of seconds is the honest resolution of a model
                    # load; a to-the-second countdown would imply precision
                    # the median of five boots does not have.
                    rounded = max(10, round(float(remaining) / 10.0) * 10)
                    return (
                        "The local voice server is starting — currently "
                        f"{label}, about {rounded} seconds left. Try the "
                        "call again shortly."
                    )
                return (
                    "The local voice server is starting — currently "
                    f"{label}. Try the call again in about a minute."
                )
        except Exception:  # noqa: BLE001 - a progress hint never breaks the verdict
            log.debug("local-realtime: boot progress hint failed", exc_info=True)
        return _LOCAL_REASON_STARTING

    async def can_open_duplex_session(self) -> bool:
        self._duplex_unavailable_reason = ""
        if not self._base_url:
            self._duplex_unavailable_reason = _LOCAL_REASON_NO_ADDRESS
            return False
        if not self._launch_command or not self._server_is_local_process():
            return True

        import importlib  # lazy (AP-26)

        try:
            supervisor = importlib.import_module("jarvis.realtime.local_server.supervisor")
            if not bool(supervisor.is_managed_launch_command(self._launch_command)):
                # A bring-your-own server is not required to expose the pinned
                # stack's private /v1/pool readiness contract.
                return True
        except Exception:  # noqa: BLE001 - preserve generic BYO connectivity
            log.debug(
                "local-realtime: managed readiness detection failed",
                exc_info=True,
            )
            return True

        self._managed_interactive_preflight = True

        def _pool_can_take_this_call() -> bool:
            """The running server's own verdict, asked directly.

            Separate from the revive below so a healthy pool never waits
            behind a spawn it does not need.
            """
            try:
                pool = supervisor.probe_runtime(self._base_url, timeout=_LOCAL_POOL_PROBE_TIMEOUT_S)
            except Exception:  # noqa: BLE001 - a probe never breaks voice startup
                log.warning(
                    "local-realtime: pool capacity probe failed",
                    exc_info=True,
                )
                self._duplex_unavailable_reason = self._local_starting_reason(supervisor)
                return False
            if pool is None:
                self._duplex_unavailable_reason = self._local_starting_reason(supervisor)
                return False
            if int(pool.get("available", 0)) > 0:
                self._duplex_unavailable_reason = ""
                return True
            self._duplex_unavailable_reason = (
                _LOCAL_REASON_BUSY if int(pool.get("in_use", 0)) > 0 else _LOCAL_REASON_NO_CAPACITY
            )
            return False

        def _ready_or_warming() -> bool:
            try:
                outcome = str(
                    supervisor.ensure_running(
                        launch_command=self._launch_command,
                        base_url=self._base_url,
                        reason="interactive-preflight",
                    )
                )
                if outcome in {"spawned", "already-running"}:
                    # A cold child keeps warming after this call fails fast;
                    # the monitor owns later crash/wedge recovery.
                    supervisor.start_runtime_monitor(
                        launch_command=self._launch_command,
                        base_url=self._base_url,
                    )
                pool = supervisor.probe_runtime(self._base_url, timeout=0.35)
                return bool(pool and pool.get("available", 0) > 0)
            except Exception:  # noqa: BLE001 - a probe never breaks voice startup
                log.warning(
                    "local-realtime: managed readiness probe failed; the "
                    "interactive call will not wait on a cold server",
                    exc_info=True,
                )
                return False

        task = asyncio.create_task(
            asyncio.to_thread(_ready_or_warming),
            name="local-realtime-interactive-preflight",
        )
        LocalRealtimeProvider._background_start_tasks.add(task)

        def _consume_background_start(done: asyncio.Task[bool]) -> None:
            LocalRealtimeProvider._background_start_tasks.discard(done)
            if done.cancelled():
                return
            try:
                done.result()
            except Exception:  # noqa: BLE001 - background failure was already degraded
                log.warning(
                    "local-realtime: background managed start failed",
                    exc_info=True,
                )

        task.add_done_callback(_consume_background_start)

        # A server that is already serving answers for itself, in its own
        # budget. Before this the verdict came only from the tail of the
        # revive above, so a running-but-briefly-slow pool was judged by a
        # clock sized for a cold start and lost the call to it.
        if await asyncio.to_thread(_pool_can_take_this_call):
            return True
        try:
            if bool(
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=_LOCAL_MANAGED_PREFLIGHT_WAIT_S,
                )
            ):
                self._duplex_unavailable_reason = ""
                return True
        except TimeoutError:
            log.info(
                "local-realtime: managed server is still starting in the "
                "background; refusing to hold this call on Connecting"
            )
            self._duplex_unavailable_reason = self._local_starting_reason(supervisor)
        return False

    async def _resolve_model(self) -> str:
        """The configured model, else the first one the server serves.

        A self-hosted realtime server names its model whatever its operator
        called it, so there is no default worth hardcoding. Asking
        ``/v1/models`` mirrors what the local brain card does and spares the
        user a field they would have to look up. A server that does not answer
        that endpoint is not an error — the connect below carries the honest
        failure.
        """
        if self._model:
            return self._model
        if self._launch_command:
            import importlib  # lazy (AP-26)

            try:
                supervisor = importlib.import_module("jarvis.realtime.local_server.supervisor")
                if supervisor.is_managed_launch_command(self._launch_command):
                    # The pinned speech-to-speech server implements the
                    # realtime protocol and /v1/pool, but not /v1/models.
                    # Probing a known-unsupported route delayed every cold
                    # connect and turned an expected 404 into misleading log
                    # noise.
                    LocalRealtimeProvider._model_cache[self._base_url] = (
                        _MODEL,
                        time.monotonic() + LocalRealtimeProvider._MODEL_CACHE_TTL_S,
                    )
                    return _MODEL
            except Exception:  # noqa: BLE001 - generic BYO discovery remains available
                log.debug("local-realtime: managed-command detection failed", exc_info=True)
        cached = LocalRealtimeProvider._model_cache.get(self._base_url)
        if cached is not None and cached[1] > time.monotonic():
            return cached[0]
        import httpx  # lazy (AP-26)

        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        try:
            async with httpx.AsyncClient(timeout=_LOCAL_PROBE_TIMEOUT_S) as client:
                resp = await client.get(f"{self._base_url}/models", headers=headers)
                resp.raise_for_status()
                served = [
                    str(entry.get("id") or "").strip() for entry in (resp.json().get("data") or [])
                ]
        except Exception as exc:  # noqa: BLE001 — the connect reports the real failure
            log.info(
                "local-realtime: /models probe failed at %s (%s); connecting with "
                "the protocol default",
                self._base_url,
                type(exc).__name__,
            )
            # A server without the endpoint answers the same way every time —
            # cache the default so the probe is not re-paid on every call.
            LocalRealtimeProvider._model_cache[self._base_url] = (
                _MODEL,
                time.monotonic() + LocalRealtimeProvider._MODEL_CACHE_TTL_S,
            )
            return _MODEL
        chosen = next((name for name in served if name), _MODEL)
        if chosen != _MODEL:
            log.info("local-realtime: no model configured — using served %s", chosen)
        LocalRealtimeProvider._model_cache[self._base_url] = (
            chosen,
            time.monotonic() + LocalRealtimeProvider._MODEL_CACHE_TTL_S,
        )
        return chosen

    def _connect_retry_window_s(self) -> float:
        """How long a failing connect keeps retrying before giving up.

        Patient only when patience can pay off: with a ``launch_command``
        Jarvis revives a crashed server itself and the retries bridge its
        warm-up. Without one — or with a command whose program provably no
        longer exists (deleted managed install) — nothing will change on the
        other end, so a short window rides out a hiccup and then reports
        honestly.
        """
        if self._launch_command and _launch_command_target_state(self._launch_command) != "missing":
            return _LOCAL_CONNECT_PATIENT_WINDOW_S
        return _LOCAL_CONNECT_SHORT_WINDOW_S

    @property
    def handshake_budget_s(self) -> float:
        """Declared handshake need (capability read by the session opener).

        The shared 12 s handshake ceiling would behead the patient reconnect
        that bridges a revived server's ~25 s model warm-up (the same
        mechanism the Codex transport uses for its cold start, AP-21: a
        declared capability, never a provider-name check). The margin on top
        of the retry window covers the actual protocol handshake once the
        server answers.
        """
        return self._connect_retry_window_s() + 15.0

    def _server_is_local_process(self) -> bool:
        """Whether the configured endpoint runs on THIS machine.

        Only a loopback server may be spawned here — launching a process
        because a LAN box went down would start a second server on the wrong
        host.
        """
        host = self._base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        return host.lower() in {"localhost", "127.0.0.1", "::1"}

    def _maybe_launch_server(self) -> bool:
        """Revive the self-hosted server, rate-limited. True if spawned.

        The reference stack's whole process dies silently under load (live
        2026-08-06 19:57: its log ends mid-request, the call died with it,
        and nothing owned bringing it back). The actual spawn lives in the
        supervisor module — ONE code path shared with the boot-time prewarm,
        so the two can never double-spawn. This cheap provider gate advances
        only after a real spawn; transient lifecycle contention must remain
        immediately retryable. The supervisor owns the durable rate limit.
        """
        if not self._launch_command or not self._server_is_local_process():
            return False
        now = time.monotonic()
        if now - LocalRealtimeProvider._last_launch_at < _LOCAL_LAUNCH_MIN_INTERVAL_S:
            return False
        import importlib  # lazy (AP-26)

        # importlib, not a literal ``from jarvis...``: the plugin-module
        # contract (no jarvis imports, AST-checked) counts lazy imports too.
        try:
            supervisor = importlib.import_module("jarvis.realtime.local_server.supervisor")
            outcome = str(
                supervisor.ensure_running(
                    launch_command=self._launch_command,
                    base_url=self._base_url,
                    reason="connect-revive",
                )
            )
        except Exception as exc:  # noqa: BLE001 — a bad command must not kill the call
            log.warning(
                "local-realtime: reviving the server failed (%s: %s) — "
                "check [brain.providers.local-realtime].launch_command",
                type(exc).__name__,
                exc,
            )
            return False
        if outcome == "spawned":
            LocalRealtimeProvider._last_launch_at = now
            log.info(
                "local-realtime: server unreachable — launched the configured "
                "server command; waiting for it to come up"
            )
            return True
        log.info("local-realtime: revive skipped (%s)", outcome)
        return False

    @classmethod
    async def prespawn_transport(cls, cfg: Any) -> bool:
        """Spawn-only prestart, safe to fire the moment the app boots.

        The managed local server pays the longest cold start of any transport
        (measured 52-90 s of model loading, see the connect window above), and
        it loads in its OWN process — so every second the warm worker spends
        behind its voice gate is a second the local voice cannot answer for no
        reason. This capability only STARTS the server and arms the crash
        monitor; readiness, the smoke-marker repair, and brain residency stay
        with :meth:`warm_transport`, which runs behind the gates exactly as
        before. Discovered by name (AP-21); best-effort by contract — no
        failure here may reach the caller.
        """
        try:
            provider = cls.from_runtime_config(cfg)
            if (
                not provider._base_url
                or not provider._launch_command
                or not provider._server_is_local_process()
            ):
                return False
            if _launch_command_target_state(provider._launch_command) == "missing":
                # The regular warm logs the actionable "reinstall or clear it"
                # message moments later; a second copy here would be noise.
                return False
            import importlib  # lazy (AP-26)

            def _spawn_only() -> bool:
                supervisor = importlib.import_module("jarvis.realtime.local_server.supervisor")
                outcome = str(
                    supervisor.ensure_running(
                        launch_command=provider._launch_command,
                        base_url=provider._base_url,
                        reason="boot-prespawn",
                    )
                )
                if outcome not in ("spawned", "already-running"):
                    return False
                supervisor.start_runtime_monitor(
                    launch_command=provider._launch_command,
                    base_url=provider._base_url,
                )
                return True

            return bool(await asyncio.to_thread(_spawn_only))
        except Exception:  # noqa: BLE001 — prespawning is best-effort by contract
            log.debug("local-realtime: prespawn_transport failed", exc_info=True)
            return False

    @classmethod
    async def warm_transport(cls, cfg: Any) -> bool:
        """Boot-time prewarm (called by the shared realtime warm worker).

        Brings the self-hosted server up BEFORE the first call and makes the
        Ollama brain model resident, so a warm connect costs a handshake
        instead of a 45-90 s cold boot plus a multi-second model load. A
        capability the warm worker discovers by name (AP-21); best-effort by
        contract — no failure here may reach the caller.
        """
        try:
            provider = cls.from_runtime_config(cfg)
            if (
                not provider._base_url
                or not provider._launch_command
                or not provider._server_is_local_process()
            ):
                return False
            if _launch_command_target_state(provider._launch_command) == "missing":
                log.info(
                    "local-realtime: prewarm skipped — the configured launch "
                    "command points at a program that does not exist "
                    "(reinstall or clear it)."
                )
                return False
            import importlib  # lazy (AP-26)

            def _warm() -> bool:
                supervisor = importlib.import_module("jarvis.realtime.local_server.supervisor")
                managed = bool(supervisor.is_managed_launch_command(provider._launch_command))
                outcome = str(
                    supervisor.ensure_running(
                        launch_command=provider._launch_command,
                        base_url=provider._base_url,
                        reason="prewarm",
                    )
                )
                if outcome not in ("spawned", "already-running"):
                    return False
                if managed:
                    ready = bool(
                        supervisor.wait_until_ready(
                            provider._base_url,
                            launch_command=provider._launch_command,
                            cleanup_on_timeout=True,
                        )
                    )
                    if not ready:
                        log.warning(
                            "local-realtime: prewarm timed out before the model pool was ready"
                        )
                        return False
                    install = importlib.import_module("jarvis.realtime.local_server.install")
                    if not bool(install.repair_smoke_marker_from_live_runtime(provider._base_url)):
                        log.warning(
                            "local-realtime: server is ready but its managed-install "
                            "smoke proof could not be repaired"
                        )
                    supervisor.start_runtime_monitor(
                        launch_command=provider._launch_command,
                        base_url=provider._base_url,
                    )
                # Only the pinned managed stack promises /v1/pool. A BYO
                # OpenAI-compatible server remains valid without that private
                # endpoint. For managed launches, the wait above also ensures
                # the speech pipeline loads before Ollama competes for VRAM.
                supervisor.warm_brain(launch_command=provider._launch_command)
                return True

            warmed = await asyncio.to_thread(_warm)
            if warmed:
                try:
                    # Fill the model-probe cache too, so the first real call
                    # pays neither the probe nor its httpx client build.
                    await provider._resolve_model()
                except Exception:  # noqa: BLE001, S110 — probe is best-effort
                    pass
            return warmed
        except Exception:  # noqa: BLE001 — warming is best-effort by contract
            log.debug("local-realtime: warm_transport failed", exc_info=True)
            return False

    async def open_session(self, cfg: Any) -> _OpenAIRealtimeSession:
        if not self._base_url:
            raise RuntimeError(
                "No server URL configured for the self-hosted realtime provider "
                "— set it on the provider card first (e.g. http://127.0.0.1:8080)."
            )
        managed_interactive = self._managed_interactive_preflight
        self._managed_interactive_preflight = False
        retry_window_s = (
            _LOCAL_MANAGED_INTERACTIVE_OPEN_S
            if managed_interactive
            else self._connect_retry_window_s()
        )
        deadline = time.monotonic() + retry_window_s
        attempt = 0
        while True:
            attempt += 1
            try:
                if managed_interactive:
                    remaining = max(0.001, deadline - time.monotonic())
                    return await asyncio.wait_for(
                        self._open_session_once(cfg),
                        timeout=remaining,
                    )
                return await self._open_session_once(cfg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — bounded retry, last error re-raised
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                # A connect failure with a launch command whose program no
                # longer exists can never be retried into success: reviving
                # would spawn a deleted entry point. Fail NOW with the fixing
                # action instead of holding the call in "Connecting…" for the
                # whole patient window (live 2026-08-08: the managed install
                # tree was gone while jarvis.toml still pointed at it).
                if (
                    self._launch_command
                    and self._server_is_local_process()
                    and _launch_command_target_state(self._launch_command) == "missing"
                ):
                    raise RuntimeError(
                        "The local realtime server is not installed anymore: "
                        "its configured launch command points at a program "
                        "that does not exist. Reinstall the managed server on "
                        "the local-realtime provider card, or clear its "
                        "launch command."
                    ) from exc
                # First failure: the server may simply be gone — revive it if
                # this install owns it, then keep knocking. A server that is
                # merely warming up needs the same patience either way.
                launched = self._maybe_launch_server()
                log.info(
                    "local-realtime: connect attempt %d failed (%s)%s — "
                    "retrying for up to another %.0f s",
                    attempt,
                    type(exc).__name__,
                    " after relaunching the server" if launched else "",
                    remaining,
                )
                await asyncio.sleep(min(_LOCAL_CONNECT_RETRY_STEP_S, max(remaining, 0.0)))

    def _shared_client(self) -> Any:
        """The cached SDK client for this endpoint (built once, reused forever)."""
        from openai import AsyncOpenAI  # lazy (AP-26)

        key = (self._base_url, self._api_key or "local")
        client = LocalRealtimeProvider._client_cache.get(key)
        if client is None:
            client = AsyncOpenAI(
                # Most self-hosted servers ignore the key; the SDK still
                # insists on a non-empty one.
                api_key=self._api_key or "local",
                base_url=self._base_url,
            )
            LocalRealtimeProvider._client_cache[key] = client
        return client

    async def _open_session_once(self, cfg: Any) -> _OpenAIRealtimeSession:
        client = self._shared_client()
        connect_model = str(getattr(cfg, "model", "") or "").strip()
        if not connect_model or connect_model == "auto":
            # "auto" is what the card offers while the server's roster is
            # unknown; resolve it into a real name rather than sending a
            # placeholder the server would reject.
            connect_model = await self._resolve_model()
        return await _open_realtime_session(
            client,
            cfg,
            model=connect_model,
            transcription_model=None,
            # A self-hosted process can crash and be revived seconds later —
            # a dead transport is worth rebuilding in place (BUG-071 path)
            # instead of ending the call.
            rebuild_on_transport_death=True,
            # Local inference can spend seconds generating before its first
            # output event; only silence after this declared budget is a wedge.
            response_start_timeout_s=_LOCAL_RESPONSE_START_TIMEOUT_S,
            # The managed server has one pipeline slot. Release it before the
            # replacement handshake and ride out asynchronous handler drain.
            disconnect_before_rebuild=True,
            rebuild_retry_window_s=self._connect_retry_window_s(),
            rebuild_retry_step_s=_LOCAL_CONNECT_RETRY_STEP_S,
            # The scrub-gate language retry must arrive as a fresh user item:
            # this server keeps the cancelled answer in its conversation, and
            # a bare response.create makes a small brain answer with an empty
            # completion (live 2026-08-10 — the retry returned one token).
            prompted_response_retry=True,
            # This server's voice exists only behind the live session (one
            # pipeline slot, no sibling TTS endpoint): the scrub gate's
            # safety-net phrase must ride the session itself, or a cancelled
            # turn stays completely silent under strict mode separation.
            renders_surface_fallback=True,
            # The client above is cached across sessions; closing this
            # session must not tear it down.
            owns_client=False,
        )
