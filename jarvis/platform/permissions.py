"""Uncached macOS system-permission probes and Apple's supported ask/settings calls.

macOS TCC permissions cannot be installed or granted programmatically.  This
port reports the native state on every call and exposes only the primitives the
just-in-time ``permission_service`` composes: ``state`` (live, never asks),
``request_native`` (the one native request, never evidence of a grant),
``open_pane`` and ``reset_row``.  It decides nothing about whether a feature may
run.  Platform frameworks are imported lazily so a base or headless installation
remains importable on every operating system.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from jarvis.core.branding import (
    MACOS_APP_NAME as APP_NAME,
)
from jarvis.core.branding import (
    MACOS_BUNDLE_ID as EXPECTED_BUNDLE_ID,
)
from jarvis.core.branding import (
    MACOS_DMG_BUNDLE_ID,
)

from . import PlatformName, detect_platform

log = logging.getLogger(__name__)

# Both ways the app reaches a Mac are "the installed app": the managed local
# bundle and the downloadable .dmg build. They carry different bundle ids and so
# keep different privacy grants, but each one is the app the user launched —
# never Terminal or a bare Python. Accepting only the managed id made every
# permission feature of the .dmg app fail closed even with all grants given, and
# hid every Allow / Open Settings button, so the user had no way to fix it.
ACCEPTED_BUNDLE_IDS: tuple[str, ...] = (EXPECTED_BUNDLE_ID, MACOS_DMG_BUNDLE_ID)

_SYSTEM_SETTINGS_BUNDLE_ID = "com.apple.systempreferences"

# State files an earlier build wrote next to the user's data: the "macOS sees
# this app as new" note and the recorded Automation answers. Nothing reads
# either one any more; the names live here only so an upgrade can delete the
# leftovers once.
_LEFTOVER_STATE_FILES: tuple[str, ...] = (
    "macos-tcc-reset.json",
    "macos-automation-consent.json",
)


def _leftover_state_dir() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir()


def remove_leftover_state_files() -> None:
    """Delete the two state files an earlier build left behind; never raises.

    Best-effort and silent for the user: nothing reads these files, so a failure
    to remove one costs a few bytes. It never asks for anything and never touches
    a permission.
    """
    try:
        directory = _leftover_state_dir()
    except Exception:  # noqa: BLE001 - housekeeping must never break an install
        log.debug("Could not locate the data directory for the leftover cleanup.", exc_info=True)
        return
    for name in _LEFTOVER_STATE_FILES:
        try:
            (directory / name).unlink(missing_ok=True)
        except OSError:
            log.debug("Could not remove the leftover %s.", name, exc_info=True)


class PermissionId(StrEnum):
    """Stable identifiers shared by the API, the CLI and the desktop permission toast."""

    MICROPHONE = "microphone"
    SCREEN_RECORDING = "screen_recording"
    ACCESSIBILITY = "accessibility"
    INPUT_MONITORING = "input_monitoring"
    EVENT_POSTING = "event_posting"
    # Apple Events consent for the media players the ducking scripts talk to
    # (Music, Spotify). Invisible before this row existed: the dialog fired
    # mid-dictation, a rebuild orphaned the answer, and nothing could reset it.
    AUTOMATION = "automation"
    # Not a TCC grant: the macOS Keychain prompts per item at first access
    # (typically right at app start, when API keys are read). Users who deny
    # it silently land on the file fallback and read the prompt as suspicious
    # unless the UI names and explains it like every other permission.
    CREDENTIAL_STORE = "credential_store"


# ``(display name, bundle id)`` of every app Jarvis scripts through Apple
# Events. The ducking backend reads this list too, so the permission row and
# the scripts can never disagree about which apps need consent.
AUTOMATION_TARGETS: tuple[tuple[str, str], ...] = (
    ("Music", "com.apple.Music"),
    ("Spotify", "com.spotify.client"),
)


class PermissionState(StrEnum):
    """Cross-platform permission states; never infer denial from uncertainty."""

    GRANTED = "granted"
    NOT_DETERMINED = "not_determined"
    DENIED = "denied"
    RESTRICTED = "restricted"
    NOT_GRANTED = "not_granted"
    UNAVAILABLE = "unavailable"
    NOT_REQUIRED = "not_required"


_LABELS: dict[PermissionId, str] = {
    PermissionId.MICROPHONE: "Microphone",
    PermissionId.SCREEN_RECORDING: "Screen Recording",
    PermissionId.ACCESSIBILITY: "Accessibility",
    PermissionId.INPUT_MONITORING: "Input Monitoring",
    PermissionId.EVENT_POSTING: "Input Control",
    PermissionId.AUTOMATION: "Automation (Music & Spotify)",
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
    PermissionId.AUTOMATION: (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
    ),
}


class RequestClass(StrEnum):
    """How macOS asks for a permission (the "Class" column of the JIT policy table).

    The just-in-time service reads this to decide what a native request can
    achieve: whether to wait for an answer, or to send the user to Settings.
    """

    # An OS dialog with an answer button: the user can allow or deny in place.
    DIALOG = "dialog"
    # A request shows at most one dialog that, as far as we know, only offers
    # "Open System Settings": the user still has to flip a switch there. That
    # shape is community-observed, not documented by Apple (UNVERIFIED), and the
    # policy treats it as the cautious reading.
    PROMPT_ONCE = "prompt_once"
    # The OS prompts by itself on first access; there is no request call to make.
    # No :class:`PermissionId` has this class today (Files & Folders is not a
    # port permission); it exists so the table speaks Apple's whole vocabulary.
    NATIVE = "native"
    # Nothing to ask natively (not a TCC grant).
    NONE = "none"


REQUEST_CLASS: dict[PermissionId, RequestClass] = {
    PermissionId.MICROPHONE: RequestClass.DIALOG,
    PermissionId.SCREEN_RECORDING: RequestClass.PROMPT_ONCE,
    PermissionId.ACCESSIBILITY: RequestClass.PROMPT_ONCE,
    PermissionId.INPUT_MONITORING: RequestClass.PROMPT_ONCE,
    PermissionId.EVENT_POSTING: RequestClass.PROMPT_ONCE,
    PermissionId.AUTOMATION: RequestClass.DIALOG,
    # The Keychain is not TCC: its prompt belongs to the credential store and
    # "Try again" replays a read, which is not a request in this sense.
    PermissionId.CREDENTIAL_STORE: RequestClass.NONE,
}

# Permissions that share one Settings pane (and one request) share a family.
# EVENT_POSTING is an ALIAS of ACCESSIBILITY for asking: one request, one pane,
# one episode. The value is the canonical member of the family, which is also
# what a UI shows and an event carries instead of the alias.
PANE_FAMILY: dict[PermissionId, PermissionId] = {
    PermissionId.MICROPHONE: PermissionId.MICROPHONE,
    PermissionId.SCREEN_RECORDING: PermissionId.SCREEN_RECORDING,
    PermissionId.ACCESSIBILITY: PermissionId.ACCESSIBILITY,
    PermissionId.INPUT_MONITORING: PermissionId.INPUT_MONITORING,
    PermissionId.EVENT_POSTING: PermissionId.ACCESSIBILITY,
    PermissionId.AUTOMATION: PermissionId.AUTOMATION,
    PermissionId.CREDENTIAL_STORE: PermissionId.CREDENTIAL_STORE,
}

# Where a person finds the switch, in plain English (the snapshot row and the docs). The
# pane names are static text on purpose: no OS sniffing. Apple does not document
# the label per macOS release (UNVERIFIED): the Screen Recording pane is called
# "Screen & System Audio Recording" from macOS 15 and was "Screen Recording"
# before, and the Accessibility pane may be renamed again by a later release.
# The Keychain has no pane, so it has no entry (like ``_SETTINGS_URLS``).
_SETTINGS_PATH_PREFIX = "System Settings > Privacy & Security > "
SETTINGS_PATH_TEXT: dict[PermissionId, str] = {
    PermissionId.MICROPHONE: f"{_SETTINGS_PATH_PREFIX}Microphone",
    PermissionId.SCREEN_RECORDING: f"{_SETTINGS_PATH_PREFIX}Screen & System Audio Recording",
    PermissionId.ACCESSIBILITY: f"{_SETTINGS_PATH_PREFIX}Accessibility",
    PermissionId.INPUT_MONITORING: f"{_SETTINGS_PATH_PREFIX}Input Monitoring",
    PermissionId.EVENT_POSTING: f"{_SETTINGS_PATH_PREFIX}Accessibility",
    PermissionId.AUTOMATION: f"{_SETTINGS_PATH_PREFIX}Automation",
}


def settings_path_text(permission_id: PermissionId | str) -> str | None:
    """The plain-English System Settings path for a permission, ``None`` without a pane."""
    return SETTINGS_PATH_TEXT.get(PermissionId(permission_id))


# What :meth:`SystemPermissionPort.request_native` reports. Never evidence of a
# grant: only ``state()`` says whether access exists.
#   dialog_shown - a system dialog may be open right now, awaiting the user's
#                  answer (the call returned before the answer): poll ``state()``.
#   no_dialog    - nothing is open: access was already granted or decided, the
#                  Automation target was not running, or the request was
#                  synchronous and has finished: read ``state()`` for the answer.
#   timed_out    - Automation only: the consent runner was killed after waiting
#                  for an answer nobody gave. Not "the player is not running":
#                  the user may simply not have looked yet, so the caller may ask
#                  again later.
#   unavailable  - the request could not be made (not macOS, a missing framework
#                  or symbol, an unusable target, or a native error).
NativeRequestOutcome = Literal["dialog_shown", "no_dialog", "timed_out", "unavailable"]

# Apple Event Manager constants for AEDeterminePermissionToAutomateTarget
# (macOS 10.14+). Four-char codes are big-endian uint32; the OSStatus values
# are stable ABI (AE.framework / MacErrors.h).
_AE_TYPE_APPLICATION_BUNDLE_ID = 0x62756E64  # 'bund'
_AE_TYPE_WILDCARD = 0x2A2A2A2A  # '****'
_AE_NO_ERR = 0
_AE_PROC_NOT_FOUND = -600  # target not running: no answer possible
_AE_EVENT_NOT_PERMITTED = -1743  # the user denied
_AE_EVENT_WOULD_REQUIRE_USER_CONSENT = -1744  # not asked yet
_AUTOMATION_STATES: dict[int, PermissionState] = {
    _AE_NO_ERR: PermissionState.GRANTED,
    _AE_EVENT_NOT_PERMITTED: PermissionState.DENIED,
    _AE_EVENT_WOULD_REQUIRE_USER_CONSENT: PermissionState.NOT_DETERMINED,
}


def _default_automation_probe(bundle_id: str, _ask: bool) -> int | None:
    """``AEDeterminePermissionToAutomateTarget`` for one bundle id.

    Returns the raw OSStatus, or ``None`` when the framework cannot be
    called. This is a silent read only: ``_ask`` is accepted for the frozen
    two-argument seam (tests and ``FakeTCC.automation_probe`` use it) but is
    NEVER forwarded, so passing ``True`` still reads silently, because an
    in-process asking call blocks the caller until a dialog is answered and
    cannot be killed. The consent dialog is raised solely through
    ``request_native`` (a killable child runner).
    """
    if sys.platform != "darwin":
        return None
    import ctypes

    class _AEDesc(ctypes.Structure):
        _fields_ = [("descriptorType", ctypes.c_uint32), ("dataHandle", ctypes.c_void_p)]

    try:
        services = ctypes.CDLL(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        services.AECreateDesc.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_long,
            ctypes.POINTER(_AEDesc),
        ]
        services.AECreateDesc.restype = ctypes.c_int32
        services.AEDisposeDesc.argtypes = [ctypes.POINTER(_AEDesc)]
        services.AEDisposeDesc.restype = ctypes.c_int32
        determine = services.AEDeterminePermissionToAutomateTarget
        determine.argtypes = [
            ctypes.POINTER(_AEDesc),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_ubyte,
        ]
        determine.restype = ctypes.c_int32
    except (OSError, AttributeError) as exc:
        log.debug("Apple Event Manager is unavailable for the Automation probe: %s", exc)
        return None
    target = _AEDesc()
    data = bundle_id.encode("utf-8")
    if services.AECreateDesc(_AE_TYPE_APPLICATION_BUNDLE_ID, data, len(data), ctypes.byref(target)):
        return None
    try:
        return int(determine(ctypes.byref(target), _AE_TYPE_WILDCARD, _AE_TYPE_WILDCARD, 0))
    finally:
        services.AEDisposeDesc(ctypes.byref(target))


# How long the Automation consent dialog may stay unanswered before its runner
# is killed. A person answers a dialog; a short timeout tore it down before
# they could click and made every attempt ask again.
_AUTOMATION_CONSENT_TIMEOUT_S = 120.0


def _is_automation_target(bundle_id: object) -> bool:
    """Whether ``bundle_id`` names a player in :data:`AUTOMATION_TARGETS`."""
    return any(bundle_id == known for _name, known in AUTOMATION_TARGETS)


def _automation_consent_script(bundle_id: str) -> str:
    """One benign Apple Event to a player, guarded so it is never launched.

    A bare ``tell application`` launches the app, so the script asks whether it
    is running INSIDE the same script and does nothing when it is not. Only
    ``bundle_id`` values from :data:`AUTOMATION_TARGETS` reach this function, so
    nothing outside the fixed table is ever interpolated.
    """
    return (
        f'if application id "{bundle_id}" is running then\n'
        f'    tell application id "{bundle_id}" to get player state\n'
        '    return "+"\n'
        "else\n"
        '    return "-"\n'
        "end if"
    )


def _default_automation_consent_runner(script: str) -> Any:
    """Run one AppleScript in a killable child so a dialog can be answered.

    The consent dialog belongs to the app that asks. A child ``osascript`` is
    attributed to its parent app (Apple DTS: algorithm undocumented, so
    UNVERIFIED), and the child, unlike an in-process Apple Event call, can be
    killed: ``subprocess.run`` kills it when the timeout passes. Returns the
    ``CompletedProcess``; ``subprocess.TimeoutExpired`` and ``OSError`` propagate
    to the caller, which owns the never-raise boundary.
    """
    import subprocess  # lazy: only the darwin request path reaches this

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    return subprocess.run(  # noqa: S603, S607 - fixed argv, no shell
        ["osascript", "-e", script],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_AUTOMATION_CONSENT_TIMEOUT_S,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


# IOKit HID access constants (IOHIDCheckAccess, macOS 10.15+). The SDK header
# (IOKit/hidsystem/IOHIDLib.h) declares both as PLAIN C enums — 32-bit int,
# not CF_ENUM(uint64_t); the raw values are stable ABI.
_IOHID_REQUEST_POST_EVENT = 0  # kIOHIDRequestTypePostEvent
_IOHID_REQUEST_LISTEN_EVENT = 1  # kIOHIDRequestTypeListenEvent
_IOHID_ACCESS_STATES: dict[int, PermissionState] = {
    0: PermissionState.GRANTED,  # kIOHIDAccessTypeGranted
    1: PermissionState.DENIED,  # kIOHIDAccessTypeDenied
    2: PermissionState.NOT_DETERMINED,  # kIOHIDAccessTypeUnknown
}


def _canonical_app_roots() -> tuple[Path, ...]:
    """The directories a legitimately installed copy of this app may live in.

    Both are equally canonical. The installer writes ``~/Applications``, but
    ``/Applications`` is where Mac users put apps, and dragging it there is a
    normal move — not a tampering signal. TCC pins a grant to the bundle id
    and its signature, never to a path, so a stricter rule buys no safety and
    costs everything: an app read as "unstable" hides every request button,
    reports every feature as not ready, and keeps insisting the permissions
    are missing while System Settings shows them enabled (BUG-161).
    """
    return (Path.home() / "Applications", Path("/Applications"))


def _default_screen_capture_live_check() -> bool | None:
    """Prove the Screen Recording grant by USING it; ``None`` when unknowable.

    ``CGPreflightScreenCaptureAccess`` answers from a value macOS freezes when
    the process first asks. A grant given in System Settings while the app runs
    therefore stays invisible until relaunch, and the app keeps demanding a
    permission the user has already given — however often they give it again
    (BUG-161). Window TITLES of other applications are screen-recording-gated
    data, so one on-screen window owned by another process and carrying a name
    is live proof the grant works right now. The reverse does not hold (there
    may simply be no other window on screen), so an empty result is ``None``
    and never contradicts the preflight.
    """
    try:
        import Quartz  # type: ignore[import-not-found]

        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
    except Exception:  # noqa: BLE001 - a missing native bridge proves nothing
        return None
    return _window_titles_are_visible(windows, os.getpid())


def _window_titles_are_visible(windows: Any, own_pid: int) -> bool | None:
    """``True`` when another app's window title is readable, else ``None``."""
    try:
        entries = list(windows or ())
    except TypeError:
        return None
    for window in entries:
        try:
            # Layer 0 is the ordinary application layer. The Dock, the menu
            # bar and other system chrome sit above it and expose names
            # without the grant, so they would fake a positive result.
            if int(window.get("kCGWindowLayer", -1)) != 0:
                continue
            if int(window.get("kCGWindowOwnerPID", own_pid)) == own_pid:
                continue
            if str(window.get("kCGWindowName") or "").strip():
                return True
        except (AttributeError, TypeError, ValueError):
            continue
    return None


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
        # 32-bit on purpose: IOHIDAccessType is a plain C enum. A c_uint64
        # restype reads all of x0 on arm64, where the ABI does NOT promise
        # zeroed high bits for a 32-bit return — garbage there would push the
        # value out of _IOHID_ACCESS_STATES and silently demote the tri-state
        # probe to the boolean preflight it exists to replace.
        check.restype = ctypes.c_uint32
        check.argtypes = [ctypes.c_uint32]
        return int(check(request_type))
    except Exception:  # noqa: BLE001 - a missing native bridge falls back
        return None


