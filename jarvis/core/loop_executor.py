"""A default executor that is already at full size when the loop first needs it.

``asyncio.to_thread`` is the app's standard way of keeping blocking work off the
event loop, and it is used in roughly 420 places. It is not, however, free of the
loop: every call goes through ``loop.run_in_executor(None, ...)``, which calls
``ThreadPoolExecutor._adjust_thread_count()``, and when no worker is idle and the
pool has not reached ``max_workers`` yet, that method calls ``Thread.start()``
**synchronously, on the calling thread** — the event loop. ``Thread.start()``
waits for the new thread to signal that it is running, so a host under load pays
that wait with the whole loop stopped.

Measured on the maintainer's box 2026-07-29 (``data/jarvis_desktop.log``), that
is not theoretical. Three of the session's worst stalls had this exact stack::

    audio/topology.py:225 watch_topology
      asyncio/threads.py:25 to_thread
        base_events.py:867 run_in_executor
          concurrent/futures/thread.py:203 _adjust_thread_count
            threading.py:999 Thread.start
              threading.py:655 wait          <-- 75.7 s

While the loop is stopped, every WebSocket frame, every HTTP route, every brain
turn and every terminal pane's output is stopped with it — which is what the user
reports as "the whole app is dead" and as a coding-CLI pane that never draws.

The fix is not to audit 420 call sites: it is to make the pool never have to
grow. :func:`install_prewarmed_default_executor` installs a pool of a known size
and brings every worker up **from a background thread**, so the one place where
``Thread.start()`` is unavoidable is a thread nobody is waiting on. Afterwards
``num_threads == max_workers`` forever, ``_adjust_thread_count`` returns without
starting anything, and ``to_thread`` costs the loop a queue append.

This does not make blocking work free — a call that blocks for a minute still
occupies a worker for a minute, and with every worker busy the next ``to_thread``
waits in the queue. That is the correct failure mode: work queues, the loop keeps
running, and the app stays answerable.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

#: Ceiling on the pool. Above this the threads cost more (stack reservation,
#: scheduler pressure) than the parallelism is worth: the work behind
#: ``to_thread`` here is filesystem, subprocess and native-library calls, none of
#: which get faster past a few dozen in flight. Matches CPython's own default
#: ceiling so the change is a shape change, not a capacity change.
MAX_WORKERS = 32

#: Floor, independent of core count. The pool exists to absorb BLOCKING calls,
#: and how many of those are in flight has nothing to do with how many cores the
#: host has — a 2-core VPS still probes audio devices, scans folders and shells
#: out to CLIs concurrently. CPython's ``cpu_count + 4`` would put such a host at
#: 6 workers and reintroduce the growth stall this module exists to remove.
MIN_WORKERS = 16

#: How long a prewarm worker parks before giving its thread back. Only an upper
#: bound for a gate that is released within milliseconds — it exists so a
#: prewarm that somehow loses its gate cannot pin the pool.
_PREWARM_TIMEOUT_S = 30.0


def default_worker_count(cpu_count: int | None = None) -> int:
    """How many workers to keep resident."""
    cores = cpu_count if cpu_count is not None else (os.cpu_count() or 4)
    return max(MIN_WORKERS, min(MAX_WORKERS, cores + 4))


def prewarm(pool: ThreadPoolExecutor, workers: int) -> threading.Thread:
    """Bring ``pool`` up to ``workers`` live threads, off the caller's thread.

    Returns the thread doing it, so a test can join. Growing a pool means
    ``Thread.start()`` per worker and that blocks whoever asks — so this asks
    from a thread of its own, which is the entire point.

    Each task parks on one shared gate so the pool cannot satisfy them by
    reusing a single worker; releasing the gate frees all of them at once.
    """

    def _fill() -> None:
        gate = threading.Event()
        try:
            futures = [pool.submit(gate.wait, _PREWARM_TIMEOUT_S) for _ in range(workers)]
        except RuntimeError:  # pragma: no cover — pool already shut down
            gate.set()
            return
        finally:
            # Before any wait on the futures: a submit that raised half-way
            # through must not leave the workers it did create parked.
            gate.set()
        for future in futures:
            try:
                future.result(timeout=_PREWARM_TIMEOUT_S)
            except Exception:  # noqa: BLE001,S110 - the worker exists either way,
                # which is all this is for; what it returned is of no interest.
                pass
        log.debug("Default executor prewarmed to %d workers", workers)

    thread = threading.Thread(target=_fill, name="executor-prewarm", daemon=True)
    thread.start()
    return thread


def install_prewarmed_default_executor(loop, *, workers: int | None = None):
    """Give ``loop`` a default executor that will never grow under it.

    Call once, before the loop starts running. Returns the pool so the owner can
    shut it down; the prewarm continues in the background and never gates the
    caller — filling it is exactly the blocking work this module keeps off the
    critical path (AP-26).
    """
    count = workers if workers is not None else default_worker_count()
    pool = ThreadPoolExecutor(max_workers=count, thread_name_prefix="jarvis-io")
    loop.set_default_executor(pool)
    prewarm(pool, count)
    return pool


__all__ = [
    "MAX_WORKERS",
    "MIN_WORKERS",
    "default_worker_count",
    "install_prewarmed_default_executor",
    "prewarm",
]
