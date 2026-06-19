"""Latenz-Messung Phase 5 Stop-Bedingung — VisionEngine.observe(mode='ui_tree') x5.

Mandat-Phase-5-Stop-Bedingung:
  Wenn p95 > 250 ms auf Ziel-Hardware -> deferred-Vermerk + skip Phase 5.
  Wenn p95 <= 250 ms -> Implementierung commit.

Run: python .tmp_research/vision_latency_probe.py

Output: 5 Einzel-Messungen + p95 + Verdict (go/deferred).
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
        print("Keine Messungen — VisionEngine schlug komplett fehl.")
        return 1

    mn = min(samples_ms)
    mx = max(samples_ms)
    avg = sum(samples_ms) / len(samples_ms)
    # p95 bei 5 samples = der höchste Wert (4. + 5. von 5 Samples ist faktisch max)
    sorted_ms = sorted(samples_ms)
    # statistics.quantiles braucht n>=2; für 5 Samples nehmen wir den höchsten
    # als p95 (konservativ)
    p95 = sorted_ms[-1]
    median = statistics.median(samples_ms)

    print(f"Min:    {mn:7.2f} ms")
    print(f"Median: {median:7.2f} ms")
    print(f"Avg:    {avg:7.2f} ms")
    print(f"p95:    {p95:7.2f} ms (hoechster der 5 Samples)")
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
