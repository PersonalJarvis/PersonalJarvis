"""Unit coverage for the uncached macOS system-permission port.

Hand-written stubs stand in for the native frameworks; the stateful FakeTCC world
is exercised in ``test_permission_characterization.py`` and ``test_fake_tcc.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.platform.permissions import (
    EXPECTED_BUNDLE_ID,
    PermissionId,
    PermissionState,
    SystemPermissionPort,
)
from jarvis.setup.macos_app_bundle import BUNDLE_ID


class _Bundle:
    def __init__(self, bundle_id: str | None = EXPECTED_BUNDLE_ID) -> None:
        self._bundle_id = bundle_id

    def bundleIdentifier(self) -> str | None:
        return self._bundle_id

    def bundlePath(self) -> str:
        return str(Path.home() / "Applications" / "Personal Jarvis.app")


class _RunningApp:
    """A placeholder application: the port only asks whether a window server answers."""


class _Workspace:
    def __init__(self, current: _RunningApp) -> None:
        self.current = current
        self.opened_urls: list[str] = []

    def frontmostApplication(self) -> _RunningApp:
        return self.current

    def openURL_(self, url: str) -> bool:
        self.opened_urls.append(url)
        return True


class _CaptureDevice:
    status = 3
    requests = 0

    @classmethod
    def authorizationStatusForMediaType_(cls, _media_type: str) -> int:
        return cls.status

    @classmethod
    def requestAccessForMediaType_completionHandler_(cls, _media_type, callback):
        cls.requests += 1
        callback(True)


def _native_modules(
    *,
    bundle_id: str | None = EXPECTED_BUNDLE_ID,
) -> tuple[dict[str, object], dict[str, bool], _Workspace]:
    current = _RunningApp()
    workspace = _Workspace(current)
    screen = {"granted": False, "requested": False}
    event = {"listen": False, "post": False}
    ax = {"trusted": True, "prompted": False}

    def request_screen() -> bool:
        # CGPreflightScreenCaptureAccess is frozen for the life of the process:
        # a grant given in response to this request stays INVISIBLE to the
        # preflight until the app relaunches. Flipping ``granted`` here would
        # model a macOS that does not exist and would hide every regression in
        # the restart handling, so tests that want the post-relaunch view set
        # ``screen["granted"]`` themselves.
        screen["requested"] = True
        return True

    def request_listen() -> bool:
        event["listen"] = True
        return True

    def request_post() -> bool:
        event["post"] = True
        return True

    def request_ax(options: dict[str, bool]) -> bool:
        ax["prompted"] = bool(options["prompt"])
        return ax["trusted"]

    modules = {
        "Foundation": SimpleNamespace(
            NSBundle=SimpleNamespace(mainBundle=lambda: _Bundle(bundle_id)),
            NSURL=SimpleNamespace(URLWithString_=lambda value: value),
        ),
        "AppKit": SimpleNamespace(
            NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
            NSRunningApplication=SimpleNamespace(currentApplication=lambda: current),
        ),
        "AVFoundation": SimpleNamespace(
            AVCaptureDevice=_CaptureDevice,
            AVMediaTypeAudio="audio",
            AVAuthorizationStatusNotDetermined=0,
            AVAuthorizationStatusRestricted=1,
            AVAuthorizationStatusDenied=2,
            AVAuthorizationStatusAuthorized=3,
        ),
        "Quartz": SimpleNamespace(
            CGPreflightScreenCaptureAccess=lambda: screen["granted"],
            CGRequestScreenCaptureAccess=request_screen,
            CGPreflightListenEventAccess=lambda: event["listen"],
            CGRequestListenEventAccess=request_listen,
            CGPreflightPostEventAccess=lambda: event["post"],
            CGRequestPostEventAccess=request_post,
        ),
        "ApplicationServices": SimpleNamespace(
            AXIsProcessTrusted=lambda: ax["trusted"],
            AXIsProcessTrustedWithOptions=request_ax,
            kAXTrustedCheckOptionPrompt="prompt",
        ),
    }
    return modules, screen, workspace


def _port(
    modules: dict[str, object],
    iohid_check: Callable[[int], int | None] = lambda _type: None,
    credential_backend: Callable[[], str] = lambda: "platform",
    credential_recover: Callable[[], bool] = lambda: True,
    screen_capture_live: Callable[[], bool | None] = lambda: None,
    automation_probe: Callable[[str, bool], int | None] = lambda _bundle_id, _ask: None,
) -> SystemPermissionPort:
    def load(name: str) -> object:
        if name not in modules:
            raise ModuleNotFoundError(name)
        return modules[name]

    # iohid_check defaults to "unavailable" so unit runs stay hermetic even on
    # a real Mac, where the default probe would read the machine's TCC state;
    # the credential and window-server stubs keep the host's real keyring and
    # screen untouched the same way.
    return SystemPermissionPort(
        platform_name="darwin",
        module_loader=load,
        iohid_check=iohid_check,
        screen_capture_live_check=screen_capture_live,
        credential_store_backend=credential_backend,
        credential_store_recover=credential_recover,
        automation_probe=automation_probe,
    )


def test_permission_bundle_id_matches_installed_app_identity() -> None:
    assert EXPECTED_BUNDLE_ID == BUNDLE_ID


def _identity(port: SystemPermissionPort):
    return port._app_identity()[0]


def test_matching_bundle_id_at_noncanonical_path_is_not_stable(tmp_path: Path) -> None:
    modules, _, _ = _native_modules()
    copied_bundle = SimpleNamespace(
        bundleIdentifier=lambda: EXPECTED_BUNDLE_ID,
        bundlePath=lambda: str(tmp_path / "Personal Jarvis.app"),
    )
    modules["Foundation"].NSBundle = SimpleNamespace(mainBundle=lambda: copied_bundle)
    port = _port(modules)

    assert _identity(port).stable is False
    assert port.outside_installed_app is True


def test_non_macos_degrades_to_not_required_without_native_imports() -> None:
    imports: list[str] = []
    port = SystemPermissionPort(
        platform_name="win32", module_loader=lambda name: imports.append(name)
    )

    states = {permission_id: port.state(permission_id) for permission_id in PermissionId}

    assert imports == []
    assert set(states.values()) == {PermissionState.NOT_REQUIRED}


def test_state_maps_native_states() -> None:
    _CaptureDevice.status = 3
    modules, _, _ = _native_modules()
    port = _port(modules)

    assert _identity(port).stable is True
    assert port.state(PermissionId.MICROPHONE) is PermissionState.GRANTED
    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.NOT_GRANTED
    # Event posting follows the trusted Accessibility fixture.
    assert port.state(PermissionId.EVENT_POSTING) is PermissionState.GRANTED


def test_state_is_uncached() -> None:
    modules, screen, _ = _native_modules()
    port = _port(modules)

    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.NOT_GRANTED
    screen["granted"] = True
    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.GRANTED


def test_state_probes_only_the_requested_permission() -> None:
    _CaptureDevice.status = 3
    modules, _, _ = _native_modules()
    imports: list[str] = []

    def load(name: str) -> object:
        imports.append(name)
        return modules[name]

    port = SystemPermissionPort(platform_name="darwin", module_loader=load)

    assert port.state("microphone") is PermissionState.GRANTED
    assert imports == ["AVFoundation"]


def test_state_reads_the_grant_whatever_the_identity_and_flags_a_foreign_app() -> None:
    """Identity gates ASKING (the service), never what a read reports."""
    _CaptureDevice.status = 3
    modules, _, _ = _native_modules()
    stable = _port(modules)
    unstable_modules, _, _ = _native_modules(bundle_id="org.python.python")
    unstable = _port(unstable_modules)

    assert stable.state(PermissionId.MICROPHONE) is PermissionState.GRANTED
    assert stable.outside_installed_app is False
    assert unstable.state(PermissionId.MICROPHONE) is PermissionState.GRANTED
    assert unstable.outside_installed_app is True
    _CaptureDevice.status = 2
    assert stable.state(PermissionId.MICROPHONE) is PermissionState.DENIED


def test_a_screen_request_does_not_unfreeze_the_preflight() -> None:
    modules, screen, _ = _native_modules()
    port = _port(modules)

    outcome = port.request_native(PermissionId.SCREEN_RECORDING)

    # CGRequestScreenCaptureAccess answered True, but the preflight is frozen for
    # the life of the process: the answer is never evidence of a grant.
    assert outcome == "no_dialog"
    assert screen["requested"] is True
    assert screen["granted"] is False
    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.NOT_GRANTED


def test_microphone_state_distinguishes_denied_and_restricted() -> None:
    modules, _, _ = _native_modules()
    port = _port(modules)

    _CaptureDevice.status = 2
    denied = port.state(PermissionId.MICROPHONE)
    _CaptureDevice.status = 1
    restricted = port.state(PermissionId.MICROPHONE)

    assert denied is PermissionState.DENIED
    assert restricted is PermissionState.RESTRICTED


def test_request_microphone_uses_avfoundation_callback_api() -> None:
    _CaptureDevice.status = 0
    _CaptureDevice.requests = 0
    modules, _, _ = _native_modules()

    outcome = _port(modules).request_native(PermissionId.MICROPHONE)

    assert outcome == "dialog_shown"
    assert _CaptureDevice.requests == 1


def test_request_accessibility_uses_prompt_option() -> None:
    modules, _, _ = _native_modules()
    calls: list[dict[str, bool]] = []
    modules["ApplicationServices"] = SimpleNamespace(
        AXIsProcessTrusted=lambda: False,
        AXIsProcessTrustedWithOptions=lambda options: calls.append(options),
        kAXTrustedCheckOptionPrompt="prompt",
    )

    outcome = _port(modules).request_native(PermissionId.ACCESSIBILITY)

    assert outcome == "dialog_shown"
    assert calls == [{"prompt": True}]


def test_request_input_monitoring_uses_the_listen_request_and_never_a_tap() -> None:
    modules, _, _ = _native_modules()
    calls: list[str] = []
    modules["Quartz"].CGRequestListenEventAccess = lambda: calls.append("listen") or False

    outcome = _port(modules).request_native(PermissionId.INPUT_MONITORING)

    assert outcome == "dialog_shown"
    assert calls == ["listen"]


def test_event_posting_is_asked_through_the_accessibility_prompt() -> None:
    """Event posting is an alias of Accessibility for asking: one request, one prompt."""
    modules, _, _ = _native_modules()
    post_requests: list[str] = []
    modules["Quartz"].CGRequestPostEventAccess = lambda: post_requests.append("post")
    calls: list[dict[str, bool]] = []
    modules["ApplicationServices"] = SimpleNamespace(
        AXIsProcessTrusted=lambda: False,
        AXIsProcessTrustedWithOptions=lambda options: calls.append(options),
        kAXTrustedCheckOptionPrompt="prompt",
    )

    outcome = _port(modules).request_native(PermissionId.EVENT_POSTING)

    assert outcome == "dialog_shown"
    assert calls == [{"prompt": True}]
    assert post_requests == []


_IOHID_POST = 0  # kIOHIDRequestTypePostEvent
_IOHID_LISTEN = 1  # kIOHIDRequestTypeListenEvent


def test_input_monitoring_denied_reads_denied() -> None:
    # macOS never re-prompts once the TCC state is determined; the tri-state read
    # is what tells "denied" from "never asked".
    modules, _, _ = _native_modules()

    port = _port(modules, iohid_check=lambda t: 1 if t == _IOHID_LISTEN else None)

    assert port.state(PermissionId.INPUT_MONITORING) is PermissionState.DENIED


def test_input_monitoring_not_determined_reads_not_determined() -> None:
    modules, _, _ = _native_modules()

    port = _port(modules, iohid_check=lambda t: 2 if t == _IOHID_LISTEN else None)

    assert port.state(PermissionId.INPUT_MONITORING) is PermissionState.NOT_DETERMINED


def test_input_monitoring_falls_back_to_boolean_preflight_without_iohid() -> None:
    modules, _, _ = _native_modules()

    assert _port(modules).state(PermissionId.INPUT_MONITORING) is PermissionState.NOT_GRANTED


def test_event_posting_follows_live_accessibility_grant() -> None:
    # The Accessibility grant authorizes event posting and updates live; it
    # must win over a stale per-process HID verdict so the state flips as soon
    # as the user grants Accessibility.
    modules, _, _ = _native_modules()

    port = _port(modules, iohid_check=lambda _t: 1)

    assert port.state(PermissionId.EVENT_POSTING) is PermissionState.GRANTED


def test_event_posting_tristate_when_accessibility_untrusted() -> None:
    modules, _, _ = _native_modules()
    modules["ApplicationServices"] = SimpleNamespace(
        AXIsProcessTrusted=lambda: False,
        AXIsProcessTrustedWithOptions=lambda _options: False,
        kAXTrustedCheckOptionPrompt="prompt",
    )

    port = _port(modules, iohid_check=lambda t: 1 if t == _IOHID_POST else None)

    assert port.state(PermissionId.EVENT_POSTING) is PermissionState.DENIED


def test_event_posting_without_the_post_event_api_follows_accessibility() -> None:
    modules, _, _ = _native_modules()
    delattr(modules["Quartz"], "CGPreflightPostEventAccess")
    delattr(modules["Quartz"], "CGRequestPostEventAccess")
    modules["ApplicationServices"] = SimpleNamespace(
        AXIsProcessTrusted=lambda: False,
        AXIsProcessTrustedWithOptions=lambda _options: False,
        kAXTrustedCheckOptionPrompt="prompt",
    )

    assert _port(modules).state(PermissionId.EVENT_POSTING) is PermissionState.NOT_GRANTED


def test_open_pane_uses_permission_specific_launchservices_url() -> None:
    modules, _, workspace = _native_modules()

    assert _port(modules).open_pane(PermissionId.INPUT_MONITORING) is True
    assert workspace.opened_urls == [
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
    ]


def test_open_pane_takes_a_plain_string_and_ignores_an_unknown_id() -> None:
    modules, _, workspace = _native_modules()
    port = _port(modules)

    assert port.open_pane("microphone") is True
    assert port.open_pane("not_a_permission") is False
    assert len(workspace.opened_urls) == 1


def test_open_pane_needs_a_desktop_session() -> None:
    modules, _, workspace = _native_modules()
    workspace.current = None  # frontmostApplication() is None: no window server answers

    assert _port(modules).open_pane(PermissionId.MICROPHONE) is False
    assert workspace.opened_urls == []


def test_open_pane_never_raises_when_launchservices_does() -> None:
    modules, _, workspace = _native_modules()

    def refuse(_url: str) -> bool:
        raise RuntimeError("LaunchServices is gone")

    workspace.openURL_ = refuse

    assert _port(modules).open_pane(PermissionId.MICROPHONE) is False


def test_open_pane_quits_running_system_settings_before_navigating() -> None:
    # System Settings ignores the pane anchor while already running: the URL
    # only raises the stale window (live on macOS 15.7 the Input Monitoring
    # link surfaced the last-open Files & Folders pane). The port must quit a
    # running System Settings first so LaunchServices relaunches it on the
    # requested pane.
    modules, _, workspace = _native_modules()
    order: list[str] = []

    class _SettingsApp:
        def __init__(self) -> None:
            self._terminated = False

        def terminate(self) -> None:
            order.append("terminate")
            self._terminated = True

        def isTerminated(self) -> bool:
            return self._terminated

    lookups: list[str] = []

    def lookup(bundle_id: str) -> list[_SettingsApp]:
        lookups.append(bundle_id)
        return [settings_app]

    settings_app = _SettingsApp()
    appkit = modules["AppKit"]
    appkit.NSRunningApplication.runningApplicationsWithBundleIdentifier_ = lookup
    original_open = workspace.openURL_

    def open_url(url: str) -> bool:
        order.append("open")
        return original_open(url)

    workspace.openURL_ = open_url

    assert _port(modules).open_pane(PermissionId.INPUT_MONITORING) is True
    assert lookups == ["com.apple.systempreferences"]
    assert order == ["terminate", "open"]


def test_keychain_has_no_reset_because_it_owns_no_tcc_row(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: pytest.fail("the Keychain has no tccutil row")
    )
    modules, _, _ = _native_modules()
    port = _port(modules, credential_backend=lambda: "file")

    assert port.state(PermissionId.CREDENTIAL_STORE) is PermissionState.NOT_GRANTED
    operation = port.reset_row(PermissionId.CREDENTIAL_STORE)

    assert operation.ok is False
    assert "no resettable macOS record" in operation.message


def test_missing_framework_reports_unavailable_without_raising() -> None:
    modules, _, _ = _native_modules()
    del modules["Quartz"]

    assert _port(modules).state(PermissionId.SCREEN_RECORDING) is PermissionState.UNAVAILABLE


def test_broken_native_bridge_import_fails_closed() -> None:
    def broken_loader(_name: str) -> object:
        raise OSError("incompatible native framework")

    def broken_credential_probe() -> str:
        raise OSError("credential probe unavailable")

    port = SystemPermissionPort(
        platform_name="darwin",
        module_loader=broken_loader,
        iohid_check=lambda _type: None,
        credential_store_backend=broken_credential_probe,
    )

    assert _identity(port).stable is False
    assert port.outside_installed_app is True
    assert {port.state(permission_id) for permission_id in PermissionId} == {
        PermissionState.UNAVAILABLE
    }


def test_credential_store_reports_granted_while_platform_keyring_serves() -> None:
    modules, _, _ = _native_modules()
    port = _port(modules)

    assert port.state(PermissionId.CREDENTIAL_STORE) is PermissionState.GRANTED
    assert port.open_pane(PermissionId.CREDENTIAL_STORE) is False


def test_credential_store_file_fallback_is_not_granted() -> None:
    # A declined macOS Keychain prompt degrades config to the 0600 file
    # fallback; the state must surface that honestly.
    modules, _, _ = _native_modules()
    port = _port(modules, credential_backend=lambda: "file")

    assert port.state(PermissionId.CREDENTIAL_STORE) is PermissionState.NOT_GRANTED


def test_credential_store_request_replays_recovery_and_reports_live_state() -> None:
    modules, _, _ = _native_modules()
    state = {"backend": "file", "recover_calls": 0}

    def recover() -> bool:
        state["recover_calls"] += 1
        state["backend"] = "platform"
        return True

    port = _port(
        modules,
        credential_backend=lambda: str(state["backend"]),
        credential_recover=recover,
    )

    outcome = port.request_native(PermissionId.CREDENTIAL_STORE)

    assert outcome == "no_dialog"
    assert state["recover_calls"] == 1
    assert port.state(PermissionId.CREDENTIAL_STORE) is PermissionState.GRANTED


def test_credential_store_declined_again_stays_not_granted() -> None:
    modules, _, _ = _native_modules()
    port = _port(
        modules,
        credential_backend=lambda: "file",
        credential_recover=lambda: False,
    )

    port.request_native(PermissionId.CREDENTIAL_STORE)

    assert port.state(PermissionId.CREDENTIAL_STORE) is PermissionState.NOT_GRANTED


def test_credential_store_open_pane_refuses_honestly() -> None:
    # There is no System Settings pane for the Keychain: nothing is opened.
    modules, _, workspace = _native_modules()
    port = _port(modules, credential_backend=lambda: "file")

    assert port.open_pane(PermissionId.CREDENTIAL_STORE) is False
    assert workspace.opened_urls == []


def test_credential_store_probe_failure_reports_unavailable() -> None:
    modules, _, _ = _native_modules()

    def broken_probe() -> str:
        raise RuntimeError("probe failed")

    port = _port(modules, credential_backend=broken_probe)

    assert port.state(PermissionId.CREDENTIAL_STORE) is PermissionState.UNAVAILABLE


# --- BUG-161: the app insisted permissions were missing after they were given


def test_the_app_in_the_shared_applications_folder_is_a_stable_identity() -> None:
    """Dragging the app to /Applications is normal, not a tampering signal.

    The old rule accepted only ~/Applications, so the ordinary move turned the
    installed app into an "unstable identity": the own-bundle reset was refused
    and the app was read as a foreign one (BUG-161).
    """
    modules, _, _ = _native_modules()
    shared_copy = SimpleNamespace(
        bundleIdentifier=lambda: EXPECTED_BUNDLE_ID,
        bundlePath=lambda: "/Applications/Personal Jarvis.app",
    )
    modules["Foundation"].NSBundle = SimpleNamespace(mainBundle=lambda: shared_copy)
    port = _port(modules)

    assert _identity(port).stable is True
    assert port.outside_installed_app is False


# --- The downloaded .dmg app is an installed app too (its own bundle id)


def _installed_as(modules: dict[str, object], bundle_id: str, path: str) -> None:
    """Make ``NSBundle.mainBundle()`` answer as ``bundle_id`` installed at ``path``."""
    running = SimpleNamespace(bundleIdentifier=lambda: bundle_id, bundlePath=lambda: path)
    modules["Foundation"].NSBundle = SimpleNamespace(mainBundle=lambda: running)


def test_the_downloaded_dmg_app_is_a_stable_identity() -> None:
    """The release .dmg is a PyInstaller bundle with its own bundle id.

    The identity check once accepted only the managed bundle's id, so the .dmg app
    read as a foreign app: it could neither reset its own rows nor ask on its own.
    """
    from jarvis.core.branding import MACOS_DMG_BUNDLE_ID

    _CaptureDevice.status = 3
    modules, _, _ = _native_modules()
    _installed_as(modules, MACOS_DMG_BUNDLE_ID, "/Applications/Personal Jarvis.app")
    port = _port(modules, iohid_check=lambda _type: 0)

    identity = _identity(port)

    assert identity.stable is True
    assert identity.bundle_id == MACOS_DMG_BUNDLE_ID
    assert port.outside_installed_app is False
    assert port.state(PermissionId.MICROPHONE) is PermissionState.GRANTED


@pytest.mark.parametrize(
    "bundle_id", ["org.python.python", "com.apple.Terminal", "ai.personaljarvis.desktop.evil", None]
)
def test_an_app_that_is_not_ours_is_never_a_stable_identity(bundle_id: str | None) -> None:
    """Widening the check to the .dmg id must not turn it into "any app"."""
    modules, _, _ = _native_modules()
    _installed_as(modules, bundle_id, "/Applications/Personal Jarvis.app")  # type: ignore[arg-type]
    port = _port(modules)

    assert _identity(port).stable is False
    assert port.outside_installed_app is True


@pytest.mark.parametrize(
    "running_id",
    [EXPECTED_BUNDLE_ID, "ai.personaljarvis.desktop"],
)
def test_reset_drops_the_rows_of_the_app_that_is_running(
    monkeypatch: pytest.MonkeyPatch, running_id: str
) -> None:
    """``tccutil reset`` is scoped to one bundle id, so it must be the right one.

    Resetting the managed id for the .dmg app would leave the .dmg app's own
    rows untouched, leaving a stranded grant stranded.
    """
    import subprocess

    modules, _, _ = _native_modules()
    _installed_as(modules, running_id, "/Applications/Personal Jarvis.app")
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    operation = _port(modules).reset_row(PermissionId.SCREEN_RECORDING)

    assert operation.ok is True
    assert operation.performed is True
    assert commands == [["/usr/bin/tccutil", "reset", "ScreenCapture", running_id]]


@pytest.mark.parametrize("running_id", ["org.python.python", "com.apple.Terminal", None])
def test_a_process_that_is_not_the_installed_app_cannot_reset_anything(
    monkeypatch: pytest.MonkeyPatch, running_id: str | None
) -> None:
    """A development run must not be able to wipe the installed app's grants."""
    import subprocess

    modules, _, _ = _native_modules()
    _installed_as(modules, running_id, "/Applications/Personal Jarvis.app")  # type: ignore[arg-type]
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("tccutil must not run for an unstable identity"),
    )

    operation = _port(modules).reset_row(PermissionId.SCREEN_RECORDING)

    assert operation.ok is False
    assert operation.performed is False
    assert "installed app" in operation.message


