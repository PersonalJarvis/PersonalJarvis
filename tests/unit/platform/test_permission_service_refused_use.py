"""Automation: a send refused with -1743 although the probe read GRANTED.

``report_failed_use(AUTOMATION, ...)`` opens ONE background ``needs_settings`` episode
that a granted probe never closes (the probe is exactly what lied); the next send that
lands (``report_use_ok``), a reset or the episode TTL does. The path never asks. The REAL
service runs on a REAL ``SystemPermissionPort`` that sits on ``FakeTCC``; the FakeTCC call
log is the proof of what was (not) asked. Nothing here ran on a real Mac: the behaviour of
an Apple Event after a grant is documented by Apple only as an error code, everything else
is a MODEL (see ``tests/fakes/fake_tcc.py``).
"""

# ruff: noqa: F811 - the harness fixtures (``make_env``, ``loop_thread``) are imported by name

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from jarvis.core.events import Event
from jarvis.platform.permission_service import PermissionOutcome
from jarvis.platform.permissions import PermissionId, PermissionState
from tests.fakes.fake_tcc import DialogPolicy, TccService
from tests.unit.platform.test_permission_service import (  # noqa: F401 - fixtures
    Env,
    LoopThread,
    RecordingBus,
    loop_thread,
    make_env,
)

_MUSIC = "com.apple.Music"
_SPOTIFY = "com.spotify.client"
_AUTOMATION = PermissionId.AUTOMATION
_SR = PermissionId.SCREEN_RECORDING
_FEATURE = "audio_ducking"


def _granted_music(make_env: Callable[..., Env], **kwargs: Any) -> Env:
    """A Mac where Music runs and Automation reads GRANTED for it."""
    return make_env(
        granted=[TccService.AUTOMATION],
        installed_players=[_MUSIC, _SPOTIFY],
        running_players=[_MUSIC, _SPOTIFY],
        **kwargs,
    )


def _report(env: Env, target: str = _MUSIC, **kwargs: Any):
    return env.service.report_failed_use(_AUTOMATION, feature=_FEATURE, target=target, **kwargs)


# ----------------------------------------------------------------------
# What a refused send opens
# ----------------------------------------------------------------------


def test_a_refused_send_after_a_granted_read_opens_one_background_needs_settings_episode(
    make_env: Callable[..., Env],
) -> None:
    env = _granted_music(make_env)

    result = _report(env)
    env.flush()

    assert result.outcome is PermissionOutcome.NEEDS_SETTINGS and not result.granted
    assert result.reason == "needs_settings" and result.target == _MUSIC
    assert not result.can_prompt and result.can_open_settings and not result.asked
    # The sentence names the player and does not claim the switch is off (the probe says on).
    assert "Automation access for Music" in result.user_detail
    assert "refused an Apple event" in result.user_detail and "is off" not in result.user_detail
    assert result.agent_detail.startswith("[permission_needed:automation] ")
    [needed] = env.bus.needed()
    assert (
        needed.feature,
        needed.permissions,
        needed.reason,
        needed.phase,
        needed.origin,
        needed.target,
        needed.can_prompt,
        needed.can_open_settings,
        needed.outside_app,
    ) == (
        _FEATURE,
        ("automation",),
        "needs_settings",
        "blocked",
        "background",
        _MUSIC,
        False,
        True,
        False,
    )
    [episode] = env.service.outstanding()
    assert (episode.feature, episode.permissions, episode.reason, episode.origin) == (
        _FEATURE,
        ("automation",),
        "needs_settings",
        "background",
    )
    assert episode.target == _MUSIC and episode.detail == needed.detail
    # It never asks: no native request, no dialog, nothing behind the user's back.
    env.tcc.assert_no_prompts()
    assert env.tcc.requests() == [] and env.tcc.dialogs_shown() == []


def test_the_same_report_on_every_session_start_stays_one_event_and_one_episode(
    make_env: Callable[..., Env],
) -> None:
    env = _granted_music(make_env)

    for _ in range(4):
        _report(env)
    env.flush()

    assert len(env.bus.needed()) == 1 and len(env.service.outstanding()) == 1
    assert env.bus.resolved() == []
    assert env.tcc.requests() == []


def test_each_player_has_its_own_episode(make_env: Callable[..., Env]) -> None:
    env = _granted_music(make_env)

    _report(env, _MUSIC)
    _report(env, _SPOTIFY)
    env.flush()

    assert sorted(e.target for e in env.service.outstanding()) == sorted([_MUSIC, _SPOTIFY])
    assert env.service.report_use_ok(_AUTOMATION, feature=_FEATURE, target=_MUSIC) is True
    env.flush()
    assert [e.target for e in env.service.outstanding()] == [_SPOTIFY]  # the other one is untouched


