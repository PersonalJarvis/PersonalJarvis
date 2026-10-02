"""Mute music while dictating: the Automation permission, asked just in time.

The REAL permission service runs on a REAL ``SystemPermissionPort`` that sits on
``FakeTCC`` (``tests/fakes/fake_tcc.py``), a stateful model of macOS privacy, and the
ducker's ``osascript`` is a stand-in that answers like macOS for the scripts the
ducker sends. The FakeTCC call log is the proof of what was (not) asked. Nothing here
ran on a real Mac: every behaviour is a MODEL (see the fidelity ledger in fake_tcc.py).
"""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import threading
from types import SimpleNamespace

import pytest

from jarvis.audio.ducking import macos
from jarvis.audio.ducking.controller import AudioDuckController
from jarvis.audio.ducking.macos import MacOSScriptDucker
from jarvis.audio.ducking.windows import WindowsPycawDucker
from jarvis.platform import permission_service as service_module
from jarvis.platform.permission_service import get_permission_service
from jarvis.platform.permissions import AUTOMATION_TARGETS, PermissionId
from tests.fakes.fake_permission_service import FakePermissionService
from tests.fakes.fake_tcc import (
    AE_EVENT_NOT_PERMITTED,
    CallKind,
    DialogPolicy,
    FakeTCC,
    TccService,
    install_port,
    make_non_darwin_port,
)
from tests.unit.platform.test_permission_service import LoopThread, RecordingBus

_MUSIC = "com.apple.Music"
_SPOTIFY = "com.spotify.client"
_AUTOMATION = PermissionId.AUTOMATION
_DENIED_STDERR = "execution error: Not authorized to send Apple events to Music. (-1743)"


class TccAppleScript:
    """An ``osascript`` stand-in that answers like macOS for the ducker's scripts.

    A pure running-state query is answered from ``FakeTCC``. A script that SENDS an
    Apple event to a player succeeds only with the grant, fails with ``-1743`` when it
    was denied, and is recorded in ``sends_without_decision`` when macOS would have
    shown its consent dialog in the middle of the call: a test that asserts the list
    stays empty proves nothing was scripted behind the user's back.
    """

    def __init__(self, tcc: FakeTCC, *, volume: int = 65) -> None:
        self.tcc = tcc
        self.volume = volume
        self.scripts: list[str] = []
        self.sends: list[str] = []
        self.sends_without_decision: list[str] = []
        self.refuse_with_1743: set[str] = set()

    @staticmethod
    def _bundle(script: str) -> str | None:
        return next(
            (bundle for _name, bundle in AUTOMATION_TARGETS if f'"{bundle}"' in script), None
        )

    def __call__(self, script: str) -> subprocess.CompletedProcess:
        self.scripts.append(script)
        bundle = self._bundle(script)
        if bundle is None:  # the master-volume tier
            return subprocess.CompletedProcess(["osascript"], 0, stdout="50\n", stderr="")
        running = self.tcc.player_running(bundle)
        if "tell application" not in script:  # the pure "is it running" query
            return subprocess.CompletedProcess(
                ["osascript"], 0, stdout="+\n" if running else "-\n", stderr=""
            )
        if not running:
            return subprocess.CompletedProcess(["osascript"], 0, stdout="-\n", stderr="")
        self.sends.append(bundle)
        state = self.tcc.state(TccService.AUTOMATION, bundle).value
        if state == "not_determined":
            self.sends_without_decision.append(bundle)
        if state != "granted" or bundle in self.refuse_with_1743:
            return subprocess.CompletedProcess(["osascript"], 1, stdout="", stderr=_DENIED_STDERR)
        return subprocess.CompletedProcess(["osascript"], 0, stdout=f"{self.volume}\n", stderr="")


