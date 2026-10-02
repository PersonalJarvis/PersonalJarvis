"""Regression tests for the three-lens review of the permission service.

Every test pins one defect the review reproduced: a hung Automation probe wedging
the watcher, the Automation request blocking ``ensure``, a Screen Recording grant
that flapped back to "not granted", the window-title oracle running far too often,
an explicit click held back by the re-ask cooldown, a ``restart_hint`` nobody could
produce, duplicate episodes, a dead feature in a session without a desktop and a
few smaller lifecycle gaps. The REAL service runs on a REAL ``SystemPermissionPort``
that sits on ``FakeTCC``; nothing here ran on a real Mac, every macOS behaviour is a
MODEL (see ``tests/fakes/fake_tcc.py``). Where a test needs a probe or a consent
runner that blocks, it replaces that one seam of the port with a hand-written stub.
"""

# ruff: noqa: F811 - the harness fixtures (``make_env``, ``loop_thread``) are imported by name

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from jarvis.core.events import PERMISSION_NEEDED_REASONS
from jarvis.platform import permission_service as service_module
from jarvis.platform.permission_service import (
    PermissionOutcome,
    PermissionService,
    user_detail_for,
)
from jarvis.platform.permissions import AUTOMATION_TARGETS, PermissionId, PermissionState
from tests.fakes.fake_tcc import CallKind, DialogPolicy, FakeTCC, TccService, install_port
from tests.unit.platform.test_permission_service import (  # noqa: F401 - fixtures
    Env,
    LoopThread,
    RecordingBus,
    loop_thread,
    make_env,
)

_MUSIC = "com.apple.Music"
_MIC = PermissionId.MICROPHONE
_AX = PermissionId.ACCESSIBILITY
_SR = PermissionId.SCREEN_RECORDING
_IM = PermissionId.INPUT_MONITORING
_AUTOMATION = PermissionId.AUTOMATION
_NEVER = 3600.0
_SETTINGS_BUNDLE_ID = "com.apple.systempreferences"


def _wait_for(condition: Callable[[], bool], *, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class BlockedConsentRunner:
    """The ``osascript`` consent child, blocked on an open dialog until released."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.scripts: list[str] = []

    def __call__(self, script: str) -> Any:
        self.scripts.append(script)
        self.entered.set()
        self.release.wait(10)
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=120.0)


class HungProbe:
    """A stand-in for ``AEDeterminePermissionToAutomateTarget`` that never returns."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, bundle_id: str, ask: bool) -> int | None:
        self.calls += 1
        self.entered.set()
        self.release.wait(10)
        return -1744  # "would require consent": what a hung call would say if it ever answered


def _hang_automation_probe(service: PermissionService) -> HungProbe:
    probe = HungProbe()
    service._port()._automation_probe = probe
    return probe


def _oracle_calls(tcc: FakeTCC) -> int:
    return len([c for c in tcc.probes("screen_recording") if "window title" in c.api])


# ----------------------------------------------------------------------
# A hung Automation probe never wedges anything else
# ----------------------------------------------------------------------


def test_a_hung_automation_probe_cannot_wedge_the_episode_watcher(
    monkeypatch: pytest.MonkeyPatch, loop_thread: LoopThread
) -> None:
    tcc = FakeTCC(
        default_policy=DialogPolicy.NEVER_ANSWERED,
        installed_players=[_MUSIC],
        running_players=[_MUSIC],
    )
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    bus = RecordingBus()
    service = PermissionService(
        watch_interval_s=0.05,
        negative_ttl_s=0.0,
        automation_timeout_s=5.0,
        automation_refresh_wait_s=0.02,
        ask_runner=lambda work: work(),
    )
    probe: HungProbe | None = None
    try:
        service.attach_bus(bus, loop_thread.loop)
        service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC)
        service.ensure(_MIC, feature="voice")
        probe = _hang_automation_probe(service)
        tcc.grant("microphone")

        assert _wait_for(
            lambda: any(r.permissions == ("microphone",) for r in bus.resolved()), seconds=3
        ), "the microphone grant was never published: a stuck player starved the watcher"
        assert probe.entered.wait(5)  # the Apple Event probe really was hung meanwhile
        assert [e.permissions for e in service.outstanding()] == [("automation",)]
    finally:
        if probe is not None:
            probe.release.set()
        service._shutdown()