def _default_iohid_request(request_type: int) -> bool | None:
    """``IOHIDRequestAccess`` for one request type; ``None`` when unavailable.

    The way to ask for Input Monitoring without creating an event tap (BUG-058
    class). Only the fallback for a Quartz binding that lacks
    ``CGRequestListenEventAccess``. Never reached by a state read, and the
    ctypes binding itself is UNVERIFIED on a real Mac.
    """
    try:
        import ctypes

        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        request = iokit.IOHIDRequestAccess
        request.restype = ctypes.c_bool
        request.argtypes = [ctypes.c_uint32]
        return bool(request(request_type))
    except Exception:  # noqa: BLE001 - a missing native bridge means "cannot ask"
        log.debug("IOHIDRequestAccess is unavailable.", exc_info=True)
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
    PermissionId.AUTOMATION: "AppleEvents",
}

# The Info.plist key whose absence makes macOS terminate the process when the
# permission's API is called (BUG-058 class); ``jarvis.core.macos_privacy_strings``
# holds the strings themselves and a parity test keeps the key names equal.
# A permission with no entry has no usage-description key. Screen Recording is
# deliberately absent: Apple documents none (the string the bundles carry for it
# is for parity, its effect is UNVERIFIED), so a missing one must not refuse the ask.
_USAGE_DESCRIPTION_KEYS: dict[PermissionId, str] = {
    PermissionId.MICROPHONE: "NSMicrophoneUsageDescription",
    PermissionId.AUTOMATION: "NSAppleEventsUsageDescription",
}


