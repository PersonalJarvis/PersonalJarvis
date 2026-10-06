"""Cross-OS contract of the just-in-time permission service.

One scenario table runs against a ``darwin`` ``FakeTCC`` and against ``win32`` /
``linux``, through every entry point (``ensure``, ``ensure_all``,
``ensure_async``). The contract:

* On macOS each scenario yields the outcome the table says, with the right
  ``asked`` / ``outside_installed_app`` flags, and a refusal is announced on the
  bus exactly once.
* Everywhere else the answer is ALWAYS ``NOT_REQUIRED`` (``granted`` is true),
  nothing is published, nothing is registered and the FakeTCC call log stays
  EMPTY: no framework was loaded, no probe ran, no request was made.
* On every platform ``EnsureResult.granted`` is true only for GRANTED and
  NOT_REQUIRED, a result that may not proceed carries the fixed-template
  sentences, and ``agent_detail`` is the prohibitive one (P9).

Every macOS behaviour is a MODEL (``tests/fakes/fake_tcc.py`` lists which parts
Apple documents); nothing here ran on a real Mac.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.core.events import Event, PermissionNeeded, PermissionResolved
from jarvis.platform.permission_service import (
    EnsureResult,
    PermissionOutcome,
    PermissionService,
)
from jarvis.platform.permissions import PermissionId, PermissionState
from tests.fakes.fake_tcc import DialogPolicy, FakeTCC, install_port

_MUSIC = "com.apple.Music"
_PROCEED = {PermissionOutcome.GRANTED, PermissionOutcome.NOT_REQUIRED}
_PLATFORMS = ("darwin", "win32", "linux")
_ENTRY_POINTS = ("ensure", "ensure_all", "ensure_async")


class _RecordingBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[Event] = []

    async def publish(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> list[Event]:
        with self._lock:
            return list(self._events)


@pytest.fixture
def bus_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()))
    thread.daemon = True
    thread.start()
    yield loop
    asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(timeout=5)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


def _flush(loop: asyncio.AbstractEventLoop) -> None:
    for _ in range(3):
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(timeout=5)


@dataclass(frozen=True)
class Scenario:
    """One row of the table: a world, a call, and what macOS must answer."""

    id: str
    permission: PermissionId
    outcome: PermissionOutcome  # on macOS; every other OS answers NOT_REQUIRED
    asked: bool = False
    outside: bool = False
    tcc: dict[str, Any] = field(default_factory=dict)
    prepare: Callable[[FakeTCC], None] = lambda tcc: None
    call: dict[str, Any] = field(default_factory=dict)
    # How many PermissionNeeded events a refusal publishes on macOS (exactly once).
    announced: int = 1


def _grant(tcc: FakeTCC) -> None:
    tcc.grant("microphone")


def _deny(tcc: FakeTCC) -> None:
    tcc.deny("microphone")


def _restrict(tcc: FakeTCC) -> None:
    tcc.restrict("microphone")


SCENARIOS = (
    Scenario(
        "mic_already_granted",
        PermissionId.MICROPHONE,
        PermissionOutcome.GRANTED,
        prepare=_grant,
        announced=0,
    ),
    Scenario(
        "mic_asked_and_allowed",
        PermissionId.MICROPHONE,
        PermissionOutcome.GRANTED,
        asked=True,
        announced=0,
    ),
    Scenario(
        "mic_asked_dialog_open",
        PermissionId.MICROPHONE,
        PermissionOutcome.PENDING,
        asked=True,
        tcc={"default_policy": DialogPolicy.NEVER_ANSWERED},
    ),
    Scenario(
        "mic_asked_and_denied",
        PermissionId.MICROPHONE,
        PermissionOutcome.DENIED,
        asked=True,
        tcc={"default_policy": DialogPolicy.DENY},
    ),
    Scenario("mic_denied_before", PermissionId.MICROPHONE, PermissionOutcome.DENIED, prepare=_deny),
    Scenario(
        "mic_restricted", PermissionId.MICROPHONE, PermissionOutcome.UNAVAILABLE, prepare=_restrict
    ),
    Scenario(
        "mic_background_consumer",
        PermissionId.MICROPHONE,
        PermissionOutcome.PENDING,
        call={"interactive": False},
    ),
    Scenario(
        "mic_missing_usage_string",
        PermissionId.MICROPHONE,
        PermissionOutcome.UNAVAILABLE,
        tcc={"usage_strings": (), "abort_raises": True},
    ),
    Scenario(
        "mic_outside_the_installed_app",
        PermissionId.MICROPHONE,
        PermissionOutcome.NEEDS_SETTINGS,
        outside=True,
        tcc={"bundle_id": None, "bundle_path": None},
    ),
    Scenario(
        "accessibility_prompt_once",
        PermissionId.ACCESSIBILITY,
        PermissionOutcome.PENDING,
        asked=True,
        tcc={"default_policy": DialogPolicy.NEVER_ANSWERED},
    ),
    Scenario(
        "screen_recording_prompt_once",
        PermissionId.SCREEN_RECORDING,
        PermissionOutcome.PENDING,
        asked=True,
        tcc={"default_policy": DialogPolicy.NEVER_ANSWERED},
    ),
    Scenario(
        "input_monitoring_prompt_once",
        PermissionId.INPUT_MONITORING,
        PermissionOutcome.PENDING,
        asked=True,
        tcc={"default_policy": DialogPolicy.NEVER_ANSWERED},
    ),
    Scenario(
        "event_posting_is_accessibility",
        PermissionId.EVENT_POSTING,
        PermissionOutcome.PENDING,
        asked=True,
        tcc={"default_policy": DialogPolicy.NEVER_ANSWERED},
    ),
    Scenario(
        "automation_player_not_installed",
        PermissionId.AUTOMATION,
        PermissionOutcome.NOT_REQUIRED,
        call={"target": _MUSIC},
        announced=0,
    ),
    Scenario(
        "automation_player_running_allowed",
        PermissionId.AUTOMATION,
        PermissionOutcome.GRANTED,
        asked=True,
        call={"target": _MUSIC},
        tcc={"installed_players": [_MUSIC], "running_players": [_MUSIC]},
        announced=0,
    ),
    Scenario(
        "automation_target_outside_the_table",
        PermissionId.AUTOMATION,
        PermissionOutcome.UNAVAILABLE,
        call={"target": "com.evil.app"},
    ),
)


@dataclass
class World:
    service: PermissionService
    tcc: FakeTCC
    bus: _RecordingBus
    loop: asyncio.AbstractEventLoop

    def run(self, entry: str, scenario: Scenario) -> EnsureResult:
        kwargs = {"feature": "voice", **scenario.call}
        if entry == "ensure":
            return self.service.ensure(scenario.permission, **kwargs)
        if entry == "ensure_all":
            [result] = self.service.ensure_all([scenario.permission], **kwargs)
            return result
        return asyncio.run(self.service.ensure_async(scenario.permission, **kwargs))

    def events(self) -> list[Event]:
        _flush(self.loop)
        return self.bus.events


@pytest.fixture
def build_world(
    monkeypatch: pytest.MonkeyPatch, bus_loop: asyncio.AbstractEventLoop
) -> Iterator[Callable[[str, Scenario], World]]:
    services: list[PermissionService] = []

    def build(platform: str, scenario: Scenario) -> World:
        tcc = FakeTCC(**scenario.tcc)
        scenario.prepare(tcc)
        install_port(monkeypatch, tcc.port(platform))  # type: ignore[arg-type]
        bus = _RecordingBus()
        # The Automation request normally runs on its own daemon thread; the table
        # asserts the final outcome, so it runs inline here.
        service = PermissionService(watch_interval_s=3600.0, ask_runner=lambda work: work())
        service.attach_bus(bus, bus_loop)
        services.append(service)
        return World(service, tcc, bus, bus_loop)

    yield build
    for service in services:
        service._shutdown()


def _assert_result_invariants(result: EnsureResult) -> None:
    """What holds for every result on every platform."""
    assert result.granted is (result.outcome in _PROCEED)
    family = "accessibility" if result.permission is PermissionId.EVENT_POSTING else None
    tag = f"[permission_needed:{family or result.permission.value}] "
    if result.granted:
        assert result.agent_detail == "" and result.user_detail == ""
        assert result.reason == ""
    else:
        assert result.agent_detail.startswith(tag)
        assert "must not retry" in result.agent_detail  # P9: prohibitive, never "click Allow"
        assert result.user_detail.endswith(".") and "Traceback" not in result.user_detail
        assert result.reason


@pytest.mark.parametrize("entry", _ENTRY_POINTS)
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_on_macos_each_scenario_has_the_documented_outcome(
    build_world: Callable[[str, Scenario], World], scenario: Scenario, entry: str
) -> None:
    world = build_world("darwin", scenario)
    result = world.run(entry, scenario)
    assert result.outcome is scenario.outcome, world.tcc.format_log()
    assert result.asked is scenario.asked, world.tcc.format_log()
    assert result.outside_installed_app is scenario.outside
    _assert_result_invariants(result)
    # A prompt only ever happens through an explicit request, never behind our back.
    assert world.tcc.implicit_prompts() == []
    assert world.tcc.aborts() == []
    assert (len(world.tcc.requests()) > 0) is scenario.asked
    needed = [e for e in world.events() if isinstance(e, PermissionNeeded)]
    if result.granted:
        assert needed == []
    else:
        assert len(needed) >= 1
    # Asking again never publishes a second event for the same state (a retry loop is quiet).
    if scenario.announced:
        before = len(needed)
        world.run(entry, scenario)
        again = [e for e in world.events() if isinstance(e, PermissionNeeded)]
        assert len(again) == before


@pytest.mark.parametrize("entry", _ENTRY_POINTS)
@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_off_macos_every_scenario_is_not_required_and_silent(
    build_world: Callable[[str, Scenario], World],
    scenario: Scenario,
    platform: str,
    entry: str,
) -> None:
    world = build_world(platform, scenario)
    result = world.run(entry, scenario)
    assert result.outcome is PermissionOutcome.NOT_REQUIRED
    assert result.granted and result.state is PermissionState.NOT_REQUIRED
    assert not result.asked and not result.outside_installed_app
    _assert_result_invariants(result)
    world.tcc.assert_silent()  # no framework loaded, no probe, no request
    assert world.events() == []
    assert world.service.outstanding() == []


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_check_is_silent_and_follows_the_platform(
    build_world: Callable[[str, Scenario], World], platform: str
) -> None:
    scenario = SCENARIOS[1]  # not determined, would be allowed if asked
    world = build_world(platform, scenario)
    for permission in PermissionId:
        state = world.service.check(permission)
        if platform == "darwin":
            assert state is not PermissionState.NOT_REQUIRED or permission in (
                PermissionId.AUTOMATION,
            )
        else:
            assert state is PermissionState.NOT_REQUIRED
    world.tcc.assert_no_prompts()
    if platform != "darwin":
        world.tcc.assert_silent()
    assert world.events() == []


@pytest.mark.parametrize("platform", _PLATFORMS)
def test_a_background_consumer_never_asks_on_any_platform(
    build_world: Callable[[str, Scenario], World], platform: str
) -> None:
    scenario = Scenario("background", PermissionId.MICROPHONE, PermissionOutcome.PENDING)
    world = build_world(platform, scenario)
    results = world.service.ensure_all(
        [
            PermissionId.MICROPHONE,
            PermissionId.SCREEN_RECORDING,
            PermissionId.ACCESSIBILITY,
            PermissionId.INPUT_MONITORING,
        ],
        feature="wake_word",
        interactive=False,
    )
    assert world.tcc.requests() == [] and world.tcc.implicit_prompts() == []
    if platform == "darwin":
        assert not any(r.granted or r.asked for r in results)
        [episode] = world.service.outstanding()
        assert episode.origin == "background"
    else:
        assert all(r.outcome is PermissionOutcome.NOT_REQUIRED for r in results)
        assert world.service.outstanding() == []


def test_a_grant_ends_the_episode_on_the_bus(
    build_world: Callable[[str, Scenario], World],
) -> None:
    scenario = SCENARIOS[2]
    world = build_world("darwin", scenario)
    assert world.service.ensure(scenario.permission, feature="voice").outcome is (
        PermissionOutcome.PENDING
    )
    world.tcc.answer("microphone", DialogPolicy.ALLOW)
    world.service.invalidate()
    world.service.refresh_episodes()
    kinds = [type(e) for e in world.events()]
    assert kinds == [PermissionNeeded, PermissionResolved]
    assert world.events()[-1].granted is True  # type: ignore[union-attr]


def test_the_scenario_table_covers_every_outcome_on_macos() -> None:
    assert {s.outcome for s in SCENARIOS} == set(PermissionOutcome)
