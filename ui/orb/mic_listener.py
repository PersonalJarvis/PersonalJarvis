"""Microphone listener with RMS level + adaptive noise gating.

Emits an audio level (0..1) to a callback, ~50 Hz. The level is
normalized to an adaptive peak and cleared of background noise
(fan, keyboard, coffee machine) via noise gating.

Design decision — no silero-vad / webrtcvad:
    For the pure "loud-enough-to-pulse" logic, a VAD library would be
    overkill (startup latency, extra model weight). Instead, adaptive
    noise-floor estimation via EMA: quiet frames slowly pull the floor
    down, the speech threshold is 3x the floor → self-calibrates within
    a few seconds to the room + mic gain. Peak tracking with auto-decay
    provides the amplitude normalization.

    If a hard speech/non-speech classification is needed later
    (e.g. for a wake-word trigger), silero-vad can be added in a separate
    listener — the MicListener stays level-only.

Threading contract:
    PortAudio calls the internal callback from its own thread. The
    on_level callback the caller passes in is invoked from this
    thread — so it must be short and thread-safe. For
    OrbOverlay.set_level() this is fine (just a float assignment, atomic
    under the GIL).
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320

# Minimum floor — prevents division by ~0 and unbounded runaway growth
# on absolutely silent input (a muted mic).
_MIN_NOISE_FLOOR = 0.001
_MIN_PEAK = 0.01


class MicListener:
    """Non-blocking mic capture with a level callback."""

    def __init__(
        self,
        on_level: Callable[[float], None],
        device: int | str | None = None,
    ) -> None:
        self._on_level = on_level
        self._device = device
        self._stream: sd.InputStream | None = None

        # Adaptive noise floor: adjusts to the room's ambient loudness
        self._noise_floor: float = 0.005
        # Adaptive peak: normalizes loud words to 1.0
        self._peak: float = 0.05
        # Smoothed output level for a natural pulsing feel
        self._smoothed: float = 0.0

    def start(self) -> None:
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=FRAME_SAMPLES,
            device=self._device,
            callback=self._on_audio,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # --- PortAudio-Thread ----------------------------------------------

    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        # RMS of the 20ms frame. mono: indata.shape == (320, 1)
        rms = float(np.sqrt(np.mean(np.square(indata))))

        # Only update the noise floor on quiet frames (EMA). This prevents
        # loud speech from pulling the floor up and then gating everything.
        if rms < self._noise_floor * 1.5:
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * rms
        self._noise_floor = max(self._noise_floor, _MIN_NOISE_FLOOR)

        # Speech gate: below 3x the noise floor counts as "no speech".
        speech_threshold = self._noise_floor * 3.0
        gated = max(0.0, rms - speech_threshold)

        # Peak tracking: fast rise, slow decay → auto-gain.
        # 0.997^50 ≈ 0.86 decay per second, i.e. the peak halves in ~3 s
        # once nothing loud comes in anymore → subsequent normal
        # speech is quickly scaled back to a "full swing" again.
        if gated > self._peak:
            self._peak = gated
        else:
            self._peak *= 0.997
        self._peak = max(self._peak, _MIN_PEAK)

        raw_level = min(1.0, gated / self._peak)

        # Attack-fast, release-slow — feels like natural pulsing.
        # Without release smoothing the orb "flickers".
        if raw_level > self._smoothed:
            self._smoothed = 0.4 * self._smoothed + 0.6 * raw_level
        else:
            self._smoothed = 0.75 * self._smoothed + 0.25 * raw_level

        try:
            self._on_level(self._smoothed)
        except Exception:
            # Callback errors must not kill the audio stream
            pass
