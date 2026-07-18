"""Unit coverage for the uncached macOS system-permission port."""

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
    def __init__(self, *, active: bool = True, pid: int = 123) -> None:
        self._active = active
        self._pid = pid

    def isActive(self) -> bool:
        return self._active

    def processIdentifier(self) -> int:
        return self._pid


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
    active: bool = True,
) -> tuple[dict[str, object], dict[str, bool], _Workspace]:
    current = _RunningApp(active=active)
    workspace = _Workspace(current)
    screen = {"granted": False, "requested": False}
    event = {"listen": False, "post": False}
    ax = {"trusted": True, "prompted": False}

    def request_screen() -> bool:
        screen["requested"] = True
        screen["granted"] = True
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
) -> SystemPermissionPort:
    def load(name: str) -> object:
        if name not in modules:
            raise ModuleNotFoundError(name)
        return modules[name]

    # iohid_check defaults to "unavailable" so unit runs stay hermetic even on
    # a real Mac, where the default probe would read the machine's TCC state;
    # the credential stubs keep the host's real keyring untouched the same way.
    return SystemPermissionPort(
        platform_name="darwin",
        module_loader=load,
        iohid_check=iohid_check,
        credential_store_backend=credential_backend,
        credential_store_recover=credential_recover,
    )


def _permission(snapshot: dict, permission_id: PermissionId) -> dict:
    return next(item for item in snapshot["permissions"] if item["id"] == permission_id)


def test_permission_bundle_id_matches_installed_app_identity() -> None:
    assert EXPECTED_BUNDLE_ID == BUNDLE_ID


def test_matching_bundle_id_at_noncanonical_path_is_not_stable(tmp_path: Path) -> None:
    modules, _, _ = _native_modules()
    copied_bundle = SimpleNamespace(
        bundleIdentifier=lambda: EXPECTED_BUNDLE_ID,
        bundlePath=lambda: str(tmp_path / "Personal Jarvis.app"),
    )
    modules["Foundation"].NSBundle = SimpleNamespace(mainBundle=lambda: copied_bundle)

    assert _port(modules).snapshot()["app_identity"]["stable"] is False


def test_non_macos_degrades_to_not_required_without_native_imports() -> None:
    imports: list[str] = []
    port = SystemPermissionPort(
        platform_name="win32", module_loader=lambda name: imports.append(name)
    )

    snapshot = port.snapshot()

    assert imports == []
    assert snapshot["supported"] is False
    assert {item["status"] for item in snapshot["permissions"]} == {PermissionState.NOT_REQUIRED}
    assert all(feature["ready"] for feature in snapshot["features"].values())


def test_snapshot_maps_native_states_and_feature_readiness() -> None:
    _CaptureDevice.status = 3
    modules, _, _ = _native_modules()

    snapshot = _port(modules).snapshot()

    assert snapshot["app_identity"]["stable"] is True
    assert snapshot["app_identity"]["foreground"] is True
    assert _permission(snapshot, PermissionId.MICROPHONE)["status"] == "granted"
    assert _permission(snapshot, PermissionId.SCREEN_RECORDING)["status"] == ("not_granted")
    assert snapshot["features"]["voice"] == {
        "ready": True,
        "missing": [],
        "identity_ready": True,
        "restart_required": False,
    }
    # event_posting is granted through the trusted Accessibility fixture, so
    # only the screen-recording grant is still missing for Computer-Use.
    assert snapshot["features"]["computer_use"] == {
        "ready": False,
        "missing": ["screen_recording"],
        "identity_ready": True,
        "restart_required": False,
    }


def test_snapshot_is_uncached() -> None:
    modules, screen, _ = _native_modules()
    port = _port(modules)

    assert _permission(port.snapshot(), PermissionId.SCREEN_RECORDING)["status"] == "not_granted"
    screen["granted"] = True
    assert _permission(port.snapshot(), PermissionId.SCREEN_RECORDING)["status"] == "granted"


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


def test_runtime_access_requires_stable_identity_and_fresh_grant() -> None:
    _CaptureDevice.status = 3
    modules, _, _ = _native_modules()
    stable = _port(modules)
    unstable_modules, _, _ = _native_modules(bundle_id="org.python.python")

    assert stable.runtime_access_granted(PermissionId.MICROPHONE) is True
    assert _port(unstable_modules).runtime_access_granted(PermissionId.MICROPHONE) is False
    _CaptureDevice.status = 2
    assert stable.runtime_access_granted(PermissionId.MICROPHONE) is False


