"""Characterization of the macOS permission port BEFORE the just-in-time rebuild.

These tests pin what the code does today. They were written on the unchanged
base commit and pass against it; the just-in-time rebuild (every protected
feature asks macOS at first use instead of being refused by our own preflight)
must keep the ``survives`` group green UNCHANGED, because Windows, Linux and the
surviving macOS rules are the promise the rebuild makes.

Naming contract (read this before touching a red test)

* ``test_survives_*``  - behaviour that must outlive the rebuild. A red one after
  a later stage is a regression, not an intended change.
* ``test_legacy_*``    - behaviour the rebuild deletes or rewrites ON PURPOSE (the
  runtime-access wall, feature readiness, the v1 snapshot keys, the restart
  flag, the foreground gate). A red one after the stage that changes it is
  intentional: rewrite or delete it in that same commit and say why. A red
  ``test_legacy_*`` in any other stage is a regression.

The v1 snapshot keys EXPECTED to change in the later snapshot-v2 stage, all
pinned only by ``test_legacy_*`` tests: top level ``features``,
``identity_reset``, ``restart_required``; ``app_identity.foreground`` and
``app_identity.expected_bundle_id``; per permission row ``wanted``, ``required``
(becomes ``used_for``) and ``restart_required`` (becomes ``restart_hint``); and
per feature ``active``. Added by v2: ``outside_installed_app`` and ``needed``.
The keys pinned by the ``survives`` tests (``platform``, ``supported``,
``headless``, ``app_identity.app_name/bundle_id/bundle_path/
launched_as_bundle/stable``, and per row ``id/label/status/can_request/
can_open_settings/can_reset/detail``) are the ones other consumers read.

Tests EXPECTED to be rewritten or deleted by a later stage (the last test of
this module fails if a ``test_legacy_*`` test is missing from this list):

* test_legacy_non_darwin_snapshot_v1_only_keys
* test_legacy_non_darwin_feature_readiness
* test_legacy_non_darwin_dry_run_request_claims_it_would_ask
* test_legacy_darwin_runtime_access_is_a_wall_that_never_asks
* test_legacy_darwin_request_is_refused_in_the_background_or_outside_the_app
* test_legacy_darwin_request_flags_follow_the_state_and_hide_the_dead_button
* test_legacy_darwin_snapshot_reports_feature_readiness_and_wanted
* test_legacy_darwin_a_screen_request_flags_a_restart_until_relaunch
* test_legacy_darwin_open_settings_needs_the_installed_app_but_not_the_foreground
* test_legacy_default_darwin_microphone_gate_is_the_runtime_access_wall

The FakeTCC call log is the proof that nothing asks: off macOS it must stay
empty (``assert_silent``), and a read on macOS never prompts
(``assert_no_prompts``). Everything about macOS behaviour here is a MODEL (the
fidelity ledger in ``tests/fakes/fake_tcc.py`` says which parts Apple documents);
nothing was run on a real Mac.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.audio import capture
from jarvis.core.branding import MACOS_DMG_BUNDLE_ID
from jarvis.platform import probes
from jarvis.platform.permissions import (
    ACCEPTED_BUNDLE_IDS,
    APP_NAME,
    AUTOMATION_TARGETS,
    EXPECTED_BUNDLE_ID,
    FEATURE_REQUIREMENTS,
    PermissionId,
    PermissionState,
    SystemPermissionPort,
    get_system_permission_port,
)
from tests.fakes.fake_tcc import (
    INSTALLED_BUNDLE_PATH,
    DialogPolicy,
    FakeAudioInput,
    TccService,
    install_port,
    make_darwin_port,
    make_non_darwin_port,
)

MIC = TccService.MICROPHONE
SCREEN = TccService.SCREEN_RECORDING
AX = TccService.ACCESSIBILITY
INPUT = TccService.INPUT_MONITORING
AUTOMATION = TccService.AUTOMATION
MUSIC = "com.apple.Music"
SPOTIFY = "com.spotify.client"

_NOT_REQUIRED_MESSAGE = "macOS permission requests are not required on this platform."


def _row(snapshot: dict, permission_id: PermissionId) -> dict:
    return next(item for item in snapshot["permissions"] if item["id"] == permission_id.value)


def _exercise_every_operation(port: SystemPermissionPort) -> None:
    """Every public entry point of the port, for every permission, dry or not."""
    for permission_id in PermissionId:
        port.state(permission_id)
        port.runtime_access_granted(permission_id)
        for dry_run in (False, True):
            port.request(permission_id, dry_run=dry_run)
            port.open_settings(permission_id, dry_run=dry_run)
            port.reset(permission_id, dry_run=dry_run)
    for feature in FEATURE_REQUIREMENTS:
        port.runtime_feature_ready(feature)
    port.snapshot()


@pytest.fixture(params=["win32", "linux"])
def non_darwin(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """A real port for Windows or Linux on a FakeTCC whose log must stay empty.

    ``subprocess.run`` is routed to the fake's ``tccutil`` stand-in, so a
    ``tccutil`` run off macOS would show up in ``tcc.tccutil_calls``.
    """
    port, tcc = make_non_darwin_port(request.param)
    monkeypatch.setattr(subprocess, "run", tcc.run_tccutil)
    return port, tcc


# ---------------------------------------------------------------------------
# (a) Windows and Linux: nothing is required, nothing is asked, nothing is read
# ---------------------------------------------------------------------------


def test_survives_non_darwin_state_is_not_required_for_every_permission(non_darwin) -> None:
    port, tcc = non_darwin

    for permission_id in PermissionId:
        assert port.state(permission_id) is PermissionState.NOT_REQUIRED, permission_id
        assert port.state(permission_id.value) is PermissionState.NOT_REQUIRED

    tcc.assert_silent()


def test_survives_non_darwin_runtime_access_is_granted_for_every_permission(non_darwin) -> None:
    port, tcc = non_darwin

    assert all(port.runtime_access_granted(permission_id) for permission_id in PermissionId)

    tcc.assert_silent()


def test_survives_non_darwin_request_refuses_and_asks_nothing(non_darwin) -> None:
    port, tcc = non_darwin

    for permission_id in PermissionId:
        operation = port.request(permission_id)
        assert operation.ok is False, permission_id
        assert operation.performed is False
        assert operation.dry_run is False
        assert operation.restart_required is False
        assert operation.action == "request"
        assert operation.message == _NOT_REQUIRED_MESSAGE

    tcc.assert_silent()


def test_survives_non_darwin_open_settings_refuses_and_opens_nothing(non_darwin) -> None:
    port, tcc = non_darwin

    for permission_id in PermissionId:
        operation = port.open_settings(permission_id)
        assert operation.ok is False, permission_id
        assert operation.performed is False
        assert operation.restart_required is False

    # The Keychain has no pane at all; every other row refuses with the same
    # platform sentence.
    assert "no System Settings pane" in port.open_settings(PermissionId.CREDENTIAL_STORE).message
    assert port.open_settings(PermissionId.MICROPHONE).message == _NOT_REQUIRED_MESSAGE
    assert tcc.workspace_opened_urls == []
    tcc.assert_silent()


def test_survives_non_darwin_reset_is_a_refused_no_op_and_never_runs_tccutil(non_darwin) -> None:
    port, tcc = non_darwin

    for permission_id in PermissionId:
        for dry_run in (False, True):
            operation = port.reset(permission_id, dry_run=dry_run)
            assert operation.ok is False, (permission_id, dry_run)
            assert operation.performed is False
            assert "has no resettable macOS record" in operation.message

    assert tcc.tccutil_calls == []
    tcc.assert_silent()


def test_survives_non_darwin_call_log_stays_empty_through_every_operation(non_darwin) -> None:
    port, tcc = non_darwin

    _exercise_every_operation(port)

    tcc.assert_silent()
    assert tcc.tccutil_calls == []
    assert tcc.workspace_opened_urls == []


@pytest.mark.parametrize("display", [True, False])
def test_survives_non_darwin_snapshot_keeps_the_keys_other_consumers_read(
    non_darwin, monkeypatch: pytest.MonkeyPatch, display: bool
) -> None:
    """``jarvis permissions status`` and the browser surface read exactly these."""
    port, tcc = non_darwin
    monkeypatch.setattr(probes, "display_present", lambda: display)

    snapshot = port.snapshot()

    assert snapshot["platform"] == port.platform
    assert snapshot["supported"] is False
    assert snapshot["headless"] is (not display)
    identity = snapshot["app_identity"]
    assert identity["app_name"] == APP_NAME
    assert identity["bundle_id"] is None
    assert identity["bundle_path"] is None
    assert identity["launched_as_bundle"] is False
    assert identity["stable"] is False
    assert [row["id"] for row in snapshot["permissions"]] == [item.value for item in PermissionId]
    for row in snapshot["permissions"]:
        assert row["status"] == "not_required"
        assert row["can_request"] is False
        assert row["can_open_settings"] is False
        assert row["can_reset"] is False
        assert "detail" in row  # its wording is pinned by the legacy snapshot test
        assert isinstance(row["label"], str) and row["label"]
    labels = {row["id"]: row["label"] for row in snapshot["permissions"]}
    assert labels["microphone"] == "Microphone"
    assert labels["screen_recording"] == "Screen Recording"
    assert labels["accessibility"] == "Accessibility"
    assert labels["input_monitoring"] == "Input Monitoring"
    tcc.assert_silent()


def test_legacy_non_darwin_snapshot_v1_only_keys(non_darwin) -> None:
    """The v1 keys snapshot-v2 drops or renames (see the module docstring)."""
    port, tcc = non_darwin

    snapshot = port.snapshot()

    assert set(snapshot) == {
        "platform",
        "supported",
        "headless",
        "app_identity",
        "permissions",
        "features",
        "identity_reset",
        "restart_required",
    }
    assert snapshot["identity_reset"] is None
    assert snapshot["restart_required"] is False
    identity = snapshot["app_identity"]
    assert set(identity) == {
        "app_name",
        "expected_bundle_id",
        "bundle_id",
        "bundle_path",
        "launched_as_bundle",
        "stable",
        "foreground",
    }
    assert identity["expected_bundle_id"] == EXPECTED_BUNDLE_ID
    assert identity["foreground"] is False
    assert set(snapshot["features"]) == set(FEATURE_REQUIREMENTS)
    for feature in snapshot["features"].values():
        assert feature == {
            "ready": True,
            "missing": [],
            "identity_ready": True,
            "restart_required": False,
            "active": True,
        }
    for row in snapshot["permissions"]:
        assert row["detail"] == "This operating system does not require a macOS TCC grant."
        assert row["wanted"] is True
        assert row["restart_required"] is False
        expected_required = [
            feature
            for feature, needs in FEATURE_REQUIREMENTS.items()
            if PermissionId(row["id"]) in needs
        ]
        assert row["required"] == expected_required
        assert set(row) == {
            "id",
            "label",
            "status",
            "required",
            "can_request",
            "can_open_settings",
            "can_reset",
            "restart_required",
            "detail",
            "wanted",
        }
    tcc.assert_silent()


def test_legacy_non_darwin_feature_readiness(non_darwin) -> None:
    """``FEATURE_REQUIREMENTS`` readiness is deleted by the rebuild."""
    port, tcc = non_darwin

    assert all(port.runtime_feature_ready(feature) for feature in FEATURE_REQUIREMENTS)

    tcc.assert_silent()


def test_legacy_non_darwin_dry_run_request_claims_it_would_ask(non_darwin) -> None:
    """A dry run answers before the platform check: it reports a request it would refuse."""
    port, tcc = non_darwin

    operation = port.request(PermissionId.MICROPHONE, dry_run=True)

    assert operation.ok is True
    assert operation.performed is False
    assert operation.dry_run is True
    tcc.assert_silent()


def test_survives_the_port_constructs_without_touching_a_framework() -> None:
    """Nothing initialises at construction (AP-26); the no-arg constructor stays."""
    loaded: list[str] = []

    def spy(name: str) -> object:
        loaded.append(name)
        raise AssertionError(f"construction must not import {name}")

    SystemPermissionPort(module_loader=spy)
    SystemPermissionPort(platform_name="darwin", module_loader=spy)
    assert SystemPermissionPort().platform in {"win32", "darwin", "linux"}
    assert loaded == []


def test_survives_the_process_wide_port_is_one_object() -> None:
    assert get_system_permission_port() is get_system_permission_port()
    assert isinstance(get_system_permission_port(), SystemPermissionPort)


# ---------------------------------------------------------------------------
# (b) macOS: the rules that must survive the rebuild
# ---------------------------------------------------------------------------


def test_survives_accepted_bundle_ids_are_the_managed_and_the_dmg_id() -> None:
    assert ACCEPTED_BUNDLE_IDS == (EXPECTED_BUNDLE_ID, MACOS_DMG_BUNDLE_ID)
    assert MACOS_DMG_BUNDLE_ID == "ai.personaljarvis.desktop"
    assert EXPECTED_BUNDLE_ID != MACOS_DMG_BUNDLE_ID


@pytest.mark.parametrize("running_id", [EXPECTED_BUNDLE_ID, MACOS_DMG_BUNDLE_ID])
def test_survives_reset_drops_the_rows_of_the_running_apps_own_bundle_id(
    monkeypatch: pytest.MonkeyPatch, running_id: str
) -> None:
    port, tcc = make_darwin_port(bundle_id=running_id)
    tcc.deny(MIC)
    monkeypatch.setattr(subprocess, "run", tcc.run_tccutil)

    operation = port.reset(PermissionId.MICROPHONE)

    assert operation.ok is True and operation.performed is True
    assert tcc.tccutil_calls == [["/usr/bin/tccutil", "reset", "Microphone", running_id]]
    assert tcc.state(MIC).value == "not_determined"


@pytest.mark.parametrize(
    ("bundle_id", "bundle_path"),
    [
        ("org.python.python", INSTALLED_BUNDLE_PATH),
        ("com.apple.Terminal", INSTALLED_BUNDLE_PATH),
        (None, INSTALLED_BUNDLE_PATH),
        (EXPECTED_BUNDLE_ID + ".evil", INSTALLED_BUNDLE_PATH),
        (MACOS_DMG_BUNDLE_ID + ".evil", INSTALLED_BUNDLE_PATH),
        # An accepted id at a path that is not an installed location.
        (EXPECTED_BUNDLE_ID, f"/opt/elsewhere/{APP_NAME}.app"),
        (MACOS_DMG_BUNDLE_ID, "/usr/local/bin"),
    ],
)
def test_survives_reset_is_refused_for_anything_but_the_installed_app(
    monkeypatch: pytest.MonkeyPatch, bundle_id: str | None, bundle_path: str
) -> None:
    """A development run must never be able to wipe the installed app's grants."""
    port, tcc = make_darwin_port(bundle_id=bundle_id, bundle_path=bundle_path)
    monkeypatch.setattr(subprocess, "run", tcc.run_tccutil)

    operation = port.reset(PermissionId.MICROPHONE)

    assert operation.ok is False and operation.performed is False
    assert "installed app" in operation.message
    assert tcc.tccutil_calls == []


