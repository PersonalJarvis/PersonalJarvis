"""Process-local microphone-loudness channel for overlay equalizers.

The VAD frame loop (which already reads every captured frame for STT) feeds the
raw per-frame RMS via :func:`feed`; a stateful normalizer — adaptive noise
floor + peak auto-gain + attack/release smoothing — turns it into a reactive
0..1 level. Subscribers (the orb / whisper-bar, via ``OrbBusBridge``) render it
as live bars that move with your voice.

Why this and not a second mic stream: the old path opened a SECOND
``sd.InputStream`` (on the default device) while the STT pipeline already had
the real mic open — fragile on Windows MME and never actually wired into the
LISTENING transition. Tapping the audio that is ALREADY flowing is exactly what
Wispr Flow does, and it's a single stream, no device conflict.

Deliberately NOT the EventBus (same rationale as ``level_tap``): ~30 Hz level
samples would spam the flight-recorder. Zero-cost when nobody subscribes — the
caller gates on :func:`has_subscribers`.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable

_log = logging.getLogger("jarvis.audio.mic_level")
_lock = threading.Lock()
_subscribers: list[Callable[[float], None]] = []

# Floors — prevent div-by-~0 and runaway gain on absolute silence (muted mic).
_MIN_NOISE_FLOOR = 0.001
_MIN_PEAK = 0.01


class LevelNormalizer:
    """RMS → 0..1 level. Lifted from MicListener: adaptive noise floor (EMA on
    quiet frames), peak auto-gain (fast attack, slow decay), and attack-fast /
    release-slow output smoothing so the bars pulse naturally instead of
    flickering."""

    def __init__(self) -> None:
        self._noise_floor = 0.005
        self._peak = 0.05
        self._smoothed = 0.0

    def push(self, rms: float) -> float:
        if rms < self._noise_floor * 1.5:
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * rms
        self._noise_floor = max(self._noise_floor, _MIN_NOISE_FLOOR)

        speech_threshold = self._noise_floor * 3.0
        gated = max(0.0, rms - speech_threshold)

        if gated > self._peak:
            self._peak = gated
        else:
            self._peak *= 0.997
        self._peak = max(self._peak, _MIN_PEAK)

        raw = min(1.0, gated / self._peak)

        if raw > self._smoothed:  # attack fast
            self._smoothed = 0.4 * self._smoothed + 0.6 * raw
        else:  # release slow
            self._smoothed = 0.75 * self._smoothed + 0.25 * raw
        return self._smoothed

    def reset(self) -> None:
        self._noise_floor = 0.005
        self._peak = 0.05
        self._smoothed = 0.0


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


def feed(rms: float) -> None:
    """Normalize a raw per-frame RMS (float, [0,1] scale) and publish the 0..1
    level to all sinks. Swallows sink errors so a bad UI handler never breaks
    audio capture."""
    level = _norm.push(float(rms))
    with _lock:
        sinks = tuple(_subscribers)
    for sink in sinks:
        try:
            sink(level)
        except Exception:  # noqa: BLE001 — a bad sink must never break capture
            _log.debug("mic_level sink failed", exc_info=True)


def reset() -> None:
    """Reset the adaptive normalizer (e.g. at the start of a fresh session)."""
    _norm.reset()


def reset_for_tests() -> None:
    """Test helper: drop all subscribers and reset the normalizer."""
    with _lock:
        _subscribers.clear()
    _norm.reset()
