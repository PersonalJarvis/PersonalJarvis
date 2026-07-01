"""Smoke script for router-permanent-vision (Wave 3 phase B + D).

Bootstraps a mock stack (FakeVisionEngine + RouterBrain +
FakeBrain) and runs two measurements:

1. Happy-path smoke: 1 Brain call with user text "was siehst du auf
   meinem screen" — asserts that an ImageBlock arrives and the hash is non-empty.  # i18n-allow

2. Latency benchmark: 20 Brain calls with vision + 20 without. Measures
   mean/p95 inject overhead + mean image bytes per call.

Starts in <5s on a normal dev setup. No real API keys, no
audio recording.

Output: JSON on stdout + a human-readable summary. On error:
exit code != 0.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import statistics
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

# Add repo root to sys.path in case the script is invoked from scripts/
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _make_png_file(size_kb: int) -> tuple[str, bytes]:
    header = b"\x89PNG\r\n\x1a\n"
    filler = b"x" * max(0, size_kb * 1024 - len(header))
    data = header + filler
    fh = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    try:
        fh.write(data)
        fh.flush()
    finally:
        fh.close()
    return fh.name, data


class FakeVisionEngine:
    def __init__(self, png_path: str, png_hash: str = "smoke-hash") -> None:
        self._path = png_path
        self._hash = png_hash
        self.calls = 0

    async def observe(self, *, mode: str = "screenshot", **_: Any):
        from jarvis.core.protocols import Observation
        self.calls += 1
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=self._path,
            screenshot_hash=self._hash,
            nodes=(),
            window_title="smoke",
            active_pid=os.getpid(),
            source="screenshot_only",
            pruning_stats={},
        )


async def _build_router(vision_provider: Any | None = None):
    from jarvis.brain.router import RouterBrain
    from jarvis.brain.streaming import StreamingAggregate
    from jarvis.core.bus import EventBus
    from jarvis.core.config import (
        BrainProviderConfig, BrainTierConfig, JarvisConfig,
    )
    from jarvis.core.protocols import BrainDelta, BrainRequest, ToolResult

    class _FakeTool:
        name = "bash"; description = ""; risk_tier = "monitor"
        schema = {"type": "object", "properties": {}}
        async def execute(self, args, ctx):
            return ToolResult(success=True, output="ok")

    class _FakeBrain:
        name = "fake"; context_window = 8192
        supports_tools = True; supports_vision = True
        async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
            yield BrainDelta(content="ok")
            yield BrainDelta(finish_reason="stop")

    class _NoopExec:
        async def execute(self, tool, args, **_):
            return await tool.execute(args, ctx=None)

    class _Recorder:
        def __init__(self):
            self.calls = []
        def tools_payload(self):
            return []
        async def dispatch(self, user_text, *, images=(), history=None, trace_id=None):
            self.calls.append({"user_text": user_text, "images": images})
            agg = StreamingAggregate(); agg.text = "ok"; agg.finish_reason = "stop"
            return agg

    cfg = JarvisConfig()
    cfg.brain.providers["fake"] = BrainProviderConfig(model="fake", deep_model="fake")
    cfg.brain.router = BrainTierConfig(
        provider="fake", model="fake",
        fallback_provider="fake", fallback_model="fake",
    )
    cfg.brain.sub_jarvis = BrainTierConfig(provider="fake", model="fake")
    bus = EventBus()

    router = RouterBrain(
        cfg, bus,
        tools={"bash": _FakeTool()},
        tool_executor=_NoopExec(),
        vision_provider=vision_provider,
    )
    router.manager._brain_cache[("fake", "fake")] = _FakeBrain()
    recorder = _Recorder()
    router.manager._build_dispatcher = lambda _b: recorder  # type: ignore[method-assign]
    return router, recorder


async def phase_smoke(png_path: str) -> dict[str, Any]:
    """Happy-path smoke: 1 Brain call with vision active."""
    from jarvis.vision.context_provider import VisionContextProvider

    engine = FakeVisionEngine(png_path, png_hash="smoke-hash-abc")
    provider = VisionContextProvider(engine, refresh_interval_s=0.05)
    await provider.start()
    await asyncio.sleep(0.1)

    router, recorder = await _build_router(vision_provider=provider)

    t0 = time.perf_counter()
    [_ async for _ in router.handle("was siehst du auf meinem screen")]  # i18n-allow
    dt_ms = (time.perf_counter() - t0) * 1000

    await provider.stop()

    assert len(recorder.calls) == 1, "Recorder did not see a dispatch call"
    images = recorder.calls[0]["images"]
    assert len(images) == 1, f"Expected exactly 1 image, got {len(images)}"
    img = images[0]
    assert img.source_hash == "smoke-hash-abc", f"Hash mismatch: {img.source_hash}"
    assert img.data_b64, "ImageBlock has no data"

    return {
        "status": "PASS",
        "latency_ms": round(dt_ms, 2),
        "image_hash": img.source_hash,
        "image_raw_bytes": len(base64.b64decode(img.data_b64)),
    }


async def phase_benchmark(png_path: str, n: int = 20) -> dict[str, Any]:
    """N calls with vision, N without. Measurement: mean + p95 inject overhead."""
    from jarvis.vision.context_provider import VisionContextProvider

    # With vision
    engine = FakeVisionEngine(png_path)
    provider = VisionContextProvider(engine, refresh_interval_s=0.05)
    await provider.start()
    await asyncio.sleep(0.1)
    router_with, rec_with = await _build_router(vision_provider=provider)

    times_with: list[float] = []
    bytes_list: list[int] = []
    for _ in range(n):
        t0 = time.perf_counter()
        [_ async for _ in router_with.handle("ping")]
        times_with.append((time.perf_counter() - t0) * 1000)
    # Bytes from the last call
    for c in rec_with.calls:
        if c["images"]:
            bytes_list.append(len(base64.b64decode(c["images"][0].data_b64)))
    await provider.stop()

    # Without vision
    router_without, _ = await _build_router(vision_provider=None)
    times_without: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        [_ async for _ in router_without.handle("ping")]
        times_without.append((time.perf_counter() - t0) * 1000)

    def _stats(xs: list[float]) -> dict[str, float]:
        xs_sorted = sorted(xs)
        p95_idx = max(0, int(len(xs_sorted) * 0.95) - 1)
        return {
            "mean_ms": round(statistics.mean(xs), 2),
            "p95_ms": round(xs_sorted[p95_idx], 2),
            "min_ms": round(min(xs), 2),
            "max_ms": round(max(xs), 2),
        }

    overhead = [w - wo for w, wo in zip(times_with, times_without, strict=False)]

    return {
        "n": n,
        "with_vision": _stats(times_with),
        "without_vision": _stats(times_without),
        "inject_overhead": _stats(overhead),
        "bytes_per_call": {
            "mean": round(statistics.mean(bytes_list), 2) if bytes_list else 0,
            "count": len(bytes_list),
        },
    }


async def main() -> int:
    start = time.perf_counter()

    # 50 KB PNG as a realistic mock screenshot
    png_path, png_data = _make_png_file(size_kb=50)

    try:
        smoke = await phase_smoke(png_path)
        bench = await phase_benchmark(png_path, n=20)

        total_s = time.perf_counter() - start

        result = {
            "smoke": smoke,
            "benchmark": bench,
            "total_duration_s": round(total_s, 3),
        }

        print(json.dumps(result, indent=2))
        print()
        print("=" * 60)
        print(f"Smoke: {smoke['status']} (latency {smoke['latency_ms']} ms)")
        print(f"Image Hash: {smoke['image_hash']}")
        print(f"Image Bytes (raw): {smoke['image_raw_bytes']}")
        print()
        print(f"Benchmark ({bench['n']} calls per branch):")
        print(f"  With-Vision:    mean {bench['with_vision']['mean_ms']} ms, p95 {bench['with_vision']['p95_ms']} ms")
        print(f"  Without-Vision: mean {bench['without_vision']['mean_ms']} ms, p95 {bench['without_vision']['p95_ms']} ms")
        print(f"  Inject overhead: mean {bench['inject_overhead']['mean_ms']} ms, p95 {bench['inject_overhead']['p95_ms']} ms")
        print(f"  Bytes/Call: mean {bench['bytes_per_call']['mean']} bytes")
        print(f"Total duration: {total_s:.2f} s")
        print("=" * 60)

        if smoke["status"] != "PASS":
            return 1
        if bench["inject_overhead"]["mean_ms"] > 50.0:
            print("WARN: inject overhead > 50 ms — over the plan budget!", file=sys.stderr)
        if total_s > 5.0:
            print(f"WARN: total duration {total_s:.1f}s > 5s — plan limit exceeded.", file=sys.stderr)
        return 0
    finally:
        try:
            os.unlink(png_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
