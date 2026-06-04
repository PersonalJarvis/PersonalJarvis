"""Process-local, out-of-band level channel for the TTS output amplitude.

Deliberately NOT the EventBus: amplitude updates fire several times per second
and would spam the flight-recorder wildcard subscriber (5 s cap). The
whisper-bar overlay registers a sink; the audio player publishes the per-flush
RMS. When no sink is registered, publishing is a cheap no-op (the player also
skips the RMS computation entirely via ``has_subscribers()``).
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from jarvis.audio.mic_level import LevelNormalizer

_log = logging.getLogger("jarvis.audio.level_tap")
_lock = threading.Lock()
_subscribers: list[Callable[[float], None]] = []

# Adaptive normalizer for the TTS output: raw speech RMS is only ~0.05-0.15, so
# publishing it un-normalized made the bars reach barely 10% (looked static).
# Same adaptive peak/gain as the mic path so Jarvis's voice drives full bars.
_norm = LevelNormalizer()


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


def publish(level: float) -> None:
    """Push a level in [0, 1] to all sinks. Clamps; swallows sink errors."""
    lv = 0.0 if level < 0.0 else 1.0 if level > 1.0 else float(level)
    with _lock:
        sinks = tuple(_subscribers)
    for sink in sinks:
        try:
            sink(lv)
        except Exception:  # noqa: BLE001 — a bad sink must never break audio
            _log.debug("level_tap sink failed", exc_info=True)


def feed(rms: float) -> None:
    """Normalize a raw TTS output RMS into a reactive 0..1 level and publish it.

    This is what the player should call (not ``publish``): the adaptive
    normalizer maps Jarvis's speech to the full bar range, mirroring the mic
    path. ``publish`` stays for raw passthrough / tests.
    """
    publish(_norm.push(float(rms)))


def reset() -> None:
    """Test helper: drop all subscribers and reset the normalizer."""
    with _lock:
        _subscribers.clear()
    _norm.reset()
