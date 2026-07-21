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
# the session sat silent until manual hang-up. A healthy in-flight response
# streams events every few tens of milliseconds; one that produces NO
# response event at all for this long is dead, and the transport is rebuilt.
_RESPONSE_STALL_S = 8.0
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


def _session_payload(cfg: Any) -> dict[str, Any]:
    """Build the current GA ``session.update`` payload.

    Audio output already includes a transcript side-channel, so the Realtime
    API accepts ``[\"audio\"]`` only; requesting text and audio together is
    invalid. PCM input and output are both explicitly declared as 24 kHz.
    """
    transcription: dict[str, Any] = {"model": "gpt-4o-mini-transcribe"}
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
    if voice:
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
    ) -> None:
        self._conn = connection
        self._connection_cm = connection_cm
        self._client = client
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
        # Last moment the server showed ANY response-lifecycle sign of life
        # (any ``response.*`` event, or our own response.create going out).
        # While ``_response_idle`` is clear this is the liveness signal that
        # detects a response whose ``response.done`` the server swallowed
        # (BUG-064 recurrence #3) — without it a stuck lifecycle disarms
        # every idle-gated deaf-wedge defense at once.
        self._last_response_activity = time.monotonic()
        self._last_item_id = ""
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
                    "Realtime transport rebuilt after the provider went deaf "
                    "(no input transcript for a heard user turn); the last "
                    "utterance was lost"
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
            yield _ProviderEvent(
                type="output_transcript_delta",
                text=str(getattr(event, "delta", "") or ""),
            )
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
            content_type = "input_text" if role == "user" else "text"
            try:
                await connection.conversation.item.create(
                    item={
                        "type": "message",
                        "role": role,
                        "content": [
                            {"type": content_type, "text": message["text"]}
                        ],
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
    ) -> None:
        del language  # Input transcription stays provider-inferred and multilingual.
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
            self._last_response_activity = time.monotonic()
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
                self._last_response_activity = time.monotonic()
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
        """A response marked in flight that emits no events at all is dead.

        BUG-064 recurrence #3 (grok-realtime 2026-07-16 10:51): the server
        never sent ``response.done`` for an accepted response whose output a
        local barge-in had dropped, so ``_response_idle`` stayed clear and
        every idle-gated defense (adoption, transcript deadline, rebuild)
        was disarmed at once. A healthy in-flight response streams events
        continuously; total silence for ``_RESPONSE_STALL_S`` is proof the
        lifecycle will never finish on this transport.
        """
        if self._closed or self._session_contract is None:
            return False
        if self._response_idle.is_set():
            return False
        return time.monotonic() - self._last_response_activity >= _RESPONSE_STALL_S

    def _maybe_begin_rebuild(self) -> None:
        if self._response_lifecycle_stalled():
            self._begin_rebuild(
                "in-flight response produced no response event for "
                f"{_RESPONSE_STALL_S:.0f} s — response.done is not coming"
            )
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

    async def _rebuild_transport(self) -> None:
        """Replace the connection when a deaf session cannot be re-armed.

        BUG-064 escalation (grok-realtime 2026-07-16 09:23): the contract
        re-arm demonstrably ran and the server still never delivered another
        input transcript — the call sat in LISTENING until manual hang-up.
        A fresh transport carrying the same session contract is the only
        remaining repair. In-call conversation history is lost, which is
        strictly better than a session that can no longer hear at all. A
        failed rebuild closes the session so the orchestrator reports an
        honest provider error instead of keeping a silently deaf call open.
        """
        log.warning(
            "OpenAI Realtime rebuilding the transport: the server kept the "
            "session deaf (heard user turn, no input transcript within "
            "%.0f s) despite a session-contract re-arm",
            _TRANSCRIPT_OVERDUE_S,
        )
        old_cm = self._connection_cm
        try:
            connection_cm = self._client.realtime.connect(model=self._connect_model or _MODEL)
            connection = await connection_cm.__aenter__()
            try:
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

                await asyncio.wait_for(_wait_ready(), timeout=_HANDSHAKE_TIMEOUT_S)
            except BaseException as exc:
                try:
                    await connection_cm.__aexit__(type(exc), exc, exc.__traceback__)
                except BaseException:  # noqa: BLE001 — preserve the root cause
                    log.debug(
                        "OpenAI Realtime rebuild cleanup after failed handshake failed",
                        exc_info=True,
                    )
                raise
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — degrade honestly instead of hanging deaf
            log.warning(
                "OpenAI Realtime transport rebuild failed; closing the dead session",
                exc_info=True,
            )
            await self.close()
            return
        # Restore the call transcript before any new turn flows (BUG-088):
        # the dead transport held the conversation server-side, and without
        # this seed the rebuilt session answers follow-ups with amnesia.
        await self._seed_conversation_history(connection)
        # Swap fully initialized state first; only then retire the old
        # transport so send_audio and the receive() hop never observe a
        # half-built connection.
        self._conn = connection
        self._connection_cm = connection_cm
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
        self._last_response_activity = time.monotonic()
        self._server_heard_user_since_response = False
        self._auto_adopted_unanswered_input = False
        self._response_idle.set()
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
        try:
            await self._connection_cm.__aexit__(None, None, None)
        finally:
            close = getattr(self._client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result


class OpenAIRealtimeProvider:
    """Structural provider entry point for the OpenAI Realtime family."""

    name = "openai-realtime"
    # Optional provider capability consumed by the shared session fallback.
    # This is account/quota metadata, not a provider-name feature gate.
    credential_family = "openai"
    supports_realtime = True
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
        connection_cm = client.realtime.connect(model=connect_model)
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
                    "OpenAI Realtime connection cleanup after failed enter failed",
                    exc_info=True,
                )
            try:
                close = getattr(client, "close", None)
                if close is not None:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
            except BaseException:  # noqa: BLE001 - preserve failure/cancellation
                log.debug(
                    "OpenAI Realtime client cleanup after failed enter failed",
                    exc_info=True,
                )
            raise
        payload = _session_payload(cfg)
        session = _OpenAIRealtimeSession(
            connection=connection,
            connection_cm=connection_cm,
            client=client,
            session_id=str(uuid4()),
            session_payload=payload,
            connect_model=connect_model,
            history_seed=tuple(getattr(cfg, "history", ()) or ()),
        )
        try:
            await connection.session.update(session=payload)
            await session.wait_until_ready()
        except BaseException:
            await session.close()
            raise
        # A mid-call open (cross-family fallback after another provider's
        # transport died) carries the call transcript; restore it so the
        # conversation survives the provider crossing (BUG-088).
        await session._seed_conversation_history(connection)
        return session
