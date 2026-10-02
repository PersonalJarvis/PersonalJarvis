"""Route tests for the just-in-time permission flows: request, open settings, reset.

The REAL ``PermissionService`` runs on a REAL ``SystemPermissionPort`` that sits on
``FakeTCC`` (``tests/fakes/fake_tcc.py``), a stateful MODEL of macOS privacy, behind
a real FastAPI ``TestClient``. The FakeTCC call log is the proof of what macOS was
(not) asked. Nothing here ran on a real Mac: every macOS behaviour is a model, and
the fidelity ledger in ``fake_tcc.py`` says which parts Apple documents.

The status snapshot and the single-row read have their own file
(``test_permissions_snapshot.py``). Time for the rate limiter is injected, so no
test waits for a cooldown.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.platform.permission_service import get_permission_service
from jarvis.platform.permissions import PermissionId
from jarvis.ui.web import permissions_routes as routes
from jarvis.ui.web.control_auth import require_control_key_or_session
from jarvis.ui.web.permissions_routes import router
from tests.fakes.fake_permission_service import FakePermissionService
from tests.fakes.fake_tcc import DialogPolicy, FakeTCC, install_port

_MUSIC = "com.apple.Music"
_SPOTIFY = "com.spotify.client"


class _Clock:
    """A monotonic clock a test advances; the rate limiter reads it."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Env:
    client: TestClient
    tcc: FakeTCC
    app: FastAPI
    clock: _Clock

    def post(self, path: str, **kwargs):
        return self.client.post(f"/api/permissions/{path}", **kwargs)


@pytest.fixture
def make_env(monkeypatch: pytest.MonkeyPatch):
    def build(
        *,
        platform: str = "darwin",
        service: object | None = None,
        config: object | None = None,
        **tcc_kwargs,
    ) -> Env:
        tcc = FakeTCC(**tcc_kwargs)
        install_port(monkeypatch, tcc.port(platform))  # type: ignore[arg-type]
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_control_key_or_session] = lambda: None
        clock = _Clock()
        runtime = routes._Runtime()
        runtime.limiter = routes._RateLimiter(clock=clock)
        app.state.permissions_runtime = runtime
        if service is not None:
            app.state.permission_service = service
        if config is not None:
            app.state.config = config
        return Env(TestClient(app), tcc, app, clock)

    return build


def _use_fake_tccutil(monkeypatch: pytest.MonkeyPatch, tcc: FakeTCC) -> None:
    """``tccutil`` is a subprocess; the port imports ``subprocess`` lazily, so patch the module."""
    monkeypatch.setattr(subprocess, "run", tcc.run_tccutil)


# ----------------------------------------------------------------------
# POST /{id}/request: the just-in-time ask
# ----------------------------------------------------------------------