@pytest.mark.parametrize(
    ("permission_id", "service"),
    [
        (PermissionId.MICROPHONE, "Microphone"),
        (PermissionId.SCREEN_RECORDING, "ScreenCapture"),
        (PermissionId.ACCESSIBILITY, "Accessibility"),
        (PermissionId.INPUT_MONITORING, "ListenEvent"),
        (PermissionId.EVENT_POSTING, "PostEvent"),
        (PermissionId.AUTOMATION, "AppleEvents"),
    ],
)
def test_survives_reset_names_one_tcc_service_for_the_own_bundle_only(
    monkeypatch: pytest.MonkeyPatch, permission_id: PermissionId, service: str
) -> None:
    port, tcc = make_darwin_port()
    monkeypatch.setattr(subprocess, "run", tcc.run_tccutil)

    operation = port.reset(permission_id)

    assert operation.ok is True
    assert tcc.tccutil_calls == [["/usr/bin/tccutil", "reset", service, EXPECTED_BUNDLE_ID]]


def test_survives_the_keychain_has_no_resettable_row(monkeypatch: pytest.MonkeyPatch) -> None:
    port, tcc = make_darwin_port()
    monkeypatch.setattr(subprocess, "run", tcc.run_tccutil)

    operation = port.reset(PermissionId.CREDENTIAL_STORE)

    assert operation.ok is False
    assert tcc.tccutil_calls == []


