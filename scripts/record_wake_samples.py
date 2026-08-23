"""Record a handful of real wake-word samples to personalize the neural model.

Prompts you to say your wake phrase N times, records ~2 s each from the default
microphone, and saves 16 kHz mono WAVs to ``data/wake_samples/<slug>/``. Those
real recordings are then mixed into the training set (heavily weighted) so the
custom openWakeWord model fires reliably on YOUR voice — the guaranteed path to
"Hey Google" reliability for a custom word.

## Why it shows you a meter

Fifteen recordings are easy to collect and hard to judge, and a bad set trains a
wake word that either ignores you or fires at nothing. So the recording is no
longer blind:

* a **live level meter**, so you can see the microphone is hearing you at all;
* a **clipping warning while you speak**, not after the set is finished —
  clipping is the most common cause of a wake word that will not train, and a
  warning that arrives once all fifteen are recorded cannot save any of them;
* a **summary** at the end that flags the individual bad takes, so you re-record
  those instead of starting the whole set again.

The audio itself is unchanged: 16 kHz mono int16, exactly what
``jarvis/plugins/wake/openwakeword_provider.py`` consumes. Nothing here filters,
normalizes or gates the recording — the meter only observes.

Works alongside the running app (Windows WASAPI shared mode). Cross-platform via
sounddevice.

usage: python scripts/record_wake_samples.py "Hey Assistant" [count]
"""
from __future__ import annotations

import os
import re
import sys
import time
import wave

import numpy as np

# The repo root, so this runs from anywhere without an editable install.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.audio.wake_sample_quality import (  # noqa: E402
    FrameLevel,
    SampleQuality,
    frame_level,
    judge_sample,
    summarize_set,
)

PHRASE = sys.argv[1] if len(sys.argv) > 1 else "Hey Assistant"
COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 15
SR = 16000
DUR = 2.0
SLUG = re.sub(r"[^a-z0-9]+", "_", PHRASE.lower()).strip("_") or "wake"
OUT = os.path.join("data", "wake_samples", SLUG)

#: Live meter refresh. 20 blocks a second is smooth to watch and cheap enough
#: that the arithmetic never competes with the capture thread.
BLOCK = SR // 20

_METER_WIDTH = 28


def _import_sounddevice():
    """Import sounddevice, or explain why this script cannot run here.

    Recording needs a real input device. On a headless server, in a container,
    or on a Linux box without PortAudio the import itself fails, and the honest
    answer is a sentence about the missing hardware rather than a traceback
    about a shared library.
    """
    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001 — any import failure means the same
        # thing to the user (no capture available), and the underlying error is
        # printed so a missing library is still diagnosable.
        print(f"\nCannot record on this machine: {exc}", file=sys.stderr)
        print(
            "This script needs a microphone and PortAudio. On a headless host "
            "record the samples on a machine that has one and copy "
            f"'{OUT}' across.",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    return sd


def _meter(level: FrameLevel) -> str:
    """One line of live feedback: a bar, the level, and a loud warning.

    The bar is peak-driven rather than RMS-driven because peak is what actually
    reaches the rail, and the point of watching it is to see the headroom
    disappear BEFORE it does.
    """
    filled = int(level.meter_fraction * _METER_WIDTH)
    bar = "#" * filled + "-" * (_METER_WIDTH - filled)
    tail = "  << TOO LOUD" if level.clipping else ""
    return f"  [{bar}] {level.peak_dbfs:6.1f} dBFS{tail}"


def record_one(path: str, *, label: str, meter: bool = True) -> SampleQuality:
    """Record one clip to ``path`` and return its verdict.

    Uses a callback stream rather than ``sd.rec`` + ``sd.wait`` so the level can
    be drawn while the audio arrives. The blocks are collected untouched and
    concatenated, so what lands on disk is byte-identical to what the old
    blocking capture produced.
    """
    sd = _import_sounddevice()
    blocks: list[np.ndarray] = []
    latest: list[FrameLevel] = []

    def on_block(indata, _frames, _time, status):
        if status:
            # An overflow means samples were dropped by the driver. Say so —
            # silently keeping a gapped recording is how an unexplained bad
            # sample gets into the training set.
            print(f"\n  audio device warning: {status}", flush=True)
        block = indata.reshape(-1).copy()
        blocks.append(block)
        latest.append(frame_level(block))

    with sd.InputStream(
        samplerate=SR, channels=1, dtype="int16", blocksize=BLOCK, callback=on_block
    ):
        deadline = time.monotonic() + DUR
        warned = False
        while time.monotonic() < deadline:
            if latest and meter:
                level = latest[-1]
                print(_meter(level), end="\r", flush=True)
                if level.clipping and not warned:
                    warned = True
            time.sleep(0.03)

    print(" " * (_METER_WIDTH + 30), end="\r")  # clear the meter line
    pcm = (
        np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.int16)
    ).astype(np.int16)

    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())

    quality = judge_sample(pcm)
    marker = {"good": "ok", "ok": "ok", "quiet": "!!", "clipped": "!!"}[quality.verdict]
    line = f"  [{marker}] {label}: {quality.rms_dbfs:.1f} dBFS ({quality.verdict})"
    if quality.advice:
        line += f"\n       {quality.advice}"
    print(line, flush=True)
    return quality


