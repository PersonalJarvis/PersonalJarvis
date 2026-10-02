"""Unit coverage for the just-in-time permission service.

Every test runs the REAL ``PermissionService`` on a REAL ``SystemPermissionPort``
that sits on ``FakeTCC`` (``tests/fakes/fake_tcc.py``), a stateful model of macOS
privacy. The FakeTCC call log is the proof of what was (not) asked: ``request``
is an explicit prompt API, ``implicit_prompt`` is macOS asking behind a feature's
back. Nothing here ran on a real Mac: every behaviour is a MODEL, and the fidelity
ledger in ``fake_tcc.py`` says which parts Apple documents.

Time is injected (``FakeClock``) wherever a test waits or lets a cooldown pass, so
no test sleeps; the few tests that exercise the real watcher or real threads use
short real intervals and bounded waits.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    PERMISSION_FEATURES,
    PERMISSION_NEEDED_REASONS,
    Event,
    PermissionNeeded,
    PermissionResolved,
)
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import PermissionGate
from jarvis.platform import permission_service as service_module
from jarvis.platform.permission_service import (
    EnsureResult,
    PermissionOutcome,
    PermissionService,
    agent_detail_for,
    get_permission_service,
    user_detail_for,
)
from jarvis.platform.permissions import AUTOMATION_TARGETS, PermissionId, PermissionState
from tests.fakes.fake_permission_service import FakePermissionService, make_result
from tests.fakes.fake_tcc import (
    CallKind,
    DialogPolicy,
    FakeTCC,
    TccService,
    install_port,
)

_MUSIC = "com.apple.Music"
_SPOTIFY = "com.spotify.client"
_MIC = PermissionId.MICROPHONE
_AX = PermissionId.ACCESSIBILITY
_SR = PermissionId.SCREEN_RECORDING
_IM = PermissionId.INPUT_MONITORING
_AUTOMATION = PermissionId.AUTOMATION

# Constructor arguments of ``PermissionService`` a test may pass through ``make_env``
# (every other keyword goes to ``FakeTCC``).
_SERVICE_KWARGS = frozenset(
    {
        "automation_timeout_s",
        "automation_quarantine_s",
        "automation_refresh_wait_s",
        "oracle_every_s",
        "ask_runner",
        "negative_ttl_s",
        "granted_ttl_s",
        "episode_ttl_s",
    }
)


def _run_inline(work: Callable[[], None]) -> None:
    work()


# A watcher interval that never ticks inside a test: edges are driven by hand
# through ``refresh_episodes`` unless the test is about the watcher itself.
_NEVER = 3600.0


# ----------------------------------------------------------------------
# Harness
# ----------------------------------------------------------------------


class FakeClock:
    """A monotonic clock a test advances; ``sleep`` advances it instead of waiting."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        self.on_sleep: Callable[[int], None] | None = None

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(len(self.sleeps))


class RecordingBus:
    """A bus stand-in: ``publish`` is a coroutine, like ``EventBus.publish``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[Event] = []
        self.threads: list[str] = []

    async def publish(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)
            self.threads.append(threading.current_thread().name)

    @property
    def events(self) -> list[Event]:
        with self._lock:
            return list(self._events)

    def of(self, kind: type[Event]) -> list[Any]:
        return [event for event in self.events if isinstance(event, kind)]

    def needed(self) -> list[PermissionNeeded]:
        return self.of(PermissionNeeded)

    def resolved(self) -> list[PermissionResolved]:
        return self.of(PermissionResolved)


class LoopThread:
    """An asyncio loop on its own thread, like the server's loop next to a worker thread."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="test-loop", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def flush(self) -> None:
        """Wait until every callback scheduled so far has run."""
        for _ in range(3):
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0), self.loop).result(timeout=5)

    def stop(self) -> None:
        self.flush()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


@dataclass
class Env:
    service: PermissionService
    tcc: FakeTCC
    clock: FakeClock
    bus: RecordingBus
    loop: LoopThread

    def flush(self) -> None:
        self.loop.flush()

    def settle(self) -> None:
        """Let the negative check() cache expire and drive the watcher's pass by hand."""
        self.clock.advance(1.5)
        self.service.refresh_episodes()
        self.flush()


@pytest.fixture
def loop_thread() -> Iterator[LoopThread]:
    thread = LoopThread()
    yield thread
    thread.stop()


@pytest.fixture
def make_env(
    monkeypatch: pytest.MonkeyPatch, loop_thread: LoopThread
) -> Iterator[Callable[..., Env]]:
    created: list[PermissionService] = []

    def build(
        *,
        platform: str = "darwin",
        attach: bool = True,
        watch_interval_s: float = _NEVER,
        tcc: FakeTCC | None = None,
        inline_ask: bool = True,
        **service_kwargs: Any,
    ) -> Env:
        tcc_kwargs = {k: v for k, v in service_kwargs.items() if k not in _SERVICE_KWARGS}
        service_kwargs = {k: v for k, v in service_kwargs.items() if k in _SERVICE_KWARGS}
        tcc = tcc if tcc is not None else FakeTCC(**tcc_kwargs)
        install_port(monkeypatch, tcc.port(platform))  # type: ignore[arg-type]
        clock = FakeClock()
        bus = RecordingBus()
        if inline_ask:
            # The Automation request normally runs on its own daemon thread; most
            # tests keep its answer inline. The threaded path has its own tests.
            service_kwargs.setdefault("ask_runner", _run_inline)
        service = PermissionService(
            clock=clock, sleep=clock.sleep, watch_interval_s=watch_interval_s, **service_kwargs
        )
        created.append(service)
        if attach:
            service.attach_bus(bus, loop_thread.loop)
        return Env(service, tcc, clock, bus, loop_thread)

    yield build
    for service in created:
        service._shutdown()


def _requests(tcc: FakeTCC, service: TccService | str) -> int:
    return len(tcc.requests(service))