def test_survives_reads_are_live_and_never_cached() -> None:
    """A state change is visible on the very next call; every call probes again."""
    port, tcc = make_darwin_port(granted=[MIC])
    assert port.state(PermissionId.MICROPHONE) is PermissionState.GRANTED

    tcc.deny(MIC)
    assert port.state(PermissionId.MICROPHONE) is PermissionState.DENIED
    assert _row(port.snapshot(), PermissionId.MICROPHONE)["status"] == "denied"
    assert port.runtime_access_granted(PermissionId.MICROPHONE) is False

    tcc.grant(MIC)
    assert port.runtime_access_granted(PermissionId.MICROPHONE) is True

    mark = tcc.mark()
    port.state(PermissionId.MICROPHONE)
    port.state(PermissionId.MICROPHONE)
    assert len(tcc.calls_of("probe", MIC, since=mark)) == 2


def test_survives_accessibility_and_input_monitoring_are_read_live() -> None:
    port, tcc = make_darwin_port()
    assert port.state(PermissionId.ACCESSIBILITY) is PermissionState.NOT_GRANTED
    assert port.state(PermissionId.INPUT_MONITORING) is PermissionState.NOT_DETERMINED

    tcc.grant(AX)
    tcc.grant(INPUT)
    assert port.state(PermissionId.ACCESSIBILITY) is PermissionState.GRANTED
    assert port.state(PermissionId.INPUT_MONITORING) is PermissionState.GRANTED

    tcc.deny(INPUT)
    tcc.reset(AX)
    assert port.state(PermissionId.ACCESSIBILITY) is PermissionState.NOT_GRANTED
    assert port.state(PermissionId.INPUT_MONITORING) is PermissionState.DENIED


