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
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": True,
            "connected": True,
            "mode": "chatgpt",
            "message": "Dedicated ChatGPT subscription voice login is ready.",
            "reason_code": "ready",
        },
    )
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

    # The route judges this provider by the isolated-profile snapshot, never
    # the ordinary Codex login. An api-key-mode login yields no dedicated
    # ChatGPT-authenticated profile, so the snapshot answers not-connected.
    # Patching the payload keeps this unit test off the real CLI and
    # independent of the host's login state.
    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": True,
            "connected": False,
            "mode": "not_connected",
            "message": "Connect the dedicated ChatGPT subscription voice login.",
            "reason_code": "login_required",
        },
    )

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
    # The precise payload diagnosis, never the generic credential sentence.
    assert "dedicated ChatGPT subscription voice login" in str(caught.value.detail)


@pytest.mark.asyncio
async def test_plan_unsupported_activation_retries_the_live_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plan_unsupported must reach verify_activation (the only path that can
    CLEAR the sticky block) and answer with the plan diagnosis on re-refusal."""
    from jarvis import codex_app_server
    from jarvis.codex_app_server import CodexSubscriptionPlanUnsupported

    monkeypatch.setattr(routes, "_codex_binary_path", lambda *_args: "codex")
    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": True,
            "connected": False,
            "mode": "not_connected",
            "message": "Subscription voice permits only personal ChatGPT accounts.",
            "reason_code": "plan_unsupported",
        },
    )

    gate_calls: list[object] = []
    close_calls: list[object] = []

    class _Client:
        async def require_chatgpt_login(self) -> None:
            gate_calls.append(self)
            raise CodexSubscriptionPlanUnsupported(
                "Subscription voice permits only personal ChatGPT accounts."
            )

        async def close(self) -> None:
            close_calls.append(self)

    monkeypatch.setattr(
        codex_app_server,
        "get_shared_codex_app_server",
        lambda *_args, **_kwargs: _Client(),
    )

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=None, cfg=None)))
    with pytest.raises(HTTPException) as caught:
        await routes.realtime_switch(
            routes.SwitchBody(
                provider="codex-subscription-realtime",
                persist=False,
                accept_experimental=True,
            ),
            request,
        )

    assert gate_calls, "the realtime provider gate must re-judge a plan-blocked account"
    assert close_calls == gate_calls
    assert caught.value.status_code == 409
    detail = str(caught.value.detail)
    assert "personal ChatGPT accounts" in detail
    assert "no configured credentials" not in detail


@pytest.mark.asyncio
async def test_realtime_activation_reports_busy_instead_of_demanding_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient busy window must not tell the user to redo a working login."""
    release_calls: list[str] = []

    async def release_previous_transport() -> None:
        release_calls.append("released")

    monkeypatch.setattr(routes, "_codex_binary_path", lambda *_args: "codex")
    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": False,
            "connected": False,
            "mode": "not_connected",
            "message": "Dedicated subscription voice status is being checked or changed.",
            "reason_code": "busy",
        },
    )
    monkeypatch.setattr(
        routes,
        "_release_codex_transports_for_voice_switch",
        release_previous_transport,
    )

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
    detail = str(caught.value.detail)
    assert "being checked" in detail
    assert "Connect or configure" not in detail
    assert release_calls == ["released"]


@pytest.mark.asyncio
async def test_realtime_activation_reclaims_a_warm_transport_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis import codex_app_server

    snapshots = [
        {
            "installed": True,
            "connected": False,
            "mode": "not_connected",
            "message": "Subscription voice is busy.",
            "reason_code": "busy",
        },
        {
            "installed": True,
            "connected": True,
            "mode": "chatgpt",
            "message": "ready",
            "reason_code": "ready",
        },
    ]
    release_calls: list[str] = []

    async def release_previous_transport() -> None:
        release_calls.append("released")

    class _Client:
        async def require_chatgpt_login(self) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: snapshots.pop(0),
    )
    monkeypatch.setattr(
        routes,
        "_release_codex_transports_for_voice_switch",
        release_previous_transport,
    )
    monkeypatch.setattr(
        codex_app_server,
        "get_shared_codex_app_server",
        lambda *_args, **_kwargs: _Client(),
    )
    monkeypatch.setattr(
        codex_app_server,
        "set_codex_subscription_activation_block",
        lambda _value: None,
    )
    config = SimpleNamespace(
        codex=SimpleNamespace(binary_path="codex"),
        voice=SimpleNamespace(profile="", mode="realtime"),
        brain=SimpleNamespace(realtime=None),
    )

    response = await routes.realtime_switch(
        routes.SwitchBody(
            provider="codex-subscription-realtime",
            persist=False,
            accept_experimental=True,
        ),
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config))),
    )

    assert response["profile"] == ""
    assert response["mode"] == "realtime"
    assert release_calls == ["released"]
    assert snapshots == []