# ----------------------------------------------------------------------
# Off macOS
# ----------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_off_macos_everything_is_not_required_and_nothing_touches_the_port(
    make_env: Callable[..., Env], platform: str
) -> None:
    env = make_env(platform=platform)
    results = [env.service.ensure(permission, feature="voice") for permission in PermissionId]
    results += env.service.ensure_all([_SR, _AX], feature="computer_use", interactive=False)
    assert all(r.outcome is PermissionOutcome.NOT_REQUIRED for r in results)
    assert all(r.granted and r.state is PermissionState.NOT_REQUIRED for r in results)
    assert not any(r.asked or r.outside_installed_app for r in results)
    assert env.service.check(_MIC) is PermissionState.NOT_REQUIRED
    assert asyncio.run(env.service.ensure_async(_MIC, feature="voice")).granted
    env.tcc.assert_silent()
    env.flush()
    assert env.bus.events == []
    assert env.service.outstanding() == []


# ----------------------------------------------------------------------
# The named JIT assertions (design-v2 section 8)
# ----------------------------------------------------------------------


def test_first_interactive_microphone_ensure_makes_exactly_one_request(
    make_env: Callable[..., Env],
) -> None:
    env = make_env()
    result = env.service.ensure(_MIC, feature="dictation", wait_s=0)
    assert result.outcome is PermissionOutcome.GRANTED  # the fake user clicked Allow
    assert result.granted and result.asked
    assert _requests(env.tcc, "microphone") == 1
    assert env.tcc.implicit_prompts() == []
    env.flush()
    assert [r.granted for r in env.bus.resolved()] == [True]
    assert env.service.outstanding() == []


def test_a_denied_microphone_is_never_asked_again(make_env: Callable[..., Env]) -> None:
    env = make_env(default_policy=DialogPolicy.DENY)
    first = env.service.ensure(_MIC, feature="voice")
    assert first.outcome is PermissionOutcome.DENIED and first.asked
    assert _requests(env.tcc, "microphone") == 1
    env.clock.advance(5)
    second = env.service.ensure(_MIC, feature="voice")
    third = env.service.ensure(_MIC, feature="dictation")
    assert second.outcome is third.outcome is PermissionOutcome.DENIED
    assert not second.asked and not third.asked
    assert _requests(env.tcc, "microphone") == 1
    assert env.tcc.ignored_requests() == []  # no re-prompt loop is hiding in the log


def test_a_denial_that_already_exists_is_not_asked_at_all(make_env: Callable[..., Env]) -> None:
    env = make_env()
    env.tcc.deny("microphone")
    result = env.service.ensure(_MIC, feature="voice")
    assert result.outcome is PermissionOutcome.DENIED and not result.asked
    assert env.tcc.requests() == []
    assert result.reason == "denied" and result.can_open_settings and not result.can_prompt


def test_wait_zero_returns_pending_at_once(make_env: Callable[..., Env]) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    result = env.service.ensure(_MIC, feature="voice", wait_s=0)
    assert result.outcome is PermissionOutcome.PENDING and result.asked
    assert not result.granted
    assert env.clock.sleeps == []  # it did not wait
    assert env.tcc.dialog_open("microphone")
    assert result.reason == "not_determined"


def test_a_waiting_ensure_polls_every_250_ms_and_sees_the_answer(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)

    def user_answers(sleep_count: int) -> None:
        if sleep_count == 3:
            env.tcc.answer("microphone", DialogPolicy.ALLOW)

    env.clock.on_sleep = user_answers
    result = env.service.ensure(_MIC, feature="voice", wait_s=10)
    assert result.outcome is PermissionOutcome.GRANTED and result.asked
    assert env.clock.sleeps == [0.25, 0.25, 0.25]
    assert _requests(env.tcc, "microphone") == 1


