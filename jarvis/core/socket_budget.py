"""Notice when the machine is running out of sockets — and say who took them.

Every outbound connection borrows an *ephemeral port* from a pool the operating
system owns, and hands it back only after the close lingers through TIME_WAIT.
Drain that pool and nothing on the machine can connect any more: not this app,
not the file manager, not the start menu, not the browser. The user does not
report "the socket pool is empty". They report that the whole computer went
unusable for a few minutes and then fixed itself — because TIME_WAIT expires on
its own and the pool refills with nobody having learned anything.

That is BUG-215, and it had been happening on the maintainer's machine almost
daily since 2026-08-04 without a single line anywhere naming a cause. Windows
logs event 4231 when the pool runs dry, but it throttles that to roughly one an
hour, so the entry rarely lands in the minute the freeze did — and it never
says which process was responsible.

So this watchdog lives OFF the event loop, in a plain daemon thread, and
answers the question the operating system will not: how much of the pool is
gone, and *to where*. When the pool gets tight it writes a census — states,
the busiest destinations, the busiest processes — into the log, which is where
the next person to hit this will actually look.

Three operating systems, three different ceilings, one shared failure:

===========  ==================  ==========  =====================================
System       Ephemeral ports     TIME_WAIT   What gives out first
===========  ==================  ==========  =====================================
Windows      16 384 (default)    120 s       the port pool — the whole desktop stalls
Linux        28 232 (default)    60 s        usually ``RLIMIT_NOFILE`` first, which
                                             kills the process instead of the desktop
macOS        16 384 (default)    15 s        the port pool, but it recovers fastest
===========  ==================  ==========  =====================================

Windows is the hard case: the smallest pool and the longest linger. Code that
stays inside the budget there is safe everywhere, which is why the thresholds
below are expressed as a FRACTION of whatever the host actually reports rather
than as a fixed number of sockets.

Usage — the app arms it once and forgets it::

    from jarvis.core.socket_budget import SocketBudgetWatchdog

    watchdog = SocketBudgetWatchdog()
    watchdog.start()

Anything that is about to open a connection it could just as well open later
asks first::

    from jarvis.core.socket_budget import should_defer_optional_io

    if should_defer_optional_io():
        return cached_answer  # the pool is tight; a poll can wait a tick
"""

from __future__ import annotations

import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

#: How often the pool is counted. The census walks the host's whole socket
#: table, so it is not free; 30 s is often enough to catch a storm that lasts
#: minutes and rare enough to disappear into the noise.
DEFAULT_INTERVAL_S: Final[float] = 30.0

#: Fraction of the pool in use above which the census goes into the log. Two
#: thirds gone is not yet a failure, but it is already far outside anything a
#: healthy idle machine does, and the warning has to arrive while the culprit
#: is still connecting — a report written after exhaustion names TIME_WAIT and
#: nothing else.
DEFAULT_WARN_AT: Final[float] = 0.60

#: Fraction above which optional traffic stands down. Polls, inventory sweeps
#: and readiness probes can all wait a tick; a voice turn cannot. Degrade
#: honestly, never fail hard.
DEFAULT_DEFER_AT: Final[float] = 0.80

#: An ongoing squeeze is re-reported at this interval rather than every tick,
#: so ten minutes of pressure leave a readable trail instead of 20 identical
#: censuses.
DEFAULT_REPEAT_S: Final[float] = 300.0

#: Documented defaults, used when the host refuses to say. Better a slightly
#: wrong ceiling than no watchdog at all — the fraction still moves, and the
#: log line says the number was assumed.
_ASSUMED_PORTS: Final[dict[str, int]] = {"win32": 16384, "darwin": 16384, "linux": 28232}

#: How many rows of each breakdown the census keeps. Enough to name a culprit,
#: short enough that the log line stays readable.
_TOP_N: Final[int] = 5


@dataclass(frozen=True, slots=True)
class PlatformLimits:
    """What this host says its socket ceiling is.

    Args:
        ephemeral_ports: Size of the outbound port pool, or ``None`` when the
            host would not say.
        fd_limit: Per-process open-file ceiling, or ``None`` on hosts without
            one. On Linux this is routinely the tighter of the two — a stock
            ``python:3.11-slim`` allows 1024 open files, well under the 28 232
            ports it advertises.
        source: Where the numbers came from, quoted verbatim in the log so a
            surprising ceiling can be traced instead of doubted.
    """

    ephemeral_ports: int | None
    fd_limit: int | None
    source: str

    @property
    def ceiling(self) -> int | None:
        """The limit that actually binds: the smaller of the two, if any."""
        candidates = [n for n in (self.ephemeral_ports, self.fd_limit) if n]
        return min(candidates) if candidates else None