def test_runtime_access_blocks_pending_restart_even_after_native_grant() -> None:
    modules, _, _ = _native_modules()
    port = _port(modules)

    result = port.request(PermissionId.SCREEN_RECORDING)

    assert result.ok is True
    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.GRANTED
    assert port.runtime_access_granted(PermissionId.SCREEN_RECORDING) is False


def test_microphone_status_distinguishes_denied_and_restricted() -> None:
    modules, _, _ = _native_modules()
    port = _port(modules)

    _CaptureDevice.status = 2
    denied = _permission(port.snapshot(), PermissionId.MICROPHONE)
    _CaptureDevice.status = 1
    restricted = _permission(port.snapshot(), PermissionId.MICROPHONE)

    assert denied["status"] == "denied"
    assert denied["can_request"] is False
    assert restricted["status"] == "restricted"
    assert restricted["can_request"] is False


def test_request_screen_capture_calls_native_api_and_requires_restart() -> None:
    modules, screen, _ = _native_modules()

    result = _port(modules).request(PermissionId.SCREEN_RECORDING)

    assert result.ok is True
    assert result.performed is True
    assert result.restart_required is True
    assert screen["requested"] is True
    assert result.snapshot["restart_required"] is True
    assert _permission(result.snapshot, PermissionId.SCREEN_RECORDING)["restart_required"] is True


def test_restart_requirement_persists_until_the_process_restarts() -> None:
    modules, _, _ = _native_modules()
    port = _port(modules)

    port.request(PermissionId.SCREEN_RECORDING)
    later = port.snapshot()

    assert later["restart_required"] is True
    assert _permission(later, PermissionId.SCREEN_RECORDING)["restart_required"] is True


def test_request_microphone_uses_avfoundation_callback_api() -> None:
    _CaptureDevice.status = 0
    _CaptureDevice.requests = 0
    modules, _, _ = _native_modules()

    result = _port(modules).request(PermissionId.MICROPHONE)

    assert result.ok is True
    assert result.performed is True
    assert result.restart_required is False
    assert _CaptureDevice.requests == 1


def test_request_accessibility_uses_prompt_option() -> None:
    modules, _, _ = _native_modules()
    calls: list[dict[str, bool]] = []
    modules["ApplicationServices"] = SimpleNamespace(
        AXIsProcessTrusted=lambda: False,
        AXIsProcessTrustedWithOptions=lambda options: calls.append(options),
        kAXTrustedCheckOptionPrompt="prompt",
    )

    result = _port(modules).request(PermissionId.ACCESSIBILITY)

    assert result.ok is True
    assert result.performed is True
    assert result.restart_required is True
    assert result.snapshot["features"]["global_hotkeys"]["restart_required"] is True
    assert result.snapshot["features"]["global_hotkeys"]["ready"] is False
    assert calls == [{"prompt": True}]


@pytest.mark.parametrize(
    "permission_id,request_name",
    [
        (PermissionId.INPUT_MONITORING, "CGRequestListenEventAccess"),
        (PermissionId.EVENT_POSTING, "CGRequestPostEventAccess"),
    ],
)
def test_request_event_permissions_use_coregraphics(
    permission_id: PermissionId, request_name: str
) -> None:
    modules, _, _ = _native_modules()
    calls: list[str] = []
    setattr(modules["Quartz"], request_name, lambda: calls.append(request_name))
    # Untrusted Accessibility keeps event_posting requestable: a trusted AX
    # grant already implies event posting and would short-circuit to granted.
    modules["ApplicationServices"] = SimpleNamespace(
        AXIsProcessTrusted=lambda: False,
        AXIsProcessTrustedWithOptions=lambda _options: False,
        kAXTrustedCheckOptionPrompt="prompt",
    )

    result = _port(modules).request(permission_id)

    assert result.ok is True
    assert result.performed is True
    assert calls == [request_name]


_IOHID_POST = 0  # kIOHIDRequestTypePostEvent
_IOHID_LISTEN = 1  # kIOHIDRequestTypeListenEvent


