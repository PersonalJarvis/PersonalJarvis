"""Process-local, out-of-band level channel for the TTS output amplitude.

Deliberately NOT the EventBus: amplitude updates fire several times per second
and would spam the flight-recorder wildcard subscriber (5 s cap). The
jarvis-bar overlay registers a sink; the audio player publishes the per-flush
RMS. When no sink is registered, publishing is a cheap no-op (the player also
skips the RMS computation entirely via ``has_subscribers()``).

Sync contract: ``stream.write()`` returns when PortAudio ACCEPTS a block, not
when the device makes it audible — the gap is the reported output latency
(hundreds of ms on Bluetooth). A level published at write time therefore leads
the heard voice, and the last ~latency of every sentence plays with no levels
at all (the bars died before the voice finished). ``feed``/``publish`` accept
``delay_s`` so the player can schedule each block's level for the moment it
becomes audible; a single lazy dispatcher thread delivers due levels.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from jarvis.audio.mic_level import LevelNormalizer

_log = logging.getLogger("jarvis.audio.level_tap")
_lock = threading.Lock()

# Delays at or below this are published synchronously — sub-frame lead is
# imperceptible and keeps the no-latency path (tests, headless, DirectSound
# with tiny buffers) free of any thread machinery.
_SYNC_DELAY_S = 0.005

# monotonic() timestamp until which TTS audio is known to be on the output
# device. The player only feeds a level at buffer-write time (a brief instant),
# then stream.write() BLOCKS for the whole multi-second playback with no further
# level. So the level alone can't tell the UI "Jarvis is speaking right now".
# note_playing() records the playback END so the jarvis bar can show the
# speaking equalizer for the ENTIRE sentence, not just the write instant.
_audible_until = 0.0
_subscribers: list[Callable[[float], None]] = []

# Shared logarithmic normalizer for TTS output: raw speech RMS is only
# ~0.05-0.15, so publishing it un-normalized made the bars reach barely 10%.
# The wide dB range also preserves sentence dynamics instead of flattening each
# newly observed peak to full scale.
_norm = LevelNormalizer()

# Delayed-delivery state: (due_monotonic, level) in append order. Delays within
# one playback share the same device latency, so the deque stays effectively
# sorted; a mid-stream latency change can misorder neighbors by a few ms, which
# is visually irrelevant. The dispatcher thread is created lazily on the first
# delayed feed and then parks on the condition while idle.
_pending: deque[tuple[float, float]] = deque()
_pending_cond = threading.Condition(_lock)
_dispatcher: threading.Thread | None = None


def _dispatch_loop() -> None:
    while True:
        with _pending_cond:
            while not _pending:
                _pending_cond.wait()
            due, level = _pending[0]
            wait_s = due - time.monotonic()
            if wait_s > 0.0:
                # Re-check after the timed wait: reset_playing() may have
                # cleared the queue, or an earlier-due item may have arrived.
                _pending_cond.wait(timeout=wait_s)
                continue
            _pending.popleft()
        publish(level)


def _schedule(level: float, delay_s: float) -> None:
    global _dispatcher
    due = time.monotonic() + delay_s
    with _pending_cond:
        _pending.append((due, level))
        if _dispatcher is None or not _dispatcher.is_alive():
            _dispatcher = threading.Thread(
                target=_dispatch_loop, name="level-tap-dispatch", daemon=True
            )
            _dispatcher.start()
        _pending_cond.notify()


def subscribe(sink: Callable[[float], None]) -> Callable[[], None]:
    """Register a level sink. Returns an unsubscribe callable."""
    with _lock:
        _subscribers.append(sink)

    def _unsub() -> None:
        with _lock:
            try:
                _subscribers.remove(sink)
            except ValueError:
                pass

    return _unsub


def has_subscribers() -> bool:
    with _lock:
        return bool(_subscribers)


def publish(level: float, delay_s: float = 0.0) -> None:
    """Push a level in [0, 1] to all sinks. Clamps; swallows sink errors.

    ``delay_s`` > ``_SYNC_DELAY_S`` defers delivery by that long (see the
    module docstring's sync contract) via the lazy dispatcher thread.
    """
    lv = 0.0 if level < 0.0 else 1.0 if level > 1.0 else float(level)
    if delay_s > _SYNC_DELAY_S:
        _schedule(lv, float(delay_s))
        return
    with _lock:
        sinks = tuple(_subscribers)
    for sink in sinks:
        try:
            sink(lv)
        except Exception:  # noqa: BLE001 — a bad sink must never break audio
            _log.debug("level_tap sink failed", exc_info=True)


def feed(rms: float, delay_s: float = 0.0) -> None:
    """Normalize a raw TTS output RMS into a reactive 0..1 level and publish it.

    This is what the player should call (not ``publish``): the logarithmic
    normalizer maps Jarvis's speech across the full bar range, mirroring the
    mic path. Pass the device's reported output latency as ``delay_s`` so the
    level lands when the block is HEARD, not when PortAudio accepted it.
    ``publish`` stays for raw passthrough / tests. The normalizer runs at feed
    time (its envelope tracks the audio stream order), only delivery is
    deferred.
    """
    publish(_norm.push(float(rms)), delay_s)


def note_playing(duration_s: float) -> None:
    """Record that ``duration_s`` of TTS audio is about to play on the device.

    Called by the player right before each blocking ``stream.write`` so the UI
    knows audio is audible for the whole block, not just the write instant. Uses
    ``max`` so overlapping/back-to-back blocks extend the window monotonically.
    """
    global _audible_until
    if duration_s <= 0.0:
        return
    _audible_until = max(_audible_until, time.monotonic() + duration_s)


def playback_active() -> bool:
    """True while TTS audio is known to be playing on the output device."""
    return time.monotonic() < _audible_until


def reset_playing() -> None:
    """Clear the playback window (barge-in / stop): audio was aborted, so the
    UI must not keep showing the speaking equalizer for the cancelled tail.
    Pending delayed levels belong to that cancelled tail too — drop them and
    push an honest zero so the bars collapse with the sound."""
    global _audible_until
    _audible_until = 0.0
    with _pending_cond:
        had_pending = bool(_pending)
        _pending.clear()
        _pending_cond.notify()
    if had_pending:
        publish(0.0)


def reset() -> None:
    """Test helper: drop all subscribers and reset the normalizer."""
    global _audible_until
    with _lock:
        _subscribers.clear()
        _pending.clear()
        _pending_cond.notify()
    _norm.reset()
    _audible_until = 0.0
