"""Uncached macOS system-permission probes and user-initiated requests.

macOS TCC permissions cannot be installed or granted programmatically.  This
port reports the native state on every call and exposes only Apple's supported
prompt/settings flows.  Platform frameworks are imported lazily so a base or
headless installation remains importable on every operating system.
"""

from __future__ import annotations

import importlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jarvis.core.branding import (
    MACOS_APP_NAME as APP_NAME,
)
from jarvis.core.branding import (
    MACOS_BUNDLE_ID as EXPECTED_BUNDLE_ID,
)

from . import PlatformName, detect_platform

log = logging.getLogger(__name__)

_SYSTEM_SETTINGS_BUNDLE_ID = "com.apple.systempreferences"


class PermissionId(StrEnum):
    """Stable identifiers shared by the API and desktop permission UI."""

    MICROPHONE = "microphone"
    SCREEN_RECORDING = "screen_recording"
    ACCESSIBILITY = "accessibility"
    INPUT_MONITORING = "input_monitoring"
    EVENT_POSTING = "event_posting"
    # Not a TCC grant: the macOS Keychain prompts per item at first access
    # (typically right at app start, when API keys are read). Users who deny
    # it silently land on the file fallback and read the prompt as suspicious
    # unless the UI names and explains it like every other permission.
    CREDENTIAL_STORE = "credential_store"


class PermissionState(StrEnum):
    """Cross-platform permission states; never infer denial from uncertainty."""

    GRANTED = "granted"
    NOT_DETERMINED = "not_determined"
    DENIED = "denied"
    RESTRICTED = "restricted"
    NOT_GRANTED = "not_granted"
    UNAVAILABLE = "unavailable"
    NOT_REQUIRED = "not_required"


FEATURE_REQUIREMENTS: dict[str, tuple[PermissionId, ...]] = {
    "voice": (PermissionId.MICROPHONE,),
    "computer_use": (
        PermissionId.SCREEN_RECORDING,
        PermissionId.ACCESSIBILITY,
        PermissionId.EVENT_POSTING,
    ),
    "global_hotkeys": (
        PermissionId.ACCESSIBILITY,
        PermissionId.INPUT_MONITORING,
    ),
    "window_control": (PermissionId.ACCESSIBILITY,),
    "api_keys": (PermissionId.CREDENTIAL_STORE,),
}

_LABELS: dict[PermissionId, str] = {
    PermissionId.MICROPHONE: "Microphone",
    PermissionId.SCREEN_RECORDING: "Screen Recording",
    PermissionId.ACCESSIBILITY: "Accessibility",
    PermissionId.INPUT_MONITORING: "Input Monitoring",
    PermissionId.EVENT_POSTING: "Input Control",
    PermissionId.CREDENTIAL_STORE: "Keychain (API keys)",
}

_SETTINGS_URLS: dict[PermissionId, str] = {
    PermissionId.MICROPHONE: (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
    ),
    PermissionId.SCREEN_RECORDING: (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
    ),
    PermissionId.ACCESSIBILITY: (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    ),
    PermissionId.INPUT_MONITORING: (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
    ),
    PermissionId.EVENT_POSTING: (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    ),
}

# IOKit HID access constants (IOHIDCheckAccess, macOS 10.15+). The header
# declares both enums as CF_ENUM(uint64_t); the raw values are stable ABI.
_IOHID_REQUEST_POST_EVENT = 0  # kIOHIDRequestTypePostEvent
_IOHID_REQUEST_LISTEN_EVENT = 1  # kIOHIDRequestTypeListenEvent
_IOHID_ACCESS_STATES: dict[int, PermissionState] = {
    0: PermissionState.GRANTED,  # kIOHIDAccessTypeGranted
    1: PermissionState.DENIED,  # kIOHIDAccessTypeDenied
    2: PermissionState.NOT_DETERMINED,  # kIOHIDAccessTypeUnknown
}


def _default_iohid_check(request_type: int) -> int | None:
    """Query IOKit's tri-state HID access check; ``None`` when unavailable.

    macOS shows the Input Monitoring / event-posting prompt only while the
    TCC state is still undetermined. The boolean ``CGPreflight*`` calls fold
    "never asked" and "denied" into one value, so only this tri-state probe
    lets the UI know when a request would silently do nothing.
    """
    try:
        import ctypes

        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        check = iokit.IOHIDCheckAccess
        check.restype = ctypes.c_uint64
        check.argtypes = [ctypes.c_uint64]
        return int(check(request_type))
    except Exception:  # noqa: BLE001 - a missing native bridge falls back
        return None


