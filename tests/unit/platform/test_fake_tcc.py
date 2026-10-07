"""Self-tests of ``tests/fakes/fake_tcc.py``.

A fake that consumer tests lean on must itself be pinned: if the simulator
drifts, every test built on it proves nothing. These tests cover the state
machine, the scripted dialog policy, "macOS does not re-ask", the frozen Screen
Recording preflight, Accessibility without ``not_determined``, the tap-before-
request auto-deny, the Automation OSStatus values, the implicit-prompt
companions and the ordered call log. A last group runs the REAL
``SystemPermissionPort`` on top of the fake.

What these tests pin is the MODEL. Where the model encodes behaviour that Apple
does not document, the fake's module docstring says so; nothing here is a claim
about a real Mac.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from jarvis.platform.permissions import AUTOMATION_TARGETS, PermissionId, PermissionState
from tests.fakes.fake_tcc import (
    AE_EVENT_NOT_PERMITTED,
    AE_EVENT_WOULD_REQUIRE_USER_CONSENT,
    AE_NO_ERR,
    AE_PROC_NOT_FOUND,
    DMG_BUNDLE_ID,
    TERMINAL_BUNDLE_ID,
    CallKind,
    CallOutcome,
    DialogPolicy,
    FakeAudioInput,
    FakeEventTap,
    FakeScreenGrab,
    FakeTCC,
    TccProcessAbort,
    TccService,
    TccState,
    install_port,
    make_darwin_port,
    make_non_darwin_port,
)

MIC = TccService.MICROPHONE
SCREEN = TccService.SCREEN_RECORDING
AX = TccService.ACCESSIBILITY
INPUT = TccService.INPUT_MONITORING
POST = TccService.POST_EVENT
AUTOMATION = TccService.AUTOMATION
MUSIC = "com.apple.Music"
SPOTIFY = "com.spotify.client"


def _av(tcc: FakeTCC) -> Any:
    return tcc.modules["AVFoundation"].AVCaptureDevice


def _request_mic(tcc: FakeTCC) -> list[bool]:
    """Fire the microphone request; the list collects what the handler reported."""
    answers: list[bool] = []
    _av(tcc).requestAccessForMediaType_completionHandler_("soun", answers.append)
    return answers


def _mic_status(tcc: FakeTCC) -> int:
    return _av(tcc).authorizationStatusForMediaType_("soun")


def _prompt_ax(tcc: FakeTCC) -> bool:
    services = tcc.modules["ApplicationServices"]
    return services.AXIsProcessTrustedWithOptions({services.kAXTrustedCheckOptionPrompt: True})


# --- state machine -----------------------------------------------------------


def test_every_service_starts_before_its_first_question() -> None:
    tcc = FakeTCC()

    assert tcc.state(MIC) is TccState.NOT_DETERMINED
    assert tcc.state(SCREEN) is TccState.NOT_DETERMINED
    assert tcc.state(INPUT) is TccState.NOT_DETERMINED
    assert tcc.state(AUTOMATION, MUSIC) is TccState.NOT_DETERMINED
    # Accessibility has no not_determined: untrusted reads as denied. Event
    # posting is an alias and follows it.
    assert tcc.state(AX) is TccState.DENIED
    assert tcc.state(POST) is TccState.DENIED
    tcc.assert_silent()  # inspecting the model is not a framework call


@pytest.mark.parametrize(
    ("policy", "final", "reported"),
    [
        (DialogPolicy.ALLOW, TccState.GRANTED, [True]),
        (DialogPolicy.DENY, TccState.DENIED, [False]),
        # A dialog closed without an answer: the DIALOG class stays unanswered
        # and the completion handler never runs (unverified model).
        (DialogPolicy.DISMISS, TccState.NOT_DETERMINED, []),
    ],
)
def test_microphone_dialog_policies(
    policy: DialogPolicy, final: TccState, reported: list[bool]
) -> None:
    tcc = FakeTCC(default_policy=policy)

    answers = _request_mic(tcc)

    assert tcc.state(MIC) is final
    assert answers == reported
    (request,) = tcc.requests(MIC)
    assert request.outcome is CallOutcome.DIALOG_SHOWN
    assert request.detail == policy.value
    assert (request.state_before, request.state_after) == (TccState.NOT_DETERMINED, final)


def test_the_status_api_reports_the_av_authorization_values() -> None:
    tcc = FakeTCC()
    assert _mic_status(tcc) == 0
    tcc.grant(MIC)
    assert _mic_status(tcc) == 3
    tcc.deny(MIC)
    assert _mic_status(tcc) == 2
    tcc.restrict(MIC)
    assert _mic_status(tcc) == 1


def test_a_prompt_once_service_dismissed_ends_denied() -> None:
    """Closing the Screen Recording dialog leaves the app registered, switch off."""
    tcc = FakeTCC(default_policy=DialogPolicy.DISMISS)

    tcc.modules["Quartz"].CGRequestScreenCaptureAccess()

    assert tcc.state(SCREEN) is TccState.DENIED


def test_a_never_answered_dialog_stays_open_until_the_test_answers() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)

    first = _request_mic(tcc)
    second = _request_mic(tcc)

    assert tcc.dialog_open(MIC) is True
    assert tcc.state(MIC) is TccState.NOT_DETERMINED
    assert first == [] and second == []
    # A second request does not stack a second dialog.
    assert [call.outcome for call in tcc.requests(MIC)] == [
        CallOutcome.DIALOG_SHOWN,
        CallOutcome.DIALOG_ALREADY_OPEN,
    ]
    assert len(tcc.dialogs_shown(MIC)) == 1

    tcc.answer(MIC, DialogPolicy.ALLOW)

    assert tcc.dialog_open(MIC) is False
    assert tcc.state(MIC) is TccState.GRANTED
    assert first == [True] and second == [True]


def test_answering_a_dialog_that_is_not_open_is_an_error() -> None:
    with pytest.raises(LookupError):
        FakeTCC().answer(MIC)


def test_scripted_policies_apply_per_dialog_then_the_default_takes_over() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.ALLOW)
    tcc.script(MIC, DialogPolicy.DENY)

    _request_mic(tcc)
    assert tcc.state(MIC) is TccState.DENIED

    tcc.reset(MIC)
    _request_mic(tcc)
    assert tcc.state(MIC) is TccState.GRANTED


def test_a_sticky_policy_is_per_service() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.ALLOW)
    tcc.set_policy(SCREEN, DialogPolicy.DENY)

    _request_mic(tcc)
    tcc.modules["Quartz"].CGRequestScreenCaptureAccess()

    assert tcc.state(MIC) is TccState.GRANTED
    assert tcc.state(SCREEN) is TccState.DENIED


def test_a_grant_in_settings_closes_an_open_dialog() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    answers = _request_mic(tcc)

    tcc.grant(MIC)

    assert tcc.dialog_open(MIC) is False
    assert answers == [True]


# --- macOS does not re-ask ----------------------------------------------------


def test_a_request_after_a_denial_is_ignored_and_recorded() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.DENY)
    _request_mic(tcc)
    tcc.default_policy = DialogPolicy.ALLOW  # the user would say yes - but is never asked

    answers = _request_mic(tcc)

    assert tcc.state(MIC) is TccState.DENIED
    assert answers == [False]  # the handler reports the answer on file
    assert len(tcc.dialogs_shown(MIC)) == 1
    (ignored,) = tcc.ignored_requests(MIC)
    assert ignored.state_before is TccState.DENIED


def test_a_request_while_granted_shows_nothing() -> None:
    tcc = FakeTCC(granted=[MIC])

    answers = _request_mic(tcc)

    assert answers == [True]
    assert tcc.dialogs_shown() == []
    assert len(tcc.ignored_requests(MIC)) == 1


def test_a_restricted_service_never_asks_and_never_grants() -> None:
    tcc = FakeTCC()
    tcc.restrict(MIC)

    answers = _request_mic(tcc)

    assert answers == [False]
    assert tcc.state(MIC) is TccState.RESTRICTED
    assert [call.outcome for call in tcc.requests(MIC)] == [CallOutcome.RESTRICTED]
    assert tcc.dialogs_shown() == []


# --- Screen Recording ---------------------------------------------------------


def test_the_screen_recording_preflight_is_frozen_until_relaunch() -> None:
    tcc = FakeTCC()
    quartz = tcc.modules["Quartz"]
    assert quartz.CGPreflightScreenCaptureAccess() is False

    # The request returns the state at the call; the user then allows.
    assert quartz.CGRequestScreenCaptureAccess() is False
    assert tcc.state(SCREEN) is TccState.GRANTED
    assert quartz.CGPreflightScreenCaptureAccess() is False  # frozen

    tcc.relaunch()

    assert quartz.CGPreflightScreenCaptureAccess() is True
    assert tcc.launch_count == 2


def test_a_grant_that_exists_at_launch_is_visible_to_the_preflight() -> None:
    tcc = FakeTCC(granted=[SCREEN])

    assert tcc.modules["Quartz"].CGPreflightScreenCaptureAccess() is True


def test_input_monitoring_preflight_is_live_unless_configured_frozen() -> None:
    live = FakeTCC()
    live.grant(INPUT)
    frozen = FakeTCC(preflight_frozen=[SCREEN, INPUT])
    frozen.grant(INPUT)

    assert live.modules["Quartz"].CGPreflightListenEventAccess() is True
    assert frozen.modules["Quartz"].CGPreflightListenEventAccess() is False
    frozen.relaunch()
    assert frozen.modules["Quartz"].CGPreflightListenEventAccess() is True


def test_a_capture_without_the_grant_returns_the_wallpaper_and_no_error() -> None:
    tcc = FakeTCC()
    tcc.deny(SCREEN)
    grab = FakeScreenGrab(tcc)

    frame = grab.grab()

    assert frame.wallpaper_only is True
    assert frame.window_titles == ()
    assert tcc.implicit_prompts() == []  # a decision is on file: nothing to ask
    tcc.grant(SCREEN)
    assert grab.grab().window_titles == ("Safari", "Notes")


def test_a_first_capture_makes_macos_ask_by_itself() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)

    frame = FakeScreenGrab(tcc, caller="screen_snapshot").grab()

    assert frame.wallpaper_only is True
    (prompt,) = tcc.implicit_prompts(SCREEN)
    assert prompt.outcome is CallOutcome.DIALOG_SHOWN
    assert prompt.caller == "screen_snapshot"
    assert tcc.requests() == []  # nothing the code asked for explicitly


def test_both_worlds_for_a_screen_grant_given_while_the_app_runs() -> None:
    """Whether capture works before relaunch is an open conflict: model both."""
    optimistic = FakeTCC()
    optimistic.grant(SCREEN)
    pessimistic = FakeTCC(screen_grant_needs_relaunch=True)
    pessimistic.grant(SCREEN)

    assert FakeScreenGrab(optimistic).grab().wallpaper_only is False
    assert FakeScreenGrab(pessimistic).grab().wallpaper_only is True
    pessimistic.relaunch()
    assert FakeScreenGrab(pessimistic).grab().wallpaper_only is False


def test_the_window_title_oracle_is_a_silent_probe() -> None:
    tcc = FakeTCC()
    assert tcc.screen_capture_live_check() is None  # unknowable, never False
    tcc.grant(SCREEN)
    assert tcc.screen_capture_live_check() is True
    tcc.assert_no_prompts()
    assert len(tcc.probes(SCREEN)) == 2


# --- Accessibility and event posting -----------------------------------------


def test_accessibility_is_a_boolean_and_its_prompt_can_be_called_again() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.DENY)
    services = tcc.modules["ApplicationServices"]
    assert services.AXIsProcessTrusted() is False

    assert _prompt_ax(tcc) is False
    assert _prompt_ax(tcc) is False

    # Re-prompt world (unverified): each call while untrusted shows a dialog.
    assert len(tcc.dialogs_shown(AX)) == 2
    assert tcc.ignored_requests(AX) == []


def test_in_the_no_reprompt_world_the_second_accessibility_prompt_is_ignored() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.DENY, ax_reprompts_while_untrusted=False)

    _prompt_ax(tcc)
    _prompt_ax(tcc)

    assert len(tcc.dialogs_shown(AX)) == 1
    assert len(tcc.ignored_requests(AX)) == 1


def test_an_accessibility_prompt_without_the_option_is_only_a_read() -> None:
    tcc = FakeTCC()

    assert tcc.modules["ApplicationServices"].AXIsProcessTrustedWithOptions({}) is False

    assert tcc.requests() == []
    assert len(tcc.probes(AX)) == 1


def test_an_allowed_accessibility_prompt_makes_the_process_trusted() -> None:
    tcc = FakeTCC()

    assert _prompt_ax(tcc) is False  # the state at the call; the user acts afterwards
    assert tcc.modules["ApplicationServices"].AXIsProcessTrusted() is True
    assert tcc.modules["Quartz"].CGPreflightPostEventAccess() is True  # alias


def test_event_posting_asks_through_the_accessibility_switch() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.DENY)

    tcc.modules["Quartz"].CGRequestPostEventAccess()

    assert tcc.state(AX) is TccState.DENIED
    (request,) = tcc.requests(POST)
    assert request.service is POST
    tcc.default_policy = DialogPolicy.ALLOW
    tcc.reset(AX)
    tcc.modules["Quartz"].CGRequestPostEventAccess()
    assert tcc.state(AX) is TccState.GRANTED


def test_the_hid_post_event_tristate_reads_unknown_until_the_user_was_asked() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.DENY)
    assert tcc.iohid_check(0) == 2

    _prompt_ax(tcc)

    assert tcc.iohid_check(0) == 1


# --- Input Monitoring and event taps -----------------------------------------


def test_a_tap_created_before_the_request_registers_the_app_as_denied() -> None:
    """The BUG-058 class: creating a tap is not a way to ask."""
    tcc = FakeTCC()
    tap = FakeEventTap(tcc, caller="hotkeys")

    assert tap.create() is False

    (prompt,) = tcc.implicit_prompts(INPUT)
    assert prompt.outcome is CallOutcome.AUTO_DENIED
    assert prompt.caller == "hotkeys"
    assert tcc.state(INPUT) is TccState.DENIED
    assert tcc.iohid_check(1) == 1

    # The request that should have come first is now ignored: no dialog, ever.
    assert tcc.modules["Quartz"].CGRequestListenEventAccess() is False
    assert len(tcc.ignored_requests(INPUT)) == 1
    assert tcc.dialogs_shown() == []


def test_a_tap_created_after_the_grant_receives_keys() -> None:
    tcc = FakeTCC()
    tcc.modules["Quartz"].CGRequestListenEventAccess()
    keys: list[int] = []
    tap = FakeEventTap(tcc)

    assert tap.create(keys.append) is True
    assert tap.deliver_key(7) is True

    assert keys == [7]
    assert tcc.implicit_prompts() == []


def test_an_accessibility_grant_also_lets_a_listen_tap_work() -> None:
    tcc = FakeTCC(granted=[AX])
    tap = FakeEventTap(tcc)

    assert tap.create() is True

    assert tcc.state(INPUT) is TccState.NOT_DETERMINED  # untouched
    assert tcc.implicit_prompts() == []


def test_an_active_tap_needs_accessibility_not_input_monitoring() -> None:
    tcc = FakeTCC(granted=[INPUT])

    assert FakeEventTap(tcc).create(listen_only=False) is False
    tcc.grant(AX)
    assert FakeEventTap(tcc).create(listen_only=False) is True


def test_a_denied_tap_can_be_created_and_receive_nothing() -> None:
    tcc = FakeTCC(silent_tap_when_denied=True)
    tcc.deny(INPUT)
    tap = FakeEventTap(tcc)

    assert tap.create() is True
    assert tap.live is True
    assert tap.receives_events is False
    assert tap.deliver_key() is False


def test_a_tap_dies_with_the_process() -> None:
    tcc = FakeTCC(granted=[INPUT])
    tap = FakeEventTap(tcc)
    tap.create()

    tcc.relaunch()

    assert tap.live is False
    assert tap.deliver_key() is False


# --- Automation ---------------------------------------------------------------


def test_automation_reports_the_apple_event_status_values() -> None:
    tcc = FakeTCC(running_players=[MUSIC])

    assert tcc.automation_probe(SPOTIFY, False) == AE_PROC_NOT_FOUND
    assert tcc.automation_probe(MUSIC, False) == AE_EVENT_WOULD_REQUIRE_USER_CONSENT
    tcc.grant(AUTOMATION, MUSIC)
    assert tcc.automation_probe(MUSIC, False) == AE_NO_ERR
    tcc.deny(AUTOMATION, MUSIC)
    assert tcc.automation_probe(MUSIC, False) == AE_EVENT_NOT_PERMITTED
    assert tcc.requests() == []  # asking=False never raises the dialog


def test_automation_asks_only_for_a_running_target_and_only_once() -> None:
    tcc = FakeTCC(running_players=[MUSIC], default_policy=DialogPolicy.DENY)

    assert tcc.automation_probe(SPOTIFY, True) == AE_PROC_NOT_FOUND
    assert tcc.automation_probe(MUSIC, True) == AE_EVENT_NOT_PERMITTED
    assert tcc.automation_probe(MUSIC, True) == AE_EVENT_NOT_PERMITTED

    outcomes = [(call.target, call.outcome) for call in tcc.requests(AUTOMATION)]
    assert outcomes == [
        (SPOTIFY, CallOutcome.TARGET_NOT_RUNNING),
        (MUSIC, CallOutcome.DIALOG_SHOWN),
        (MUSIC, CallOutcome.IGNORED_REQUEST),
    ]


def test_automation_decisions_are_per_target() -> None:
    tcc = FakeTCC(running_players=[MUSIC, SPOTIFY])
    tcc.script(AUTOMATION, DialogPolicy.ALLOW, target=MUSIC)
    tcc.script(AUTOMATION, DialogPolicy.DENY, target=SPOTIFY)

    assert tcc.automation_probe(MUSIC, True) == AE_NO_ERR
    assert tcc.automation_probe(SPOTIFY, True) == AE_EVENT_NOT_PERMITTED
    assert tcc.state(AUTOMATION, MUSIC) is TccState.GRANTED
    assert tcc.state(AUTOMATION, SPOTIFY) is TccState.DENIED


def test_an_unanswered_automation_dialog_does_not_hang_the_caller() -> None:
    tcc = FakeTCC(running_players=[MUSIC], default_policy=DialogPolicy.NEVER_ANSWERED)

    assert tcc.automation_probe(MUSIC, True) == AE_EVENT_WOULD_REQUIRE_USER_CONSENT

    assert tcc.dialog_open(AUTOMATION, MUSIC) is True


def test_automation_state_changes_need_a_target() -> None:
    with pytest.raises(ValueError):
        FakeTCC().grant(AUTOMATION)


# --- the audio companion ------------------------------------------------------


def test_opening_the_microphone_while_not_determined_prompts_by_itself() -> None:
    tcc = FakeTCC()
    audio = FakeAudioInput(tcc, caller="wake_loop")

    stream = audio(device=0, channels=1, blocksize=4)
    stream.start()

    (prompt,) = tcc.implicit_prompts(MIC)
    assert prompt.caller == "wake_loop"
    assert tcc.requests() == []
    assert tcc.state(MIC) is TccState.GRANTED  # the default simulated user allows


def test_a_denied_microphone_delivers_digital_silence_not_an_error() -> None:
    tcc = FakeTCC()
    tcc.deny(MIC)
    audio = FakeAudioInput(tcc)
    received: list[bytes] = []
    stream = audio(
        channels=1, blocksize=4, callback=lambda data, _f, _t, _s: received.append(bytes(data))
    )

    stream.start()
    stream.pump(2)

    assert received == [b"\x00" * 8] * 2
    assert (stream.silent_blocks, stream.audible_blocks) == (2, 0)
    assert tcc.implicit_prompts() == []


def test_a_granted_microphone_delivers_audio() -> None:
    tcc = FakeTCC(granted=[MIC])
    received: list[bytes] = []
    stream = FakeAudioInput(tcc)(
        channels=1, blocksize=4, callback=lambda data, _f, _t, _s: received.append(bytes(data))
    )

    stream.start()
    stream.pump()

    assert received and any(received[0])
    assert stream.audible_blocks == 1


def test_the_microphone_stays_silent_until_the_open_dialog_is_answered() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    stream = FakeAudioInput(tcc)(channels=1, blocksize=2)
    stream.start()

    stream.pump()
    tcc.answer(MIC)
    stream.pump()

    assert (stream.silent_blocks, stream.audible_blocks) == (1, 1)


def test_without_tcc_a_stream_opens_without_a_question() -> None:
    audio = FakeAudioInput(None)
    stream = audio(channels=1, blocksize=2)

    stream.start()

    assert audio.starts == [stream]
    assert stream.active is True
    stream.stop()
    stream.close()
    assert stream.active is False


def test_a_stream_that_was_never_started_opened_no_device() -> None:
    tcc = FakeTCC()
    audio = FakeAudioInput(tcc)

    audio(channels=1)  # constructed, not started: consumers assert audio.starts == []

    assert audio.starts == []
    tcc.assert_no_prompts()


# --- purpose strings ----------------------------------------------------------


def test_a_missing_microphone_purpose_string_aborts_the_process() -> None:
    tcc = FakeTCC(usage_strings=())

    answers = _request_mic(tcc)

    assert answers == []
    assert tcc.state(MIC) is TccState.NOT_DETERMINED
    assert tcc.dialogs_shown() == []
    (abort,) = tcc.aborts()
    assert abort.outcome is CallOutcome.PROCESS_ABORT


def test_an_abort_can_be_raised_as_a_base_exception() -> None:
    tcc = FakeTCC(usage_strings=(), abort_raises=True)

    with pytest.raises(TccProcessAbort):
        FakeAudioInput(tcc)(channels=1).start()

    # An ``except Exception`` guard cannot hide it, as with a real SIGABRT.
    assert not issubclass(TccProcessAbort, Exception)


def test_a_terminal_started_process_is_covered_by_the_terminals_strings() -> None:
    tcc = FakeTCC(bundle_id=None, bundle_path="/usr/bin/python3", usage_strings=())

    _request_mic(tcc)

    assert tcc.aborts() == []
    assert tcc.grantee == TERMINAL_BUNDLE_ID
    assert tcc.requests(MIC)[0].grantee == TERMINAL_BUNDLE_ID


def test_the_bundle_reports_its_purpose_strings() -> None:
    bundle = FakeTCC().modules["Foundation"].NSBundle.mainBundle()
    bare = FakeTCC(usage_strings=()).modules["Foundation"].NSBundle.mainBundle()

    assert bundle.objectForInfoDictionaryKey_("NSMicrophoneUsageDescription")
    assert bundle.objectForInfoDictionaryKey_("NSAppleEventsUsageDescription")
    assert "NSMicrophoneUsageDescription" in bundle.infoDictionary()
    assert bundle.objectForInfoDictionaryKey_("NSCameraUsageDescription") is None
    assert bare.objectForInfoDictionaryKey_("NSMicrophoneUsageDescription") is None


# --- the call log -------------------------------------------------------------


def test_the_log_is_ordered_and_records_the_calling_thread() -> None:
    tcc = FakeTCC()
    tcc.modules["Quartz"].CGPreflightScreenCaptureAccess()

    worker = threading.Thread(target=_request_mic, args=(tcc,), name="worker-1")
    worker.start()
    worker.join()

    assert [call.seq for call in tcc.calls] == [0, 1]
    assert tcc.kinds() == ["probe", "request"]
    assert [call.thread for call in tcc.calls][1] == "worker-1"
    assert tcc.calls[0].thread == threading.current_thread().name


def test_the_log_filters_accept_a_permission_id_or_a_plain_string() -> None:
    tcc = FakeTCC()
    _request_mic(tcc)

    assert tcc.requests("microphone") == tcc.requests(PermissionId.MICROPHONE) == tcc.requests(MIC)
    assert tcc.requests("screen_recording") == []
    assert tcc.implicit_prompts("microphone") == []


def test_log_filters_marks_and_clear() -> None:
    tcc = FakeTCC()
    _mic_status(tcc)
    mark = tcc.mark()
    _request_mic(tcc)
    tcc.modules["Quartz"].CGRequestScreenCaptureAccess()

    assert len(tcc.probes()) == 1
    assert len(tcc.requests()) == 2
    assert len(tcc.requests(MIC)) == 1
    assert [call.service for call in tcc.calls_since(mark)] == [MIC, SCREEN]
    assert tcc.calls_of("request", "screen_recording") == tcc.requests(SCREEN)
    assert tcc.calls_of(CallKind.PROBE, target="") == tcc.probes()

    tcc.clear_log()

    assert tcc.calls == ()
    tcc.assert_silent()


def test_assert_no_prompts_names_the_offending_call() -> None:
    tcc = FakeTCC()
    _mic_status(tcc)
    tcc.assert_no_prompts()  # a probe is not a prompt
    mark = tcc.mark()
    _request_mic(tcc)

    with pytest.raises(AssertionError, match="requestAccessForMediaType"):
        tcc.assert_no_prompts()
    tcc.assert_no_prompts(since=tcc.mark())  # nothing after the last call
    assert mark == 1


def test_assert_silent_fails_when_a_framework_was_loaded() -> None:
    tcc = FakeTCC()
    tcc.module_loader("Quartz")

    with pytest.raises(AssertionError, match="Quartz"):
        tcc.assert_silent()


def test_a_missing_framework_cannot_be_imported() -> None:
    tcc = FakeTCC(missing_frameworks=["Quartz"])
    tcc.drop_framework("AppKit")

    for name in ("Quartz", "AppKit", "Nonexistent"):
        with pytest.raises(ModuleNotFoundError):
            tcc.module_loader(name)
    assert tcc.module_loader("Foundation") is tcc.modules["Foundation"]


# --- reset, relaunch, tccutil -------------------------------------------------


def test_relaunch_closes_dialogs_and_counts_launches() -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    _request_mic(tcc)
    assert tcc.launch_count == 1

    tcc.relaunch()

    assert tcc.dialog_open(MIC) is False
    assert tcc.state(MIC) is TccState.NOT_DETERMINED
    assert tcc.launch_count == 2


def test_tccutil_resets_only_the_named_bundles_row() -> None:
    tcc = FakeTCC(granted=[MIC, AX])

    other = tcc.run_tccutil(["/usr/bin/tccutil", "reset", "Microphone", "com.example.other"])
    own = tcc.run_tccutil(["/usr/bin/tccutil", "reset", "Microphone", tcc.grantee])
    bad = tcc.run_tccutil(["/usr/bin/tccutil", "reset", "NoSuchService", tcc.grantee])
    ax = tcc.run_tccutil(["/usr/bin/tccutil", "reset", "Accessibility", tcc.grantee])

    assert (other.returncode, own.returncode, bad.returncode, ax.returncode) == (0, 0, 1, 0)
    assert tcc.state(MIC) is TccState.NOT_DETERMINED
    assert tcc.state(AX) is TccState.DENIED  # no not_determined: untrusted again
    assert len(tcc.tccutil_calls) == 4


def test_reset_without_a_target_forgets_every_automation_target() -> None:
    tcc = FakeTCC(granted=[AUTOMATION])
    assert all(
        tcc.state(AUTOMATION, bundle) is TccState.GRANTED for _n, bundle in AUTOMATION_TARGETS
    )

    tcc.reset(AUTOMATION)

    assert all(
        tcc.state(AUTOMATION, bundle) is TccState.NOT_DETERMINED
        for _n, bundle in AUTOMATION_TARGETS
    )


def test_the_upgrader_whose_grants_are_all_in_place() -> None:
    tcc = FakeTCC.all_granted()

    assert all(
        tcc.state(service, MUSIC if service is AUTOMATION else "") is TccState.GRANTED
        for service in TccService
    )
    assert tcc.modules["Quartz"].CGPreflightScreenCaptureAccess() is True


# --- players and identity -----------------------------------------------------


def test_a_closed_player_can_be_launched_hidden_and_closed_again() -> None:
    tcc = FakeTCC(installed_players=[MUSIC])
    appkit = tcc.modules["AppKit"]
    workspace = appkit.NSWorkspace.sharedWorkspace()
    config = appkit.NSWorkspaceOpenConfiguration.configuration()
    config.setActivates_(False)
    config.setHides_(True)
    url = workspace.URLForApplicationWithBundleIdentifier_(MUSIC)
    assert workspace.URLForApplicationWithBundleIdentifier_(SPOTIFY) is None
    assert appkit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(MUSIC) == []

    workspace.openApplicationAtURL_configuration_completionHandler_(url, config, lambda *_a: None)

    (player,) = appkit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(MUSIC)
    assert tcc.launches == [(MUSIC, False, True)]
    player.terminate()
    assert player.isTerminated() is True
    assert tcc.player_running(MUSIC) is False


def test_the_frontmost_application_follows_the_foreground_and_headless_switches() -> None:
    tcc = FakeTCC()
    appkit = tcc.modules["AppKit"]
    workspace = appkit.NSWorkspace.sharedWorkspace()

    assert workspace.frontmostApplication().isActive() is True
    tcc.foreground = False
    assert workspace.frontmostApplication().isActive() is False
    assert appkit.NSRunningApplication.currentApplication().isActive() is False
    tcc.headless = True
    assert workspace.frontmostApplication() is None


def test_the_dmg_build_is_an_installed_identity_too() -> None:
    tcc = FakeTCC(bundle_id=DMG_BUNDLE_ID)

    assert tcc.launched_as_bundle is True
    assert tcc.grantee == DMG_BUNDLE_ID


# --- the real port on top of the fake ----------------------------------------


def test_the_real_port_reads_the_fake_without_ever_prompting() -> None:
    port, tcc = make_darwin_port()

    states = {permission_id.value: port.state(permission_id) for permission_id in PermissionId}

    assert states["microphone"] is PermissionState.NOT_DETERMINED
    assert states["screen_recording"] is PermissionState.NOT_GRANTED
    assert states["accessibility"] is PermissionState.NOT_GRANTED
    assert states["input_monitoring"] is PermissionState.NOT_DETERMINED
    assert states["automation"] is PermissionState.NOT_REQUIRED  # no scriptable player installed
    assert port._app_identity()[0].stable is True
    assert tcc.probes() and tcc.kinds().count("probe") == len(tcc.calls)
    tcc.assert_no_prompts()


def test_the_real_port_requests_through_the_fake_and_macos_never_asks_twice() -> None:
    port, tcc = make_darwin_port(default_policy=DialogPolicy.DENY)

    first = port.request_native(PermissionId.MICROPHONE)
    second = port.request_native(PermissionId.MICROPHONE)

    assert first == "dialog_shown"
    assert second == "no_dialog"  # the decision is on file: no second dialog
    assert len(tcc.dialogs_shown(MIC)) == 1
    assert len(tcc.ignored_requests(MIC)) == 1
    assert port.state(PermissionId.MICROPHONE) is PermissionState.DENIED


def test_the_real_port_reads_a_state_change_on_the_very_next_call() -> None:
    port, tcc = make_darwin_port(granted=[MIC])
    assert port.state(PermissionId.MICROPHONE) is PermissionState.GRANTED

    tcc.deny(MIC)

    assert port.state(PermissionId.MICROPHONE) is PermissionState.DENIED


def test_the_real_port_sees_a_screen_grant_through_the_live_oracle() -> None:
    port, tcc = make_darwin_port()
    tcc.grant(SCREEN)

    assert tcc.modules["Quartz"].CGPreflightScreenCaptureAccess() is False
    assert port.state(PermissionId.SCREEN_RECORDING) is PermissionState.NOT_GRANTED
    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.GRANTED


def test_the_real_port_needs_a_relaunch_in_the_pessimistic_screen_world() -> None:
    port, tcc = make_darwin_port(screen_grant_needs_relaunch=True)
    tcc.grant(SCREEN)

    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.NOT_GRANTED
    tcc.relaunch()
    assert port.state(PermissionId.SCREEN_RECORDING, deep=True) is PermissionState.GRANTED


@pytest.mark.parametrize("platform_name", ["win32", "linux"])
def test_a_non_darwin_port_never_touches_the_fake(platform_name: str) -> None:
    port, tcc = make_non_darwin_port(platform_name)  # type: ignore[arg-type]

    port.state(PermissionId.MICROPHONE)
    port.request_native(PermissionId.MICROPHONE)
    port.open_pane(PermissionId.MICROPHONE)
    port.reset_row(PermissionId.MICROPHONE)

    tcc.assert_silent()
    assert port.platform == platform_name


def test_install_port_replaces_the_process_wide_port(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.platform.permissions as permissions

    before = permissions.get_system_permission_port()
    port, _tcc = make_darwin_port()

    assert install_port(monkeypatch, port) is port
    assert permissions.get_system_permission_port() is port
    monkeypatch.undo()
    assert permissions.get_system_permission_port() is before
