"""The socket ceiling must be readable on every OS Jarvis ships to.

BUG-215 was an outage of the whole machine caused by running the operating
system out of ephemeral ports. The watchdog that catches it can only do so if
it knows how many there are — and every platform answers that question through
a different door, in a different unit, with a different thing that actually
runs out first:

===========  ==========================================  =======================
System       Where the number lives                      What binds first
===========  ==========================================  =======================
Windows      ``netsh int ipv4 show dynamicport tcp``     the port pool
Linux        ``/proc/sys/net/ipv4/ip_local_port_range``  usually RLIMIT_NOFILE
macOS        ``sysctl net.inet.ip.portrange.first/last`` the port pool
===========  ==========================================  =======================

These tests fake each host in turn, because CI runs on one of them at a time
and the two it is not running on are exactly where a silent regression hides.

The Windows case has its own trap and its own test: ``netsh`` translates its
own output. A parser that looks for the words "Start Port" reports "no limit"
on every non-English Windows in the install base — which is most of them, and
which was the maintainer's own machine.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from jarvis.core import socket_budget


@pytest.fixture(autouse=True)
def _fresh_limits() -> Any:
    """Each test gets an unprobed module.

    Both the ceiling and the last-measured pressure are process-wide on
    purpose — every caller reads one number — so a test that leaves either
    behind is a test that decides the next one's answer.
    """
    socket_budget.read_platform_limits(refresh=True)
    socket_budget.set_pressure_for_tests(0.0)
    yield
    socket_budget.read_platform_limits(refresh=True)
    socket_budget.set_pressure_for_tests(0.0)


def _fake_run(stdout: str) -> Any:
    def _run(*_args: Any, **_kwargs: Any) -> Any:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    return _run


# ── Windows ──────────────────────────────────────────────────────────────
_NETSH_EN = """
Protocol tcp Dynamic Port Range
---------------------------------
Start Port      : 49152
Number of Ports : 16384
"""

_NETSH_DE = """
Protokoll tcp Dynamischer Portbereich
---------------------------------
Startport      : 49152
Anzahl von Ports : 16384
"""

_NETSH_JA = """
プロトコル tcp 動的ポート範囲
---------------------------------
開始ポート          : 49152
ポート数            : 16384
"""


@pytest.mark.parametrize(
    ("name", "output"),
    [("english", _NETSH_EN), ("german", _NETSH_DE), ("japanese", _NETSH_JA)],
)
def test_windows_port_pool_is_read_in_any_language(
    name: str, output: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pool size is the second integer, whatever the labels say.

    This is the whole reason the parser reads numbers instead of field names.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", _fake_run(output))

    limits = socket_budget.read_platform_limits(refresh=True)

    assert limits.ephemeral_ports == 16384, f"{name} netsh output was misread"
    assert "netsh" in limits.source


def test_windows_without_netsh_falls_back_to_the_documented_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked netsh must not silence the watchdog entirely."""
    monkeypatch.setattr(sys, "platform", "win32")

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise FileNotFoundError("netsh")

    monkeypatch.setattr(subprocess, "run", _boom)

    limits = socket_budget.read_platform_limits(refresh=True)

    assert limits.ephemeral_ports == 16384
    assert "assumed" in limits.source


def test_windows_has_no_file_descriptor_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no RLIMIT_NOFILE there, and inventing one would be a lie."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", _fake_run(_NETSH_EN))
    monkeypatch.setitem(sys.modules, "resource", None)

    limits = socket_budget.read_platform_limits(refresh=True)

    assert limits.fd_limit is None
    assert limits.ceiling == 16384


# ── Linux ────────────────────────────────────────────────────────────────
def test_linux_reads_the_proc_range(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    proc = tmp_path / "ip_local_port_range"
    proc.write_text("32768\t60999\n", encoding="utf-8")
    monkeypatch.setattr(socket_budget, "_linux_port_pool", lambda: (28232, str(proc)))

    limits = socket_budget.read_platform_limits(refresh=True)

    assert limits.ephemeral_ports == 28232


def test_the_tighter_of_the_two_limits_is_the_one_that_binds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slim container has 28 232 ports and 1024 file descriptors.

    The ports are irrelevant there: the process dies of EMFILE first. A
    watchdog measuring against the larger number would report 4 % pressure
    while the backend was already refusing connections.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(socket_budget, "_probe_ephemeral_ports", lambda: (28232, "fake /proc"))
    monkeypatch.setattr(socket_budget, "_probe_fd_limit", lambda: 1024)

    limits = socket_budget.read_platform_limits(refresh=True)

    assert limits.ceiling == 1024


def test_an_unlimited_descriptor_ceiling_does_not_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(socket_budget, "_probe_ephemeral_ports", lambda: (28232, "fake /proc"))
    monkeypatch.setattr(socket_budget, "_probe_fd_limit", lambda: None)

    assert socket_budget.read_platform_limits(refresh=True).ceiling == 28232


# ── macOS ────────────────────────────────────────────────────────────────
def test_macos_reads_the_sysctl_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", _fake_run("49152\n65535\n"))
    monkeypatch.setattr(socket_budget, "_probe_fd_limit", lambda: None)

    limits = socket_budget.read_platform_limits(refresh=True)

    assert limits.ephemeral_ports == 16384
    assert "sysctl" in limits.source


def test_macos_without_sysctl_still_reports_a_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    def _boom(*_a: Any, **_kw: Any) -> Any:
        raise FileNotFoundError("sysctl")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(socket_budget, "_probe_fd_limit", lambda: None)

    assert socket_budget.read_platform_limits(refresh=True).ephemeral_ports == 16384


# ── An OS nobody planned for ─────────────────────────────────────────────
def test_an_unknown_platform_degrades_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ceiling means no watchdog — never a crash, and never a false alarm."""
    monkeypatch.setattr(sys, "platform", "sunos5")
    monkeypatch.setattr(socket_budget, "_probe_fd_limit", lambda: None)

    limits = socket_budget.read_platform_limits(refresh=True)

    assert limits.ceiling is None
    assert "sunos5" in limits.source


def test_no_ceiling_never_throttles_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    """A watchdog that cannot see must not be what stops the app working."""
    monkeypatch.setattr(sys, "platform", "sunos5")
    monkeypatch.setattr(socket_budget, "_probe_fd_limit", lambda: None)
    # The ceiling is cached for the life of the process (reading it costs a
    # netsh spawn on Windows), so the fake host has to be probed explicitly.
    assert socket_budget.read_platform_limits(refresh=True).ceiling is None
    watchdog = socket_budget.SocketBudgetWatchdog(
        census=lambda: socket_budget.SocketCensus(total=99_999, lingering=99_999)
    )

    assert watchdog.tick() == 0.0
    assert socket_budget.should_defer_optional_io() is False
