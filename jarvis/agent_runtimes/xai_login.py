"""The SuperGrok / X Premium+ login Hermes and OpenClaw agents run on.

xAI serves its subscription to third-party agents: Hermes (``xai-oauth``) and
OpenClaw (``--provider xai --method oauth``) both log in with xAI's OAuth
device flow and then call ``api.x.ai/v1`` with the access token instead of an
API key. Jarvis does that login ONCE for all its agents, and the model gateway
(``gateway.py``) answers every Grok call of a Hermes / OpenClaw agent with it,
so the runtimes never hold the token.

The login is Jarvis' own, separate from the Grok CLI's (``~/.grok``): a second
holder of one refresh token could rotate it away from the CLI and sign the
person out there. The tokens live in the credential store (``set_secret``),
never in a file or ``jarvis.toml``.

Endpoints, client id and scopes follow xAI's published OAuth configuration as
used by Hermes Agent.
"""

# Portions adapted from NousResearch/hermes-agent @ 0e21933114
# (hermes_cli/auth_constants.py and auth_xai.py: the xAI OAuth client id,
# scopes, device-code and refresh requests), MIT License,
# Copyright (c) 2025 Nous Research. See third_party/hermes-agent/LICENSE.

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlparse

from jarvis.core.http_pool import SyncHttpClientPool

log = logging.getLogger(__name__)

__all__ = [
    "INFERENCE_BASE_URL",
    "DeviceLogin",
    "XaiLoginError",
    "access_token",
    "connected",
    "disconnect",
    "poll_device_login",
    "start_device_login",
    "status",
]

ISSUER: Final[str] = "https://auth.x.ai"
DISCOVERY_URL: Final[str] = f"{ISSUER}/.well-known/openid-configuration"
DEVICE_CODE_URL: Final[str] = f"{ISSUER}/oauth2/device/code"
CLIENT_ID: Final[str] = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPE: Final[str] = "openid profile email offline_access grok-cli:access api:access"
DEVICE_GRANT: Final[str] = "urn:ietf:params:oauth:grant-type:device_code"
INFERENCE_BASE_URL: Final[str] = "https://api.x.ai/v1"

#: The credential-store slot of the agents' xAI login (a JSON blob).
SECRET_SLOT: Final[str] = "xai_agents_oauth"  # noqa: S105 — a slot name, not a secret

#: Refresh this long before the access token (about six hours) runs out.
_REFRESH_SKEW_S: Final[float] = 3600.0

_FORM: Final[dict[str, str]] = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
}

_HTTP: Final = SyncHttpClientPool(timeout_s=20.0)
_LOCK = threading.Lock()


class XaiLoginError(RuntimeError):
    """The xAI login cannot answer; ``code`` says why, the message is user-facing."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DeviceLogin:
    """A device-code login waiting for the person to approve it in the browser."""

    device_code: str
    user_code: str
    verification_url: str
    expires_at: float
    interval_s: float

    def to_public(self) -> dict[str, Any]:
        """What the UI shows: never the device code itself."""
        return {
            "user_code": self.user_code,
            "verification_url": self.verification_url,
            "expires_in": max(0, int(self.expires_at - time.time())),
            "interval": self.interval_s,
        }


# ------------------------------------------------------------------ storage


def _load() -> dict[str, Any] | None:
    from jarvis.core.config import get_secret

    raw = get_secret(SECRET_SLOT, "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("xai login: the stored login is unreadable; treating it as signed out")
        return None
    return data if isinstance(data, dict) and data.get("refresh_token") else None


def _save(data: dict[str, Any]) -> None:
    from jarvis.core.config import set_secret

    if not set_secret(SECRET_SLOT, json.dumps(data)):
        raise XaiLoginError("The credential store refused the xAI login.", "store_failed")


def _jwt_exp(token: str) -> float | None:
    """The ``exp`` claim of a JWT access token, or ``None`` when it has none."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = claims.get("exp")
        return float(exp) if isinstance(exp, int | float) else None
    except (IndexError, ValueError, UnicodeDecodeError):
        return None


def _tokens(payload: dict[str, Any], *, fallback_refresh: str = "") -> dict[str, Any]:
    access = str(payload.get("access_token") or "").strip()
    refresh = str(payload.get("refresh_token") or fallback_refresh).strip()
    if not access or not refresh:
        raise XaiLoginError("xAI answered the login without the expected tokens.", "invalid_tokens")
    expires_in = payload.get("expires_in")
    expires_at = _jwt_exp(access) or (
        time.time() + float(expires_in) if isinstance(expires_in, int | float) else time.time()
    )
    return {"access_token": access, "refresh_token": refresh, "expires_at": expires_at}


# ------------------------------------------------------------------ endpoints


def _xai_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "x.ai" or host.endswith(".x.ai")):
        raise XaiLoginError("xAI's login configuration points outside x.ai.", "discovery_invalid")
    return url


