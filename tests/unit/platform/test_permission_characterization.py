"""Characterization of the macOS permission port across the just-in-time rebuild.

These tests were written on the unchanged base commit, before every protected
feature started asking macOS at first use instead of being refused by our own
preflight. The ``survives`` group is the promise the rebuild makes: Windows and
Linux behave exactly as before and the surviving macOS rules (live reads, the
own-bundle-only reset, the Automation allow-list, the pane deep links) hold. The
wall itself (the runtime-access gate, feature readiness, the v1 snapshot, the
restart flag, the foreground gate) is gone, and so are the ``test_legacy_*``
tests that pinned it; where the new contract has an observable counterpart the
old test was rewritten to assert it, under a ``survives`` name, in the same
commit that deleted the wall.

Naming contract (read this before touching a red test)

* ``test_survives_*``  - behaviour that must outlive the rebuild. A red one is a
  regression, not an intended change.
* ``test_legacy_*``    - none remain. The last test of this module fails if one
  comes back, because a test named after deleted behaviour is a test that pins
  the wall again.

The port is read through ``state`` (live, never asks), ``request_native`` (the
one native request, never evidence of a grant), ``open_pane`` and ``reset_row``;
who may ask, and when, is decided by the permission service and tested there.
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


def _exercise_every_operation(port: SystemPermissionPort) -> None:
    """Every public entry point of the port, for every permission, dry or not."""
    for permission_id in PermissionId:
        port.state(permission_id)
        port.state(permission_id, deep=True)
        port.request_native(permission_id, target=MUSIC)
        port.open_pane(permission_id)
        for dry_run in (False, True):
            port.reset_row(permission_id, dry_run=dry_run)
    port.usage_string_present(PermissionId.MICROPHONE)
    port.has_desktop_session()
    port._app_identity()


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


def test_survives_non_darwin_request_native_asks_nothing(non_darwin) -> None:
    port, tcc = non_darwin

    for permission_id in PermissionId:
        assert port.request_native(permission_id, target=MUSIC) == "unavailable", permission_id

    tcc.assert_silent()


def test_survives_non_darwin_open_pane_opens_nothing(non_darwin) -> None:
    port, tcc = non_darwin

    for permission_id in PermissionId:
        assert port.open_pane(permission_id) is False, permission_id

    assert tcc.workspace_opened_urls == []
    tcc.assert_silent()


def test_survives_non_darwin_reset_is_a_refused_no_op_and_never_runs_tccutil(non_darwin) -> None:
    port, tcc = non_darwin

    for permission_id in PermissionId:
        for dry_run in (False, True):
            operation = port.reset_row(permission_id, dry_run=dry_run)
            assert operation.ok is False, (permission_id, dry_run)
            assert operation.performed is False
            assert operation.action == "reset"
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
def test_survives_non_darwin_identity_keeps_the_values_other_consumers_read(
    non_darwin, monkeypatch: pytest.MonkeyPatch, display: bool
) -> None:
    """The status snapshot and the browser surface read exactly these values."""
    port, tcc = non_darwin
    monkeypatch.setattr(probes, "display_present", lambda: display)

    identity, headless = port._app_identity()

    assert port.platform in {"win32", "linux"}
    assert headless is (not display)
    assert identity.app_name == APP_NAME
    assert identity.bundle_id is None
    assert identity.bundle_path is None
    assert identity.launched_as_bundle is False
    assert identity.stable is False
    assert port.outside_installed_app is True
    assert port.launched_as_bundle is False
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

    operation = port.reset_row(PermissionId.MICROPHONE)

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

    operation = port.reset_row(PermissionId.MICROPHONE)

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

    operation = port.reset_row(permission_id)

    assert operation.ok is True
    assert tcc.tccutil_calls == [["/usr/bin/tccutil", "reset", service, EXPECTED_BUNDLE_ID]]


def test_survives_the_keychain_has_no_resettable_row(monkeypatch: pytest.MonkeyPatch) -> None:
    port, tcc = make_darwin_port()
    monkeypatch.setattr(subprocess, "run", tcc.run_tccutil)

    operation = port.reset_row(PermissionId.CREDENTIAL_STORE)

    assert operation.ok is False
    assert tcc.tccutil_calls == []


def test_survives_reads_are_live_and_never_cached() -> None:
    """A state change is visible on the very next call; every call probes again."""
    port, tcc = make_darwin_port(granted=[MIC])
    assert port.state(PermissionId.MICROPHONE) is PermissionState.GRANTED

    tcc.deny(MIC)
    assert port.state(PermissionId.MICROPHONE) is PermissionState.DENIED

    tcc.grant(MIC)
    assert port.state(PermissionId.MICROPHONE) is PermissionState.GRANTED

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
    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.NOT_GRANTED
    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.GRANTED


def test_survives_a_frozen_screen_preflight_is_never_invented_into_a_grant() -> None:
    port, tcc = make_darwin_port(screen_grant_needs_relaunch=True)
    tcc.grant(SCREEN)

    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.NOT_GRANTED
    tcc.relaunch()
    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.GRANTED


def test_survives_reading_state_never_prompts() -> None:
    port, tcc = make_darwin_port()

    for permission_id in PermissionId:
        port.state(permission_id)
        port.state(permission_id, deep=True)
        port.state(permission_id, target=MUSIC)

    tcc.assert_no_prompts()
    assert tcc.dialogs_shown() == []


def test_survives_app_identity_of_the_installed_app() -> None:
    port, _tcc = make_darwin_port()

    identity, headless = port._app_identity()

    assert identity.app_name == APP_NAME
    assert identity.bundle_id == EXPECTED_BUNDLE_ID
    assert identity.bundle_path == INSTALLED_BUNDLE_PATH
    assert identity.launched_as_bundle is True
    assert identity.stable is True
    assert headless is False
    assert port.outside_installed_app is False


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

    identity, _headless = port._app_identity()

    assert identity.bundle_id == bundle_id
    assert identity.bundle_path == bundle_path
    assert identity.launched_as_bundle is launched
    assert identity.stable is stable
    assert port.outside_installed_app is (not stable)


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

    state = port.state(PermissionId.AUTOMATION)

    targets = {call.target for call in tcc.calls_of(service=AUTOMATION)}
    assert targets == {MUSIC, SPOTIFY}
    assert other not in targets
    assert tcc.requests(AUTOMATION) == []  # the status read never raises the dialog
    assert state is PermissionState.NOT_DETERMINED  # Spotify has no answer yet: strictest wins


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
def test_survives_open_pane_deep_links_to_the_matching_pane(
    permission_id: PermissionId, pane: str
) -> None:
    port, tcc = make_darwin_port()

    assert port.open_pane(permission_id) is True
    assert tcc.workspace_opened_urls == [
        f"x-apple.systempreferences:com.apple.preference.security?{pane}"
    ]


def test_survives_the_keychain_has_no_settings_pane() -> None:
    port, tcc = make_darwin_port()

    assert port.open_pane(PermissionId.CREDENTIAL_STORE) is False
    assert tcc.workspace_opened_urls == []


# ---------------------------------------------------------------------------
# (b') macOS: what replaced the wall
# ---------------------------------------------------------------------------


def test_survives_a_foreign_grantee_is_read_as_the_os_reports_it() -> None:
    """Identity decides who may ASK, never what a state read says (design P6).

    The old wall refused a live grant unless the process was the installed app. The
    port now reports the grant of whatever code is responsible, and flags that it is
    not the installed app so the service can refuse to ASK on its own.
    """
    port, tcc = make_darwin_port()
    assert port.state(PermissionId.MICROPHONE) is PermissionState.NOT_DETERMINED
    assert port.state(PermissionId.ACCESSIBILITY) is PermissionState.NOT_GRANTED
    tcc.assert_no_prompts()  # a read never asks

    terminal_port, terminal = make_darwin_port(bundle_id="com.apple.Terminal", granted=[MIC])
    assert terminal_port.state(PermissionId.MICROPHONE) is PermissionState.GRANTED
    assert terminal_port.outside_installed_app is True
    terminal.assert_no_prompts()


def test_survives_request_native_reaches_the_os_whichever_the_identity_and_the_foreground() -> None:
    """Whether we may ask is the service's decision; the port only makes the call."""
    background_port, background = make_darwin_port(foreground=False)
    unstable_port, unstable = make_darwin_port(bundle_id="org.python.python")

    assert background_port.request_native(PermissionId.MICROPHONE) == "dialog_shown"
    assert unstable_port.request_native(PermissionId.MICROPHONE) == "dialog_shown"

    assert len(background.requests(MIC)) == 1
    assert len(unstable.requests(MIC)) == 1
    assert unstable_port.outside_installed_app is True