@dataclass(frozen=True, slots=True)
class SocketCensus:
    """One count of the host's sockets, with enough detail to blame someone.

    Args:
        total: Sockets seen, all states.
        lingering: Sockets in TIME_WAIT/CLOSE_WAIT — closed, but still holding
            a port. A pool emptied by churn is almost entirely this.
        by_state: Count per connection state.
        top_destinations: Busiest ``host:port`` targets, most first.
        top_processes: Busiest ``pid`` owners, most first. Empty on a host that
            will not attribute sockets without elevation (macOS).
        partial: True when the host refused the full table and only this
            process's own sockets were counted, so ``total`` is a floor.
    """

    total: int
    lingering: int
    by_state: dict[str, int] = field(default_factory=dict)
    top_destinations: list[tuple[str, int]] = field(default_factory=list)
    top_processes: list[tuple[int, int]] = field(default_factory=list)
    partial: bool = False

    def summary(self) -> str:
        """One multi-line block naming the pressure and its likeliest cause."""
        states = ", ".join(f"{name}={count}" for name, count in sorted(self.by_state.items()))
        destinations = (
            "\n".join(f"    {target}  x{count}" for target, count in self.top_destinations)
            or "    (none)"
        )
        processes = (
            "\n".join(f"    pid {pid}  x{count}" for pid, count in self.top_processes)
            # A closed socket keeps its port but loses its owner, so the busiest
            # destination is the only handle on a pool emptied by pure churn.
            or "    (unattributed — closed sockets keep the port, not the owner)"
        )
        scope = " (own process only — the host refused the full table)" if self.partial else ""
        return (
            f"  sockets: {self.total} total, {self.lingering} lingering{scope}\n"
            f"  states: {states}\n"
            f"  busiest destinations:\n{destinations}\n"
            f"  busiest processes:\n{processes}"
        )


# ----------------------------------------------------------------------
# Reading the host's ceiling
# ----------------------------------------------------------------------
_limits_cache: PlatformLimits | None = None
_limits_lock = threading.Lock()


def read_platform_limits(*, refresh: bool = False) -> PlatformLimits:
    """The host's socket ceiling, probed once and remembered.

    The pool size does not change while the process runs — and on Windows
    reading it costs a ``netsh`` spawn — so this is cached. ``refresh`` is for
    the tests, which need each fake host read fresh.
    """
    global _limits_cache
    with _limits_lock:
        if _limits_cache is not None and not refresh:
            return _limits_cache
        limits = _probe_platform_limits()
        _limits_cache = limits
        return limits


def _probe_platform_limits() -> PlatformLimits:
    ports, source = _probe_ephemeral_ports()
    fd_limit = _probe_fd_limit()
    if ports is None:
        assumed = _ASSUMED_PORTS.get(sys.platform)
        if assumed is not None:
            ports = assumed
            source = f"{source}; port pool assumed to be the {sys.platform} default"
    return PlatformLimits(ephemeral_ports=ports, fd_limit=fd_limit, source=source)


def _probe_ephemeral_ports() -> tuple[int | None, str]:
    """Size of the outbound port pool, per platform. ``None`` when unreadable."""
    if sys.platform == "win32":
        return _windows_port_pool()
    if sys.platform == "darwin":
        return _macos_port_pool()
    if sys.platform.startswith("linux"):
        return _linux_port_pool()
    return None, f"no port-pool probe for {sys.platform}"