def test_spec_payload_preserves_the_isolated_snapshot_diagnosis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The precise setup diagnosis must survive into /api/providers."""
    spec = get_spec("codex-subscription-realtime")
    assert spec is not None
    monkeypatch.setattr(routes.cfg_mod, "get_secret", lambda *_args, **_kwargs: None)

    payload = routes._spec_to_payload(
        spec,
        active_brain=None,
        active_tts=None,
        active_stt=None,
        active_realtime=None,
        codex_subscription_ready=False,
        codex_subscription_status={
            "installed": True,
            "connected": False,
            "mode": "not_connected",
            "message": "Subscription voice requires Codex CLI codex-cli 0.146.0.",
            "reason_code": "setup_invalid",
        },
    )

    assert payload["codex_status"]["reason_code"] == "setup_invalid"
    assert (
        payload["codex_status"]["message"]
        == "Subscription voice requires Codex CLI codex-cli 0.146.0."
    )


def test_subscription_status_payload_maps_busy_without_claiming_missing_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis import codex_app_server

    class _PresenceService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def _resolve_binary(self) -> str:
            return "codex"

    snapshot = codex_app_server.CodexAppServerCapability(
        available=False,
        chatgpt_authenticated=False,
        binary_path=None,
        version=None,
        reason="Dedicated subscription voice status is being checked or changed.",
        reason_code="busy",
    )
    monkeypatch.setattr(
        codex_app_server,
        "codex_subscription_auth_snapshot",
        lambda _binary_path: snapshot,
    )
    monkeypatch.setattr(routes, "CodexAuthService", _PresenceService)

    payload = routes._codex_subscription_status_payload(None)

    assert payload["reason_code"] == "busy"
    assert payload["connected"] is False
    # The name of this test is a promise: busy must not read as a missing CLI.
    assert payload["installed"] is True
    assert payload["message"] == (
        "Dedicated subscription voice status is being checked or changed."
    )


def test_spec_payload_busy_does_not_claim_missing_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """During busy the card must not render the install prompt via cli_installed."""
    spec = get_spec("codex-subscription-realtime")
    assert spec is not None
    monkeypatch.setattr(routes.cfg_mod, "get_secret", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "_cli_installed", lambda _spec: True)

    payload = routes._spec_to_payload(
        spec,
        active_brain=None,
        active_tts=None,
        active_stt=None,
        active_realtime=None,
        codex_subscription_ready=False,
        codex_subscription_status={
            "installed": False,
            "connected": False,
            "mode": "not_connected",
            "message": "Dedicated subscription voice status is being checked or changed.",
            "reason_code": "busy",
        },
    )

    assert payload["codex_status"]["reason_code"] == "busy"
    assert payload["cli_installed"] is True


def test_status_payload_survives_a_raising_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising probe must degrade to busy, not 500 the whole providers screen."""
    from jarvis import codex_app_server

    def explode(_binary_path: str | None) -> None:
        raise OSError("cli exploded")

    monkeypatch.setattr(codex_app_server, "codex_subscription_auth_snapshot", explode)

    payload = routes._codex_subscription_status_payload(None)

    assert payload["reason_code"] == "busy"
    assert payload["connected"] is False
    assert "OSError" in payload["message"]


@pytest.mark.asyncio
async def test_section_health_reports_busy_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient busy window is not an amber "needs setup" dot."""
    spec = get_spec("codex-subscription-realtime")
    assert spec is not None
    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": False,
            "connected": False,
            "mode": "not_connected",
            "message": "Dedicated subscription voice status is being checked or changed.",
            "reason_code": "busy",
        },
    )

    health = await routes._tier_section_health(
        SimpleNamespace(),
        spec,
        binary_path="codex-custom",
    )

    assert health.status == "unknown"
    assert health.reason == "busy"


@pytest.mark.asyncio
async def test_tier_test_judges_the_isolated_voice_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Test button must never answer from the ordinary Codex login."""
    spec = get_spec("codex-subscription-realtime")
    assert spec is not None
    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": True,
            "connected": True,
            "mode": "chatgpt",
            "message": "Dedicated ChatGPT subscription voice login is ready.",
            "reason_code": "ready",
        },
    )

    captured: dict[str, object] = {}

    async def capture_run(spec_arg, cfg_arg, **kwargs):
        captured.update(kwargs)
        status_fn = kwargs.get("codex_status")
        assert callable(status_fn)
        return SimpleNamespace(status="ok", detail=status_fn().message)

    monkeypatch.setattr(routes._provider_test, "run_provider_test", capture_run)

    result = await routes._run_tier_test(spec, SimpleNamespace())

    assert result.status == "ok"
    assert "subscription voice login is ready" in result.detail
    assert "codex_status" in captured