def _world(monkeypatch: pytest.MonkeyPatch, **tcc_kwargs):
    """A Mac with Music installed and running; returns (tcc, osascript, ducker)."""
    tcc_kwargs.setdefault("installed_players", [_MUSIC])
    tcc_kwargs.setdefault("running_players", [_MUSIC])
    tcc = FakeTCC(**tcc_kwargs)
    install_port(monkeypatch, tcc.port("darwin"))
    osa = TccAppleScript(tcc)
    return tcc, osa, MacOSScriptDucker(run=osa)


def _requests(tcc: FakeTCC) -> list[str]:
    return [call.target for call in tcc.requests("automation")]


# ----------------------------------------------------------------------
# Switching on: prewarm() is the one asking path
# ----------------------------------------------------------------------


def test_switching_on_with_music_running_asks_exactly_once_and_names_the_player(monkeypatch):
    tcc, osa, ducker = _world(monkeypatch)
    report = ducker.prewarm()
    assert _requests(tcc) == [_MUSIC]  # one ask, for the running player only
    assert len(tcc.dialogs_shown("automation")) == 1
    (music,) = report.players
    assert (music.player, music.target) == ("Music", _MUSIC)
    assert music.outcome == "granted" and music.asked and music.reason == ""
    assert report.not_running == ("Spotify",) and report.note == ""
    assert tcc.implicit_prompts() == []
    # The ducker never scripts the player itself to provoke a dialog, and carries no
    # private copy of the consent script any more (the port owns it).
    assert not osa.sends and not any("get player state" in s for s in osa.scripts)
    assert not hasattr(macos, "_prewarm_script")
    assert "consent_run" not in inspect.signature(MacOSScriptDucker).parameters


def test_every_player_ask_gets_the_full_budget_not_the_leftover(monkeypatch):
    """A slow first dialog must not starve the second ask into a late, lost dialog."""
    import time

    tcc = FakeTCC(installed_players=[_MUSIC, _SPOTIFY], running_players=[_MUSIC, _SPOTIFY])
    runs: list[str] = []

    def slow_consent_runner(script: str):
        runs.append(_MUSIC if _MUSIC in script else _SPOTIFY)
        time.sleep(0.6)  # the person takes a while to answer each dialog
        return tcc.automation_consent_runner(script)

    install_port(monkeypatch, tcc.port("darwin", automation_consent_runner=slow_consent_runner))
    # One ask fits the budget, two do not: a shared deadline would leave the second
    # ask 0.4 s, report "unavailable" and still leave its dialog open on the screen.
    monkeypatch.setattr(macos, "_ASK_BUDGET_S", 1.0)
    report = MacOSScriptDucker(run=TccAppleScript(tcc)).prewarm()
    assert runs == [_MUSIC, _SPOTIFY]
    assert [(p.player, p.outcome, p.asked) for p in report.players] == [
        ("Music", "granted", True),
        ("Spotify", "granted", True),
    ]


def test_a_dialog_still_open_at_the_end_of_the_wait_is_pending_and_never_granted(monkeypatch):
    """The consent runner blocks until the person answers: the service answers PENDING at
    the end of the wait (budget minus a margin) instead of the ducker blocking forever."""
    import time

    tcc = FakeTCC(installed_players=[_MUSIC], running_players=[_MUSIC])
    release = threading.Event()

    def stuck_consent_runner(script: str):
        release.wait(5)
        return tcc.automation_consent_runner(script)

    install_port(monkeypatch, tcc.port("darwin", automation_consent_runner=stuck_consent_runner))
    monkeypatch.setattr(macos, "_ASK_BUDGET_S", 0.5)
    started = time.monotonic()
    try:
        (music,) = MacOSScriptDucker(run=TccAppleScript(tcc)).prewarm().players
    finally:
        release.set()
    assert time.monotonic() - started < 2.0
    assert music.outcome == "pending" and music.asked  # asked once, no answer yet
    assert music.outcome != "granted"