@dataclass(frozen=True)
class AppIdentity:
    """Who this process is to macOS (static for the life of the process)."""

    app_name: str
    bundle_id: str | None
    bundle_path: str | None
    launched_as_bundle: bool
    stable: bool


@dataclass(frozen=True)
class PermissionOperation:
    """The answer of :meth:`SystemPermissionPort.reset_row`."""

    ok: bool
    permission_id: str
    action: str
    performed: bool
    dry_run: bool
    message: str


class SystemPermissionPort:
    """Read and request OS permissions without caching native state."""

    def __init__(
        self,
        *,
        platform_name: PlatformName | None = None,
        module_loader: Callable[[str], Any] = importlib.import_module,
        iohid_check: Callable[[int], int | None] = _default_iohid_check,
        screen_capture_live_check: Callable[[], bool | None] = _default_screen_capture_live_check,
        credential_store_backend: Callable[[], str] = _default_credential_store_backend,
        credential_store_recover: Callable[[], bool] = _default_credential_store_recover,
        automation_probe: Callable[[str, bool], int | None] = _default_automation_probe,
        iohid_request: Callable[[int], bool | None] = _default_iohid_request,
        automation_consent_runner: Callable[[str], Any] = _default_automation_consent_runner,
    ) -> None:
        self._platform_name = platform_name
        self._module_loader = module_loader
        self._iohid_check = iohid_check
        self._screen_capture_live_check = screen_capture_live_check
        self._credential_store_backend = credential_store_backend
        self._credential_store_recover = credential_store_recover
        self._automation_probe = automation_probe
        # Seams of :meth:`request_native` only; no state read ever reaches them.
        self._iohid_request = iohid_request
        self._automation_consent_runner = automation_consent_runner
        # Static per process: the main bundle and its on-disk path cannot
        # change while this process runs. Caching it is NOT a cached
        # permission probe — every TCC state read in _state() stays live.
        self._bundle_identity_cache: tuple[str | None, str | None, bool, bool] | None = None
        # The pane URL THIS process last opened in System Settings (operation
        # state, not a probe): a running Settings is closed before an open only
        # when it may show another pane than the one asked for.
        self._last_opened_url: str | None = None

    @property
    def platform(self) -> PlatformName:
        return self._platform_name or detect_platform()

    def _load(self, module: str) -> Any | None:
        try:
            return self._module_loader(module)
        except Exception:  # noqa: BLE001 - a broken native bridge fails closed
            log.debug("Native permission framework %s is unavailable.", module, exc_info=True)
            return None

    def _bundle_identity(self) -> tuple[str | None, str | None, bool, bool]:
        """``(bundle_id, bundle_path, launched_as_bundle, stable)``, cached.

        Computed once per process: NSBundle metadata and the two
        ``Path.resolve`` calls cannot change for a running process, and the
        hot Computer-Use path probes permissions before every grab and every
        input action — recomputing this each time cost two filesystem
        resolutions plus ObjC bridge round trips per probe. The live TCC
        grant states are deliberately NOT cached (see :meth:`_state`).
        """
        if self._bundle_identity_cache is not None:
            return self._bundle_identity_cache
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
                resolved = Path(bundle_path).resolve()
                canonical_path = any(
                    resolved == (root / f"{APP_NAME}.app").resolve()
                    for root in _canonical_app_roots()
                )
            except (OSError, ValueError):
                # A path that will not resolve is not the canonical bundle
                # location. False is the cautious reading and it feeds a
                # stability flag the caller already reports on.
                canonical_path = False
        stable = bundle_id in ACCEPTED_BUNDLE_IDS and launched_as_bundle and canonical_path
        self._bundle_identity_cache = (bundle_id, bundle_path, launched_as_bundle, stable)
        return self._bundle_identity_cache

    def _stable_identity(self) -> bool:
        """Whether this process is the installed app (cached; darwin only)."""
        return self._bundle_identity()[3]

    @property
    def outside_installed_app(self) -> bool:
        """Whether this process is NOT the installed app bundle.

        ``True`` for a development run (Terminal, bare Python), for an app that
        does not carry one of :data:`ACCEPTED_BUNDLE_IDS`, for one that runs
        from outside the canonical application folders (a mounted ``.dmg``, the
        Downloads folder), and everywhere off macOS, where there is no app
        bundle at all. It is the same verdict ``app_identity.stable`` reports,
        inverted, and it gates only whether we may start a native request on
        our own: the grant of whatever app is responsible is still acted on
        (design P6). Static for the life of the process.
        """
        if self.platform != "darwin":
            return True
        return not self._stable_identity()

    @property
    def launched_as_bundle(self) -> bool:
        """Whether the main bundle is a real ``.app`` (cached; ``False`` off macOS).

        Together with :attr:`outside_installed_app` it tells a copy run from a
        mounted disk image or the Downloads folder (a bundle, wrongly placed:
        the grantee is Personal Jarvis itself) from a terminal or IDE run (no
        bundle: the grantee is the app that started it).
        """
        if self.platform != "darwin":
            return False
        return bool(self._bundle_identity()[2])

    def has_desktop_session(self) -> bool:
        """Whether a window server answers, so a dialog or a pane can be shown at all.

        One AppKit round trip: a status route or an ask path calls it, a hot path
        does not. Fails closed (``False``) when AppKit cannot be read, like the
        ``headless`` flag of the status snapshot. Off macOS it is the display probe.
        """
        if self.platform != "darwin":
            from .probes import display_present

            return bool(display_present())
        appkit = self._load("AppKit")
        if appkit is None:
            return False
        try:
            return appkit.NSWorkspace.sharedWorkspace().frontmostApplication() is not None
        except Exception:  # noqa: BLE001 - fail closed for prompt safety
            log.debug("Could not read the macOS desktop session.", exc_info=True)
            return False

    def usage_string_present(self, permission_id: PermissionId | str) -> bool:
        """Whether the main bundle carries the usage string the permission needs.

        Calling the microphone or Apple Events API without its ``Info.plist``
        string makes macOS terminate the process (BUG-058 class), so a caller
        must refuse the native request when this is ``False``. ``True`` also
        means "not applicable": the permission needs no key (Screen Recording,
        Accessibility, Input Monitoring and the Keychain have none), this is not
        macOS, or the process has no bundle id because the responsible code is a
        terminal and not this app. An unreadable bundle fails closed (``False``).
        """
        permission = PermissionId(permission_id)
        key = _USAGE_DESCRIPTION_KEYS.get(permission)
        if key is None or self.platform != "darwin":
            return True
        if self._bundle_identity()[0] is None:
            return True
        foundation = self._load("Foundation")
        try:
            value = foundation.NSBundle.mainBundle().objectForInfoDictionaryKey_(key)
        except Exception:  # noqa: BLE001 - an unreadable bundle must not look present
            log.debug("Could not read %s from the main bundle.", key, exc_info=True)
            return False
        present = value is not None and bool(str(value).strip())
        if not present:
            log.debug("The main bundle has no %s; %s must not be requested.", key, permission.value)
        return present

    def _app_identity(self) -> tuple[AppIdentity, bool]:
        """``(identity, headless)``: who this process is, and whether a window server answers."""
        if self.platform != "darwin":
            from .probes import display_present

            return (
                AppIdentity(
                    app_name=APP_NAME,
                    bundle_id=None,
                    bundle_path=None,
                    launched_as_bundle=False,
                    stable=False,
                ),
                not display_present(),
            )

        bundle_id, bundle_path, launched_as_bundle, stable = self._bundle_identity()
        headless = True
        appkit = self._load("AppKit")
        if appkit is not None:
            try:
                headless = appkit.NSWorkspace.sharedWorkspace().frontmostApplication() is None
            except Exception:  # noqa: BLE001 - fail closed for prompt safety
                log.debug("Could not read the macOS desktop session.", exc_info=True)
                headless = True

        return (
            AppIdentity(
                app_name=APP_NAME,
                bundle_id=bundle_id,
                bundle_path=bundle_path,
                launched_as_bundle=launched_as_bundle,
                stable=stable,
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

    def _screen_capture_live(self) -> bool:
        """Whether a live probe proves the frozen Screen Recording preflight wrong."""
        try:
            return self._screen_capture_live_check() is True
        except Exception:  # noqa: BLE001 - native probes never crash callers
            return False

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

    # ---- Automation (Apple Events) ---------------------------------------

    def _installed_automation_targets(self) -> list[str] | None:
        """Bundle ids of the scriptable players present; ``None`` = no AppKit."""
        appkit = self._load("AppKit")
        if appkit is None:
            return None
        try:
            workspace = appkit.NSWorkspace.sharedWorkspace()
        except Exception:  # noqa: BLE001 - a broken native bridge fails closed
            return None
        locate = getattr(workspace, "URLForApplicationWithBundleIdentifier_", None)
        if not callable(locate):
            # No lookup API: nothing can be scripted, so nothing to consent to.
            return []
        installed: list[str] = []
        for _name, bundle_id in AUTOMATION_TARGETS:
            try:
                if locate(bundle_id) is not None:
                    installed.append(bundle_id)
            except Exception:  # noqa: BLE001 - one bad lookup skips one player
                log.debug("Could not locate %s.", bundle_id, exc_info=True)
        return installed

    def _running_automation_targets(self, appkit: Any, bundle_id: str) -> list[Any]:
        lookup = getattr(
            getattr(appkit, "NSRunningApplication", None),
            "runningApplicationsWithBundleIdentifier_",
            None,
        )
        if not callable(lookup):
            return []
        try:
            return list(lookup(bundle_id) or [])
        except Exception:  # noqa: BLE001 - treat as not running
            return []

    def _live_automation_state(self, bundle_id: str) -> PermissionState | None:
        """The OS answer for a RUNNING target; ``None`` when there is none.

        Always the non-asking form of the probe (``ask`` is False): a state read
        never raises the dialog. The two-argument probe signature is the frozen
        seam of the fakes; the consent dialog itself is only ever raised through
        :meth:`request_native`.
        """
        try:
            status = self._automation_probe(bundle_id, False)
        except Exception:  # noqa: BLE001 - native probes never crash callers
            log.debug("Automation probe for %s failed.", bundle_id, exc_info=True)
            return None
        if status is None:
            return None
        return _AUTOMATION_STATES.get(int(status))

    def _player_state(self, bundle_id: str) -> PermissionState:
        """The live Automation answer for one INSTALLED player; never asks, never writes.

        Apple Events answer only for a RUNNING target, so a player that is not
        running reads ``NOT_DETERMINED``: unknown, not "never asked" (the answer
        may well be on file). Nothing is recorded to stand in for it, and the
        probe can hang for a running player without a window (Apple forums
        thread 666528), so a caller that must not block runs this off its loop.
        """
        appkit = self._load("AppKit")
        if appkit is not None and self._running_automation_targets(appkit, bundle_id):
            live = self._live_automation_state(bundle_id)
            if live is not None:
                return live
        return PermissionState.NOT_DETERMINED

    def _automation_state(self) -> PermissionState:
        """One aggregate for every scriptable player, read live; the strictest answer wins.

        No player installed means nothing to consent to. A player that is not
        running is unknown (``NOT_DETERMINED``). The just-in-time service reads
        one player at a time through ``target``; this aggregate is for a caller
        that has no target.
        """
        installed = self._installed_automation_targets()
        if installed is None:
            return PermissionState.UNAVAILABLE
        if not installed:
            return PermissionState.NOT_REQUIRED
        states = [self._player_state(bundle_id) for bundle_id in installed]
        if PermissionState.DENIED in states:
            return PermissionState.DENIED
        if PermissionState.NOT_DETERMINED in states:
            return PermissionState.NOT_DETERMINED
        return PermissionState.GRANTED

    def _automation_target_state(self, bundle_id: str) -> PermissionState:
        """The Automation state for ONE player from :data:`AUTOMATION_TARGETS`."""
        if not _is_automation_target(bundle_id):
            log.debug(
                "Automation state requested for %r, which is not a scriptable player.", bundle_id
            )
            return PermissionState.UNAVAILABLE
        installed = self._installed_automation_targets()
        if installed is None:
            return PermissionState.UNAVAILABLE
        if bundle_id not in installed:
            # Nothing to script, so nothing to consent to.
            return PermissionState.NOT_REQUIRED
        return self._player_state(bundle_id)

    def _state(
        self,
        permission_id: PermissionId,
        *,
        deep: bool,
        target: str | None = None,
    ) -> PermissionState:
        if self.platform != "darwin":
            return PermissionState.NOT_REQUIRED
        if permission_id is PermissionId.CREDENTIAL_STORE:
            return self._credential_store_state()
        if permission_id is PermissionId.AUTOMATION:
            if target is not None:
                return self._automation_target_state(target)
            return self._automation_state()
        if permission_id is PermissionId.MICROPHONE:
            return self._microphone_state()
        if permission_id is PermissionId.SCREEN_RECORDING:
            preflight = self._boolean_state("Quartz", "CGPreflightScreenCaptureAccess")
            if preflight is PermissionState.GRANTED or not deep:
                return preflight
            # The preflight is frozen per process and can only go stale
            # NEGATIVE. Ask the window server whether the grant works right
            # now before telling the user a permission they just gave is
            # missing (BUG-161). Only a deep read does this: it enumerates the
            # on-screen windows, which a hot path must never pay for.
            return PermissionState.GRANTED if self._screen_capture_live() else preflight
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

    def state(
        self,
        permission_id: PermissionId | str,
        *,
        target: str | None = None,
        deep: bool = False,
    ) -> PermissionState:
        """Probe one permission live, without asking.

        Every read goes to the OS and nothing is cached. This never prompts:
        no native request and no ``ask`` path is reachable from here.

        ``target`` is only meaningful for Automation: the bundle id of one
        player from :data:`AUTOMATION_TARGETS`. A target that is not one of
        them reads ``UNAVAILABLE`` (logged at debug, never raised); for every
        other permission it is ignored. Without a target, Automation reads one
        aggregate over every installed player, live (nothing is recorded to
        stand in for a player that is not running).

        ``deep`` only matters for Screen Recording. ``False`` (the default) is the
        preflight alone and NEVER enumerates windows. ``True`` also runs the
        window-title oracle that sees a grant the frozen preflight still denies
        (BUG-161); it enumerates the on-screen windows, so a hot path never asks
        for it.
        """
        return self._state(PermissionId(permission_id), deep=deep, target=target)

    def request_native(
        self,
        permission_id: PermissionId | str,
        *,
        target: str | None = None,
    ) -> NativeRequestOutcome:
        """Perform ONLY the native request for one permission; never raises.

        It makes no identity check of its own: whether we may
        ask at all is the caller's decision (design P6), and the caller must also
        have confirmed :meth:`usage_string_present` first. The return value
        says what the OS may now be doing (see :data:`NativeRequestOutcome`) and
        is NEVER evidence of a grant: only :meth:`state` is. Nothing is
        recorded here either.

        EVENT_POSTING is an alias of ACCESSIBILITY for asking, so both make the
        one Accessibility request. Automation needs ``target`` (a bundle id from
        :data:`AUTOMATION_TARGETS`); it runs through a killable child, never
        launches the player, and is synchronous: it returns once the dialog was
        answered or the child was killed after the timeout (``"timed_out"``; that
        macOS then tears the dialog down is UNVERIFIED), so it can block for
        minutes and belongs on a worker thread. A failure is logged at debug
        and reported as ``"unavailable"``.
        """
        try:
            permission = PermissionId(permission_id)
        except (ValueError, TypeError):
            log.debug("Native request for the unknown permission %r ignored.", permission_id)
            return "unavailable"
        if self.platform != "darwin":
            return "unavailable"
        if permission is PermissionId.EVENT_POSTING:
            permission = PermissionId.ACCESSIBILITY
        try:
            return self._issue_native_request(permission, target)
        except Exception:  # noqa: BLE001 - the native request boundary never raises
            log.debug("The native %s request failed.", permission.value, exc_info=True)
            return "unavailable"

    def _issue_native_request(
        self, permission_id: PermissionId, target: str | None
    ) -> NativeRequestOutcome:
        """The per-permission native call behind :meth:`request_native` (may raise)."""
        if permission_id is PermissionId.CREDENTIAL_STORE:
            # Replaying the failed Keychain read is the only supported way to
            # make macOS show its prompt again. Synchronous: it has been
            # answered when this returns.
            self._credential_store_recover()
            return "no_dialog"
        if permission_id is PermissionId.AUTOMATION:
            return self._request_automation_target(target)
        if permission_id is PermissionId.MICROPHONE:
            av = self._load("AVFoundation")
            owner = getattr(av, "AVCaptureDevice", None)
            request = getattr(owner, "requestAccessForMediaType_completionHandler_", None)
            if not callable(request):
                return "unavailable"
            # The call returns nothing: the status read beforehand is the only
            # way to tell a dialog (not determined) from a silent no-op.
            before = self._microphone_state()
            request(av.AVMediaTypeAudio, lambda _granted: None)
            return "dialog_shown" if before is PermissionState.NOT_DETERMINED else "no_dialog"
        if permission_id is PermissionId.ACCESSIBILITY:
            app_services = self._load("ApplicationServices")
            trusted_with_options = getattr(app_services, "AXIsProcessTrustedWithOptions", None)
            prompt_key = getattr(app_services, "kAXTrustedCheckOptionPrompt", None)
            if not callable(trusted_with_options) or prompt_key is None:
                return "unavailable"
            # Prompting is asynchronous and does not change the return value. A
            # False answer covers both "the dialog is up now" and "macOS
            # suppressed it", so it is reported as a dialog that MAY be open.
            trusted = bool(trusted_with_options({prompt_key: True}))
            return "no_dialog" if trusted else "dialog_shown"
        if permission_id is PermissionId.SCREEN_RECORDING:
            quartz = self._load("Quartz")
            request = getattr(quartz, "CGRequestScreenCaptureAccess", None)
            if not callable(request):
                return "unavailable"
            return "no_dialog" if bool(request()) else "dialog_shown"
        if permission_id is PermissionId.INPUT_MONITORING:
            # Asked through the request calls only, never by creating an event
            # tap (BUG-058 class). The tri-state read beforehand tells a dialog
            # (not determined) from a decision already on file.
            before = self._iohid_state(_IOHID_REQUEST_LISTEN_EVENT)
            quartz = self._load("Quartz")
            request = getattr(quartz, "CGRequestListenEventAccess", None)
            if callable(request):
                answer: bool | None = bool(request())
            else:
                answer = self._iohid_request(_IOHID_REQUEST_LISTEN_EVENT)
            if answer is None:
                return "unavailable"
            if answer or before in {PermissionState.GRANTED, PermissionState.DENIED}:
                return "no_dialog"
            return "dialog_shown"
        raise RuntimeError(f"No native request exists for {permission_id.value}")

    def _request_automation_target(self, target: str | None) -> NativeRequestOutcome:
        """Ask for ONE player's Automation consent through a killable child.

        The child is a guarded AppleScript, so a player that is not running is
        left alone (nothing is launched, hidden or otherwise) and nothing is
        asked for it. Nothing here holds a lock or writes a record.
        """
        import subprocess  # lazy: only the darwin request path reaches this

        if target is None or not _is_automation_target(target):
            log.debug("Automation consent needs a scriptable player, got %r.", target)
            return "unavailable"
        try:
            completed = self._automation_consent_runner(_automation_consent_script(target))
        except subprocess.TimeoutExpired:
            # The runner has killed the child. Whether macOS tears down the
            # dialog the child raised is not documented by Apple (UNVERIFIED), so
            # the state read stays the only truth about an answer, and the caller
            # hears "timed_out" rather than "no_dialog" (a player that is not
            # running): the user may simply not have looked yet.
            log.debug("The Automation consent for %s was not answered in time.", target)
            return "timed_out"
        if completed is None:
            return "unavailable"
        log.debug(
            "The Automation consent run for %s ended with rc=%s.",
            target,
            getattr(completed, "returncode", None),
        )
        return "no_dialog"

    def open_pane(self, permission_id: PermissionId | str) -> bool:
        """Open the System Settings pane of a permission; nothing else. Never raises.

        Opening a pane is not a prompt and not an action on the permission, so it
        does not need the
        installed-app identity (design P6: identity decides who may RESET and
        whether we may auto-ASK, not who may open a pane); it only needs a desktop
        session. ``False`` when there is no pane, no session or the open failed.
        """
        try:
            permission = PermissionId(permission_id)
        except (ValueError, TypeError):
            log.debug("Opening the pane of the unknown permission %r ignored.", permission_id)
            return False
        if self.platform != "darwin" or permission not in _SETTINGS_URLS:
            return False
        try:
            if not self.has_desktop_session():
                log.debug("No desktop session: the %s pane is not opened.", permission.value)
                return False
            opened = self._open_settings(permission)
        except Exception:  # noqa: BLE001 - the native settings boundary
            log.debug("Opening the %s pane failed.", permission.value, exc_info=True)
            return False
        return opened

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
        url_text = _SETTINGS_URLS[permission_id]
        # Terminate a running Settings only when it may show ANOTHER pane than the
        # one asked for: the anchor of a running instance is ignored (observed on
        # macOS 15.7, not documented, UNVERIFIED), but closing the window the user
        # already has on the right pane for no reason is rude. What this process
        # last opened is all we know; the user may have navigated since.
        if self._last_opened_url != url_text:
            self._quit_system_settings(appkit)
        url = foundation.NSURL.URLWithString_(url_text)
        opened = bool(appkit.NSWorkspace.sharedWorkspace().openURL_(url))
        if opened:
            self._last_opened_url = url_text
        return opened

    def reset_row(
        self, permission_id: PermissionId, *, dry_run: bool = False
    ) -> PermissionOperation:
        """Drop this app's own TCC row so the native prompt can appear again.

        The in-app way out of the auto-denied trap: once ANY build of the app
        ever created an input listener before the user was asked - or a signature
        change orphaned the recorded grant (BUG-083) - macOS silently registers
        the app as DENIED and suppresses every further prompt.
        ``tccutil reset <service> <our bundle id>`` returns that one row to "not
        determined" (never touching other apps' grants), so the real system
        dialog can fire again on the next request.

        Only the installed app may name its own bundle id: the .dmg build owns
        ``ai.personaljarvis.desktop`` and never touched the managed bundle's rows,
        while a development run (Terminal, bare Python) must not be able to wipe
        the installed app's grants. A dry run changes nothing. Only ``tccutil``
        runs here; nothing is probed, the caller reads the one permission it cares
        about before and after.
        """
        service = tcc_reset_service(permission_id)
        label = _LABELS[permission_id]

        def refused(message: str) -> PermissionOperation:
            return PermissionOperation(False, permission_id.value, "reset", False, dry_run, message)

        if self.platform != "darwin" or service is None:
            return refused(f"{label} has no resettable macOS record.")
        if dry_run:
            return PermissionOperation(
                True,
                permission_id.value,
                "reset",
                False,
                True,
                f"Would reset this app's {label} record.",
            )
        bundle_id, _path, _launched, stable = self._bundle_identity()
        if not stable or not bundle_id:
            return refused(
                "Relaunch Personal Jarvis from its installed app before resetting a permission."
            )
        import subprocess  # lazy: this method is darwin-only at runtime

        from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

        try:
            result = subprocess.run(
                ["/usr/bin/tccutil", "reset", service, bundle_id],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
            performed = result.returncode == 0
            if not performed:
                log.debug(
                    "tccutil reset %s failed with status %s: %s",
                    service,
                    result.returncode,
                    (result.stderr or result.stdout or "").strip()[-200:],
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            # The person reads a fixed sentence (no OS error text, no paths); the
            # reason is kept in the debug log.
            performed = False
            log.debug("tccutil reset %s could not run", service, exc_info=exc)
        if not performed:
            return refused(f"Could not reset the {label} record.")
        return PermissionOperation(
            True,
            permission_id.value,
            "reset",
            True,
            False,
            f"{label} was reset - the system prompt can appear again on the next request.",
        )


def tcc_reset_service(permission_id: PermissionId) -> str | None:
    """The ``tccutil`` service name of ``permission_id``, or ``None`` when it has no TCC row.

    The one table behind ``tccutil reset <service> <bundle id>``: the port's
    :meth:`SystemPermissionPort.reset_row` and the local ``jarvis permissions reset``
    command both read it, so the two can never disagree about a service name.
    """
    return _TCC_RESET_SERVICES.get(permission_id)


_DEFAULT_SYSTEM_PERMISSION_PORT = SystemPermissionPort()


def get_system_permission_port() -> SystemPermissionPort:
    """Return the process-wide port; it keeps only the static bundle identity."""
    return _DEFAULT_SYSTEM_PERMISSION_PORT


__all__ = [
    "ACCEPTED_BUNDLE_IDS",
    "APP_NAME",
    "AUTOMATION_TARGETS",
    "EXPECTED_BUNDLE_ID",
    "PANE_FAMILY",
    "REQUEST_CLASS",
    "SETTINGS_PATH_TEXT",
    "AppIdentity",
    "NativeRequestOutcome",
    "PermissionId",
    "PermissionOperation",
    "PermissionState",
    "RequestClass",
    "SystemPermissionPort",
    "get_system_permission_port",
    "remove_leftover_state_files",
    "settings_path_text",
    "tcc_reset_service",
]
