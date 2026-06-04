"""Rolling-Window Whisper Wake-Detection — robuster Wake ohne VAD-Abhängigkeit.

Im Gegensatz zu `whisper_wake.py` (wartet auf VAD-Endpoint, scheitert bei
leisen Mics): hier wird ein Ring-Buffer von 2.5 Sekunden Audio gehalten
und alle 500 ms durch Whisper transkribiert. Wenn "jarvis" im Transkript
auftaucht — Trigger.

Vorteile:
- Kein VAD-Dependency → funktioniert auch bei niedrigem Mic-Pegel
- Triggert sofort (500 ms Polling-Intervall), nicht erst nach Sprachende
- Nutzt Whisper (nativ deutsch-fähig) → keine Englisch-Trainings-Bias

Nachteile:
- Höhere GPU-Last (Whisper läuft permanent statt nur bei Utterance-Ende)
- Auf RTX 5070 Ti mit distil-large-v3: ~80-150 ms pro 2.5-Sek-Transkription
  = ~20 % GPU-Nutzung bei 500 ms Poll-Intervall

Parameter:
- `window_s`: Buffer-Länge (Default 2.5 s — lang genug für "Hey Jarvis")
- `poll_interval_s`: wie oft wir transkribieren (Default 0.5 s)
- `cooldown_s`: nach Trigger nicht sofort wieder (Default 2 s)
"""
from __future__ import annotations

import logging
import os
import re
import time
import wave
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np

from jarvis.audio.capture import pcm_bytes_to_np
from jarvis.core.protocols import AudioChunk
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider

# The strict "hey/hi/hallo + jarv-stem" pattern now lives in wake_constants as
# the single source of truth (the prefix verifier re-exports the same object),
# so the two STT wake paths can never drift apart (BUG-008). Re-exported here
# under the historical ``DEFAULT_PATTERN`` name so existing call sites and tests
# keep working. ``pattern=`` also accepts a ``WakeMatcher`` (duck-types
# ``.search().group(0)``) so a custom wake phrase can drive this backstop.
from jarvis.speech.wake_constants import JARVIS_WAKE_PATTERN as DEFAULT_PATTERN

log = logging.getLogger("jarvis.wake.rolling")


# Watchdog-Verzeichnis für Debug-WAVs
DEBUG_DIR = Path(os.environ.get("JARVIS_DEBUG_DIR", "./data/wake_debug"))


