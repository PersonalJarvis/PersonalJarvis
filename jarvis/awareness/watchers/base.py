"""AwarenessWatcher protocol — lifecycle contract for all watchers.

Structural (``typing.Protocol``) rather than inheritance — consistent with
the plugin-system pattern in ``jarvis/core/protocols.py``. Watcher
implementations do NOT need Jarvis as a dependency; they satisfy the
protocol via method signatures.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AwarenessWatcher(Protocol):
    """Lifecycle contract for background watchers.

    Both methods are idempotent: calling ``start()`` twice is a no-op,
    calling ``stop()`` twice is a no-op. ``stop()`` may block for at most
    2 s — otherwise there is a risk of memory/handle leaks.
    """
    async def start(self) -> None:
        """Start background activity. Returns once running."""
        ...

    async def stop(self) -> None:
        """Stop cleanly within 2 s. Guarantees no hook/thread leak."""
        ...