def test_a_stale_user_origin_episode_of_the_player_never_lends_its_card_to_the_report(
    make_env: Callable[..., Env],
) -> None:
    """The ask's own episode (user origin) is answered but not yet swept by the watcher."""
    env = make_env(
        default_policy=DialogPolicy.NEVER_ANSWERED,
        installed_players=[_MUSIC],
        running_players=[_MUSIC],
    )
    env.service.ensure(_AUTOMATION, feature=_FEATURE, target=_MUSIC)  # the switch-on gesture
    assert [e.origin for e in env.service.outstanding()] == ["user"]
    env.tcc.grant(TccService.AUTOMATION, _MUSIC)  # answered; the watcher has not run yet

    _report(env)
    env.flush()

    [episode] = env.service.outstanding()
    assert (episode.reason, episode.origin) == ("needs_settings", "background")
    assert [e.granted for e in env.bus.resolved()] == [True]  # the answered ask ended first


# ----------------------------------------------------------------------
# What ends it, and what must not
# ----------------------------------------------------------------------


def test_a_granted_probe_never_resolves_it_but_a_landed_send_does(
    make_env: Callable[..., Env],
) -> None:
    env = _granted_music(make_env)
    _report(env)

    for _ in range(3):  # the watcher pass, a status read and ensure all see GRANTED
        env.settle()
        assert env.service.check(_AUTOMATION, target=_MUSIC) is PermissionState.GRANTED
    assert env.service.ensure(
        _AUTOMATION, feature=_FEATURE, interactive=False, target=_MUSIC
    ).granted
    assert env.service.ensure(_AUTOMATION, feature=_FEATURE, target=_MUSIC).granted
    env.flush()
    assert env.bus.resolved() == []
    assert [e.reason for e in env.service.outstanding()] == ["needs_settings"]
    assert env.tcc.requests() == []  # a granted state asks nothing, however often it is read

    assert env.service.report_use_ok(_AUTOMATION, feature=_FEATURE, target=_MUSIC) is True
    env.flush()

    assert env.service.outstanding() == []
    [resolved] = env.bus.resolved()
    assert (resolved.feature, resolved.permissions, resolved.granted) == (
        _FEATURE,
        ("automation",),
        True,
    )
    assert env.service.report_use_ok(_AUTOMATION, feature=_FEATURE, target=_MUSIC) is False


def test_the_real_watcher_does_not_resolve_it_either(
    make_env: Callable[..., Env], loop_thread: LoopThread
) -> None:
    import time

    env = _granted_music(make_env, watch_interval_s=0.02)
    _report(env)

    deadline = time.monotonic() + 0.5  # many watcher passes, every one reads GRANTED
    while time.monotonic() < deadline:
        time.sleep(0.02)
    env.flush()

    assert env.bus.resolved() == []
    assert [e.reason for e in env.service.outstanding()] == ["needs_settings"]


def test_a_landed_send_only_clears_the_feature_and_player_it_names(
    make_env: Callable[..., Env],
) -> None:
    env = _granted_music(make_env)
    _report(env)

    assert env.service.report_use_ok(_AUTOMATION, feature="voice", target=_MUSIC) is False
    assert env.service.report_use_ok(_AUTOMATION, feature=_FEATURE, target=_SPOTIFY) is False
    assert env.service.report_use_ok(_AUTOMATION, feature=_FEATURE) is False  # no player named
    assert env.service.report_use_ok(_SR, feature=_FEATURE, target=_MUSIC) is False
    assert env.service.report_use_ok(_AUTOMATION, feature=_FEATURE, target="com.evil.app") is False

    assert [e.reason for e in env.service.outstanding()] == ["needs_settings"]


def test_the_episode_ends_by_its_ttl_and_every_report_keeps_it_alive(
    make_env: Callable[..., Env],
) -> None:
    env = _granted_music(make_env)
    _report(env)

    env.clock.advance(400)
    _report(env)  # the refusal is still happening: the clock starts again
    env.clock.advance(400)
    env.service.refresh_episodes()
    env.flush()
    assert [e.reason for e in env.service.outstanding()] == ["needs_settings"]

    env.clock.advance(201)  # nobody reported for ten minutes: stale evidence
    env.service.refresh_episodes()
    env.flush()

    assert env.service.outstanding() == []
    assert [r.granted for r in env.bus.resolved()] == [False]


