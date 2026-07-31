from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.ui.web import provider_routes as routes
from jarvis.ui.web.provider_spec import get_spec, provider_billing


class _Status:
    def __init__(self, *, connected: bool, mode: str) -> None:
        self.connected = connected
        self.mode = mode

    def to_dict(self) -> dict[str, object]:
        return {
            "installed": True,
            "connected": self.connected,
            "mode": self.mode,
            "message": "Codex status",
            "version": "1.0",
        }


def test_codex_subscription_realtime_spec_has_no_api_key_path() -> None:
    spec = get_spec("codex-subscription-realtime")

    assert spec is not None
    assert spec.tier == "realtime"
    assert spec.auth_mode == "codex"
    assert spec.secret_keys == ()
    assert provider_billing(spec) == "subscription"
    assert spec.experimental is True


@pytest.mark.parametrize(
    ("connected", "mode", "configured"),
    [
        (True, "chatgpt", True),
        (True, "api_key", False),
        (False, "not_connected", False),
    ],
)
def test_keyless_codex_readiness_requires_chatgpt_login(
    monkeypatch: pytest.MonkeyPatch,
    connected: bool,
    mode: str,
    configured: bool,
) -> None:
    spec = get_spec("codex-subscription-realtime")
    assert spec is not None

    class _Service:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def status(self) -> _Status:
            return _Status(connected=connected, mode=mode)

    monkeypatch.setattr(routes, "CodexAuthService", _Service)
    monkeypatch.setattr(routes, "_codex_binary_path", lambda *_args: "codex")
    monkeypatch.setattr(routes.cfg_mod, "get_secret", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        routes,
        "_is_credential_present",
        lambda *_args, **_kwargs: pytest.fail("keyless Codex must use login status"),
    )

    payload = routes._spec_to_payload(
        spec,
        active_brain=None,
        active_tts=None,
        active_stt=None,
        active_realtime=spec.id if configured else None,
    )

    assert payload["configured"] is configured
    assert payload["billing"] == "subscription"
    assert payload["codex_status"]["mode"] == mode
    assert payload["cli_installed"] is True
    assert "codex_brain_ready" not in payload


def test_authoritative_subscription_probe_overrides_stale_chatgpt_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = get_spec("codex-subscription-realtime")
    assert spec is not None

    class _Service:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def status(self) -> _Status:
            return _Status(connected=True, mode="chatgpt")

    monkeypatch.setattr(routes, "CodexAuthService", _Service)
    monkeypatch.setattr(routes, "_codex_binary_path", lambda *_args: "codex")
    monkeypatch.setattr(routes.cfg_mod, "get_secret", lambda *_args, **_kwargs: None)

    payload = routes._spec_to_payload(
        spec,
        active_brain=None,
        active_tts=None,
        active_stt=None,
        active_realtime=None,
        codex_subscription_ready=False,
    )

    assert payload["configured"] is False
    assert payload["codex_status"]["connected"] is False
    assert payload["codex_status"]["mode"] == "not_connected"
    assert "not ready" in payload["codex_status"]["message"]


@pytest.mark.asyncio
async def test_section_health_accepts_keyring_only_chatgpt_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis import codex_app_server

    spec = get_spec("codex-subscription-realtime")
    assert spec is not None

    async def ready(_binary_path: str | None = None) -> bool:
        return True

    async def healthy_probe(*_args, **_kwargs):
        return SimpleNamespace(status="ok", detail="subscription reachable")

    monkeypatch.setattr(codex_app_server, "codex_subscription_login_ready", ready)
    monkeypatch.setattr(routes, "_run_tier_test", healthy_probe)
    monkeypatch.setattr(
        routes,
        "_is_credential_present",
        lambda *_args, **_kwargs: pytest.fail(
            "section health must use the authoritative app-server auth probe"
        ),
    )

    health = await routes._realtime_section_health(
        SimpleNamespace(),
        spec,
        binary_path="codex-custom",
    )

    assert health.status == "ok"
    assert health.reason == "ok"
    assert health.subject_id == "codex-subscription-realtime"


@pytest.mark.asyncio
async def test_realtime_activation_rejects_codex_api_key_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Service:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def status(self) -> _Status:
            return _Status(connected=True, mode="api_key")

    monkeypatch.setattr(routes, "CodexAuthService", _Service)
    monkeypatch.setattr(routes, "_codex_binary_path", lambda *_args: "codex")

    with pytest.raises(HTTPException) as caught:
        await routes.realtime_switch(
            routes.SwitchBody(
                provider="codex-subscription-realtime",
                persist=False,
                accept_experimental=True,
            ),
            object(),
        )

    assert caught.value.status_code == 409
    assert "Connect or configure this provider first" in str(caught.value.detail)