def test_survives_a_screen_grant_is_seen_through_the_live_window_oracle() -> None:
    """BUG-161: the preflight is frozen; the live probe proves the grant works now."""
    port, tcc = make_darwin_port()
    tcc.grant(SCREEN)

    assert tcc.modules["Quartz"].CGPreflightScreenCaptureAccess() is False
    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.GRANTED


def test_survives_a_frozen_screen_preflight_is_never_invented_into_a_grant() -> None:
    port, tcc = make_darwin_port(screen_grant_needs_relaunch=True)
    tcc.grant(SCREEN)

    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.NOT_GRANTED
    tcc.relaunch()
    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.GRANTED


def test_survives_reading_state_never_prompts() -> None:
    port, tcc = make_darwin_port()

    port.snapshot()
    for permission_id in PermissionId:
        port.state(permission_id)
        port.runtime_access_granted(permission_id)

    tcc.assert_no_prompts()
    assert tcc.dialogs_shown() == []


def test_survives_app_identity_of_the_installed_app() -> None:
    port, _tcc = make_darwin_port()

    identity = port.snapshot()["app_identity"]

    assert identity["app_name"] == APP_NAME
    assert identity["bundle_id"] == EXPECTED_BUNDLE_ID
    assert identity["bundle_path"] == INSTALLED_BUNDLE_PATH
    assert identity["launched_as_bundle"] is True
    assert identity["stable"] is True


