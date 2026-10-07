"""The Grok subscription for Hermes / OpenClaw agents: device login, refresh, routing."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx
import pytest

from jarvis.agent_runtimes import model_map, xai_login
from jarvis.core.http_pool import SyncHttpClientPool

TOKEN_URL = "https://auth.x.ai/oauth2/token"  # noqa: S105 — test fixture


def _jwt(exp: float) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"exp": int(exp)}).encode()).decode().rstrip("=")
    return f"h.{claims}.s"


class _Xai:
    """xAI's login service, answered in process."""

    def __init__(self) -> None:
        self.token_answers: list[tuple[int, dict[str, Any]]] = []
        self.posts: list[dict[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"token_endpoint": TOKEN_URL})
        form = dict(httpx.QueryParams(request.content.decode()))
        self.posts.append(form)
        if url.endswith("/oauth2/device/code"):
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-1",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://accounts.x.ai/device",
                    "verification_uri_complete": "https://accounts.x.ai/device?code=ABCD-EFGH",
                    "expires_in": 600,
                    "interval": 1,
                },
            )
        status, body = self.token_answers.pop(0)
        return httpx.Response(status, json=body)


@pytest.fixture
def xai(monkeypatch: pytest.MonkeyPatch) -> _Xai:
    from jarvis.core import config

    service = _Xai()
    monkeypatch.setattr(
        xai_login,
        "_HTTP",
        SyncHttpClientPool(timeout_s=5, transport=httpx.MockTransport(service.handler)),
    )
    store: dict[str, str] = {}
    monkeypatch.setattr(config, "get_secret", lambda key, env=None: store.get(key))
    monkeypatch.setattr(
        config, "set_secret", lambda key, value: store.__setitem__(key, value) or True
    )
    monkeypatch.setattr(
        config, "delete_secret", lambda key: store.pop(key, None) is not None or True
    )
    service.store = store  # type: ignore[attr-defined]
    return service


def test_the_device_login_stores_the_tokens_once_approved(xai: _Xai) -> None:
    login = xai_login.start_device_login()
    assert login.to_public()["user_code"] == "ABCD-EFGH"
    assert "device_code" not in login.to_public()
    assert xai.posts[0]["client_id"] == xai_login.CLIENT_ID
    xai.token_answers += [
        (400, {"error": "authorization_pending"}),
        (200, {"access_token": _jwt(time.time() + 6 * 3600), "refresh_token": "r1"}),
    ]
    assert xai_login.poll_device_login(login) is False
    assert xai_login.poll_device_login(login) is True
    assert xai_login.connected() and xai_login.status()["connected"]
    assert xai_login.access_token().startswith("h.")  # fresh: no refresh call
    assert len(xai.posts) == 3


def test_a_declined_login_and_a_plan_without_api_access_say_why(xai: _Xai) -> None:
    login = xai_login.start_device_login()
    xai.token_answers += [(400, {"error": "access_denied"}), (403, {"error": "forbidden"})]
    with pytest.raises(xai_login.XaiLoginError) as declined:
        xai_login.poll_device_login(login)
    assert declined.value.code == "declined"
    with pytest.raises(xai_login.XaiLoginError) as tier:
        xai_login.poll_device_login(login)
    assert tier.value.code == "tier_denied"
    assert not xai_login.connected()


def test_an_expiring_token_is_refreshed_and_a_dead_login_is_forgotten(xai: _Xai) -> None:
    xai.store[xai_login.SECRET_SLOT] = json.dumps(
        {
            "access_token": _jwt(time.time() + 60),
            "refresh_token": "r1",
            "expires_at": time.time() + 60,
        }
    )
    fresh = _jwt(time.time() + 6 * 3600)
    xai.token_answers.append((200, {"access_token": fresh, "refresh_token": "r2"}))
    assert xai_login.access_token() == fresh
    assert xai.posts[-1] == {
        "grant_type": "refresh_token",
        "client_id": xai_login.CLIENT_ID,
        "refresh_token": "r1",
    }
    assert json.loads(xai.store[xai_login.SECRET_SLOT])["refresh_token"] == "r2"  # noqa: S105 — test fixture
    stored = json.loads(xai.store[xai_login.SECRET_SLOT])
    xai.store[xai_login.SECRET_SLOT] = json.dumps({**stored, "expires_at": time.time()})
    xai.token_answers.append((401, {"error": "invalid_grant"}))
    with pytest.raises(xai_login.XaiLoginError) as dead:
        xai_login.access_token()
    assert dead.value.code == "relogin" and not xai_login.connected()


def test_no_login_means_not_connected(xai: _Xai) -> None:
    with pytest.raises(xai_login.XaiLoginError) as missing:
        xai_login.access_token()
    assert missing.value.code == "not_connected"


def test_the_grok_subscription_is_a_gateway_seat_listed_with_its_reason(
    xai: _Xai, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "grok-build" in model_map.subscription_providers()
    monkeypatch.setattr(model_map, "claude_login_token", lambda: None)
    blocked = model_map.access_blocked()
    assert blocked["grok-build"] == {"subscription": "xai_login_needed"}
    assert blocked["antigravity"] == {"subscription": "vendor_forbids_subscription"}
    with pytest.raises(model_map.RouteUnavailable):
        model_map._checked_model(None, "grok-build", "")
    xai.store[xai_login.SECRET_SLOT] = json.dumps(
        {"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 9000}
    )
    assert "grok-build" not in model_map.access_blocked()
    assert model_map._checked_model(None, "grok-build", "grok-4.7") == "grok-4.7"


def test_the_gateway_answers_the_grok_subscription_with_the_agents_token() -> None:
    from jarvis.agent_runtimes import gateway
    from jarvis.plugins.brain.grok import GrokBrain

    brain = gateway._new_brain("grok-build", "grok-4.7", "token-1")
    inner = getattr(brain, "_brain", None) or getattr(brain, "inner", None) or brain
    assert isinstance(inner, GrokBrain) and inner._auth_token == "token-1"  # noqa: S105 — test fixture
    assert gateway._catalog_provider("grok-build") == "grok"
