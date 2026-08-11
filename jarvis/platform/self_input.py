"""Was Jarvis itself just typed? — so its own keystrokes cannot trigger it.

An OS-level hotkey listener cannot tell synthetic input from human input: a
session event tap (macOS), a polling matcher (Windows) and an X11 listener all
see the keystroke this very process just posted. So every global shortcut is
also armed against Jarvis's own actuation — and the collision is not exotic,
it is the DEFAULT on macOS:

    ``[hotkeys].dictate = "cmd"`` (a hold-to-dictate key) + the platform paste
    chord ``cmd+v``

Every dictation ended by pasting its own transcript, the tap saw the Cmd flag
rise, and the bare-Cmd dictation shortcut fired a fresh press — into a lane
that was still finishing, which refused it as ``already_running`` and painted
the Jarvis Bar's failure mark over a dictation that had just pasted perfectly
(live log, 2026-08-09 22:05:57). The same shape is one config away on Windows
(``ctrl`` as the dictation key next to ``ctrl+v``), and it is strictly worse
during a Computer-Use mission: there no dictation is running, so the phantom
press does not refuse — it STARTS a recording and takes the microphone.

The fix is a stamp, not a guess: the actuation layer marks the window in which
this process is synthesizing input, and the hotkey trigger drops the shortcut
edges that would START something inside it. It is deliberately time-boxed and
fail-open — a stamp that somehow never cleared must degrade to "hotkeys work",
never to a keyboard that has gone quietly dead.

Related but NOT the same: :mod:`jarvis.cu.indicator.self_input` answers "did
Jarvis type the ESCAPE that just arrived" for the mission-cancel listener, and
must stay that narrow — a user's own Esc has to cancel a mission even while
Jarvis is typing.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

#: How long after the last synthesized keystroke an arriving shortcut edge is
#: still attributed to us. It only has to cover the delivery lag between
#: ``CGEventPost`` (or ``SendInput``) and the listener that observes it — a few
#: milliseconds in practice. 250 ms is generous by two orders of magnitude and
#: still far below the reaction time of someone reaching for a shortcut after
#: watching Jarvis type.
SUPPRESS_WINDOW_MS = 250

_lock = threading.Lock()
#: Nesting depth: > 0 while a synthesis call is actually in flight, which is
#: what covers a ``type_text`` that runs for seconds. The timestamp alone could
#: not — it would expire in the middle of a long typing burst.
_depth = 0
_last_release_ns = 0


@contextmanager
def synthetic_input() -> Iterator[None]:
    """Mark the block that synthesizes keystrokes as ours.

    Re-entrant and thread-safe (actuation runs off the event loop, listeners
    run on their own threads). The window stays open for
    :data:`SUPPRESS_WINDOW_MS` after the block exits, because the listener sees
    the event slightly later than the call that posted it returns.
    """
    global _depth, _last_release_ns
    with _lock:
        _depth += 1
    try:
        yield
    finally:
        with _lock:
            _depth = max(0, _depth - 1)
            _last_release_ns = time.monotonic_ns()


def synthetic_input_recent(window_ms: int = SUPPRESS_WINDOW_MS) -> bool:
    """True while a keystroke arriving now is most likely one we just sent."""
    with _lock:
        depth = _depth
        stamp = _last_release_ns
    if depth > 0:
        return True
    if stamp == 0:
        return False
    return (time.monotonic_ns() - stamp) < window_ms * 1_000_000


def reset() -> None:
    """Test hook: forget the window and any leaked depth."""
    global _depth, _last_release_ns
    with _lock:
        _depth = 0
        _last_release_ns = 0


__all__ = [
    "SUPPRESS_WINDOW_MS",
    "reset",
    "synthetic_input",
    "synthetic_input_recent",
]
