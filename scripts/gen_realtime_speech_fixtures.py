"""Generate the committed speech fixtures for the codex live-call probe.

Run ONCE (or when a sentence changes) by a developer with a working TTS
provider; the WAVs are committed so RUNNING the probe needs no TTS key at
all. Uses the normal capability-resolved TTS chain (``build_tts_from_config``,
AP-22) exactly like ``scripts/measure_voice_stages.py``.

The sentences are NONCE questions: each carries an invented word or object
that cannot occur in ordinary conversation, so the probe's role-play check
can match the literal question inside an assistant transcript without false
positives. Room tone is generated deterministically at ~-55 dBFS - genuinely
quiet, but never digital zero, which would cheat the energy gates the probe
exists to exercise.

Usage::

    python scripts/gen_realtime_speech_fixtures.py           # write fixtures
    python scripts/gen_realtime_speech_fixtures.py --dry-run # list only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import wave
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from jarvis.core.config import load_config  # noqa: E402

TARGET_RATE = 24_000  # the codex adapter's _INPUT_RATE
FIXTURE_DIR = REPO / "tests" / "fixtures" / "audio" / "realtime"
MAX_FILE_BYTES = 500_000
NOISE_SECONDS = 8.0
NOISE_PEAK = 58  # ~-55 dBFS on int16 full scale
NOISE_SEED = 20260806

#: id, language, BCP-47 for TTS, the sentence, and its load-bearing nonce.
#: German sentences are speech-input vocabulary for the probe (§1 category 3;
#: this file and the manifest are entries in scripts/ci/german-allowlist.txt).
SENTENCES: tuple[dict[str, str], ...] = (
    {
        "id": "en_giraffe",
        "language": "en",
        "bcp47": "en-US",
        "text": "What color is the violet giraffe from my example? Answer in one short sentence.",
        "nonce": "violet giraffe",
    },
    {
        "id": "en_tandelbrook",
        "language": "en",
        "bcp47": "en-US",
        "text": "Please repeat my invented word tandelbrook exactly once.",
        "nonce": "tandelbrook",
    },
    {
        "id": "en_bicycle",
        "language": "en",
        "bcp47": "en-US",
        "text": "How many wheels does my imaginary quintuple bicycle have?",
        "nonce": "quintuple bicycle",
    },
    {
        "id": "en_glimmerpond",
        "language": "en",
        "bcp47": "en-US",
        "text": "Name one word that rhymes with my invented word glimmerpond.",
        "nonce": "glimmerpond",
    },
    {
        "id": "en_zebrastorm",
        "language": "en",
        "bcp47": "en-US",
        "text": "In one sentence, what would a zebrastorm picnic be?",
        "nonce": "zebrastorm",
    },
    {
        "id": "de_giraffe",
        "language": "de",
        "bcp47": "de-DE",
        "text": (
            "Welche Farbe hat die violette Giraffe aus meinem Beispiel? "
            "Antworte in einem kurzen Satz."
        ),
        "nonce": "violette Giraffe",
    },
    {
        "id": "de_tandelbrook",
        "language": "de",
        "bcp47": "de-DE",
        "text": "Bitte wiederhole mein erfundenes Wort Tandelbrook genau einmal.",
        "nonce": "Tandelbrook",
    },
    {
        "id": "de_fahrrad",
        "language": "de",
        "bcp47": "de-DE",
        "text": "Wie viele Räder hat mein erfundenes Fünffach-Fahrrad?",
        "nonce": "Fünffach-Fahrrad",
    },
)


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(TARGET_RATE)
        handle.writeframes(pcm)


def _room_noise() -> bytes:
    # Deterministic room tone, not cryptography - reproducible fixtures beat
    # secure randomness here.
    rng = random.Random(NOISE_SEED)  # noqa: S311
    samples = int(NOISE_SECONDS * TARGET_RATE)
    return b"".join(
        rng.randint(-NOISE_PEAK, NOISE_PEAK).to_bytes(2, "little", signed=True)
        for _ in range(samples)
    )


async def _synthesize(tts, text: str, bcp47: str) -> tuple[bytes, int]:
    """Return (pcm, source_rate) for one sentence via the real TTS chain."""
    pieces: list[bytes] = []
    rate = TARGET_RATE
    try:
        stream = tts.synthesize(text, language_code=bcp47)
    except TypeError:
        stream = tts.synthesize(text)
    async for chunk in stream:
        pieces.append(bytes(chunk.pcm))
        rate = int(getattr(chunk, "sample_rate", TARGET_RATE) or TARGET_RATE)
    return b"".join(pieces), rate


def _resample(pcm: bytes, source_rate: int) -> bytes:
    if source_rate == TARGET_RATE:
        return pcm
    from jarvis.realtime.audio import StreamingPcm16Resampler

    # One whole utterance in one call; the resampler's un-flushed tail is a
    # single trailing sample and does not matter for a fixture.
    return StreamingPcm16Resampler(source_rate, TARGET_RATE).process(pcm)


async def main(dry_run: bool) -> int:
    if dry_run:
        for entry in SENTENCES:
            print(f"{entry['id']:16} [{entry['language']}] {entry['text']}")
        print("room_noise       [--] deterministic ~-55 dBFS room tone")
        return 0

    cfg = load_config(REPO / "jarvis.toml")
    from jarvis.plugins.tts import build_tts_from_config

    tts = build_tts_from_config(cfg.tts)
    print(f"TTS chain: {type(tts).__name__}")
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    manifest: list[dict[str, object]] = []
    for entry in SENTENCES:
        pcm, rate = await _synthesize(tts, entry["text"], entry["bcp47"])
        if not pcm:
            print(f"ERROR: TTS produced no audio for {entry['id']}")
            return 1
        pcm = _resample(pcm, rate)
        duration_ms = int(len(pcm) / 2 / TARGET_RATE * 1000)
        path = FIXTURE_DIR / f"{entry['id']}.wav"
        _write_wav(path, pcm)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            print(
                f"ERROR: {path.name} is {size} bytes (cap {MAX_FILE_BYTES}) - "
                "shorten the sentence"
            )
            return 1
        manifest.append(
            {
                "id": entry["id"],
                "path": path.name,
                "language": entry["language"],
                "text": entry["text"],
                "nonce": entry["nonce"],
                "duration_ms": duration_ms,
                "sample_rate": TARGET_RATE,
            }
        )
        print(f"  {path.name}: {duration_ms} ms, {size / 1024:.0f} KB")

    noise = _room_noise()
    noise_path = FIXTURE_DIR / "room_noise.wav"
    _write_wav(noise_path, noise)
    manifest.append(
        {
            "id": "room_noise",
            "path": noise_path.name,
            "language": "",
            "text": "",
            "nonce": "",
            "duration_ms": int(NOISE_SECONDS * 1000),
            "sample_rate": TARGET_RATE,
        }
    )
    print(f"  {noise_path.name}: {int(NOISE_SECONDS * 1000)} ms (deterministic)")

    manifest_path = FIXTURE_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        from _grpc_exit import hard_exit  # noqa: PLC0415 - sibling helper
    except ImportError:  # pragma: no cover - helper is repo-local
        raise SystemExit(asyncio.run(main(args.dry_run))) from None
    hard_exit(asyncio.run(main(args.dry_run)))