def test_input_monitoring_denied_hides_request_and_keeps_settings() -> None:
    # macOS never re-prompts once the TCC state is determined; a visible
    # "request" button would silently do nothing (the dead Allow button).
    modules, _, _ = _native_modules()

    snapshot = _port(modules, iohid_check=lambda t: 1 if t == _IOHID_LISTEN else None).snapshot()

    item = _permission(snapshot, PermissionId.INPUT_MONITORING)
    assert item["status"] == "denied"
    assert item["can_request"] is False
    assert item["can_open_settings"] is True


def test_input_monitoring_not_determined_still_offers_the_prompt() -> None:
    modules, _, _ = _native_modules()

    snapshot = _port(modules, iohid_check=lambda t: 2 if t == _IOHID_LISTEN else None).snapshot()

    item = _permission(snapshot, PermissionId.INPUT_MONITORING)
    assert item["status"] == "not_determined"
    assert item["can_request"] is True


def test_input_monitoring_falls_back_to_boolean_preflight_without_iohid() -> None:
    modules, _, _ = _native_modules()

    item = _permission(_port(modules).snapshot(), PermissionId.INPUT_MONITORING)

    assert item["status"] == "not_granted"


def test_event_posting_follows_live_accessibility_grant() -> None:
    # The Accessibility grant authorizes event posting and updates live; it
    # must win over a stale per-process HID verdict so the row flips as soon
    # as the user grants Accessibility.
    modules, _, _ = _native_modules()

    snapshot = _port(modules, iohid_check=lambda _t: 1).snapshot()

    assert _permission(snapshot, PermissionId.EVENT_POSTING)["status"] == "granted"


def test_event_posting_tristate_when_accessibility_untrusted() -> None:
    modules, _, _ = _native_modules()
    modules["ApplicationServices"] = SimpleNamespace(
        AXIsProcessTrusted=lambda: False,
        AXIsProcessTrustedWithOptions=lambda _options: False,
        kAXTrustedCheckOptionPrompt="prompt",
    )

    snapshot = _port(modules, iohid_check=lambda t: 1 if t == _IOHID_POST else None).snapshot()

    item = _permission(snapshot, PermissionId.EVENT_POSTING)
    assert item["status"] == "denied"
    assert item["can_request"] is False
    assert item["can_open_settings"] is True


def test_legacy_macos_event_posting_falls_back_to_accessibility_prompt() -> None:
    modules, _, _ = _native_modules()
    delattr(modules["Quartz"], "CGPreflightPostEventAccess")
    delattr(modules["Quartz"], "CGRequestPostEventAccess")
    calls: list[dict[str, bool]] = []
    modules["ApplicationServices"] = SimpleNamespace(
        AXIsProcessTrusted=lambda: False,
        AXIsProcessTrustedWithOptions=lambda options: calls.append(options),
        kAXTrustedCheckOptionPrompt="prompt",
    )

    result = _port(modules).request(PermissionId.EVENT_POSTING)

    assert result.ok is True
    assert result.performed is True
    assert calls == [{"prompt": True}]


def test_request_refuses_unstable_or_background_identity() -> None:
    modules, screen, _ = _native_modules(bundle_id="org.python.python")
    unstable = _port(modules).request(PermissionId.SCREEN_RECORDING)
    modules, _, _ = _native_modules(active=False)
    background = _port(modules).request(PermissionId.SCREEN_RECORDING)

    assert unstable.ok is False
    assert "Terminal or Python" in unstable.message
    assert background.ok is False
    assert "foreground" in background.message
    assert screen["requested"] is False


def test_dry_run_never_invokes_native_request() -> None:
    modules, screen, _ = _native_modules()

    result = _port(modules).request(PermissionId.SCREEN_RECORDING, dry_run=True)

    assert result.ok is True
    assert result.dry_run is True
    assert result.performed is False
    assert screen["requested"] is False


def test_open_settings_uses_permission_specific_launchservices_url() -> None:
    modules, _, workspace = _native_modules()

    result = _port(modules).open_settings(PermissionId.INPUT_MONITORING)

    assert result.ok is True
    assert result.performed is True
    assert workspace.opened_urls == [
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
    ]