def test_login_window_keeps_the_cli_reported_as_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """During the minutes-long interactive login the snapshot is silent about
    the CLI; the card must not tell the user to reinstall mid-login."""
    from jarvis import codex_app_server

    class _PresenceService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def _resolve_binary(self) -> str:
            return "codex"

    snapshot = codex_app_server.CodexAppServerCapability(
        available=False,
        chatgpt_authenticated=False,
        binary_path=None,
        version=None,
        reason="Dedicated ChatGPT subscription login is in progress.",
        reason_code="login_in_progress",
    )
    monkeypatch.setattr(
        codex_app_server,
        "codex_subscription_auth_snapshot",
        lambda _binary_path: snapshot,
    )
    monkeypatch.setattr(routes, "CodexAuthService", _PresenceService)

    payload = routes._codex_subscription_status_payload(None)

    assert payload["reason_code"] == "login_in_progress"
    assert payload["installed"] is True


def test_plan_block_turns_a_ready_login_into_plan_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a refused activation, surfaces must stop claiming ready."""
    from jarvis import codex_app_server

    snapshot = codex_app_server.CodexAppServerCapability(
        available=True,
        chatgpt_authenticated=True,
        binary_path="codex",
        version="codex-cli 0.146.0",
        reason="Dedicated ChatGPT login is available.",
        reason_code="ready",
    )
    monkeypatch.setattr(
        codex_app_server,
        "codex_subscription_auth_snapshot",
        lambda _binary_path: snapshot,
    )
    monkeypatch.setattr(
        codex_app_server,
        "_subscription_activation_block",
        "Subscription voice permits only personal ChatGPT accounts.",
    )

    payload = routes._codex_subscription_status_payload(None)

    assert payload["reason_code"] == "plan_unsupported"
    assert payload["connected"] is False
    assert "personal ChatGPT accounts" in payload["message"]


def test_lifecycle_unavailable_does_not_claim_missing_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsupported OS must not render the install prompt for a CLI that
    would not help anyway."""
    from jarvis import codex_app_server

    class _PresenceService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def _resolve_binary(self) -> str:
            return "codex"

    snapshot = codex_app_server.CodexAppServerCapability(
        available=False,
        chatgpt_authenticated=False,
        binary_path=None,
        version=None,
        reason="This operating-system architecture is not approved.",
        reason_code="lifecycle_unavailable",
    )
    monkeypatch.setattr(
        codex_app_server,
        "codex_subscription_auth_snapshot",
        lambda _binary_path: snapshot,
    )
    monkeypatch.setattr(routes, "CodexAuthService", _PresenceService)

    payload = routes._codex_subscription_status_payload(None)

    assert payload["reason_code"] == "lifecycle_unavailable"
    assert payload["installed"] is True


def test_wrong_release_payload_shows_the_install_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong codex release must surface the install flow, not a profile
    warning: installed=False drives the pinned npm command on the card."""
    from jarvis import codex_app_server

    snapshot = codex_app_server.CodexAppServerCapability(
        available=False,
        chatgpt_authenticated=False,
        binary_path="codex",
        version="codex-cli 0.150.0",
        reason="Subscription voice requires Codex CLI 0.146.0.",
        reason_code="not_installed",
    )
    monkeypatch.setattr(
        codex_app_server,
        "codex_subscription_auth_snapshot",
        lambda _binary_path: snapshot,
    )

    payload = routes._codex_subscription_status_payload(None)

    assert payload["reason_code"] == "not_installed"
    assert payload["installed"] is False
    assert "requires Codex CLI 0.146.0" in payload["message"]


@pytest.mark.asyncio
async def test_tier_test_reports_busy_as_transient_not_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Test button must not issue a setup verdict for a transient window."""
    spec = get_spec("codex-subscription-realtime")
    assert spec is not None
    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": True,
            "connected": False,
            "mode": "not_connected",
            "message": "Dedicated subscription voice status is being checked or changed.",
            "reason_code": "busy",
        },
    )

    result = await routes._run_tier_test(spec, SimpleNamespace())

    assert result.status == "rate_limited"
    assert "being checked" in result.detail


