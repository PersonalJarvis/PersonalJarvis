"""One writer lock per store path, shared by every instance pointing at it.

Why this exists
---------------
The dictation sidecars (:mod:`jarvis.dictation.history`,
:mod:`jarvis.dictation.stats`) are read-modify-write stores: load the whole
JSON file, change one thing, write it back atomically. That is only safe if
the load and the write are one indivisible step.

A lock held on the *instance* cannot provide that here, because nothing keeps
an instance around: the REST routes and the speech pipeline each construct a
fresh ``DictationHistory()`` / ``DictationStats()`` per operation. Two of them
serialise against nothing at all, so the last :func:`os.replace` silently
wins — a Restore can be erased by a dictation that happened to finish a
moment later. The file was never torn, just quietly reverted, which is the
kind of loss nobody reports because it looks like it never happened.

So the lock belongs to the PATH, not to the object holding it. This registry
hands out one lock per resolved store path; two instances built from two
spellings of the same file get the same lock.

Deadlock, and why there is none
-------------------------------
``DictationHistory.add`` holds the history lock while it feeds the statistics
sidecar, so the only nesting that exists runs history -> stats, never the
reverse (nothing in the statistics module knows the history exists). Those are
two different files and therefore two different locks. The one way to make
them the same lock is to point both stores at one path, which is why these are
re-entrant: that configuration then blocks nothing instead of deadlocking a
dictation.

Scope: this serialises threads inside ONE process, which is what the app is.
Two separate processes writing the same sidecar still race — the atomic
tempfile + ``os.replace`` keeps the file readable, it just does not keep both
updates.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

#: Guards the registry itself. Held only long enough to look up or insert.
_REGISTRY_GUARD = threading.Lock()
#: Resolved store path -> its lock. Never evicted: a real install has a
#: handful of distinct sidecar paths, and a lock cannot be weak-referenced
#: (``_thread.RLock`` does not support it), so an eviction scheme would have
#: to guess when a writer is done. A few dozen bytes per path is the cheaper
#: side of that trade.
_LOCKS: dict[str, threading.RLock] = {}


def lock_key(path: Path | str) -> str:
    """The registry key for ``path`` — resolved and case-normalised.

    ``resolve`` collapses ``..`` segments and symlinks so two spellings of one
    file share a lock; ``normcase`` finishes the job on Windows, where paths
    are case-insensitive and ``History.json`` is the same file as
    ``history.json``. On POSIX ``normcase`` is the identity function, so the
    case-sensitive filesystem keeps its semantics.

    A path that cannot be resolved (a vanished parent, a permission error)
    falls back to its literal form rather than raising: a lock key is never
    worth failing a write over.
    """
    candidate = Path(path)
    try:
        resolved = candidate.resolve()
    except (OSError, ValueError, RuntimeError):
        resolved = candidate
    return os.path.normcase(str(resolved))


def store_lock(path: Path | str) -> threading.RLock:
    """The one lock for ``path``. Same file in, same lock object out."""
    key = lock_key(path)
    with _REGISTRY_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


__all__ = ["lock_key", "store_lock"]
