"""Unit coverage for the additive just-in-time API of the permission port.

``state(..., target=, deep=)``, ``request_native``, ``usage_string_present``,
``outside_installed_app`` and the request-class / pane-family / settings-path
metadata. Every native framework is a hand-written stub; nothing here talks to
a real Mac, and every behaviour that Apple does not document stays labelled
unverified in the port.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.branding import MACOS_DMG_BUNDLE_ID
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.platform import permissions as permissions_module
from jarvis.platform.permissions import (
    ACCEPTED_BUNDLE_IDS,
    AUTOMATION_TARGETS,
    EXPECTED_BUNDLE_ID,
    PANE_FAMILY,
    REQUEST_CLASS,
    SETTINGS_PATH_TEXT,
    PermissionId,
    PermissionState,
    RequestClass,
    SystemPermissionPort,
    settings_path_text,
)

_MUSIC = "com.apple.Music"
_SPOTIFY = "com.spotify.client"
_INSTALLED_PATH = str(Path.home() / "Applications" / "Personal Jarvis.app")

# kIOHIDRequestTypeListenEvent / kIOHIDAccessType* raw values (stable ABI).
_IOHID_LISTEN = 1
_IOHID_DENIED = 1
_IOHID_UNKNOWN = 2

_ALL_INFO = {
    "NSMicrophoneUsageDescription": "Personal Jarvis uses the microphone.",
    "NSAppleEventsUsageDescription": "Personal Jarvis sends commands to other apps.",
}


@pytest.fixture(autouse=True)
def _isolated_state_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the two state files of the legacy snapshot out of the real data dir."""
    monkeypatch.setattr(
        permissions_module, "identity_reset_marker_path", lambda: tmp_path / "reset.json"
    )
    monkeypatch.setattr(
        permissions_module, "automation_consent_path", lambda: tmp_path / "consent.json"
    )


class _Bundle:
    """``NSBundle.mainBundle()`` stand-in with a readable Info.plist."""

    def __init__(
        self,
        bundle_id: str | None,
        path: str,
        info: dict[str, str] | None,
        info_error: Exception | None = None,
    ) -> None:
        self._bundle_id = bundle_id
        self._path = path
        self._info = info or {}
        self._info_error = info_error
        self.info_reads: list[str] = []

    def bundleIdentifier(self) -> str | None:
        return self._bundle_id

    def bundlePath(self) -> str:
        return self._path

    def objectForInfoDictionaryKey_(self, key: str) -> str | None:
        self.info_reads.append(key)
        if self._info_error is not None:
            raise self._info_error
        return self._info.get(key)


class _CaptureDevice:
    """``AVCaptureDevice`` stand-in reading and recording on its world."""

    def __init__(self, world: _World) -> None:
        self._world = world

    def authorizationStatusForMediaType_(self, _media_type: str) -> int:
        return self._world.mic_status

    def requestAccessForMediaType_completionHandler_(self, media_type: str, handler: Any) -> None:
        self._world.boom()
        self._world.requests.append("microphone")
        self._world.mic_media_types.append(media_type)
        handler(True)


class _Player:
    def __init__(self, bundle_id: str) -> None:
        self.bundle_id = bundle_id


class _Workspace:
    def __init__(self, world: _World) -> None:
        self._world = world

    def URLForApplicationWithBundleIdentifier_(self, bundle_id: str) -> str | None:
        if bundle_id in self._world.installed:
            return f"file:///Applications/{bundle_id}.app"
        return None

    def openApplicationAtURL_configuration_completionHandler_(self, url: str, *_rest: Any) -> None:
        self._world.launches.append(url)

    def frontmostApplication(self) -> SimpleNamespace:
        return SimpleNamespace(processIdentifier=lambda: 123)