def test_open_settings_quits_running_system_settings_before_navigating() -> None:
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

    result = _port(modules).open_settings(PermissionId.INPUT_MONITORING)

    assert result.ok is True
    assert lookups == ["com.apple.systempreferences"]
    assert order == ["terminate", "open"]


def test_screen_capture_restart_pending_hides_the_dead_allow_button() -> None:
    # CGPreflightScreenCaptureAccess stays frozen for the process lifetime, and
    # macOS never re-prompts after the first request — a second visible Allow
    # button could only ever do nothing (live Mac finding 2026-07-18).
    modules, screen, _ = _native_modules()
    port = _port(modules)

    port.request(PermissionId.SCREEN_RECORDING)
    # Mimic the real frozen preflight: the grant is invisible until relaunch.
    screen["granted"] = False
    item = _permission(port.snapshot(), PermissionId.SCREEN_RECORDING)

    assert item["status"] == "not_granted"
    assert item["restart_required"] is True
    assert item["can_request"] is False
    assert "restart" in (item["detail"] or "").lower()


def test_missing_framework_reports_unavailable_without_raising() -> None:
    modules, _, _ = _native_modules()
    del modules["Quartz"]

    snapshot = _port(modules).snapshot()

    assert _permission(snapshot, PermissionId.SCREEN_RECORDING)["status"] == ("unavailable")
    assert snapshot["features"]["computer_use"]["ready"] is False


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

    snapshot = port.snapshot()

    assert snapshot["app_identity"]["stable"] is False
    assert {item["status"] for item in snapshot["permissions"]} == {"unavailable"}
    assert all(not feature["ready"] for feature in snapshot["features"].values())


def test_credential_store_reports_granted_while_platform_keyring_serves() -> None:
    modules, _, _ = _native_modules()

    item = _permission(_port(modules).snapshot(), PermissionId.CREDENTIAL_STORE)

    assert item["status"] == "granted"
    assert item["can_open_settings"] is False


def test_credential_store_file_fallback_is_not_granted_and_requestable() -> None:
    # A declined macOS Keychain prompt degrades config to the 0600 file
    # fallback; the row must surface that honestly and keep the retry alive.
    modules, _, _ = _native_modules()
    port = _port(modules, credential_backend=lambda: "file")

    snapshot = port.snapshot()
    item = _permission(snapshot, PermissionId.CREDENTIAL_STORE)

    assert item["status"] == "not_granted"
    assert item["can_request"] is True
    assert item["can_open_settings"] is False
    assert "Keychain" in (item["detail"] or "")
    assert snapshot["features"]["api_keys"]["ready"] is False


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

    result = port.request(PermissionId.CREDENTIAL_STORE)

    assert result.ok is True
    assert result.performed is True
    assert result.restart_required is False
    assert state["recover_calls"] == 1
    assert _permission(result.snapshot, PermissionId.CREDENTIAL_STORE)["status"] == "granted"
    assert result.snapshot["restart_required"] is False


def test_credential_store_declined_again_stays_not_granted() -> None:
    modules, _, _ = _native_modules()
    port = _port(
        modules,
        credential_backend=lambda: "file",
        credential_recover=lambda: False,
    )

    result = port.request(PermissionId.CREDENTIAL_STORE)

    assert result.ok is True
    assert result.performed is True
    assert _permission(result.snapshot, PermissionId.CREDENTIAL_STORE)["status"] == "not_granted"


def test_credential_store_open_settings_refuses_honestly() -> None:
    # There is no System Settings pane for the Keychain; a silent no-op button
    # would look like the app is broken.
    modules, _, workspace = _native_modules()
    port = _port(modules, credential_backend=lambda: "file")

    result = port.open_settings(PermissionId.CREDENTIAL_STORE)

    assert result.ok is False
    assert "System Settings pane" in result.message
    assert workspace.opened_urls == []


def test_credential_store_probe_failure_reports_unavailable() -> None:
    modules, _, _ = _native_modules()

    def broken_probe() -> str:
        raise RuntimeError("probe failed")

    item = _permission(
        _port(modules, credential_backend=broken_probe).snapshot(),
        PermissionId.CREDENTIAL_STORE,
    )

    assert item["status"] == "unavailable"
    assert item["can_request"] is False
