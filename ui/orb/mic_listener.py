"""Microphone listener with a noise-gated logarithmic RMS level.

Emits an audio level (0..1) to a callback, ~50 Hz. The level is
mapped across a wide dB range and cleared of background noise via the shared
desktop level normalizer.

Design decision — no silero-vad / webrtcvad:
    For the pure "loud-enough-to-pulse" logic, a VAD library would be
    overkill (startup latency, extra model weight). Instead, the shared
    normalizer estimates a quiet-frame noise floor and maps the remaining RMS
    over a logarithmic range. This retains real quiet/loud differences while
    covering microphones with very different gains.

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

import logging
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from jarvis.audio.mic_level import LevelNormalizer

_log = logging.getLogger("jarvis.ui.orb.mic_listener")

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320


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

        self._normalizer = LevelNormalizer()

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
                _log.debug("Microphone level stream cleanup failed", exc_info=True)
            self._stream = None

    # --- PortAudio-Thread ----------------------------------------------

    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        # RMS of the 20ms frame. mono: indata.shape == (320, 1)
        rms = float(np.sqrt(np.mean(np.square(indata))))

        try:
            self._on_level(self._normalizer.push(rms))
        except Exception:
            # Callback errors must not kill the audio stream
            _log.debug("Microphone level callback failed", exc_info=True)
