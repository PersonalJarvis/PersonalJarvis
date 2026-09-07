"""One cached answer to "where is this binary?", for the paths that ask often.

``shutil.which`` is not a lookup, it is a SEARCH: it walks every directory on
``PATH`` and, on Windows, tries every ``PATHEXT`` extension in each of them
until something matches. Measured on the maintainer's machine with 74 PATH
entries: 10-16 ms per call, and a MISS is the expensive case — nothing matches,
so every directory and every extension is visited before the answer is "no".

That would be unremarkable if it happened once. It does not. Opening one
Agentic-IDE pane resolves the agent's binary twice (once when the pane is added
to the grid, once when its PTY is spawned), the workspace's ``/agents`` sweep
resolves every registered CLI, and the CLI catalog prober resolves one per spec.
All of it runs on the shared event loop — the same loop that has to accept the
new panes' WebSockets and deliver the wake microphone — so the search does not
only cost its own milliseconds, it postpones the work it was asked about.
Measured: ``GET /api/agentic-ide/agents`` spent 92 ms of a warm response inside
this one function.

So the answer is remembered for :data:`TTL_S`.

**Why a TTL rather than a permanent cache.** A binary can appear (the user
installs a CLI) or vanish. A permanent cache would report a freshly installed
agent as missing for the rest of the session. A TTL bounds that to a few
seconds, and it is deliberately the same bound
:mod:`jarvis.workspace.agents` already gives its detection sweep — those two
answer the same question ("is this CLI here?") and must not disagree by more
than one of them is willing to be wrong.

**Why misses are cached too.** The miss is the slow case, and it is the common
one on a machine that has three of the eight registered CLIs installed. Caching
only hits would leave the expensive half uncached.

**Why the PATH is part of the key.** ``jarvis.core.path_augment.ensure_cli_paths``
appends install directories to ``os.environ["PATH"]`` while the app runs — a
GUI-launched process starts without Homebrew or the npm prefix on it. An answer
found before that happened is not an answer to the same question, so a changed
PATH simply misses the cache instead of returning something stale.

Cross-platform by construction: ``shutil.which`` is the only OS-specific part
and it is the standard library's problem, not this module's.
"""

from __future__ import annotations

import os
import shutil
import threading
import time

#: How long a resolved (or unresolved) binary location answers for.
#:
#: Matched to ``jarvis.workspace.agents._DETECTION_TTL_S`` on purpose: the
#: detection sweep and this cache answer the same question by different means,
#: and a user who installs a CLI from inside the app should not see one of them
#: catch up before the other. Anything that KNOWS the answer changed calls
#: :func:`invalidate` instead of waiting this out.
TTL_S = 30.0

#: Above this many remembered lookups, expired entries are swept before another
#: is added. The steady state is a dozen or so (one per registered CLI plus the
#: shells), so this is only ever reached when the PATH itself keeps changing —
#: every distinct PATH is its own key, and without a sweep those would
#: accumulate for the life of the process.
_MAX_ENTRIES = 256

#: name + PATH -> (answered_at, resolved path or None).
_cache: dict[tuple[str, str], tuple[float, str | None]] = {}
#: Guards ``_cache``. Reached from the event loop AND from worker threads (the
#: PTY spawn preparation runs in one), so a plain dict is not enough on its own.
_lock = threading.Lock()


def invalidate() -> None:
    """Forget every remembered location — the next lookup searches again.

    For callers that KNOW the answer just changed: a CLI installed or removed
    from inside the app. A user watching an install finish does not want to wait
    out :data:`TTL_S` to see the pane become available.
    """
    with _lock:
        _cache.clear()


def which(name: str) -> str | None:
    """``shutil.which(name)``, remembered for :data:`TTL_S`.

    Same contract as the standard library's: the absolute path of the
    executable, or ``None`` when it is not on ``PATH``. An empty name is
    ``None`` without touching the cache — that is a caller with nothing to look
    up, not a binary that is missing.

    This does NOT augment the PATH itself; a caller that needs the well-known
    install directories on it calls
    :func:`jarvis.core.path_augment.ensure_cli_paths` first, exactly as it did
    before this cache existed. Doing it here would put a filesystem sweep back
    on the path this function exists to keep cheap.
    """
    if not name:
        return None
    key = (name, os.environ.get("PATH", ""))
    now = time.monotonic()
    with _lock:
        remembered = _cache.get(key)
        if remembered is not None and now - remembered[0] < TTL_S:
            return remembered[1]

    # Deliberately OUTSIDE the lock: the search is the slow part, and holding a
    # process-wide lock across it would serialize every pane that is starting —
    # which is the burst this module exists to make cheap. Two callers racing on
    # the same name both search and both store the same answer; that costs one
    # redundant search and can never store a wrong one.
    found = shutil.which(name)

    with _lock:
        if len(_cache) >= _MAX_ENTRIES:
            stale = [k for k, (at, _) in _cache.items() if now - at >= TTL_S]
            for k in stale:
                del _cache[k]
            if len(_cache) >= _MAX_ENTRIES:
                # Nothing was stale — every entry is a live PATH variant. Start
                # over rather than grow without bound; the cost is one round of
                # re-searching, which is what this looked like before the cache.
                _cache.clear()
        _cache[key] = (now, found)
    return found


__all__ = ["TTL_S", "invalidate", "which"]