def _token_endpoint() -> str:
    try:
        response = _HTTP.client().get(DISCOVERY_URL, headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001 — any transport failure is "xAI unreachable"
        raise XaiLoginError("xAI's login service is unreachable right now.", "unreachable") from exc
    if response.status_code != 200:
        raise XaiLoginError("xAI's login service is unreachable right now.", "unreachable")
    try:
        endpoint = str(response.json().get("token_endpoint") or "")
    except ValueError as exc:
        message = "xAI's login configuration is unreadable."
        raise XaiLoginError(message, "discovery_invalid") from exc
    return _xai_url(endpoint)


def _post(url: str, data: dict[str, str]) -> Any:
    try:
        return _HTTP.client().post(url, headers=_FORM, data=data)
    except Exception as exc:  # noqa: BLE001 — any transport failure is "xAI unreachable"
        raise XaiLoginError("xAI's login service is unreachable right now.", "unreachable") from exc


# ------------------------------------------------------------------ device login


def start_device_login() -> DeviceLogin:
    """Ask xAI for a device code the person approves in the browser. Blocking."""
    response = _post(DEVICE_CODE_URL, {"client_id": CLIENT_ID, "scope": SCOPE})
    if response.status_code != 200:
        raise XaiLoginError("xAI did not start the login. Try again in a moment.", "start_failed")
    try:
        body = response.json()
        return DeviceLogin(
            device_code=str(body["device_code"]),
            user_code=str(body["user_code"]),
            verification_url=_xai_url(
                str(body.get("verification_uri_complete") or body["verification_uri"])
            ),
            expires_at=time.time() + float(body["expires_in"]),
            interval_s=max(1.0, float(body.get("interval") or 5)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise XaiLoginError("xAI answered the login start unexpectedly.", "start_failed") from exc


def poll_device_login(login: DeviceLogin) -> bool:
    """One poll: ``True`` once approved (the login is stored), ``False`` while
    pending. Raises ``XaiLoginError`` when declined, expired or refused. Blocking."""
    if time.time() >= login.expires_at:
        raise XaiLoginError("The login code expired. Start the login again.", "expired")
    response = _post(
        _token_endpoint(),
        {"grant_type": DEVICE_GRANT, "client_id": CLIENT_ID, "device_code": login.device_code},
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 200:
        tokens = _tokens(body if isinstance(body, dict) else {})
        with _LOCK:
            _save({**tokens, "connected_at": time.time()})
        log.info("xai login: connected for Hermes / OpenClaw agents")
        return True
    error = str(body.get("error") or "") if isinstance(body, dict) else ""
    if error in {"authorization_pending", "slow_down"}:
        return False
    if error == "access_denied":
        raise XaiLoginError("The login was declined in the browser.", "declined")
    if error == "expired_token":
        raise XaiLoginError("The login code expired. Start the login again.", "expired")
    if response.status_code == 403:
        raise XaiLoginError(_TIER_DENIED, "tier_denied")
    raise XaiLoginError("xAI refused the login. Try again in a moment.", "refused")


_TIER_DENIED: Final[str] = (
    "xAI does not allow API use for this account's plan. A SuperGrok or X Premium+ "
    "subscription is needed; otherwise use an xAI API key."
)


# ------------------------------------------------------------------ tokens


def connected() -> bool:
    """A login is stored (it may still need a refresh). Blocking (keyring)."""
    return _load() is not None


def status() -> dict[str, Any]:
    """``{"connected": bool, "since": epoch | None}`` for the UI. Blocking (keyring)."""
    data = _load()
    return {
        "connected": data is not None,
        "since": data.get("connected_at") if data is not None else None,
    }


def access_token() -> str:
    """A live access token, refreshed when it runs out within the hour.

    Raises ``XaiLoginError`` when no login is stored or xAI refuses the
    refresh. Blocking (keyring, network on refresh): call it in a thread.
    """
    with _LOCK:
        data = _load()
        if data is None:
            raise XaiLoginError(
                "Grok's subscription is not connected for agents. Connect it in the New agent "
                "dialog or in Settings → API keys → xAI.",
                "not_connected",
            )
        if float(data.get("expires_at") or 0) - time.time() > _REFRESH_SKEW_S:
            return str(data["access_token"])
        response = _post(
            _token_endpoint(),
            {
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": str(data["refresh_token"]),
            },
        )
        if response.status_code == 403:
            raise XaiLoginError(_TIER_DENIED, "tier_denied")
        if response.status_code in {400, 401}:
            _forget()
            raise XaiLoginError(
                "The Grok login for agents expired. Connect it again.", "relogin"
            )
        if response.status_code != 200:
            raise XaiLoginError("xAI's login service is unreachable right now.", "unreachable")
        try:
            body = response.json()
        except ValueError as exc:
            raise XaiLoginError("xAI answered the refresh unexpectedly.", "invalid_tokens") from exc
        tokens = _tokens(body, fallback_refresh=str(data["refresh_token"]))
        _save({**data, **tokens})
        return str(tokens["access_token"])


def _forget() -> None:
    from jarvis.core.config import delete_secret

    if not delete_secret(SECRET_SLOT):
        log.warning("xai login: the stored login could not be removed")


def disconnect() -> None:
    """Forget the agents' xAI login. Blocking (keyring)."""
    with _LOCK:
        _forget()
