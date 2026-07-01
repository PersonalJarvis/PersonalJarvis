"""Voice-comparison test for the JARVIS voice.

Generates the same test phrase with all male, deep-voiced Gemini voices and
plays them back one after another with a 0.7 s pause. Listen and decide which
one best matches the calm, formal assistant tone of the Jarvis voice.

Usage:
    python -m jarvis.speech.voice_compare
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
except Exception:  # noqa: BLE001 — sounddevice/PortAudio (libportaudio2) absent (headless/slim)
    sd = None  # type: ignore[assignment]

# Candidate voices — male/deep/authoritative per the Gemini voice docs
JARVIS_CANDIDATES: tuple[str, ...] = (
    "Charon",       # informative/calm
    "Fenrir",       # excitable, deep
    "Orus",         # firm, authoritative
    "Algieba",      # current default (for comparison)
    "Enceladus",    # breathy
    "Rasalgethi",   # informative
    "Iapetus",      # clear
    "Alnilam",      # firm
    "Sadachbia",    # lively
    "Zubenelgenubi", # casual
)

# Neutral English voice-test phrase (calm, formal assistant tone; varied phonemes)
TEST_PHRASE = (
    "Good evening. It is half past nine, and everything is on schedule. "
    "How can I help you today?"
)


def _setup() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass
    # Load .env
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if v and not os.environ.get(k):
                    os.environ[k] = v


async def _synth(voice: str, text: str) -> bytes:
    """Synthesizes the test phrase with one voice and returns PCM bytes."""
    from google import genai
    from google.genai import types
    key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_AIStudio_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )
    client = genai.Client(api_key=key)
    resp = await asyncio.to_thread(
        client.models.generate_content,
        model="gemini-3.1-flash-tts-preview",
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )
    return resp.candidates[0].content.parts[0].inline_data.data


def _play(pcm_bytes: bytes, sample_rate: int = 24_000) -> None:
    arr = np.frombuffer(pcm_bytes, dtype=np.int16)
    sd.play(arr, samplerate=sample_rate, blocking=True)


async def main() -> None:
    _setup()
    print("=" * 64)
    print(f"  JARVIS Voice-Comparison — {len(JARVIS_CANDIDATES)} candidates")
    print("=" * 64)
    print(f"Phrase: \"{TEST_PHRASE}\"\n")

    # Generate all first (so playback is smooth)
    print("Generating all samples (in parallel)...")
    tasks = [_synth(v, TEST_PHRASE) for v in JARVIS_CANDIDATES]
    pcms = await asyncio.gather(*tasks, return_exceptions=True)
    print("Done.\n")

    # Save to WAVs for later comparison
    out_dir = Path(__file__).resolve().parents[2] / "data" / "voice_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    import wave
    for voice, pcm in zip(JARVIS_CANDIDATES, pcms):
        if isinstance(pcm, Exception):
            print(f"  ! {voice}: ERROR {pcm}")
            continue
        p = out_dir / f"jarvis_test_{voice}.wav"
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24_000)
            wf.writeframes(pcm)

    # Play back one after another, with an announcement
    for voice, pcm in zip(JARVIS_CANDIDATES, pcms):
        if isinstance(pcm, Exception):
            continue
        duration = len(pcm) / 2 / 24_000
        print(f"▶ {voice}  ({duration:.1f}s)")
        _play(pcm, 24_000)
        time.sleep(0.7)

    print()
    print("=" * 64)
    print(f"WAV files saved under: {out_dir}")
    print("Which voice sounds most like JARVIS to you?")
    print("=" * 64)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAborted.")
