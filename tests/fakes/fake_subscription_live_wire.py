"""Offline OAuth, HTTP and sideband peers for the private Live wire contract."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from types import SimpleNamespace

import httpx

SDP = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"


class FakeSubscriptionSocket:
    def __init__(self, events: list | None = None) -> None:
        self.events = deque(events or [])
        self.sent: list[dict] = []
        self.closed = 0
        self.send_error: Exception | None = None

    async def send(self, payload: str) -> None:
        if self.send_error:
            raise self.send_error
        self.sent.append(json.loads(payload))

    async def recv(self):
        if not self.events:
            await asyncio.Future()
        event = self.events.popleft()
        if isinstance(event, BaseException):
            raise event
        return json.dumps(event) if isinstance(event, (dict, list)) else event

    async def close(self) -> None:
        self.closed += 1


class FakeSubscriptionLiveWire:
    def __init__(self, responses: list[httpx.Response] | None = None) -> None:
        self.responses = deque(
            responses
            or [
                httpx.Response(
                    201,
                    text=SDP,
                    headers={"location": "/v1/live/rtc_fake"},
                )
            ]
        )
        self.requests: list[httpx.Request] = []
        self.auth_refreshes: list[bool] = []
        self.connects: list[tuple[str, dict]] = []
        self.socket = FakeSubscriptionSocket()
        self.connect_errors: deque[Exception] = deque()
        self.permits = 0

    async def credentials(self, *, force_refresh: bool = False):
        self.auth_refreshes.append(force_refresh)
        return SimpleNamespace(
            access_token="fake-refreshed" if force_refresh else "fake-original",
            account_id="fake-account",
        )

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.popleft()

    def client(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(self._respond), **kwargs)

    async def connect(self, url: str, **kwargs):
        self.connects.append((url, kwargs))
        if self.connect_errors:
            raise self.connect_errors.popleft()
        return self.socket

    async def permit(self):
        self.permits += 1

    def provider(self):
        from jarvis.plugins.realtime.openai_subscription_live import OpenAISubscriptionLiveProvider

        return OpenAISubscriptionLiveProvider(
            credentials=self.credentials,
            connection_permit=self.permit,
            http_client_factory=self.client,
            websocket_connect=self.connect,
        )

    @staticmethod
    def config(**session):
        return SimpleNamespace(
            offer_sdp=SDP,
            session={
                "model": "gpt-live-1-codex",
                "audio": {"output": {"voice": "cove"}},
                **session,
            },
        )
