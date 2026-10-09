"""Codex subscription GPT-Live: direct WebRTC audio and Jarvis-owned control.

Wire reference: OpenClaw extensions/openai/realtime-quicksilver-{wire,events,
protocol}.ts, inspected 2026-10-02. This experimental Codex protocol is separate
from the public Live API; an OAuth credential never becomes a project API key.
No provider body, credential or transport URL belongs in errors or logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

log = logging.getLogger(__name__)
_jitter = random.SystemRandom()

CALL_URL = (
    "https://chatgpt.com/backend-api/codex/realtime/calls?intent=quicksilver&architecture=avas"
)
SIDEBAND_BASE = "wss://api.openai.com/v1/live/"
SUBSCRIPTION_MODEL = "gpt-live-1-codex"
SUBSCRIPTION_VOICES = (
    "arbor",
    "breeze",
    "cove",
    "ember",
    "juniper",
    "maple",
    "sol",
    "spruce",
    "vale",
)
_MAX_SDP_BYTES = 256 * 1024
_MAX_FRAME_BYTES = 1024 * 1024
_MAX_SEEN_EVENTS = 8192
_CALL_ID = re.compile(
    r"(?:rtc_[A-Za-z0-9_-]+|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z"
)


class SubscriptionLiveError(RuntimeError):
    """Static, safe error classification for UI recovery and no-key fallback."""

    def __init__(self, code: str, *, allocation_unconfirmed: bool = False) -> None:
        self.code = code
        self.allocation_unconfirmed = allocation_unconfirmed
        messages = {
            "authentication_required": "Reconnect your ChatGPT subscription before starting voice.",
            "access_denied": "The ChatGPT account does not allow this GPT-Live session.",
            "quota_exhausted": "The ChatGPT subscription voice allowance is exhausted.",
            "rate_limited": "ChatGPT subscription voice is temporarily rate limited.",
            "invalid_configuration": "The selected subscription voice configuration was rejected.",
            "invalid_response": "ChatGPT subscription voice returned an invalid response.",
            "connection_lost": "The ChatGPT subscription voice connection was lost.",
            "control_unavailable": "The ChatGPT subscription voice control connection failed.",
            "provider_error": "ChatGPT subscription voice is temporarily unavailable.",
        }
        super().__init__(messages.get(code, messages["provider_error"]))


def _error_code(status: int | None, error: dict | None = None) -> str:
    """Read only known classification fields; never retain provider detail."""
    if isinstance(status, str) and status.isdigit():
        status = int(status)
    code = str((error or {}).get("code", "")).lower()
    if status == 401 or code in {
        "authentication_error",
        "invalid_token",
        "token_expired",
        "invalid_api_key",
    }:
        return "authentication_required"
    if code in {"insufficient_quota", "usage_limit_reached", "credit_balance_exhausted"}:
        return "quota_exhausted"
    if status == 429 or code in {"rate_limit_exceeded", "rate_limited"}:
        return "rate_limited"
    if status == 403:
        return "access_denied"
    if status == 400:
        return "invalid_configuration"
    return "provider_error"


def _audio_only_sdp(value: str) -> bool:
    media = [line for line in value.splitlines() if line.startswith("m=")]
    return (
        value.lstrip().startswith("v=0")
        and bool(media)
        and all(line.startswith("m=audio ") for line in media)
    )


def _call_id(headers: Any) -> str:
    """Extract a bounded id, never follow a provider-supplied URL."""
    location = str(headers.get("location", ""))
    if len(location) <= 2048:
        try:
            candidates = urlsplit(location).path.split("/")
        except ValueError:
            log.debug("Subscription call location was invalid; checking the session header.")
            candidates = []
        for value in reversed(candidates):
            if len(value) <= 128 and _CALL_ID.fullmatch(value):
                return value
    value = str(headers.get("openai-session-id", "")).strip()
    if len(value) <= 128 and _CALL_ID.fullmatch(value):
        return value
    raise SubscriptionLiveError("invalid_response")


def _text_chunks(text: str, max_bytes: int = 500) -> list[str]:
    """The Codex append limit is bytes; keep multi-byte characters intact."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for character in text:
        width = len(character.encode("utf-8"))
        if current and size + width > max_bytes:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(character)
        size += width
    if current:
        chunks.append("".join(current))
    return chunks


