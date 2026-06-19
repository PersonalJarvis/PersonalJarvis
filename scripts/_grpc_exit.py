"""Hard-exit helper for scripts that make real Gemini / Vertex calls.

Root cause (measured 2026-06-11, scripts/diag_threads2.py): the ``google-genai``
gRPC clients leak NON-DAEMON background threads — ``asyncio_0`` (gRPC C-core
poller) after a Vertex TTS call and ``Thread-N (_connection_worker_thread)``
after a Gemini brain call. gRPC never marks these daemon and our code never
closes the channels, so once a script's work is done the interpreter blocks on
``threading._shutdown`` waiting for threads that never finish. The process then
sits idle (CPU ~0) until the shell timeout kills it — observed as a ~28-minute
"running" shell for a job that finished in ~11 s.

``hard_exit`` flushes the streams and calls ``os._exit``, which terminates the
process immediately without waiting on the zombie gRPC threads. Safe for these
scripts: they have already printed + flushed their results and hold no critical
unsynced state. NOT for the long-running app (it intentionally never exits) and
NOT a substitute for closing clients in library code.
"""
from __future__ import annotations

import os
import sys


def hard_exit(code: int | None = 0) -> None:
    """Flush stdio and terminate now, skipping the zombie-gRPC-thread join."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(int(code or 0))
