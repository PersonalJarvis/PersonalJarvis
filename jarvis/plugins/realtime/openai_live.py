"""OpenAI Live wire transport. No Jarvis imports or native platform dependencies."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote


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
    requires_webrtc_offer = True
    implicit_usage_fallback_allowed = False
    input_sample_rate = 24000
    output_sample_rate = 24000
    credential_candidates = (
        ("openai_api_key", "OPENAI_API_KEY"),
        ("realtime_openai_api_key", "JARVIS_REALTIME_OPENAI_API_KEY"),
    )

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = api_key or ""

    async def can_open_duplex_session(self) -> bool:
        return bool(self._api_key)

    async def open_session(self, cfg: Any) -> OpenAILiveConnection:
        # Imported only on an explicit call, never during boot or registration.
        import httpx
        from websockets.asyncio.client import connect

        if not self._api_key:
            raise ValueError("Connect OpenAI in API Keys before starting voice.")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        session = dict(cfg.session)
        offer = cfg.offer_sdp
        session_id = ""
        answer = ""
        url = "wss://api.openai.com/v1/live/sessions"
        if offer:
            # No automatic retries: session creation is a billed mutation.
            async with httpx.AsyncClient(timeout=25) as client:
                response = await client.post(
                    "https://api.openai.com/v1/live/sessions",
                    headers=headers,
                    json={"session": session, "transport": {"type": "webrtc", "sdp": offer}},
                )
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"OpenAI Live session creation failed (HTTP {response.status_code})."
                    )
                payload = response.json()
                session_id = payload["session"]["id"]
                answer = payload["transport"]["sdp"]
            url += f"/{quote(session_id, safe='')}/attach"
        else:
            session["audio"] = {
                **session.get("audio", {}),
                "format": {"type": "audio/pcm", "rate": 24000},
            }
        try:
            socket = await connect(
                url, additional_headers=headers, open_timeout=25, max_size=8_000_000
            )
        except Exception:
            if session_id:
                # A failed attachment must not leave the billed primary session open.
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        "https://api.openai.com/v1/live/sessions/"
                        f"{quote(session_id, safe='')}/hangup",
                        headers=headers,
                    )
            raise
        connection = OpenAILiveConnection(socket, session_id=session_id, answer_sdp=answer)
        if not offer:
            await connection.send({"type": "session.start", "session": session})
        return connection