def test_a_reset_ends_it_because_the_decision_is_gone(make_env: Callable[..., Env]) -> None:
    env = _granted_music(make_env)
    _report(env)

    env.service.note_reset(_AUTOMATION)  # "Ask again": tccutil dropped the Apple Events row
    env.tcc.reset(TccService.AUTOMATION)
    env.settle()

    assert [e.reason for e in env.service.outstanding()] == ["not_determined"]
    assert env.tcc.requests() == []


def test_a_state_that_is_no_longer_granted_is_described_by_its_real_reason(
    make_env: Callable[..., Env],
) -> None:
    env = _granted_music(make_env)
    _report(env)

    env.tcc.deny(TccService.AUTOMATION, _MUSIC)
    env.settle()
    [episode] = env.service.outstanding()
    assert episode.reason == "denied" and "refused an Apple event" not in episode.detail

    env.tcc.grant(TccService.AUTOMATION, _MUSIC)  # fixed in Settings, but no send has landed yet
    env.settle()
    [episode] = env.service.outstanding()
    assert episode.reason == "needs_settings" and "refused an Apple event" in episode.detail


def test_a_live_state_that_is_not_granted_is_reported_through_the_plain_path(
    make_env: Callable[..., Env],
) -> None:
    env = _granted_music(make_env)
    env.tcc.deny(TccService.AUTOMATION, _MUSIC)

    result = _report(env)
    env.flush()

    assert result.reason == "denied" and not result.granted and not result.asked
    [episode] = env.service.outstanding()
    assert (episode.reason, episode.origin, episode.target) == ("denied", "background", _MUSIC)
    assert env.tcc.requests() == []


# ----------------------------------------------------------------------
# The path never asks, and leaves the ask rules alone
# ----------------------------------------------------------------------


