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
    if turn_detection == "server_vad":
        turn_detection_config["silence_duration_ms"] = int(
            getattr(cfg, "silence_duration_ms", 1_500) or 1_500
        )

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
    ) -> None:
        self._conn = connection
        self._connection_cm = connection_cm
        self._client = client
        self._events = connection.__aiter__()
        self.session_id = session_id
        # The full session contract as sent at open. Kept current by
        # update_session() so a BUG-064 re-arm never reverts live
        # instructions or tool declarations to their session-start values.
        self._session_contract = session_payload
        self._last_contract_rearm = float("-inf")
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
        await self._conn.input_audio_buffer.append(
            audio=base64.b64encode(pcm).decode("ascii")
        )

    async def receive(self) -> AsyncIterator[_ProviderEvent]:
        async for event in self._events:
            event_type = str(getattr(event, "type", "") or "")
            if event_type == "response.created":
                await self._handle_response_created(event)
                continue
            if event_type.startswith("response.") and not self._response_is_accepted(
                event
            ):
                continue
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
                yield _ProviderEvent(
                    type="input_transcript",
                    text="",
                    is_final=True,
                    error=_error_message(event),
                    item_id=str(getattr(event, "item_id", "") or "") or None,
                )
            elif event_type == "input_audio_buffer.speech_started":
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
                error_code = _error_code(event)
                yield _ProviderEvent(
                    type="error",
                    error=_error_message(event),
                    recoverable=error_code in _RECOVERABLE_ERROR_CODES,
                )

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

    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None:
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
            # BUG-064: under the manual-response contract an unsolicited
            # response should be impossible — its arrival means the server
            # dropped the session configuration (observed live on grok-realtime
            # 2026-07-16 08:07: after a barge-in cancel, input transcription
            # stopped and server VAD auto-created responses; the session sat
            # deaf until manual hang-up). Re-assert the full contract so the
            # session hears again; on a healthy server this is an idempotent
            # no-op.
            await self._rearm_session_contract()
            return

        if not response_id:
            log.warning(
                "OpenAI Realtime response.created had no response id; "
                "response events remain suppressed"
            )
            return
        self._accepted_response_ids.add(response_id)

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
        connection_cm = client.realtime.connect(
            model=str(getattr(cfg, "model", "") or _MODEL)
        )
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
        )
        try:
            await connection.session.update(session=payload)
            await session.wait_until_ready()
        except BaseException:
            await session.close()
            raise
        return session
