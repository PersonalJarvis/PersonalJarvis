"""Empirical probe: does Gemini TTS stream audio chunks incrementally?

Compares the current blocking call (``generate_content``) against the
streaming variant (``client.aio.models.generate_content_stream``) on the SAME
model + voice + config the live pipeline uses. Decides whether switching the
provider to the streaming API is a real time-to-first-audio win.

Usage: python scripts/probe_tts_streaming.py
"""
from __future__ import annotations

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

TEXT = "Es ist gerade kurz nach sieben Uhr abends, Chef. Soll ich noch etwas vorbereiten?"  # i18n-allow: sample TTS voice output text used to probe streaming


async def main() -> int:
    cfg = load_config(Path("jarvis.toml"))
    # Build the raw provider exactly like the factory does (no fallback wrap).
    from jarvis.plugins.tts import _build_provider

    tts = _build_provider(cfg.tts, "gemini-flash-tts")
    tts._ensure_client()
    gen_config = tts._build_config(tts._default_voice)
    client = tts._client
    model = tts._model_name
    print(f"model={model} voice={tts._default_voice} vertex={tts._use_vertex}\n")

    for run in range(2):
        t0 = time.perf_counter_ns()
        chunk_log: list[tuple[float, int]] = []
        async for chunk in await client.aio.models.generate_content_stream(
            model=model, contents=TEXT, config=gen_config,
        ):
            now_ms = (time.perf_counter_ns() - t0) / 1_000_000
            size = 0
            for cand in chunk.candidates or []:
                content = cand.content
                for part in (content.parts if content else None) or []:
                    if part.inline_data and part.inline_data.data:
                        size += len(part.inline_data.data)
            chunk_log.append((now_ms, size))
        total_ms = (time.perf_counter_ns() - t0) / 1_000_000
        audio_chunks = [(t, s) for t, s in chunk_log if s > 0]
        first_ms = audio_chunks[0][0] if audio_chunks else float("nan")
        total_bytes = sum(s for _, s in audio_chunks)
        print(
            f"run {run + 1}: chunks={len(audio_chunks)}  "
            f"first_audio={first_ms:.0f} ms  stream_end={total_ms:.0f} ms  "
            f"audio={total_bytes / 2 / 24000:.1f} s"
        )
        for t, s in audio_chunks[:6]:
            print(f"    +{t:7.0f} ms  {s / 1024:6.1f} KB")
    return 0


if __name__ == "__main__":
    from _grpc_exit import hard_exit  # noqa: E402 — sibling helper in scripts/

    # Leaked gRPC threads from real Gemini/Vertex calls would hang exit.
    hard_exit(asyncio.run(main()))