def test_an_ask_that_hangs_inside_the_gate_is_unavailable_and_never_granted(monkeypatch):
    """The budget stays the backstop for a native call that never returns."""
    import time

    release = threading.Event()

    class HungGate(FakePermissionService):
        def ensure(self, permission, **kwargs):
            release.wait(5)
            return super().ensure(permission, **kwargs)

    tcc = FakeTCC(installed_players=[_MUSIC], running_players=[_MUSIC])
    monkeypatch.setattr(macos, "_ASK_BUDGET_S", 0.1)
    ducker = MacOSScriptDucker(run=TccAppleScript(tcc), access_gate=HungGate())
    started = time.monotonic()
    try:
        (music,) = ducker.prewarm().players
    finally:
        release.set()
    assert time.monotonic() - started < 2.0
    assert music.outcome == "unavailable" and not music.asked


def test_the_ask_waits_on_the_request_thread_for_the_budget_minus_a_margin(monkeypatch):
    """prewarm() is a worker-thread caller: it must pass wait_s, never rely on wait_s=0."""
    seen: list[float] = []

    class RecordingGate(FakePermissionService):
        def ensure(self, permission, **kwargs):
            seen.append(kwargs["wait_s"])
            return super().ensure(permission, **kwargs)

    tcc = FakeTCC(installed_players=[_MUSIC], running_players=[_MUSIC])
    ducker = MacOSScriptDucker(run=TccAppleScript(tcc), access_gate=RecordingGate())
    ducker.prewarm()
    assert seen == [pytest.approx(macos._ASK_BUDGET_S - macos._ASK_WAIT_MARGIN_S)]
    assert 0 < seen[0] < macos._ASK_BUDGET_S


def test_switching_on_again_does_not_ask_a_second_time(monkeypatch):
    tcc, _osa, ducker = _world(monkeypatch)
    ducker.prewarm()
    again = ducker.prewarm()
    assert _requests(tcc) == [_MUSIC]
    assert again.players[0].outcome == "granted" and not again.players[0].asked


def test_switching_on_with_no_player_running_asks_nothing_and_says_so(monkeypatch):
    tcc, osa, ducker = _world(monkeypatch, installed_players=[_MUSIC, _SPOTIFY], running_players=[])
    report = ducker.prewarm()
    assert tcc.calls_of(CallKind.REQUEST) == [] and tcc.implicit_prompts() == []
    assert not osa.sends  # a player is never launched or scripted to find out
    assert report.players == () and set(report.not_running) == {"Music", "Spotify"}
    assert "checked while the player is running" in report.note
    body = report.as_dict()
    assert body["checked"] is False and body["asked"] is False and body["players"] == []


def test_a_player_that_is_not_running_is_not_asked_even_when_another_one_is(monkeypatch):
    tcc, _osa, ducker = _world(
        monkeypatch, installed_players=[_MUSIC, _SPOTIFY], running_players=[_SPOTIFY]
    )
    report = ducker.prewarm()
    assert _requests(tcc) == [_SPOTIFY]
    assert [p.player for p in report.players] == ["Spotify"] and report.not_running == ("Music",)


def test_a_denied_player_is_reported_skipped_and_never_asked_again(monkeypatch):
    tcc, osa, ducker = _world(monkeypatch, default_policy=DialogPolicy.DENY)
    first = ducker.prewarm().players[0]
    assert first.outcome == "denied" and first.reason == "denied" and first.asked
    assert first.can_open_settings and "Automation access for Music" in first.detail
    assert _requests(tcc) == [_MUSIC]

    # A voice session (non-interactive) and a second switch-on: no new question.
    assert ducker.mute_others(own_pid=1, never=frozenset()) == []
    second = ducker.prewarm().players[0]
    assert second.outcome == "denied" and not second.asked
    assert _requests(tcc) == [_MUSIC]
    assert tcc.ignored_requests() == []  # macOS was not even bothered with a repeat
    assert not osa.sends  # a denied player is never scripted


def test_outside_the_installed_app_the_ask_is_withheld_and_reported(monkeypatch):
    tcc, _osa, ducker = _world(
        monkeypatch, bundle_id=None, bundle_path="/usr/bin/python3", granted=()
    )
    player = ducker.prewarm().players[0]
    assert tcc.requests() == []  # no native request without an explicit confirmation
    assert player.outcome == "needs_settings" and player.outside_installed_app
    assert player.asked is False