@pytest.mark.parametrize(
    ("bundle_id", "bundle_path", "launched", "stable"),
    [
        (EXPECTED_BUNDLE_ID, str(Path.home() / "Applications" / f"{APP_NAME}.app"), True, True),
        (MACOS_DMG_BUNDLE_ID, INSTALLED_BUNDLE_PATH, True, True),
        (EXPECTED_BUNDLE_ID, f"/opt/elsewhere/{APP_NAME}.app", True, False),
        ("org.python.python", INSTALLED_BUNDLE_PATH, True, False),
        (None, "/usr/local/bin", False, False),
    ],
)
def test_survives_app_identity_stability_rules(
    bundle_id: str | None, bundle_path: str, launched: bool, stable: bool
) -> None:
    port, _tcc = make_darwin_port(bundle_id=bundle_id, bundle_path=bundle_path)

    identity = port.snapshot()["app_identity"]

    assert identity["bundle_id"] == bundle_id
    assert identity["bundle_path"] == bundle_path
    assert identity["launched_as_bundle"] is launched
    assert identity["stable"] is stable


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        (lambda tcc: None, PermissionState.NOT_DETERMINED),
        (lambda tcc: tcc.grant(MIC), PermissionState.GRANTED),
        (lambda tcc: tcc.deny(MIC), PermissionState.DENIED),
        (lambda tcc: tcc.restrict(MIC), PermissionState.RESTRICTED),
    ],
)
def test_survives_microphone_state_mapping(setup, expected: PermissionState) -> None:
    port, tcc = make_darwin_port()
    setup(tcc)

    assert port.state(PermissionId.MICROPHONE) is expected
    row = _row(port.snapshot(), PermissionId.MICROPHONE)
    assert row["status"] == expected.value