class _World:
    """One simulated Mac: native stubs, their answers and an ordered request log."""

    def __init__(
        self,
        *,
        bundle_id: str | None = EXPECTED_BUNDLE_ID,
        bundle_path: str = _INSTALLED_PATH,
        info: dict[str, str] | None = None,
        info_error: Exception | None = None,
    ) -> None:
        self.bundle = _Bundle(
            bundle_id, bundle_path, dict(_ALL_INFO) if info is None else info, info_error
        )
        self.imports: list[str] = []
        # Every native call that could make macOS ask, in order. A state read
        # must leave this empty.
        self.requests: list[str] = []
        self.event_taps = 0
        self.launches: list[str] = []
        self.mic_media_types: list[str] = []
        self.explode = False  # every native request raises
        self.mic_status = 0  # not determined
        self.screen_preflight = False
        self.screen_request_answer = False
        self.ax_trusted = False
        self.ax_request_answer = False
        self.ax_options: list[dict[str, bool]] = []
        self.listen_request_answer = False
        self.post_request_answer = False
        self.iohid_state: dict[int, int | None] = {}
        self.iohid_request_answer: bool | None = None
        self.installed: set[str] = set()
        self.running: set[str] = set()
        self.probe_answers: dict[str, Any] = {}
        self.probes: list[tuple[str, bool]] = []
        self.live_check_answer: bool | None = None
        self.live_checks = 0
        self.consent_scripts: list[str] = []
        self.consent_result: Any = SimpleNamespace(returncode=0, stdout="-\n", stderr="")
        self.recover_calls = 0
        self.modules = self._build_modules()

    # ---- stubs -----------------------------------------------------------
    def boom(self) -> None:
        if self.explode:
            raise RuntimeError("native call exploded")

    def _build_modules(self) -> dict[str, object]:
        workspace = _Workspace(self)
        current = SimpleNamespace(isActive=lambda: True, processIdentifier=lambda: 123)

        def running(bundle_id: str) -> list[_Player]:
            return [_Player(bundle_id)] if bundle_id in self.running else []

        def request_screen() -> bool:
            self.boom()
            self.requests.append("screen_recording")
            return self.screen_request_answer

        def request_listen() -> bool:
            self.boom()
            self.requests.append("listen")
            return self.listen_request_answer

        def request_post() -> bool:
            self.boom()
            self.requests.append("post")
            return self.post_request_answer

        def request_ax(options: dict[str, bool]) -> bool:
            if options.get("prompt"):
                self.boom()
                self.requests.append("accessibility")
                self.ax_options.append(dict(options))
                return self.ax_request_answer
            return self.ax_trusted

        def event_tap(*_args: Any) -> None:
            self.event_taps += 1

        return {
            "Foundation": SimpleNamespace(
                NSBundle=SimpleNamespace(mainBundle=lambda: self.bundle),
                NSURL=SimpleNamespace(URLWithString_=lambda value: value),
            ),
            "AppKit": SimpleNamespace(
                NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
                NSRunningApplication=SimpleNamespace(
                    currentApplication=lambda: current,
                    runningApplicationsWithBundleIdentifier_=running,
                ),
            ),
            "AVFoundation": SimpleNamespace(
                AVCaptureDevice=_CaptureDevice(self),
                AVMediaTypeAudio="audio",
                AVAuthorizationStatusNotDetermined=0,
                AVAuthorizationStatusRestricted=1,
                AVAuthorizationStatusDenied=2,
                AVAuthorizationStatusAuthorized=3,
            ),
            "Quartz": SimpleNamespace(
                CGPreflightScreenCaptureAccess=lambda: self.screen_preflight,
                CGRequestScreenCaptureAccess=request_screen,
                CGPreflightListenEventAccess=lambda: False,
                CGRequestListenEventAccess=request_listen,
                CGPreflightPostEventAccess=lambda: False,
                CGRequestPostEventAccess=request_post,
                CGEventTapCreate=event_tap,
            ),
            "ApplicationServices": SimpleNamespace(
                AXIsProcessTrusted=lambda: self.ax_trusted,
                AXIsProcessTrustedWithOptions=request_ax,
                kAXTrustedCheckOptionPrompt="prompt",
            ),
        }

    def load(self, name: str) -> object:
        self.imports.append(name)
        if name not in self.modules:
            raise ModuleNotFoundError(name)
        return self.modules[name]

    def _probe(self, bundle_id: str, ask: bool) -> int | None:
        self.probes.append((bundle_id, ask))
        if ask:
            self.requests.append(f"ae_ask:{bundle_id}")
        answer = self.probe_answers.get(bundle_id)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _live_check(self) -> bool | None:
        self.live_checks += 1
        return self.live_check_answer

    def _consent_runner(self, script: str) -> Any:
        self.boom()
        self.consent_scripts.append(script)
        self.requests.append("consent_runner")
        if isinstance(self.consent_result, Exception):
            raise self.consent_result
        return self.consent_result

    def _iohid_request(self, request_type: int) -> bool | None:
        self.boom()
        self.requests.append(f"iohid_request:{request_type}")
        return self.iohid_request_answer

    def _recover(self) -> bool:
        self.boom()
        self.recover_calls += 1
        self.requests.append("keychain_replay")
        return True

    def port(self, *, default_live_check: bool = False) -> SystemPermissionPort:
        kwargs: dict[str, Any] = {}
        if not default_live_check:
            kwargs["screen_capture_live_check"] = self._live_check
        return SystemPermissionPort(
            platform_name="darwin",
            module_loader=self.load,
            iohid_check=lambda request_type: self.iohid_state.get(request_type),
            credential_store_backend=lambda: "platform",
            credential_store_recover=self._recover,
            automation_probe=self._probe,
            iohid_request=self._iohid_request,
            automation_consent_runner=self._consent_runner,
            **kwargs,
        )


def _non_darwin_port(imports: list[str]) -> SystemPermissionPort:
    def load(name: str) -> object:
        imports.append(name)
        raise AssertionError(f"a non-macOS port must not load {name}")

    return SystemPermissionPort(platform_name="win32", module_loader=load)