def test_a_waiting_ensure_that_is_never_answered_ends_pending(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    result = env.service.ensure(_MIC, feature="voice", wait_s=1.0)
    assert result.outcome is PermissionOutcome.PENDING  # never "denied" while a dialog is up
    assert sum(env.clock.sleeps) == pytest.approx(1.0)
    assert _requests(env.tcc, "microphone") == 1


def test_a_background_consumer_never_asks_and_opens_a_background_episode(
    make_env: Callable[..., Env],
) -> None:
    env = make_env()
    results = [
        env.service.ensure(_MIC, feature="wake_word", interactive=False, wait_s=5) for _ in range(4)
    ]
    assert all(r.outcome is PermissionOutcome.PENDING and not r.asked for r in results)
    assert env.tcc.requests() == [] and env.tcc.implicit_prompts() == []
    assert env.clock.sleeps == []  # a background caller never waits
    env.flush()
    events = env.bus.needed()
    assert len(events) == 1  # repeated silent calls publish once
    assert (events[0].origin, events[0].phase, events[0].reason) == (
        "background",
        "blocked",
        "not_determined",
    )
    assert events[0].can_prompt and events[0].feature == "wake_word"
    [episode] = env.service.outstanding()
    assert episode.origin == "background" and episode.permissions == ("microphone",)


def test_a_gesture_upgrades_a_background_episode_and_asks_once(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_MIC, feature="voice", interactive=False)
    interactive = env.service.ensure(_MIC, feature="voice", interactive=True)
    assert interactive.asked and _requests(env.tcc, "microphone") == 1
    env.flush()
    origins = [(e.origin, e.phase) for e in env.bus.needed()]
    assert origins == [("background", "blocked"), ("user", "os_dialog")]
    [episode] = env.service.outstanding()
    assert episode.origin == "user"


def test_outside_the_installed_app_no_native_request_is_made(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(bundle_id=None, bundle_path=None)  # started from a terminal
    result = env.service.ensure(_MIC, feature="voice")
    assert result.outcome is PermissionOutcome.NEEDS_SETTINGS
    assert result.outside_installed_app and not result.asked and not result.granted
    assert env.tcc.requests() == []
    assert result.can_prompt and result.can_open_settings
    assert "not running as an installed app" in result.user_detail
    env.flush()
    [event] = env.bus.needed()
    assert event.outside_app and event.phase == "blocked" and event.reason == "needs_settings"


def test_outside_the_installed_app_an_explicit_confirmation_asks_for_the_grantee(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(bundle_id=None, bundle_path=None, default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_MIC, feature="voice")
    result = env.service.ensure(_MIC, feature="voice", allow_outside_app=True)
    assert result.asked and not result.outside_installed_app
    assert result.outcome is PermissionOutcome.PENDING
    assert _requests(env.tcc, "microphone") == 1
    assert env.tcc.requests()[0].grantee == "com.apple.Terminal"


def test_a_missing_usage_string_is_unavailable_and_nothing_is_called(
    make_env: Callable[..., Env],
) -> None:
    # Without NSMicrophoneUsageDescription the request would abort the process (BUG-058).
    env = make_env(usage_strings=(), abort_raises=True)
    result = env.service.ensure(_MIC, feature="voice")
    assert result.outcome is PermissionOutcome.UNAVAILABLE and not result.asked
    assert result.reason == "unavailable" and not result.can_prompt
    assert env.tcc.requests() == [] and env.tcc.aborts() == []
    env.flush()
    assert [e.reason for e in env.bus.needed()] == ["unavailable"]


def test_restricted_is_an_explanation_only_refusal(make_env: Callable[..., Env]) -> None:
    env = make_env()
    env.tcc.restrict("microphone")
    result = env.service.ensure(_MIC, feature="voice")
    assert result.outcome is PermissionOutcome.UNAVAILABLE
    assert result.state is PermissionState.RESTRICTED and result.reason == "restricted"
    assert not result.can_open_settings and not result.can_prompt and not result.asked
    assert env.tcc.requests() == []
    env.flush()
    [event] = env.bus.needed()
    assert event.reason == "restricted" and not event.can_open_settings


def test_a_prompt_once_permission_is_requested_once_per_cooldown(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    first = env.service.ensure(_AX, feature="computer_use")
    assert first.asked and first.outcome is PermissionOutcome.PENDING
    # A different feature, a minute later: the per-process cooldown still holds.
    env.clock.advance(60)
    other = env.service.ensure(_AX, feature="window_control")
    assert not other.asked
    assert _requests(env.tcc, "accessibility") == 1
    # Ten minutes after the first request the prompt may be shown again.
    env.clock.advance(600)
    later = env.service.ensure(_AX, feature="dictation_insert")
    assert later.asked and _requests(env.tcc, "accessibility") == 2


def test_screen_recording_is_requested_once_per_process(make_env: Callable[..., Env]) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    assert env.service.ensure(_SR, feature="computer_use").asked
    env.clock.advance(100_000)
    again = env.service.ensure(_SR, feature="screen_context")
    assert not again.asked
    assert _requests(env.tcc, "screen_recording") == 1


def test_a_prompt_once_dialog_turns_into_needs_settings_after_fifteen_seconds(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    first = env.service.ensure(_AX, feature="computer_use")
    assert first.outcome is PermissionOutcome.PENDING
    env.clock.advance(16)
    second = env.service.ensure(_AX, feature="computer_use")
    assert second.outcome is PermissionOutcome.NEEDS_SETTINGS and not second.asked
    env.flush()
    assert [(e.phase, e.reason) for e in env.bus.needed()] == [
        ("os_dialog", "needs_settings"),
        ("blocked", "needs_settings"),
    ]
    # A repeated refused call in the same state publishes nothing more.
    env.service.ensure(_AX, feature="computer_use")
    env.flush()
    assert len(env.bus.needed()) == 2


def test_an_app_refocus_makes_a_prompt_once_episode_blocked_at_once(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_SR, feature="computer_use")
    env.service.note_app_activated()
    env.flush()
    assert [(e.phase, e.origin) for e in env.bus.needed()] == [
        ("os_dialog", "user"),
        ("blocked", "user"),
    ]
    assert env.service.outstanding()[0].phase == "blocked"


# ----------------------------------------------------------------------
# Episodes
# ----------------------------------------------------------------------


def test_ensure_all_is_one_coalesced_episode_and_one_event(make_env: Callable[..., Env]) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    results = env.service.ensure_all([_SR, _AX], feature="computer_use")
    assert [r.permission for r in results] == [_SR, _AX]  # request order
    assert all(r.asked and r.outcome is PermissionOutcome.PENDING for r in results)
    assert _requests(env.tcc, "screen_recording") == _requests(env.tcc, "accessibility") == 1
    again = env.service.ensure_all([_SR, _AX], feature="computer_use")
    assert not any(r.asked for r in again)
    env.flush()
    [event] = env.bus.needed()
    assert set(event.permissions) == {"screen_recording", "accessibility"}
    assert event.feature == "computer_use"
    assert len(env.service.outstanding()) == 1


def test_a_granted_member_is_left_out_of_the_coalesced_event(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(
        granted=[TccService.SCREEN_RECORDING], default_policy=DialogPolicy.NEVER_ANSWERED
    )
    results = env.service.ensure_all([_SR, _AX], feature="computer_use")
    assert [r.granted for r in results] == [True, False]
    env.flush()
    [event] = env.bus.needed()
    assert event.permissions == ("accessibility",)


def test_event_posting_is_folded_into_accessibility(make_env: Callable[..., Env]) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    results = env.service.ensure_all([_AX, PermissionId.EVENT_POSTING], feature="computer_use")
    assert _requests(env.tcc, "accessibility") == 1  # one request, one episode
    assert [r.permission for r in results] == [_AX, PermissionId.EVENT_POSTING]
    assert results[1].agent_detail.startswith("[permission_needed:accessibility] ")
    env.flush()
    [event] = env.bus.needed()
    assert event.permissions == ("accessibility",)


def test_the_episode_is_resolved_when_the_user_grants_and_the_watcher_pass_runs(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_AX, feature="computer_use")
    env.tcc.grant("accessibility")  # the user flips the switch in System Settings
    env.settle()
    [resolved] = env.bus.resolved()
    assert resolved.granted and resolved.feature == "computer_use"
    assert resolved.permissions == ("accessibility",)
    assert env.service.outstanding() == []


def test_a_granted_ensure_resolves_the_open_episode_of_another_feature(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_AX, feature="computer_use")
    env.tcc.grant("accessibility")
    env.clock.advance(1.5)
    assert env.service.ensure(_AX, feature="window_control").granted
    env.flush()
    assert [r.feature for r in env.bus.resolved()] == ["computer_use"]


def test_an_episode_nobody_touches_ends_without_a_grant(make_env: Callable[..., Env]) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_AX, feature="computer_use")
    env.clock.advance(601)
    env.service.refresh_episodes()
    env.flush()
    [resolved] = env.bus.resolved()
    assert resolved.granted is False
    assert env.service.outstanding() == []


def test_a_denied_dialog_publishes_blocked_after_the_os_dialog_phase(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_MIC, feature="dictation")
    env.tcc.answer("microphone", DialogPolicy.DENY)
    env.settle()
    assert [(e.phase, e.reason) for e in env.bus.needed()] == [
        ("os_dialog", "not_determined"),
        ("blocked", "denied"),
    ]
    assert env.service.outstanding()[0].reason == "denied"


def test_outstanding_describes_the_open_episode(make_env: Callable[..., Env]) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_MIC, feature="voice", trace_id="6f1d3c0a-1f6e-4b0e-9d5a-0e1f3a4b5c6d")
    [episode] = env.service.outstanding()
    assert episode.feature == "voice" and episode.trace_id.startswith("6f1d3c0a")
    assert episode.as_dict()["permissions"] == ["microphone"]
    assert episode.detail and episode.opened_at_ns > 0


def test_the_unknown_feature_is_tolerated_and_the_unknown_permission_is_a_value_error(
    make_env: Callable[..., Env],
) -> None:
    env = make_env()
    assert env.service.ensure(_MIC, feature="not_a_feature").granted
    with pytest.raises(ValueError):
        env.service.ensure("camera", feature="voice")


# ----------------------------------------------------------------------
# EnsureResult
# ----------------------------------------------------------------------


@pytest.mark.parametrize("outcome", list(PermissionOutcome))
def test_ensure_result_granted_is_true_only_for_granted_and_not_required(
    outcome: PermissionOutcome,
) -> None:
    result = EnsureResult(
        permission=_MIC,
        outcome=outcome,
        state=PermissionState.GRANTED,
        asked=False,
        outside_installed_app=False,
        agent_detail="",
        user_detail="",
    )
    assert result.granted is (
        outcome in (PermissionOutcome.GRANTED, PermissionOutcome.NOT_REQUIRED)
    )


def test_every_outcome_the_service_produces_has_the_right_details(
    make_env: Callable[..., Env],
) -> None:
    produced: dict[PermissionOutcome, EnsureResult] = {}

    env = make_env()
    env.tcc.grant("microphone")
    produced[PermissionOutcome.GRANTED] = env.service.ensure(_MIC, feature="voice")

    env = make_env(installed_players=[])
    produced[PermissionOutcome.NOT_REQUIRED] = env.service.ensure(
        _AUTOMATION, feature="audio_ducking", target=_MUSIC
    )

    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    produced[PermissionOutcome.PENDING] = env.service.ensure(_MIC, feature="voice")

    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.ensure(_AX, feature="computer_use")
    env.clock.advance(20)
    produced[PermissionOutcome.NEEDS_SETTINGS] = env.service.ensure(_AX, feature="computer_use")

    env = make_env()
    env.tcc.deny("microphone")
    produced[PermissionOutcome.DENIED] = env.service.ensure(_MIC, feature="voice")

    env = make_env()
    env.tcc.restrict("microphone")
    produced[PermissionOutcome.UNAVAILABLE] = env.service.ensure(_MIC, feature="voice")

    assert set(produced) == set(PermissionOutcome)
    for outcome, result in produced.items():
        assert result.outcome is outcome
        assert result.granted is (
            outcome in (PermissionOutcome.GRANTED, PermissionOutcome.NOT_REQUIRED)
        )
        if result.granted:
            assert result.agent_detail == "" and result.user_detail == ""
        else:
            tag = f"[permission_needed:{result.permission.value}] "
            assert result.agent_detail.startswith(tag)
            assert result.user_detail and result.user_detail.endswith(".")


@pytest.mark.parametrize("permission", [p for p in PermissionId])
@pytest.mark.parametrize("reason", PERMISSION_NEEDED_REASONS)
@pytest.mark.parametrize("asking", [False, True])
def test_the_sentences_are_fixed_english_templates(
    permission: PermissionId, reason: str, asking: bool
) -> None:
    user = user_detail_for(permission, reason, asking=asking)
    agent = agent_detail_for(permission, reason, asking=asking)
    assert user.endswith(".") and user[0].isalpha()
    assert "Traceback" not in user and "Exception" not in user and "/Users" not in user
    family = "accessibility" if permission is PermissionId.EVENT_POSTING else permission.value
    assert agent.startswith(f"[permission_needed:{family}] ")
    # P9: prohibitive, never an invitation to click or retry.
    assert "must not try to answer, click or dismiss any macOS system dialog" in agent
    assert "must not retry" in agent


def test_the_automation_sentence_names_the_player_from_the_fixed_table() -> None:
    text = user_detail_for(_AUTOMATION, "denied", target=_MUSIC)
    assert "Automation access for Music" in text
    assert {name for name, _ in AUTOMATION_TARGETS} >= {"Music", "Spotify"}


# ----------------------------------------------------------------------
# Never raises
# ----------------------------------------------------------------------

_LEAK = "boom /Users/someone/Library/secret-window-title"


class ExplodingPort:
    """A port whose every native call raises with text the user must never see."""

    platform = "darwin"
    outside_installed_app = False

    def __init__(self, *, state: PermissionState | None = None, usage_raises: bool = False):
        self._state = state
        self._usage_raises = usage_raises
        self.request_calls = 0

    def state(self, permission: Any, **_kwargs: Any) -> PermissionState:
        if self._state is not None:
            return self._state
        raise RuntimeError(_LEAK)

    def usage_string_present(self, permission: Any) -> bool:
        if self._usage_raises:
            raise RuntimeError(_LEAK)
        return True

    def request_native(self, permission: Any, **_kwargs: Any) -> str:
        self.request_calls += 1
        raise RuntimeError(_LEAK)

    def open_settings(self, permission: Any) -> Any:
        raise RuntimeError(_LEAK)


@pytest.fixture
def bare_service() -> Iterator[PermissionService]:
    service = PermissionService(watch_interval_s=_NEVER)
    yield service
    service._shutdown()


def _assert_clean(result: EnsureResult) -> None:
    assert result.outcome is PermissionOutcome.UNAVAILABLE and not result.granted
    for text in (result.user_detail, result.agent_detail):
        assert "boom" not in text and "/Users" not in text and "secret" not in text


def test_ensure_never_raises_when_the_state_read_raises(
    monkeypatch: pytest.MonkeyPatch, bare_service: PermissionService
) -> None:
    monkeypatch.setattr(
        service_module._permissions_module, "get_system_permission_port", ExplodingPort
    )
    _assert_clean(bare_service.ensure(_MIC, feature="voice"))
    assert bare_service.check(_MIC) is PermissionState.UNAVAILABLE
    assert bare_service.open_settings(_MIC) is False
    _assert_clean(asyncio.run(bare_service.ensure_async(_MIC, feature="voice")))
    [only] = bare_service.ensure_all([_MIC, _AX], feature="voice")[:1]
    _assert_clean(only)


def test_ensure_never_raises_when_the_native_request_raises(
    monkeypatch: pytest.MonkeyPatch, bare_service: PermissionService
) -> None:
    port = ExplodingPort(state=PermissionState.NOT_DETERMINED)
    monkeypatch.setattr(
        service_module._permissions_module, "get_system_permission_port", lambda: port
    )
    result = bare_service.ensure(_MIC, feature="voice")
    _assert_clean(result)
    assert result.asked and port.request_calls == 1
    # The failure is remembered for the episode: no hammering on every retry.
    again = bare_service.ensure(_MIC, feature="voice")
    assert not again.asked and port.request_calls == 1


def test_a_failing_usage_string_check_fails_closed(
    monkeypatch: pytest.MonkeyPatch, bare_service: PermissionService
) -> None:
    port = ExplodingPort(state=PermissionState.NOT_DETERMINED, usage_raises=True)
    monkeypatch.setattr(
        service_module._permissions_module, "get_system_permission_port", lambda: port
    )
    _assert_clean(bare_service.ensure(_MIC, feature="voice"))
    assert port.request_calls == 0


def test_a_hand_written_port_without_the_new_api_still_works(
    monkeypatch: pytest.MonkeyPatch, bare_service: PermissionService
) -> None:
    class LegacyPort:
        platform = "darwin"

        def state(self, permission: Any) -> PermissionState:  # no ``deep``/``target`` keyword
            return PermissionState.GRANTED

    monkeypatch.setattr(
        service_module._permissions_module, "get_system_permission_port", LegacyPort
    )
    assert bare_service.ensure(_MIC, feature="voice").granted


def test_a_broken_bus_never_breaks_a_check(
    make_env: Callable[..., Env], loop_thread: LoopThread
) -> None:
    class ExplodingBus:
        def publish(self, event: Event) -> Any:
            raise RuntimeError(_LEAK)

    env = make_env(attach=False, default_policy=DialogPolicy.NEVER_ANSWERED)
    env.service.attach_bus(ExplodingBus(), loop_thread.loop)
    result = env.service.ensure(_MIC, feature="voice")
    assert result.outcome is PermissionOutcome.PENDING


def test_without_a_bus_the_episode_is_kept_and_a_debug_line_is_written(
    make_env: Callable[..., Env], caplog: pytest.LogCaptureFixture
) -> None:
    env = make_env(attach=False, default_policy=DialogPolicy.NEVER_ANSWERED)
    with caplog.at_level(logging.DEBUG, logger=service_module.log.name):
        result = env.service.ensure(_MIC, feature="voice")
    assert result.outcome is PermissionOutcome.PENDING
    assert len(env.service.outstanding()) == 1
    assert any("No bus attached" in record.getMessage() for record in caplog.records)


def test_a_closed_loop_drops_the_event_without_raising(make_env: Callable[..., Env]) -> None:
    env = make_env(attach=False, default_policy=DialogPolicy.NEVER_ANSWERED)
    dead = asyncio.new_event_loop()
    dead.close()
    env.service.attach_bus(RecordingBus(), dead)
    assert env.service.ensure(_MIC, feature="voice").outcome is PermissionOutcome.PENDING


# ----------------------------------------------------------------------
# Automation
# ----------------------------------------------------------------------


def test_automation_is_asked_only_for_a_scriptable_player(make_env: Callable[..., Env]) -> None:
    env = make_env(installed_players=[_MUSIC], running_players=[_MUSIC])
    bad = env.service.ensure(_AUTOMATION, feature="audio_ducking", target="com.evil.app")
    assert bad.outcome is PermissionOutcome.UNAVAILABLE and not bad.granted
    missing = env.service.ensure(_AUTOMATION, feature="audio_ducking")
    assert missing.outcome is PermissionOutcome.UNAVAILABLE
    assert env.tcc.calls == ()  # nothing was probed or asked for a target outside the table


def test_automation_asks_through_the_consent_runner_for_a_running_player(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(installed_players=[_MUSIC], running_players=[_MUSIC])
    result = env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC)
    assert result.outcome is PermissionOutcome.GRANTED and result.asked
    assert [call.target for call in env.tcc.requests("automation")] == [_MUSIC]


def test_automation_for_a_player_that_is_not_running_asks_nothing_and_can_retry(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(installed_players=[_SPOTIFY])
    first = env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_SPOTIFY)
    assert first.outcome is PermissionOutcome.UNAVAILABLE
    assert env.tcc.requests("automation") == []  # the guarded script never launches the player
    env.tcc.launch_player(_SPOTIFY)
    second = env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_SPOTIFY)
    assert second.asked and second.outcome is PermissionOutcome.GRANTED


def test_automation_for_a_player_that_is_not_installed_is_not_required(
    make_env: Callable[..., Env],
) -> None:
    env = make_env()
    result = env.service.ensure(_AUTOMATION, feature="audio_ducking", target=_MUSIC)
    assert result.outcome is PermissionOutcome.NOT_REQUIRED and result.granted


def test_the_credential_store_is_never_requested(make_env: Callable[..., Env]) -> None:
    env = make_env(credential_backend="file")
    result = env.service.ensure(PermissionId.CREDENTIAL_STORE, feature="voice")
    assert result.outcome is PermissionOutcome.DENIED and not result.asked
    assert env.tcc.requests() == [] and env.tcc.keychain_recover_calls == 0


# ----------------------------------------------------------------------
# check()
# ----------------------------------------------------------------------


def test_check_never_prompts_never_publishes_and_caches_a_grant_briefly(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(granted=[TccService.MICROPHONE])
    assert env.service.check(_MIC) is PermissionState.GRANTED
    probes = len(env.tcc.probes("microphone"))
    for _ in range(5):
        assert env.service.check(_MIC) is PermissionState.GRANTED
    assert len(env.tcc.probes("microphone")) == probes  # served from the cache
    env.tcc.deny("microphone")
    assert env.service.check(_MIC) is PermissionState.GRANTED  # still inside the ~1 s window
    env.clock.advance(1.1)
    assert env.service.check(_MIC) is PermissionState.DENIED
    env.tcc.assert_no_prompts()
    env.flush()
    assert env.bus.events == []


def test_invalidate_bypasses_the_cache_after_a_capture_error(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(granted=[TccService.MICROPHONE])
    assert env.service.check(_MIC) is PermissionState.GRANTED
    env.tcc.deny("microphone")
    env.service.invalidate(_MIC)
    assert env.service.check(_MIC) is PermissionState.DENIED
    env.service.invalidate()
    assert env.service.check(_MIC) is PermissionState.DENIED


def test_a_negative_is_cached_for_a_quarter_of_a_second_only(
    make_env: Callable[..., Env],
) -> None:
    env = make_env()
    assert env.service.check(_MIC) is PermissionState.NOT_DETERMINED
    env.tcc.grant("microphone")
    env.clock.advance(0.1)
    assert env.service.check(_MIC) is PermissionState.NOT_DETERMINED  # cached
    env.clock.advance(0.2)
    assert env.service.check(_MIC) is PermissionState.GRANTED


def test_check_takes_no_lock(make_env: Callable[..., Env]) -> None:
    env = make_env(granted=[TccService.MICROPHONE])
    answers: list[PermissionState] = []
    with env.service._lock:  # a writer holds the service lock
        worker = threading.Thread(target=lambda: answers.append(env.service.check(_MIC)))
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert answers == [PermissionState.GRANTED]


def test_a_screen_recording_grant_the_frozen_preflight_hides_is_seen_by_ensure(
    make_env: Callable[..., Env],
) -> None:
    # BUG-161: the preflight is frozen per process, so only the window-title oracle
    # sees a grant given while the app runs. ensure() and the watcher use it.
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    assert env.service.ensure(_SR, feature="computer_use").outcome is PermissionOutcome.PENDING
    env.tcc.grant("screen_recording")
    env.clock.advance(1.5)
    assert env.service.check(_SR) is PermissionState.NOT_GRANTED  # hot path: preflight only
    assert env.service.ensure(_SR, feature="computer_use").granted
    env.flush()
    assert [r.granted for r in env.bus.resolved()] == [True]


def test_in_the_world_where_a_screen_grant_needs_a_relaunch_the_user_is_told_to_wait(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED, screen_grant_needs_relaunch=True)
    env.service.ensure(_SR, feature="computer_use")
    env.tcc.grant("screen_recording")
    env.clock.advance(20)
    result = env.service.ensure(_SR, feature="computer_use")
    assert not result.granted and result.outcome is PermissionOutcome.NEEDS_SETTINGS


def test_check_reads_screen_recording_without_the_window_oracle(
    make_env: Callable[..., Env],
) -> None:
    env = make_env()
    env.service.check(_SR)
    apis = {call.api for call in env.tcc.calls}
    assert "window title oracle" not in apis  # deep=False: a hot path never enumerates windows


# ----------------------------------------------------------------------
# Threads, the bus and the loop
# ----------------------------------------------------------------------


@pytest.mark.parametrize("policy", [DialogPolicy.NEVER_ANSWERED, DialogPolicy.ALLOW])
def test_many_threads_produce_one_native_request(
    make_env: Callable[..., Env], policy: DialogPolicy
) -> None:
    env = make_env(default_policy=policy)
    count = 24
    barrier = threading.Barrier(count)
    results: list[EnsureResult] = []
    guard = threading.Lock()

    def worker(index: int) -> None:
        barrier.wait(timeout=5)
        # Half the threads share one feature, half use another: the cooldown covers both.
        feature = "voice" if index % 2 else "dictation"
        result = env.service.ensure(_MIC, feature=feature, wait_s=0)
        with guard:
            results.append(result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(results) == count
    assert _requests(env.tcc, "microphone") == 1
    assert sum(1 for r in results if r.asked) == 1
    allowed = {PermissionOutcome.PENDING, PermissionOutcome.GRANTED}
    assert {r.outcome for r in results} <= allowed


def test_a_publish_from_a_worker_thread_reaches_the_loop(
    monkeypatch: pytest.MonkeyPatch, loop_thread: LoopThread
) -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    bus = EventBus()
    delivered: list[tuple[PermissionNeeded, str]] = []
    arrived = threading.Event()

    async def handler(event: PermissionNeeded) -> None:
        delivered.append((event, threading.current_thread().name))
        arrived.set()

    bus.subscribe(PermissionNeeded, handler)
    service = PermissionService(watch_interval_s=_NEVER)
    try:
        service.attach_bus(bus, loop_thread.loop)
        worker = threading.Thread(
            target=lambda: service.ensure(_MIC, feature="voice"), name="tool-worker"
        )
        worker.start()
        worker.join(timeout=5)
        assert arrived.wait(timeout=5)
    finally:
        service._shutdown()
    [(event, thread_name)] = delivered
    assert thread_name == "test-loop"  # the handler ran ON the loop, not on the worker
    assert event.feature == "voice" and event.permissions == ("microphone",)


async def test_ensure_on_the_loop_thread_never_blocks_on_its_own_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A publish that waited for its own result on the thread that runs the loop would deadlock.
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    bus = EventBus()
    seen: list[PermissionNeeded] = []

    async def handler(event: PermissionNeeded) -> None:
        seen.append(event)

    bus.subscribe(PermissionNeeded, handler)
    service = PermissionService(watch_interval_s=_NEVER)
    try:
        service.attach_bus(bus, asyncio.get_running_loop())
        result = service.ensure(_MIC, feature="voice", wait_s=0)  # a synchronous call on the loop
        assert result.outcome is PermissionOutcome.PENDING
        for _ in range(3):
            await asyncio.sleep(0)
        assert [e.feature for e in seen] == ["voice"]
    finally:
        service._shutdown()


def test_a_listener_that_raises_does_not_stop_the_others(
    make_env: Callable[..., Env], caplog: pytest.LogCaptureFixture
) -> None:
    env = make_env(attach=False, default_policy=DialogPolicy.NEVER_ANSWERED)
    called: list[str] = []

    def first() -> None:
        called.append("first")

    def broken() -> None:
        called.append("broken")
        raise RuntimeError("listener bug")

    def last() -> None:
        called.append("last")

    for callback in (first, broken, last):
        env.service.add_listener(_AX, callback)
    env.service.ensure(_AX, feature="computer_use")
    env.tcc.grant("accessibility")
    with caplog.at_level(logging.WARNING, logger=service_module.log.name):
        env.settle()
    assert called == ["first", "broken", "last"]
    assert any("A permission listener raised" in r.getMessage() for r in caplog.records)
    # The edge fires once: another pass does not call anybody again.
    env.settle()
    assert called == ["first", "broken", "last"]


def test_listeners_run_on_the_loop_thread_when_a_loop_is_attached(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    threads: list[str] = []
    env.service.add_listener(_MIC, lambda: threads.append(threading.current_thread().name))
    env.service.ensure(_MIC, feature="wake_word", interactive=False)
    env.tcc.grant("microphone")
    env.settle()
    assert threads == ["test-loop"]


def test_an_unsubscribed_listener_is_not_called(make_env: Callable[..., Env]) -> None:
    env = make_env(attach=False, default_policy=DialogPolicy.NEVER_ANSWERED)
    called: list[int] = []
    unsubscribe = env.service.add_listener(PermissionId.EVENT_POSTING, lambda: called.append(1))
    unsubscribe()
    unsubscribe()  # idempotent
    env.service.ensure(_AX, feature="computer_use")
    env.tcc.grant("accessibility")
    env.settle()
    assert called == []


def test_a_listener_fires_per_permission_even_before_the_whole_episode_is_done(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(attach=False, default_policy=DialogPolicy.NEVER_ANSWERED)
    fired: list[str] = []
    env.service.add_listener(_SR, lambda: fired.append("sr"))
    env.service.add_listener(_AX, lambda: fired.append("ax"))
    env.service.ensure_all([_SR, _AX], feature="computer_use")
    env.tcc.grant("accessibility")
    env.settle()
    assert fired == ["ax"] and len(env.service.outstanding()) == 1
    env.tcc.grant("screen_recording")
    env.clock.advance(10)  # the frozen preflight needs the oracle, which is rate limited
    env.settle()
    assert fired == ["ax", "sr"] and env.service.outstanding() == []


def test_the_thread_watcher_resolves_an_episode_without_a_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    service = PermissionService(watch_interval_s=0.02, negative_ttl_s=0.0)
    granted = threading.Event()
    try:
        service.add_listener(_AX, granted.set)
        service.ensure(_AX, feature="computer_use")
        assert service._watch_thread is not None and service._watch_thread.daemon
        tcc.grant("accessibility")
        assert granted.wait(timeout=5)
        deadline = threading.Event()
        for _ in range(100):
            if not service._watch_running:
                break
            deadline.wait(0.02)
        assert service.outstanding() == []
        assert service._watch_running is False  # it stops when nothing is open
    finally:
        service._shutdown()


def test_the_task_watcher_publishes_resolved_through_the_bus(
    monkeypatch: pytest.MonkeyPatch, loop_thread: LoopThread
) -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    bus = RecordingBus()
    service = PermissionService(watch_interval_s=0.02, negative_ttl_s=0.0)
    try:
        service.attach_bus(bus, loop_thread.loop)
        service.ensure(_MIC, feature="wake_word", interactive=False)
        assert service._watch_kind == "task"
        tcc.grant("microphone")
        for _ in range(250):
            if bus.resolved():
                break
            threading.Event().wait(0.02)
        [resolved] = bus.resolved()
        assert resolved.granted and resolved.feature == "wake_word"
    finally:
        service._shutdown()


async def test_ensure_async_polls_without_pinning_an_executor_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)  # one worker: a pinned worker would starve it
    loop.set_default_executor(executor)
    service = PermissionService(poll_s=0.01, watch_interval_s=_NEVER, negative_ttl_s=0.0)
    try:
        waiting = asyncio.create_task(service.ensure_async(_MIC, feature="voice", wait_s=5))
        await asyncio.sleep(0.2)  # the request is made and the polling loop is running
        assert not waiting.done()
        free = await asyncio.wait_for(loop.run_in_executor(None, lambda: "free"), timeout=2)
        assert free == "free"
        tcc.answer("microphone", DialogPolicy.ALLOW)
        result = await asyncio.wait_for(waiting, timeout=5)
        assert result.outcome is PermissionOutcome.GRANTED and result.asked
        # Only the first read-and-request ran on a worker; every poll after that ran
        # on the loop thread, so the single worker was never held while waiting.
        loop_name = threading.current_thread().name
        threads = [c.thread for c in tcc.calls if c.kind is CallKind.PROBE]
        last_worker = max(i for i, name in enumerate(threads) if name != loop_name)
        assert set(threads[last_worker + 1 :]) == {loop_name}
        assert len(threads[last_worker + 1 :]) > 3
    finally:
        service._shutdown()
        executor.shutdown(wait=False)


async def test_ensure_async_wait_zero_returns_pending_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc = FakeTCC(default_policy=DialogPolicy.NEVER_ANSWERED)
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    service = PermissionService(watch_interval_s=_NEVER)
    try:
        result = await service.ensure_async(_MIC, feature="wake_word", wait_s=0)
        assert result.outcome is PermissionOutcome.PENDING and result.asked
        granted = await service.ensure_async(_AX, feature="computer_use", interactive=False)
        assert not granted.granted and not granted.asked
    finally:
        service._shutdown()


# ----------------------------------------------------------------------
# Singleton, imports, reset
# ----------------------------------------------------------------------


def test_the_singleton_is_lazy_and_reset_drops_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "_SERVICE", None)
    first = get_permission_service()
    assert get_permission_service() is first
    service_module._reset_for_tests()
    assert get_permission_service() is not first


def test_importing_the_service_loads_no_native_framework() -> None:
    code = (
        "import sys\n"
        "import jarvis.platform.permission_service as service\n"
        "service.PermissionService()\n"
        "loaded = [m for m in sys.modules if m.split('.')[0] in "
        "('objc', 'Quartz', 'AppKit', 'Foundation', 'AVFoundation', 'ApplicationServices')]\n"
        "assert not loaded, loaded\n"
    )
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", code],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    assert done.returncode == 0, done.stderr


def test_constructing_the_service_calls_no_framework(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc = FakeTCC()
    install_port(monkeypatch, tcc.port("darwin"))  # type: ignore[arg-type]
    service = PermissionService()
    assert tcc.calls == () and tcc.loaded_modules == []
    service._shutdown()


def test_both_implementations_satisfy_the_gate_protocol() -> None:
    assert isinstance(PermissionService(), PermissionGate)
    assert isinstance(FakePermissionService(), PermissionGate)
    assert "voice" in PERMISSION_FEATURES


# ----------------------------------------------------------------------
# The consumer-side fake
# ----------------------------------------------------------------------


def test_the_fake_service_serves_scripted_outcomes_and_logs_every_call() -> None:
    gate = FakePermissionService()
    gate.script(_AX, PermissionOutcome.DENIED, PermissionOutcome.GRANTED)
    gate.script(_MIC, "pending")
    denied = gate.ensure(_AX, feature="computer_use")
    granted = gate.ensure(_AX, feature="computer_use")
    pending = gate.ensure(_MIC, feature="voice", interactive=False)
    assert (denied.outcome, granted.outcome) == (
        PermissionOutcome.DENIED,
        PermissionOutcome.GRANTED,
    )
    assert not denied.granted and granted.granted
    assert denied.agent_detail.startswith("[permission_needed:accessibility] ")
    assert pending.outcome is PermissionOutcome.PENDING and not pending.asked
    assert gate.check(_MIC) is PermissionState.NOT_DETERMINED
    assert [c.feature for c in gate.ensure_calls(_AX)] == ["computer_use", "computer_use"]
    assert len(gate.check_calls(_MIC)) == 1
    assert not gate.native_free()  # the interactive AX ensure could have asked
    assert gate.open_settings(_MIC) is True
    assert FakePermissionService(settings_opens=False).open_settings(_MIC) is False


def test_the_fake_service_can_prove_that_a_consumer_never_asked() -> None:
    gate = FakePermissionService(default=PermissionOutcome.PENDING)
    gate.ensure(_MIC, feature="wake_word", interactive=False)
    gate.check(_MIC)
    assert gate.native_free()
    gate.ensure(_MIC, feature="voice")
    assert not gate.native_free()


def test_the_fake_service_returns_a_ready_made_result_as_it_is() -> None:
    ready = make_result(_SR, PermissionOutcome.NEEDS_SETTINGS, asked=True)
    gate = FakePermissionService({_SR: ready})
    assert gate.ensure(_SR, feature="computer_use") is ready
    assert asyncio.run(gate.ensure_async(_SR, feature="computer_use")) is ready
    with pytest.raises(ValueError):
        gate.script(_SR)
