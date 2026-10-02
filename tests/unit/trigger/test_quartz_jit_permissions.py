"""The macOS global-shortcut tap under just-in-time permissions (AP-35).

Driven against ``FakeTCC`` + ``FakeEventTap`` (the stateful macOS simulator in
``tests/fakes``), so the REAL permission port, the REAL permission service and
the REAL ``QuartzHotkeyBackend`` / ``HotkeyTrigger`` run on top of it. Nothing
here is verified on a real Mac; the fidelity labels of ``fake_tcc`` apply.

What is pinned:

* Boot with nothing granted creates no event tap, makes no request and logs at
  INFO only (the lazy tap; never a tap to provoke a prompt, BUG-058 class).
* The requirement is Input Monitoring ONLY; Accessibility is not consulted.
* A grant re-arms the tap in process through the service listener, once.
* The tap callback never reaches the service's ensure path or its lock.
* The raw-callback counter, the deaf-tap rules and "never auto-restart".
* Non-darwin: nothing requested, nothing published, backends without the probe
  untouched.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from jarvis.platform import permission_service as service_mod
from jarvis.platform.permissions import PermissionId, PermissionState
from jarvis.trigger.backends import quartz as quartz_mod
from jarvis.trigger.backends.pynput import _macos_hotkey_permissions_granted
from jarvis.trigger.backends.quartz import QuartzHotkeyBackend
from jarvis.trigger.hotkey import HotkeyTrigger
from tests.fakes.fake_quartz_tap import FakeQuartz
from tests.fakes.fake_tcc import (
    FakeTCC,
    TccService,
    install_port,
    make_darwin_port,
    make_non_darwin_port,
)

_KC_J = 0x26
_FLAG_CTRL = 1 << 18


def _darwin(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeTCC, FakeQuartz]:
    """A Mac where nothing was decided yet, with the fake tap installed."""
    port, tcc = make_darwin_port()
    install_port(monkeypatch, port)
    fake = FakeQuartz(tcc).install(monkeypatch)
    return tcc, fake


def _backend(*rows: list) -> QuartzHotkeyBackend:
    backend = QuartzHotkeyBackend()  # the DEFAULT permission seam: the service
    backend.register(list(rows) or [["control + j", lambda: None, None]])
    return backend


# --------------------------------------------------------------------------
# Boot: lazy, silent, never asking.
# --------------------------------------------------------------------------


def test_boot_with_nothing_granted_creates_no_tap_and_asks_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    tcc, fake = _darwin(monkeypatch)
    backend = _backend()

    with caplog.at_level(logging.DEBUG, logger="jarvis.trigger.backends.quartz"):
        backend.start()

    assert fake.tap.attempts == [], "CGEventTapCreate must wait for the grant"
    assert tcc.requests() == []
    assert tcc.implicit_prompts() == []
    assert tcc.dialogs_shown() == []
    assert backend.is_listening() is False
    assert backend.waiting_for_permission is True
    # The decision is still the user's: nothing registered the app as denied.
    assert tcc.state(TccService.INPUT_MONITORING).value == "not_determined"
    quiet = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert quiet == [], "an unattended Mac at boot must not produce warnings"
    assert any(
        r.levelno == logging.INFO and "Input Monitoring" in r.message for r in caplog.records
    )


def test_boot_is_silent_with_input_monitoring_denied_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, fake = _darwin(monkeypatch)
    tcc.deny(TccService.INPUT_MONITORING)
    _backend().start()
    assert fake.tap.attempts == []
    assert tcc.requests() == [] and tcc.implicit_prompts() == []


def test_the_requirement_is_input_monitoring_only(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc, _fake = _darwin(monkeypatch)
    tcc.grant(TccService.ACCESSIBILITY)  # Accessibility alone does not arm the tap
    assert _macos_hotkey_permissions_granted() is False

    service_mod.get_permission_service().invalidate()  # skip the 250 ms negative cache
    tcc.grant(TccService.INPUT_MONITORING)
    tcc.deny(TccService.ACCESSIBILITY)  # and it is not needed once Input Monitoring is on
    assert _macos_hotkey_permissions_granted() is True


def test_input_monitoring_granted_creates_the_tap_once(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc, fake = _darwin(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)
    backend = _backend()
    try:
        backend.start()
        backend.start()  # idempotent: the permission listener may call it again
        assert fake.tap.attempts == ["live"]
        assert backend.is_listening() is True
        assert backend.waiting_for_permission is False
        assert tcc.requests() == [] and tcc.implicit_prompts() == []
    finally:
        backend.stop()


def test_a_listening_tap_still_never_asks_for_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc, fake = _darwin(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)
    backend = _backend()
    try:
        backend.start()
        fake.deliver_flags(_FLAG_CTRL)
        fake.deliver_key(_KC_J)
        assert tcc.requests() == []
    finally:
        backend.stop()


# --------------------------------------------------------------------------
# The tap callback: lock-free cached verdict, never the service's ensure path.
# --------------------------------------------------------------------------


class _ForbiddenLock:
    """A lock that fails the test the moment anything takes it."""

    def __enter__(self) -> None:
        raise AssertionError("the tap callback took a permission-service lock")

    def __exit__(self, *_exc: object) -> None:
        return None

    def acquire(self, *_args: object, **_kwargs: object) -> bool:
        raise AssertionError("the tap callback took a permission-service lock")

    def release(self) -> None:
        return None


def test_the_tap_callback_never_calls_ensure_or_takes_a_service_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, fake = _darwin(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)
    fired: list[str] = []
    backend = _backend(["control + j", lambda: fired.append("down"), lambda: fired.append("up")])
    service = service_mod.get_permission_service()
    real_lock = service._lock
    reads: list[str] = []
    real_check = service.check

    def counting_check(permission, **kwargs):  # noqa: ANN001, ANN003
        reads.append(str(permission))
        return real_check(permission, **kwargs)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the tap callback reached the service's ensure path")

    try:
        backend.start()
        monkeypatch.setattr(service, "check", counting_check)
        for name in ("ensure", "ensure_all", "ensure_async", "ensure_all_async", "open_settings"):
            monkeypatch.setattr(service, name, forbidden)
        monkeypatch.setattr(service, "_lock", _ForbiddenLock())

        # Force a re-probe inside the callback (the cached verdict went stale).
        backend._permission_checked_at = 0.0
        assert fake.deliver_flags(_FLAG_CTRL)
        assert fake.deliver_key(_KC_J)
        fake.deliver_key(_KC_J, down=False)
    finally:
        service._lock = real_lock  # the fixture's teardown needs the real one
        backend.stop()

    assert fired == ["down", "up"]
    assert reads and set(reads) == {"input_monitoring"}, "only the silent check, only IM"


def test_the_callback_reads_a_cached_verdict_not_a_probe_per_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, fake = _darwin(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)
    backend = _backend()
    try:
        backend.start()
        for _ in range(60):
            fake.deliver_key(_KC_J)
            fake.deliver_key(_KC_J, down=False)
        # One verdict at most, not one native probe per keystroke.
        assert len(tcc.probes(TccService.INPUT_MONITORING)) <= 2
    finally:
        backend.stop()


# --------------------------------------------------------------------------
# Raw-callback counter and the deaf-tap rules.
# --------------------------------------------------------------------------


def test_every_event_the_tap_hears_counts_bound_or_not(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc, fake = _darwin(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)
    backend = _backend()
    try:
        backend.start()
        assert backend.raw_event_count() == 0
        fake.deliver_key(0x00)  # "a": not part of any chord
        fake.deliver_flags(_FLAG_CTRL)
        fake.deliver_key(0x00, down=False)
        assert backend.raw_event_count() == 3
        assert backend.received_any_event() is False, "no BOUND chord fired"
    finally:
        backend.stop()


def _listening_backend(monkeypatch: pytest.MonkeyPatch) -> tuple[QuartzHotkeyBackend, FakeQuartz]:
    tcc, fake = _darwin(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)
    backend = _backend()
    backend.start()
    assert backend.is_listening()
    backend._secure_input_enabled = lambda: False  # the Carbon flag cannot be read here
    return backend, fake


def test_a_tap_that_is_not_listening_is_never_called_deaf(monkeypatch: pytest.MonkeyPatch) -> None:
    _darwin(monkeypatch)
    backend = _backend()
    assert backend.deaf_tap_suspected() is False


def test_deaf_needs_user_typing_after_the_tap_came_up(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, _fake = _listening_backend(monkeypatch)
    try:
        now = quartz_mod.time.monotonic()
        backend._armed_at = now - 30.0

        # The user typed 5 s ago, i.e. after the tap came up, and it heard nothing.
        backend._seconds_since_last_key = lambda: 5.0
        assert backend.deaf_tap_suspected() is True

        # Nobody has typed since the tap came up: not deaf, just idle.
        backend._seconds_since_last_key = lambda: 600.0
        assert backend.deaf_tap_suspected() is False

        # The idle counter is unreadable: no claim.
        backend._seconds_since_last_key = lambda: None
        assert backend.deaf_tap_suspected() is False

        # Too young to judge.
        backend._armed_at = now - 1.0
        backend._seconds_since_last_key = lambda: 0.5
        assert backend.deaf_tap_suspected() is False
    finally:
        backend.stop()


def test_secure_event_input_never_reads_as_a_deaf_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under Secure Event Input macOS hides key events from every tap by design."""
    backend, _fake = _listening_backend(monkeypatch)
    try:
        backend._armed_at = quartz_mod.time.monotonic() - 30.0
        # The idle counter advances (the user typed into a password field) ...
        backend._seconds_since_last_key = lambda: 5.0
        backend._secure_input_enabled = lambda: True
        assert backend.deaf_tap_suspected() is False, "a healthy tap must not ask for a restart"
        # ... an unreadable flag makes no claim either ...
        backend._secure_input_enabled = lambda: None
        assert backend.deaf_tap_suspected() is False
        # ... and with the flag read as off the same evidence is a deaf tap.
        backend._secure_input_enabled = lambda: False
        assert backend.deaf_tap_suspected() is True
    finally:
        backend.stop()


