"""Microphone-Listener mit RMS-Level + adaptivem Noise-Gating.

Emittiert Audio-Level (0..1) an einen Callback, ~50 Hz. Der Level ist
normalisiert auf einen adaptiven Peak und durch Noise-Gating von
Hintergrundrauschen (Luefter, Tastatur, Kaffeemaschine) befreit.

Designentscheidung — kein silero-vad / webrtcvad:
    Fuer die reine "laut-genug-zum-Pulsieren"-Logik waere eine VAD-Library
    Overkill (Startup-Latenz, extra Modellgewicht). Stattdessen adaptive
    Noise-Floor-Schaetzung per EMA: leise Frames ziehen den Floor langsam
    nach, Speech-Threshold ist 3x Floor → self-kalibriert innerhalb
    weniger Sekunden an Raum + Mic-Gain. Peak-Tracking mit Auto-Decay
    liefert die Amplituden-Normalisierung.

    Wenn spaeter harte Speech/Non-Speech-Klassifikation gebraucht wird
    (z.B. fuer Wake-Word-Trigger), kann silero-vad in einem separaten
    Listener dazugenommen werden — der MicListener bleibt Level-only.

Threading-Kontrakt:
    PortAudio ruft den internen Callback aus seinem eigenen Thread. Der
    on_level-Callback, den der Aufrufer uebergibt, wird aus diesem
    Thread aufgerufen — er muss also kurz und thread-safe sein. Fuer
    OrbOverlay.set_level() ist das OK (nur eine Float-Zuweisung, atomic
    unter GIL).
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320

# Minimaler Floor — verhindert Division durch ~0 und unendliches Hochlaufen
# bei absolut stillen Eingaengen (stummgeschaltetes Mic).
_MIN_NOISE_FLOOR = 0.001
_MIN_PEAK = 0.01


class MicListener:
    """Nicht-blockender Mic-Capture mit Level-Callback."""

    def __init__(
        self,
        on_level: Callable[[float], None],
        device: int | str | None = None,
    ) -> None:
        self._on_level = on_level
        self._device = device
        self._stream: sd.InputStream | None = None

        # Adaptiver Noise-Floor: passt sich an Raumlautstaerke an
        self._noise_floor: float = 0.005
        # Adaptiver Peak: normalisiert laute Worte auf 1.0
        self._peak: float = 0.05
        # Geglaetteter Output-Level fuer natuerliches Pulsieren
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
        # RMS des 20-ms-Frames. mono: indata.shape == (320, 1)
        rms = float(np.sqrt(np.mean(np.square(indata))))

        # Noise-Floor nur bei leisen Frames updaten (EMA). Das verhindert,
        # dass lautes Sprechen den Floor hochzieht und danach alles gated.
        if rms < self._noise_floor * 1.5:
            self._noise_floor = 0.95 * self._noise_floor + 0.05 * rms
        self._noise_floor = max(self._noise_floor, _MIN_NOISE_FLOOR)

        # Speech-Gate: unter 3x Noise-Floor gilt als "kein Sprechen".
        speech_threshold = self._noise_floor * 3.0
        gated = max(0.0, rms - speech_threshold)

        # Peak-Tracking: schneller Anstieg, langsamer Abfall → Auto-Gain.
        # 0.997^50 ≈ 0.86 pro Sekunde decay, d.h. in ~3 s halbiert sich der
        # Peak wenn nichts mehr lautes kommt → nachfolgendes normales
        # Sprechen wird schnell wieder als "voller Ausschlag" skaliert.
        if gated > self._peak:
            self._peak = gated
        else:
            self._peak *= 0.997
        self._peak = max(self._peak, _MIN_PEAK)

        raw_level = min(1.0, gated / self._peak)

        # Attack-fast, Release-slow — fuehlt sich wie natuerliches Pulsieren
        # an. Ohne Release-Smoothing "flackert" der Orb.
        if raw_level > self._smoothed:
            self._smoothed = 0.4 * self._smoothed + 0.6 * raw_level
        else:
            self._smoothed = 0.75 * self._smoothed + 0.25 * raw_level

        try:
            self._on_level(self._smoothed)
        except Exception:
            # Callback-Fehler duerfen den Audio-Stream nicht killen
            pass
