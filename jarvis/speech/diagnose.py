"""Speech diagnostics CLI — helps when wake word / mic / TTS aren't working.

Usage:
    python -m jarvis.speech.diagnose

Shows:
  1. All audio devices (input + output)
  2. Live mic level (dBFS) over 10 seconds — so you can see whether your mic
     is picking anything up at all
  3. Live wake-word score over 20 seconds — say your configured wake word
     and see what your custom openWakeWord model measures (skipped when no
     custom model is configured)
  4. Whisper transcription test — say something for 3 sec, Jarvis shows
     what it understood
  5. Chime playback — short tone on the output device
  6. TTS round-trip — Jarvis says a sentence via Gemini
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import Callable
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
    _print_header(1, "Audio devices")
    devices = sd.query_devices()
    default_in, default_out = sd.default.device
    print(f"Default Input : #{default_in}  →  {devices[default_in]['name']}")
    print(f"Default Output: #{default_out}  →  {devices[default_out]['name']}")
    print()
    print("All inputs:")
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            marker = " ★" if i == default_in else "  "
            print(f"  {marker} #{i:2d}  {d['name']}  (ch={d['max_input_channels']}, rate={int(d['default_samplerate'])})")
    print()
    print("All outputs:")
    for i, d in enumerate(devices):
        if d["max_output_channels"] > 0:
            marker = " ★" if i == default_out else "  "
            print(f"  {marker} #{i:2d}  {d['name']}  (ch={d['max_output_channels']}, rate={int(d['default_samplerate'])})")


async def measure_mic_dbfs(
    duration_s: float = 3.0,
    *,
    on_frame: Callable[[float, float, int], None] | None = None,
) -> float:
    """Return the max dBFS heard over ``duration_s``; -120.0 if no samples / no
    device / any error. Pure measurement (no printing) — reused by the
    onboarding mic-level route (``GET /api/settings/wake-word/mic-level``) and
    by ``step_mic_level``'s CLI bars via the optional ``on_frame`` hook
    (called per chunk with ``(dbfs, running_max, n_samples)``). Never raises."""
    max_dbfs = -120.0
    try:
        async with MicrophoneCapture() as mic:
            t_end = time.time() + duration_s
            async for chunk in mic.stream():
                if time.time() >= t_end:
                    break
                arr = pcm_bytes_to_np(chunk.pcm)
                rms = float(np.sqrt(np.mean(arr * arr)) + 1e-12)
                dbfs = 20.0 * float(np.log10(rms))
                max_dbfs = max(max_dbfs, dbfs)
                if on_frame is not None:
                    on_frame(dbfs, max_dbfs, len(arr))
    except Exception:  # noqa: BLE001 — headless / no device / any error → honest floor
        return -120.0
    return max_dbfs


async def step_mic_level(duration_s: float = 10.0) -> float:
    """Measures the live mic level — the user should speak loudly, max dBFS is recorded."""
    _print_header(2, f"Mic level test ({int(duration_s)} seconds — SPEAK NOW!)")
    print("Speak loudly — count to ten or sing a bit.")
    print("Max level should be in the range -20 to -5 dBFS.")
    print()
    samples_seen = 0

    def _render_bar(dbfs: float, running_max: float, n_samples: int) -> None:
        nonlocal samples_seen
        samples_seen += n_samples
        bar_len = max(0, int((dbfs + 60) / 2))  # scales [-60 .. 0] → [0 .. 30]
        bar = "█" * min(bar_len, 30)
        sys.stdout.write(f"\r  level: {dbfs:6.1f} dBFS  {bar:<30s}  (max: {running_max:6.1f})")
        sys.stdout.flush()

    max_dbfs = await measure_mic_dbfs(duration_s=duration_s, on_frame=_render_bar)
    print()
    print(f"→ Samples received: {samples_seen}   Max level: {max_dbfs:.1f} dBFS")
    if samples_seen == 0:
        print("  ⚠ NO audio! Check mic selection in Windows.")
    elif max_dbfs < -40:
        print("  ⚠ Very quiet mic level. Turn up the Windows mic volume or move the headset closer.")
    elif max_dbfs > -3:
        print("  ⚠ Clipping! Reduce the mic volume in Windows.")
    else:
        print("  ✓ Level looks good.")
    return max_dbfs


async def step_wake_live(duration_s: float = 20.0) -> None:
    _print_header(3, f"Wake-word live score ({int(duration_s)} seconds)")
    print("Say your wake word SEVERAL times — try different pronunciations.")
    print("Shows the highest wake score per second live.")
    print()
    from jarvis.core.config import load_config
    from jarvis.plugins.wake.openwakeword_provider import (
        OWW_FRAME_SAMPLES,
        OpenWakeWordProvider,
    )
    from jarvis.speech.wake_phrase import resolve_wake_plan

    # This live-score step only applies to an openWakeWord MODEL — i.e. a
    # user-trained custom .onnx from the configured wake plan. The product
    # ships no named model (design 2026-07-07); vosk/whisper wake engines
    # have their own confirm paths and no frame score to display.
    ww = load_config().trigger.wake_word
    plan = resolve_wake_plan(ww, local_whisper_available=False)
    if plan.engine != "custom_onnx" or not plan.oww_model_path:
        print(
            "  (skipped) No custom wake model configured — this step scores a "
            "custom .onnx model. Your wake engine is "
            f"'{plan.engine}'; use the app's wake settings to test it."
        )
        return
    keyword = plan.oww_keyword
    prov = OpenWakeWordProvider(
        keywords=(keyword,),
        model_path=plan.oww_model_path,
        activation_threshold=0.15,
        score_log_threshold=1.1,  # disable inline logging, we show our own
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
                # Exactly one model is loaded; it reports under its file stem.
                s = float(max(scores.values())) if scores else 0.0
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
    print(f"→ Global max score: {max_score_global:.3f}")
    if max_score_global < 0.15:
        print("  ⚠ openWakeWord does not reliably detect your wake word.")
        print("    → The Whisper-wake fallback takes over in the full pipeline.")
    else:
        print("  ✓ openWakeWord would trigger at threshold 0.15.")


async def step_whisper(duration_s: float = 4.0) -> None:
    _print_header(4, f"Whisper transcription test ({int(duration_s)} seconds)")
    print("Say a short sentence. Jarvis transcribes it and shows what it understood.")
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
    print("  Transcribing …")
    transcript = await stt.transcribe_pcm(bytes(collected))
    print(f"  Language:   {transcript.language}")
    print(f"  Confidence: {transcript.confidence:.2f}")
    print(f"  Text:       {transcript.text!r}")


async def step_chime() -> None:
    _print_header(5, "Chime playback")
    print("Should now play a short ding tone over the speakers …")
    player = AudioPlayer()
    await player.play_pcm(CHIME_PCM, sample_rate=CHIME_SAMPLE_RATE)
    print("  ✓ Chime played.")


async def step_tts() -> None:
    _print_header(6, "TTS round-trip (Gemini 3.1 Flash)")
    print("Jarvis is now saying a test sentence …")
    from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS
    tts = GeminiFlashTTS(default_voice="Algieba", language_code="de-DE")
    tts._ensure_client()
    player = AudioPlayer()
    text = "Hallo, ich bin Jarvis. Diagnose abgeschlossen. Du kannst mich jetzt nutzen."  # i18n-allow
    async for chunk in tts.synthesize(text):
        await player.play_pcm(chunk.pcm, sample_rate=chunk.sample_rate)
    print("  ✓ TTS output done.")


async def _main() -> None:
    _setup_stdout()
    _load_env()
    logging.basicConfig(level=logging.WARNING)  # quiet, we print ourselves

    step_devices()
    await step_mic_level(10.0)
    await step_wake_live(20.0)
    await step_whisper(4.0)
    await step_chime()
    await step_tts()
    print()
    print("=" * 64)
    print("  Diagnostics complete.")
    print("=" * 64)


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nAborted.")