@pytest.mark.asyncio
async def test_tier_test_reports_login_in_progress_as_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """During a running login the Test button must not dissent from the card."""
    spec = get_spec("codex-subscription-realtime")
    assert spec is not None
    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": True,
            "connected": False,
            "mode": "not_connected",
            "message": "Dedicated ChatGPT subscription login is in progress.",
            "reason_code": "login_in_progress",
        },
    )

    result = await routes._run_tier_test(spec, SimpleNamespace())

    assert result.status == "rate_limited"
    assert "login is in progress" in result.detail


@pytest.mark.asyncio
async def test_section_health_reports_login_in_progress_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = get_spec("codex-subscription-realtime")
    assert spec is not None
    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": True,
            "connected": False,
            "mode": "not_connected",
            "message": "Dedicated ChatGPT subscription login is in progress.",
            "reason_code": "login_in_progress",
        },
    )

    health = await routes._tier_section_health(SimpleNamespace(), spec, binary_path="codex-custom")

    assert health.status == "unknown"
    assert health.reason == "login_in_progress"


@pytest.mark.parametrize(
    ("reason_code", "message", "expected_fragment"),
    [
        (
            "plan_unsupported",
            "Subscription voice permits only personal ChatGPT accounts.",
            "personal ChatGPT accounts",
        ),
        (
            "lifecycle_unavailable",
            "Interactive subscription-voice login is unavailable on headless Linux.",
            "headless Linux",
        ),
        (
            "not_installed",
            "Subscription voice requires Codex CLI 0.146.0.",
            "requires Codex CLI 0.146.0",
        ),
    ],
)
@pytest.mark.asyncio
async def test_section_health_hover_names_the_real_remedy(
    monkeypatch: pytest.MonkeyPatch,
    reason_code: str,
    message: str,
    expected_fragment: str,
) -> None:
    """The amber dot's hover must never say "not connected or configured"
    when the actual remedy is a plan change, another host, or an install."""
    spec = get_spec("codex-subscription-realtime")
    assert spec is not None
    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": True,
            "connected": False,
            "mode": "not_connected",
            "message": message,
            "reason_code": reason_code,
        },
    )

    health = await routes._tier_section_health(SimpleNamespace(), spec, binary_path="codex-custom")

    assert health.status == "needs_setup"
    assert health.reason == reason_code
    assert expected_fragment in health.detail
    assert "not connected or configured" not in health.detail


@pytest.mark.asyncio
async def test_realtime_activation_asks_to_finish_a_running_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "_codex_binary_path", lambda *_args: "codex")
    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": True,
            "connected": False,
            "mode": "not_connected",
            "message": "Dedicated ChatGPT subscription login is in progress.",
            "reason_code": "login_in_progress",
        },
    )

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
    assert "Finish" in str(caught.value.detail)


def test_install_hint_pins_the_supported_release() -> None:
    """The card's install command must name the pinned release — a silent
    unpin would send users to a version the trust gate rejects."""
    spec = get_spec("codex-subscription-realtime")
    assert spec is not None
    assert spec.install_hint == "npm i -g @openai/codex@0.147.0"


def test_reason_code_vocabulary_is_pinned() -> None:
    """The five-layer enum contract: this list mirrors the TS union in
    useProviders.ts and the card mapping in ProviderTierSection.tsx — grow
    them together or the UI silently misrenders a new backend state."""
    from typing import get_args, get_type_hints

    from jarvis.codex_app_server import CodexAppServerCapability

    hints = get_type_hints(CodexAppServerCapability)
    assert set(get_args(hints["reason_code"])) == {
        "ready",
        "login_required",
        "login_in_progress",
        "lifecycle_unavailable",
        "not_installed",
        "setup_invalid",
        "plan_unsupported",
        "busy",
    }


@pytest.mark.asyncio
async def test_subscription_realtime_activation_preserves_realtime_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis import codex_app_server
    from jarvis.core import config_writer

    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": True,
            "connected": True,
            "mode": "chatgpt",
            "message": "ready",
            "reason_code": "ready",
        },
    )

    close_calls: list[object] = []
    release_calls: list[str] = []

    async def release_previous_transport() -> None:
        release_calls.append("released")

    class _Client:
        async def require_chatgpt_login(self) -> None:
            return None

        async def close(self) -> None:
            close_calls.append(self)

    monkeypatch.setattr(
        codex_app_server,
        "get_shared_codex_app_server",
        lambda *_args, **_kwargs: _Client(),
    )
    monkeypatch.setattr(
        codex_app_server,
        "set_codex_subscription_activation_block",
        lambda _value: None,
    )
    monkeypatch.setattr(
        routes,
        "_release_codex_transports_for_voice_switch",
        release_previous_transport,
    )
    persisted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer,
        "set_realtime_provider",
        lambda provider: persisted.append(("provider", provider)),
    )
    config = SimpleNamespace(
        codex=SimpleNamespace(binary_path="codex"),
        voice=SimpleNamespace(profile="", mode="realtime"),
        brain=SimpleNamespace(realtime=None),
    )
    response = await routes.realtime_switch(
        routes.SwitchBody(
            provider="codex-subscription-realtime",
            persist=True,
            accept_experimental=True,
        ),
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config))),
    )

    assert response["profile"] == ""
    assert response["mode"] == "realtime"
    assert config.voice.mode == "realtime"
    assert config.brain.realtime.provider == "codex-subscription-realtime"
    assert close_calls == []
    assert release_calls == ["released"]
    assert persisted == [("provider", "codex-subscription-realtime")]


