"""Record a handful of real wake-word samples to personalize the neural model.

Prompts you to say your wake phrase N times, records ~2 s each from the default
microphone, and saves 16 kHz mono WAVs to ``data/wake_samples/<slug>/``. Those
real recordings are then mixed into the training set (heavily weighted) so the
custom openWakeWord model fires reliably on YOUR voice — the guaranteed path to
"Hey Google" reliability for a custom word.

Works alongside the running app (Windows WASAPI shared mode). Cross-platform via
sounddevice.

usage: python scripts/record_wake_samples.py "Hey Nico" [count]
"""
from __future__ import annotations

import os
import re
import sys
import time
import wave

import numpy as np
import sounddevice as sd

PHRASE = sys.argv[1] if len(sys.argv) > 1 else "Hey Nico"
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 15
SR = 16000
DUR = 2.0
SLUG = re.sub(r"[^a-z0-9]+", "_", PHRASE.lower()).strip("_") or "wake"
OUT = os.path.join("data", "wake_samples", SLUG)
os.makedirs(OUT, exist_ok=True)


def record_one(path: str) -> float:
    a = sd.rec(int(DUR * SR), samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    a = a.reshape(-1)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(a.tobytes())
    return float(np.sqrt(np.mean((a.astype(np.float32) / 32768.0) ** 2)))


def main() -> None:
    print(f"\nRecording {COUNT} samples of '{PHRASE}'. Speak naturally, at your")
    print("normal distance and volume. Vary it a little (a bit faster/slower).\n")
    kept = 0
    for i in range(COUNT):
        for c in (3, 2, 1):
            print(f"  sample {i + 1}/{COUNT} in {c}...", end="\r", flush=True)
            time.sleep(0.7)
        print(f"  sample {i + 1}/{COUNT}: SPEAK NOW -> '{PHRASE}'        ", flush=True)
        path = os.path.join(OUT, f"{SLUG}_{i:02d}.wav")
        rms = record_one(path)
        if rms < 0.005:
            print(f"    (very quiet, rms={rms:.3f} — kept, but speak up if you can)")
        kept += 1
        time.sleep(0.3)
    print(f"\nDone. Saved {kept} samples to {OUT}")
    print("Tell the assistant you're finished — it will retrain the model on your voice.")


main()