def test_survives_a_decision_on_file_is_never_asked_again_by_macos() -> None:
    port, tcc = make_darwin_port(default_policy=DialogPolicy.DENY)

    first = port.request_native(PermissionId.MICROPHONE)
    after_first = port.state(PermissionId.MICROPHONE)
    second = port.request_native(PermissionId.MICROPHONE)

    assert first == "dialog_shown" and after_first is PermissionState.DENIED
    assert second == "no_dialog"  # the status read beforehand tells a silent no-op
    assert len(tcc.dialogs_shown(MIC)) == 1  # macOS showed exactly one dialog
    assert len(tcc.ignored_requests(MIC)) == 1  # the second request was ignored


def test_survives_a_native_request_is_never_evidence_of_a_grant() -> None:
    """The Screen Recording preflight is frozen until relaunch, whatever the user answered."""
    port, tcc = make_darwin_port(screen_grant_needs_relaunch=True)

    outcome = port.request_native(PermissionId.SCREEN_RECORDING)

    assert outcome in {"dialog_shown", "no_dialog"}
    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.NOT_GRANTED
    tcc.relaunch()
    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.GRANTED


def test_survives_open_pane_works_from_the_background_and_outside_the_installed_app() -> None:
    """The Settings deep link is not a prompt: no foreground and no identity needed."""
    background_port, background = make_darwin_port(foreground=False)
    unstable_port, unstable = make_darwin_port(bundle_id="org.python.python")

    assert background_port.open_pane(PermissionId.MICROPHONE) is True
    assert unstable_port.open_pane(PermissionId.MICROPHONE) is True

    assert background.workspace_opened_urls
    assert unstable.workspace_opened_urls


# ---------------------------------------------------------------------------
# (c) Consumers whose behaviour is unlikely to change by design
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("platform_name", ["win32", "linux"])
def test_survives_the_default_microphone_gate_is_absent_off_macos(
    monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """Off macOS the capture has no access gate of its own: the stream just opens."""
    port, tcc = make_non_darwin_port(platform_name)  # type: ignore[arg-type]
    install_port(monkeypatch, port)

    assert capture.MicrophoneCapture(device=0)._access_gate is None
    tcc.assert_silent()


@pytest.mark.skipif(sys.platform == "darwin", reason="off-macOS behaviour; the host decides")
async def test_survives_microphone_capture_opens_off_macos_without_touching_the_permission_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Elsewhere than macOS the stream just opens: no permission is consulted."""
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


def test_no_test_in_this_module_pins_the_deleted_wall() -> None:
    """A ``test_legacy_*`` name would announce behaviour the rebuild deleted on purpose."""
    legacy = sorted(
        name
        for name, value in globals().items()
        if name.startswith("test_legacy_") and callable(value)
    )

    assert legacy == []