def _session_config(session: dict) -> dict:
    """Allowlist private-wire fields rather than forwarding API configuration."""
    if session.get("model", SUBSCRIPTION_MODEL) != SUBSCRIPTION_MODEL:
        raise SubscriptionLiveError("invalid_configuration")
    voice = session.get("audio", {}).get("output", {}).get("voice", "cove")
    if voice not in SUBSCRIPTION_VOICES:
        raise SubscriptionLiveError("invalid_configuration")
    result = {
        "model": SUBSCRIPTION_MODEL,
        "instructions": str(session.get("instructions", "")),
        "audio": {"output": {"voice": voice}},
        "delegation": {"type": "client"},
    }
    history = session.get("initial_items", session.get("input", []))
    initial: list[dict] = []
    remaining = 8000
    for item in reversed(history[-16:]):
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        text = "".join(
            str(part.get("text", ""))
            for part in item.get("content", [])
            if isinstance(part, dict) and part.get("type") in {"input_text", "output_text"}
        )[:800]
        text = text.encode("utf-8")[:remaining].decode("utf-8", errors="ignore")
        if not text:
            continue
        remaining -= len(text.encode("utf-8"))
        initial.append(
            {
                "type": "message",
                "role": item["role"],
                "content": [
                    {
                        "type": "output_text" if item["role"] == "assistant" else "input_text",
                        "text": text,
                    }
                ],
            }
        )
        if remaining <= 0:
            break
    if initial:
        result["initial_items"] = list(reversed(initial))
    return result