def test_a_player_that_was_never_asked_is_not_asked_by_the_report(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(
        default_policy=DialogPolicy.NEVER_ANSWERED,
        installed_players=[_MUSIC],
        running_players=[_MUSIC],
    )

    result = _report(env)
    env.flush()

    assert result.reason == "not_determined" and not result.asked
    assert env.tcc.requests() == [] and env.tcc.dialogs_shown() == []
    assert [(e.origin, e.reason) for e in env.service.outstanding()] == [
        ("background", "not_determined")
    ]


def test_the_report_leaves_the_one_request_per_episode_rule_alone(
    make_env: Callable[..., Env],
) -> None:
    env = _granted_music(make_env)
    _report(env)
    env.tcc.reset(TccService.AUTOMATION, _MUSIC)  # the grant is gone: undecided again
    env.service.invalidate()

    first = env.service.ensure(_AUTOMATION, feature=_FEATURE, target=_MUSIC)  # the gesture
    again = env.service.ensure(_AUTOMATION, feature=_FEATURE, target=_MUSIC)

    assert first.asked and first.granted  # the report neither used up the ask ...
    assert not again.asked  # ... nor doubled it
    assert [call.target for call in env.tcc.requests("automation")] == [_MUSIC]


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_off_macos_nothing_happens(make_env: Callable[..., Env], platform: str) -> None:
    env = make_env(
        platform=platform,
        granted=[TccService.AUTOMATION],
        installed_players=[_MUSIC],
        running_players=[_MUSIC],
    )

    result = _report(env)
    ok = env.service.report_use_ok(_AUTOMATION, feature=_FEATURE, target=_MUSIC)
    env.flush()

    assert result.outcome is PermissionOutcome.NOT_REQUIRED and result.granted and ok is False
    assert env.service.outstanding() == [] and env.bus.events == []
    env.tcc.assert_silent()  # no framework loaded, no call of any kind


# ----------------------------------------------------------------------
# Unknown targets, reasons and origins
# ----------------------------------------------------------------------


@pytest.mark.parametrize("target", ["com.evil.app", "../../etc/passwd", "Music", "", None])
def test_a_target_outside_the_player_table_is_rejected_and_never_echoed(
    make_env: Callable[..., Env], target: str | None
) -> None:
    env = _granted_music(make_env)
    mark = env.tcc.mark()

    result = env.service.report_failed_use(_AUTOMATION, feature=_FEATURE, target=target)
    env.flush()

    assert result.outcome is PermissionOutcome.UNAVAILABLE and result.reason == "unavailable"
    assert result.target == "" and not result.asked
    for text in (repr(result), repr(env.bus.events), repr(env.service.outstanding())):
        assert "evil" not in text and "passwd" not in text
    assert env.service.outstanding() == [] and env.bus.events == []
    assert env.tcc.calls_since(mark) == ()  # nothing was even read, let alone asked


def test_a_reason_the_family_cannot_carry_is_a_plain_non_interactive_ensure(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(
        granted=[TccService.AUTOMATION, TccService.SCREEN_RECORDING],
        installed_players=[_MUSIC],
        running_players=[_MUSIC],
    )

    automation = _report(env, reason="restart_hint")  # Apple Events are checked per send
    screen = env.service.report_failed_use(_SR, feature="computer_use", reason="needs_settings")
    env.flush()

    assert automation.granted and screen.granted
    assert env.service.outstanding() == [] and env.bus.events == []
    assert env.tcc.requests() == []


def test_the_origin_defaults_per_family_and_an_unknown_one_is_ignored(
    make_env: Callable[..., Env],
) -> None:
    env = _granted_music(make_env)
    env.tcc.grant(TccService.SCREEN_RECORDING)

    _report(env)  # Automation: background
    env.service.report_failed_use(_SR, feature="computer_use")  # Screen Recording: user
    by_permission = {e.permissions: e.origin for e in env.service.outstanding()}
    assert by_permission == {("automation",): "background", ("screen_recording",): "user"}

    explicit = make_env(granted=[TccService.SCREEN_RECORDING])
    explicit.service.report_failed_use(
        _SR, feature="computer_use", origin="background"
    )  # no card for a background consumer
    assert [e.origin for e in explicit.service.outstanding()] == ["background"]

    bogus = _granted_music(make_env)
    _report(bogus, origin="floating_card")  # outside the vocabulary: the family default
    assert [e.origin for e in bogus.service.outstanding()] == ["background"]

    user = _granted_music(make_env)
    _report(user, origin="user")  # a caller that really is a gesture may say so
    assert [e.origin for e in user.service.outstanding()] == ["user"]


# ----------------------------------------------------------------------
# AP-18 / AP-30: a broken bus never breaks the report, and nothing fails silently
# ----------------------------------------------------------------------


class _SyncRaisingBus:
    def publish(self, event: Event) -> None:
        raise RuntimeError("the bus is broken")


class _AsyncRaisingBus:
    async def publish(self, event: Event) -> None:
        raise RuntimeError("the subscriber blew up")


@pytest.mark.parametrize("bus", [_SyncRaisingBus(), _AsyncRaisingBus()], ids=["sync", "async"])
def test_a_bus_that_raises_never_breaks_the_report(
    make_env: Callable[..., Env], bus: Any, caplog: pytest.LogCaptureFixture
) -> None:
    env = _granted_music(make_env)
    env.service.attach_bus(bus, env.loop.loop)

    with caplog.at_level("DEBUG", logger="jarvis.platform.permission_service"):
        result = _report(env)
        env.flush()

    assert result.reason == "needs_settings"
    assert [e.reason for e in env.service.outstanding()] == [
        "needs_settings"
    ]  # the registry kept it
    assert any(
        "permission event" in r.message or "bus refused" in r.message for r in caplog.records
    )
    assert env.service.report_use_ok(_AUTOMATION, feature=_FEATURE, target=_MUSIC) is True


def test_a_closed_loop_drops_the_event_but_not_the_episode(
    make_env: Callable[..., Env], caplog: pytest.LogCaptureFixture
) -> None:
    env = _granted_music(make_env)
    closed = asyncio.new_event_loop()
    closed.close()
    env.service.attach_bus(RecordingBus(), closed)

    with caplog.at_level("DEBUG", logger="jarvis.platform.permission_service"):
        result = _report(env)

    assert result.reason == "needs_settings"
    assert [e.reason for e in env.service.outstanding()] == ["needs_settings"]
    assert any("dropped" in record.message for record in caplog.records)


def test_a_failed_read_is_logged_and_never_raised(
    make_env: Callable[..., Env], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env = _granted_music(make_env)

    def broken(*_args: Any, **_kwargs: Any) -> PermissionState:
        raise RuntimeError("the native read failed")

    monkeypatch.setattr(env.service, "_read_state", broken)

    with caplog.at_level("DEBUG", logger="jarvis.platform.permission_service"):
        result = _report(env)

    assert result.outcome is PermissionOutcome.UNAVAILABLE and result.target == _MUSIC
    assert any("report_failed_use" in record.message for record in caplog.records)
    assert env.service.outstanding() == []