@pytest.mark.asyncio
async def test_realtime_activation_requires_experimental_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_probe(*_args, **_kwargs) -> bool:
        pytest.fail("the experimental acknowledgement must be checked first")

    monkeypatch.setattr(
        routes,
        "_provider_credential_present_async",
        unexpected_probe,
    )

    with pytest.raises(HTTPException) as caught:
        await routes.realtime_switch(
            routes.SwitchBody(
                provider="codex-subscription-realtime",
                persist=False,
            ),
            object(),
        )

    assert caught.value.status_code == 409
    assert "Explicitly acknowledge" in str(caught.value.detail)


@pytest.mark.asyncio
async def test_realtime_activation_uses_provider_capability_not_provider_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.core.registry as registry

    config = SimpleNamespace(marker="active-config")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))
    spec = SimpleNamespace(
        id="arbitrary-subscription-transport",
        tier="realtime",
        experimental=False,
    )
    verified: list[object] = []

    class Provider:
        @classmethod
        async def verify_activation(cls, cfg: object) -> None:
            verified.append(cfg)
            raise RuntimeError("provider capability rejected activation")

    async def credential_present(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(routes, "get_spec", lambda _provider: spec)
    monkeypatch.setattr(
        routes,
        "_provider_credential_present_async",
        credential_present,
    )
    monkeypatch.setattr(registry, "load", lambda *_args, **_kwargs: Provider)

    with pytest.raises(HTTPException) as caught:
        await routes.realtime_switch(
            routes.SwitchBody(
                provider=spec.id,
                persist=False,
            ),
            request,
        )

    assert caught.value.status_code == 409
    assert "capability rejected" in str(caught.value.detail)
    assert verified == [config]


def _request_with_codex_path() -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(codex=SimpleNamespace(binary_path="codex-custom"))
            )
        )
    )


@pytest.mark.asyncio
async def test_subscription_voice_status_uses_isolated_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "installed": True,
        "connected": True,
        "mode": "chatgpt",
        "message": "ready",
    }
    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda binary_path: expected | {"binary_path": binary_path},
    )

    result = await routes.codex_subscription_voice_status(_request_with_codex_path())

    assert result["connected"] is True
    assert result["binary_path"] == "codex-custom"


@pytest.mark.parametrize(
    ("available", "authenticated", "version", "reason_code"),
    [
        (True, True, "codex-cli 0.146.0", "ready"),
        (True, False, "codex-cli 0.146.0", "login_required"),
        (False, False, "codex-cli 0.146.0", "login_required"),
        (False, False, None, "not_installed"),
        (False, False, "codex-cli 0.147.0", "setup_invalid"),
    ],
)
def test_subscription_status_has_stable_reason_codes(
    monkeypatch: pytest.MonkeyPatch,
    available: bool,
    authenticated: bool,
    version: str | None,
    reason_code: str,
) -> None:
    from jarvis import codex_app_server

    snapshot = codex_app_server.CodexAppServerCapability(
        available=available,
        chatgpt_authenticated=authenticated,
        binary_path="codex" if version is not None else None,
        version=version,
        reason="setup result",
        reason_code=reason_code,
    )
    monkeypatch.setattr(
        codex_app_server,
        "codex_subscription_auth_snapshot",
        lambda _binary_path: snapshot,
    )

    payload = routes._codex_subscription_status_payload(None)

    assert payload["reason_code"] == reason_code


@pytest.mark.asyncio
async def test_subscription_voice_login_uses_dedicated_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis import codex_app_server

    calls: list[str | None] = []
    monkeypatch.setattr(
        codex_app_server,
        "start_codex_subscription_login",
        lambda binary_path=None: calls.append(binary_path) or SimpleNamespace(pid=731),
    )

    result = await routes.codex_subscription_voice_login(_request_with_codex_path())

    assert calls == ["codex-custom"]
    assert result["pid"] == 731


@pytest.mark.asyncio
async def test_subscription_voice_logout_uses_atomic_backend_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis import codex_app_server

    calls: list[str | None] = []

    async def disconnect_and_logout(binary_path=None):
        calls.append(binary_path)
        return True, None

    monkeypatch.setattr(
        codex_app_server,
        "disconnect_and_logout_codex_subscription",
        disconnect_and_logout,
    )

    result = await routes.codex_subscription_voice_logout(_request_with_codex_path())

    assert calls == ["codex-custom"]
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_subscription_voice_logout_does_not_parse_exception_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis import codex_app_server

    async def malformed_profile(_binary_path=None):
        raise codex_app_server.CodexSubscriptionUnavailable(
            "The profile has not been created but is also malformed."
        )

    monkeypatch.setattr(
        codex_app_server,
        "disconnect_and_logout_codex_subscription",
        malformed_profile,
    )

    with pytest.raises(HTTPException) as caught:
        await routes.codex_subscription_voice_logout(_request_with_codex_path())

    assert caught.value.status_code == 409


def test_subscription_voice_logout_is_marked_dangerous_in_openapi() -> None:
    route = next(
        route
        for route in routes.router.routes
        if getattr(route, "path", None) == "/api/codex/subscription-voice/logout"
    )

    assert route.openapi_extra == {"x-jarvis-dangerous": True}