# ----------------------------------------------------------------------
# A voice session never asks
# ----------------------------------------------------------------------


def test_mute_others_never_asks_and_never_scripts_an_unasked_player(monkeypatch):
    tcc, osa, ducker = _world(monkeypatch)
    assert ducker.mute_others(own_pid=1, never=frozenset()) == []
    assert tcc.calls_of(CallKind.REQUEST) == [] and tcc.implicit_prompts() == []
    assert not osa.sends and osa.sends_without_decision == []
    # The miss is recorded for the inline status: a background episode, never the card.
    (episode,) = get_permission_service().outstanding()
    assert (episode.feature, episode.origin, episode.target) == (
        "audio_ducking",
        "background",
        _MUSIC,
    )
    assert episode.reason == "not_determined" and episode.permissions == ("automation",)


def test_a_player_that_is_not_running_leaves_no_episode(monkeypatch):
    tcc, _osa, ducker = _world(monkeypatch, running_players=[])
    assert ducker.mute_others(own_pid=1, never=frozenset()) == []
    assert get_permission_service().outstanding() == []
    assert tcc.calls_of(CallKind.REQUEST) == []


def test_mute_others_ducks_a_player_with_a_live_grant_and_asks_nothing(monkeypatch):
    tcc, osa, ducker = _world(monkeypatch, granted=[TccService.AUTOMATION])
    tokens = ducker.mute_others(own_pid=1, never=frozenset())
    assert tokens == [1] and osa.sends == [_MUSIC]
    assert tcc.calls_of(CallKind.REQUEST) == []
    ducker.restore(tokens)
    assert get_permission_service().outstanding() == []


def test_a_grant_given_in_system_settings_is_used_by_the_next_session(monkeypatch):
    tcc, osa, ducker = _world(monkeypatch)
    assert ducker.mute_others(own_pid=1, never=frozenset()) == []
    tcc.grant(TccService.AUTOMATION, _MUSIC)  # the user flipped the switch
    get_permission_service().invalidate()  # the ~250 ms negative window has passed
    assert ducker.mute_others(own_pid=1, never=frozenset()) == [1]
    assert osa.sends == [_MUSIC] and tcc.calls_of(CallKind.REQUEST) == []


def test_minus_1743_after_granted_skips_the_player_and_reports_needs_settings(monkeypatch):
    tcc, osa, ducker = _world(monkeypatch, granted=[TccService.AUTOMATION])
    osa.refuse_with_1743.add(_MUSIC)  # the probe says granted, the real sender is refused
    assert ducker.mute_others(own_pid=1, never=frozenset()) == []
    assert osa.sends == [_MUSIC]
    assert tcc.calls_of(CallKind.REQUEST) == []  # nothing is asked because of it

    player = ducker.prewarm().players[0]
    assert (player.outcome, player.reason) == ("needs_settings", "needs_settings")
    assert player.can_open_settings and not player.asked
    assert "Automation access for Music" in player.detail
    assert tcc.calls_of(CallKind.REQUEST) == []

    osa.refuse_with_1743.clear()  # fixed in System Settings: the next duck lands
    assert ducker.mute_others(own_pid=1, never=frozenset()) == [1]
    assert ducker.prewarm().players[0].outcome == "granted"


@pytest.fixture
def events():
    """The real service with a bus attached, so a test can read what it published."""
    loop = LoopThread()
    bus = RecordingBus()
    get_permission_service().attach_bus(bus, loop.loop)
    yield SimpleNamespace(bus=bus, flush=loop.flush)
    # The watcher task lives on this loop: stop the service before the loop.
    service_module._reset_for_tests()
    loop.stop()


