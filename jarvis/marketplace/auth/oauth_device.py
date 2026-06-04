"""DeviceFlowHandler — covers GitHub.

GitHub's OAuth Device Flow (RFC 8628). The user sees a short user-code
in the Jarvis dialog, opens `verification_uri` in their browser, types
the code, approves; meanwhile our backend polls `token_url` every
`interval` seconds until the auth server returns a token.

Why this for GitHub: it's the only GitHub flow that doesn't require a
client_secret in the binary AND supports refresh tokens. Same flow that
`gh auth login` uses.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from jarvis.marketplace.auth.base import (
    AuthSession,
    FlowResult,
    session_id,
)
from jarvis.marketplace.token_store import Tokens

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceFlowConfig:
    plugin_id: str
    device_url: str
    verify_url: str
    token_url: str
    client_id: str
    scopes: list[str]
    timeout_seconds: int = 10


@dataclass
class _PendingDeviceFlow:
    config: DeviceFlowConfig
    device_code: str
    interval: int
    expires_at: datetime


class DeviceFlowHandler:
    """One handler per device-flow plugin (GitHub today)."""

    def __init__(self, config: DeviceFlowConfig) -> None:
        self.plugin_id = config.plugin_id
        self._config = config
        self._pending: dict[str, _PendingDeviceFlow] = {}

    # ------------------------------------------------------------------

    async def start(self, plugin_spec: object) -> AuthSession:
        scope_str = " ".join(self._config.scopes)
        body = {"client_id": self._config.client_id}
        if scope_str:
            body["scope"] = scope_str
        timeout = httpx.Timeout(self._config.timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    self._config.device_url,
                    data=body,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Personal-Jarvis/1.0",
                    },
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"device_code request failed: {exc}") from exc
        if r.status_code != 200:
            raise RuntimeError(
                f"device_code HTTP {r.status_code}: {r.text[:200]}"
            )
        payload = r.json()
        if "device_code" not in payload or "user_code" not in payload:
            raise RuntimeError(
                f"device_code response missing required fields: {payload}"
            )

        sid = session_id()
        interval = int(payload.get("interval", 5))
        expires_in = int(payload.get("expires_in", 900))
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        self._pending[sid] = _PendingDeviceFlow(
            config=self._config,
            device_code=payload["device_code"],
            interval=interval,
            expires_at=expires_at,
        )
        return AuthSession(
            flow_id=sid,
            plugin_id=self.plugin_id,
            kind="device_flow",
            user_code=payload["user_code"],
            verification_uri=payload.get("verification_uri"),
            verification_uri_complete=payload.get("verification_uri_complete"),
            expires_at_ms=int(expires_at.timestamp() * 1000),
            interval=interval,
        )

    # ------------------------------------------------------------------

    async def await_completion(self, session: AuthSession) -> FlowResult:
        pending = self._pending.get(session.flow_id)
        if pending is None:
            return FlowResult(tokens=None, error="unknown flow_id")

        try:
            tokens = await self._poll(pending)
            return FlowResult(tokens=tokens, error=None)
        except RuntimeError as exc:
            return FlowResult(tokens=None, error=str(exc))
        finally:
            self._pending.pop(session.flow_id, None)

    async def _poll(self, pending: _PendingDeviceFlow) -> Tokens:
        """Polls the token endpoint until success, denial, or expiry.

        GitHub's four error codes (per docs):
          - authorization_pending: keep polling
          - slow_down: increase interval by 5 seconds
          - expired_token: device_code is dead, user took too long
          - access_denied: user rejected, abort
        """
        interval = pending.interval
        timeout = httpx.Timeout(pending.config.timeout_seconds)

        while datetime.now(UTC) < pending.expires_at:
            await asyncio.sleep(interval)
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    r = await client.post(
                        pending.config.token_url,
                        data={
                            "client_id": pending.config.client_id,
                            "device_code": pending.device_code,
                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        },
                        headers={
                            "Accept": "application/json",
                            "User-Agent": "Personal-Jarvis/1.0",
                        },
                    )
            except httpx.HTTPError as exc:
                log.warning("device-flow poll http error: %s", exc)
                continue  # network blip — retry next tick

            if r.status_code != 200:
                # GitHub returns 200 even on `authorization_pending`; a
                # non-200 means a real protocol error.
                raise RuntimeError(
                    f"token poll HTTP {r.status_code}: {r.text[:200]}"
                )

            payload = r.json()
            err = payload.get("error")

            if err is None and "access_token" in payload:
                expires_in = payload.get("expires_in")
                expires_at = (
                    datetime.now(UTC) + timedelta(seconds=int(expires_in))
                    if expires_in is not None
                    else None
                )
                extra: dict[str, str] = {}
                if "scope" in payload:
                    extra["scope"] = payload["scope"]
                if "token_type" in payload:
                    extra["token_type"] = payload["token_type"]
                return Tokens(
                    access=payload["access_token"],
                    refresh=payload.get("refresh_token"),
                    expires_at=expires_at,
                    extra=extra,
                )

            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            if err == "expired_token":
                raise RuntimeError(
                    "device code expired — user took too long; please retry"
                )
            if err == "access_denied":
                raise RuntimeError("user denied authorization")
            # Unknown error
            raise RuntimeError(
                f"unknown error from token endpoint: {payload!r}"
            )

        raise RuntimeError("device code expired locally before user approved")

    # ------------------------------------------------------------------

    async def refresh(self, current: Tokens) -> Tokens:
        if not current.refresh:
            raise RuntimeError("no refresh token stored")
        # GitHub device-flow refresh: same token endpoint, no client_secret
        # required (the killer feature of device flow).
        timeout = httpx.Timeout(self._config.timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    self._config.token_url,
                    data={
                        "client_id": self._config.client_id,
                        "grant_type": "refresh_token",
                        "refresh_token": current.refresh,
                    },
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Personal-Jarvis/1.0",
                    },
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"refresh request failed: {exc}") from exc
        payload = r.json()
        if r.status_code != 200 or payload.get("error"):
            err = payload.get("error", f"HTTP {r.status_code}")
            if err == "invalid_grant" or err == "bad_refresh_token":
                raise RuntimeError("revoked")
            raise RuntimeError(f"refresh failed: {err}")
        access = payload.get("access_token")
        if not access:
            raise RuntimeError(f"refresh response missing access_token: {payload}")
        expires_in = payload.get("expires_in")
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=int(expires_in))
            if expires_in is not None
            else None
        )
        return Tokens(
            access=access,
            refresh=payload.get("refresh_token") or current.refresh,
            expires_at=expires_at,
            extra=current.extra,
        )

    @staticmethod
    def auth_header(tokens: Tokens) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens.access}"}