def test_the_secure_input_probe_is_lazy_and_reads_unknown_when_carbon_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing Carbon framework reads "unknown", never an exception.

    Hermetic: on a real Mac the framework exists, so the load is made to fail
    instead of relying on the host being something other than a Mac.
    """
    import ctypes

    def _no_framework(*_args: object, **_kwargs: object) -> object:
        raise OSError("Carbon.framework is not available")

    monkeypatch.setattr(ctypes.cdll, "LoadLibrary", _no_framework)
    assert quartz_mod._secure_event_input_enabled() is None


def test_stop_waits_for_a_start_that_is_already_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """A grant-driven start in a worker thread must not outlive the stop that follows."""
    import threading

    tcc, _fake = _darwin(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)
    backend = _backend()
    inside, release = threading.Event(), threading.Event()
    real_check = backend._permission_check

    def slow_check() -> bool:
        inside.set()
        assert release.wait(5), "the test never released the start"
        return real_check()

    backend._permission_check = slow_check
    starter = threading.Thread(target=backend.start)
    starter.start()
    assert inside.wait(5)
    stopper = threading.Thread(target=backend.stop)
    stopper.start()
    stopper.join(0.2)
    assert stopper.is_alive(), "stop() must wait for the start in flight"
    release.set()
    starter.join(5)
    stopper.join(5)
    assert not stopper.is_alive() and not starter.is_alive()
    assert backend.is_listening() is False, "no orphan tap after the stop"


def test_one_heard_event_ends_the_suspicion(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, fake = _listening_backend(monkeypatch)
    try:
        backend._armed_at = quartz_mod.time.monotonic() - 30.0
        backend._seconds_since_last_key = lambda: 5.0
        assert backend.deaf_tap_suspected() is True
        fake.deliver_key(0x00)
        assert backend.deaf_tap_suspected() is False
    finally:
        backend.stop()


def test_a_deaf_tap_is_never_restarted_automatically(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, fake = _listening_backend(monkeypatch)
    try:
        backend._armed_at = quartz_mod.time.monotonic() - 30.0
        backend._seconds_since_last_key = lambda: 5.0
        thread = backend._thread
        for _ in range(3):
            assert backend.deaf_tap_suspected() is True
        assert backend.is_listening() is True
        assert backend._thread is thread
        assert fake.tap.attempts == ["live"], "the hint must not recreate the tap"
    finally:
        backend.stop()


def test_a_restarted_tap_starts_a_fresh_count(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, fake = _listening_backend(monkeypatch)
    try:
        fake.deliver_key(0x00)
        assert backend.raw_event_count() == 1
        backend.stop()
        backend.start()
        assert backend.raw_event_count() == 0
    finally:
        backend.stop()


def _make_deaf(backend: QuartzHotkeyBackend) -> None:
    """The user typed after the tap came up and the tap heard nothing."""
    backend._armed_at = quartz_mod.time.monotonic() - 30.0
    backend._seconds_since_last_key = lambda: 5.0


def test_a_deaf_tap_is_reported_once_as_a_restart_hint_and_never_restarted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, fake = _listening_backend(monkeypatch)
    service = service_mod.get_permission_service()
    reports: list[tuple[str, str]] = []
    real_report = service.report_failed_use

    def spy(permission, *, feature, **kwargs):  # noqa: ANN001, ANN003
        reports.append((str(permission), feature))
        return real_report(permission, feature=feature, **kwargs)

    monkeypatch.setattr(service, "report_failed_use", spy)
    try:
        _make_deaf(backend)
        thread = backend._thread
        for _ in range(3):
            assert backend.report_if_deaf() is True  # still worth watching
        assert reports == [("input_monitoring", "global_shortcuts")], "once per tap"
        (episode,) = service.outstanding()
        assert (episode.permissions, episode.feature, episode.reason, episode.origin) == (
            ("input_monitoring",),
            "global_shortcuts",
            "restart_hint",
            "user",
        )
        # A hint: nothing was asked, nothing restarted.
        assert backend.is_listening() is True and backend._thread is thread
        assert fake.tap.attempts == ["live"]
        assert fake.tcc.requests() == []
    finally:
        backend.stop()


def test_a_tap_that_has_heard_events_is_never_reported_and_stops_the_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, fake = _listening_backend(monkeypatch)
    try:
        _make_deaf(backend)
        fake.deliver_key(0x00)
        assert backend.report_if_deaf() is False  # healthy: nothing left to watch
        assert service_mod.get_permission_service().outstanding() == []
    finally:
        backend.stop()


@pytest.mark.parametrize(
    "secure_input, idle",
    [(True, 5.0), (None, 5.0), (False, 600.0), (False, None)],
    ids=["secure_input", "flag_unreadable", "nobody_typed", "idle_unreadable"],
)
def test_the_deaf_rules_gate_the_report(
    monkeypatch: pytest.MonkeyPatch, secure_input: bool | None, idle: float | None
) -> None:
    backend, _fake = _listening_backend(monkeypatch)
    try:
        _make_deaf(backend)
        backend._secure_input_enabled = lambda: secure_input
        backend._seconds_since_last_key = lambda: idle
        assert backend.report_if_deaf() is True
        assert service_mod.get_permission_service().outstanding() == []
    finally:
        backend.stop()


def test_a_tap_that_is_not_listening_reads_nothing_and_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, _fake = _darwin(monkeypatch)
    backend = _backend()
    backend.start()  # nothing granted: no tap
    before = len(tcc.calls)
    assert backend.report_if_deaf() is True
    assert len(tcc.calls) == before, "no tap, so the liveness pass reads nothing"
    assert tcc.requests() == []
    assert service_mod.get_permission_service().outstanding() == []


def test_a_tap_that_starts_hearing_after_the_report_ends_the_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, fake = _listening_backend(monkeypatch)
    service = service_mod.get_permission_service()
    try:
        _make_deaf(backend)
        backend.report_if_deaf()
        assert [e.reason for e in service.outstanding()] == ["restart_hint"]

        fake.deliver_key(0x00)  # the tap hears the keyboard after all
        assert backend.report_if_deaf() is False
        service.refresh_episodes()
        assert service.outstanding() == []
    finally:
        backend.stop()


def test_a_restarted_tap_is_a_new_episode(monkeypatch: pytest.MonkeyPatch) -> None:
    backend, fake = _listening_backend(monkeypatch)
    service = service_mod.get_permission_service()
    try:
        _make_deaf(backend)
        backend.report_if_deaf()
        assert backend._deaf_reported is True
        assert [e.reason for e in service.outstanding()] == ["restart_hint"]
        backend.stop()
        backend.start()
        assert backend._deaf_reported is False
        # The episode the old tap opened is still open until a tap hears something.
        assert [e.reason for e in service.outstanding()] == ["restart_hint"]

        backend._secure_input_enabled = lambda: False
        fake.deliver_key(0x00)  # the NEW tap hears the keyboard
        assert backend.report_if_deaf() is False
        service.refresh_episodes()
        assert service.outstanding() == [], "a healthy new tap must end the old card"
    finally:
        backend.stop()


# --------------------------------------------------------------------------
# HotkeyTrigger: the grant re-arms the backend in process.
# --------------------------------------------------------------------------


async def _enter_trigger(monkeypatch: pytest.MonkeyPatch) -> HotkeyTrigger:
    import jarvis.trigger.hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod, "make_hotkey_backend", lambda: QuartzHotkeyBackend())
    trigger = HotkeyTrigger({"call": ["control+j"]})
    await trigger.__aenter__()
    return trigger


async def _settle(trigger: HotkeyTrigger) -> None:
    for _ in range(100):
        task = trigger._grant_task
        if task is not None and task.done():
            return
        await asyncio.sleep(0.01)


async def test_a_grant_after_boot_arms_the_tap_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, fake = _darwin(monkeypatch)
    service = service_mod.get_permission_service()
    trigger = await _enter_trigger(monkeypatch)
    try:
        assert trigger.listening() is False
        assert trigger.armed is False
        assert trigger.needs_input_monitoring is True
        assert fake.tap.attempts == []
        # The wait opened a background episode: no request, no dialog.
        assert tcc.requests() == [] and tcc.dialogs_shown() == []
        assert [e.permissions for e in service.outstanding()] == [("input_monitoring",)]

        tcc.grant(TccService.INPUT_MONITORING)  # the user flips the switch in System Settings
        service.invalidate()  # the 250 ms negative cache has long expired by the watcher's tick
        await asyncio.to_thread(service.refresh_episodes)
        await _settle(trigger)

        assert fake.tap.attempts == ["live"], "exactly one tap, created after the grant"
        assert trigger.armed is True
        assert trigger.needs_input_monitoring is False
        assert tcc.requests() == []
    finally:
        await trigger.__aexit__(None, None, None)


async def test_a_second_grant_signal_does_not_create_a_second_tap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, fake = _darwin(monkeypatch)
    trigger = await _enter_trigger(monkeypatch)
    try:
        tcc.grant(TccService.INPUT_MONITORING)
        service_mod.get_permission_service().invalidate()  # as the service's own detection does
        trigger._on_input_monitoring_granted()
        trigger._on_input_monitoring_granted()
        await asyncio.sleep(0.05)
        await _settle(trigger)
        trigger._on_input_monitoring_granted()
        await asyncio.sleep(0.05)
        await _settle(trigger)
        assert fake.tap.attempts == ["live"]
    finally:
        await trigger.__aexit__(None, None, None)


async def test_an_all_granted_upgrader_arms_at_boot_with_zero_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tcc, fake = _darwin(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)
    trigger = await _enter_trigger(monkeypatch)
    try:
        assert trigger.armed is True
        assert fake.tap.attempts == ["live"]
        assert tcc.requests() == [] and tcc.implicit_prompts() == []
        assert service_mod.get_permission_service().outstanding() == []
    finally:
        await trigger.__aexit__(None, None, None)


async def test_a_failing_rearm_never_leaks_out_of_its_task(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AP-18: a backend that raises in ``start`` is logged, not propagated."""
    tcc, _fake = _darwin(monkeypatch)
    trigger = await _enter_trigger(monkeypatch)
    try:
        backend = trigger._backend
        assert backend is not None

        def explode() -> None:
            raise RuntimeError("tap thread exploded")

        monkeypatch.setattr(backend, "start", explode)
        tcc.grant(TccService.INPUT_MONITORING)
        with caplog.at_level(logging.ERROR, logger="jarvis.trigger.hotkey"):
            trigger._on_input_monitoring_granted()
            await _settle(trigger)
        task = trigger._grant_task
        assert task is not None and task.done() and task.exception() is None
        assert any("Re-arming the global shortcuts" in r.message for r in caplog.records)
    finally:
        await trigger.__aexit__(None, None, None)