def _two_players_one_refused(monkeypatch):
    """Music and Spotify both read GRANTED; the real sender is refused for Music only."""
    tcc, osa, ducker = _world(
        monkeypatch,
        installed_players=[_MUSIC, _SPOTIFY],
        running_players=[_MUSIC, _SPOTIFY],
        granted=[TccService.AUTOMATION],
    )
    osa.refuse_with_1743.add(_MUSIC)
    return tcc, osa, ducker


def test_minus_1743_after_granted_at_mute_time_opens_one_background_episode(monkeypatch, events):
    tcc, osa, ducker = _two_players_one_refused(monkeypatch)

    # Music (token 1) is skipped, Spotify (token 2) still ducks.
    assert ducker.mute_others(own_pid=1, never=frozenset()) == [2]
    events.flush()

    assert osa.sends == [_MUSIC, _SPOTIFY]
    tcc.assert_no_prompts()  # nothing is asked because of a refusal
    assert tcc.requests() == [] and tcc.dialogs_shown() == []
    (episode,) = get_permission_service().outstanding()
    assert (
        episode.feature,
        episode.permissions,
        episode.reason,
        episode.phase,
        episode.origin,
        episode.target,
    ) == ("audio_ducking", ("automation",), "needs_settings", "blocked", "background", _MUSIC)
    assert episode.can_open_settings and not episode.can_prompt
    assert "Automation access for Music" in episode.detail
    # ONE event, background origin: only the inline status and the Privacy row show it,
    # never the floating card (that opens for the user origin only).
    needed = events.bus.needed()
    assert [(e.origin, e.reason, e.target) for e in needed] == [
        ("background", "needs_settings", _MUSIC)
    ]


def test_the_refusal_persists_across_sessions_without_a_second_event_or_a_request(
    monkeypatch, events
):
    tcc, osa, ducker = _two_players_one_refused(monkeypatch)

    first = ducker.mute_others(own_pid=1, never=frozenset())
    ducker.restore(first)
    second = ducker.mute_others(own_pid=1, never=frozenset())
    events.flush()

    assert first == second == [2]  # the ducker keeps skipping Music and keeps ducking Spotify
    assert osa.sends.count(_MUSIC) == 2  # it retries every session: that is how a fix is noticed
    assert len(get_permission_service().outstanding()) == 1
    assert len(events.bus.needed()) == 1 and events.bus.resolved() == []
    assert tcc.requests() == []


def test_a_later_successful_send_clears_the_episode(monkeypatch, events):
    tcc, osa, ducker = _two_players_one_refused(monkeypatch)
    ducker.restore(ducker.mute_others(own_pid=1, never=frozenset()))
    assert len(get_permission_service().outstanding()) == 1

    osa.refuse_with_1743.clear()  # fixed in System Settings: the next duck lands
    assert ducker.mute_others(own_pid=1, never=frozenset()) == [1, 2]
    events.flush()

    assert get_permission_service().outstanding() == []
    [resolved] = events.bus.resolved()
    assert (resolved.feature, resolved.permissions, resolved.granted) == (
        "audio_ducking",
        ("automation",),
        True,
    )
    assert tcc.requests() == []


def test_a_probe_that_still_reads_granted_does_not_resolve_it(monkeypatch, events):
    """The probe is exactly what lied: only a send that landed may end the episode."""
    tcc, osa, ducker = _two_players_one_refused(monkeypatch)
    ducker.mute_others(own_pid=1, never=frozenset())
    service = get_permission_service()

    for _ in range(3):  # what the 2 s watcher and every status read do
        service.invalidate()
        assert service.check(PermissionId.AUTOMATION, target=_MUSIC).value == "granted"
        service.refresh_episodes()
    events.flush()

    assert [e.reason for e in service.outstanding()] == ["needs_settings"]
    assert events.bus.resolved() == []
    assert tcc.requests() == []


