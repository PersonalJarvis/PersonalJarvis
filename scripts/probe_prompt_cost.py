"""Isolate the warm brain-TTFT cost: full router system prompt vs a tiny one.

The e2e harness showed brain first-token at 8.8 s cold / ~2-3 s warm for a
trivial question ("capital of France"). A flash model answers that in well
under a second, so the suspicion is the ~8-9k-token system prompt being
re-ingested every turn — not the model. This probe proves it: same model,
same question, warm, measured first with the real full ``_build_system_prompt``
output and then with a 60-char stub. A large gap = the prompt is the cost.

Usage: python scripts/probe_prompt_cost.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from jarvis.core.bus import EventBus  # noqa: E402
from jarvis.core.config import load_config  # noqa: E402

UTTERANCE = "Was ist die Hauptstadt von Frankreich?"  # i18n-allow: simulated German user utterance used to probe prompt cost
RUNS = 3


async def _ttft(bm, text: str) -> float:
    if hasattr(bm, "_history"):
        bm._history.clear()
    t0 = time.perf_counter_ns()
    first = float("nan")
    async for ch in bm.generate_stream(text):
        if ch and first != first:
            first = (time.perf_counter_ns() - t0) / 1_000_000
    return first


def _med(xs: list[float]) -> float:
    s = sorted(xs)
    return s[len(s) // 2] if s else float("nan")


async def main() -> int:
    cfg = load_config(Path("jarvis.toml"))
    from jarvis.brain.manager import BrainManager

    bm = BrainManager(config=cfg, bus=EventBus(), tools={}, tool_executor=None)
    try:
        from jarvis.brain.router import SYSTEM_PROMPT as ROUTER_SYSTEM_PROMPT
        bm._system_prompt_extra = ROUTER_SYSTEM_PROMPT
    except Exception as exc:  # noqa: BLE001
        print(f"(router prompt inject skipped: {exc})", flush=True)

    full = bm._build_system_prompt()
    print(f"model={cfg.brain.router.model}  full system prompt={len(full)} chars "
          f"(~{len(full) // 4} tokens)\n", flush=True)

    print("-- FULL prompt (warm; run 1 is cold) --", flush=True)
    full_ms: list[float] = []
    for i in range(RUNS):
        ms = await asyncio.wait_for(_ttft(bm, UTTERANCE), timeout=45)
        full_ms.append(ms)
        print(f"  run {i + 1}: {ms:.0f} ms", flush=True)

    tiny = "You are a concise voice assistant. Answer in one short sentence."
    bm._build_system_prompt = lambda *a, **k: tiny  # type: ignore[method-assign]
    print(f"\n-- STRIPPED prompt ({len(tiny)} chars) --", flush=True)
    tiny_ms: list[float] = []
    for i in range(RUNS):
        ms = await asyncio.wait_for(_ttft(bm, UTTERANCE), timeout=45)
        tiny_ms.append(ms)
        print(f"  run {i + 1}: {ms:.0f} ms", flush=True)

    warm_full = _med(full_ms[1:]) if len(full_ms) > 1 else _med(full_ms)
    warm_tiny = _med(tiny_ms[1:]) if len(tiny_ms) > 1 else _med(tiny_ms)
    print("\n=== VERDICT ===", flush=True)
    print(f"warm median TTFT  full={warm_full:.0f} ms   stripped={warm_tiny:.0f} ms",
          flush=True)
    if warm_tiny > 0:
        print(f"the {len(full)}-char prompt adds ~{warm_full - warm_tiny:.0f} ms "
              f"({warm_full / warm_tiny:.1f}x) to every warm turn", flush=True)
    return 0


if __name__ == "__main__":
    from _grpc_exit import hard_exit  # noqa: E402 — sibling helper in scripts/

    # Leaked gRPC threads from real Gemini/Vertex calls would hang exit.
    hard_exit(asyncio.run(main()))