def test_survives_microphone_state_is_unavailable_when_the_probe_cannot_answer() -> None:
    port, tcc = make_darwin_port()
    capture_device = tcc.modules["AVFoundation"].AVCaptureDevice

    capture_device.authorizationStatusForMediaType_ = lambda _media: 99
    assert port.state(PermissionId.MICROPHONE) is PermissionState.UNAVAILABLE

    def broken(_media: object) -> int:
        raise RuntimeError("bridge exploded")

    capture_device.authorizationStatusForMediaType_ = broken
    assert port.state(PermissionId.MICROPHONE) is PermissionState.UNAVAILABLE

    fresh_port, fresh = make_darwin_port()
    fresh.drop_framework("AVFoundation")
    assert fresh_port.state(PermissionId.MICROPHONE) is PermissionState.UNAVAILABLE


def test_survives_the_automation_target_allow_list_is_music_and_spotify_only() -> None:
    assert AUTOMATION_TARGETS == (("Music", MUSIC), ("Spotify", SPOTIFY))


def test_survives_automation_probes_only_the_allow_listed_players() -> None:
    other = "com.apple.Safari"
    port, tcc = make_darwin_port(
        installed_players=[MUSIC, SPOTIFY, other], running_players=[MUSIC, SPOTIFY, other]
    )
    tcc.grant(AUTOMATION, MUSIC)

    row = _row(port.snapshot(), PermissionId.AUTOMATION)

    targets = {call.target for call in tcc.calls_of(service=AUTOMATION)}
    assert targets == {MUSIC, SPOTIFY}
    assert other not in targets
    assert tcc.requests(AUTOMATION) == []  # the status read never raises the dialog
    assert row["status"] == "not_determined"  # Spotify has no answer yet: strictest wins


def test_survives_automation_is_not_required_without_a_scriptable_player() -> None:
    port, tcc = make_darwin_port(
        installed_players=["com.apple.Safari"], running_players=["com.apple.Safari"]
    )

    assert port.state(PermissionId.AUTOMATION) is PermissionState.NOT_REQUIRED
    assert tcc.calls_of(service=AUTOMATION) == []


@pytest.mark.parametrize(
    ("permission_id", "pane"),
    [
        (PermissionId.MICROPHONE, "Privacy_Microphone"),
        (PermissionId.SCREEN_RECORDING, "Privacy_ScreenCapture"),
        (PermissionId.ACCESSIBILITY, "Privacy_Accessibility"),
        (PermissionId.INPUT_MONITORING, "Privacy_ListenEvent"),
        (PermissionId.EVENT_POSTING, "Privacy_Accessibility"),
        (PermissionId.AUTOMATION, "Privacy_Automation"),
    ],
)
def test_survives_open_settings_deep_links_to_the_matching_pane(
    permission_id: PermissionId, pane: str
) -> None:
    port, tcc = make_darwin_port()

    operation = port.open_settings(permission_id)

    assert operation.ok is True
    assert tcc.workspace_opened_urls == [
        f"x-apple.systempreferences:com.apple.preference.security?{pane}"
    ]


def test_survives_the_keychain_has_no_settings_pane() -> None:
    port, tcc = make_darwin_port()

    operation = port.open_settings(PermissionId.CREDENTIAL_STORE)

    assert operation.ok is False
    assert tcc.workspace_opened_urls == []


