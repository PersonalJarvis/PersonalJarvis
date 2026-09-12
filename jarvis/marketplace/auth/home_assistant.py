"""Home Assistant's instance-local IndieAuth browser approval lifecycle."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx

from jarvis.marketplace.auth.base import AuthSession
from jarvis.marketplace.auth.oauth_pkce_loopback import (
    PkceLoopbackConfig,
    PkceLoopbackHandler,
    _PendingPkceFlow,
)
from jarvis.marketplace.instance_url import normalize_instance_url
from jarvis.marketplace.token_store import Tokens


class HomeAssistantHandler(PkceLoopbackHandler):
    """Reuse callback state/cancellation while honoring HA's non-PKCE protocol.

    HA identifies an application by its URL. The callback origin therefore is
    its client ID; no publisher or end-user developer registration is required.
    Refresh and revocation metadata stay inside the protected credential blob.
    """

    def __init__(
        self, instance_url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._instance_url = normalize_instance_url(instance_url) if instance_url else ""
        self._transport = transport
        super().__init__(
            PkceLoopbackConfig(
                plugin_id="home_assistant",
                authorization_url=self._instance_url + "/auth/authorize",
                token_url=self._instance_url + "/auth/token",
                client_id="",
                callback_port=0,
                scopes=[],
            )
        )

    async def start(self, plugin_spec: object) -> AuthSession:
        if not self._instance_url:
            raise ValueError("Enter the Home Assistant instance address.")
        return await super().start(plugin_spec)

    def _authorize_params(self, *, redirect_uri: str, state: str, challenge: str) -> dict[str, str]:
        parts = urlsplit(redirect_uri)
        client_id = f"{parts.scheme}://{parts.netloc}"
        self._config = replace(self._config, client_id=client_id)
        return {"client_id": client_id, "redirect_uri": redirect_uri, "state": state}

    async def _request_token(self, instance_url: str, body: dict[str, str]) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10, transport=self._transport) as client:
                response = await client.post(instance_url + "/auth/token", data=body)
            if response.status_code != 200:
                if body.get("grant_type") == "refresh_token" and response.status_code in (
                    400,
                    401,
                    403,
                ):
                    raise RuntimeError("revoked")
                raise RuntimeError(
                    f"Home Assistant token request failed (HTTP {response.status_code})"
                )
            payload = response.json()
            if (
                not isinstance(payload, dict)
                or not isinstance(payload.get("access_token"), str)
                or not payload["access_token"]
            ):
                raise RuntimeError("Home Assistant token response missing access token")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Home Assistant token endpoint unreachable or invalid") from exc

    @staticmethod
    def _expires(payload: dict) -> datetime:
        try:
            seconds = int(payload["expires_in"])
            if seconds <= 0:
                raise ValueError("non-positive expiry")
            return datetime.now(UTC) + timedelta(seconds=seconds)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("Home Assistant token response has invalid expiry") from exc

    async def _exchange(self, pending: _PendingPkceFlow, *, code: str) -> Tokens:
        payload = await self._request_token(
            self._instance_url,
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": pending.config.client_id,
            },
        )
        if not isinstance(payload.get("refresh_token"), str) or not payload["refresh_token"]:
            raise RuntimeError("Home Assistant token response missing refresh token")
        tokens = Tokens(
            access=payload["access_token"],
            refresh=payload["refresh_token"],
            expires_at=self._expires(payload),
            extra={
                "instance_url": self._instance_url,
                "client_id": pending.config.client_id,
                "revocation_endpoint": self._instance_url + "/auth/revoke",
            },
        )
        try:
            async with httpx.AsyncClient(timeout=10, transport=self._transport) as client:
                response = await client.get(
                    self._instance_url + "/api/", headers=self.auth_header(tokens)
                )
            if response.status_code != 200 or response.json() != {"message": "API running."}:
                raise RuntimeError("Home Assistant API verification failed")
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Home Assistant API verification unreachable or invalid") from exc
        return tokens

    async def refresh(self, current: Tokens) -> Tokens:
        if not current.refresh or not current.extra.get("client_id"):
            raise RuntimeError("Home Assistant browser approval required")
        instance = normalize_instance_url(current.extra.get("instance_url", ""))
        payload = await self._request_token(
            instance,
            {
                "grant_type": "refresh_token",
                "refresh_token": current.refresh,
                "client_id": current.extra["client_id"],
            },
        )
        return Tokens(
            access=payload["access_token"],
            refresh=current.refresh,
            expires_at=self._expires(payload),
            extra=dict(current.extra),
        )