def test_switching_on_again_after_a_refusal_that_is_then_allowed_for_real_clears_it(
    monkeypatch, events
):
    """A fresh ask that was allowed went through the real sender: the refusal is over."""
    tcc, osa, ducker = _two_players_one_refused(monkeypatch)
    ducker.mute_others(own_pid=1, never=frozenset())
    assert len(get_permission_service().outstanding()) == 1

    tcc.reset(TccService.AUTOMATION, _MUSIC)  # the decision is gone: macOS will ask again
    get_permission_service().invalidate()
    report = ducker.prewarm()
    events.flush()

    music = next(p for p in report.players if p.player == "Music")
    assert music.outcome == "granted" and music.asked
    assert [call.target for call in tcc.requests("automation")] == [_MUSIC]  # ONE ask, by prewarm
    assert get_permission_service().outstanding() == []
    assert [r.granted for r in events.bus.resolved()] == [True]


async def test_a_report_that_hangs_never_holds_the_controller_session_start_up(monkeypatch):
    """The controller holds its lock around mute_others: a service call must be bounded."""
    release, entered = threading.Event(), threading.Event()

    class HungReportGate(FakePermissionService):
        def report_failed_use(self, permission, **kwargs):
            entered.set()
            release.wait(10)
            return super().report_failed_use(permission, **kwargs)

    monkeypatch.setattr(macos, "_PROBE_TIMEOUT_S", 0.05)
    tcc = FakeTCC(installed_players=[_MUSIC, _SPOTIFY], running_players=[_MUSIC, _SPOTIFY])
    tcc.grant(TccService.AUTOMATION, _SPOTIFY)  # Music is refused by the real sender
    ducker = MacOSScriptDucker(run=TccAppleScript(tcc), access_gate=HungReportGate())
    controller = AudioDuckController(bus=_Bus(), cfg=_cfg(enabled=True), ducker=ducker)

    try:
        await asyncio.wait_for(controller._on_start(object()), 5)
        assert entered.is_set()  # the report really was out while the session started
        assert controller._muted == [2]  # the other player still ducked
        assert not controller._lock.locked()
    finally:
        release.set()


def test_ae_denial_constant_matches_the_script_shape_the_ducker_detects():
    assert str(AE_EVENT_NOT_PERMITTED) in _DENIED_STDERR


def test_a_hung_automation_read_never_stalls_the_session(monkeypatch):
    release = threading.Event()
    reads: list[str] = []

    class HungGate(FakePermissionService):
        def check(self, permission, *, target=None):
            reads.append(str(target))
            release.wait(10)
            return super().check(permission, target=target)

    monkeypatch.setattr(macos, "_PROBE_TIMEOUT_S", 0.05)
    tcc = FakeTCC(installed_players=[_MUSIC], running_players=[_MUSIC])
    ducker = MacOSScriptDucker(run=TccAppleScript(tcc), access_gate=HungGate())
    try:
        assert ducker.mute_others(own_pid=1, never=frozenset()) == []  # returns, skipped
        assert ducker.mute_others(own_pid=1, never=frozenset()) == []
        # A hung read still owns its slot: no second thread piles up behind it.
        assert reads.count(_MUSIC) == 1 and reads.count(_SPOTIFY) == 1
    finally:
        release.set()


# ----------------------------------------------------------------------
# The controller: the ask never holds its lock
# ----------------------------------------------------------------------


class _Bus:
    def __init__(self) -> None:
        self.subs: dict[str, object] = {}

    def subscribe(self, event, handler) -> None:
        self.subs[event.__name__] = handler


def _cfg(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        ducking=SimpleNamespace(enabled=enabled, restore_delay_ms=0, never_mute=[])
    )