# ---------------------------------------------------------------------------
# (b') macOS: the wall the rebuild removes
# ---------------------------------------------------------------------------


def test_legacy_darwin_runtime_access_is_a_wall_that_never_asks() -> None:
    """Today a feature is refused by OUR preflight and macOS is never asked."""
    port, tcc = make_darwin_port()

    assert port.runtime_access_granted(PermissionId.MICROPHONE) is False
    assert port.runtime_access_granted(PermissionId.ACCESSIBILITY) is False
    tcc.assert_no_prompts()  # the wall refuses without ever asking

    tcc.grant(MIC)
    assert port.runtime_access_granted(PermissionId.MICROPHONE) is True

    # A live grant is not enough without the stable installed identity.
    terminal_port, terminal = make_darwin_port(bundle_id="com.apple.Terminal", granted=[MIC])
    assert terminal_port.state(PermissionId.MICROPHONE) is PermissionState.GRANTED
    assert terminal_port.runtime_access_granted(PermissionId.MICROPHONE) is False
    terminal.assert_no_prompts()


def test_legacy_darwin_request_is_refused_in_the_background_or_outside_the_app() -> None:
    background_port, background = make_darwin_port(foreground=False)
    unstable_port, unstable = make_darwin_port(bundle_id="org.python.python")

    refused_background = background_port.request(PermissionId.SCREEN_RECORDING)
    refused_unstable = unstable_port.request(PermissionId.SCREEN_RECORDING)

    assert refused_background.ok is False and "foreground" in refused_background.message
    assert refused_unstable.ok is False and "Terminal or Python" in refused_unstable.message
    assert background.requests() == [] and unstable.requests() == []


def test_legacy_darwin_request_flags_follow_the_state_and_hide_the_dead_button() -> None:
    port, tcc = make_darwin_port(default_policy=DialogPolicy.DENY)

    before = _row(port.snapshot(), PermissionId.MICROPHONE)
    first = port.request(PermissionId.MICROPHONE)
    after = _row(first.snapshot, PermissionId.MICROPHONE)
    second = port.request(PermissionId.MICROPHONE)

    assert before["can_request"] is True and before["status"] == "not_determined"
    assert first.ok is True and first.performed is True and first.restart_required is False
    assert after["status"] == "denied"
    assert after["can_request"] is False and after["can_reset"] is True
    assert second.ok is False and second.performed is False
    assert len(tcc.requests(MIC)) == 1  # macOS was asked exactly once


def test_legacy_darwin_snapshot_reports_feature_readiness_and_wanted() -> None:
    port, tcc = make_darwin_port()

    cold = port.snapshot()
    tcc.grant(MIC)
    warm = port.snapshot()
    ducking_off = port.snapshot(active_features=frozenset(FEATURE_REQUIREMENTS) - {"audio_ducking"})

    assert cold["features"]["voice"]["ready"] is False
    assert cold["features"]["voice"]["missing"] == ["microphone"]
    assert warm["features"]["voice"]["ready"] is True
    assert all(row["wanted"] for row in cold["permissions"])
    assert _row(ducking_off, PermissionId.AUTOMATION)["wanted"] is False
    assert ducking_off["features"]["audio_ducking"]["active"] is False
    assert cold["identity_reset"] is None


def test_legacy_darwin_a_screen_request_flags_a_restart_until_relaunch() -> None:
    port, tcc = make_darwin_port(screen_grant_needs_relaunch=True)

    operation = port.request(PermissionId.SCREEN_RECORDING)

    assert operation.ok is True and operation.restart_required is True
    assert operation.snapshot["restart_required"] is True
    row = _row(operation.snapshot, PermissionId.SCREEN_RECORDING)
    assert row["restart_required"] is True and row["can_request"] is False
    tcc.relaunch()
    assert _row(port.snapshot(), PermissionId.SCREEN_RECORDING)["restart_required"] is False