async def test_leaving_the_trigger_unsubscribes_its_grant_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _tcc, _fake = _darwin(monkeypatch)
    service = service_mod.get_permission_service()
    trigger = await _enter_trigger(monkeypatch)
    assert service._listeners.get(PermissionId.INPUT_MONITORING)
    await trigger.__aexit__(None, None, None)
    assert not service._listeners.get(PermissionId.INPUT_MONITORING)


async def test_a_grant_long_after_the_service_episode_would_have_expired_still_arms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trigger renews its background episode, so the 10-minute expiry never hides a grant."""
    import jarvis.trigger.hotkey as hotkey_mod

    tcc, fake = _darwin(monkeypatch)
    service = service_mod.get_permission_service()
    service._episode_ttl_s = 0.3  # stands in for the service's 600 s expiry
    monkeypatch.setattr(hotkey_mod, "_EPISODE_RENEW_S", 0.05)
    trigger = await _enter_trigger(monkeypatch)
    try:
        for _ in range(8):  # well past the expiry: each tick runs the service's own sweep
            await asyncio.sleep(0.1)
            await asyncio.to_thread(service.refresh_episodes)
        assert [e.permissions for e in service.outstanding()] == [("input_monitoring",)]
        assert fake.tap.attempts == []

        tcc.grant(TccService.INPUT_MONITORING)
        service.invalidate()
        await asyncio.to_thread(service.refresh_episodes)
        await _settle(trigger)
        assert fake.tap.attempts == ["live"], "the grant after the expiry window must re-arm"
        assert tcc.requests() == [] and tcc.dialogs_shown() == []
    finally:
        await trigger.__aexit__(None, None, None)


async def test_the_episode_renewal_task_ends_with_the_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.trigger.hotkey as hotkey_mod

    _tcc, _fake = _darwin(monkeypatch)
    monkeypatch.setattr(hotkey_mod, "_EPISODE_RENEW_S", 0.05)
    trigger = await _enter_trigger(monkeypatch)
    keepalive = trigger._keepalive_task
    assert keepalive is not None and not keepalive.done()
    await trigger.__aexit__(None, None, None)
    await asyncio.sleep(0)
    assert keepalive.done()
    assert trigger._keepalive_task is None


async def test_leaving_during_a_grant_rearm_leaves_no_orphan_tap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``__aexit__`` waits for the ``start`` running in a worker thread before ``stop``."""
    import threading

    tcc, fake = _darwin(monkeypatch)
    trigger = await _enter_trigger(monkeypatch)
    backend = trigger._backend
    assert backend is not None
    gate = threading.Event()
    real_start = backend.start

    def slow_start() -> None:
        assert gate.wait(5), "the test never released the start"
        real_start()

    monkeypatch.setattr(backend, "start", slow_start)
    tcc.grant(TccService.INPUT_MONITORING)
    service_mod.get_permission_service().invalidate()
    trigger._on_input_monitoring_granted()
    await asyncio.sleep(0.05)  # the start is now blocked inside its worker thread
    leaving = asyncio.create_task(trigger.__aexit__(None, None, None))
    await asyncio.sleep(0.05)
    assert not leaving.done(), "the exit must wait for the start in flight"
    gate.set()
    await asyncio.wait_for(leaving, 10)
    await asyncio.sleep(0.1)
    assert backend.is_listening() is False, "no live tap may outlive the trigger"
    assert fake.tap.attempts == ["live"]  # the start did finish, then the stop tore it down


