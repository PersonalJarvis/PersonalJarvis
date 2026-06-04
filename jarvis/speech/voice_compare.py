"""Voice-Comparison-Test für JARVIS-Stimme.

Generiert dieselbe Test-Phrase mit allen männlich-tiefen Gemini-Voices
und spielt sie nacheinander mit 0.7 Sek Pause ab. Du hörst und entscheidest
welche am ehesten nach Paul Bettany's J.A.R.V.I.S. klingt.

Aufruf:
    python -m jarvis.speech.voice_compare
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

# Kandidaten-Voices — männlich/tief/autoritativ laut Gemini-Voice-Docs
JARVIS_CANDIDATES: tuple[str, ...] = (
    "Charon",       # informativ/ruhig
    "Fenrir",       # erregbar, tief
    "Orus",         # firm, autoritär
    "Algieba",      # aktueller Default (für Vergleich)
    "Enceladus",    # breathy
    "Rasalgethi",   # informativ
    "Iapetus",      # klar
    "Alnilam",      # firm
    "Sadachbia",    # lebhaft
    "Zubenelgenubi", # casual
)

# JARVIS-typische Begrüßung auf Englisch (Tony Stark Butler-Ton)
TEST_PHRASE = (
    "Good evening, Sir. This is JARVIS. "
    "All systems are online and operational. How may I be of assistance?"
)


def _setup() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass
    # .env laden
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
    """Synthetisiert Test-Phrase mit einer Voice und returnt PCM-Bytes."""
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
    print(f"  JARVIS Voice-Comparison — {len(JARVIS_CANDIDATES)} Kandidaten")
    print("=" * 64)
    print(f"Phrase: \"{TEST_PHRASE}\"\n")

    # Erst alle generieren (damit Playback fluide ist)
    print("Generiere alle Samples (parallel)...")
    tasks = [_synth(v, TEST_PHRASE) for v in JARVIS_CANDIDATES]
    pcms = await asyncio.gather(*tasks, return_exceptions=True)
    print("Fertig.\n")

    # Save to WAVs für späteren Vergleich
    out_dir = Path(__file__).resolve().parents[2] / "data" / "voice_compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    import wave
    for voice, pcm in zip(JARVIS_CANDIDATES, pcms):
        if isinstance(pcm, Exception):
            print(f"  ! {voice}: FEHLER {pcm}")
            continue
        p = out_dir / f"jarvis_test_{voice}.wav"
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24_000)
            wf.writeframes(pcm)

    # Nacheinander abspielen, mit Ansage
    for voice, pcm in zip(JARVIS_CANDIDATES, pcms):
        if isinstance(pcm, Exception):
            continue
        duration = len(pcm) / 2 / 24_000
        print(f"▶ {voice}  ({duration:.1f}s)")
        _play(pcm, 24_000)
        time.sleep(0.7)

    print()
    print("=" * 64)
    print(f"WAV-Dateien gespeichert unter: {out_dir}")
    print("Welche Voice klingt für dich am besten nach JARVIS?")
    print("=" * 64)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