def test_legacy_darwin_open_settings_needs_the_installed_app_but_not_the_foreground() -> None:
    background_port, background = make_darwin_port(foreground=False)
    unstable_port, unstable = make_darwin_port(bundle_id="org.python.python")

    assert background_port.open_settings(PermissionId.MICROPHONE).ok is True
    refused = unstable_port.open_settings(PermissionId.MICROPHONE)

    assert background.workspace_opened_urls
    assert refused.ok is False and unstable.workspace_opened_urls == []


# ---------------------------------------------------------------------------
# (c) Consumers whose behaviour is unlikely to change by design
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform_name", ["win32", "linux"])
def test_survives_the_default_microphone_gate_is_absent_off_macos(
    monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    # A tight window: only the synchronous factory call runs with the patched name.
    with monkeypatch.context() as patch:
        patch.setattr(capture.sys, "platform", platform_name)
        gate = capture._macos_microphone_access_gate()

    assert gate is None


@pytest.mark.skipif(sys.platform == "darwin", reason="off-macOS behaviour; the host decides")
async def test_survives_microphone_capture_opens_off_macos_without_touching_the_permission_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The capture gate is a macOS-only wall: elsewhere the stream just opens."""
    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    audio = FakeAudioInput(None)  # no TCC off macOS: nothing to consult
    monkeypatch.setattr(capture, "sd", SimpleNamespace(InputStream=audio))

    mic = capture.MicrophoneCapture(device=0)
    assert mic._access_gate is None  # the default off macOS
    async with mic:
        (stream,) = audio.starts
        stream.pump()
        frames = mic.stream()
        chunk = await asyncio.wait_for(anext(frames), timeout=2.0)
        await frames.aclose()

    assert chunk.sample_rate == capture.SAMPLE_RATE
    assert any(chunk.pcm)
    assert stream.closed is True
    tcc.assert_silent()


def test_legacy_default_darwin_microphone_gate_is_the_runtime_access_wall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On macOS the default gate is ``runtime_access_granted``: it never asks."""
    port, tcc = make_darwin_port()
    install_port(monkeypatch, port)

    with monkeypatch.context() as patch:
        patch.setattr(capture.sys, "platform", "darwin")
        gate = capture._macos_microphone_access_gate()

    assert gate is not None
    assert gate() is False  # not_determined: refused, and macOS was not asked
    tcc.assert_no_prompts()
    tcc.grant(MIC)
    assert gate() is True


@pytest.mark.parametrize(
    ("platform_name", "has_hotkey", "backend_module", "backend_class"),
    [
        ("win32", True, "global_hotkeys", "GlobalHotkeysBackend"),
        ("darwin", True, "quartz", "QuartzHotkeyBackend"),
        ("linux", True, "pynput", "PynputBackend"),
        ("linux", False, "noop", "NoopBackend"),
    ],
)
def test_survives_the_hotkey_backend_factory_choice_per_platform(
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    has_hotkey: bool,
    backend_module: str,
    backend_class: str,
) -> None:
    """Windows keeps global-hotkeys, macOS the Quartz tap, Linux pynput, Wayland a no-op."""
    import importlib

    import jarvis.platform as platform_package
    import jarvis.platform.capabilities as capabilities
    from jarvis.trigger.backends import make_hotkey_backend

    # Constructing a backend must not ask the OS anything on any platform.
    port, tcc = make_darwin_port() if platform_name == "darwin" else make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    monkeypatch.setattr(platform_package, "detect_platform", lambda: platform_name)
    monkeypatch.setattr(
        capabilities, "detect_capabilities", lambda: SimpleNamespace(has_hotkey=has_hotkey)
    )

    backend = make_hotkey_backend()

    module = importlib.import_module(f"jarvis.trigger.backends.{backend_module}")
    assert type(backend) is getattr(module, backend_class)
    if platform_name == "darwin":
        tcc.assert_no_prompts()
    else:
        tcc.assert_silent()


def test_the_module_docstring_lists_every_legacy_test() -> None:
    """The red tests a later stage expects must be announced, not discovered."""
    legacy = sorted(
        name
        for name, value in globals().items()
        if name.startswith("test_legacy_") and callable(value)
    )
    documented = __doc__ or ""

    assert legacy
    assert [name for name in legacy if f"* {name}\n" not in documented] == []