class OpenAISubscriptionLiveConnection:
    """One subscription sideband with timed audio and host-owned controls."""

    requires_close_ack = False
    usage_billed = False
    delegation_mode = "client"

    def __init__(
        self,
        socket: Any,
        *,
        session_id: str,
        answer_sdp: str,
        request_headers: dict[str, str] | None = None,
    ) -> None:
        self.socket = socket
        self.session_id = session_id
        self.answer_sdp = answer_sdp
        self._request_headers = dict(request_headers or {})
        self._started_at = time.monotonic()
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._delegations: set[str] = set()
        self._turns: dict[str, dict] = {}
        self._closed = False
        self._close_sent = False
        self.provider_closed = False
        self._send_lock = asyncio.Lock()

    def _already_seen(self, event: dict) -> bool:
        identifier = event.get("event_id")
        if not isinstance(identifier, str) or not identifier:
            return False
        if identifier in self._seen:
            return True
        self._seen.add(identifier)
        self._seen_order.append(identifier)
        if len(self._seen_order) > _MAX_SEEN_EVENTS:
            self._seen.discard(self._seen_order.popleft())
        return False

    def _transcript(self, event: dict, role: str, *, done: bool) -> dict:
        now = max(0, int((time.monotonic() - self._started_at) * 1000))
        payload = event.get("turn" if done else "item", {})
        if not isinstance(payload, dict):
            raise SubscriptionLiveError("invalid_response")
        text = payload.get("transcript" if done else "text", "")
        if not isinstance(text, str):
            raise SubscriptionLiveError("invalid_response")
        timing = payload if done else event
        start, end = timing.get("start_ms"), timing.get("end_ms")
        timed = (
            type(start) in (int, float) and type(end) in (int, float)
            and math.isfinite(start) and math.isfinite(end) and 0 <= start < end
        )
        turn = self._turns.setdefault(
            role,
            {
                "id": str(uuid4()),
                "text": "",
                "start_ms": start if timed else now,
            },
        )
        turn["text"] = text if done else turn["text"] + text
        if len(turn["text"].encode("utf-8")) > 128 * 1024:
            raise SubscriptionLiveError("invalid_response")
        result = {
            "type": f"session.{'input' if role == 'user' else 'output'}_transcript."
            f"{'done' if done else 'delta'}",
            "event_id": str(event.get("event_id") or uuid4()),
            "role": role,
            "turn_id": turn["id"],
            "segment_id": turn["id"],
            "delta": "" if done else text,
            "transcript": turn["text"],
            "snapshot": True,
            "is_final": done,
            "start_ms": turn["start_ms"],
            "end_ms": end if timed else now,
            "timestamp_source": "source_audio" if timed else "local_receive_clock",
            **({"fragment_start_ms": start, "fragment_end_ms": end} if timed else {}),
        }
        if done:
            del self._turns[role]
        return result

    def _normalize(self, event: dict) -> dict:
        if self._already_seen(event):
            return {"type": "subscription.ignored"}
        kind = event.get("type")
        if kind in {"session.output_audio.delta", "output_audio.delta"}:
            audio = event.get("delta" if kind.startswith("session.") else "audio")
            if not isinstance(audio, str) or len(audio) > _MAX_FRAME_BYTES:
                raise SubscriptionLiveError("invalid_response")
            # Playback is explicitly negotiated as PCM by the host. RTP is
            # still the input transport but must not also drive the speaker.
            return {
                "type": "session.output_audio.delta", "delta": audio,
                "start_ms": event.get("start_ms"), "end_ms": event.get("end_ms"),
            }
        if kind in {"input_transcript.added", "output_transcript.added"}:
            return self._transcript(
                event,
                "user" if kind == "input_transcript.added" else "assistant",
                done=False,
            )
        if kind == "turn.done":
            turn = event.get("turn", {})
            role = turn.get("role") if isinstance(turn, dict) else None
            if role in {"user", "assistant"}:
                return self._transcript(event, role, done=True)
        if kind == "delegation.created":
            item = event.get("item", {})
            if (
                not isinstance(item, dict)
                or item.get("type") != "delegation"
                or item.get("target") != "client"
            ):
                raise SubscriptionLiveError("invalid_response")
            identifier = item.get("id")
            if not isinstance(identifier, str) or not identifier or len(identifier) > 256:
                raise SubscriptionLiveError("invalid_response")
            if identifier in self._delegations:
                return {"type": "subscription.ignored"}
            if len(self._delegations) >= 4096:
                raise SubscriptionLiveError("invalid_response")
            self._delegations.add(identifier)
            content = item.get("content", [])
            if not isinstance(content, list):
                raise SubscriptionLiveError("invalid_response")
            prompt = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "input_text"
            )
            return {
                "type": "session.delegation.created",
                "delegation": {"id": identifier, "type": "delegation", "target": "client"},
                "prompt": prompt,
            }
        if kind == "session.started":
            session = event.get("session", {})
            session = session if isinstance(session, dict) else {}
            return {
                "type": kind,
                "session": {
                    "id": self.session_id,
                    "expires_at": session.get("expires_at"),
                },
            }
        if kind == "session.closed":
            self.provider_closed = True
            reason = event.get("reason")
            if reason not in {
                "close_requested",
                "expired",
                "content",
                "remote_hangup",
                "connection_lost",
            }:
                reason = "remote_hangup"
            return {"type": kind, "reason": reason, "usage_finalized": False}
        if kind == "output_audio_buffer.cleared":
            return {"type": kind}
        if kind == "error":
            error = event.get("error")
            error = error if isinstance(error, dict) else {}
            status = event.get("status", error.get("status"))
            code = _error_code(status, error)
            return {
                "type": "error",
                "fatal": True,
                "error": {
                    "code": code,
                    "message": str(SubscriptionLiveError(code)),
                },
            }
        return {"type": "subscription.ignored"}

    async def receive(self) -> dict:
        try:
            raw = await self.socket.recv()
        except Exception:
            raise SubscriptionLiveError("connection_lost") from None
        if not isinstance(raw, (str, bytes)):
            raise SubscriptionLiveError("invalid_response")
        size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
        if size > _MAX_FRAME_BYTES:
            raise SubscriptionLiveError("invalid_response")
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError
        except (UnicodeDecodeError, ValueError):
            raise SubscriptionLiveError("invalid_response") from None
        return self._normalize(event)

    async def send(self, event: dict) -> None:
        """Translate only host context/close; provider reasoning never runs here."""
        kind = event.get("type")
        if kind == "session.close":
            frames = [{"type": "session.close"}]
        elif kind in {"session.commentary.append", "session.thinking.append"}:
            text = event.get("content", "")
            if not isinstance(text, str):
                raise ValueError("Subscription Live context must be text")
            identifier = event.get("delegation_id")
            if identifier is not None and identifier not in self._delegations:
                raise ValueError("Unknown subscription Live delegation")
            frames = [
                {
                    "type": "delegation.context.append" if identifier else "session.context.append",
                    **({"delegation_item_id": identifier} if identifier else {}),
                    "channel": "speakable" if kind == "session.commentary.append" else "commentary",
                    "content": [{"type": "input_text", "text": chunk}],
                }
                for chunk in _text_chunks(text)
            ]
        else:
            raise ValueError("Unsupported subscription Live control command")
        async with self._send_lock:
            if self._closed or self.provider_closed:
                if kind == "session.close":
                    return
                raise SubscriptionLiveError("connection_lost")
            if self._close_sent:
                if kind == "session.close":
                    return
                raise SubscriptionLiveError("connection_lost")
            for frame in frames:
                try:
                    await self.socket.send(json.dumps(frame))
                except Exception:
                    raise SubscriptionLiveError("connection_lost") from None
            if kind == "session.close":
                self._close_sent = True

    async def close(self) -> None:
        if self._closed:
            return
        try:
            if not self._close_sent and not self.provider_closed:
                await self.send({"type": "session.close"})
        except Exception:
            # Closing the owned socket remains mandatory after a failed close frame.
            log.warning("Subscription Live close frame could not be delivered.")
        finally:
            self._closed = True
            try:
                await self.socket.close()
            except Exception:
                raise SubscriptionLiveError("connection_lost") from None

    async def retire_control(self) -> None:
        """Release a failed socket without ending the still-live WebRTC call."""
        if self._closed:
            return
        self._closed = True
        try:
            await self.socket.close()
        except Exception:
            # A dead transport can reject close; it must never trigger a new call.
            log.warning("Subscription Live retired control socket could not close cleanly.")