# --- request class, pane family and settings path metadata ------------------


def test_request_class_vocabulary_is_stable() -> None:
    assert [item.value for item in RequestClass] == ["dialog", "prompt_once", "native", "none"]
    assert RequestClass.DIALOG == "dialog"


def test_every_permission_has_a_request_class() -> None:
    assert set(REQUEST_CLASS) == set(PermissionId)


@pytest.mark.parametrize(
    ("permission", "expected"),
    [
        (PermissionId.MICROPHONE, RequestClass.DIALOG),
        (PermissionId.SCREEN_RECORDING, RequestClass.PROMPT_ONCE),
        (PermissionId.ACCESSIBILITY, RequestClass.PROMPT_ONCE),
        (PermissionId.INPUT_MONITORING, RequestClass.PROMPT_ONCE),
        (PermissionId.EVENT_POSTING, RequestClass.PROMPT_ONCE),
        (PermissionId.AUTOMATION, RequestClass.DIALOG),
        (PermissionId.CREDENTIAL_STORE, RequestClass.NONE),
    ],
)
def test_request_class_follows_the_policy_table(
    permission: PermissionId, expected: RequestClass
) -> None:
    assert REQUEST_CLASS[permission] is expected


def test_event_posting_and_accessibility_share_one_pane_family() -> None:
    assert PANE_FAMILY[PermissionId.EVENT_POSTING] is PermissionId.ACCESSIBILITY
    assert PANE_FAMILY[PermissionId.ACCESSIBILITY] is PermissionId.ACCESSIBILITY


def test_pane_family_names_a_canonical_member_for_every_permission() -> None:
    assert set(PANE_FAMILY) == set(PermissionId)
    for permission, family in PANE_FAMILY.items():
        assert PANE_FAMILY[family] is family, permission
    # Every other permission keeps a family of its own.
    families = {family for permission, family in PANE_FAMILY.items()}
    assert len(families) == len(PermissionId) - 1


def test_pane_family_agrees_with_the_settings_deep_links() -> None:
    urls = permissions_module._SETTINGS_URLS
    for first in urls:
        for second in urls:
            assert (urls[first] == urls[second]) == (PANE_FAMILY[first] == PANE_FAMILY[second])


def test_settings_path_text_is_plain_english_for_every_pane() -> None:
    assert set(SETTINGS_PATH_TEXT) == set(permissions_module._SETTINGS_URLS)
    for permission, text in SETTINGS_PATH_TEXT.items():
        assert text.startswith("System Settings > Privacy & Security > "), permission
        assert text.isascii(), permission
    assert SETTINGS_PATH_TEXT[PermissionId.MICROPHONE] == (
        "System Settings > Privacy & Security > Microphone"
    )
    assert (
        SETTINGS_PATH_TEXT[PermissionId.EVENT_POSTING]
        == (SETTINGS_PATH_TEXT[PermissionId.ACCESSIBILITY])
    )


def test_settings_path_helper_takes_strings_and_has_no_path_for_the_keychain() -> None:
    assert settings_path_text("input_monitoring") == (
        "System Settings > Privacy & Security > Input Monitoring"
    )
    assert settings_path_text(PermissionId.CREDENTIAL_STORE) is None


def test_the_existing_labels_and_deep_links_are_untouched() -> None:
    assert permissions_module._LABELS[PermissionId.EVENT_POSTING] == "Input Control"
    assert permissions_module._SETTINGS_URLS[PermissionId.INPUT_MONITORING] == (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
    )


# --- state(): live, never asks, optional target and depth --------------------


def test_state_never_calls_a_request_function() -> None:
    world = _World()
    world.installed = {_MUSIC}
    world.running = {_MUSIC}
    world.probe_answers = {_MUSIC: -1744}  # would require consent: the dialog trigger
    port = world.port()

    for permission in PermissionId:
        port.state(permission)
        port.state(permission, deep=True)
        port.state(permission, deep=False)
    port.state(PermissionId.AUTOMATION, target=_MUSIC)

    assert world.requests == []
    assert world.event_taps == 0
    assert world.launches == []
    assert world.probes
    assert all(ask is False for _bundle_id, ask in world.probes)


def test_state_stays_live_and_uncached() -> None:
    world = _World()
    port = world.port()

    world.mic_status = 0
    first = port.state(PermissionId.MICROPHONE)
    world.mic_status = 3
    second = port.state("microphone")

    assert (first, second) == (PermissionState.NOT_DETERMINED, PermissionState.GRANTED)


def test_target_and_deep_are_keyword_only() -> None:
    port = _World().port()

    with pytest.raises(TypeError):
        port.state(PermissionId.SCREEN_RECORDING, None, True)  # type: ignore[misc]


def test_a_target_is_ignored_for_every_permission_but_automation() -> None:
    world = _World()
    world.mic_status = 3
    port = world.port()

    assert port.state(PermissionId.MICROPHONE, target="not.a.player") is PermissionState.GRANTED
    assert world.probes == []