def _default_credential_store_backend() -> str:
    """Ask the config layer which credential backend is live right now."""
    from jarvis.core.config import credential_store_backend

    return credential_store_backend()


def _default_credential_store_recover() -> bool:
    """Retry the OS credential store; on macOS this re-triggers the prompt."""
    from jarvis.core.config import try_recover_platform_credential_store

    return try_recover_platform_credential_store()


# tccutil service names for the per-permission reset recovery. Keychain
# (credential_store) is not TCC-governed and has no resettable row.
_TCC_RESET_SERVICES: dict[PermissionId, str] = {
    PermissionId.MICROPHONE: "Microphone",
    PermissionId.SCREEN_RECORDING: "ScreenCapture",
    PermissionId.ACCESSIBILITY: "Accessibility",
    PermissionId.INPUT_MONITORING: "ListenEvent",
    PermissionId.EVENT_POSTING: "PostEvent",
}

_READY_STATES = frozenset({PermissionState.GRANTED, PermissionState.NOT_REQUIRED})
_RESTART_AFTER_CHANGE = frozenset(
    {
        PermissionId.SCREEN_RECORDING,
        # The global-hotkey backend exits without creating a listener when
        # Accessibility is absent. A restart is therefore required even
        # though AX itself can observe a newly granted value immediately.
        PermissionId.ACCESSIBILITY,
        PermissionId.INPUT_MONITORING,
    }
)


@dataclass(frozen=True)
class AppIdentity:
    app_name: str
    expected_bundle_id: str
    bundle_id: str | None
    bundle_path: str | None
    launched_as_bundle: bool
    stable: bool
    foreground: bool


@dataclass(frozen=True)
class PermissionStatus:
    id: str
    label: str
    status: str
    required: tuple[str, ...]
    can_request: bool
    can_open_settings: bool
    restart_required: bool
    detail: str | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["required"] = list(self.required)
        return payload