def test_a_dry_run_reset_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: pytest.fail("a dry run must not run tccutil")
    )
    modules, _, _ = _native_modules()

    operation = _port(modules).reset_row(PermissionId.MICROPHONE, dry_run=True)

    assert operation.ok is True
    assert operation.dry_run is True
    assert operation.performed is False
    assert "Would reset" in operation.message


@pytest.mark.parametrize(
    "failure",
    [
        SimpleNamespace(returncode=1, stdout="", stderr="tccutil: no such service /usr/bin/x"),
        OSError(2, "No such file or directory: '/usr/bin/tccutil'"),
    ],
)
def test_a_failed_reset_says_why_and_performs_nothing(
    monkeypatch: pytest.MonkeyPatch, failure: object
) -> None:
    import subprocess

    def fake_run(*_args, **_kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(subprocess, "run", fake_run)
    modules, _, _ = _native_modules()

    operation = _port(modules).reset_row(PermissionId.MICROPHONE)

    assert operation.ok is False
    assert operation.performed is False
    assert operation.message == "Could not reset the Microphone record."
    assert "/usr/bin" not in operation.message
    assert "tccutil" not in operation.message


def test_state_is_the_preflight_alone_unless_a_deep_read_is_asked_for() -> None:
    """The default read never enumerates windows; only ``deep=True`` runs the oracle."""
    modules, screen, _ = _native_modules()
    screen["granted"] = False
    oracle_calls: list[int] = []

    def oracle() -> bool:
        oracle_calls.append(1)
        return True

    port = _port(modules, screen_capture_live=oracle)

    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.NOT_GRANTED
    assert oracle_calls == []
    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.GRANTED
    assert oracle_calls == [1]


def test_a_live_window_probe_beats_the_frozen_screen_recording_preflight() -> None:
    """A grant given in System Settings mid-session must be seen at once.

    ``CGPreflightScreenCaptureAccess`` answers from a value frozen when the
    process first asked, so without a live probe the app reports the grant as
    missing no matter how often the user gives it.
    """
    modules, screen, _ = _native_modules()
    screen["granted"] = False

    stale = _port(modules, screen_capture_live=lambda: None)
    healed = _port(modules, screen_capture_live=lambda: True)

    assert stale.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.NOT_GRANTED
    assert healed.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.GRANTED


def test_a_live_window_probe_never_invents_a_grant_the_preflight_denies() -> None:
    """An empty window list proves nothing; only a title upgrades the verdict."""
    modules, screen, _ = _native_modules()
    screen["granted"] = False

    port = _port(modules, screen_capture_live=lambda: False)

    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.NOT_GRANTED


def test_a_crashing_live_probe_leaves_the_preflight_verdict_intact() -> None:
    def _explode() -> bool | None:
        raise RuntimeError("window server is gone")

    modules, screen, _ = _native_modules()
    screen["granted"] = False

    assert (
        _port(modules, screen_capture_live=_explode).state(PermissionId.SCREEN_RECORDING, deep=True)
        is PermissionState.NOT_GRANTED
    )


def test_window_titles_prove_the_grant_only_from_another_apps_own_layer() -> None:
    from jarvis.platform.permissions import _window_titles_are_visible

    own = [{"kCGWindowLayer": 0, "kCGWindowOwnerPID": 42, "kCGWindowName": "Jarvis"}]
    chrome = [{"kCGWindowLayer": 25, "kCGWindowOwnerPID": 7, "kCGWindowName": "Menubar"}]
    nameless = [{"kCGWindowLayer": 0, "kCGWindowOwnerPID": 7, "kCGWindowName": ""}]
    foreign = [{"kCGWindowLayer": 0, "kCGWindowOwnerPID": 7, "kCGWindowName": "Safari"}]

    assert _window_titles_are_visible(own, 42) is None
    assert _window_titles_are_visible(chrome, 42) is None
    assert _window_titles_are_visible(nameless, 42) is None
    assert _window_titles_are_visible(None, 42) is None
    assert _window_titles_are_visible(foreign, 42) is True


# --- Automation (Apple Events): the Music/Spotify consent row


_MUSIC = "com.apple.Music"
_SPOTIFY = "com.spotify.client"


class _Player:
    """An ``NSRunningApplication`` stand-in for one running scriptable player."""


def _automation_fixture(
    *,
    installed: set[str],
    running: set[str],
    answers: dict[str, int],
) -> tuple[
    dict[str, object],
    list[str],
    list[tuple[str, bool]],
    Callable[[str, bool], int | None],
]:
    """Native fakes for the Automation row plus the launch log, probe log and probe."""
    modules, _screen, workspace = _native_modules()
    workspace.URLForApplicationWithBundleIdentifier_ = (  # type: ignore[attr-defined]
        lambda bundle_id: (
            f"file:///Applications/{bundle_id}.app" if bundle_id in installed else None
        )
    )
    def running_apps(bundle_id: str) -> list[_Player]:
        return [_Player()] if bundle_id in running else []

    # Tripwire: the port never launches a player; a call would land in this log.
    launches: list[str] = []
    workspace.openApplicationAtURL_configuration_completionHandler_ = (  # type: ignore[attr-defined]
        lambda url, _configuration, _handler: launches.append(url)
    )
    appkit = modules["AppKit"]
    appkit.NSRunningApplication = SimpleNamespace(  # type: ignore[attr-defined]
        currentApplication=lambda: workspace.current,
        runningApplicationsWithBundleIdentifier_=running_apps,
    )
    probes: list[tuple[str, bool]] = []

    def probe(bundle_id: str, ask: bool) -> int | None:
        probes.append((bundle_id, ask))
        return answers.get(bundle_id)

    return modules, launches, probes, probe


def test_the_default_automation_probe_never_forwards_an_asking_flag() -> None:
    """The in-process Apple Event probe is a silent read: only the killable runner asks."""
    import inspect

    import jarvis.platform.permissions as permissions

    source = inspect.getsource(permissions._default_automation_probe)

    assert "if ask" not in source
    assert "_AE_TYPE_WILDCARD, 0))" in source


def test_automation_is_not_required_without_a_scriptable_player() -> None:
    modules, _launches, probes, probe = _automation_fixture(
        installed=set(), running=set(), answers={}
    )

    port = _port(modules, automation_probe=probe)

    assert port.state(PermissionId.AUTOMATION) is PermissionState.NOT_REQUIRED
    assert port.state(PermissionId.AUTOMATION, target=_MUSIC) is PermissionState.NOT_REQUIRED
    assert probes == []


def test_automation_reads_a_running_player_live_without_ever_asking() -> None:
    modules, _launches, probes, probe = _automation_fixture(
        installed={_MUSIC}, running={_MUSIC}, answers={_MUSIC: 0}
    )

    state = _port(modules, automation_probe=probe).state(PermissionId.AUTOMATION)

    assert state is PermissionState.GRANTED
    assert probes == [(_MUSIC, False)]  # a status probe never raises the dialog


def test_a_closed_player_reads_unknown_and_is_never_launched() -> None:
    """Apple answers only for a running target: a closed player is unknown, not "denied"."""
    modules, launches, probes, probe = _automation_fixture(
        installed={_MUSIC}, running=set(), answers={_MUSIC: 0}
    )
    port = _port(modules, automation_probe=probe)

    assert port.state(PermissionId.AUTOMATION) is PermissionState.NOT_DETERMINED
    assert port.state(PermissionId.AUTOMATION, target=_MUSIC) is PermissionState.NOT_DETERMINED
    assert launches == []
    assert probes == []


def test_automation_denial_reads_denied_and_the_reset_is_scoped_to_the_own_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modules, _launches, _probes, probe = _automation_fixture(
        installed={_MUSIC}, running={_MUSIC}, answers={_MUSIC: -1743}
    )
    port = _port(modules, automation_probe=probe)
    assert port.state(PermissionId.AUTOMATION) is PermissionState.DENIED

    commands: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        commands.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    operation = port.reset_row(PermissionId.AUTOMATION)

    assert operation.ok and operation.performed
    assert commands == [["/usr/bin/tccutil", "reset", "AppleEvents", BUNDLE_ID]]


def test_automation_strictest_player_wins() -> None:
    modules, _launches, _probes, probe = _automation_fixture(
        installed={_MUSIC, _SPOTIFY},
        running={_SPOTIFY},
        answers={_SPOTIFY: -1744},
    )

    # Music is not running (unknown) and Spotify has not been asked yet.
    assert (
        _port(modules, automation_probe=probe).state(PermissionId.AUTOMATION)
        is PermissionState.NOT_DETERMINED
    )

    both_modules, _l, _p, both_probe = _automation_fixture(
        installed={_MUSIC, _SPOTIFY},
        running={_MUSIC, _SPOTIFY},
        answers={_MUSIC: 0, _SPOTIFY: -1743},
    )
    assert (
        _port(both_modules, automation_probe=both_probe).state(PermissionId.AUTOMATION)
        is PermissionState.DENIED
    )

    granted_modules, _l, _p, granted_probe = _automation_fixture(
        installed={_MUSIC, _SPOTIFY},
        running={_MUSIC, _SPOTIFY},
        answers={_MUSIC: 0, _SPOTIFY: 0},
    )
    assert (
        _port(granted_modules, automation_probe=granted_probe).state(PermissionId.AUTOMATION)
        is PermissionState.GRANTED
    )


# --- Leftover state files of the earlier permission wall


def test_the_leftover_state_files_are_removed_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.platform import permissions as permissions_module

    for name in ("macos-tcc-reset.json", "macos-automation-consent.json", "keep.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(permissions_module, "_leftover_state_dir", lambda: tmp_path)

    permissions_module.remove_leftover_state_files()
    permissions_module.remove_leftover_state_files()  # a second run finds nothing: no error

    assert sorted(path.name for path in tmp_path.iterdir()) == ["keep.json"]


def test_the_leftover_cleanup_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from jarvis.platform import permissions as permissions_module

    # A directory with the file's name makes unlink fail with an OSError.
    (tmp_path / "macos-tcc-reset.json").mkdir()
    monkeypatch.setattr(permissions_module, "_leftover_state_dir", lambda: tmp_path)
    with caplog.at_level(logging.DEBUG, logger=permissions_module.log.name):
        permissions_module.remove_leftover_state_files()

    def locate_fails() -> Path:
        raise RuntimeError("no data directory")

    monkeypatch.setattr(permissions_module, "_leftover_state_dir", locate_fails)
    permissions_module.remove_leftover_state_files()

    assert "Could not remove the leftover macos-tcc-reset.json." in caplog.text