def test_a_shallow_screen_recording_read_is_the_preflight_alone() -> None:
    world = _World()
    world.live_check_answer = True  # the oracle WOULD upgrade the verdict
    port = world.port()

    assert port.state(PermissionId.SCREEN_RECORDING, deep=False) is PermissionState.NOT_GRANTED
    assert world.live_checks == 0


def test_a_deep_screen_recording_read_runs_the_oracle_only_when_it_can_help() -> None:
    world = _World()
    world.live_check_answer = True
    port = world.port()

    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.GRANTED
    assert world.live_checks == 1

    # A granted preflight needs no second opinion.
    world.screen_preflight = True
    world.live_checks = 0
    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.GRANTED
    assert world.live_checks == 0


def test_a_deep_read_never_invents_a_grant_the_oracle_cannot_prove() -> None:
    world = _World()
    world.live_check_answer = None
    port = world.port()

    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.NOT_GRANTED


def test_only_a_deep_read_enumerates_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default oracle lists the on-screen windows; a shallow read must not."""
    enumerations: list[int] = []

    def windows(*_args: Any) -> list[dict[str, object]]:
        enumerations.append(1)
        return [{"kCGWindowLayer": 0, "kCGWindowOwnerPID": os.getpid() + 1, "kCGWindowName": "X"}]

    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        SimpleNamespace(
            CGWindowListCopyWindowInfo=windows,
            kCGWindowListOptionOnScreenOnly=1,
            kCGWindowListExcludeDesktopElements=16,
            kCGNullWindowID=0,
        ),
    )
    port = _World().port(default_live_check=True)

    assert port.state(PermissionId.SCREEN_RECORDING, deep=False) is PermissionState.NOT_GRANTED
    assert enumerations == []
    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.GRANTED
    assert enumerations == [1]


def test_a_state_read_off_macos_loads_nothing() -> None:
    imports: list[str] = []
    port = _non_darwin_port(imports)

    for permission in PermissionId:
        assert port.state(permission) is PermissionState.NOT_REQUIRED
        assert port.state(permission, deep=True, target=_MUSIC) is PermissionState.NOT_REQUIRED
    assert imports == []


# --- state(AUTOMATION, target=): one player, read-only -------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (0, PermissionState.GRANTED),
        (-1743, PermissionState.DENIED),
        (-1744, PermissionState.NOT_DETERMINED),
    ],
)
def test_a_running_player_is_read_live_without_asking(
    answer: int, expected: PermissionState, tmp_path: Path
) -> None:
    world = _World()
    world.installed = {_MUSIC, _SPOTIFY}
    world.running = {_MUSIC}
    world.probe_answers = {_MUSIC: answer, _SPOTIFY: 0}

    state = world.port().state(PermissionId.AUTOMATION, target=_MUSIC)

    assert state is expected
    # Exactly the one requested player is probed, and never with ask=True.
    assert world.probes == [(_MUSIC, False)]
    # The legacy aggregate keeps a consent record; the per-target read must not.
    assert not (tmp_path / "consent.json").exists()


def test_a_player_that_is_not_running_is_unknown_not_granted() -> None:
    world = _World()
    world.installed = {_MUSIC}
    world.probe_answers = {_MUSIC: 0}  # would read granted if it were asked

    state = world.port().state(PermissionId.AUTOMATION, target=_MUSIC)

    assert state is PermissionState.NOT_DETERMINED
    assert world.probes == []


def test_a_probe_without_an_answer_for_a_running_player_is_unknown() -> None:
    world = _World()
    world.installed = {_MUSIC}
    world.running = {_MUSIC}
    world.probe_answers = {_MUSIC: -600}  # raced with the player quitting

    assert world.port().state(PermissionId.AUTOMATION, target=_MUSIC) is (
        PermissionState.NOT_DETERMINED
    )


def test_a_probe_that_raises_degrades_to_unknown() -> None:
    world = _World()
    world.installed = {_MUSIC}
    world.running = {_MUSIC}
    world.probe_answers = {_MUSIC: RuntimeError("Apple Event Manager hung up")}

    assert world.port().state(PermissionId.AUTOMATION, target=_MUSIC) is (
        PermissionState.NOT_DETERMINED
    )


def test_a_player_that_is_not_installed_needs_no_consent() -> None:
    world = _World()
    world.installed = {_SPOTIFY}

    assert world.port().state(PermissionId.AUTOMATION, target=_MUSIC) is (
        PermissionState.NOT_REQUIRED
    )


def test_an_unknown_target_is_unavailable_and_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = _World()
    world.installed = {_MUSIC}
    world.running = {_MUSIC}
    caplog.set_level(logging.DEBUG, logger="jarvis.platform.permissions")

    for target in ("com.example.NotAPlayer", "", "Music"):
        assert world.port().state(PermissionId.AUTOMATION, target=target) is (
            PermissionState.UNAVAILABLE
        )

    assert world.probes == []
    assert any("com.example.NotAPlayer" in record.getMessage() for record in caplog.records)


def test_a_missing_appkit_makes_a_target_read_unavailable() -> None:
    world = _World()
    del world.modules["AppKit"]

    assert world.port().state(PermissionId.AUTOMATION, target=_MUSIC) is (
        PermissionState.UNAVAILABLE
    )


def test_every_known_player_is_a_valid_target() -> None:
    world = _World()
    world.installed = {bundle_id for _name, bundle_id in AUTOMATION_TARGETS}
    port = world.port()

    for _name, bundle_id in AUTOMATION_TARGETS:
        assert port.state(PermissionId.AUTOMATION, target=bundle_id) is (
            PermissionState.NOT_DETERMINED
        )


# --- request_native(): only the native request, never raises -------------------


def test_a_microphone_request_that_can_show_a_dialog_says_so() -> None:
    world = _World()
    world.mic_status = 0  # not determined

    outcome = world.port().request_native(PermissionId.MICROPHONE)

    assert outcome == "dialog_shown"
    assert world.requests == ["microphone"]
    assert world.mic_media_types == ["audio"]


@pytest.mark.parametrize("status", [1, 2, 3])
def test_a_microphone_that_is_already_decided_shows_no_dialog(status: int) -> None:
    world = _World()
    world.mic_status = status

    assert world.port().request_native(PermissionId.MICROPHONE) == "no_dialog"
    assert world.requests == ["microphone"]  # still the one native call, nothing else


@pytest.mark.parametrize(("answer", "expected"), [(False, "dialog_shown"), (True, "no_dialog")])
def test_screen_recording_request_reports_what_the_os_may_be_doing(
    answer: bool, expected: str
) -> None:
    world = _World()
    world.screen_request_answer = answer

    assert world.port().request_native(PermissionId.SCREEN_RECORDING) == expected
    assert world.requests == ["screen_recording"]


@pytest.mark.parametrize(("answer", "expected"), [(False, "dialog_shown"), (True, "no_dialog")])
def test_accessibility_request_uses_the_prompt_option(answer: bool, expected: str) -> None:
    world = _World()
    world.ax_request_answer = answer

    assert world.port().request_native(PermissionId.ACCESSIBILITY) == expected
    assert world.ax_options == [{"prompt": True}]


def test_event_posting_is_the_accessibility_request() -> None:
    world = _World()

    outcome = world.port().request_native(PermissionId.EVENT_POSTING)

    assert outcome == "dialog_shown"
    assert world.requests == ["accessibility"]  # not CGRequestPostEventAccess
    assert world.ax_options == [{"prompt": True}]


def test_input_monitoring_not_determined_may_show_a_dialog() -> None:
    world = _World()
    world.iohid_state = {_IOHID_LISTEN: _IOHID_UNKNOWN}

    assert world.port().request_native(PermissionId.INPUT_MONITORING) == "dialog_shown"
    assert world.requests == ["listen"]


def test_input_monitoring_with_a_decision_on_file_shows_no_dialog() -> None:
    world = _World()
    world.iohid_state = {_IOHID_LISTEN: _IOHID_DENIED}

    assert world.port().request_native(PermissionId.INPUT_MONITORING) == "no_dialog"
    assert world.requests == ["listen"]  # the request is still made; the OS ignores it


def test_input_monitoring_already_granted_shows_no_dialog() -> None:
    world = _World()
    world.listen_request_answer = True

    assert world.port().request_native(PermissionId.INPUT_MONITORING) == "no_dialog"


def test_input_monitoring_without_a_tristate_reads_the_boolean_answer() -> None:
    world = _World()  # no IOHID answer at all

    assert world.port().request_native(PermissionId.INPUT_MONITORING) == "dialog_shown"


def test_input_monitoring_falls_back_to_iohid_when_quartz_lacks_the_request() -> None:
    world = _World()
    del world.modules["Quartz"].CGRequestListenEventAccess
    world.iohid_state = {_IOHID_LISTEN: _IOHID_UNKNOWN}
    world.iohid_request_answer = False

    assert world.port().request_native(PermissionId.INPUT_MONITORING) == "dialog_shown"
    assert world.requests == [f"iohid_request:{_IOHID_LISTEN}"]


def test_input_monitoring_with_no_way_to_ask_is_unavailable() -> None:
    world = _World()
    del world.modules["Quartz"].CGRequestListenEventAccess
    world.iohid_request_answer = None

    assert world.port().request_native(PermissionId.INPUT_MONITORING) == "unavailable"


def test_a_native_request_never_creates_an_event_tap() -> None:
    world = _World()
    port = world.port()

    for permission in PermissionId:
        port.request_native(permission, target=_MUSIC)

    assert world.event_taps == 0


@pytest.mark.parametrize(
    ("missing_module", "permission", "attempted"),
    [
        ("AVFoundation", PermissionId.MICROPHONE, []),
        ("Quartz", PermissionId.SCREEN_RECORDING, []),
        ("ApplicationServices", PermissionId.ACCESSIBILITY, []),
        ("ApplicationServices", PermissionId.EVENT_POSTING, []),
        # Input Monitoring tries IOHIDRequestAccess when Quartz is gone; the
        # stub has no answer for it, so there is still no way to ask.
        ("Quartz", PermissionId.INPUT_MONITORING, [f"iohid_request:{_IOHID_LISTEN}"]),
    ],
)
def test_a_missing_framework_makes_the_request_unavailable(
    missing_module: str, permission: PermissionId, attempted: list[str]
) -> None:
    world = _World()
    del world.modules[missing_module]

    assert world.port().request_native(permission) == "unavailable"
    assert world.requests == attempted


def test_a_request_function_the_binding_lacks_is_unavailable() -> None:
    world = _World()
    del world.modules["Quartz"].CGRequestScreenCaptureAccess

    assert world.port().request_native(PermissionId.SCREEN_RECORDING) == "unavailable"


@pytest.mark.parametrize("permission", list(PermissionId))
def test_request_native_never_raises_when_the_native_call_does(
    permission: PermissionId, caplog: pytest.LogCaptureFixture
) -> None:
    world = _World()
    world.explode = True
    caplog.set_level(logging.DEBUG, logger="jarvis.platform.permissions")

    outcome = world.port().request_native(permission, target=_MUSIC)

    assert outcome == "unavailable"
    # Never silent (AP-30): the failure is logged with its traceback.
    assert any(record.exc_info for record in caplog.records), permission


def test_request_native_ignores_an_unknown_permission() -> None:
    world = _World()

    assert world.port().request_native("bogus") == "unavailable"
    assert world.requests == []


def test_request_native_off_macos_loads_nothing_and_asks_nothing() -> None:
    imports: list[str] = []
    port = _non_darwin_port(imports)

    for permission in PermissionId:
        assert port.request_native(permission, target=_MUSIC) == "unavailable"
    assert imports == []


@pytest.mark.parametrize(
    ("bundle_id", "path"),
    [
        (None, "/usr/local/bin"),
        ("org.python.python", "/Library/Frameworks/Python.framework/Python.app"),
        (EXPECTED_BUNDLE_ID, "/Users/someone/Downloads/Personal Jarvis.app"),
    ],
)
def test_request_native_makes_no_identity_check_of_its_own(
    bundle_id: str | None, path: str
) -> None:
    """Whether we may ask is the service's decision (design P6), not the port's."""
    world = _World(bundle_id=bundle_id, bundle_path=path, info={})
    port = world.port()
    assert port.outside_installed_app is True

    assert port.request_native(PermissionId.MICROPHONE) == "dialog_shown"
    assert port.request_native(PermissionId.SCREEN_RECORDING) == "dialog_shown"
    assert world.requests == ["microphone", "screen_recording"]
    # ... and it does not look for the usage string either: that is the service's job.
    assert world.bundle.info_reads == []


def test_a_request_answer_is_never_evidence_of_a_grant(tmp_path: Path) -> None:
    world = _World()
    world.screen_request_answer = True  # the API says "already granted"
    world.ax_request_answer = True
    port = world.port()

    assert port.request_native(PermissionId.SCREEN_RECORDING) == "no_dialog"
    assert port.request_native(PermissionId.ACCESSIBILITY) == "no_dialog"

    # Only the live read says anything about access, and it still says no.
    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.NOT_GRANTED
    assert port.state(PermissionId.ACCESSIBILITY) is PermissionState.NOT_GRANTED
    # And the request recorded nothing: no restart flag, no consent file.
    assert port._restart_required == set()
    assert not (tmp_path / "consent.json").exists()


# --- request_native(AUTOMATION): a killable, guarded child, never a launch -----


def test_automation_needs_a_scriptable_player() -> None:
    world = _World()
    port = world.port()

    assert port.request_native(PermissionId.AUTOMATION) == "unavailable"
    assert port.request_native(PermissionId.AUTOMATION, target="com.example.Evil") == "unavailable"
    assert port.request_native(PermissionId.AUTOMATION, target='x" & (do shell script "id")') == (
        "unavailable"
    )
    assert world.consent_scripts == []
    assert world.requests == []


def test_automation_runs_one_guarded_script_and_never_launches_the_player(
    tmp_path: Path,
) -> None:
    world = _World()
    world.installed = {_MUSIC}  # installed, NOT running

    outcome = world.port().request_native(PermissionId.AUTOMATION, target=_MUSIC)

    assert outcome == "no_dialog"
    assert len(world.consent_scripts) == 1
    script = world.consent_scripts[0]
    # The running check sits INSIDE the script, so a bare tell can never launch it.
    assert f'if application id "{_MUSIC}" is running then' in script
    assert f'tell application id "{_MUSIC}" to get player state' in script
    assert "launch" not in script and "activate" not in script
    assert world.launches == []
    # No in-process Apple Event ask, no player probe, no consent record.
    assert world.probes == []
    assert not (tmp_path / "consent.json").exists()


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(returncode=0, stdout="-\n", stderr=""),  # player not running
        SimpleNamespace(returncode=0, stdout="+\n", stderr=""),  # event sent and permitted
        SimpleNamespace(returncode=1, stdout="", stderr="execution error: (-1743)"),  # denied
        subprocess.TimeoutExpired(cmd="osascript", timeout=120.0),  # nobody answered
    ],
    ids=["not_running", "permitted", "denied", "timed_out"],
)
def test_a_finished_automation_request_leaves_no_dialog_open(result: Any) -> None:
    world = _World()
    world.consent_result = result

    # Synchronous: the answer, if any, is in state(); the return value is not one.
    assert world.port().request_native(PermissionId.AUTOMATION, target=_SPOTIFY) == "no_dialog"
    assert world.requests == ["consent_runner"]


def test_automation_is_unavailable_when_osascript_cannot_run() -> None:
    world = _World()
    world.consent_result = FileNotFoundError("osascript")

    assert world.port().request_native(PermissionId.AUTOMATION, target=_MUSIC) == "unavailable"


def test_automation_is_unavailable_when_the_runner_reports_nothing() -> None:
    world = _World()
    world.consent_result = None

    assert world.port().request_native(PermissionId.AUTOMATION, target=_MUSIC) == "unavailable"


def test_the_default_consent_runner_is_killable_windowless_and_shell_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="-\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    permissions_module._default_automation_consent_runner("return 1")

    assert captured["argv"] == ["osascript", "-e", "return 1"]
    kwargs = captured["kwargs"]
    # 120 s: long enough for a person to answer, and subprocess.run kills the
    # child when it passes (never a 3 s kill that tears the dialog down).
    assert kwargs["timeout"] == 120.0
    assert kwargs["creationflags"] == NO_WINDOW_CREATIONFLAGS
    assert kwargs["encoding"] == "utf-8"
    assert not kwargs.get("shell")


def test_the_keychain_replay_is_a_synchronous_request() -> None:
    world = _World()

    assert world.port().request_native(PermissionId.CREDENTIAL_STORE) == "no_dialog"
    assert world.recover_calls == 1


# --- usage_string_present() -----------------------------------------------------

_NEEDS_A_KEY = [
    (PermissionId.MICROPHONE, "NSMicrophoneUsageDescription"),
    (PermissionId.AUTOMATION, "NSAppleEventsUsageDescription"),
]
_NEEDS_NO_KEY = [
    PermissionId.SCREEN_RECORDING,
    PermissionId.ACCESSIBILITY,
    PermissionId.INPUT_MONITORING,
    PermissionId.EVENT_POSTING,
    PermissionId.CREDENTIAL_STORE,
]


@pytest.mark.parametrize(("permission", "key"), _NEEDS_A_KEY)
def test_the_usage_string_is_found_in_the_main_bundle(permission: PermissionId, key: str) -> None:
    world = _World()

    assert world.port().usage_string_present(permission) is True
    assert world.bundle.info_reads == [key]


@pytest.mark.parametrize(("permission", "key"), _NEEDS_A_KEY)
def test_a_missing_usage_string_refuses(permission: PermissionId, key: str) -> None:
    other = {name: text for name, text in _ALL_INFO.items() if name != key}
    world = _World(info=other)

    assert world.port().usage_string_present(permission) is False


@pytest.mark.parametrize(("permission", "key"), _NEEDS_A_KEY)
@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_a_blank_usage_string_counts_as_missing(
    permission: PermissionId, key: str, blank: str
) -> None:
    world = _World(info={key: blank})

    assert world.port().usage_string_present(permission) is False


def test_only_the_microphone_key_is_missing() -> None:
    world = _World(info={"NSAppleEventsUsageDescription": "Sends commands."})
    port = world.port()

    assert port.usage_string_present(PermissionId.MICROPHONE) is False
    assert port.usage_string_present(PermissionId.AUTOMATION) is True


@pytest.mark.parametrize("permission", _NEEDS_NO_KEY)
def test_a_permission_without_a_usage_key_never_refuses(permission: PermissionId) -> None:
    world = _World(info={})  # no strings at all

    assert world.port().usage_string_present(permission) is True
    assert world.bundle.info_reads == []


@pytest.mark.parametrize(("permission", "_key"), _NEEDS_A_KEY)
def test_no_bundle_id_means_a_terminal_is_responsible_and_the_key_is_not_applicable(
    permission: PermissionId, _key: str
) -> None:
    world = _World(bundle_id=None, bundle_path="/usr/local/bin", info={})

    assert world.port().usage_string_present(permission) is True
    assert world.bundle.info_reads == []


@pytest.mark.parametrize(("permission", "_key"), _NEEDS_A_KEY)
def test_an_unloadable_foundation_means_no_bundle_id_and_so_not_applicable(
    permission: PermissionId, _key: str
) -> None:
    world = _World()
    del world.modules["Foundation"]

    assert world.port().usage_string_present(permission) is True


def test_a_foreign_bundle_without_the_key_refuses() -> None:
    """A frozen Python.app or any other bundle that lacks the key would be killed."""
    world = _World(bundle_id="org.python.python", info={})

    assert world.port().usage_string_present(PermissionId.MICROPHONE) is False


def test_an_unreadable_info_dictionary_fails_closed_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = _World(info_error=RuntimeError("bundle vanished"))
    caplog.set_level(logging.DEBUG, logger="jarvis.platform.permissions")

    assert world.port().usage_string_present(PermissionId.MICROPHONE) is False
    assert any(record.exc_info for record in caplog.records)


def test_the_usage_string_check_accepts_a_plain_string_id() -> None:
    assert _World().port().usage_string_present("microphone") is True


def test_the_usage_string_check_off_macos_loads_nothing() -> None:
    imports: list[str] = []
    port = _non_darwin_port(imports)

    for permission in PermissionId:
        assert port.usage_string_present(permission) is True
    assert imports == []


def test_the_usage_keys_are_keys_the_bundles_really_carry() -> None:
    strings = pytest.importorskip("jarvis.core.macos_privacy_strings")

    for key in permissions_module._USAGE_DESCRIPTION_KEYS.values():
        assert key in strings.REQUIRED_USAGE_KEYS


# --- outside_installed_app -------------------------------------------------------


@pytest.mark.parametrize(
    ("bundle_id", "path"),
    [
        (None, "/usr/local/bin"),
        ("org.python.python", "/Library/Frameworks/Python.framework/Versions/3.11/Python.app"),
        ("com.apple.Terminal", "/System/Applications/Utilities/Terminal.app"),
        ("ai.personaljarvis.desktop.evil", "/Applications/Personal Jarvis.app"),
    ],
    ids=["no_bundle", "python_framework", "terminal", "lookalike_id"],
)
def test_a_development_run_is_outside_the_installed_app(bundle_id: str | None, path: str) -> None:
    assert _World(bundle_id=bundle_id, bundle_path=path).port().outside_installed_app is True


@pytest.mark.parametrize("bundle_id", ACCEPTED_BUNDLE_IDS)
@pytest.mark.parametrize("path", [_INSTALLED_PATH, "/Applications/Personal Jarvis.app"])
def test_an_installed_bundle_is_inside_whichever_accepted_id_it_carries(
    bundle_id: str, path: str
) -> None:
    assert _World(bundle_id=bundle_id, bundle_path=path).port().outside_installed_app is False


def test_the_downloaded_dmg_app_is_inside_once_it_is_installed() -> None:
    world = _World(bundle_id=MACOS_DMG_BUNDLE_ID, bundle_path="/Applications/Personal Jarvis.app")

    assert world.port().outside_installed_app is False


def test_the_dmg_app_running_from_the_mounted_image_is_still_outside() -> None:
    world = _World(
        bundle_id=MACOS_DMG_BUNDLE_ID,
        bundle_path="/Volumes/Personal Jarvis/Personal Jarvis.app",
    )

    assert world.port().outside_installed_app is True


def test_a_bundle_not_in_an_application_folder_is_outside_even_with_our_id() -> None:
    world = _World(bundle_path="/Users/someone/Downloads/Personal Jarvis.app")

    assert world.port().outside_installed_app is True


def test_outside_installed_app_is_the_inverse_of_the_reported_stable_identity() -> None:
    for bundle_id, path in [
        (EXPECTED_BUNDLE_ID, _INSTALLED_PATH),
        (MACOS_DMG_BUNDLE_ID, "/Volumes/Personal Jarvis/Personal Jarvis.app"),
        (None, "/usr/local/bin"),
    ]:
        port = _World(bundle_id=bundle_id, bundle_path=path).port()
        assert port.outside_installed_app is (not port.snapshot()["app_identity"]["stable"])


def test_the_reported_app_identity_keeps_its_shape() -> None:
    identity = _World().port().snapshot()["app_identity"]

    assert set(identity) == {
        "app_name",
        "expected_bundle_id",
        "bundle_id",
        "bundle_path",
        "launched_as_bundle",
        "stable",
        "foreground",
    }
    assert identity["stable"] is True
    assert identity["bundle_id"] == EXPECTED_BUNDLE_ID


def test_reading_the_identity_asks_the_os_for_nothing() -> None:
    world = _World(bundle_id=None, bundle_path="/usr/local/bin")

    assert world.port().outside_installed_app is True
    assert world.requests == []
    assert world.imports == ["Foundation"]


def test_outside_installed_app_is_true_off_macos_and_loads_nothing() -> None:
    imports: list[str] = []

    assert _non_darwin_port(imports).outside_installed_app is True
    assert imports == []