async def test_the_controller_lock_is_free_while_the_dialog_is_open(monkeypatch):
    release, entered = threading.Event(), threading.Event()
    asking_thread: list[str] = []
    tcc = FakeTCC(installed_players=[_MUSIC], running_players=[_MUSIC])

    def blocking_consent_runner(script: str):
        asking_thread.append(threading.current_thread().name)
        entered.set()
        assert release.wait(10), "the test never answered the dialog"
        return tcc.automation_consent_runner(script)

    install_port(monkeypatch, tcc.port("darwin", automation_consent_runner=blocking_consent_runner))
    bus = _Bus()
    controller = AudioDuckController(
        bus=bus, cfg=_cfg(enabled=False), ducker=MacOSScriptDucker(run=TccAppleScript(tcc))
    )
    controller.attach()

    ask = asyncio.create_task(controller.set_enabled(True))
    try:
        assert await asyncio.to_thread(entered.wait, 5), "the ask never reached macOS"
        assert asking_thread[0] != threading.current_thread().name  # a worker, not the loop
        assert not controller._lock.locked()  # nobody holds it across the ask
        # The user starts and ends a voice session while the dialog is still open:
        # neither handler waits for it, and neither asks anything.
        await asyncio.wait_for(bus.subs["VoiceSessionStarted"](object()), 5)
        assert controller._muted == []  # no grant yet: nothing was scripted
        await asyncio.wait_for(bus.subs["VoiceSessionEnded"](object()), 5)
        assert not ask.done() and tcc.requests("automation") == []
    finally:
        release.set()
    report = await asyncio.wait_for(ask, 10)
    assert [call.target for call in tcc.requests("automation")] == [_MUSIC]
    assert report.players[0].outcome == "granted"
    # The ducker's own state lock-free too: a later session ducks with the new grant.
    get_permission_service().invalidate()
    await asyncio.wait_for(bus.subs["VoiceSessionStarted"](object()), 5)
    assert controller._muted == [1]


# ----------------------------------------------------------------------
# Boot: a configured-on feature asks nothing until the person switches it on
# ----------------------------------------------------------------------


async def test_boot_with_ducking_enabled_and_players_open_asks_and_reads_nothing(monkeypatch):
    """Config ``enabled = true`` is not a gesture: building and attaching the controller
    performs no OS dialog, no request, no probe, and opens no episode."""
    from jarvis.audio.ducking import factory

    tcc = FakeTCC(installed_players=[_MUSIC, _SPOTIFY], running_players=[_MUSIC, _SPOTIFY])
    install_port(monkeypatch, tcc.port("darwin"))
    monkeypatch.setattr(factory, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(factory, "_osascript_available", lambda: True)
    cfg = _cfg(enabled=True)
    bus = _Bus()

    ducker = factory.make_audio_ducker(cfg)
    assert isinstance(ducker, MacOSScriptDucker)
    controller = AudioDuckController(bus=bus, cfg=cfg, ducker=ducker)
    controller.attach()
    await asyncio.sleep(0.05)  # nothing may be scheduled behind attach() either

    assert set(bus.subs) == {"VoiceSessionStarted", "VoiceSessionEnded"}
    tcc.assert_silent()  # no framework loaded, no native call of any kind
    assert get_permission_service().outstanding() == []


# ----------------------------------------------------------------------
# Off macOS nothing changes
# ----------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_off_macos_the_ducker_asks_nothing_and_publishes_nothing(monkeypatch, platform):
    port, tcc = make_non_darwin_port(platform, installed_players=[_MUSIC], running_players=[_MUSIC])
    install_port(monkeypatch, port)
    osa = TccAppleScript(tcc)
    ducker = MacOSScriptDucker(run=osa)  # never built off macOS; inert if it were
    assert ducker.mute_others(own_pid=1, never=frozenset()) == []
    assert not osa.sends
    assert get_permission_service().outstanding() == []
    tcc.assert_silent()


async def test_windows_ducking_is_untouched_by_the_permission_work(monkeypatch):
    port, tcc = make_non_darwin_port("win32")
    install_port(monkeypatch, port)
    ducker = WindowsPycawDucker()
    assert not hasattr(ducker, "prewarm")  # no asking path, no permission concept
    controller = AudioDuckController(bus=_Bus(), cfg=_cfg(enabled=False), ducker=ducker)
    assert await controller.set_enabled(True) is None  # no report: the route adds no key
    assert await controller.set_enabled(False) is None
    tcc.assert_silent()
    assert get_permission_service().outstanding() == []