def _windows_port_pool() -> tuple[int | None, str]:
    """Read the dynamic port range out of ``netsh``.

    Parsed as "the first two integers in the output" rather than by label:
    ``netsh`` translates its own field names, so a German host answers
    ``Startport`` / ``Anzahl von Ports`` and a label-matching parser reports no
    limit at all on every non-English Windows in the install base.
    """
    import re
    import subprocess

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            ["netsh", "int", "ipv4", "show", "dynamicport", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except Exception as exc:  # noqa: BLE001 - a missing/blocked netsh is not fatal
        return None, f"netsh unavailable ({type(exc).__name__})"
    numbers = [int(match) for match in re.findall(r"\d+", completed.stdout)]
    if len(numbers) < 2:
        return None, "netsh answered without a start/count pair"
    # Order is start, count — the same on every localisation.
    return numbers[1], f"netsh dynamicport tcp (start {numbers[0]})"


def _linux_port_pool() -> tuple[int | None, str]:
    from pathlib import Path

    path = Path("/proc/sys/net/ipv4/ip_local_port_range")
    try:
        low, high = (int(part) for part in path.read_text(encoding="utf-8").split()[:2])
    except Exception as exc:  # noqa: BLE001 - a container without /proc is fine
        return None, f"{path} unreadable ({type(exc).__name__})"
    return max(0, high - low + 1), str(path)


def _macos_port_pool() -> tuple[int | None, str]:
    import subprocess

    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            ["sysctl", "-n", "net.inet.ip.portrange.first", "net.inet.ip.portrange.last"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5.0,
        )
        low, high = (int(line) for line in completed.stdout.split()[:2])
    except Exception as exc:  # noqa: BLE001 - sysctl absent or renamed
        return None, f"sysctl net.inet.ip.portrange unreadable ({type(exc).__name__})"
    return max(0, high - low + 1), "sysctl net.inet.ip.portrange"


def _probe_fd_limit() -> int | None:
    """The per-process open-file ceiling, where the platform has one.

    Windows has no ``RLIMIT_NOFILE``; its handle ceiling is far above anything
    a socket storm reaches, so there is nothing to report there.
    """
    try:
        import resource
    except ImportError:
        return None  # Windows — no such limit, and none worth inventing.
    try:
        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception as exc:  # noqa: BLE001 - an exotic host without the limit
        from loguru import logger

        logger.debug("Socket budget: RLIMIT_NOFILE unreadable ({}).", exc)
        return None
    if soft == resource.RLIM_INFINITY:
        return None
    return int(soft)


# ----------------------------------------------------------------------
# Counting what is in use
# ----------------------------------------------------------------------
#: States that no longer carry traffic but still own their port. These are what
#: a churn storm leaves behind, and what makes the pool refill on its own.
_LINGERING: Final[frozenset[str]] = frozenset({"TIME_WAIT", "CLOSE_WAIT", "FIN_WAIT2", "LAST_ACK"})


def take_census() -> SocketCensus | None:
    """Count the host's sockets. ``None`` when psutil cannot look at all."""
    try:
        import psutil  # lazy (AP-26): nothing on the boot critical path
    except ImportError:
        from loguru import logger

        logger.debug("Socket budget: psutil not installed; the pool goes uncounted.")
        return None

    partial = False
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        # macOS refuses the system-wide table to an unprivileged process. Our
        # own sockets are still a real signal — this app is the one whose
        # behaviour we can change — so count those and say the number is a floor.
        partial = True
        try:
            connections = psutil.Process().net_connections(kind="inet")
        except Exception as exc:  # noqa: BLE001 - nothing left to fall back to
            from loguru import logger

            logger.debug("Socket budget: no socket table available ({}).", exc)
            return None
    except Exception as exc:  # noqa: BLE001 - a census must never take the app down
        from loguru import logger

        logger.debug("Socket budget: census failed ({}).", exc)
        return None

    states: Counter[str] = Counter()
    destinations: Counter[str] = Counter()
    processes: Counter[int] = Counter()
    lingering = 0
    for conn in connections:
        state = getattr(conn, "status", None) or "NONE"
        states[state] += 1
        if state in _LINGERING:
            lingering += 1
        remote = getattr(conn, "raddr", None)
        if remote:
            destinations[f"{remote[0]}:{remote[1]}"] += 1
        pid = getattr(conn, "pid", None)
        if pid:
            processes[int(pid)] += 1
    return SocketCensus(
        total=len(connections),
        lingering=lingering,
        by_state=dict(states),
        top_destinations=destinations.most_common(_TOP_N),
        top_processes=processes.most_common(_TOP_N),
        partial=partial,
    )


# ----------------------------------------------------------------------
# The pressure everyone else reads
# ----------------------------------------------------------------------
_pressure = 0.0
_pressure_lock = threading.Lock()


def current_pressure() -> float:
    """Fraction of the socket ceiling in use, 0.0-1.0, as last measured.

    Zero until the first census lands, and zero on a host whose ceiling could
    not be read — a number nobody can compute must never throttle anything.
    """
    with _pressure_lock:
        return _pressure


def set_pressure_for_tests(value: float) -> None:
    """Force the reading. Tests only; nothing in the app calls this."""
    global _pressure
    with _pressure_lock:
        _pressure = max(0.0, float(value))


def should_defer_optional_io(*, threshold: float = DEFAULT_DEFER_AT) -> bool:
    """Is the pool too tight for traffic that could just as well wait?

    Asked by pollers, inventory sweeps and readiness probes — never by a voice
    turn, a brain call or anything the user is waiting on. The default answer
    on an unmeasured host is False: a watchdog that cannot see must not be the
    thing that stops the app from working.
    """
    return current_pressure() >= threshold


class SocketBudgetWatchdog:
    """Sample the socket pool from a thread nothing else can block.

    Args:
        interval_s: Seconds between censuses.
        warn_at: Fraction of the ceiling above which the census is logged.
        repeat_s: How often ongoing pressure is re-reported.
        on_pressure: Receives ``(fraction, census, limits)``. Defaults to a
            loguru warning. Injected so the behaviour is testable without
            asserting on log output.
        census: Injected census function, for the same reason.
    """

    def __init__(
        self,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        warn_at: float = DEFAULT_WARN_AT,
        repeat_s: float = DEFAULT_REPEAT_S,
        on_pressure: Callable[[float, SocketCensus, PlatformLimits], None] | None = None,
        census: Callable[[], SocketCensus | None] | None = None,
    ) -> None:
        self._interval_s = max(1.0, float(interval_s))
        self._warn_at = max(0.0, min(1.0, float(warn_at)))
        self._repeat_s = max(self._interval_s, float(repeat_s))
        self._on_pressure = on_pressure or _log_pressure
        self._census = census or take_census
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reported_at: float | None = None

    def start(self) -> None:
        """Begin sampling. Idempotent."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="jarvis-socket-budget",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        """Stop sampling and wait briefly for the thread to unwind."""
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def tick(self) -> float:
        """One census. Returns the pressure it measured; 0.0 when unmeasurable.

        Separate from the loop so a test can drive a single sample without a
        thread and without waiting out an interval.
        """
        limits = read_platform_limits()
        ceiling = limits.ceiling
        census = self._census()
        if census is None or not ceiling:
            return 0.0
        fraction = census.total / float(ceiling)
        global _pressure
        with _pressure_lock:
            _pressure = fraction
        if fraction < self._warn_at:
            self._reported_at = None
            return fraction
        now = time.monotonic()
        if self._reported_at is not None and now - self._reported_at < self._repeat_s:
            return fraction
        self._reported_at = now
        try:
            self._on_pressure(fraction, census, limits)
        except Exception:  # noqa: BLE001, S110 - reporting never kills the watchdog
            pass
        return fraction

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - a watchdog never takes the app down
                from loguru import logger

                logger.opt(exception=True).debug("Socket-budget tick failed.")
            if self._stop.wait(self._interval_s):
                return


#: The one watchdog per process, so every entry point arms the same thread.
_default: SocketBudgetWatchdog | None = None
_default_lock = threading.Lock()


def start_default_watchdog() -> SocketBudgetWatchdog | None:
    """Arm the process-wide watchdog. Idempotent; never fatal.

    Called from both entry points, because both can be the process that empties
    the pool: the desktop shell (which drives the windows whose sockets storm)
    and a headless run (which polls exactly the same loops on a server with a
    far tighter file-descriptor ceiling).

    Nothing is measured here — the first census is a full interval away — so
    this stays off the boot critical path (AP-26).
    """
    global _default
    with _default_lock:
        if _default is not None:
            return _default
        try:
            watchdog = SocketBudgetWatchdog()
            watchdog.start()
        except Exception:  # noqa: BLE001 — a diagnostic must never break boot
            from loguru import logger

            logger.opt(exception=True).debug("Socket-budget watchdog not armed.")
            return None
        _default = watchdog
        return watchdog


def stop_default_watchdog() -> None:
    """Retire the process-wide watchdog, if one was armed."""
    global _default
    with _default_lock:
        watchdog = _default
        _default = None
    if watchdog is not None:
        try:
            watchdog.stop()
        except Exception:  # noqa: BLE001, S110 — teardown of a diagnostic
            pass


def _log_pressure(fraction: float, census: SocketCensus, limits: PlatformLimits) -> None:
    """Default reporter: the number, the ceiling it is measured against, the census."""
    from loguru import logger

    logger.warning(
        "Socket pool {:.0%} used ({} of ~{}, per {}). Empty this and NOTHING on "
        "the machine can connect until the closed ones time out, which reads "
        "to the user as the whole computer freezing and then healing itself "
        "(BUG-215). Census:\n{}",
        fraction,
        census.total,
        limits.ceiling,
        limits.source,
        census.summary(),
    )


__all__ = [
    "DEFAULT_DEFER_AT",
    "DEFAULT_INTERVAL_S",
    "DEFAULT_REPEAT_S",
    "DEFAULT_WARN_AT",
    "PlatformLimits",
    "SocketBudgetWatchdog",
    "SocketCensus",
    "current_pressure",
    "read_platform_limits",
    "set_pressure_for_tests",
    "should_defer_optional_io",
    "start_default_watchdog",
    "stop_default_watchdog",
    "take_census",
]
