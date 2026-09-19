"""Desktop half of the confidential publisher OAuth flow.

Only a PKCE-bound one-time completion code crosses the browser handoff. Provider
refresh tokens and client secrets remain in the publisher service.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx

from jarvis.core.http_pool import HttpClientPool
from jarvis.marketplace.auth.base import AuthSession, FlowResult, pkce_pair, random_state
from jarvis.marketplace.oauth_callback_server import CallbackTimeoutError, OAuthCallbackServer
from jarvis.marketplace.token_store import Tokens

log = logging.getLogger(__name__)


@dataclass
class _BrokerFlow:
    verifier: str
    callback: OAuthCallbackServer


def broker_base(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Publisher HTTPS broker is not configured")
    return value.rstrip("/")


class OAuthBrokerHandler:
    def __init__(self, plugin_id: str, base_url: str, *, legacy_handler=None, transport=None):
        self.plugin_id = plugin_id
        self.base_url = broker_base(base_url) if base_url else ""
        self.legacy_handler = legacy_handler
        self._pool = HttpClientPool(
            timeout_s=25, transport=transport, client_kwargs={"follow_redirects": False}
        )
        self.pending: dict[str, _BrokerFlow] = {}

    async def request(self, path: str, body: dict, *, base: str | None = None) -> dict:
        failed = True
        try:
            response = await self._pool.client().post((base or self.base_url) + path, json=body)
            if response.status_code == 401:
                raise RuntimeError("revoked")
            if response.status_code != 200:
                raise RuntimeError("Publisher connection temporarily unavailable")
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Invalid broker response")
            failed = False
            return data
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("Publisher connection temporarily unavailable") from None
        finally:
            if failed or path not in {"/start", "/redeem"}:
                await self._pool.aclose()

    async def start(self, plugin_spec) -> AuthSession:
        if not self.base_url:
            raise RuntimeError("Publisher service is not configured")
        verifier, challenge = pkce_pair()
        state = random_state()
        callback = OAuthCallbackServer(state, callback_path="/oauth/broker", port=0)
        await callback.start()
        try:
            result = await self.request(
                "/start",
                {
                    "provider": self.plugin_id,
                    "challenge": challenge,
                    "loopback_uri": callback.redirect_uri,
                    "client_state": state,
                },
            )
            url = result.get("authorization_url", "")
            expected = urlsplit(plugin_spec.auth.authorization_url)
            if not isinstance(url, str):
                raise RuntimeError("Publisher returned an invalid authorization destination")
            actual = urlsplit(url)
            if (
                actual.scheme != "https"
                or actual.netloc != expected.netloc
                or actual.path != expected.path
            ):
                raise RuntimeError("Publisher returned an unexpected authorization destination")
            flow = result.get("flow_id")
            if not isinstance(flow, str) or not 32 <= len(flow) <= 128:
                raise RuntimeError("Publisher returned an invalid authorization session")
            self.pending[flow] = _BrokerFlow(verifier, callback)
            return AuthSession(
                flow_id=flow,
                plugin_id=self.plugin_id,
                kind="browser_redirect",
                open_url=url,
                redirect_uri=callback.redirect_uri,
                expires_at_ms=int((time.time() + 300) * 1000),
            )
        except BaseException:
            await callback.stop()
            await self._pool.aclose()
            raise

    def tokens(self, result: dict, *, base: str | None = None, extra: dict | None = None) -> Tokens:
        access = result.get("access_token")
        handle = result.get("refresh_handle")
        if not isinstance(access, str) or not access or not isinstance(handle, str) or not handle:
            raise RuntimeError("Publisher returned no usable grant")
        return Tokens(
            access=access,
            refresh=handle,
            expires_at=datetime.now(UTC) + timedelta(seconds=int(result["expires_in"])),
            extra={
                **(extra or {}),
                "broker_url": base or self.base_url,
                "client_id": result["client_id"],
                "auth_kind": "broker",
            },
        )

    async def await_completion(self, session: AuthSession) -> FlowResult:
        pending = self.pending.get(session.flow_id)
        if pending is None:
            return FlowResult(tokens=None, error="Unknown authorization", error_code="unknown")
        proof = {"flow_id": session.flow_id, "verifier": pending.verifier}
        try:
            callback = await pending.callback.await_callback()
            result = await self.request("/redeem", {**proof, "handoff_code": callback.code})
            if self.pending.get(session.flow_id) is not pending:
                if result.get("state") == "connected" and result.get("refresh_handle"):
                    await self.request(
                        "/disconnect",
                        {"provider": self.plugin_id, "handle": result["refresh_handle"]},
                    )
                return FlowResult(tokens=None, error="Authorization cancelled", error_code="denied")
            if result.get("state") == "connected":
                return FlowResult(tokens=self.tokens(result), error=None)
            return FlowResult(
                tokens=None, error="Authorization was not completed", error_code="denied"
            )
        except CallbackTimeoutError:
            return FlowResult(tokens=None, error="Authorization timed out", error_code="timeout")
        except (RuntimeError, KeyError, ValueError, TypeError) as exc:
            denied = "denied" in str(exc).lower()
            return FlowResult(
                tokens=None,
                error="Authorization declined" if denied else "Publisher connection failed; retry",
                error_code="denied" if denied else "provider_unreachable",
            )
        finally:
            self.pending.pop(session.flow_id, None)
            await pending.callback.stop()
            try:
                await self.request("/cancel", proof)
            except RuntimeError:
                log.debug("broker pending-flow cleanup unavailable for %s", self.plugin_id)
            await self._pool.aclose()

    async def cancel(self, session: AuthSession) -> None:
        pending = self.pending.pop(session.flow_id, None)
        if pending is not None:
            await pending.callback.stop()
            await self.request(
                "/cancel", {"flow_id": session.flow_id, "verifier": pending.verifier}
            )

    async def refresh(self, current: Tokens) -> Tokens:
        base = current.extra.get("broker_url")
        if not base:
            if self.legacy_handler is None:
                raise RuntimeError("Issuing client missing; reconnect")
            return await self.legacy_handler.refresh(current)
        base = broker_base(base)
        result = await self.request(
            "/refresh", {"provider": self.plugin_id, "handle": current.refresh}, base=base
        )
        extra = {k: v for k, v in current.extra.items() if k != "client_secret"}
        return self.tokens(result, base=base, extra=extra)

    @staticmethod
    def auth_header(tokens: Tokens) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens.access}"}
