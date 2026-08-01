"""Realtime voice through a user's authenticated ChatGPT Codex session.

The adapter intentionally contains no platform API key path.  It talks only to
the local Codex app-server, which owns ChatGPT authentication, and keeps all
imports from ``jarvis.*`` lazy so plugin discovery stays off the startup path.
The app-server realtime surface is experimental. Failures may cross only to
realtime fallbacks the user configured explicitly; otherwise the session
closes honestly instead of silently entering usage-billed API voice.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_INPUT_RATE = 24_000
_OUTPUT_RATE = 24_000
_BROKER_OFFER_WAIT_S = 3.0
_OUTPUT_QUIESCENCE_S = 0.5
_NORMALIZATION_QUEUE_MAX = 128
_REMOTE_CLEANUP_TIMEOUT_S = 1.5
_TURN_INTERRUPT_TIMEOUT_S = 1.5
# The experimental v1 protocol was shut off server-side with the ChatGPT-Live
# launch (every v1 start now answers "403 Voice session access denied",
# verified live 2026-08-01). v3 is the ChatGPT-Live protocol: the SERVER
# chooses the model (a client `model` field is rejected outright), the client
# only picks the voice. The legacy model id is still accepted in config as a
# no-op so older pins keep working.
_LEGACY_V1_MODEL = "gpt-realtime-1.5"
_DEFAULT_VOICE = "cove"
# Server-confirmed v3 roster (the refusal for an unknown voice lists exactly
# these nine, verified live 2026-08-01).
_V3_VOICES = frozenset(
    {
        "arbor",
        "breeze",
        "cove",
        "ember",
        "juniper",
        "maple",
        "sol",
        "spruce",
        "vale",
    }
)

_THREAD_BASE_INSTRUCTIONS = (
    # The thread used to be told it was a dumb pipe ("never perform actions"),
    # which is why the voice had no identity, no project knowledge and no idea
    # that actions exist. The SECURITY boundary is unchanged — the Codex agent
    # itself still never touches tools or the filesystem — but the assistant
    # is now allowed to be an assistant. Its real persona, capabilities and
    # project context arrive as developer context at the start of every call.
    "You are the live voice of the user's own assistant on an open call. "
    "Speak naturally and conversationally, in short spoken sentences — never "
    "read out lists, markup, or boilerplate. Your persona, your capabilities "
    "and the current project context are delivered as developer context at "
    "the start of this call: follow them as your own identity and knowledge. "
    "Boundary: you are the VOICE, not the executor. Never run tools, shell "
    "commands, applications, plugins, skills, web search, MCP servers, or "
    "other agents yourself, and never read or write the filesystem. When the "
    "user asks for something to be DONE, request a handoff — the user's own "
    "supervisor performs it and reports back through you."
)
_THREAD_DEVELOPER_INSTRUCTIONS = (
    "Execution boundary: do not call tools, shell commands, applications, "
    "plugins, skills, web search, MCP servers, or other agents, and do not "
    "read or write the filesystem. Every action goes to the client through a "
    "handoff. Conversation itself — answering, explaining, remembering what "
    "was said, using the developer context you were given — is your job."
)
_LANGUAGE_UPDATE_TEXT = {
    "de": (
        "For every following assistant audio and text response, reply only in "
        "German. This is a voice-rendering instruction, not a request to use "
        "tools or perform an action."
    ),
    "en": (
        "For every following assistant audio and text response, reply only in "
        "English. This is a voice-rendering instruction, not a request to use "
        "tools or perform an action."
    ),
    "es": (
        "For every following assistant audio and text response, reply only in "
        "Spanish. This is a voice-rendering instruction, not a request to use "
        "tools or perform an action."
    ),
}


@dataclass(frozen=True, slots=True)
class _PcmChunk:
    pcm: bytes
    sample_rate: int
    timestamp_ns: int = 0
    channels: int = 1


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
    handoff_id: str | None = None
    provider_turn_id: str | None = None


def _notification_parts(notification: Any) -> tuple[str, dict[str, Any]]:
    """Accept the production notification record and lightweight test fakes."""
    if isinstance(notification, dict):
        method = str(notification.get("method", "") or "")
        params = notification.get("params", {})
    else:
        method = str(getattr(notification, "method", "") or "")
        params = getattr(notification, "params", {})
    return method, params if isinstance(params, dict) else {}


def _thread_id_from_result(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    thread = result.get("thread")
    if not isinstance(thread, dict):
        return ""
    return str(thread.get("id", "") or "").strip()


def _safe_error(exc: BaseException, *, max_chars: int = 500) -> str:
    text = " ".join(str(exc).split())
    return (text or type(exc).__name__)[:max_chars]


def _handoff_text(item: dict[str, Any]) -> str:
    direct = str(
        item.get("input_transcript", "")
        or item.get("inputTranscript", "")
        or ""
    ).strip()
    if direct:
        return direct
    active = item.get("active_transcript", item.get("activeTranscript", []))
    if not isinstance(active, list):
        return ""
    for entry in reversed(active):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("role", "") or "").strip().lower() != "user":
            continue
        text = str(entry.get("text", "") or "").strip()
        if text:
            return text
    return ""


async def _close_subscription(subscription: Any) -> None:
    close = getattr(subscription, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _consume_cleanup_task(task: asyncio.Task[Any]) -> None:
    with suppress(asyncio.CancelledError, Exception):
        task.result()


async def _bounded_remote_call(
    client: Any,
    method_name: str,
    thread_id: str,
    *args: str,
) -> bool:
    """Run one cleanup/control RPC without letting it hold session teardown."""
    method = getattr(client, method_name, None)
    if not callable(method):
        log.warning("Codex app-server client lacks required %s cleanup", method_name)
        return False
    task = asyncio.create_task(method(thread_id, *args))
    done, _pending = await asyncio.wait(
        {task}, timeout=_REMOTE_CLEANUP_TIMEOUT_S
    )
    if not done:
        task.cancel()
        # Reap the canceled coroutine within a small second bound so it cannot
        # leak an unobserved exception into loop shutdown. A cleanup RPC that
        # suppresses cancellation still cannot hold session teardown open.
        reaped, _still_pending = await asyncio.wait({task}, timeout=0.1)
        if reaped:
            _consume_cleanup_task(task)
        else:
            task.add_done_callback(_consume_cleanup_task)
        log.warning(
            "Codex subscription %s timed out after %.1fs",
            method_name,
            _REMOTE_CLEANUP_TIMEOUT_S,
        )
        return False
    try:
        task.result()
    except Exception:  # noqa: BLE001 - cleanup continues to the next boundary
        log.warning("Codex subscription %s failed", method_name, exc_info=True)
        return False
    return True


async def _poison_client(client: Any) -> None:
    method = getattr(client, "poison", None) or getattr(client, "close", None)
    if not callable(method):
        log.error("Codex app-server cleanup failed and the client cannot be poisoned")
        return
    try:
        result = method()
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 - containment attempt is logged explicitly
        log.error("Codex app-server poison failed", exc_info=True)


async def _cleanup_remote_thread(client: Any, thread_id: str) -> bool:
    """Stop realtime and unload its ephemeral app-server thread, in order."""
    if not thread_id:
        return True
    stopped = await _bounded_remote_call(client, "realtime_stop", thread_id)
    unsubscribed = await _bounded_remote_call(
        client,
        "thread_unsubscribe",
        thread_id,
    )
    clean = stopped and unsubscribed
    if not clean:
        await _poison_client(client)
    return clean


class _CodexSubscriptionRealtimeSession:
    supports_tool_updates = False
    creates_responses_automatically = True
    isolates_response_generations = True
    rebuild_on_transport_death = False
    direct_speech_is_authoritative = True

    def __init__(
        self,
        *,
        client: Any,
        subscription: Any,
        thread_id: str,
        answer_sdp: str,
        audio_endpoint: Any = None,
        input_transcriber: Any = None,
        language: str = "en",
    ) -> None:
        self._client = client
        self._subscription = subscription
        self._thread_id = thread_id
        self.session_id = thread_id
        self.answer_sdp = answer_sdp
        self.realtime_version = ""
        # Owns the media path: ChatGPT-Live carries audio ONLY over WebRTC.
        self._audio_endpoint = audio_endpoint
        # ChatGPT-Live never transcribes the USER, so Jarvis does it locally.
        # Without this the bar stays blank and every transcript-driven
        # integration (delegate, wiki, project files, hang-up phrase) is deaf.
        self._input_transcriber = input_transcriber
        self._closed = False
        self._last_input_item_id = ""
        self._assistant_delta_text = ""
        # The far end's own guess at what the user said. Kept as a live preview
        # and promoted to the recorded turn only if the local recognizer fails.
        self._server_user_preview = ""
        self._language = str(language or "en").strip().lower()
        self._active_codex_turn_id = ""
        self._handoff_interrupt_pending = False
        # Last persona/context text actually delivered, so a re-issued
        # identical one is not sent again mid-call.
        self._delivered_context = ""

    async def _interrupt_active_codex_turn(self) -> None:
        turn_id = self._active_codex_turn_id
        if not turn_id:
            return
        self._active_codex_turn_id = ""
        self._handoff_interrupt_pending = False
        method = getattr(self._client, "turn_interrupt", None)
        if not callable(method):
            log.warning("Codex app-server client cannot interrupt handoff turn")
            return
        try:
            await asyncio.wait_for(
                method(self._thread_id, turn_id),
                timeout=_TURN_INTERRUPT_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 - containment flags remain the backstop
            log.warning(
                "Codex subscription handoff turn interrupt failed",
                exc_info=True,
            )

    async def send_audio(self, chunk: Any) -> None:
        if self._closed:
            raise RuntimeError("Codex subscription realtime session is closed")
        sample_rate = int(getattr(chunk, "sample_rate", 0) or 0)
        channels = int(getattr(chunk, "channels", 1) or 1)
        if sample_rate <= 0:
            raise ValueError("Codex subscription realtime audio needs a sample rate")
        if channels != 1:
            raise ValueError("Codex subscription realtime accepts mono PCM only")
        pcm = bytes(getattr(chunk, "pcm", b"") or b"")
        if not pcm:
            return
        if self._audio_endpoint is None:
            raise RuntimeError(
                "Codex subscription realtime has no media path for microphone audio"
            )
        # ChatGPT-Live (v3) has NO audio client event: the sideband append that
        # the retired v1 protocol used is rejected outright ("Invalid value:
        # 'input_audio.append'"). Microphone audio rides the media track.
        self._audio_endpoint.send_pcm(pcm, sample_rate)
        if self._input_transcriber is not None:
            self._input_transcriber.feed(pcm, sample_rate)

    def _server_user_transcript_is_plausible(self) -> bool:
        """Was there microphone energy behind this server-side transcript?

        ChatGPT-Live transcribes the user itself, and like every recognizer it
        invents caption-style text on silence and on the echo of its own voice
        — observed live as "[exhale]", "blurred gray", "a_lee pixelated image",
        each recorded as something the user had said and answered in earnest.
        The test is word-agnostic (AP-27): the local endpointer already knows,
        from audio energy alone, whether anybody was speaking. Content rules
        cannot do this job — a hallucination is spelled like a sentence.
        """
        transcriber = self._input_transcriber
        if transcriber is None:
            # No endpointer, so no evidence either way; a deaf bar would be a
            # worse failure than an occasional invented line.
            return True
        checker = getattr(transcriber, "speech_recently", None)
        if not callable(checker):
            return True
        return bool(checker())

    async def receive(self) -> AsyncIterator[_ProviderEvent]:
        # Stay below app-server's bounded subscription queue so a stalled
        # consumer propagates backpressure instead of silently buffering an
        # unbounded amount of audio in a second layer.
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(
            maxsize=_NORMALIZATION_QUEUE_MAX
        )
        timer_tasks: set[asyncio.Task[None]] = set()
        completion_task: asyncio.Task[None] | None = None
        completion_generation = 0
        completion_emitted = False
        stream_ended = False
        version = self.realtime_version.lower().removeprefix("v")
        authoritative_done = version == "3"

        async def _pump_notifications() -> None:
            try:
                async for notification in self._subscription:
                    await queue.put(("notification", notification))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - normalized by consumer
                await queue.put(("stream_error", exc))
            else:
                await queue.put(("stream_end", None))

        async def _pump_local_input() -> None:
            """Feed locally recognized USER speech into the same event stream.

            Other providers deliver these; ChatGPT-Live does not. Emitting
            them here is what makes the bar show live text, the indicators
            move, and every transcript-driven Jarvis integration work.
            """
            transcriber = self._input_transcriber
            if transcriber is None:
                return
            try:
                while True:
                    event = await transcriber.next_event()
                    if event is None:
                        return
                    await queue.put(("local_input", event))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a deaf bar must not kill the call
                log.warning(
                    "Local input transcription stream ended early", exc_info=True
                )

        async def _pump_media_audio() -> None:
            """Feed decoded RTP audio into the same normalized event stream.

            This is the ONLY audio source on ChatGPT-Live: the sideband
            ``outputAudio`` notification the retired v1 protocol used is never
            emitted (verified live — 956 RTP frames, zero sideband deltas).
            """
            endpoint = self._audio_endpoint
            if endpoint is None:
                return
            try:
                while True:
                    pcm = await endpoint.next_output_pcm()
                    if pcm is None:
                        return
                    await queue.put(("media_audio", pcm))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - normalized by consumer
                await queue.put(("stream_error", exc))

        async def _emit_after_idle(generation: int) -> None:
            await asyncio.sleep(_OUTPUT_QUIESCENCE_S)
            await queue.put(("completion", generation))

        def _cancel_completion() -> None:
            nonlocal completion_task, completion_generation
            completion_generation += 1
            if completion_task is not None and not completion_task.done():
                completion_task.cancel()
            completion_task = None

        def _arm_completion() -> None:
            nonlocal completion_task, completion_generation
            _cancel_completion()
            generation = completion_generation
            completion_task = asyncio.create_task(
                _emit_after_idle(generation),
                name=f"codex-realtime-quiescence-{self._thread_id}",
            )
            timer_tasks.add(completion_task)
            completion_task.add_done_callback(timer_tasks.discard)

        pump_task = asyncio.create_task(
            _pump_notifications(),
            name=f"codex-realtime-notifications-{self._thread_id}",
        )
        media_task = asyncio.create_task(
            _pump_media_audio(),
            name=f"codex-realtime-media-{self._thread_id}",
        )
        input_task = asyncio.create_task(
            _pump_local_input(),
            name=f"codex-realtime-local-input-{self._thread_id}",
        )
        try:
            while True:
                queue_kind, payload = await queue.get()
                if queue_kind == "local_input":
                    if payload.kind == "speech_started":
                        _cancel_completion()
                        completion_emitted = False
                        self._assistant_delta_text = ""
                        self._server_user_preview = ""
                        yield _ProviderEvent(type="speech_started")
                    elif payload.kind == "transcript_failed":
                        # The local recognizer could not deliver this
                        # utterance. A turn the user really spoke must not
                        # vanish, so the far end's preview is promoted — it
                        # covers the same audio and passed the same energy
                        # gate. Silence here would strand the whole turn.
                        preview = self._server_user_preview
                        self._server_user_preview = ""
                        if preview:
                            log.info(
                                "Local recognizer delivered nothing; using the "
                                "provider's own transcript for this turn"
                            )
                            yield _ProviderEvent(
                                type="input_transcript",
                                text=preview,
                                is_final=True,
                                item_id=self._last_input_item_id or None,
                            )
                    else:
                        self._server_user_preview = ""
                        yield _ProviderEvent(
                            type="input_transcript",
                            text=payload.text,
                            is_final=payload.is_final,
                        )
                    continue
                if queue_kind == "media_audio":
                    # Real provider audio keeps the turn alive exactly like the
                    # old sideband deltas did (quiescence timer, Orb state,
                    # transcript-gated playback).
                    if completion_task is not None:
                        _arm_completion()
                    yield _ProviderEvent(
                        type="audio_delta",
                        audio=_PcmChunk(
                            pcm=payload,
                            sample_rate=_OUTPUT_RATE,
                            channels=1,
                        ),
                    )
                    continue
                if queue_kind == "completion":
                    if payload != completion_generation:
                        continue
                    completion_task = None
                    if not completion_emitted:
                        completion_emitted = True
                        yield _ProviderEvent(type="turn_complete")
                    if stream_ended:
                        if not self._closed:
                            yield _ProviderEvent(
                                type="error",
                                error=(
                                    "Codex app-server notification stream ended "
                                    "unexpectedly"
                                ),
                            )
                        return
                    continue

                if queue_kind == "stream_error":
                    _cancel_completion()
                    exc = payload
                    yield _ProviderEvent(
                        type="error",
                        error=(
                            "Codex app-server notification stream failed: "
                            f"{type(exc).__name__}: {_safe_error(exc)}"
                        ),
                    )
                    return

                if queue_kind == "stream_end":
                    stream_ended = True
                    if completion_task is not None:
                        continue
                    if not self._closed:
                        yield _ProviderEvent(
                            type="error",
                            error=(
                                "Codex app-server notification stream ended "
                                "unexpectedly"
                            ),
                        )
                    return

                notification = payload
                method, params = _notification_parts(notification)
                event_thread = str(params.get("threadId", "") or "")
                if event_thread and event_thread != self._thread_id:
                    continue

                if method == "turn/started":
                    turn = params.get("turn")
                    if isinstance(turn, dict):
                        self._active_codex_turn_id = str(
                            turn.get("id", "") or ""
                        ).strip()
                    if self._handoff_interrupt_pending:
                        await self._interrupt_active_codex_turn()
                    continue

                if method in {"turn/completed", "turn/failed"}:
                    turn = params.get("turn")
                    completed_id = (
                        str(turn.get("id", "") or "").strip()
                        if isinstance(turn, dict)
                        else ""
                    )
                    if not completed_id or completed_id == self._active_codex_turn_id:
                        self._active_codex_turn_id = ""
                    continue

                if method == "thread/realtime/started":
                    # The start RPC result is empty in exact-0.146; the live
                    # protocol version exists only on this notification.
                    self.realtime_version = str(params.get("version", "") or "").strip()
                    authoritative_done = (
                        self.realtime_version.lower().removeprefix("v") == "3"
                    )
                    continue

                if method == "thread/realtime/transcript/delta":
                    role = str(params.get("role", "") or "").lower()
                    delta = str(params.get("delta", "") or "")
                    if not delta:
                        continue
                    if role == "user":
                        if not self._server_user_transcript_is_plausible():
                            log.debug(
                                "Dropping a server user transcript delta that "
                                "no microphone energy backs"
                            )
                            continue
                        _cancel_completion()
                        completion_emitted = False
                        yield _ProviderEvent(
                            type="input_transcript",
                            text=delta,
                            is_final=False,
                            item_id=self._last_input_item_id or None,
                        )
                    elif role == "assistant":
                        # A later transcript delta proves the previous
                        # transcript-part ``done`` was not a turn boundary.
                        _cancel_completion()
                        completion_emitted = False
                        self._assistant_delta_text += delta
                        yield _ProviderEvent(
                            type="output_transcript_delta",
                            text=delta,
                            is_final=False,
                        )
                    continue

                if method == "thread/realtime/transcript/done":
                    role = str(params.get("role", "") or "").lower()
                    text = str(params.get("text", "") or "")
                    if role == "user":
                        if not self._server_user_transcript_is_plausible():
                            log.info(
                                "Ignoring a server user transcript that no "
                                "microphone energy backs (%r)",
                                text[:80],
                            )
                            continue
                        _cancel_completion()
                        completion_emitted = False
                        # The local recognizer owns the FINAL text: it is the
                        # one the user configured, with their dictionary and
                        # bias prompt, and it is what every other Jarvis
                        # feature hears. This stays a live preview unless that
                        # recognizer reports it could not deliver.
                        local_owns_final = self._input_transcriber is not None
                        if local_owns_final:
                            self._server_user_preview = text
                        yield _ProviderEvent(
                            type="input_transcript",
                            text=text,
                            is_final=not local_owns_final,
                            item_id=self._last_input_item_id or None,
                        )
                    elif role == "assistant":
                        # Emit only a missing suffix so transcript consumers do
                        # not see the final text twice.
                        suffix = text
                        if text.startswith(self._assistant_delta_text):
                            suffix = text[len(self._assistant_delta_text) :]
                        if suffix:
                            yield _ProviderEvent(
                                type="output_transcript_delta",
                                text=suffix,
                                is_final=True,
                            )
                        self._assistant_delta_text = ""
                        if authoritative_done:
                            _cancel_completion()
                            if not completion_emitted:
                                completion_emitted = True
                                yield _ProviderEvent(type="turn_complete")
                        elif not completion_emitted:
                            _arm_completion()
                    continue

                if method == "thread/realtime/outputAudio/delta":
                    if self._audio_endpoint is not None:
                        # ChatGPT-Live never emits this; if a future protocol
                        # revision mirrors audio again, playing BOTH sources
                        # would double every word. The media track wins.
                        continue
                    # Legacy sideband path (retired v1 protocol).
                    audio = params.get("audio")
                    if not isinstance(audio, dict):
                        _cancel_completion()
                        yield _ProviderEvent(
                            type="error",
                            error="Codex app-server emitted an invalid audio payload",
                        )
                        return
                    try:
                        pcm = base64.b64decode(
                            str(audio.get("data", "") or ""), validate=True
                        )
                        sample_rate = int(audio.get("sampleRate", 0) or 0)
                        channels = int(audio.get("numChannels", 1) or 1)
                    except (ValueError, TypeError) as exc:
                        _cancel_completion()
                        yield _ProviderEvent(
                            type="error",
                            error=(
                                "Codex app-server emitted malformed realtime audio: "
                                f"{_safe_error(exc)}"
                            ),
                        )
                        return
                    if not pcm or sample_rate <= 0 or channels != 1:
                        _cancel_completion()
                        yield _ProviderEvent(
                            type="error",
                            error=(
                                "Codex app-server emitted unsupported realtime audio "
                                f"(sample_rate={sample_rate}, channels={channels})"
                            ),
                        )
                        return
                    if completion_task is not None:
                        _arm_completion()
                    yield _ProviderEvent(
                        type="audio_delta",
                        audio=_PcmChunk(
                            pcm=pcm,
                            sample_rate=sample_rate,
                            channels=channels,
                        ),
                    )
                    continue

                if method == "thread/realtime/itemAdded":
                    item = params.get("item")
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("type", "") or "")
                    if item_type == "input_audio_buffer.speech_started":
                        _cancel_completion()
                        completion_emitted = False
                        self._assistant_delta_text = ""
                        self._last_input_item_id = str(
                            item.get("item_id", "") or ""
                        )
                        yield _ProviderEvent(
                            type="speech_started",
                            item_id=self._last_input_item_id or None,
                        )
                    elif item_type == "response.cancelled":
                        _cancel_completion()
                        completion_emitted = False
                        self._assistant_delta_text = ""
                        yield _ProviderEvent(type="interrupted")
                    elif item_type == "handoff_request":
                        # Exact Codex 0.146 routes this item into a normal Codex
                        # turn even with clientManagedHandoffs enabled; that flag
                        # suppresses only automatic output delivery. Interrupt
                        # the turn as soon as its id is visible and hand control
                        # to Jarvis's deterministic supervisor.
                        _cancel_completion()
                        self._handoff_interrupt_pending = True
                        direct_turn_id = str(
                            item.get("turn_id", "")
                            or item.get("turnId", "")
                            or ""
                        ).strip()
                        if direct_turn_id:
                            self._active_codex_turn_id = direct_turn_id
                        await self._interrupt_active_codex_turn()
                        yield _ProviderEvent(
                            type="handoff_requested",
                            text=_handoff_text(item) or None,
                            handoff_id=str(
                                item.get("handoff_id", "")
                                or item.get("handoffId", "")
                                or ""
                            ).strip()
                            or None,
                            provider_turn_id=(direct_turn_id or None),
                        )
                    continue

                if method == "thread/realtime/error":
                    _cancel_completion()
                    message = str(params.get("message", "") or "").strip()
                    yield _ProviderEvent(
                        type="error",
                        error=message or "Codex subscription realtime transport failed",
                    )
                    return

                if method == "thread/realtime/closed":
                    _cancel_completion()
                    if self._closed:
                        return
                    reason = str(params.get("reason", "") or "").strip()
                    yield _ProviderEvent(
                        type="error",
                        error=(
                            "Codex subscription realtime transport closed"
                            + (f": {reason}" if reason else "")
                        ),
                    )
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize adapter failures
            yield _ProviderEvent(
                type="error",
                error=(
                    "Codex app-server notification handling failed: "
                    f"{type(exc).__name__}: {_safe_error(exc)}"
                ),
            )
        finally:
            _cancel_completion()
            for pump in (pump_task, media_task, input_task):
                if not pump.done():
                    pump.cancel()
            for task in tuple(timer_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                pump_task,
                media_task,
                input_task,
                *tuple(timer_tasks),
                return_exceptions=True,
            )

    async def update_session(
        self,
        *,
        instructions: str | None = None,
        language: str | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> None:
        # Tools stay unsupported by design: the Jarvis supervisor remains the
        # only action boundary. INSTRUCTIONS, however, are the assistant's
        # identity and project knowledge — dropping them was why the voice
        # knew nothing about its own project. ChatGPT-Live accepts developer
        # context (session.context.append), so a changed persona is delivered
        # mid-call instead of being discarded.
        del tools
        await self._deliver_context(instructions)
        normalized = str(language or "").strip().lower()
        if normalized not in _LANGUAGE_UPDATE_TEXT or normalized == self._language:
            return
        await self._client.realtime_append_text(
            self._thread_id,
            _LANGUAGE_UPDATE_TEXT[normalized],
            role="developer",
        )
        self._language = normalized

    async def _deliver_context(self, instructions: str | None) -> None:
        """Give the model Jarvis's own persona, capabilities and context.

        ChatGPT-Live has no session-instructions field a client may set, but
        it does accept developer context — which is how the assistant learns
        who it is and what this project is. Delivered once per distinct text
        so a re-issued identical persona costs nothing.
        """
        text = str(instructions or "").strip()
        if not text or text == self._delivered_context:
            return
        try:
            await self._client.realtime_append_text(
                self._thread_id, text, role="developer"
            )
        except Exception:  # noqa: BLE001 - a mute persona must not kill the call
            log.warning(
                "Codex subscription realtime could not deliver its context; "
                "the assistant continues without the persona",
                exc_info=True,
            )
            return
        self._delivered_context = text

    async def request_response(self, *, required_tool: str | None = None) -> None:
        # The upstream VAD creates responses automatically.  A required tool
        # stays with the Jarvis delegate rather than becoming a direct Codex
        # app-server action.
        del required_tool

    async def send_text(self, text: str) -> None:
        await self._client.realtime_append_text(
            self._thread_id, str(text), role="developer"
        )

    async def send_speech(self, text: str) -> None:
        """Queue trusted verbatim speech without starting a Codex model turn."""
        await self._client.realtime_append_speech(self._thread_id, str(text))

    async def truncate(self, audio_end_ms: int) -> None:
        del audio_end_ms

    async def interrupt(self) -> None:
        await self._interrupt_active_codex_turn()

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        del call_id, name, result
        raise RuntimeError("Codex subscription realtime does not execute tools directly")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await _cleanup_remote_thread(self._client, self._thread_id)
        finally:
            try:
                await _close_subscription(self._subscription)
            finally:
                try:
                    if self._input_transcriber is not None:
                        await self._input_transcriber.close()
                finally:
                    if self._audio_endpoint is not None:
                        await self._audio_endpoint.close()


class CodexSubscriptionRealtimeProvider:
    """Structural provider entry point backed by ChatGPT-managed Codex auth."""

    name = "codex-subscription-realtime"
    credential_family = "openai-chatgpt-subscription"
    supports_realtime = True
    implicit_usage_fallback_allowed = False
    # Jarvis owns the WebRTC peer in-process now, so the UI no longer has to
    # broker a signalling offer (and a headless host needs no browser at all).
    requires_webrtc_offer = False
    # Declared handshake need (capability, AP-21): a COLD start spawns
    # app-server, verifies the live account, re-audits config, and negotiates
    # WebRTC — measured 15-25s on a mid-range desktop. The shared 12s ceiling
    # beheaded every cold call into a pipeline fallback (log 2026-08-01).
    handshake_budget_s = 45.0
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE
    credential_candidates: tuple[tuple[str, str | None], ...] = ()

    @classmethod
    async def verify_activation(cls, cfg: Any) -> None:
        """Run the provider's authoritative account/config activation gate.

        When the gate itself started app-server just to judge the account, it
        also cleans up: leaving the transport reservation and an idle Codex
        child running would make the card's reconnect button answer
        "disconnect active subscription voice" after every activation. A
        client that was ALREADY ready is carrying a live call — verifying is
        harmless, closing it would cut the call mid-sentence.
        """
        app_server_module = importlib.import_module("jarvis.codex_app_server")
        codex_cfg = getattr(cfg, "codex", None)
        binary_path = str(getattr(codex_cfg, "binary_path", "") or "").strip() or None
        client = app_server_module.get_shared_codex_app_server(binary_path)
        was_ready = bool(getattr(client, "ready", False))
        try:
            await client.require_chatgpt_login()
        finally:
            # A cold call may have raced this gate onto the same client while
            # we awaited: active thread subscriptions mean someone else is
            # using it now — closing would cut their session mid-setup.
            if not was_ready and not getattr(client, "_subscriptions", None):
                await client.close()

    def __init__(
        self,
        *,
        client: Any = None,
        audio_endpoint_factory: Any = None,
        input_transcriber_factory: Any = None,
        binary_path: str | None = None,
    ) -> None:
        self._client = client
        # Injected in tests; production builds the in-process WebRTC endpoint.
        self._audio_endpoint_factory = audio_endpoint_factory
        self._input_transcriber_factory = input_transcriber_factory
        self._binary_path = str(binary_path or "").strip() or None

    @classmethod
    def from_runtime_config(cls, cfg: Any) -> CodexSubscriptionRealtimeProvider:
        binary_path = str(
            getattr(getattr(cfg, "codex", None), "binary_path", "") or ""
        ).strip()
        return cls(binary_path=binary_path or None)

    @classmethod
    def external_login_ready(cls, cfg: Any = None) -> bool:
        """Return the dedicated-profile snapshot; app-server verifies it live."""
        try:
            # Dynamic import preserves the plugin boundary: discovery imports
            # no ``jarvis.*`` module until this explicit capability probe.
            app_server_module = importlib.import_module("jarvis.codex_app_server")
            binary_path = str(
                getattr(getattr(cfg, "codex", None), "binary_path", "") or ""
            ).strip()
            if app_server_module.codex_subscription_activation_block():
                # The live account gate refused this login permanently;
                # advertising the provider as available would build sessions
                # that can never start (and mislead GET /voice-mode).
                return False
            status = app_server_module.codex_subscription_auth_snapshot(
                binary_path or None
            )
            if getattr(status, "reason_code", "") == "busy":
                # Transiently unknown, not "no login": failing closed here
                # flips voice-mode availability (and its 400) on a healthy
                # install. Fail open — opening a session runs the
                # authoritative live account verification anyway, and a
                # genuinely missing login still stops the call honestly.
                return True
            return bool(
                getattr(status, "available", False)
                and getattr(status, "chatgpt_authenticated", False)
            )
        except Exception:  # noqa: BLE001 - discovery degrades to other providers
            log.warning("Codex ChatGPT login status probe failed", exc_info=True)
            return False

    async def can_open_duplex_session(self) -> bool:
        # The factory already performed the bounded external-login probe. The
        # authoritative handshake below starts app-server, which independently
        # verifies the live ChatGPT account. Repeating the CLI probe here costs
        # seconds on a cold call without strengthening the trust boundary.
        return True

    async def open_session(self, cfg: Any) -> _CodexSubscriptionRealtimeSession:
        """Open a session, preferring the fast host-only media path.

        Host candidates alone connect on an ordinary network and cost no
        gathering time; STUN costs a fixed ~5 s wait that used to sit in front
        of every call and swallow the user's first sentence. A network that
        genuinely needs a reflexive candidate gets one on the retry.
        """
        transport_module = importlib.import_module(
            "jarvis.realtime.webrtc_transport"
        )
        attempts: tuple[Any, ...] = (None, transport_module.stun_ice_servers)
        last_error: BaseException | None = None
        for index, ice_factory in enumerate(attempts):
            try:
                return await self._open_session_once(
                    cfg, None if ice_factory is None else ice_factory()
                )
            except transport_module.WebRtcMediaPathUnavailable as exc:
                last_error = exc
                if index + 1 < len(attempts):
                    log.warning(
                        "Realtime media path did not connect on host candidates "
                        "(%s); retrying with a STUN server",
                        exc,
                    )
                    continue
                raise
        raise RuntimeError(  # pragma: no cover - the loop always returns or raises
            "Codex subscription realtime could not open a media path"
        ) from last_error

    def _build_input_transcriber(self) -> Any:
        """Local user-speech recognition, or ``None`` when unavailable.

        ChatGPT-Live sends assistant transcripts only, so without this the
        provider is deaf to Jarvis: the model talks, but the bar, the
        indicators and every transcript-driven integration stay idle.
        """
        if self._input_transcriber_factory is not None:
            return self._input_transcriber_factory()
        try:
            module = importlib.import_module("jarvis.realtime.input_transcription")
            return module.LocalInputTranscriber(sample_rate=_INPUT_RATE)
        except Exception:  # noqa: BLE001 - the call still works, just deaf
            log.warning(
                "Local input transcription could not be started; the voice "
                "answers but Jarvis-side transcript features stay idle",
                exc_info=True,
            )
            return None

    async def _open_session_once(
        self, cfg: Any, ice_servers: Any
    ) -> _CodexSubscriptionRealtimeSession:
        # Jarvis owns the media path in-process. The UI could only ever broker
        # a signalling-shaped offer (no microphone), which ChatGPT-Live cannot
        # use: on v3 the audio IS the WebRTC track.
        audio_endpoint: Any = None
        if self._audio_endpoint_factory is not None:
            audio_endpoint = self._audio_endpoint_factory(ice_servers)
        else:
            transport_module = importlib.import_module(
                "jarvis.realtime.webrtc_transport"
            )
            audio_endpoint = transport_module.RealtimeWebRtcAudioEndpoint(
                ice_servers
            )
        offer_sdp = await audio_endpoint.create_offer()

        client = self._client
        if client is None:
            app_server_module = importlib.import_module("jarvis.codex_app_server")
            client = app_server_module.get_shared_codex_app_server(self._binary_path)

        subscription: Any = None
        thread_id = ""
        try:
            # ``thread_start`` lazily calls app-server ``ensure_started``. That
            # performs the authoritative capability probe plus live
            # ``account/read`` verification before accepting this thread.
            thread_result = await client.thread_start(
                base_instructions=_THREAD_BASE_INSTRUCTIONS,
                developer_instructions=_THREAD_DEVELOPER_INSTRUCTIONS,
                ephemeral=True,
            )
            thread_id = _thread_id_from_result(thread_result)
            if not thread_id:
                raise RuntimeError("Codex app-server did not return a thread id")
            subscription = client.subscribe(thread_id)

            configured_model = str(getattr(cfg, "model", "") or "").strip()
            if configured_model and configured_model not in {
                "auto",
                _LEGACY_V1_MODEL,
            }:
                # v3 rejects a client model outright; an unknown leftover pin
                # must not brick the call — the server chooses the model.
                log.info(
                    "Codex subscription realtime ignores the configured model "
                    "%r: ChatGPT-Live (v3) selects the model server-side",
                    configured_model,
                )
            voice = (
                str(getattr(cfg, "voice", "") or "").strip().lower()
                or _DEFAULT_VOICE
            )
            if voice not in _V3_VOICES:
                raise RuntimeError(
                    "Codex subscription realtime has an unsupported voice configured"
                )
            start = await client.realtime_start(
                thread_id,
                output_modality="audio",
                offer_sdp=offer_sdp,
                prompt="",
                # v3 (ChatGPT-Live): the server chooses the model; sending a
                # client model is rejected with "Field `session.model` is not
                # allowed" (verified live 2026-08-01). The dead v1 protocol
                # answered every start with 403 since the ChatGPT-Live launch.
                model=None,
                voice=voice,
                version="v3",
                include_startup_context=False,
                client_managed_handoffs=True,
            )
            answer_sdp = str(getattr(start, "answer_sdp", "") or "").strip()
            if not answer_sdp:
                raise RuntimeError("Codex app-server did not return a WebRTC answer SDP")
            await audio_endpoint.apply_answer(answer_sdp)
            # Fail here rather than mid-call: without a live media path the
            # session would look connected and stay mute in both directions.
            await audio_endpoint.wait_connected()
            session = _CodexSubscriptionRealtimeSession(
                client=client,
                subscription=subscription,
                thread_id=thread_id,
                answer_sdp=answer_sdp,
                audio_endpoint=audio_endpoint,
                input_transcriber=self._build_input_transcriber(),
                language=str(getattr(cfg, "language", "en") or "en"),
            )
            # Identity FIRST: the model must know who it is and what this
            # project is before the user's first word arrives.
            await session._deliver_context(getattr(cfg, "instructions", ""))
            return session
        except BaseException:
            try:
                await _cleanup_remote_thread(client, thread_id)
            finally:
                try:
                    if subscription is not None:
                        await _close_subscription(subscription)
                finally:
                    await audio_endpoint.close()
            raise


__all__ = ["CodexSubscriptionRealtimeProvider"]