def test_a_hung_automation_read_times_out_and_the_player_is_quarantined(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(
        installed_players=[_MUSIC],
        running_players=[_MUSIC],
        automation_timeout_s=0.2,
        negative_ttl_s=0.0,
    )
    probe = _hang_automation_probe(env.service)
    try:
        started = time.monotonic()
        assert env.service.check(_AUTOMATION, target=_MUSIC) is PermissionState.UNAVAILABLE
        assert time.monotonic() - started < 3  # the hard timeout, not the hang

        started = time.monotonic()
        for _ in range(20):  # quarantined: no thread, no waiting
            assert env.service.check(_AUTOMATION, target=_MUSIC) is PermissionState.UNAVAILABLE
        assert time.monotonic() - started < 0.5
        assert probe.calls == 1  # one call in flight per target, never a pile of threads

        env.clock.advance(601)  # the quarantine is over, the old call is still hung
        assert env.service.check(_AUTOMATION, target=_MUSIC) is PermissionState.UNAVAILABLE
        assert probe.calls == 1
    finally:
        probe.release.set()


def test_an_automation_read_without_a_player_asks_nobody(make_env: Callable[..., Env]) -> None:
    env = make_env(installed_players=[_MUSIC], running_players=[_MUSIC])

    assert env.service.check(_AUTOMATION) is PermissionState.UNAVAILABLE
    assert env.service.check(_AUTOMATION, target="com.evil.app") is PermissionState.UNAVAILABLE
    # The aggregate would probe every running player and rewrite the consent record.
    assert env.tcc.probes("automation") == []


def test_an_automation_read_on_the_event_loop_never_waits_for_the_probe(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(installed_players=[_MUSIC], running_players=[_MUSIC], automation_timeout_s=5.0)
    probe = _hang_automation_probe(env.service)

    async def on_the_loop() -> tuple[PermissionState, float]:
        started = time.monotonic()
        return env.service.check(_AUTOMATION, target=_MUSIC), time.monotonic() - started

    try:
        state, elapsed = asyncio.run(on_the_loop())
        assert state is PermissionState.NOT_DETERMINED  # "unknown": the probe is still running
        assert elapsed < 1.0
    finally:
        probe.release.set()


async def test_ensure_async_never_probes_automation_on_the_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = FakeTCC(
        default_policy=DialogPolicy.NEVER_ANSWERED,
        installed_players=[_MUSIC],
        running_players=[_MUSIC],
    )
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    service = PermissionService(poll_s=0.01, watch_interval_s=_NEVER, negative_ttl_s=0.0)
    runner = BlockedConsentRunner()
    service._port()._automation_consent_runner = runner  # the dialog stays open
    try:
        result = await service.ensure_async(
            _AUTOMATION, feature="audio_ducking", target=_MUSIC, wait_s=0.3
        )
        assert result.outcome is PermissionOutcome.PENDING and result.asked
        loop_name = threading.current_thread().name
        automation_calls = tcc.calls_of(service="automation")
        assert automation_calls, "the test never reached the OS"
        assert all(call.thread != loop_name for call in automation_calls), tcc.format_log()
    finally:
        runner.release.set()
        service._shutdown()


# ----------------------------------------------------------------------
# The Automation request runs off the calling thread
# ----------------------------------------------------------------------


def test_the_automation_request_never_blocks_ensure_and_a_slow_user_is_not_a_missing_player(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(installed_players=[_MUSIC], running_players=[_MUSIC], inline_ask=False)
    runner = BlockedConsentRunner()
    env.service._port()._automation_consent_runner = runner
    try:
        started = time.monotonic()
        result = env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC, wait_s=0)
        assert time.monotonic() - started < 2.0  # the runner is blocked for up to 120 s
        assert result.outcome is PermissionOutcome.PENDING and result.asked
        assert runner.entered.wait(5)

        # While the dialog is open the episode says so, with a full classification
        # (never an episode with an empty reason and phase).
        [episode] = env.service.outstanding()
        assert (episode.reason, episode.phase) == ("not_determined", "os_dialog")
        again = env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC)
        assert again.outcome is PermissionOutcome.PENDING and not again.asked
        assert len(runner.scripts) == 1

        runner.release.set()  # the runner is killed after its timeout, nobody answered
        assert _wait_for(lambda: env.service.outstanding()[0].phase == "blocked")
        [episode] = env.service.outstanding()
        # Not "the player is not running": the user may ask again.
        assert (episode.reason, episode.can_prompt) == ("not_determined", True)
        env.clock.advance(1)
        retry = env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC)
        assert retry.asked and _wait_for(lambda: len(runner.scripts) == 2)
    finally:
        runner.release.set()


