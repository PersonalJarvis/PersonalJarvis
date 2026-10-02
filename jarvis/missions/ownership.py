"""Which process owns a running mission, and is that process still alive?

Recovery used to judge an unfinished mission only by how recently it was
active: anything with an event or heartbeat younger than
``RECOVERY_STALE_AFTER_MS`` (30 min) was "presumed owned by a live instance".
That guess is right when a second instance shares ``missions.db`` with a live
first one, but wrong after a plain app restart: the old process — and with it
the mission's worker — is gone, yet the new instance kept the mission RUNNING
for the full 30 minutes plus one re-sweep interval (live forensic 2026-10-02,
mission 01a0fcc3: worker killed by a restart at 15:19:40, swept to
FAILED('crash_recovery') only at 15:56:42, no artifact ever written).

The live orchestrator therefore stamps its own process identity next to the
heartbeat. Recovery asks :func:`owner_is_alive` about that stamp: a provably
dead owner means the mission is orphaned right now, whatever its timestamps
say. Anything uncertain (no stamp yet, psutil missing, access denied) answers
``None`` and recovery falls back to the old freshness guard, so a mission a
live instance is running is never swept on a guess.

The identity is ``(pid, start_ms)``. The process start time guards against PID
reuse: a new, unrelated process that inherited the dead owner's PID has a
different start time and does not count as the owner.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# psutil rounds create_time differently across platforms; two readings of the
# same process can differ by a few milliseconds.
_START_TIME_TOLERANCE_MS = 1_000

_current_identity: tuple[int, int] | None = None


def _process_start_ms(pid: int) -> int | None:
    """Start time of ``pid`` in epoch milliseconds, or None if unknowable."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        return int(psutil.Process(pid).create_time() * 1000)
    except psutil.NoSuchProcess:
        raise
    except psutil.Error as exc:
        log.debug("mission ownership: cannot read start time of pid %s: %s", pid, exc)
        return None


def current_process_identity() -> tuple[int, int]:
    """``(pid, start_ms)`` of this process; ``start_ms`` is 0 when unknown."""
    global _current_identity
    pid = os.getpid()
    if _current_identity is None or _current_identity[0] != pid:
        try:
            start_ms = _process_start_ms(pid) or 0
        except Exception as exc:  # noqa: BLE001 - our own pid always exists; stay best-effort
            log.debug("mission ownership: own start time unavailable: %s", exc)
            start_ms = 0
        _current_identity = (pid, start_ms)
    return _current_identity


def owner_is_alive(pid: int, start_ms: int) -> bool | None:
    """Whether the process that stamped ``(pid, start_ms)`` is still running.

    Returns ``True`` (alive), ``False`` (provably gone, or its PID now belongs
    to a different process) or ``None`` (no stamp or no way to tell).
    """
    if pid <= 0:
        return None
    if pid == os.getpid():
        own_start = current_process_identity()[1]
        if not start_ms or not own_start:
            return True
        return abs(own_start - start_ms) <= _START_TIME_TOLERANCE_MS
    try:
        import psutil
    except ImportError:
        return None
    try:
        actual_start = _process_start_ms(pid)
    except psutil.NoSuchProcess:
        return False
    except Exception as exc:  # noqa: BLE001 - an unreadable owner must count as "unknown", not "dead"
        log.debug("mission ownership: liveness of pid %s unknown: %s", pid, exc)
        return None
    if actual_start is None:
        # The process exists but its start time is unreadable (access denied):
        # it may well be the owner, so do not declare it dead.
        return None
    if not start_ms:
        return None
    return abs(actual_start - start_ms) <= _START_TIME_TOLERANCE_MS
