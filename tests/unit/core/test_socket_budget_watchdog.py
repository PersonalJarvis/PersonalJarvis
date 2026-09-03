"""The socket watchdog must warn early, name a culprit, and never cry wolf.

The failure it exists for (BUG-215) is invisible after the fact: the machine
runs out of ephemeral ports, everything on it stops connecting, and two minutes
later TIME_WAIT expires and the evidence is gone. Windows logs its own event at
most once an hour, so the entry usually lands in the wrong minute and never
names a process.

So the value of this watchdog is entirely in WHEN it speaks and WHAT it says.
These tests pin both.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from jarvis.core import socket_budget
from jarvis.core.socket_budget import SocketBudgetWatchdog, SocketCensus


@pytest.fixture(autouse=True)
def _fake_ceiling(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A round 1000-socket ceiling, so a count reads as a percentage."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(socket_budget, "_probe_ephemeral_ports", lambda: (1000, "fake"))
    monkeypatch.setattr(socket_budget, "_probe_fd_limit", lambda: None)
    socket_budget.read_platform_limits(refresh=True)
    socket_budget.set_pressure_for_tests(0.0)
    yield
    socket_budget.read_platform_limits(refresh=True)
    socket_budget.set_pressure_for_tests(0.0)


def _census(total: int, **kwargs: Any) -> SocketCensus:
    kwargs.setdefault("lingering", total)
    return SocketCensus(total=total, **kwargs)


def _watchdog(total: int, reports: list[tuple[float, SocketCensus, Any]]) -> SocketBudgetWatchdog:
    return SocketBudgetWatchdog(
        census=lambda: _census(total),
        on_pressure=lambda fraction, census, limits: reports.append((fraction, census, limits)),
    )


def test_a_quiet_machine_says_nothing() -> None:
    """A watchdog that reports normal traffic gets muted, and then it is useless."""
    reports: list[Any] = []

    assert _watchdog(120, reports).tick() == pytest.approx(0.12)
    assert reports == []


def test_it_warns_before_the_pool_is_empty_not_after() -> None:
    """60 %, not 99 %.

    The report has to arrive while the culprit is still connecting. Written
    after exhaustion it names TIME_WAIT and nothing else, which is exactly the
    useless evidence this replaces.
    """
    reports: list[Any] = []

    fraction = _watchdog(650, reports).tick()

    assert fraction == pytest.approx(0.65)
    assert len(reports) == 1


def test_ongoing_pressure_is_re_reported_on_an_interval_not_every_tick() -> None:
    """Ten minutes of squeeze must leave a trail, not 20 identical blocks."""
    reports: list[Any] = []
    watchdog = _watchdog(900, reports)

    for _ in range(5):
        watchdog.tick()

    assert len(reports) == 1


def test_recovering_re_arms_the_warning() -> None:
    """A second squeeze after a calm spell is news again."""
    reports: list[Any] = []
    counts = iter([900, 100, 900])
    watchdog = SocketBudgetWatchdog(
        census=lambda: _census(next(counts)),
        on_pressure=lambda *args: reports.append(args),
    )

    watchdog.tick()
    watchdog.tick()
    watchdog.tick()

    assert len(reports) == 2


def test_optional_traffic_stands_down_only_once_it_is_really_tight() -> None:
    """Polls wait at 80 %; a voice turn never asks and is never refused."""
    reports: list[Any] = []

    _watchdog(700, reports).tick()
    assert socket_budget.should_defer_optional_io() is False

    _watchdog(850, reports).tick()
    assert socket_budget.should_defer_optional_io() is True


def test_the_report_names_where_the_sockets_went() -> None:
    """Without a destination the report is a number nobody can act on."""
    census = SocketCensus(
        total=900,
        lingering=880,
        by_state={"TIME_WAIT": 880, "ESTABLISHED": 20},
        top_destinations=[("127.0.0.1:47821", 400), ("::1:11434", 300)],
        top_processes=[(24364, 500)],
    )

    summary = census.summary()

    assert "127.0.0.1:47821" in summary
    assert "::1:11434" in summary
    assert "pid 24364" in summary
    assert "TIME_WAIT=880" in summary


def test_a_census_with_no_owners_says_so_instead_of_looking_empty() -> None:
    """Closed sockets keep the port and lose the owner — that is the answer.

    An empty "busiest processes" list reads as "nothing to see"; the truth is
    that the pool was emptied by churn, which is a different and more useful
    thing to know.
    """
    summary = SocketCensus(total=900, lingering=900).summary()

    assert "unattributed" in summary


def test_a_partial_census_admits_it_is_a_floor() -> None:
    """macOS refuses the system-wide table; a floor must not read as a total."""
    summary = SocketCensus(total=40, lingering=10, partial=True).summary()

    assert "own process only" in summary


def test_an_unreadable_census_is_not_pressure() -> None:
    """No psutil, no permission, no numbers — and so no throttling."""
    watchdog = SocketBudgetWatchdog(census=lambda: None)

    assert watchdog.tick() == 0.0
    assert socket_budget.should_defer_optional_io() is False


def test_a_reporter_that_throws_does_not_kill_the_watchdog() -> None:
    """The diagnostic must never become the outage."""

    def _boom(*_args: Any) -> None:
        raise RuntimeError("the log sink is gone")

    watchdog = SocketBudgetWatchdog(census=lambda: _census(900), on_pressure=_boom)

    assert watchdog.tick() == pytest.approx(0.9)


def test_start_and_stop_are_idempotent() -> None:
    """Arming twice must not leave a second thread sampling forever."""
    watchdog = SocketBudgetWatchdog(interval_s=1.0, census=lambda: _census(10))

    watchdog.start()
    watchdog.start()
    watchdog.stop()
    watchdog.stop()

    assert watchdog._thread is None
