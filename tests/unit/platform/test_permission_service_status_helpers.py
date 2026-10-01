"""The read helpers the permission routes need from ``PermissionService``.

``app_info`` (who this process is to macOS), ``check_deep`` (the fresh read with the
Screen Recording window-title oracle), ``can_request`` (could a gesture make macOS
show something) and the ``note_reset`` memory of an open episode. The REAL service
runs on a REAL ``SystemPermissionPort`` that sits on ``FakeTCC``; nothing here ran on
a real Mac, every macOS behaviour is a MODEL (see ``tests/fakes/fake_tcc.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from jarvis.platform import permission_service as service_module
from jarvis.platform.permission_service import AppInfo, PermissionOutcome, PermissionService
from jarvis.platform.permissions import APP_NAME, PermissionId, PermissionState
from tests.fakes.fake_tcc import DMG_BUNDLE_ID, DialogPolicy, FakeTCC, TccService, install_port

_MIC = PermissionId.MICROPHONE
_SR = PermissionId.SCREEN_RECORDING
_AX = PermissionId.ACCESSIBILITY
_AUTOMATION = PermissionId.AUTOMATION
_MUSIC = "com.apple.Music"


@pytest.fixture
def make_service(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., tuple[PermissionService, FakeTCC]]]:
    created: list[PermissionService] = []

    def build(*, platform: str = "darwin", **tcc_kwargs: Any) -> tuple[PermissionService, FakeTCC]:
        tcc = FakeTCC(**tcc_kwargs)
        install_port(monkeypatch, tcc.port(platform))  # type: ignore[arg-type]
        service = PermissionService(watch_interval_s=3600.0)
        created.append(service)
        return service, tcc

    yield build
    for service in created:
        service._shutdown()


# ----------------------------------------------------------------------
# app_info
# ----------------------------------------------------------------------


def test_app_info_of_the_installed_app(make_service) -> None:
    service, _tcc = make_service()

    assert service.app_info() == AppInfo(
        platform="darwin",
        app_name=APP_NAME,
        bundle_id="com.personal-jarvis.desktop",
        bundle_path="/Applications/Personal Jarvis.app",
        launched_as_bundle=True,
        stable=True,
        outside_installed_app=False,
        headless=False,
    )


def test_app_info_of_the_dmg_app_is_an_installed_app_too(make_service) -> None:
    service, _tcc = make_service(bundle_id=DMG_BUNDLE_ID)

    info = service.app_info()

    assert info.bundle_id == DMG_BUNDLE_ID and info.stable and not info.outside_installed_app


def test_app_info_outside_the_installed_app(make_service) -> None:
    service, _tcc = make_service(bundle_id=None, bundle_path=None)

    info = service.app_info()

    assert info.outside_installed_app is True and info.stable is False
    assert info.bundle_id is None and info.launched_as_bundle is False


def test_app_info_reports_a_missing_desktop_session(make_service) -> None:
    service, _tcc = make_service(headless=True)

    assert service.app_info().headless is True


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_app_info_off_macos_has_no_bundle_and_is_not_outside_anything(
    make_service, platform: str
) -> None:
    service, tcc = make_service(platform=platform)

    info = service.app_info()

    assert info.platform == platform
    assert (info.bundle_id, info.bundle_path) == (None, None)
    assert info.stable is False and info.outside_installed_app is False
    tcc.assert_silent()


def test_app_info_of_a_port_that_cannot_describe_itself_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BarePort:
        platform = "darwin"

    monkeypatch.setattr(service_module._permissions_module, "get_system_permission_port", BarePort)
    service = PermissionService()

    info = service.app_info()

    assert info.platform == "darwin" and info.app_name == APP_NAME
    assert info.bundle_id is None and info.stable is False and info.headless is False


# ----------------------------------------------------------------------
# check_deep
# ----------------------------------------------------------------------


def test_check_deep_sees_the_screen_grant_the_frozen_preflight_still_denies(make_service) -> None:
    service, tcc = make_service()
    tcc.grant("screen_recording")  # mid-process: the preflight is frozen until relaunch (BUG-161)

    assert service.check(_SR) is PermissionState.NOT_GRANTED
    assert service.check_deep(_SR) is PermissionState.GRANTED


def test_check_deep_is_fresh_where_check_may_be_cached(make_service) -> None:
    service, tcc = make_service(granted=[TccService.MICROPHONE])
    assert service.check(_MIC) is PermissionState.GRANTED  # cached for about a second

    tcc.deny("microphone")

    assert service.check(_MIC) is PermissionState.GRANTED  # the cache answers
    assert service.check_deep(_MIC) is PermissionState.DENIED  # this one goes to the OS


def test_check_deep_never_prompts(make_service) -> None:
    service, tcc = make_service()

    for permission in (_MIC, _SR, _AX):
        service.check_deep(permission)

    tcc.assert_no_prompts()


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_check_deep_off_macos_is_not_required_and_silent(make_service, platform: str) -> None:
    service, tcc = make_service(platform=platform)

    assert service.check_deep(_SR) is PermissionState.NOT_REQUIRED
    tcc.assert_silent()


def test_check_deep_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class ExplodingPort:
        platform = "darwin"

        def state(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("the native bridge blew up")

    monkeypatch.setattr(
        service_module._permissions_module, "get_system_permission_port", ExplodingPort
    )

    assert PermissionService().check_deep(_MIC) is PermissionState.UNAVAILABLE


# ----------------------------------------------------------------------
# can_request
# ----------------------------------------------------------------------


@pytest.mark.parametrize("permission", [_MIC, _SR, _AX, PermissionId.INPUT_MONITORING])
def test_a_permission_nobody_decided_can_be_requested(make_service, permission) -> None:
    service, _tcc = make_service()

    assert service.can_request(permission) is True


def test_a_decided_permission_cannot_be_requested(make_service) -> None:
    service, tcc = make_service(granted=[TccService.MICROPHONE])
    tcc.deny("screen_recording")
    tcc.restrict("accessibility")

    assert service.can_request(_MIC) is False  # granted
    assert service.can_request(_SR, PermissionState.DENIED) is False  # macOS will not ask again
    assert service.can_request(_AX, PermissionState.RESTRICTED) is False
    assert service.can_request(_AX, PermissionState.UNAVAILABLE) is False


def test_the_keychain_has_no_native_request(make_service) -> None:
    service, _tcc = make_service(credential_backend="file")

    assert service.can_request(PermissionId.CREDENTIAL_STORE) is False


def test_without_a_usage_string_nothing_may_be_requested(make_service) -> None:
    service, tcc = make_service(usage_strings=[TccService.AUTOMATION])

    assert service.can_request(_MIC) is False  # BUG-058 class: asking would abort the process
    assert tcc.requests() == []


def test_automation_needs_a_player_or_a_known_state(make_service) -> None:
    service, tcc = make_service(installed_players=[_MUSIC], running_players=[_MUSIC])

    assert service.can_request(_AUTOMATION) is False  # no player: never the aggregate read
    assert tcc.calls == ()
    assert service.can_request(_AUTOMATION, target=_MUSIC) is True
    assert service.can_request(_AUTOMATION, PermissionState.NOT_DETERMINED) is True


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_nothing_can_be_requested_off_macos(make_service, platform: str) -> None:
    service, tcc = make_service(platform=platform)

    assert service.can_request(_MIC) is False
    tcc.assert_silent()


# ----------------------------------------------------------------------
# note_reset: "Ask again" must really ask
# ----------------------------------------------------------------------


def test_after_a_reset_an_open_episode_may_ask_again(make_service) -> None:
    service, tcc = make_service(default_policy=DialogPolicy.DENY)
    first = service.ensure(_MIC, feature="voice")
    assert first.asked and first.outcome is PermissionOutcome.DENIED
    assert [o.permissions for o in service.outstanding()] == [("microphone",)]  # still open

    tcc.reset("microphone")  # tccutil reset: the decision is gone
    service.note_reset(_MIC)
    tcc.set_policy("microphone", DialogPolicy.ALLOW)
    second = service.ensure(_MIC, feature="voice")

    assert second.asked is True and second.outcome is PermissionOutcome.GRANTED
    assert len(tcc.requests("microphone")) == 2


def test_without_note_reset_the_open_episode_would_not_ask_again(make_service) -> None:
    """The reason ``note_reset`` clears the episode's memory: one ask per episode."""
    service, tcc = make_service(default_policy=DialogPolicy.DENY)
    service.ensure(_MIC, feature="voice")
    tcc.reset("microphone")
    tcc.set_policy("microphone", DialogPolicy.ALLOW)

    second = service.ensure(_MIC, feature="voice")

    assert second.asked is False
    assert len(tcc.requests("microphone")) == 1
