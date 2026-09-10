"""Provider protocol verification at the webhook boundary; no provider code is embedded."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
import time
from types import SimpleNamespace
from typing import Any

from jarvis.core.config import get_secret
from jarvis.core.http_pool import SyncHttpClientPool

from .webhook_auth import _slot

_google_pool = SyncHttpClientPool(timeout_s=10)
_certificates: dict[str, tuple[float, Any]] = {}
_certificate_lock = threading.Lock()


def _google_request(url, method="GET", **kwargs):
    with _certificate_lock:
        cached = _certificates.get(url)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        response = _google_pool.client().request(method, url)
        result = SimpleNamespace(
            status=response.status_code, data=response.content, headers=response.headers
        )
        if response.status_code == 200:
            _certificates[url] = (time.monotonic() + 300, result)
        return result


def verify_google_token(token: str, audience: str, service_account: str) -> bool:
    if not audience or not service_account.endswith(".gserviceaccount.com"):
        return False
    from google.oauth2.id_token import verify_oauth2_token

    try:
        claims = verify_oauth2_token(token, _google_request, audience=audience)
        return claims.get("email") == service_account and claims.get("email_verified") is True
    except (ValueError, TypeError):
        return False


def verify_provider(
    row: dict, trigger: Any, raw: bytes, headers: Any, *, now: float | None = None
) -> bool:
    now = time.time() if now is None else now
    if trigger.provider == "gmail":
        scheme, _, token = headers.get("authorization", "").partition(" ")
        return scheme.lower() == "bearer" and verify_google_token(
            token, trigger.oidc_audience, trigger.service_account
        )
    key = get_secret(_slot(row))
    if not key:
        return False
    signed, signatures = raw, []
    try:
        if trigger.provider == "github":
            value = headers.get("x-hub-signature-256", "")
            signatures = [value.removeprefix("sha256=")] if value.startswith("sha256=") else []
        elif trigger.provider == "linear":
            signatures = [headers.get("linear-signature", "")]
            timestamp = json.loads(raw).get("webhookTimestamp")
            if not isinstance(timestamp, int) or abs(now - timestamp / 1000) > 60:
                return False
        elif trigger.provider == "slack":
            timestamp = headers.get("x-slack-request-timestamp", "")
            if abs(now - int(timestamp)) > 300:
                return False
            signed = b"v0:" + timestamp.encode("ascii") + b":" + raw
            value = headers.get("x-slack-signature", "")
            signatures = [value.removeprefix("v0=")] if value.startswith("v0=") else []
        elif trigger.provider == "stripe":
            parts = [
                item.strip().partition("=")
                for item in headers.get("stripe-signature", "").split(",")
            ]
            timestamps = [value for name, _, value in parts if name == "t"]
            if len(timestamps) != 1 or abs(now - int(timestamps[0])) > 300:
                return False
            signed = timestamps[0].encode("ascii") + b"." + raw
            signatures = [value for name, _, value in parts if name == "v1"]
        else:
            return False
    except (ValueError, TypeError, AttributeError):
        return False
    expected = hmac.new(key.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return any(
        re.fullmatch(r"[a-fA-F0-9]{64}", value) and hmac.compare_digest(expected, value.lower())
        for value in signatures
    )


def normalize_provider(provider: str, payload: dict) -> dict:
    if provider == "gmail":
        message = payload.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("data"), str):
            raise ValueError("Expected an authenticated Gmail Pub/Sub message")
        try:
            data = json.loads(base64.b64decode(message["data"], validate=True))
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid Gmail notification") from exc
        if not isinstance(data, dict) or "historyId" not in data:
            raise ValueError("Gmail notifications must contain a historyId")
        return {
            "message_id": message.get("messageId", ""),
            "data": data,
            "subscription": payload.get("subscription", ""),
        }
    return payload
