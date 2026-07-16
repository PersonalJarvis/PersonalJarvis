"""Keep the indicator out of Computer-Use's own perception frames.

On Windows the sidecar windows carry ``WDA_EXCLUDEFROMCAPTURE`` and are
invisible to every grab — this module stays a no-op there. On macOS/Linux
no such API exists, so the controller registers a hook that hides the
border for the split second of each frame grab (blank → grab → unblank).

Fail-open by design: a missing hook, a dead sidecar, or a late ack must
NEVER delay or break the grab beyond the small ack timeout — a border
pixel in one frame is a cosmetic defect, a broken perception loop kills
the mission.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

# The registered hook is itself a contextmanager factory: entering it
# blanks the indicator, leaving it restores. ``None`` → no indicator.
_hook_lock = threading.Lock()
_hook: Callable[[], object] | None = None


def register_hook(hook: Callable[[], object]) -> None:
    global _hook
    with _hook_lock:
        _hook = hook


def unregister_hook() -> None:
    global _hook
    with _hook_lock:
        _hook = None


@contextmanager
def indicator_suppressed() -> Iterator[None]:
    """Wrap a frame grab; the indicator is hidden while inside (best effort)."""
    with _hook_lock:
        hook = _hook
    if hook is None:
        yield
        return
    try:
        cm = hook()
        enter = getattr(cm, "__enter__", None)
        exit_ = getattr(cm, "__exit__", None)
        if enter is None or exit_ is None:
            yield
            return
        enter()
    except Exception:  # noqa: BLE001 — fail-open
        yield
        return
    # The blank succeeded — run the grab exactly once and swallow ONLY the
    # unblank failure. The previous ``with cm: yield`` + ``except: yield``
    # shape resumed the generator into a SECOND yield whenever the hook's
    # __exit__ raised (dead sidecar, late ack), which @contextmanager turns
    # into ``RuntimeError: generator didn't stop`` — killing the very frame
    # grab this guard exists to protect (macOS/Linux path; Windows uses
    # WDA_EXCLUDEFROMCAPTURE and never registers a hook).
    try:
        yield
    finally:
        try:
            exit_(None, None, None)
        except Exception:  # noqa: BLE001, S110 — guard failure must not kill the grab
            pass


__all__ = ["indicator_suppressed", "register_hook", "unregister_hook"]