NEG_OUT = os.path.join("data", "wake_samples", SLUG + "_neg")
NEG_SECONDS = 60.0


def record_negatives() -> None:
    """Capture ~60 s of your real environment as NEGATIVES: breathing, silence,
    and normal talk — but NEVER the wake phrase. This is what teaches the model
    to STOP false-firing on your breath / room / random words."""
    os.makedirs(NEG_OUT, exist_ok=True)
    print(f"\nNow ~{int(NEG_SECONDS)}s of your normal environment as NEGATIVES.")
    print(f"Breathe, be quiet, talk about anything — but do NOT say '{PHRASE}'.")
    for c in (3, 2, 1):
        print(f"  starting in {c}...", end="\r", flush=True)
        time.sleep(0.8)
    print("  RECORDING negatives — breathe / talk / stay quiet (no wake word)   ", flush=True)
    chunk = 3.0
    n = int(NEG_SECONDS / chunk)
    for i in range(n):
        record_one(
            os.path.join(NEG_OUT, f"neg_{i:02d}.wav"),
            label=f"negatives {int((i + 1) * chunk)}/{int(NEG_SECONDS)}s",
            meter=False,
        )
    print(f"  saved {n} negative clips to {NEG_OUT}")


def _print_summary(qualities: dict[int, SampleQuality]) -> list[int]:
    """Print the set table and return the indices worth re-recording."""
    summary = summarize_set(list(qualities.values()), needed=COUNT)
    print(f"\n  {summary.headline}")
    if summary.clipped:
        print(f"  {summary.clipped} clipped — those are the ones that hurt training most.")
    if summary.quiet:
        print(f"  {summary.quiet} too quiet.")

    bad = [i for i, q in sorted(qualities.items()) if not q.usable]
    if bad:
        print("\n  Sample  Level      Problem")
        for i in bad:
            q = qualities[i]
            print(f"  {i + 1:>6}  {q.rms_dbfs:>6.1f} dB  {q.verdict}")
    return bad


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    print(f"\nRecording {COUNT} samples of '{PHRASE}'. Speak naturally, at your")
    print("normal distance and volume. Vary it a little (a bit faster/slower).")
    print("Watch the meter: the bar should move well, and never say TOO LOUD.\n")

    qualities: dict[int, SampleQuality] = {}
    for i in range(COUNT):
        for c in (3, 2, 1):
            print(f"  sample {i + 1}/{COUNT} in {c}...", end="\r", flush=True)
            time.sleep(0.7)
        print(f"  sample {i + 1}/{COUNT}: SPEAK NOW -> '{PHRASE}'        ", flush=True)
        path = os.path.join(OUT, f"{SLUG}_{i:02d}.wav")
        qualities[i] = record_one(path, label=f"sample {i + 1}")
        time.sleep(0.3)

    print(f"\nSaved {len(qualities)} wake samples to {OUT}")

    # Re-record loop: one sample at a time, so a single bad take never costs
    # the whole set.
    while True:
        bad = _print_summary(qualities)
        if not bad:
            break
        answer = input(
            "\n  Re-record which one? (number, 'all' for every flagged one, "
            "Enter to keep them) "
        ).strip().lower()
        if not answer:
            break
        targets = bad if answer == "all" else _parse_target(answer, len(qualities))
        if not targets:
            print("  Not a sample number.")
            continue
        for i in targets:
            print(f"\n  sample {i + 1}: SPEAK NOW -> '{PHRASE}'        ", flush=True)
            time.sleep(0.4)
            path = os.path.join(OUT, f"{SLUG}_{i:02d}.wav")
            qualities[i] = record_one(path, label=f"sample {i + 1}")

    record_negatives()
    print(
        "\nAll done. Tell the assistant you're finished — it retrains on "
        "your voice + environment."
    )


def _parse_target(answer: str, count: int) -> list[int]:
    """A 1-based sample number from the prompt, as a 0-based index."""
    try:
        index = int(answer) - 1
    except ValueError:
        return []
    return [index] if 0 <= index < count else []


if __name__ == "__main__":
    main()
