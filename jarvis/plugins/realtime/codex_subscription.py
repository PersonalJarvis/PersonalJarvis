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
_V1_MODEL = "gpt-realtime-1.5"
_V1_DEFAULT_VOICE = "cove"
_V1_VOICES = frozenset(
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
    "This ephemeral thread exists only to carry a realtime voice transport. "
    "Never use tools, inspect files, access the network, or perform actions. "
    "Never answer a realtime handoff as a Codex agent; the client-owned Jarvis "
    "supervisor handles every handoff."
)
_THREAD_DEVELOPER_INSTRUCTIONS = (
    "Transport-only boundary: do not call tools, shell commands, applications, "
    "plugins, skills, web search, MCP servers, or other agents. Do not read or "
    "write the filesystem. Yield all realtime handoffs to the client."
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
        offer_lease: Any = None,
        language: str = "en",
    ) -> None:
        self._client = client
        self._subscription = subscription
        self._thread_id = thread_id
        self.session_id = thread_id
        self.answer_sdp = answer_sdp
        self.realtime_version = ""
        self._offer_lease = offer_lease
        self._closed = False
        self._last_input_item_id = ""
        self._assistant_delta_text = ""
        self._language = str(language or "en").strip().lower()
        self._active_codex_turn_id = ""
        self._handoff_interrupt_pending = False

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
        await self._client.realtime_append_audio(
            self._thread_id,
            data=base64.b64encode(pcm).decode("ascii"),
            sample_rate=sample_rate,
            num_channels=channels,
            samples_per_channel=len(pcm) // 2,
        )

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
        try:
            while True:
                queue_kind, payload = await queue.get()
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
                        _cancel_completion()
                        completion_emitted = False
                        yield _ProviderEvent(
                            type="input_transcript",
                            text=text,
                            is_final=True,
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
                    # The sideband PCM is authoritative for Jarvis playback:
                    # it passes through the existing scrub, barge-in, and
                    # persistence gates. The browser peer intentionally does
                    # not play the duplicate RTP track.
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
            if not pump_task.done():
                pump_task.cancel()
            for task in tuple(timer_tasks):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                pump_task,
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
        # Dynamic tools/instructions are intentionally unsupported; the shared
        # Jarvis supervisor remains the only action boundary. The exact API
        # does support developer-role appendText, which is the safe per-turn
        # boundary for the canonical de/en/es resolver.
        del instructions, tools
        normalized = str(language or "").strip().lower()
        if normalized not in _LANGUAGE_UPDATE_TEXT or normalized == self._language:
            return
        await self._client.realtime_append_text(
            self._thread_id,
            _LANGUAGE_UPDATE_TEXT[normalized],
            role="developer",
        )
        self._language = normalized

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
                if self._offer_lease is not None:
                    await self._offer_lease.release()


class CodexSubscriptionRealtimeProvider:
    """Structural provider entry point backed by ChatGPT-managed Codex auth."""

    name = "codex-subscription-realtime"
    credential_family = "openai-chatgpt-subscription"
    supports_realtime = True
    implicit_usage_fallback_allowed = False

    @classmethod
    async def verify_activation(cls, cfg: Any) -> None:
        """Run the provider's authoritative account/config activation gate.

        The gate starts app-server just to judge the account, so it must also
        clean up after itself: leaving the transport reservation and an idle
        Codex child running would make the card's reconnect button answer
        "disconnect active subscription voice" after every activation. A call
        that starts later simply re-runs ``ensure_started``.
        """
        app_server_module = importlib.import_module("jarvis.codex_app_server")
        codex_cfg = getattr(cfg, "codex", None)
        binary_path = str(getattr(codex_cfg, "binary_path", "") or "").strip() or None
        client = app_server_module.get_shared_codex_app_server(binary_path)
        try:
            await client.require_chatgpt_login()
        finally:
            await client.close()
    requires_webrtc_offer = True
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE
    credential_candidates: tuple[tuple[str, str | None], ...] = ()

    def __init__(
        self,
        *,
        client: Any = None,
        offer_broker: Any = None,
        binary_path: str | None = None,
    ) -> None:
        self._client = client
        self._offer_broker = offer_broker
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
        offer_sdp = str(getattr(cfg, "transport_offer_sdp", "") or "").strip()
        offer_lease: Any = None
        if not offer_sdp:
            broker = self._offer_broker
            if broker is None:
                broker_module = importlib.import_module(
                    "jarvis.realtime.offer_broker"
                )
                broker = broker_module.get_realtime_transport_offer_broker()
            offer_lease = await broker.acquire(timeout_s=_BROKER_OFFER_WAIT_S)
            if offer_lease is None:
                raise RuntimeError(
                    "Codex subscription realtime needs a connected UI WebRTC "
                    "offer, so this session cannot start"
                )
            offer_sdp = offer_lease.offer_sdp

        broker_module = importlib.import_module("jarvis.realtime.offer_broker")
        offer_sdp = broker_module.validate_webrtc_offer_sdp(offer_sdp)

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

            model = str(getattr(cfg, "model", "") or "").strip() or _V1_MODEL
            voice = (
                str(getattr(cfg, "voice", "") or "").strip().lower()
                or _V1_DEFAULT_VOICE
            )
            if model != _V1_MODEL:
                raise RuntimeError(
                    "Codex subscription realtime has an unsupported V1 model configured"
                )
            if voice not in _V1_VOICES:
                raise RuntimeError(
                    "Codex subscription realtime has an unsupported V1 voice configured"
                )
            start = await client.realtime_start(
                thread_id,
                output_modality="audio",
                offer_sdp=offer_sdp,
                prompt="",
                model=model,
                voice=voice,
                # Codex 0.146 deliberately ignores the configured voice for a
                # WebRTC start whose version is omitted. V1 is the current
                # supported protocol and preserves the selected voice.
                version="v1",
                include_startup_context=False,
                client_managed_handoffs=True,
            )
            answer_sdp = str(getattr(start, "answer_sdp", "") or "").strip()
            if not answer_sdp:
                raise RuntimeError("Codex app-server did not return a WebRTC answer SDP")
            if offer_lease is not None and not await offer_lease.answer(answer_sdp):
                raise RuntimeError("The UI WebRTC offer disconnected before Codex answered")
            return _CodexSubscriptionRealtimeSession(
                client=client,
                subscription=subscription,
                thread_id=thread_id,
                answer_sdp=answer_sdp,
                offer_lease=offer_lease,
                language=str(getattr(cfg, "language", "en") or "en"),
            )
        except BaseException:
            try:
                await _cleanup_remote_thread(client, thread_id)
            finally:
                try:
                    if subscription is not None:
                        await _close_subscription(subscription)
                finally:
                    if offer_lease is not None:
                        await offer_lease.release()
            raise


__all__ = ["CodexSubscriptionRealtimeProvider"]
