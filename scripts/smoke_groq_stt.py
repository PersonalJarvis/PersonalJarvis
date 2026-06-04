"""Live smoke test for the Groq STT plugin (one-shot).

Generates a short synthetic audio buffer (modulated sine + noise envelope —
silence is rejected by some endpoints), uploads it via the real Groq endpoint,
and prints the parsed Transcript. The API key MUST be in the GROQ_API_KEY env
var; this script will not accept it on argv (Windows process listing).

Usage (Windows PowerShell):
    $env:GROQ_API_KEY = "gsk_..."
    python scripts/smoke_groq_stt.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass

import numpy as np

# Ensure we hit the editable-install path
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

from jarvis.plugins.stt.groq_api import GroqWhisperAPI  # noqa: E402


@dataclass
class _Chunk:
    pcm: bytes
    sample_rate: int = 16_000
    channels: int = 1
    timestamp_ns: int = 0


def _synthetic_speech_like_pcm(duration_s: float = 1.6, sr: int = 16_000) -> bytes:
    """Tone + amplitude envelope so the endpoint doesn't reject pure silence."""
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    # Two formant-ish frequencies, modulated envelope
    tone = 0.30 * np.sin(2 * np.pi * 220 * t) + 0.20 * np.sin(2 * np.pi * 660 * t)
    envelope = 0.5 * (1 + np.sin(2 * np.pi * 4 * t))  # 4 Hz word-rhythm
    sig = (tone * envelope * 0.6 + 0.02 * np.random.randn(t.size))
    sig = np.clip(sig, -1.0, 1.0)
    return (sig * 32767).astype(np.int16).tobytes()


async def _async_iter(chunks):
    for c in chunks:
        yield c


async def main() -> int:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        print("ERROR: set GROQ_API_KEY before invoking this script.", file=sys.stderr)
        return 2
    print(f"[smoke] GROQ_API_KEY present (len={len(key)}, prefix={key[:4]}...)")

    pcm = _synthetic_speech_like_pcm()
    chunk = _Chunk(pcm=pcm)
    provider = GroqWhisperAPI(language="de")

    start = time.perf_counter()
    try:
        transcript = await provider.transcribe(_async_iter([chunk]))
    finally:
        await provider.aclose()
    elapsed_ms = (time.perf_counter() - start) * 1000

    print(f"[smoke] HTTP roundtrip: {elapsed_ms:.0f} ms")
    print(f"[smoke] text       = {transcript.text!r}")
    print(f"[smoke] language   = {transcript.language!r}")
    print(f"[smoke] confidence = {transcript.confidence:.3f}")
    print(f"[smoke] segments   = {len(transcript.segments)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