class OpenAISubscriptionLiveProvider:
    """Additive subscription adapter; never searches for or falls back to a key."""

    name = "openai-live-subscription"
    source_timed_audio = True
    credential_family = "openai-codex"
    credential_candidates: tuple = ()
    supports_realtime = True
    continuous_conversation = True
    browser_audio = True
    requires_webrtc_offer = True
    implicit_usage_fallback_allowed = False
    provider_fallback_allowed = False
    handshake_budget_s = 60.0
    input_sample_rate = 24000
    output_sample_rate = 24000
    usage_billed = False
    delegation_mode = "client"
    requires_close_ack = False
    external_credentials_kind = "chatgpt_oauth"
    client_managed_delegation = True
    webrtc_start_event_required = False

    def __init__(
        self,
        *,
        credentials: Callable[..., Awaitable[Any]],
        connection_permit: Callable[[], Awaitable[None]] | None = None,
        http_client_factory: Callable[..., Any] | None = None,
        websocket_connect: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._credentials = credentials
        self._connection_permit = connection_permit
        self._http_client_factory = http_client_factory
        self._websocket_connect = websocket_connect

    async def can_open_duplex_session(self) -> bool:
        return callable(self._credentials)

    @staticmethod
    async def warm_transport(cfg: Any = None) -> None:
        """Prepare local imports/trust stores after the host's voice warm gate.

        No credential read or refresh, microphone, DNS or remote session is
        needed. Every explicit call still reads its selected account afresh.
        """
        from ._live_transport import warm_transport

        await warm_transport()

    async def _headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        credential = await self._credentials(force_refresh=force_refresh)
        token = getattr(credential, "access_token", "")
        account = getattr(credential, "account_id", "")
        if not token or not account:
            raise SubscriptionLiveError("authentication_required")
        return {"Authorization": f"Bearer {token}", "chatgpt-account-id": account}

    async def _retire_allocation(self, call_id: str, headers: dict, connector: Any) -> bool:
        """Best-effort disposal through the verified control protocol only.

        Sending close confirms delivery, not a provider acknowledgment. Failure
        stays explicit in the raised error so callers never report remote
        cleanup as verified or automatically allocate another call.
        """
        if not call_id or self._connection_permit is None:
            return False
        from ._live_transport import websocket_options

        socket = None
        try:
            async with asyncio.timeout(8):
                await asyncio.sleep(_jitter.uniform(0.15, 0.35))
                await self._connection_permit()
                socket = await connector(
                    SIDEBAND_BASE + call_id,
                    additional_headers=headers,
                    open_timeout=5,
                    close_timeout=2,
                    max_size=_MAX_FRAME_BYTES,
                    max_queue=4,
                    **await websocket_options(),
                )
                await socket.send(json.dumps({"type": "session.close"}))
            return True
        except Exception:
            log.warning("Subscription Live allocation cleanup could not be confirmed.")
            return False
        finally:
            if socket is not None:
                try:
                    await socket.close()
                except Exception:
                    log.warning("Subscription Live cleanup socket could not close cleanly.")

    async def reattach_session(
        self,
        previous: OpenAISubscriptionLiveConnection,
    ) -> OpenAISubscriptionLiveConnection | None:
        """Reconnect the existing call without replaying tasks or audio.

        A caller owns recovery admission after a terminal read error. A 404/410
        explicitly tells it this allocation cannot resume; auth and allowance
        failures remain terminal instead of selecting another billing mode.
        """
        from websockets.asyncio.client import connect

        from ._live_transport import websocket_options

        if previous.provider_closed or previous._close_sent:
            return None
        if self._connection_permit is None:
            raise SubscriptionLiveError("control_unavailable")
        if not _CALL_ID.fullmatch(previous.session_id):
            raise SubscriptionLiveError("invalid_response")
        await previous.retire_control()
        connector = self._websocket_connect or connect
        headers = {**await self._headers(), **previous._request_headers}
        refreshed = False
        for attempt in range(3):
            await asyncio.sleep(_jitter.uniform(0.15, 0.35) * (2**attempt))
            await self._connection_permit()
            try:
                socket = await connector(
                    SIDEBAND_BASE + previous.session_id,
                    additional_headers=headers,
                    open_timeout=12,
                    close_timeout=3,
                    max_size=_MAX_FRAME_BYTES,
                    max_queue=32,
                    **await websocket_options(),
                )
            except Exception as error:
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status in {404, 410}:
                    return None
                if status == 401 and not refreshed and attempt < 2:
                    headers = {
                        **await self._headers(force_refresh=True),
                        **previous._request_headers,
                    }
                    refreshed = True
                    continue
                if status in {401, 403, 429}:
                    raise SubscriptionLiveError(_error_code(status)) from None
                if attempt < 2:
                    continue
                raise SubscriptionLiveError("control_unavailable") from None
            resumed = OpenAISubscriptionLiveConnection(
                socket,
                session_id=previous.session_id,
                answer_sdp=previous.answer_sdp,
                request_headers=previous._request_headers,
            )
            resumed._started_at = previous._started_at
            resumed._turns = previous._turns
            resumed._seen = previous._seen
            resumed._seen_order = previous._seen_order
            resumed._delegations = previous._delegations
            return resumed
        raise SubscriptionLiveError("control_unavailable")

    async def open_session(self, cfg: Any) -> OpenAISubscriptionLiveConnection:
        # Optional dependencies are loaded only for an explicitly started call.
        from websockets.asyncio.client import connect

        from ._live_transport import (
            preparing_http_client,
            preparing_websocket_options,
            startup_http_trace,
            startup_websocket_options,
        )

        offer = str(cfg.offer_sdp or "")
        if not _audio_only_sdp(offer) or len(offer.encode("utf-8")) > _MAX_SDP_BYTES:
            raise ValueError("Subscription Live requires a bounded WebRTC audio offer")
        session = _session_config(dict(cfg.session))
        request_headers = {
            "OpenAI-Alpha": "quicksilver=v2",
            "session-id": str(uuid4()),
            "thread-id": str(uuid4()),
            "x-session-id": str(uuid4()),
        }
        headers = dict(request_headers)
        connector = self._websocket_connect or connect
        call_id, answer = "", ""
        allocated = False
        credentials_ready = False
        started_at = time.monotonic()
        mark = getattr(cfg, "on_startup_phase", None) or (lambda _phase: None)
        try:
            # Local TLS/client construction and selected-account loading are
            # independent. Neither operation can allocate a remote voice call.
            async with (
                preparing_http_client(
                    self._http_client_factory, timeout=25, follow_redirects=False,
                ) as preparation,
                preparing_websocket_options() as tls_preparation,
            ):
                headers = {**await self._headers(), **request_headers}
                credentials_ready = True
                mark("credentials_ready")
                client = await asyncio.shield(preparation)
                mark("http_client_ready")
                for attempt in range(2):
                    async with client.stream(
                        "POST",
                        CALL_URL,
                        headers=headers,
                        json={"sdp": offer, "session": session},
                        extensions={"trace": startup_http_trace(mark)},
                    ) as response:
                        if response.status_code == 401 and attempt == 0:
                            # Rejected auth allocated no call. Refresh once;
                            # never switch the billing source.
                            headers = {**await self._headers(force_refresh=True), **request_headers}
                            continue
                        if response.status_code >= 300:
                            raise SubscriptionLiveError(_error_code(response.status_code))
                        allocated = True
                        call_id = _call_id(response.headers)
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > _MAX_SDP_BYTES:
                                raise SubscriptionLiveError("invalid_response")
                        answer = content.decode("utf-8")
                        if not _audio_only_sdp(answer):
                            raise SubscriptionLiveError("invalid_response")
                        mark("session_response")
                        break
                on_transport_ready = getattr(cfg, "on_transport_ready", None)
                if on_transport_ready is not None:
                    # ICE/DTLS can also overlap a cold local trust-store load.
                    # The host withholds input until media AND control are ready.
                    await on_transport_ready(answer)
                options = await tls_preparation
                mark("control_tls_ready")
        except asyncio.CancelledError:
            if allocated:
                await self._retire_allocation(call_id, headers, connector)
            raise
        except SubscriptionLiveError as error:
            if allocated:
                await self._retire_allocation(call_id, headers, connector)
                error.allocation_unconfirmed = True
            raise
        except Exception:
            if not credentials_ready:
                # Preserve the auth layer's actionable, sanitized errors just
                # as before preparation was overlapped with credential work.
                raise
            if allocated:
                await self._retire_allocation(call_id, headers, connector)
            raise SubscriptionLiveError(
                "provider_error", allocation_unconfirmed=allocated
            ) from None
        log.info(
            "Subscription Live call created in %.0f ms.", (time.monotonic() - started_at) * 1000
        )
        # Only a transient call-indexing 404 may retry the SAME sideband. Every
        # retry pays the host's shared connection permit and includes jitter.
        for attempt in range(3):
            try:
                attach_started_at = time.monotonic()
                mark("control_connect_started")
                socket = await connector(
                    SIDEBAND_BASE + call_id,
                    additional_headers=headers,
                    open_timeout=12,
                    close_timeout=3,
                    max_size=_MAX_FRAME_BYTES,
                    max_queue=32,
                    **options,
                    **startup_websocket_options(mark),
                )
                log.info(
                    "Subscription Live control attached in %.0f ms.",
                    (time.monotonic() - attach_started_at) * 1000,
                )
                return OpenAISubscriptionLiveConnection(
                    socket,
                    session_id=call_id,
                    answer_sdp=answer,
                    request_headers=request_headers,
                )
            except asyncio.CancelledError:
                await self._retire_allocation(call_id, headers, connector)
                raise
            except Exception as error:
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status == 404 and attempt < 2 and self._connection_permit is not None:
                    try:
                        await asyncio.sleep(_jitter.uniform(0.15, 0.35) * (2**attempt))
                        await self._connection_permit()
                    except asyncio.CancelledError:
                        await self._retire_allocation(call_id, headers, connector)
                        raise
                    continue
                # No verified HTTP hangup exists for this private route. Try
                # the same control channel for cleanup, never another call.
                await self._retire_allocation(call_id, headers, connector)
                raise SubscriptionLiveError(
                    _error_code(status) if status in {401, 403, 429} else "control_unavailable",
                    allocation_unconfirmed=True,
                ) from None
        raise SubscriptionLiveError("control_unavailable")
