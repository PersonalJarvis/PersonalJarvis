"""Process-local envelope record of the audio Jarvis itself just played.

The desktop surfaces are half-duplex because raw PortAudio capture has no
portable acoustic echo cancellation — yet the one signal that separates the
assistant's own speaker echo from a genuine user barge-in is the OUTPUT audio
itself, and this process owns that signal end to end. ``AudioPlayer`` records
one (timestamp, duration, RMS) sample per ~60 ms write block here; the
barge-in detector correlates a confirmed speech candidate against this
envelope before it is allowed to cancel playback (BUG-101).

Deliberately NOT the EventBus (several samples per second, see
``level_tap``) and deliberately dependency-light: plain floats under one
lock, bounded ring, cheap no-ops when nothing has played. All platforms share
this path — the discriminator is arithmetic on audio the process produced,
never an OS audio API.
"""

from __future__ import annotations

import threading
import time
from collections import deque

# ~60 ms per player write block → 512 entries cover ~30 s, far beyond any
# realistic device-latency lag window while staying tiny.
_MAX_ENTRIES = 512

_lock = threading.Lock()
# (monotonic timestamp at write-return, block duration in s, block RMS 0..1)
_entries: deque[tuple[float, float, float]] = deque(maxlen=_MAX_ENTRIES)


def record(rms: float, duration_s: float, *, timestamp: float | None = None) -> None:
    """Append one played output block's RMS to the envelope record.

    ``timestamp`` defaults to ``time.monotonic()`` at call time — the player
    calls this right after the blocking ``stream.write`` returns, so the stamp
    marks when the block entered the device buffer (audibility lags by the
    device's output latency, which the correlator's lag search covers).
    """
    if duration_s <= 0.0:
        return
    stamp = time.monotonic() if timestamp is None else float(timestamp)
    with _lock:
        _entries.append((stamp, float(duration_s), max(0.0, float(rms))))


def snapshot(window_s: float) -> list[tuple[float, float, float]]:
    """Return (timestamp, duration, rms) entries from the last ``window_s``.

    Oldest first. An empty list means nothing was played recently — callers
    must fail open (no echo judgment without a reference).
    """
    if window_s <= 0.0:
        return []
    horizon = time.monotonic() - float(window_s)
    with _lock:
        return [entry for entry in _entries if entry[0] >= horizon]


def reset() -> None:
    """Drop all recorded entries (tests, device teardown)."""
    with _lock:
        _entries.clear()


__all__ = ["record", "snapshot", "reset"]