async def test_a_grant_signal_after_leaving_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc, fake = _darwin(monkeypatch)
    trigger = await _enter_trigger(monkeypatch)
    await trigger.__aexit__(None, None, None)
    tcc.grant(TccService.INPUT_MONITORING)
    trigger._on_input_monitoring_granted()
    await asyncio.sleep(0.05)
    assert trigger._grant_task is None
    assert fake.tap.attempts == []


async def test_no_backend_and_the_noop_backend_report_that_nothing_listens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Esc pill must not promise a key when no listener exists (any OS)."""
    import jarvis.trigger.hotkey as hotkey_mod
    from jarvis.trigger.backends.noop import NoopBackend

    port, _tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    assert HotkeyTrigger({"cu_cancel": ["esc"]}).listening() is None, "not entered yet"

    monkeypatch.setattr(hotkey_mod, "make_hotkey_backend", lambda: NoopBackend())
    async with HotkeyTrigger({"cu_cancel": ["esc"]}) as noop:
        assert noop.listening() is False

    def refuse() -> None:
        raise RuntimeError("no backend")

    monkeypatch.setattr(hotkey_mod, "make_hotkey_backend", refuse)
    async with HotkeyTrigger({"cu_cancel": ["esc"]}) as failed:
        assert failed._backend is None
        assert failed.listening() is False


async def test_the_trigger_watches_a_listening_tap_and_reports_it_when_it_is_deaf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.trigger.hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod, "_LIVENESS_POLL_S", 0.01)
    tcc, fake = _darwin(monkeypatch)
    tcc.grant(TccService.INPUT_MONITORING)
    trigger = await _enter_trigger(monkeypatch)
    backend = trigger._backend
    assert backend is not None and trigger._liveness_task is not None
    try:
        backend._secure_input_enabled = lambda: False
        _make_deaf(backend)
        for _ in range(200):
            if service_mod.get_permission_service().outstanding():
                break
            await asyncio.sleep(0.01)
        (episode,) = service_mod.get_permission_service().outstanding()
        assert (episode.feature, episode.reason) == ("global_shortcuts", "restart_hint")
        assert fake.tap.attempts == ["live"], "the hint never recreates the tap"
        assert tcc.requests() == []
    finally:
        await trigger.__aexit__(None, None, None)
    assert trigger._liveness_task is None


async def test_boot_with_nothing_granted_runs_the_watch_without_touching_the_os(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.trigger.hotkey as hotkey_mod

    monkeypatch.setattr(hotkey_mod, "_LIVENESS_POLL_S", 0.01)
    tcc, fake = _darwin(monkeypatch)
    trigger = await _enter_trigger(monkeypatch)
    try:
        calls_before = len(tcc.calls)
        await asyncio.sleep(0.1)  # several liveness passes
        assert len(tcc.calls) == calls_before, "no tap, so nothing to read"
        assert fake.tap.attempts == [] and tcc.requests() == []
    finally:
        await trigger.__aexit__(None, None, None)


async def test_a_backend_without_the_liveness_probe_gets_no_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.trigger.hotkey as hotkey_mod

    port, tcc = make_non_darwin_port("win32")
    install_port(monkeypatch, port)
    monkeypatch.setattr(hotkey_mod, "make_hotkey_backend", lambda: _PlainBackend())
    async with HotkeyTrigger({"call": ["f3+f4"]}) as trigger:
        assert trigger._liveness_task is None
    tcc.assert_silent()


# --------------------------------------------------------------------------
# Off macOS nothing changes.
# --------------------------------------------------------------------------


class _PlainBackend:
    """A hand-written backend like the Windows poller: no permission probes."""

    def __init__(self) -> None:
        self.started = 0

    def register(self, bindings, on_event=None) -> None:  # noqa: ANN001
        return None

    def unregister(self) -> None:
        return None

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        return None

    def received_any_event(self) -> bool:
        return False

    def chord_is_down(self, combo: str) -> bool | None:
        return None


async def test_a_backend_without_a_permission_never_touches_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.trigger.hotkey as hotkey_mod

    port, tcc = make_non_darwin_port("win32")
    install_port(monkeypatch, port)
    monkeypatch.setattr(hotkey_mod, "make_hotkey_backend", lambda: _PlainBackend())
    trigger = HotkeyTrigger({"call": ["f3+f4"]})
    async with trigger:
        assert trigger.listening() is None, "cannot say: the pill keeps its old behaviour"
        assert trigger.armed is False
        assert trigger.needs_input_monitoring is False
        assert trigger.deaf_tap_suspected() is False
    tcc.assert_silent()
    assert service_mod._SERVICE is None or not service_mod._SERVICE._listeners


def test_off_macos_the_default_probe_reads_not_required_and_asks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    assert service_mod.get_permission_service().check(PermissionId.INPUT_MONITORING) is (
        PermissionState.NOT_REQUIRED
    )
    assert _macos_hotkey_permissions_granted() is True
    tcc.assert_silent()


def test_the_backend_factory_is_unchanged_per_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.platform import capabilities as caps_mod
    from jarvis.trigger import backends as backends_pkg
    from jarvis.trigger.backends.global_hotkeys import GlobalHotkeysBackend

    class _Caps:
        has_hotkey = True

    monkeypatch.setattr(caps_mod, "detect_capabilities", lambda: _Caps())
    import jarvis.platform as platform_mod

    monkeypatch.setattr(platform_mod, "detect_platform", lambda: "darwin")
    assert type(backends_pkg.make_hotkey_backend()) is QuartzHotkeyBackend
    monkeypatch.setattr(platform_mod, "detect_platform", lambda: "win32")
    assert type(backends_pkg.make_hotkey_backend()) is GlobalHotkeysBackend