def test_a_closed_player_is_unavailable_but_may_be_asked_again_once_it_runs(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(installed_players=[_MUSIC], inline_ask=False)

    first = env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC, wait_s=2)

    assert first.outcome is PermissionOutcome.UNAVAILABLE  # the guarded script found no player
    env.tcc.launch_player(_MUSIC)
    env.clock.advance(1)
    second = env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC, wait_s=2)
    assert second.outcome is PermissionOutcome.GRANTED and second.asked  # the fake user allows


def test_an_episode_is_invisible_until_its_asks_are_finished(
    make_env: Callable[..., Env],
) -> None:
    seen: dict[str, Any] = {}
    runner = BlockedConsentRunner()

    def observing_runner(work: Callable[[], None]) -> None:
        # Runs inside ensure(), after the episode exists but before it is classified.
        env.flush()
        seen["outstanding"] = env.service.outstanding()
        seen["events"] = env.bus.needed()
        env.service.refresh_episodes()
        env.flush()
        seen["events_after_refresh"] = env.bus.needed()
        threading.Thread(target=work, daemon=True).start()

    env = make_env(
        installed_players=[_MUSIC],
        running_players=[_MUSIC],
        ask_runner=observing_runner,
        inline_ask=False,
    )
    env.service._port()._automation_consent_runner = runner
    try:
        env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC)
        env.flush()

        assert seen["outstanding"] == [] and seen["events"] == []
        assert seen["events_after_refresh"] == []  # no "blocked, can prompt" card mid-request
        [event] = env.bus.needed()
        assert (event.phase, event.reason) == ("os_dialog", "not_determined")
        assert event.can_prompt is False and event.origin == "user"
    finally:
        runner.release.set()


# ----------------------------------------------------------------------
# Screen Recording: a proven grant stays proven, the oracle is rate limited
# ----------------------------------------------------------------------