def _save_wav(pcm_bytes: bytes, sample_rate: int, path: Path) -> None:
    """Schreibt int16-PCM als gültiges WAV-File."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16 = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)


class RollingWhisperWake:
    """Rolling-Window Wake-Detection per Whisper-Transkription."""

    def __init__(
        self,
        stt: FasterWhisperProvider,
        # Either a compiled regex or a WakeMatcher — both expose
        # ``.search(text)`` returning an object with ``.group(0)``.
        pattern: Any = DEFAULT_PATTERN,
        window_s: float = 1.8,        # kürzer = weniger Stille-Anteil = höhere avg-RMS
        poll_interval_s: float = 0.3,  # schnellere Wake-Reaktion
        cooldown_s: float = 5.0,      # längerer Cooldown → weniger Over-Triggering
        sample_rate: int = 16_000,
        # 2026-04-22 (3. Iteration): RMS/Peak-Gates zurueck auf niedrig. Die
        # Headset-Aussteuerung des Users ist sehr leise (typisch rms 0.01-0.02
        # bei normalem Sprechen). Hoehere Gates blockten echtes "Hey Jarvis".
        # Schutz gegen Halluzinationen liefert jetzt das Pattern alleine:
        # Whisper halluziniert "JARVIS.", "Vielen Dank.", "Thank you" — das
        # matched unser Pattern (nur "hey/hi/hallo + jarv-Stamm") nicht.
        # Whisper wird dabei etwas oefter aufgerufen (mehr GPU-Last), aber das
        # Trigger-Verhalten ist korrekt — genau was der User will.
        min_rms: float = 0.003,
        min_peak: float = 0.02,
        save_debug_wavs: bool = True,  # Watchdog-Modus
        heartbeat_interval_s: float = 3.0,
        # Peak-Normalization statt fester Gain: misst Audio-Peak, wendet
        # dynamisch den Gain an, der nötig ist um auf -3 dBFS zu kommen.
        # Ersetzt fehlenden Windows/Hardware-Mic-Boost OHNE Clipping.
        # Bei Stille/leisem Rauschen wird der Gain gecappt (max_gain_db).
        target_peak_dbfs: float = -3.0,
        max_gain_db: float = 40.0,
        language: str = "de",
    ) -> None:
        self._stt = stt
        self._pattern = pattern
        self._window_samples = int(window_s * sample_rate)
        self._poll_interval_s = poll_interval_s
        self._cooldown_s = cooldown_s
        self._sample_rate = sample_rate
        self._min_rms = min_rms
        self._save_debug_wavs = save_debug_wavs
        self._heartbeat_interval_s = heartbeat_interval_s
        self._target_peak = float(10.0 ** (target_peak_dbfs / 20.0))  # -3 dBFS ≈ 0.707
        self._max_gain_factor = float(10.0 ** (max_gain_db / 20.0))    # 40 dB = 100x
        self._min_peak = min_peak
        # Wake-Transkription auf eine feste Sprache pinnen — auto-detect auf
        # 1.8s-Chunks kippt oft fälschlich auf EN (User spricht DE, Whisper
        # halluziniert "Thank you"). None = auto (nicht empfohlen).
        self._language: str | None = language
        # Statistik für Heartbeat
        self._chunks_seen = 0
        self._total_bytes = 0
        self._max_rms = 0.0
        self._last_transcript = ""
        self._last_heartbeat_t = time.time()

    async def detect(
        self, chunks: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[str]:
        """Konsumiert Audio-Chunks, yielded matched-Keyword bei Hit."""
        # Ring-Buffer: float32 samples im [-1, 1] Bereich
        buffer: deque[np.ndarray] = deque()
        buffer_len = 0
        last_poll_t = time.time()
        last_trigger_t = 0.0

        async for chunk in chunks:
            samples = pcm_bytes_to_np(chunk.pcm)
            buffer.append(samples)
            buffer_len += len(samples)

            # Heartbeat-Statistik updaten (live RMS pro Chunk)
            self._chunks_seen += 1
            self._total_bytes += len(chunk.pcm)
            chunk_rms = float(np.sqrt(np.mean(samples * samples) + 1e-12))
            if chunk_rms > self._max_rms:
                self._max_rms = chunk_rms

            # Heartbeat regelmäßig ausgeben — auch wenn Whisper nichts matched
            now_hb = time.time()
            if now_hb - self._last_heartbeat_t >= self._heartbeat_interval_s:
                dbfs = 20.0 * np.log10(max(self._max_rms, 1e-12))
                log.info(
                    "💓 wake-heartbeat: chunks=%d bytes=%dKB max-rms=%.4f (%.1f dBFS) last-transcript=%r",
                    self._chunks_seen,
                    self._total_bytes // 1024,
                    self._max_rms,
                    dbfs,
                    self._last_transcript[:80],
                )
                self._chunks_seen = 0
                self._total_bytes = 0
                self._max_rms = 0.0
                self._last_heartbeat_t = now_hb

            # Ältere Samples rauswerfen wenn Buffer zu lang
            while buffer_len > self._window_samples:
                oldest = buffer[0]
                overflow = buffer_len - self._window_samples
                if len(oldest) <= overflow:
                    buffer.popleft()
                    buffer_len -= len(oldest)
                else:
                    buffer[0] = oldest[overflow:]
                    buffer_len -= overflow

            # Poll-Intervall abwarten
            now = time.time()
            if now - last_poll_t < self._poll_interval_s:
                continue
            last_poll_t = now

            # Cooldown nach letztem Trigger
            if now - last_trigger_t < self._cooldown_s:
                continue

            # Noch nicht genug Audio im Buffer
            if buffer_len < self._sample_rate:  # mind. 1 Sek
                continue

            # Concatenate + Lautstärke-Check (RMS) — kein Whisper-Call bei Stille
            audio_np = np.concatenate(list(buffer))
            rms = float(np.sqrt(np.mean(audio_np * audio_np) + 1e-12))
            if rms < self._min_rms:
                continue

            # Peak-Gate: bei reinem Rauschen gar nicht erst Whisper bemühen
            peak = float(np.max(np.abs(audio_np)))
            if peak < self._min_peak:
                # Kein Whisper-Call — zu leise für Sprache
                continue

            # Whisper-Call mit Peak-Normalization (dynamischer Gain)
            try:
                if peak > 1e-6:
                    # Gain berechnen um Ziel-Peak zu erreichen, aber cappen
                    gain = min(self._target_peak / peak, self._max_gain_factor)
                else:
                    gain = 1.0
                boosted = audio_np * gain
                applied_db = 20.0 * np.log10(max(gain, 1e-12))
                pcm_bytes = (
                    np.clip(boosted, -1.0, 1.0) * 32767.0
                ).astype(np.int16).tobytes()
                log.debug("whisper-gain applied=%.1f dB (peak-in=%.3f)", applied_db, peak)
                transcript = await self._stt.transcribe_pcm(
                    pcm_bytes, language=self._language
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Rolling-Whisper Transkription fehlgeschlagen: %s", exc)
                continue

            text = transcript.text.strip()
            self._last_transcript = text

            # Watchdog: WAV speichern damit User/ich die Aufnahme nachprüfen können
            if self._save_debug_wavs:
                try:
                    pcm_bytes_for_wav = (
                        np.clip(audio_np, -1.0, 1.0) * 32767.0
                    ).astype(np.int16).tobytes()
                    ts = time.strftime("%H%M%S")
                    safe_text = re.sub(r"[^\w\-]+", "_", text[:40]) or "empty"
                    wav_path = DEBUG_DIR / f"wake_{ts}_rms{rms:.3f}_{safe_text}.wav"
                    _save_wav(pcm_bytes_for_wav, self._sample_rate, wav_path)
                except Exception as exc:  # noqa: BLE001
                    log.warning("WAV-Save fehlgeschlagen: %s", exc)

            if not text:
                log.info("rolling-whisper: rms=%.4f text=<leer>", rms)
                continue

            log.info("rolling-whisper: rms=%.4f text=%r", rms, text)
            m = self._pattern.search(text)
            if m:
                last_trigger_t = now
                yield m.group(0)
