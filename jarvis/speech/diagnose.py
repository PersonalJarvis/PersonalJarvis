"""Speech-Diagnose-CLI — hilft wenn Wake-Word / Mic / TTS nicht funktionieren.

Aufruf:
    python -m jarvis.speech.diagnose

Zeigt:
  1. Alle Audio-Devices (Input + Output)
  2. Live-Mic-Pegel (dBFS) über 10 Sekunden — damit du siehst ob dein Mic
     überhaupt ankommt
  3. Live-Wake-Word-Score über 20 Sekunden — sag "Hey Jarvis" / "Jarvis"
     und sieh was openWakeWord misst
  4. Whisper-Transkriptions-Test — sag irgendwas 3 Sek, Jarvis zeigt was
     er verstanden hat
  5. Chime-Playback — kurzer Ton zum Output-Device
  6. TTS-Rundlauf — Jarvis sagt einen Satz via Gemini
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except Exception:  # noqa: BLE001 — sounddevice/PortAudio (libportaudio2) absent (headless/slim)
    sd = None  # type: ignore[assignment]

from jarvis.audio.capture import MicrophoneCapture, pcm_bytes_to_np
from jarvis.audio.chime import CHIME_PCM, CHIME_SAMPLE_RATE
from jarvis.audio.player import AudioPlayer

log = logging.getLogger("jarvis.diagnose")


def _setup_stdout() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass


def _load_env() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and not os.environ.get(k):
            os.environ[k] = v


# ----------------------------------------------------------------------
# Steps
# ----------------------------------------------------------------------

def _print_header(n: int, title: str) -> None:
    print()
    print("=" * 64)
    print(f"  [{n}] {title}")
    print("=" * 64)


def step_devices() -> None:
    _print_header(1, "Audio-Devices")
    devices = sd.query_devices()
    default_in, default_out = sd.default.device
    print(f"Default Input : #{default_in}  →  {devices[default_in]['name']}")
    print(f"Default Output: #{default_out}  →  {devices[default_out]['name']}")
    print()
    print("Alle Inputs:")
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            marker = " ★" if i == default_in else "  "
            print(f"  {marker} #{i:2d}  {d['name']}  (ch={d['max_input_channels']}, rate={int(d['default_samplerate'])})")
    print()
    print("Alle Outputs:")
    for i, d in enumerate(devices):
        if d["max_output_channels"] > 0:
            marker = " ★" if i == default_out else "  "
            print(f"  {marker} #{i:2d}  {d['name']}  (ch={d['max_output_channels']}, rate={int(d['default_samplerate'])})")


async def step_mic_level(duration_s: float = 10.0) -> float:
    """Misst Live-Mic-Pegel — User sollte laut sprechen, Max-dBFS wird gemerkt."""
    _print_header(2, f"Mic-Pegel-Test ({int(duration_s)} Sekunden — SPRICH JETZT!)")
    print("Sprich laut — zähl bis zehn oder sing ein bisschen.")
    print("Max-Pegel sollte im Bereich -20 bis -5 dBFS liegen.")
    print()
    max_dbfs = -120.0
    samples_seen = 0
    t_end = time.time() + duration_s
    async with MicrophoneCapture() as mic:
        async for chunk in mic.stream():
            if time.time() >= t_end:
                break
            arr = pcm_bytes_to_np(chunk.pcm)
            rms = float(np.sqrt(np.mean(arr * arr)) + 1e-12)
            dbfs = 20.0 * np.log10(rms)
            max_dbfs = max(max_dbfs, dbfs)
            samples_seen += len(arr)
            bar_len = max(0, int((dbfs + 60) / 2))  # skaliert [-60 .. 0] → [0 .. 30]
            bar = "█" * min(bar_len, 30)
            sys.stdout.write(f"\r  level: {dbfs:6.1f} dBFS  {bar:<30s}  (max: {max_dbfs:6.1f})")
            sys.stdout.flush()
    print()
    print(f"→ Samples empfangen: {samples_seen}   Max-Pegel: {max_dbfs:.1f} dBFS")
    if samples_seen == 0:
        print("  ⚠ KEIN Audio! Mic-Auswahl in Windows prüfen.")
    elif max_dbfs < -40:
        print("  ⚠ Sehr leiser Mic-Pegel. Windows-Mic-Volume hochdrehen oder Headset näher.")
    elif max_dbfs > -3:
        print("  ⚠ Übersteuert! Mic-Volume in Windows reduzieren.")
    else:
        print("  ✓ Pegel sieht gut aus.")
    return max_dbfs


async def step_wake_live(duration_s: float = 20.0) -> None:
    _print_header(3, f"Wake-Word Live-Score ({int(duration_s)} Sekunden)")
    print("Sag MEHRMALS dein Wake-Word — versuche verschiedene Aussprachen.")
    print("Zeigt den höchsten Wake-Score pro Sekunde live.")
    print()
    from jarvis.plugins.wake.openwakeword_provider import (
        OWW_FRAME_SAMPLES,
        OpenWakeWordProvider,
    )
    prov = OpenWakeWordProvider(
        keywords=("hey_jarvis",),
        activation_threshold=0.15,
        score_log_threshold=1.1,  # disable inline logging, wir zeigen eigene
    )
    prov._ensure_model()
    assert prov._model is not None

    residual = np.empty(0, dtype=np.int16)
    max_score_global = 0.0
    last_report_t = time.time()
    max_in_window = 0.0
    t_end = time.time() + duration_s
    async with MicrophoneCapture() as mic:
        async for chunk in mic.stream():
            if time.time() >= t_end:
                break
            int16 = np.frombuffer(chunk.pcm, dtype=np.int16)
            buf = np.concatenate([residual, int16])
            n_full = len(buf) // OWW_FRAME_SAMPLES
            if n_full == 0:
                residual = buf
                continue
            frames = buf[: n_full * OWW_FRAME_SAMPLES].reshape(n_full, OWW_FRAME_SAMPLES)
            residual = buf[n_full * OWW_FRAME_SAMPLES:]
            for frame in frames:
                scores = await asyncio.to_thread(prov._model.predict, frame)
                s = float(scores.get("hey_jarvis", 0.0))
                max_in_window = max(max_in_window, s)
                max_score_global = max(max_score_global, s)
            now = time.time()
            if now - last_report_t >= 0.5:
                bar_len = min(30, int(max_in_window * 30))
                bar = "█" * bar_len
                mark = "✓ HIT" if max_in_window >= 0.15 else "     "
                sys.stdout.write(
                    f"\r  score last 0.5s: {max_in_window:.3f}  {bar:<30s}  global-max: {max_score_global:.3f}  {mark}"
                )
                sys.stdout.flush()
                last_report_t = now
                max_in_window = 0.0
    print()
    print(f"→ Global-Max-Score: {max_score_global:.3f}")
    if max_score_global < 0.15:
        print("  ⚠ openWakeWord erkennt dein Wake-Word nicht zuverlässig.")
        print("    → Whisper-Wake-Fallback übernimmt in der vollen Pipeline.")
    else:
        print("  ✓ openWakeWord würde triggern bei Threshold 0.15.")


async def step_whisper(duration_s: float = 4.0) -> None:
    _print_header(4, f"Whisper-Transkriptions-Test ({int(duration_s)} Sekunden)")
    print("Sag einen kurzen Satz. Jarvis transkribiert und zeigt was er verstand.")
    print()
    from jarvis.plugins.stt.fwhisper import FasterWhisperProvider
    stt = FasterWhisperProvider()
    stt._ensure_model()

    collected = bytearray()
    t_end = time.time() + duration_s
    async with MicrophoneCapture() as mic:
        async for chunk in mic.stream():
            if time.time() >= t_end:
                break
            collected.extend(chunk.pcm)
    print("  Transkribiere …")
    transcript = await stt.transcribe_pcm(bytes(collected))
    print(f"  Sprache:    {transcript.language}")
    print(f"  Confidence: {transcript.confidence:.2f}")
    print(f"  Text:       {transcript.text!r}")


async def step_chime() -> None:
    _print_header(5, "Chime-Playback")
    print("Sollte jetzt einen kurzen Ding-Ton über die Lautsprecher spielen …")
    player = AudioPlayer()
    await player.play_pcm(CHIME_PCM, sample_rate=CHIME_SAMPLE_RATE)
    print("  ✓ Chime abgespielt.")


async def step_tts() -> None:
    _print_header(6, "TTS-Rundlauf (Gemini 3.1 Flash)")
    print("Jarvis sagt jetzt einen Test-Satz …")
    from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS
    tts = GeminiFlashTTS(default_voice="Algieba", language_code="de-DE")
    tts._ensure_client()
    player = AudioPlayer()
    text = "Hallo, ich bin Jarvis. Diagnose abgeschlossen. Du kannst mich jetzt nutzen."
    async for chunk in tts.synthesize(text):
        await player.play_pcm(chunk.pcm, sample_rate=chunk.sample_rate)
    print("  ✓ TTS-Ausgabe fertig.")


async def _main() -> None:
    _setup_stdout()
    _load_env()
    logging.basicConfig(level=logging.WARNING)  # leise, wir printen selbst

    step_devices()
    await step_mic_level(10.0)
    await step_wake_live(20.0)
    await step_whisper(4.0)
    await step_chime()
    await step_tts()
    print()
    print("=" * 64)
    print("  Diagnose abgeschlossen.")
    print("=" * 64)


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
