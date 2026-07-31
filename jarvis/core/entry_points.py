"""One cached read of the installed entry-point catalogue, shared process-wide.

``importlib.metadata.entry_points()`` is not a lookup — it is a filesystem
sweep. Every call re-walks ``sys.path``, enumerates every installed
distribution and opens each one's ``entry_points.txt``. On a full Jarvis
install that is 535 distributions; measured here it costs ~30 ms once the OS
file cache is warm and **624 ms cold**, and a cold sweep under disk load has
been seen to take seconds.

That would be merely wasteful if it happened once. It does not: plugin
resolution asks per candidate, so a single cross-family fallback chain pays for
it once per provider it probes, and the callers sit on the asyncio event loop —
the same loop that serves every WebSocket, HTTP route and terminal pane. A
16.5 s loop stall was captured with exactly this call at the bottom of the
stack (``_dictation_session`` → ``build_stt_from_config`` →
``_load_provider_class`` → ``entry_points()`` → ``read_text`` → ``io.open``),
which is why this module exists.

Caching for the life of the process is correct rather than merely convenient:
the catalogue only changes when a distribution is installed or removed, and a
new entry-point does not take effect in an already-running interpreter anyway
(hence CLAUDE.md §10's ``pip install -e . --no-deps`` followed by a restart).
:func:`invalidate` is provided for the one case that does need it — a test, or
an in-process install that wants the next read to see new plugins.
"""

from __future__ import annotations

import threading
from importlib import metadata

__all__ = ["entry_points_for", "invalidate"]

# Guards the fill, not the read. Without it, the several subsystems that resolve
# plugins concurrently during boot would each pay the cold sweep in parallel —
# the one moment the sweep is at its most expensive.
_LOCK = threading.Lock()
_CACHE: dict[str, tuple[metadata.EntryPoint, ...]] = {}


def entry_points_for(group: str) -> tuple[metadata.EntryPoint, ...]:
    """Every entry-point declared in ``group``, read from disk at most once.

    Args:
        group: An entry-point group name, e.g. ``"jarvis.stt"``.

    Returns:
        The group's entry-points as an immutable tuple. Empty when the group
        has no registered plugins — indistinguishable, deliberately, from a
        group nobody declared: both mean "nothing to load here".
    """
    cached = _CACHE.get(group)
    if cached is not None:
        return cached

    with _LOCK:
        # A second caller may have filled it while this one waited.
        cached = _CACHE.get(group)
        if cached is not None:
            return cached
        resolved = tuple(metadata.entry_points(group=group))
        _CACHE[group] = resolved
        return resolved


def invalidate() -> None:
    """Drop the cache so the next read goes back to disk.

    For tests, and for the rare in-process install that adds plugins. Callers
    that merely want fresh *behaviour* from an existing plugin do not need
    this — the catalogue caches the entry-point declarations, never the loaded
    modules.
    """
    with _LOCK:
        _CACHE.clear()
