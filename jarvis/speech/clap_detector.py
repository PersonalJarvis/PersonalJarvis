"""Double-clap activation — two hand claps wake Jarvis, like the wake word.

Deterministic signal processing on the wake microphone stream; no model, no
network, and no audio is kept beyond a few hundred milliseconds of analysis
state in memory. Loudness alone never triggers: a candidate must look like a
clap in shape AND spectrum, and two of them must form a double clap.

Per 16 ms frame (16 kHz mono int16):

1. **Noise floor** — an exponential average of frame RMS while nothing is
   happening, so a noisy room raises the bar instead of firing constantly.
2. **Onset** — RMS jumps above ``ONSET_OVER_FLOOR`` x floor AND above
   ``ONSET_OVER_PREVIOUS`` x the previous frame: a transient, not a swell.
3. **Shape** — the event peaks within ``MAX_RISE_FRAMES`` and decays to
   ``DECAY_RATIO`` of its peak within ``MAX_DECAY_FRAMES`` (~130 ms). Speech
   syllables and music notes sustain; a clap does not.
4. **Spectrum** — over the onset window the spectral centroid is at least
   ``MIN_CENTROID_HZ`` and the 2-8 kHz band holds at least
   ``MIN_HIGH_BAND_RATIO`` of the energy. A clap is broadband and bright;
   voiced speech concentrates below ~1 kHz.
5. **Pair** — a second clap ``MIN_GAP_S``-``MAX_GAP_S`` after the first, of
   similar strength, and no third clap within ``SERIES_GUARD_S`` (applause and
   drumming are series, not a double clap).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME = 256  # 16 ms

ONSET_OVER_FLOOR = 8.0
ONSET_OVER_PREVIOUS = 4.0
#: Absolute floor under the relative onset test (full scale = 1.0). Low on
#: purpose: a laptop microphone with noise suppression delivers a clap across
#: the room at ~0.01; the floor-relative and shape tests do the rejecting.
MIN_ONSET_RMS = 0.005
MAX_RISE_FRAMES = 2
MAX_DECAY_FRAMES = 8
DECAY_RATIO = 0.25
#: A clap rings for 30-60 ms: at least this many frames stay above
#: ``BODY_RATIO`` of the peak. A keystroke next to a laptop microphone is just
#: as bright and sudden but gone within one 16 ms frame.
MIN_BODY_FRAMES = 2
BODY_RATIO = 0.3
#: Measured through a laptop speaker/mic pair a clap still sat at 1.2-1.5 kHz
#: centroid with ~0.2-0.3 of its energy in 2-8 kHz; voiced speech sits lower.
MIN_CENTROID_HZ = 1200.0
MIN_HIGH_BAND_RATIO = 0.2
MIN_GAP_S = 0.2
MAX_GAP_S = 0.8
MAX_PEAK_RATIO = 3.0
SERIES_GUARD_S = 0.35
COOLDOWN_S = 2.0
#: After a series is recognised, claps are ignored until this long passes
#: without one — the rest of the applause must not pair up again.
SERIES_QUIET_S = 1.0
FLOOR_ALPHA = 0.02
MIN_FLOOR = 1e-4


@dataclass
class _Event:
    start_frame: int
    frames: list[np.ndarray] = field(default_factory=list)
    rms: list[float] = field(default_factory=list)


class ClapDetector:
    """Streaming double-clap detector. ``feed`` returns True on a double clap."""

    def __init__(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._frame_no = 0
        self._floor = MIN_FLOOR
        self._prev_rms = MIN_FLOOR
        self._prev2_rms = MIN_FLOOR
        self._event: _Event | None = None
        self._claps: list[tuple[float, float]] = []  # (time_s, peak_rms)
        self._pending_fire_at: float | None = None
        self._cooldown_until = 0.0
        self._series_until = 0.0
        self.stats = {"onsets": 0, "claps": 0, "rejected_shape": 0,
                      "rejected_spectrum": 0, "doubles": 0, "series": 0}

    # ------------------------------------------------------------------ core

    def feed(self, pcm: bytes) -> bool:
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self._buf = np.concatenate([self._buf, samples])
        fired = False
        while self._buf.size >= FRAME:
            frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
            if self._step(frame):
                fired = True
        return fired

    def _now(self) -> float:
        return self._frame_no * FRAME / SAMPLE_RATE

    def _step(self, frame: np.ndarray) -> bool:
        self._frame_no += 1
        rms = float(np.sqrt(np.mean(frame * frame)) + 1e-12)
        now = self._now()
        fired = False

        if self._pending_fire_at is not None and now >= self._pending_fire_at:
            self._pending_fire_at = None
            self._claps.clear()
            self._cooldown_until = now + COOLDOWN_S
            self.stats["doubles"] += 1
            fired = True

        if self._event is not None:
            ev = self._event
            ev.frames.append(frame)
            ev.rms.append(rms)
            peak = max(ev.rms)
            decayed = len(ev.rms) > 1 and rms <= DECAY_RATIO * peak
            if decayed or len(ev.rms) > MAX_DECAY_FRAMES:
                self._finish_event(ev, decayed)
                self._event = None
        elif (
            rms > MIN_ONSET_RMS
            and rms > ONSET_OVER_FLOOR * self._floor
            # A transient can straddle two 16 ms frames: compare with the
            # quieter of the two frames before it.
            and rms > ONSET_OVER_PREVIOUS * min(self._prev_rms, self._prev2_rms)
        ):
            self.stats["onsets"] += 1
            self._event = _Event(start_frame=self._frame_no, frames=[frame], rms=[rms])
        else:
            self._floor = max(MIN_FLOOR, (1 - FLOOR_ALPHA) * self._floor + FLOOR_ALPHA * rms)

        self._prev2_rms = self._prev_rms
        self._prev_rms = rms
        return fired

    def _finish_event(self, ev: _Event, decayed: bool) -> None:
        peak_idx = int(np.argmax(ev.rms))
        peak = max(ev.rms)
        body = sum(1 for r in ev.rms if r >= BODY_RATIO * peak)
        if not decayed or peak_idx > MAX_RISE_FRAMES or body < MIN_BODY_FRAMES:
            self.stats["rejected_shape"] += 1
            return
        window = np.concatenate(ev.frames[: peak_idx + 2])
        if not _bright_broadband(window):
            self.stats["rejected_spectrum"] += 1
            return
        t = ev.start_frame * FRAME / SAMPLE_RATE
        self.stats["claps"] += 1
        self._on_clap(t, max(ev.rms))

    def _on_clap(self, t: float, peak: float) -> None:
        if t < self._cooldown_until:
            return
        if t < self._series_until:
            self._series_until = t + SERIES_QUIET_S
            return
        if self._pending_fire_at is not None:
            # A third clap inside the guard: a series (applause), not a double.
            self._pending_fire_at = None
            self.stats["series"] += 1
            self._claps = []
            self._series_until = t + SERIES_QUIET_S
            return
        self._claps = [c for c in self._claps if t - c[0] <= MAX_GAP_S]
        if self._claps:
            t1, p1 = self._claps[-1]
            gap = t - t1
            ratio = max(p1, peak) / max(1e-9, min(p1, peak))
            if MIN_GAP_S <= gap <= MAX_GAP_S and ratio <= MAX_PEAK_RATIO:
                self._pending_fire_at = t + SERIES_GUARD_S
                return
        self._claps.append((t, peak))


def _bright_broadband(window: np.ndarray) -> bool:
    if window.size < 64:
        return False
    spectrum = np.abs(np.fft.rfft(window * np.hanning(window.size))) ** 2
    freqs = np.fft.rfftfreq(window.size, 1.0 / SAMPLE_RATE)
    total = float(spectrum.sum()) + 1e-12
    centroid = float((freqs * spectrum).sum() / total)
    high = float(spectrum[(freqs >= 2000) & (freqs <= 8000)].sum()) / total
    return centroid >= MIN_CENTROID_HZ and high >= MIN_HIGH_BAND_RATIO


async def detect_double_clap(chunks: AsyncIterator[object]) -> AsyncIterator[str]:
    """Consume wake-mic ``AudioChunk``s; yield ``"double"`` per double clap."""
    detector = ClapDetector()
    async for chunk in chunks:
        pcm = getattr(chunk, "pcm", None)
        if not pcm:
            continue
        if detector.feed(pcm):
            log.info("Double clap detected (%s)", detector.stats)
            yield "double"


__all__ = ["ClapDetector", "detect_double_clap"]
