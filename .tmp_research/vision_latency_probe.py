"""Latency measurement, Phase 5 stop condition — VisionEngine.observe(mode='ui_tree') x5.

Mandated Phase-5 stop condition:
  If p95 > 250 ms on target hardware -> deferred note + skip Phase 5.
  If p95 <= 250 ms -> commit the implementation.

Run: python .tmp_research/vision_latency_probe.py

Output: 5 individual measurements + p95 + verdict (go/deferred).
"""
from __future__ import annotations

import asyncio
import statistics
import time

from jarvis.vision.engine import VisionEngine


async def main() -> int:
    engine = VisionEngine()
    runs = 5
    samples_ms: list[float] = []

    print(f"VisionEngine.observe(mode='ui_tree') × {runs} runs ...")
    print("=" * 60)

    for i in range(runs):
        t0 = time.perf_counter()
        try:
            obs = await engine.observe(mode="ui_tree")
            dt_ms = (time.perf_counter() - t0) * 1000.0
            samples_ms.append(dt_ms)
            window = obs.window_title or "(no title)"
            nodes = len(obs.nodes)
            print(f"  Run {i+1}: {dt_ms:7.2f} ms  window='{window[:40]}' nodes={nodes}")
        except Exception as exc:  # noqa: BLE001
            dt_ms = (time.perf_counter() - t0) * 1000.0
            print(f"  Run {i+1}: ERROR after {dt_ms:.2f} ms — {type(exc).__name__}: {exc}")
            samples_ms.append(dt_ms)

    await engine.close()

    print("=" * 60)
    if not samples_ms:
        print("No measurements — VisionEngine failed completely.")
        return 1

    mn = min(samples_ms)
    mx = max(samples_ms)
    avg = sum(samples_ms) / len(samples_ms)
    # p95 at 5 samples = the highest value (the 4th + 5th of 5 samples is effectively the max)
    sorted_ms = sorted(samples_ms)
    # statistics.quantiles needs n>=2; for 5 samples we take the highest
    # as p95 (conservative)
    p95 = sorted_ms[-1]
    median = statistics.median(samples_ms)

    print(f"Min:    {mn:7.2f} ms")
    print(f"Median: {median:7.2f} ms")
    print(f"Avg:    {avg:7.2f} ms")
    print(f"p95:    {p95:7.2f} ms (highest of the 5 samples)")
    print(f"Max:    {mx:7.2f} ms")
    print()
    threshold_ms = 250.0
    if p95 <= threshold_ms:
        print(f"VERDICT: go (p95 {p95:.2f} ms <= {threshold_ms:.0f} ms)")
        return 0
    print(f"VERDICT: deferred (p95 {p95:.2f} ms > {threshold_ms:.0f} ms)")
    return 2


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    raise SystemExit(exit_code)