@pytest.mark.asyncio
async def test_subscription_realtime_activation_repairs_the_old_pipeline_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis import codex_app_server
    from jarvis.core import config_writer

    monkeypatch.setattr(
        routes,
        "_codex_subscription_status_payload",
        lambda _binary_path: {
            "installed": True,
            "connected": True,
            "mode": "chatgpt",
            "message": "ready",
            "reason_code": "ready",
        },
    )

    class _Client:
        async def require_chatgpt_login(self) -> None:
            return None

    monkeypatch.setattr(
        codex_app_server,
        "get_shared_codex_app_server",
        lambda *_args, **_kwargs: _Client(),
    )
    monkeypatch.setattr(
        codex_app_server,
        "set_codex_subscription_activation_block",
        lambda _value: None,
    )

    async def release_previous_transport() -> None:
        return None

    monkeypatch.setattr(
        routes,
        "_release_codex_transports_for_voice_switch",
        release_previous_transport,
    )

    writes: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        config_writer,
        "set_realtime_voice_selection",
        lambda provider, *, profile, mode: writes.append(
            (provider, profile, mode)
        ),
    )
    config = SimpleNamespace(
        codex=SimpleNamespace(binary_path="codex"),
        voice=SimpleNamespace(profile="codex-subscription-voice", mode="pipeline"),
        brain=SimpleNamespace(realtime=SimpleNamespace(provider="openai-realtime")),
    )

    response = await routes.realtime_switch(
        routes.SwitchBody(
            provider="codex-subscription-realtime",
            persist=True,
            accept_experimental=True,
        ),
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config))),
    )

    assert response["mode"] == "realtime"
    assert response["profile"] == ""
    assert config.voice.mode == "realtime"
    assert config.voice.profile == ""
    assert config.brain.realtime.provider == "codex-subscription-realtime"
    assert writes == [("codex-subscription-realtime", "", "realtime")]


@pytest.mark.asyncio
async def test_switching_away_releases_the_subscription_voice_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.core.registry as registry

    spec = SimpleNamespace(
        id="replacement-realtime",
        tier="realtime",
        experimental=False,
    )
    release_calls: list[str] = []

    class Provider:
        requires_webrtc_offer = False

        @classmethod
        async def verify_activation(cls, _cfg: object) -> None:
            return None

    async def credential_present(*_args, **_kwargs) -> bool:
        return True

    async def release_previous_transport() -> None:
        release_calls.append("released")

    monkeypatch.setattr(routes, "get_spec", lambda _provider: spec)
    monkeypatch.setattr(
        routes,
        "_provider_credential_present_async",
        credential_present,
    )
    monkeypatch.setattr(registry, "load", lambda *_args, **_kwargs: Provider)
    monkeypatch.setattr(
        routes,
        "_release_codex_transports_for_voice_switch",
        release_previous_transport,
    )
    config = SimpleNamespace(
        voice=SimpleNamespace(
            profile="codex-subscription-voice",
            mode="pipeline",
        ),
        brain=SimpleNamespace(
            realtime=SimpleNamespace(provider="codex-subscription-realtime")
        ),
    )

    response = await routes.realtime_switch(
        routes.SwitchBody(provider=spec.id, persist=False),
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config))),
    )

    assert response["active"] == spec.id
    assert release_calls == ["released"]
    assert config.voice.profile == ""


@pytest.mark.asyncio
async def test_voice_switch_release_closes_shared_codex_transports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis import codex_app_server

    calls: list[str] = []

    async def close_shared() -> None:
        calls.append("closed")

    monkeypatch.setattr(
        codex_app_server,
        "close_shared_codex_app_servers",
        close_shared,
    )

    await routes._release_codex_transports_for_voice_switch()

    assert calls == ["closed"]


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
        (False, False, None, "busy"),
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