@dataclass(frozen=True)
class PermissionOperation:
    ok: bool
    permission_id: str
    action: str
    performed: bool
    dry_run: bool
    restart_required: bool
    message: str
    snapshot: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SystemPermissionPort:
    """Read and request OS permissions without caching native state."""

    def __init__(
        self,
        *,
        platform_name: PlatformName | None = None,
        module_loader: Callable[[str], Any] = importlib.import_module,
        iohid_check: Callable[[int], int | None] = _default_iohid_check,
        credential_store_backend: Callable[[], str] = _default_credential_store_backend,
        credential_store_recover: Callable[[], bool] = _default_credential_store_recover,
    ) -> None:
        self._platform_name = platform_name
        self._module_loader = module_loader
        self._iohid_check = iohid_check
        self._credential_store_backend = credential_store_backend
        self._credential_store_recover = credential_store_recover
        # This is operation state, not a cached permission probe. The set lives
        # only for the current process and therefore clears exactly when the
        # required app restart has happened.
        self._restart_required: set[PermissionId] = set()

    @property
    def platform(self) -> PlatformName:
        return self._platform_name or detect_platform()

    def _load(self, module: str) -> Any | None:
        try:
            return self._module_loader(module)
        except Exception:  # noqa: BLE001 - a broken native bridge fails closed
            log.debug("Native permission framework %s is unavailable.", module, exc_info=True)
            return None

    def _app_identity(self) -> tuple[AppIdentity, bool]:
        if self.platform != "darwin":
            from .probes import display_present

            return (
                AppIdentity(
                    app_name=APP_NAME,
                    expected_bundle_id=EXPECTED_BUNDLE_ID,
                    bundle_id=None,
                    bundle_path=None,
                    launched_as_bundle=False,
                    stable=False,
                    foreground=False,
                ),
                not display_present(),
            )

        bundle_id: str | None = None
        bundle_path: str | None = None
        foundation = self._load("Foundation")
        if foundation is not None:
            try:
                bundle = foundation.NSBundle.mainBundle()
                raw_id = bundle.bundleIdentifier()
                raw_path = bundle.bundlePath()
                bundle_id = str(raw_id) if raw_id else None
                bundle_path = str(raw_path) if raw_path else None
            except Exception:  # noqa: BLE001 - native metadata is advisory
                log.debug("Could not read the current macOS app identity.", exc_info=True)

        launched_as_bundle = bool(bundle_path and ".app/" in f"{bundle_path}/")
        canonical_path = False
        if launched_as_bundle and bundle_path:
            try:
                expected_path = Path.home() / "Applications" / f"{APP_NAME}.app"
                canonical_path = Path(bundle_path).resolve() == expected_path.resolve()
            except (OSError, ValueError):
                canonical_path = False
        stable = bundle_id == EXPECTED_BUNDLE_ID and launched_as_bundle and canonical_path
        foreground = False
        headless = True
        appkit = self._load("AppKit")
        if appkit is not None:
            try:
                workspace = appkit.NSWorkspace.sharedWorkspace()
                frontmost = workspace.frontmostApplication()
                current = appkit.NSRunningApplication.currentApplication()
                headless = frontmost is None
                if frontmost is not None and current is not None:
                    foreground = bool(
                        current.isActive() or frontmost.processIdentifier() == os.getpid()
                    )
            except Exception:  # noqa: BLE001 - fail closed for prompt safety
                headless = True
                foreground = False

        return (
            AppIdentity(
                app_name=APP_NAME,
                expected_bundle_id=EXPECTED_BUNDLE_ID,
                bundle_id=bundle_id,
                bundle_path=bundle_path,
                launched_as_bundle=launched_as_bundle,
                stable=stable,
                foreground=foreground,
            ),
            headless,
        )

    def _microphone_state(self) -> PermissionState:
        av = self._load("AVFoundation")
        if av is None:
            return PermissionState.UNAVAILABLE
        try:
            raw = av.AVCaptureDevice.authorizationStatusForMediaType_(av.AVMediaTypeAudio)
            mapping = {
                int(
                    getattr(av, "AVAuthorizationStatusNotDetermined", 0)
                ): PermissionState.NOT_DETERMINED,
                int(getattr(av, "AVAuthorizationStatusRestricted", 1)): PermissionState.RESTRICTED,
                int(getattr(av, "AVAuthorizationStatusDenied", 2)): PermissionState.DENIED,
                int(getattr(av, "AVAuthorizationStatusAuthorized", 3)): PermissionState.GRANTED,
            }
            return mapping.get(int(raw), PermissionState.UNAVAILABLE)
        except Exception:  # noqa: BLE001 - native probes never crash callers
            return PermissionState.UNAVAILABLE

    def _boolean_state(self, module_name: str, function_name: str) -> PermissionState:
        module = self._load(module_name)
        function = getattr(module, function_name, None) if module is not None else None
        if not callable(function):
            return PermissionState.UNAVAILABLE
        try:
            return PermissionState.GRANTED if bool(function()) else PermissionState.NOT_GRANTED
        except Exception:  # noqa: BLE001 - native probes never crash callers
            return PermissionState.UNAVAILABLE

    def _iohid_state(self, request_type: int) -> PermissionState | None:
        """Tri-state TCC probe that separates "denied" from "not asked yet"."""
        try:
            raw = self._iohid_check(request_type)
        except Exception:  # noqa: BLE001 - native probes never crash callers
            return None
        if raw is None:
            return None
        return _IOHID_ACCESS_STATES.get(int(raw))

    def _credential_store_state(self) -> PermissionState:
        """Map the live credential backend onto a permission state.

        The macOS Keychain has no TCC preflight; the observable truth is which
        keyring backend serves this process. A declined Keychain prompt makes
        the next read raise, config degrades to the 0600 file fallback, and
        this row turns "not granted" — recoverable through the request flow,
        which replays the failed read so macOS prompts again.
        """
        try:
            backend = self._credential_store_backend()
        except Exception:  # noqa: BLE001 - a broken probe fails closed
            return PermissionState.UNAVAILABLE
        if backend == "platform":
            return PermissionState.GRANTED
        if backend == "file":
            return PermissionState.NOT_GRANTED
        return PermissionState.UNAVAILABLE

    def _state(self, permission_id: PermissionId) -> PermissionState:
        if self.platform != "darwin":
            return PermissionState.NOT_REQUIRED
        if permission_id is PermissionId.CREDENTIAL_STORE:
            return self._credential_store_state()
        if permission_id is PermissionId.MICROPHONE:
            return self._microphone_state()
        if permission_id is PermissionId.SCREEN_RECORDING:
            return self._boolean_state("Quartz", "CGPreflightScreenCaptureAccess")
        if permission_id is PermissionId.ACCESSIBILITY:
            return self._boolean_state("ApplicationServices", "AXIsProcessTrusted")
        if permission_id is PermissionId.INPUT_MONITORING:
            # macOS shows the Input Monitoring prompt only while the state is
            # still undetermined (an app that ever created an event listener
            # is auto-registered as denied). Without the tri-state the UI
            # offers a request that would silently do nothing.
            state = self._iohid_state(_IOHID_REQUEST_LISTEN_EVENT)
            if state is not None:
                return state
            return self._boolean_state("Quartz", "CGPreflightListenEventAccess")
        # Event posting: the Accessibility grant authorizes posting input
        # events on macOS and there is no second prompt for it. AX reads the
        # live TCC value, unlike the CGPreflight result that is frozen per
        # process, so a mid-session Accessibility grant flips this row too.
        ax_state = self._boolean_state("ApplicationServices", "AXIsProcessTrusted")
        if ax_state is PermissionState.GRANTED:
            return PermissionState.GRANTED
        state = self._iohid_state(_IOHID_REQUEST_POST_EVENT)
        if state is not None:
            return state
        quartz = self._load("Quartz")
        if callable(getattr(quartz, "CGPreflightPostEventAccess", None)):
            return self._boolean_state("Quartz", "CGPreflightPostEventAccess")
        # Older supported macOS releases protect CGEvent posting through the
        # Accessibility grant and do not expose the separate PostEvent API.
        return ax_state

    def state(self, permission_id: PermissionId | str) -> PermissionState:
        """Probe one permission directly without constructing a full snapshot."""
        return self._state(PermissionId(permission_id))

    def runtime_access_granted(self, permission_id: PermissionId | str) -> bool:
        """Fail closed unless this installed app can use the grant right now."""
        resolved = PermissionId(permission_id)
        if self.platform != "darwin":
            return self._state(resolved) in _READY_STATES
        identity, _headless = self._app_identity()
        return (
            identity.stable
            and resolved not in self._restart_required
            and self._state(resolved) is PermissionState.GRANTED
        )

    def runtime_feature_ready(self, feature: str) -> bool:
        """Check every live grant for a feature under one stable app identity."""
        requirements = FEATURE_REQUIREMENTS[feature]
        if self.platform != "darwin":
            return all(self._state(item) in _READY_STATES for item in requirements)
        identity, _headless = self._app_identity()
        return identity.stable and all(
            item not in self._restart_required and self._state(item) is PermissionState.GRANTED
            for item in requirements
        )

    def _requester_available(self, permission_id: PermissionId) -> bool:
        if permission_id is PermissionId.CREDENTIAL_STORE:
            return True
        if permission_id is PermissionId.MICROPHONE:
            module = self._load("AVFoundation")
            owner = getattr(module, "AVCaptureDevice", None)
            return callable(getattr(owner, "requestAccessForMediaType_completionHandler_", None))
        if permission_id is PermissionId.ACCESSIBILITY:
            module = self._load("ApplicationServices")
            return callable(getattr(module, "AXIsProcessTrustedWithOptions", None))
        module = self._load("Quartz")
        name = {
            PermissionId.SCREEN_RECORDING: "CGRequestScreenCaptureAccess",
            PermissionId.INPUT_MONITORING: "CGRequestListenEventAccess",
            PermissionId.EVENT_POSTING: "CGRequestPostEventAccess",
        }[permission_id]
        native_request = getattr(module, name, None)
        if permission_id is not PermissionId.EVENT_POSTING or callable(native_request):
            return callable(native_request)
        app_services = self._load("ApplicationServices")
        return callable(getattr(app_services, "AXIsProcessTrustedWithOptions", None))

    @staticmethod
    def _detail(state: PermissionState) -> str | None:
        return {
            PermissionState.GRANTED: None,
            PermissionState.NOT_REQUIRED: (
                "This operating system does not require a macOS TCC grant."
            ),
            PermissionState.NOT_DETERMINED: "The user has not chosen yet.",
            PermissionState.DENIED: "Access was denied; use System Settings.",
            PermissionState.RESTRICTED: ("Access is restricted by the system or device policy."),
            PermissionState.NOT_GRANTED: "Access has not been granted.",
            PermissionState.UNAVAILABLE: (
                "The native permission API is unavailable in this installation."
            ),
        }[state]

    def snapshot(self) -> dict[str, Any]:
        """Return a fresh native snapshot; no permission result is retained."""
        identity, headless = self._app_identity()
        statuses: list[PermissionStatus] = []
        states: dict[PermissionId, PermissionState] = {}
        eligible = (
            self.platform == "darwin" and identity.stable and identity.foreground and not headless
        )
        for permission_id in PermissionId:
            state = self._state(permission_id)
            states[permission_id] = state
            required = tuple(
                feature
                for feature, requirements in FEATURE_REQUIREMENTS.items()
                if permission_id in requirements
            )
            restart_pending = permission_id in self._restart_required
            # After the first request/settings visit macOS never re-prompts in
            # this process (and the Screen Recording preflight stays frozen
            # until relaunch), so a second request button would be a dead
            # control — hide it and let the restart call-to-action take over.
            can_request = (
                eligible
                and not restart_pending
                and self._requester_available(permission_id)
                and state
                not in {
                    PermissionState.GRANTED,
                    PermissionState.NOT_REQUIRED,
                    PermissionState.RESTRICTED,
                    PermissionState.DENIED,
                    PermissionState.UNAVAILABLE,
                }
            )
            detail = self._detail(state)
            if (
                permission_id is PermissionId.CREDENTIAL_STORE
                and state is PermissionState.NOT_GRANTED
            ):
                detail = (
                    "Keychain access was declined, so API keys are kept in a "
                    "local file for now. Allow access to store them encrypted "
                    "in the macOS Keychain."
                )
            if restart_pending and state not in _READY_STATES:
                detail = (
                    "Permission changes made in System Settings take effect "
                    "after Personal Jarvis restarts."
                )
            statuses.append(
                PermissionStatus(
                    id=permission_id.value,
                    label=_LABELS[permission_id],
                    status=state.value,
                    required=required,
                    can_request=can_request,
                    # The Keychain has no System Settings pane; its only
                    # recovery path is the request flow above.
                    can_open_settings=eligible and permission_id in _SETTINGS_URLS,
                    restart_required=restart_pending,
                    detail=detail,
                )
            )

        features: dict[str, dict[str, Any]] = {}
        for feature, requirements in FEATURE_REQUIREMENTS.items():
            missing = [
                permission_id.value
                for permission_id in requirements
                if states[permission_id] not in _READY_STATES
            ]
            identity_ready = self.platform != "darwin" or identity.stable
            restart_required = any(
                permission_id in self._restart_required for permission_id in requirements
            )
            features[feature] = {
                "ready": not missing and identity_ready and not restart_required,
                "missing": missing,
                "identity_ready": identity_ready,
                "restart_required": restart_required,
            }

        return {
            "platform": self.platform,
            "supported": self.platform == "darwin",
            "headless": headless,
            "app_identity": asdict(identity),
            "permissions": [status.to_dict() for status in statuses],
            "features": features,
            "restart_required": bool(self._restart_required),
        }

    def _eligibility_error(self, snapshot: dict[str, Any]) -> str | None:
        if self.platform != "darwin":
            return "macOS permission requests are not required on this platform."
        if snapshot["headless"]:
            return "Permission requests require an interactive macOS desktop session."
        identity = snapshot["app_identity"]
        if not identity["stable"]:
            return (
                "Relaunch Personal Jarvis from its installed app before requesting "
                "permissions; granting access to Terminal or Python is unsafe."
            )
        if not identity["foreground"]:
            return "Bring Personal Jarvis to the foreground and try again."
        return None

    def _native_request(self, permission_id: PermissionId) -> None:
        if permission_id is PermissionId.CREDENTIAL_STORE:
            # Replaying the failed Keychain read is the only supported way to
            # make macOS show the prompt again; the outcome (allowed or denied
            # once more) lands honestly in the after-snapshot.
            self._credential_store_recover()
            return
        if permission_id is PermissionId.MICROPHONE:
            av = self._load("AVFoundation")
            av.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                av.AVMediaTypeAudio, lambda _granted: None
            )
            return
        if permission_id is PermissionId.ACCESSIBILITY:
            app_services = self._load("ApplicationServices")
            prompt_key = app_services.kAXTrustedCheckOptionPrompt
            app_services.AXIsProcessTrustedWithOptions({prompt_key: True})
            return
        quartz = self._load("Quartz")
        name = {
            PermissionId.SCREEN_RECORDING: "CGRequestScreenCaptureAccess",
            PermissionId.INPUT_MONITORING: "CGRequestListenEventAccess",
            PermissionId.EVENT_POSTING: "CGRequestPostEventAccess",
        }[permission_id]
        requester = getattr(quartz, name, None)
        if callable(requester):
            requester()
            return
        if permission_id is PermissionId.EVENT_POSTING:
            app_services = self._load("ApplicationServices")
            prompt_key = app_services.kAXTrustedCheckOptionPrompt
            app_services.AXIsProcessTrustedWithOptions({prompt_key: True})
            return
        raise RuntimeError(f"Native permission request is unavailable: {name}")

    def request(self, permission_id: PermissionId, *, dry_run: bool = False) -> PermissionOperation:
        """Invoke Apple's supported prompt API after identity/foreground checks."""
        before = self.snapshot()
        current = next(item for item in before["permissions"] if item["id"] == permission_id.value)
        if dry_run:
            return PermissionOperation(
                ok=True,
                permission_id=permission_id.value,
                action="request",
                performed=False,
                dry_run=True,
                restart_required=False,
                message=f"Would request {_LABELS[permission_id]} access.",
                snapshot=before,
            )
        error = self._eligibility_error(before)
        if error is not None:
            return PermissionOperation(
                False, permission_id.value, "request", False, False, False, error, before
            )
        if current["status"] == PermissionState.GRANTED.value:
            restart = permission_id in self._restart_required
            return PermissionOperation(
                True,
                permission_id.value,
                "request",
                False,
                False,
                restart,
                f"{_LABELS[permission_id]} access is already granted.",
                before,
            )
        if not current["can_request"]:
            return PermissionOperation(
                False,
                permission_id.value,
                "request",
                False,
                False,
                False,
                current["detail"] or "This permission cannot be requested now.",
                before,
            )
        try:
            self._native_request(permission_id)
        except Exception as exc:  # noqa: BLE001 - native request boundary
            return PermissionOperation(
                False,
                permission_id.value,
                "request",
                False,
                False,
                False,
                f"The native permission request failed: {type(exc).__name__}.",
                self.snapshot(),
            )
        restart = permission_id in _RESTART_AFTER_CHANGE
        if restart:
            self._restart_required.add(permission_id)
        after = self.snapshot()
        return PermissionOperation(
            True,
            permission_id.value,
            "request",
            True,
            False,
            restart,
            (
                "Permission requested. Restart Personal Jarvis after granting access."
                if restart
                else "Permission requested; the status will update after your choice."
            ),
            after,
        )

    def _quit_system_settings(self, appkit: Any) -> None:
        """Close a running System Settings so the pane deep link can navigate.

        System Settings ignores the ``x-apple.systempreferences`` anchor while
        it is already running: the URL merely raises the existing window on
        whatever pane it last showed (observed live on macOS 15.7 — the Input
        Monitoring link surfaced the stale Files & Folders pane instead).
        Terminating first makes LaunchServices relaunch it directly on the
        requested pane. ``NSRunningApplication.terminate`` needs no TCC grant;
        everything here is best-effort and never blocks the open call.
        """
        runner = getattr(appkit, "NSRunningApplication", None)
        lookup = getattr(runner, "runningApplicationsWithBundleIdentifier_", None)
        if not callable(lookup):
            return
        try:
            running = list(lookup(_SYSTEM_SETTINGS_BUNDLE_ID) or [])
            if not running:
                return
            for app in running:
                app.terminate()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if all(bool(app.isTerminated()) for app in running):
                    break
                time.sleep(0.05)
        except Exception:  # noqa: BLE001 - closing Settings is best-effort
            log.debug("Could not close a running System Settings.", exc_info=True)

    def _open_settings(self, permission_id: PermissionId) -> bool:
        appkit = self._load("AppKit")
        foundation = self._load("Foundation")
        if appkit is None or foundation is None:
            return False
        self._quit_system_settings(appkit)
        url = foundation.NSURL.URLWithString_(_SETTINGS_URLS[permission_id])
        return bool(appkit.NSWorkspace.sharedWorkspace().openURL_(url))

    def reset(
        self, permission_id: PermissionId, *, dry_run: bool = False
    ) -> PermissionOperation:
        """Drop this app's own TCC row so the native prompt can appear again.

        The in-app way out of the auto-denied trap: once ANY build of the
        app ever created an input listener before the user was asked - or a
        signature change orphaned the recorded grant (BUG-083) - macOS
        silently registers the app as DENIED and suppresses every further
        prompt. The permissions view then shows a dead "Denied" forever.
        ``tccutil reset <service> <our bundle id>`` returns that one row to
        "not determined" (never touching other apps' grants), so the real
        system dialog can fire again on the next request.
        """
        before = self.snapshot()
        service = _TCC_RESET_SERVICES.get(permission_id)
        if self.platform != "darwin" or service is None:
            return PermissionOperation(
                False,
                permission_id.value,
                "reset",
                False,
                dry_run,
                False,
                f"{_LABELS[permission_id]} has no resettable macOS record.",
                before,
            )
        if dry_run:
            return PermissionOperation(
                True,
                permission_id.value,
                "reset",
                False,
                True,
                False,
                f"Would reset this app's {_LABELS[permission_id]} record.",
                before,
            )
        import subprocess  # lazy: this method is darwin-only at runtime

        from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
        from jarvis.setup.macos_app_bundle import BUNDLE_ID

        try:
            result = subprocess.run(
                ["/usr/bin/tccutil", "reset", service, BUNDLE_ID],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            performed = result.returncode == 0
            detail = (result.stderr or result.stdout or "").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            performed = False
            detail = str(exc)
        if not performed:
            return PermissionOperation(
                False,
                permission_id.value,
                "reset",
                False,
                False,
                False,
                f"Could not reset the {_LABELS[permission_id]} record: "
                f"{detail[-200:] or 'unknown error'}",
                before,
            )
        self._restart_required.discard(permission_id)
        return PermissionOperation(
            True,
            permission_id.value,
            "reset",
            True,
            False,
            False,
            f"{_LABELS[permission_id]} was reset - the system prompt can "
            "appear again on the next request.",
            self.snapshot(),
        )

    def open_settings(
        self, permission_id: PermissionId, *, dry_run: bool = False
    ) -> PermissionOperation:
        """Open the matching System Settings pane via LaunchServices."""
        before = self.snapshot()
        if permission_id not in _SETTINGS_URLS:
            return PermissionOperation(
                False,
                permission_id.value,
                "open_settings",
                False,
                dry_run,
                False,
                f"{_LABELS[permission_id]} has no System Settings pane; "
                "use the request flow instead.",
                before,
            )
        if dry_run:
            return PermissionOperation(
                True,
                permission_id.value,
                "open_settings",
                False,
                True,
                False,
                f"Would open {_LABELS[permission_id]} in System Settings.",
                before,
            )
        error = self._eligibility_error(before)
        if error is not None:
            return PermissionOperation(
                False,
                permission_id.value,
                "open_settings",
                False,
                False,
                False,
                error,
                before,
            )
        try:
            opened = self._open_settings(permission_id)
        except Exception:  # noqa: BLE001 - native settings boundary
            opened = False
        restart = opened and permission_id in _RESTART_AFTER_CHANGE
        if restart:
            self._restart_required.add(permission_id)
        after = self.snapshot()
        return PermissionOperation(
            opened,
            permission_id.value,
            "open_settings",
            opened,
            False,
            restart,
            (
                "System Settings opened. Restart Personal Jarvis after changing access."
                if restart
                else (
                    "System Settings opened." if opened else "System Settings could not be opened."
                )
            ),
            after,
        )


_DEFAULT_SYSTEM_PERMISSION_PORT = SystemPermissionPort()


def get_system_permission_port() -> SystemPermissionPort:
    """Return the process-wide port that retains only pending-restart state."""
    return _DEFAULT_SYSTEM_PERMISSION_PORT


__all__ = [
    "APP_NAME",
    "EXPECTED_BUNDLE_ID",
    "FEATURE_REQUIREMENTS",
    "AppIdentity",
    "PermissionId",
    "PermissionOperation",
    "PermissionState",
    "PermissionStatus",
    "SystemPermissionPort",
    "get_system_permission_port",
]