def test_request_asks_macos_once_and_answers_at_once_with_the_outcome(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    started = time.monotonic()
    response = env.post("microphone/request")
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    body = response.json()
    assert body["permission"] == "microphone"
    assert body["outcome"] == "pending" and body["granted"] is False
    assert body["asked"] is True and body["reason"] == "not_determined"
    assert body["user_detail"]
    assert body["agent_detail"].startswith("[permission_needed:microphone] ")
    assert len(env.tcc.requests("microphone")) == 1
    assert env.tcc.dialog_open("microphone")  # macOS owns the next step
    assert elapsed < 2.0  # no HTTP hold on an unanswered dialog


def test_request_reports_granted_when_the_person_allowed_in_the_dialog(make_env) -> None:
    env = make_env()  # the fake person clicks Allow

    body = env.post("microphone/request").json()

    assert body["outcome"] == "granted" and body["granted"] is True and body["asked"] is True
    assert body["reason"] == "" and body["user_detail"] == "" and body["agent_detail"] == ""


def test_request_is_attributed_to_the_feature_it_names(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    env.post("microphone/request", json={"feature": "browser_voice"})

    [episode] = get_permission_service().outstanding()
    assert episode.feature == "browser_voice" and episode.origin == "user"
    assert episode.permissions == ("microphone",)


@pytest.mark.parametrize(
    ("permission", "feature"),
    [
        ("microphone", "voice"),
        ("screen_recording", "computer_use"),
        ("accessibility", "computer_use"),
        ("input_monitoring", "global_shortcuts"),
    ],
)
def test_request_without_a_feature_uses_the_first_feature_of_the_permission(
    make_env, permission: str, feature: str
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    env.post(f"{permission}/request")

    [episode] = get_permission_service().outstanding()
    assert episode.feature == feature


@pytest.mark.parametrize(
    "body",
    [{"feature": "teleport"}, {"target": "com.evil.app"}, {"allow_outside": True}],
    ids=["unknown-feature", "unknown-player", "misspelled-consent-flag"],
)
def test_request_refuses_an_unknown_feature_player_or_key(make_env, body: dict) -> None:
    env = make_env()

    response = env.post("microphone/request", json=body)

    assert response.status_code == 422
    assert env.tcc.requests() == []


def test_a_denied_permission_is_reported_and_never_asked_again(make_env) -> None:
    env = make_env()
    env.tcc.deny("microphone")

    body = env.post("microphone/request").json()

    assert body["outcome"] == "denied" and body["asked"] is False
    assert body["reason"] == "denied" and body["can_open_settings"] is True
    assert body["can_prompt"] is False
    assert env.tcc.requests() == []


def test_outside_the_installed_app_nothing_is_asked_without_the_confirm_flag(make_env) -> None:
    env = make_env(bundle_id=None, bundle_path=None, default_policy=DialogPolicy.NEVER_ANSWERED)

    first = env.post("microphone/request").json()

    assert first["outcome"] == "needs_settings" and first["asked"] is False
    assert first["outside_installed_app"] is True
    assert env.tcc.requests() == [] and env.tcc.implicit_prompts() == []

    env.clock.advance(6)  # past the per-permission cooldown
    confirmed = env.post("microphone/request", json={"allow_outside_app": True}).json()

    assert confirmed["asked"] is True and confirmed["outcome"] == "pending"
    assert len(env.tcc.requests("microphone")) == 1


def test_the_confirm_flag_is_never_assumed(make_env) -> None:
    env = make_env(bundle_id=None, bundle_path=None)

    body = env.post("microphone/request", json={"allow_outside_app": False}).json()

    assert body["asked"] is False and body["outcome"] == "needs_settings"
    assert env.tcc.requests() == []


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_request_off_macos_is_not_required_and_touches_nothing(make_env, platform: str) -> None:
    env = make_env(platform=platform)

    body = env.post("microphone/request").json()

    assert body["outcome"] == "not_required" and body["granted"] is True
    assert body["asked"] is False
    env.tcc.assert_silent()


def test_event_posting_is_an_alias_of_accessibility_for_asking(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    body = env.post("event_posting/request").json()

    assert body["permission"] == "event_posting" and body["outcome"] == "pending"
    [episode] = get_permission_service().outstanding()
    assert episode.permissions == ("accessibility",)  # one request, one episode


def test_request_hands_the_fake_service_exactly_one_interactive_non_waiting_ensure(
    make_env,
) -> None:
    gate = FakePermissionService({PermissionId.MICROPHONE: "pending"})
    env = make_env(service=gate)

    body = env.post(
        "microphone/request", json={"feature": "dictation", "allow_outside_app": True}
    ).json()

    assert body["outcome"] == "pending"
    [call] = gate.ensure_calls(PermissionId.MICROPHONE)
    assert (call.method, call.feature, call.interactive) == ("ensure", "dictation", True)
    assert call.wait_s == 0 and call.allow_outside_app is True
    env.tcc.assert_silent()  # the fake service never reached the OS


def test_dry_run_changes_nothing(make_env, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env()
    _use_fake_tccutil(monkeypatch, env.tcc)

    for action in ("request", "open-settings", "reset"):
        response = env.post(f"microphone/{action}?dry_run=true")
        body = response.json()
        assert response.status_code == 200, action
        assert body["dry_run"] is True and body["performed"] is False and body["ok"] is True
        assert body["action"] == action.replace("-", "_")

    assert env.tcc.requests() == [] and env.tcc.workspace_opened_urls == []
    assert env.tcc.tccutil_calls == []


def test_a_dry_run_does_not_count_against_the_rate_limit(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    env.post("microphone/request?dry_run=true")
    env.post("microphone/request?dry_run=true")

    assert env.post("microphone/request").status_code == 200


# ----------------------------------------------------------------------
# The Automation ask: blocking natively, never an HTTP hold
# ----------------------------------------------------------------------


def test_automation_without_a_player_asks_the_first_one_that_runs(make_env) -> None:
    env = make_env(installed_players=[_MUSIC, _SPOTIFY], running_players=[_SPOTIFY])

    body = env.post("automation/request").json()

    # Music is first in the table but is not running: the guarded script asks
    # nothing for it, so the route goes on to Spotify.
    assert body["asked"] is True and body["outcome"] == "granted"
    assert [call.target for call in env.tcc.requests("automation")] == [_SPOTIFY]
    assert body["target"] == _SPOTIFY


def test_automation_with_a_named_player_asks_only_that_one(make_env) -> None:
    env = make_env(installed_players=[_MUSIC, _SPOTIFY], running_players=[_MUSIC, _SPOTIFY])

    env.post("automation/request", json={"target": _SPOTIFY})

    assert [call.target for call in env.tcc.requests("automation")] == [_SPOTIFY]


def test_automation_with_no_player_running_launches_nothing_and_is_unavailable(make_env) -> None:
    env = make_env(installed_players=[_MUSIC])

    body = env.post("automation/request").json()

    assert body["outcome"] == "unavailable" and body["granted"] is False
    assert env.tcc.requests("automation") == [] and env.tcc.launches == []


def test_an_automation_dialog_that_is_still_open_answers_pending_and_does_not_hold(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(installed_players=[_MUSIC], running_players=[_MUSIC])
    service = get_permission_service()
    entered: list[str | None] = []
    release = threading.Event()
    reached = threading.Event()  # set by the stub itself: no assertion races a slow thread start
    real_ensure = service.ensure

    def blocking_ensure(*args, **kwargs):
        entered.append(kwargs.get("target"))
        reached.set()
        release.wait(10)  # the consent runner, blocked on an open dialog
        return real_ensure(*args, **kwargs)

    monkeypatch.setattr(service, "ensure", blocking_ensure)
    monkeypatch.setattr(routes, "_AUTOMATION_ASK_WAIT_S", 0.2)
    try:
        started = time.monotonic()
        body = env.post("automation/request").json()
        elapsed = time.monotonic() - started

        assert body["outcome"] == "pending" and body["asked"] is True
        assert body["can_prompt"] is False and "Waiting for the user" in body["user_detail"]
        assert elapsed < 5.0
        # While that call is still blocked a second one starts nothing.
        env.clock.advance(6)
        again = env.post("automation/request").json()
        assert again["outcome"] == "pending" and again["asked"] is False
        assert reached.wait(5)
        assert entered == [_MUSIC]
    finally:
        release.set()
        thread = env.app.state.permissions_runtime.automation_ask._thread
        if thread is not None:
            thread.join(5)


# ----------------------------------------------------------------------
# The Keychain row: "Try again" replays the failed read
# ----------------------------------------------------------------------


def test_keychain_try_again_replays_the_failed_read(make_env) -> None:
    env = make_env(credential_backend="file")

    body = env.post("credential_store/request").json()

    assert env.tcc.keychain_recover_calls == 1
    assert body["permission"] == "credential_store"
    assert body["outcome"] == "granted" and body["asked"] is True


def test_keychain_try_again_that_is_declined_again_says_so(make_env) -> None:
    env = make_env(credential_backend="file")
    env.tcc.keychain_recover_result = False

    body = env.post("credential_store/request").json()

    assert body["outcome"] == "denied" and body["reason"] == "denied"
    assert body["can_prompt"] is True and body["can_open_settings"] is False
    assert "local file" in body["user_detail"]


def test_keychain_that_already_works_replays_nothing(make_env) -> None:
    env = make_env(credential_backend="platform")

    body = env.post("credential_store/request").json()

    assert body["outcome"] == "granted" and body["asked"] is False
    assert env.tcc.keychain_recover_calls == 0


# ----------------------------------------------------------------------
# Rate limits: a cooldown per (action, permission) and a global cap
# ----------------------------------------------------------------------


def test_a_second_request_for_one_permission_within_five_seconds_is_429(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    assert env.post("microphone/request").status_code == 200
    again = env.post("microphone/request")

    assert again.status_code == 429
    assert again.json() == {
        "error": "rate_limited",
        "scope": "permission",
        "action": "request",
        "permission_id": "microphone",
        "retry_after_s": 5,
    }
    assert again.headers["retry-after"] == "5"
    assert len(env.tcc.requests("microphone")) == 1  # the refusal never reached the OS

    env.clock.advance(5.1)
    assert env.post("microphone/request").status_code == 200


def test_the_cooldown_is_per_permission_and_per_action(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    assert env.post("microphone/request").status_code == 200
    assert env.post("screen_recording/request").status_code == 200
    assert env.post("microphone/open-settings").status_code == 200
    again = env.post("microphone/open-settings")
    assert again.status_code == 429 and again.json()["action"] == "open_settings"


def test_an_alias_cannot_dodge_the_cooldown_of_its_pane_family(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    assert env.post("accessibility/request").status_code == 200
    assert env.post("event_posting/request").status_code == 429


def test_the_global_cap_covers_request_and_open_settings_together(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.app.state.permissions_runtime.limiter = routes._RateLimiter(
        cooldown_s=0, global_max=3, global_window_s=60, clock=env.clock
    )

    assert env.post("microphone/request").status_code == 200
    assert env.post("microphone/open-settings").status_code == 200
    assert env.post("screen_recording/request").status_code == 200
    refused = env.post("screen_recording/open-settings")

    assert refused.status_code == 429
    body = refused.json()
    assert body["scope"] == "global" and body["retry_after_s"] == 60
    assert len(env.tcc.workspace_opened_urls) == 1  # the refused call opened nothing

    env.clock.advance(61)  # the window slides on
    assert env.post("screen_recording/open-settings").status_code == 200


def test_a_refused_call_is_not_counted(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.app.state.permissions_runtime.limiter = routes._RateLimiter(
        cooldown_s=5, global_max=2, global_window_s=60, clock=env.clock
    )

    assert env.post("microphone/request").status_code == 200
    for _ in range(5):
        assert env.post("microphone/request").status_code == 429  # cooldown, not the cap
    assert env.post("screen_recording/request").status_code == 200  # the 2nd counted call


def test_reset_is_rate_limited_like_the_other_routes_that_change_what_macos_shows(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "Reset, then request" in a loop would defeat the re-ask limits (P4).
    env = make_env()
    _use_fake_tccutil(monkeypatch, env.tcc)

    assert env.post("microphone/reset").status_code == 200
    again = env.post("microphone/reset")
    assert again.status_code == 429 and again.json()["action"] == "reset"
    assert len(env.tcc.tccutil_calls) == 1  # the refusal never reached tccutil

    env.clock.advance(5.1)
    assert env.post("microphone/reset").status_code == 200


# ----------------------------------------------------------------------
# POST /{id}/open-settings
# ----------------------------------------------------------------------


def test_open_settings_opens_the_matching_pane_and_returns_the_row(make_env) -> None:
    env = make_env()

    response = env.post("microphone/open-settings")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["action"] == "open_settings" and body["performed"] is True
    assert body["permission_id"] == "microphone" and body["dry_run"] is False
    assert body["permission"]["id"] == "microphone"
    assert env.tcc.workspace_opened_urls == [
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
    ]
    assert env.tcc.requests() == []  # opening a pane is not a prompt


def test_open_settings_that_cannot_open_is_a_409_with_the_full_payload(make_env) -> None:
    env = make_env(headless=True)  # no desktop session: the port refuses

    response = env.post("microphone/open-settings")

    assert response.status_code == 409
    body = response.json()
    assert body["ok"] is False and body["performed"] is False
    assert body["message"] == "System Settings could not be opened."
    assert env.tcc.workspace_opened_urls == []


def test_the_keychain_has_no_settings_pane(make_env) -> None:
    env = make_env()

    response = env.post("credential_store/open-settings")

    assert response.status_code == 409
    assert "no System Settings pane" in response.json()["message"]
    assert env.tcc.workspace_opened_urls == []


# ----------------------------------------------------------------------
# POST /{id}/reset ("Ask again")
# ----------------------------------------------------------------------


def test_reset_is_refused_with_409_while_the_permission_is_granted(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(granted=["microphone"])
    _use_fake_tccutil(monkeypatch, env.tcc)

    response = env.post("microphone/reset")

    assert response.status_code == 409
    body = response.json()
    assert body["ok"] is False and body["performed"] is False
    assert "allowed right now" in body["message"]
    assert env.tcc.tccutil_calls == []
    assert env.tcc.state("microphone").value == "granted"


def test_reset_is_refused_when_only_the_window_oracle_sees_the_screen_grant(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env()
    _use_fake_tccutil(monkeypatch, env.tcc)
    env.tcc.grant("screen_recording")  # mid-process: the frozen preflight still says no

    shallow = env.client.get("/api/permissions/screen_recording").json()
    response = env.post("screen_recording/reset")

    assert shallow["status"] == "not_granted"  # the snapshot never uses the oracle
    assert response.status_code == 409  # a reset must never throw a working grant away
    assert env.tcc.tccutil_calls == []


def test_reset_of_a_denied_permission_drops_this_apps_own_row(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env()
    _use_fake_tccutil(monkeypatch, env.tcc)
    env.tcc.deny("microphone")

    response = env.post("microphone/reset")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["performed"] is True and body["action"] == "reset"
    assert env.tcc.tccutil_calls == [["/usr/bin/tccutil", "reset", "Microphone", env.tcc.grantee]]
    assert env.tcc.state("microphone").value == "not_determined"
    assert body["permission"]["status"] == "not_determined"


def test_ask_again_after_a_reset_really_asks(make_env, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(default_policy=DialogPolicy.DENY)
    _use_fake_tccutil(monkeypatch, env.tcc)

    first = env.post("microphone/request").json()
    assert first["outcome"] == "denied" and first["asked"] is True
    env.clock.advance(6)
    assert env.post("microphone/request").json()["asked"] is False  # stable: not asked again
    assert env.post("microphone/reset").status_code == 200
    env.clock.advance(6)
    env.tcc.set_policy("microphone", DialogPolicy.ALLOW)

    again = env.post("microphone/request").json()

    assert again["asked"] is True and again["outcome"] == "granted"
    assert len(env.tcc.requests("microphone")) == 2


def test_reset_outside_the_installed_app_is_refused_by_the_port(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(bundle_id=None, bundle_path=None)
    _use_fake_tccutil(monkeypatch, env.tcc)
    env.tcc.deny("microphone")

    response = env.post("microphone/reset")

    assert response.status_code == 409
    assert env.tcc.tccutil_calls == []  # a developer run must not wipe the installed app's rows


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_reset_off_macos_is_a_409(make_env, platform: str) -> None:
    env = make_env(platform=platform)

    response = env.post("microphone/reset")

    assert response.status_code == 409
    assert "only possible on macOS" in response.json()["message"]
    env.tcc.assert_silent()


def test_a_restricted_permission_cannot_be_reset(make_env, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env()
    _use_fake_tccutil(monkeypatch, env.tcc)
    env.tcc.restrict("microphone")

    response = env.post("microphone/reset")

    assert response.status_code == 409
    assert "cannot be reset from here" in response.json()["message"]
    assert env.tcc.tccutil_calls == []


# ----------------------------------------------------------------------
# Contract
# ----------------------------------------------------------------------


def test_unknown_permission_id_is_rejected(make_env) -> None:
    env = make_env()

    for method, path in (
        ("post", "camera/request"),
        ("post", "camera/open-settings"),
        ("post", "camera/reset"),
        ("get", "camera"),
    ):
        assert getattr(env.client, method)(f"/api/permissions/{path}").status_code == 422, path


def test_mutating_routes_are_marked_dangerous_in_openapi() -> None:
    app = FastAPI()
    app.include_router(router)

    paths = app.openapi()["paths"]

    for action in ("request", "open-settings", "reset"):
        route = paths[f"/api/permissions/{{permission_id}}/{action}"]["post"]
        assert route["x-jarvis-dangerous"] is True, action
    for read in ("/api/permissions/status", "/api/permissions/{permission_id}"):
        assert "x-jarvis-dangerous" not in paths[read]["get"]  # reads are never gated


def test_every_handler_is_a_sync_def() -> None:
    """A native call must never run on the event loop (scripts/ci/check_async_routes.py)."""
    import inspect

    for route in router.routes:
        assert not inspect.iscoroutinefunction(route.endpoint), route.path


def test_no_route_reaches_the_old_readiness_aggregation(make_env, monkeypatch) -> None:
    """The routes stopped using ``port.snapshot()`` (the feature/``wanted`` aggregation)."""
    from jarvis.platform.permissions import SystemPermissionPort

    env = make_env(granted=["microphone"])

    def forbidden(self, **kwargs):  # noqa: ANN001, ARG001
        raise AssertionError("the status routes must not aggregate readiness")

    monkeypatch.setattr(SystemPermissionPort, "snapshot", forbidden)
    assert env.client.get("/api/permissions/status").status_code == 200
    assert env.client.get("/api/permissions/microphone").status_code == 200
    assert env.post("microphone/request").status_code == 200


def test_open_settings_and_reset_are_light_and_never_snapshot_or_probe_other_permissions(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both "way out" routes used to run two full snapshots each: the Automation probe of
    every running player, the window-title oracle and a consent-file write."""
    from jarvis.platform.permissions import SystemPermissionPort

    env = make_env(
        default_policy=DialogPolicy.NEVER_ANSWERED,
        installed_players=[_MUSIC],
        running_players=[_MUSIC],
    )
    _use_fake_tccutil(monkeypatch, env.tcc)

    def forbidden(self, **kwargs):  # noqa: ANN001, ARG001
        raise AssertionError("open-settings and reset must not aggregate readiness")

    monkeypatch.setattr(SystemPermissionPort, "snapshot", forbidden)
    assert env.post("accessibility/open-settings").status_code == 200
    assert env.post("microphone/reset").status_code == 200

    assert env.tcc.probes("screen_recording") == []  # no oracle, no preflight
    assert env.tcc.probes("automation") == []  # no Apple Event probe of the player
    assert len(env.tcc.workspace_opened_urls) == 1 and len(env.tcc.tccutil_calls) == 1


def test_open_settings_works_outside_the_installed_app_because_it_is_not_a_prompt(
    make_env,
) -> None:
    env = make_env(bundle_id=None, bundle_path=None)

    body = env.post("microphone/open-settings").json()

    assert body["ok"] is True and body["performed"] is True
    assert body["permission"]["can_open_settings"] is True  # the row says what the route does
    assert len(env.tcc.workspace_opened_urls) == 1


# ----------------------------------------------------------------------
# Who may confirm a grant for the app that started Jarvis, and who may skip the cooldown
# ----------------------------------------------------------------------


@pytest.fixture
def control_key(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A valid Bearer control key: the way a coding agent drives the CLI."""
    monkeypatch.setattr(
        routes._control_key,
        "verify_control_key",
        lambda token: token == "agent-key",  # noqa: S105 - a test key, not a secret
    )
    return {"Authorization": "Bearer agent-key"}


def test_an_agent_with_only_the_control_key_cannot_confirm_a_grant_for_another_app(
    make_env, control_key: dict[str, str]
) -> None:
    env = make_env(bundle_id=None, bundle_path=None, default_policy=DialogPolicy.NEVER_ANSWERED)

    env.client.cookies.clear()  # an agent has no browser session, only the control key
    refused = env.post("microphone/request", json={"allow_outside_app": True}, headers=control_key)

    assert refused.status_code == 403
    assert refused.json()["error"] == "confirmation_requires_ui"
    assert env.tcc.requests() == [] and env.tcc.implicit_prompts() == []
    # The same agent may still ask without the confirmation (nothing is requested
    # outside the installed app, the answer says so).
    plain = env.post("microphone/request", headers=control_key).json()
    assert plain["outside_installed_app"] is True and plain["asked"] is False


def test_the_ui_may_confirm_a_grant_for_the_app_that_started_jarvis(make_env) -> None:
    env = make_env(bundle_id=None, bundle_path=None, default_policy=DialogPolicy.NEVER_ANSWERED)

    body = env.post("microphone/request", json={"allow_outside_app": True}).json()

    assert body["asked"] is True and len(env.tcc.requests("microphone")) == 1


def test_only_an_explicit_ui_click_skips_the_reask_cooldown(
    make_env, control_key: dict[str, str]
) -> None:
    gate = FakePermissionService({PermissionId.ACCESSIBILITY: "pending"})
    env = make_env(service=gate)

    env.post("accessibility/request")  # the person clicked "Allow" in the Jarvis window
    env.clock.advance(6)
    env.client.cookies.clear()  # an agent has no browser session, only the control key
    env.post("accessibility/request", headers=control_key)

    ui_call, agent_call = gate.ensure_calls(PermissionId.ACCESSIBILITY)
    assert ui_call.force_ask is True and agent_call.force_ask is False


def test_the_allow_button_asks_again_in_the_real_service_after_the_cooldown_of_a_dismissed_prompt(
    make_env,
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    first = env.post("accessibility/request").json()
    env.clock.advance(6)  # past the route's own 5 s cooldown, inside the 10 minute re-ask window
    second = env.post("accessibility/request").json()

    assert first["asked"] is True and second["asked"] is True
    assert len(env.tcc.requests("accessibility")) == 2


def test_reset_is_refused_while_any_player_is_still_allowed(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strictest-answer aggregate hid a working player behind one never asked."""
    env = make_env(installed_players=[_MUSIC, _SPOTIFY], running_players=[_MUSIC, _SPOTIFY])
    env.tcc.grant("automation", _MUSIC)  # Spotify was never asked
    _use_fake_tccutil(monkeypatch, env.tcc)

    response = env.post("automation/reset")

    assert response.status_code == 409
    assert "allowed right now" in response.json()["message"]
    assert env.tcc.tccutil_calls == []  # the working Music grant was not thrown away