def test_a_screen_recording_grant_proven_by_the_oracle_does_not_flap_back(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    assert env.service.ensure(_SR, feature="computer_use").outcome is PermissionOutcome.PENDING
    env.tcc.grant("screen_recording")  # the preflight stays frozen negative until a relaunch
    env.clock.advance(0.5)  # past the quarter second a negative answer is cached

    assert env.service.ensure(_SR, feature="computer_use").granted  # the oracle proved it
    for seconds in (0.1, 1.5, 10, 60):
        env.clock.advance(seconds)
        assert env.service.check(_SR) is PermissionState.GRANTED, seconds

    env.service.invalidate(_SR)  # a capture error: the proof is dropped
    assert env.service.check(_SR) is PermissionState.NOT_GRANTED
    assert env.service.ensure(_SR, feature="computer_use").granted  # and re-proven by a gesture


def test_a_reset_drops_the_screen_recording_proof(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_SR, feature="computer_use")
    env.tcc.grant("screen_recording")
    env.clock.advance(0.5)
    assert env.service.ensure(_SR, feature="computer_use").granted

    env.service.note_reset(_SR)

    assert env.service.check(_SR) is PermissionState.NOT_GRANTED


def test_the_oracle_runs_once_per_gesture_and_only_rarely_for_an_open_episode(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    env.service.ensure(_SR, feature="computer_use")
    # One gesture: one look on entry, one right after the request it made.
    assert _oracle_calls(env.tcc) == 2

    for _ in range(4):  # the watcher passes inside the cadence read the preflight only
        env.clock.advance(2)
        env.service.refresh_episodes()
    assert _oracle_calls(env.tcc) == 2
    env.clock.advance(10)
    env.service.refresh_episodes()
    assert _oracle_calls(env.tcc) == 3  # one every ten seconds at most
    env.service.note_app_activated()  # the user came back: look at once
    env.clock.advance(0.5)  # past the quarter second a negative answer is cached
    env.service.refresh_episodes()
    assert _oracle_calls(env.tcc) == 4


def test_a_background_caller_never_runs_the_oracle(make_env: Callable[..., Env]) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    for _ in range(5):
        env.clock.advance(20)
        env.service.ensure(_SR, feature="computer_use", interactive=False)
        env.service.refresh_episodes()

    assert _oracle_calls(env.tcc) == 0
    assert env.tcc.requests() == []


async def test_ensure_on_the_event_loop_never_runs_the_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    service = PermissionService(watch_interval_s=_NEVER)
    try:
        result = service.ensure(_SR, feature="computer_use", wait_s=0)  # a sync call on the loop
        assert result.outcome is PermissionOutcome.PENDING
        assert _oracle_calls(tcc) == 0
    finally:
        service._shutdown()


# ----------------------------------------------------------------------
# An explicit click is never held back by the re-ask cooldown
# ----------------------------------------------------------------------


def test_an_explicit_click_skips_the_cooldown_of_a_prompt_once_permission(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    assert env.service.ensure(_AX, feature="computer_use").asked
    env.clock.advance(60)

    automatic = env.service.ensure(_AX, feature="window_control")
    click = env.service.ensure(_AX, feature="window_control", force_ask=True)

    assert not automatic.asked  # the cooldown still holds for automatic asks
    assert click.asked
    assert len(env.tcc.requests("accessibility")) == 2
    # The same card clicked again inside its own episode is not a retry loop either.
    env.clock.advance(6)
    assert not env.service.ensure(_AX, feature="window_control").asked
    assert env.service.ensure(_AX, feature="window_control", force_ask=True).asked
    assert len(env.tcc.requests("accessibility")) == 3


def test_an_explicit_click_asks_for_screen_recording_again_in_the_same_process(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    assert env.service.ensure(_SR, feature="computer_use").asked
    env.clock.advance(60)

    assert not env.service.ensure(_SR, feature="screen_context").asked
    assert env.service.ensure(_SR, feature="screen_context", force_ask=True).asked


def test_an_explicit_click_never_asks_about_a_denial_or_a_dialog_that_is_open(
    make_env: Callable[..., Env],
) -> None:
    env = make_env()
    env.tcc.deny("microphone")
    denied = env.service.ensure(_MIC, feature="voice", force_ask=True)
    assert denied.outcome is PermissionOutcome.DENIED and not denied.asked
    assert env.tcc.requests() == []

    other = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    assert other.service.ensure(_MIC, feature="voice").asked
    again = other.service.ensure(_MIC, feature="dictation", force_ask=True)
    assert not again.asked  # a dialog class request is still in flight


# ----------------------------------------------------------------------
# restart_hint has a producer now
# ----------------------------------------------------------------------


def test_a_real_failed_use_of_a_granted_screen_recording_raises_the_restart_hint(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_SR, feature="computer_use")
    env.tcc.grant("screen_recording")  # granted, but the capture still fails

    result = env.service.report_failed_use(_SR, feature="computer_use")
    env.flush()

    assert result.reason == "restart_hint" and not result.granted
    assert result.outcome is PermissionOutcome.NEEDS_SETTINGS
    assert "quit and reopen" in result.user_detail
    assert result.agent_detail.startswith("[permission_needed:screen_recording] ")
    hint = [e for e in env.bus.needed() if e.reason == "restart_hint"]
    assert [(e.phase, e.origin) for e in hint] == [("blocked", "user")]
    [episode] = env.service.outstanding()
    assert episode.reason == "restart_hint" and episode.permissions == ("screen_recording",)
    # A hint is not a refusal: the state still reads granted and ensure still says so.
    assert env.service.check(_SR) is PermissionState.GRANTED
    assert env.service.ensure(_SR, feature="computer_use").granted


def test_the_restart_hint_after_a_return_from_settings_in_the_frozen_preflight_world(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED, screen_grant_needs_relaunch=True)
    env.service.ensure(_SR, feature="computer_use")
    env.tcc.grant("screen_recording")  # on in Settings, but the oracle cannot see it yet

    assert env.service.report_failed_use(_SR, feature="computer_use").reason != "restart_hint"

    env.service.note_app_activated()  # the user came back from Settings
    result = env.service.report_failed_use(_SR, feature="computer_use")

    assert result.reason == "restart_hint"
    assert env.service.check(_SR) is PermissionState.NOT_GRANTED  # still frozen negative


def test_the_restart_hint_is_only_for_screen_recording_and_input_monitoring(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(granted=[TccService.ACCESSIBILITY, TccService.INPUT_MONITORING])

    ax = env.service.report_failed_use(_AX, feature="computer_use")
    im = env.service.report_failed_use(_IM, feature="global_shortcuts")

    assert ax.reason == "" and ax.granted  # an ordinary ensure
    assert im.reason == "restart_hint"
    assert env.service.report_failed_use("event_posting", feature="computer_use").granted


def test_report_failed_use_off_macos_is_not_required(make_env: Callable[..., Env]) -> None:
    env = make_env(platform="linux")

    assert env.service.report_failed_use(_SR, feature="computer_use").granted
    env.tcc.assert_silent()


# ----------------------------------------------------------------------
# One record per feature and permission
# ----------------------------------------------------------------------


def test_granting_one_member_of_a_pair_never_opens_a_second_episode(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure_all([_SR, _AX], feature="computer_use")
    env.tcc.grant("screen_recording")
    env.clock.advance(10)

    env.service.ensure_all([_SR, _AX], feature="computer_use")
    env.flush()

    [episode] = env.service.outstanding()
    assert episode.permissions == ("accessibility",)  # only what is still missing
    assert env.bus.needed()[-1].permissions == ("accessibility",)
    env.tcc.grant("accessibility")
    env.settle()
    assert [r.granted for r in env.bus.resolved()] == [True]  # one resolve, not two
    assert env.service.outstanding() == []


def test_two_episodes_for_one_permission_call_the_listener_once_per_pass(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(attach=False, default_policy=DialogPolicy.NEVER_ANSWERED)
    fired: list[int] = []
    env.service.add_listener(_AX, lambda: fired.append(1))
    env.service.ensure(_AX, feature="computer_use")
    env.service.ensure(_AX, feature="window_control")
    assert len(env.service.outstanding()) == 2
    env.tcc.grant("accessibility")

    env.settle()

    assert fired == [1]


# ----------------------------------------------------------------------
# No desktop session, nothing to show
# ----------------------------------------------------------------------


@pytest.mark.parametrize("permission", [_MIC, _SR, _AX, _IM])
def test_without_a_desktop_session_nothing_is_requested(
    make_env: Callable[..., Env], permission: PermissionId
) -> None:
    env = make_env(headless=True)

    result = env.service.ensure(permission, feature="voice", wait_s=0)

    assert result.outcome is PermissionOutcome.UNAVAILABLE and not result.asked
    assert env.tcc.requests() == [] and env.tcc.implicit_prompts() == []
    [episode] = env.service.outstanding()
    assert episode.reason == "unavailable"  # a card with a reason, never a silent dead feature


def test_a_dialog_nobody_answers_turns_blocked_instead_of_asking_forever(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_MIC, feature="wake_word")
    [episode] = env.service.outstanding()
    assert (episode.phase, episode.reason) == ("os_dialog", "not_determined")

    env.clock.advance(131)
    env.service.refresh_episodes()
    env.flush()

    [episode] = env.service.outstanding()
    assert (episode.phase, episode.reason) == ("blocked", "needs_settings")


# ----------------------------------------------------------------------
# Opening a pane and resetting are light
# ----------------------------------------------------------------------


def test_opening_a_pane_probes_nothing_and_needs_no_installed_app(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(
        bundle_id=None,
        bundle_path=None,
        installed_players=[_MUSIC],
        running_players=[_MUSIC],
    )

    assert env.service.open_settings(_AX) is True
    assert env.service.open_settings(_AUTOMATION) is True

    assert len(env.tcc.workspace_opened_urls) == 2
    assert (
        env.tcc.probes() == [] and env.tcc.requests() == []
    )  # no snapshot, no oracle, no AE probe


def test_opening_a_pane_without_a_desktop_session_fails_without_a_native_call(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(headless=True)

    assert env.service.open_settings(_AX) is False
    assert env.tcc.workspace_opened_urls == []


def test_a_running_system_settings_is_closed_only_when_it_may_show_another_pane(
    make_env: Callable[..., Env],
) -> None:
    env = make_env()
    env.tcc.launch_player(_SETTINGS_BUNDLE_ID)

    assert env.service.open_settings(_AX) is True
    assert not env.tcc.player_running(_SETTINGS_BUNDLE_ID)  # unknown pane: closed first

    env.tcc.launch_player(_SETTINGS_BUNDLE_ID)  # opened on the Accessibility pane by us
    assert env.service.open_settings(_AX) is True
    assert env.tcc.player_running(_SETTINGS_BUNDLE_ID)  # the right pane is already there

    assert env.service.open_settings(_MIC) is True
    assert not env.tcc.player_running(_SETTINGS_BUNDLE_ID)  # another pane: closed first


def test_going_to_settings_keeps_the_open_episode_alive(make_env: Callable[..., Env]) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED, episode_ttl_s=600.0)
    env.service.ensure(_AX, feature="computer_use")
    env.clock.advance(590)
    assert env.service.open_settings(_AX) is True
    env.clock.advance(590)  # 20 minutes after the ask, 10 after the visit to Settings

    env.service.refresh_episodes()
    env.flush()

    assert env.bus.resolved() == []
    env.tcc.grant("accessibility")
    env.settle()
    assert [r.granted for r in env.bus.resolved()] == [True]  # a late grant is still heard


# ----------------------------------------------------------------------
# The smaller lifecycle gaps
# ----------------------------------------------------------------------


def test_a_failed_native_request_is_retried_after_a_while_and_at_once_for_a_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingPort:
        platform = "darwin"
        outside_installed_app = False

        def __init__(self) -> None:
            self.request_calls = 0

        def state(self, permission: Any, **_kwargs: Any) -> PermissionState:
            return PermissionState.NOT_GRANTED

        def request_native(self, permission: Any, **_kwargs: Any) -> str:
            self.request_calls += 1
            return "unavailable"

    port = FailingPort()
    monkeypatch.setattr(
        service_module._permissions_module, "get_system_permission_port", lambda: port
    )
    now = [1000.0]
    service = PermissionService(clock=lambda: now[0], watch_interval_s=_NEVER)
    try:
        assert service.ensure(_AX, feature="computer_use").outcome is PermissionOutcome.UNAVAILABLE
        assert not service.ensure(_AX, feature="computer_use").asked  # no hammering
        assert port.request_calls == 1
        now[0] += 31
        assert service.ensure(_AX, feature="computer_use").asked  # the transient failure heals
        assert port.request_calls == 2
        assert service.ensure(_AX, feature="computer_use", force_ask=True).asked
        assert port.request_calls == 3
    finally:
        service._shutdown()


def test_resetting_automation_without_a_player_forgets_every_player(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(installed_players=[_MUSIC], running_players=[_MUSIC], inline_ask=False)
    runner = BlockedConsentRunner()
    env.service._port()._automation_consent_runner = runner
    try:
        assert env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC).asked
        assert runner.entered.wait(5)
        env.clock.advance(6)
        assert not env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC).asked

        env.service.note_reset(_AUTOMATION)  # the route does not know the player

        assert env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC).asked
        assert _wait_for(lambda: len(runner.scripts) == 2)
    finally:
        runner.release.set()


def test_invalidate_never_raises_while_another_thread_fills_the_cache(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(granted=[TccService.MICROPHONE])
    stop = threading.Event()

    def filler() -> None:
        index = 0
        while not stop.is_set():
            env.service._cache[(_MIC, str(index % 5000))] = (PermissionState.GRANTED, 0.0, False)
            index += 1

    thread = threading.Thread(target=filler, daemon=True)
    thread.start()
    try:
        for _ in range(3000):
            env.service.invalidate(_MIC)
    finally:
        stop.set()
        thread.join(5)


def test_a_read_that_started_before_an_invalidation_never_stores_its_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder: dict[str, PermissionService] = {}

    class RacingPort:
        platform = "darwin"

        def state(self, permission: Any, **_kwargs: Any) -> PermissionState:
            holder["service"].invalidate(_MIC)  # a capture error lands mid-read
            return PermissionState.GRANTED

    monkeypatch.setattr(
        service_module._permissions_module, "get_system_permission_port", RacingPort
    )
    service = PermissionService(watch_interval_s=_NEVER)
    holder["service"] = service
    try:
        assert service.check(_MIC) is PermissionState.GRANTED
        assert service._cache == {}  # the stale answer did not get a fresh one second
    finally:
        service._shutdown()


def test_check_stays_lock_free_after_a_dialog_permission_was_decided(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_MIC, feature="voice")  # a request is "in flight"
    env.tcc.answer("microphone", DialogPolicy.ALLOW)
    env.clock.advance(1)  # past the quarter second a negative answer is cached
    answers: list[PermissionState] = []
    with env.service._lock:
        worker = threading.Thread(target=lambda: answers.append(env.service.check(_MIC)))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert answers == [PermissionState.GRANTED]
    assert (_MIC, "") not in env.service._last_native  # the decision ended the "in flight" stamp


def test_a_target_nobody_listed_is_never_echoed(make_env: Callable[..., Env]) -> None:
    env = make_env(installed_players=[_MUSIC], running_players=[_MUSIC])

    result = env.service.ensure(_AUTOMATION, feature="audio_ducking", target="com.evil.app")
    env.flush()

    assert result.outcome is PermissionOutcome.UNAVAILABLE and result.target == ""
    assert all("com.evil.app" not in repr(e) for e in env.bus.events)
    assert all(e.target == "" for e in env.service.outstanding())
    exploded = asyncio.run(
        env.service.ensure_async(_AUTOMATION, feature="audio_ducking", target="/Users/x/app")
    )
    assert exploded.target == ""


def test_an_unknown_feature_is_reported_without_one(
    make_env: Callable[..., Env], caplog: pytest.LogCaptureFixture
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    env.service.ensure(_MIC, feature="teleport")
    env.flush()

    assert [e.feature for e in env.bus.needed()] == [""]
    assert "unknown feature" in caplog.text


def test_the_outside_sentence_names_the_right_grantee(make_env: Callable[..., Env]) -> None:
    # One env after the other: each installs its own port as the process port.
    terminal = make_env(bundle_id=None, bundle_path=None)
    from_terminal = terminal.service.ensure(_MIC, feature="voice")
    mounted = make_env(bundle_path="/Volumes/Personal Jarvis/Personal Jarvis.app")
    from_dmg = mounted.service.ensure(_MIC, feature="voice")

    assert from_terminal.outside_installed_app and from_dmg.outside_installed_app
    assert "not running as an installed app" in from_terminal.user_detail
    assert "granted to the app that started it" in from_terminal.user_detail
    assert "outside its installed location" in from_dmg.user_detail
    assert "app that started it" not in from_dmg.user_detail


def test_the_sentences_state_facts_and_never_command_the_reader() -> None:
    imperatives = ("Choose", "Turn", "Confirm", "Allow", "Open", "Click", "Press", "Quit", "Come")
    for permission in PermissionId:
        for reason in PERMISSION_NEEDED_REASONS:
            for asking in (False, True):
                for outside in (False, True):
                    text = user_detail_for(permission, reason, asking=asking, outside_app=outside)
                    sentences = [part.strip() for part in text.split(". ") if part.strip()]
                    for sentence in sentences:
                        assert not sentence.startswith(imperatives), sentence


# ----------------------------------------------------------------------
# Requirements the mutation run found unpinned
# ----------------------------------------------------------------------


def test_input_monitoring_is_not_re_asked_inside_its_cooldown(make_env: Callable[..., Env]) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    assert env.service.ensure(_IM, feature="global_shortcuts").asked
    env.clock.advance(60)

    assert not env.service.ensure(_IM, feature="dictation").asked  # another episode, same cooldown

    env.clock.advance(601)
    assert env.service.ensure(_IM, feature="dictation").asked
    assert len(env.tcc.requests("input_monitoring")) == 2


@pytest.mark.parametrize("permission", [_SR, _AX], ids=["screen-recording", "accessibility"])
def test_ask_again_after_a_reset_really_asks(
    make_env: Callable[..., Env], permission: PermissionId
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    assert env.service.ensure(permission, feature="computer_use").asked
    env.clock.advance(60)
    assert not env.service.ensure(permission, feature="window_control").asked

    env.service.note_reset(permission)  # tccutil dropped the row: the cooldown is forgotten

    assert env.service.ensure(permission, feature="window_control").asked


def test_a_coalesced_episode_is_only_asking_while_every_member_is_asking(
    make_env: Callable[..., Env],
) -> None:
    both_asking = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    both_asking.service.ensure_all([_AX, _SR], feature="computer_use")
    both_asking.flush()
    assert both_asking.bus.needed()[-1].phase == "os_dialog"

    mixed = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    mixed.tcc.deny("microphone")  # one member is already a final "no"
    mixed.service.ensure_all([_MIC, _AX], feature="voice")
    mixed.flush()
    [event] = mixed.bus.needed()
    assert (event.phase, event.reason) == ("blocked", "denied")  # the card may open now


def test_the_reason_a_coalesced_episode_shows_is_the_most_final_one() -> None:
    assert service_module._REASON_PRIORITY == (
        "restricted",
        "unavailable",
        "denied",
        "needs_settings",
        "restart_hint",
        "not_determined",
    )

    def view(reason: str) -> service_module._View:
        return service_module._View(
            PermissionOutcome.DENIED, reason, "blocked", False, False, False
        )

    slot = service_module._Slot(family=_MIC, target="", probe=_MIC)
    for stronger, weaker in (
        ("restricted", "denied"),
        ("denied", "needs_settings"),
        ("needs_settings", "not_determined"),
        ("restart_hint", "not_determined"),
    ):
        reason, _phase, _view = PermissionService._aggregate(
            [(slot, view(weaker)), (slot, view(stronger))]
        )
        assert reason == stronger


async def test_ensure_async_sees_a_screen_recording_grant_the_preflight_hides_while_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    service = PermissionService(
        poll_s=0.01, watch_interval_s=_NEVER, negative_ttl_s=0.0, oracle_every_s=0.02
    )
    try:
        waiting = asyncio.create_task(service.ensure_async(_SR, feature="computer_use", wait_s=5))
        await asyncio.sleep(0.1)
        assert not waiting.done()
        tcc.grant("screen_recording")  # the preflight stays frozen negative until a relaunch
        result = await asyncio.wait_for(waiting, timeout=5)
        assert result.granted and result.asked
        assert service.check(_SR) is PermissionState.GRANTED  # and it stays proven
    finally:
        service._shutdown()


# ----------------------------------------------------------------------
# Boot and the upgrader (design section 8)
# ----------------------------------------------------------------------


def test_an_upgrader_with_every_grant_in_place_sees_no_card_and_no_request(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(tcc=FakeTCC.all_granted(installed_players=[_MUSIC], running_players=[_MUSIC]))

    results = env.service.ensure_all([_MIC, _SR, _AX, _IM], feature="computer_use")
    results.append(env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC))
    results.append(asyncio.run(env.service.ensure_async(_SR, feature="screen_context")))
    env.service.refresh_episodes()
    env.flush()

    assert all(r.granted and not r.asked for r in results)
    assert env.tcc.requests() == [] and env.tcc.implicit_prompts() == []
    assert env.service.outstanding() == [] and env.bus.events == []


def test_the_automation_player_table_is_what_the_service_may_ask_about() -> None:
    assert {bundle_id for _name, bundle_id in AUTOMATION_TARGETS} >= {_MUSIC}


def test_the_service_keeps_asking_through_the_frozen_api(make_env: Callable[..., Env]) -> None:
    """The review added keyword arguments only: every frozen entry point still answers."""
    env = make_env()
    assert env.service.ensure(_MIC, feature="voice").granted
    assert env.service.ensure_all([_MIC], feature="voice")[0].granted
    assert asyncio.run(env.service.ensure_async(_MIC, feature="voice")).granted
    assert env.service.check(_MIC) is PermissionState.GRANTED
    assert env.service.open_settings(_MIC) is True
    assert env.service.outstanding() == []
    assert any(c.kind is CallKind.REQUEST for c in env.tcc.calls)
