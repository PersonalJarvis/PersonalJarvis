"""OpenAI Live wire transport. No Jarvis imports or native platform dependencies."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)

class OpenAILiveConnection:
    """One primary socket or WebRTC sideband; the application owns tool execution."""

    def __init__(self, socket: Any, *, session_id: str = "", answer_sdp: str = "") -> None:
        self.socket = socket
        self.session_id = session_id
        self.answer_sdp = answer_sdp

    async def send(self, event: dict) -> None:
        await self.socket.send(json.dumps(event))

    async def receive(self) -> dict:
        payload = json.loads(await self.socket.recv())
        if not isinstance(payload, dict):
            raise ValueError("Invalid Live event")
        return payload

    async def close(self) -> None:
        await self.socket.close()


class OpenAILiveProvider:
    """Capability-discovered continuous voice provider."""

    name = "openai-live"
    credential_family = "openai"
    supports_realtime = True
    continuous_conversation = True
    browser_audio = True
    source_timed_audio = True
    requires_webrtc_offer = True
    handshake_budget_s = 60.0
    implicit_usage_fallback_allowed = False
    input_sample_rate = 24000
    output_sample_rate = 24000
    credential_candidates = (
        ("openai_api_key", "OPENAI_API_KEY"),
        ("realtime_openai_api_key", "JARVIS_REALTIME_OPENAI_API_KEY"),
    )

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = api_key or ""

    async def reattach_session(self, previous: OpenAILiveConnection) -> OpenAILiveConnection | None:
        """Restore only the sideband of an existing WebRTC call, without creating a billed call."""
        from websockets.asyncio.client import connect
        from websockets.exceptions import InvalidStatus

        from ._live_transport import websocket_options

        if not previous.session_id or not previous.answer_sdp:
            return None
        try:
            socket = await connect(
                "wss://api.openai.com/v1/live/sessions/"
                f"{quote(previous.session_id, safe='')}/attach",
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
                open_timeout=15, max_size=8_000_000,
                **await websocket_options(),
            )
        except InvalidStatus as exc:
            if exc.response.status_code in {404, 410}:
                return None
            raise
        return OpenAILiveConnection(
            socket, session_id=previous.session_id, answer_sdp=previous.answer_sdp,
        )

    async def can_open_duplex_session(self) -> bool:
        return bool(self._api_key)

    @staticmethod
    async def warm_transport(cfg: Any = None) -> None:
        """Pre-import the handshake stack and resolve the Live endpoint.

        The first wake of the day paid for all of this inside the call:
        importing httpx + websockets from a cold disk, and the DNS lookup
        for api.openai.com on the handshake's critical path. Warming moves
        it to the boot worker (after the voice gate, re-armed after every
        call), so a wake word pays only the session creation itself.
        Best-effort by contract: no socket is held, no billed call is made,
        and any failure here only costs the latency it was meant to save.
        """
        del cfg  # nothing session-specific about imports and DNS
        from ._live_transport import warm_transport

        await warm_transport()

        def _warm() -> None:
            try:
                import socket

                socket.getaddrinfo("api.openai.com", 443)
            except OSError:
                # Offline or DNS-blocked: the handshake reports it honestly.
                pass

        await asyncio.to_thread(_warm)

    async def open_session(self, cfg: Any) -> OpenAILiveConnection:
        # Imported only on an explicit call, never during boot or registration.
        # (warm_transport pre-imports these; the import here stays as the
        # fallback for a call that was never warmed.)
        import time

        from websockets.asyncio.client import connect

        from ._live_transport import preparing_http_client, websocket_options

        if not self._api_key:
            raise ValueError("Connect OpenAI in API Keys before starting voice.")
        mark = getattr(cfg, "on_startup_phase", None) or (lambda _phase: None)
        mark("credentials_ready")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        session = dict(cfg.session)
        offer = cfg.offer_sdp
        session_id = ""
        answer = ""
        url = "wss://api.openai.com/v1/live/sessions"
        try:
            if offer:
                # No automatic retries: session creation is a billed mutation.
                started_at = time.monotonic()
                # Construction can load certificates/proxy settings synchronously.
                # Keep the application's existing HTTP trust/cache policy;
                # never retain a billed or loop-bound idle voice connection.
                async with preparing_http_client(timeout=25) as preparation:
                    client = await asyncio.shield(preparation)
                    mark("http_client_ready")
                    response = await client.post(
                        "https://api.openai.com/v1/live/sessions",
                        headers=headers,
                        json={"session": session, "transport": {"type": "webrtc", "sdp": offer}},
                    )
                    if response.status_code >= 400:
                        from jarvis.brain.provider_test import classify_provider_error

                        # Only the classification leaves this scope, never the
                        # provider's body (AP-34): "no_credits" and "rate_limited"
                        # share HTTP 429 and need different words for the user.
                        cause = classify_provider_error(
                            f"HTTP {response.status_code} {response.text[:2000]}"
                        )
                        raise RuntimeError(
                            "OpenAI Live session creation failed "
                            f"(HTTP {response.status_code}, {cause})."
                        )
                    payload = response.json()
                    session_id = payload["session"]["id"]
                    answer = payload["transport"]["sdp"]
                    mark("session_response")
                log.info(
                    "OpenAI Live session created in %.0f ms.",
                    (time.monotonic() - started_at) * 1000.0,
                )
                url += f"/{quote(session_id, safe='')}/attach"
            else:
                session["audio"] = {
                    **session.get("audio", {}),
                    "format": {"type": "audio/pcm", "rate": 24000},
                }
            on_transport_ready = getattr(cfg, "on_transport_ready", None)
            if answer and on_transport_ready is not None:
                # Let ICE/DTLS run concurrently with sideband attachment. The
                # application still withholds microphone audio and tool-ready
                # state until this method returns successfully.
                await on_transport_ready(answer)
            options = await websocket_options()
            mark("control_tls_ready")
            attach_started_at = time.monotonic()
            socket = await connect(
                url, additional_headers=headers, open_timeout=25, max_size=8_000_000,
                **options,
            )
            log.info(
                "OpenAI Live transport attached in %.0f ms.",
                (time.monotonic() - attach_started_at) * 1000.0,
            )
        except BaseException:
            if session_id:
                # Cancellation after creation owns the billed session too.
                # Preserve the original error if bounded cleanup also fails.
                try:
                    async with asyncio.timeout(10):
                        async with preparing_http_client(timeout=10) as preparation:
                            client = await asyncio.shield(preparation)
                            response = await client.post(
                                "https://api.openai.com/v1/live/sessions/"
                                f"{quote(session_id, safe='')}/hangup",
                                headers=headers,
                            )
                            if response.status_code >= 400:
                                log.warning(
                                    "Live startup hangup was not confirmed (HTTP %d)",
                                    response.status_code,
                                )
                except Exception:
                    log.warning("Live startup hangup was not confirmed")
            raise
        connection = OpenAILiveConnection(socket, session_id=session_id, answer_sdp=answer)
        if not offer:
            try:
                await connection.send({"type": "session.start", "session": session})
            except BaseException:
                # A failed or cancelled start still owns the newly opened socket.
                await connection.close()
                raise
        return connection
