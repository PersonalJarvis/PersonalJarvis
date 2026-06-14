"""Per-stage latency probe for a SIMPLE voice request, with REAL providers.

Complements scripts/latency_bench.py (router decision + brain TTFT) with the
two stages that bench cannot see: cloud STT (final utterance transcription)
and cloud TTS (time-to-first-audio-chunk). Together they reconstruct the full
post-endpoint hot path of one simple turn:

    VAD endpoint -> STT final -> [pre-brain guards] -> brain TTFT
        -> first sentence -> TTS first chunk -> playback start

No microphone, no speaker, no running app needed. The STT input is generated
by the TTS provider itself (a real ~2 s German utterance), so the probe stays
self-contained while exercising the exact provider classes + config the live
pipeline uses (``build_stt_from_config`` / ``build_tts_from_config``).

Usage:
    python scripts/measure_voice_stages.py            # 3 runs per stage
    python scripts/measure_voice_stages.py --runs 5
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from jarvis.core.config import load_config  # noqa: E402

PROBE_SENTENCE = "Es ist gerade kurz nach sieben Uhr abends, Chef."
PROBE_QUESTION_PCM_RATE = 24_000  # Gemini TTS output rate


def _ms(start_ns: int, end_ns: int) -> float:
    return (end_ns - start_ns) / 1_000_000


async def _measure_tts(tts, text: str) -> tuple[float, float, bytes]:
    """Return (first_chunk_ms, total_ms, pcm) for one synthesize call."""
    t0 = time.perf_counter_ns()
    first_ms = float("nan")
    pieces: list[bytes] = []
    try:
        gen = tts.synthesize(text, language_code="de-DE")
    except TypeError:
        gen = tts.synthesize(text)
    async for chunk in gen:
        if first_ms != first_ms:  # still NaN
            first_ms = _ms(t0, time.perf_counter_ns())
        pieces.append(bytes(chunk.pcm))
    total_ms = _ms(t0, time.perf_counter_ns())
    return first_ms, total_ms, b"".join(pieces)


async def main(runs: int) -> int:
    cfg = load_config(Path("jarvis.toml"))
    print(f"stt.provider={cfg.stt.provider}  tts.provider={cfg.tts.provider}")
    print(f"runs per stage: {runs} (run 1 = cold start incl. client init)\n")

    # --- TTS: time-to-first-chunk is what gates audible output -----------
    from jarvis.plugins.tts import build_tts_from_config

    tts = build_tts_from_config(cfg.tts)
    print(f"TTS [{type(tts).__name__}]  text={PROBE_SENTENCE!r}")
    probe_pcm = b""
    for i in range(runs):
        first_ms, total_ms, pcm = await _measure_tts(tts, PROBE_SENTENCE)
        probe_pcm = pcm or probe_pcm
        audio_s = len(pcm) / 2 / PROBE_QUESTION_PCM_RATE
        tag = "cold" if i == 0 else "warm"
        print(
            f"  run {i + 1} ({tag}): first_chunk={first_ms:8.1f} ms   "
            f"total={total_ms:8.1f} ms   audio={audio_s:4.1f} s"
        )

    if not probe_pcm:
        print("TTS produced no audio — cannot run the STT stage.")
        return 1

    # --- STT: final-utterance transcription (the blocking pipeline stage) -
    from jarvis.plugins.stt import build_stt_from_config

    stt = build_stt_from_config(cfg.stt)
    print(f"\nSTT [{type(stt).__name__}]  input={len(probe_pcm) / 1024:.0f} KB PCM")
    for i in range(runs):
        t0 = time.perf_counter_ns()
        transcript = await stt.transcribe_pcm(
            probe_pcm, sample_rate=PROBE_QUESTION_PCM_RATE
        )
        elapsed = _ms(t0, time.perf_counter_ns())
        tag = "cold" if i == 0 else "warm"
        print(
            f"  run {i + 1} ({tag}): {elapsed:8.1f} ms   "
            f"text={transcript.text[:60]!r}"
        )
    return 0


if __name__ == "__main__":
    from _grpc_exit import hard_exit  # noqa: E402 — sibling helper in scripts/

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    # Leaked gRPC threads from real Gemini/Vertex calls would hang exit.
    hard_exit(asyncio.run(main(args.runs)))
