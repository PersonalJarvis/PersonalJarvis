"""Voice hot-path latency benchmark (Wave 0 — omni-latency suite).

Feeds simulated utterances through the router-tier BrainManager (text-in, no
STT/TTS/audio) and reports p50/p95 for the metrics we can measure offline:

  * router_decision_ms  — ``_should_force_openclaw`` heuristic (SLO: p95 < 150ms)
  * prompt_build_ms     — ``_build_system_prompt`` assembly cost (Wave 2 target)
  * prompt_chars        — system-prompt size (cache-friendliness proxy)
  * first_token_ms      — real provider TTFT          (``--real`` only)
  * total_ms            — real full-response latency   (``--real`` only)

Default mode is key-free and deterministic (no provider call) so it runs in CI
and on a 1-vCPU VPS. ``--real`` hits the configured provider for true TTFT
(needs API keys + network) — use it to see the Wave 1/2/3 cache + payload wins.

Usage:
    python scripts/latency_bench.py                 # offline: routing + prompt
    python scripts/latency_bench.py --real --runs 5 # live provider TTFT
    python scripts/latency_bench.py --assert-slo    # exit 1 if an SLO is missed
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Windows cp1252 stdout would mangle the ✓/µ glyphs below (CLAUDE.md Unicode rule).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from jarvis.core.bus import EventBus  # noqa: E402
from jarvis.core.config import load_config  # noqa: E402
from jarvis.core.events import BrainTTFT, LatencySpan  # noqa: E402

# (category, utterance). Mix of the turn classes the suite cares about: cheap
# smalltalk/knowledge turns (snappiness-sensitive), screen-reference turns
# (must keep vision), and action turns (force-spawn path).
SCENARIOS: list[tuple[str, str]] = [  # i18n-allow: simulated German user utterances driving the router latency probe
    ("smalltalk", "hallo jarvis"),
    ("smalltalk", "wie spät ist es"),  # i18n-allow: simulated German user utterance under test
    ("smalltalk", "danke dir"),
    ("knowledge", "was ist die hauptstadt von frankreich"),  # i18n-allow: simulated German user utterance under test
    ("knowledge", "erklär mir kurz was ein vektor ist"),  # i18n-allow: simulated German user utterance under test
    ("screen_ref", "was siehst du hier auf dem bildschirm"),  # i18n-allow: simulated German user utterance under test
    ("screen_ref", "was ist das hier"),  # i18n-allow: simulated German user utterance under test
    ("action", "öffne den browser"),  # i18n-allow: simulated German user utterance under test
    ("action", "repariere den bug in main.py"),
    ("action", "starte einen subagenten für die recherche"),  # i18n-allow: simulated German user utterance under test
]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _fmt(values: list[float]) -> str:
    if not values:
        return "    n/a"
    return f"p50={_percentile(values, 50):7.2f}  p95={_percentile(values, 95):7.2f}"


def _build_brain(bus: EventBus):
    """Mirror voice_e2e_probe.py: prefer the full router build, fall back."""
    from jarvis.brain.factory import build_default_brain

    try:
        bm = build_default_brain(tier="router", bus=bus)
        print("Brain: build_default_brain(tier='router') ✓")
        return bm
    except Exception as exc:  # noqa: BLE001
        from jarvis.brain.manager import BrainManager

        print(f"Brain: factory failed ({type(exc).__name__}: {exc}); direct BrainManager")
        cfg = load_config(Path("jarvis.toml"))
        bm = BrainManager(config=cfg, bus=bus, tools={}, tool_executor=None)
        try:
            from jarvis.brain.router import SYSTEM_PROMPT as ROUTER_SYSTEM_PROMPT

            bm._system_prompt_extra = ROUTER_SYSTEM_PROMPT  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            print(f"  (router system-prompt inject skipped: {exc})")
        return bm


async def _measure_real_ttft(bm, text: str) -> tuple[float, float]:
    """Return (first_token_ms, total_ms) for one live streamed turn."""
    if hasattr(bm, "_history"):
        bm._history.clear()  # type: ignore[attr-defined]
    t0 = time.perf_counter_ns()
    first_ms = float("nan")
    saw_token = False
    async for chunk in bm.generate_stream(text):
        if chunk and not saw_token:
            saw_token = True
            first_ms = (time.perf_counter_ns() - t0) / 1_000_000
    total_ms = (time.perf_counter_ns() - t0) / 1_000_000
    return first_ms, total_ms


async def run_bench(runs: int, real: bool, assert_slo: bool) -> int:
    cfg = load_config(Path("jarvis.toml"))
    print(f"Primary brain: {cfg.brain.primary} / router={cfg.brain.router.model}")
    print(f"Flags: streaming_tts={cfg.performance.streaming_tts} "
          f"anthropic_cache={cfg.performance.anthropic_prompt_cache} "
          f"gemini_cache={cfg.performance.gemini_context_cache} "
          f"latency={cfg.latency.enabled}")

    bus = EventBus()
    captured: list[object] = []

    async def _capture(event: object) -> None:
        if isinstance(event, (LatencySpan, BrainTTFT)):
            captured.append(event)

    bus.subscribe_all(_capture)

    bm = _build_brain(bus)

    # --- prompt build cost + size (utterance-independent in the offline path) ---
    prompt_build_ms: list[float] = []
    for _ in range(max(runs, 20)):
        t = time.perf_counter_ns()
        prompt = bm._build_system_prompt()  # type: ignore[attr-defined]
        prompt_build_ms.append((time.perf_counter_ns() - t) / 1_000_000)
    prompt_chars = len(prompt)
    print()
    print(f"System prompt: {prompt_chars} chars   build {_fmt(prompt_build_ms)} ms")
    print()

    # --- per-scenario routing decision + (optional) real TTFT ---
    print(f"{'category':<11} {'router_decision (ms)':<26} "
          + ("first_token (ms)        total (ms)" if real else ""))
    print("-" * (37 + (40 if real else 0)))

    router_all: list[float] = []
    ttft_all: list[float] = []
    by_cat_router: dict[str, list[float]] = {}
    by_cat_ttft: dict[str, list[float]] = {}

    for cat, text in SCENARIOS:
        # Renamed during the 2026-06-10 spawn-threshold rework; support both
        # so the bench keeps running on older checkouts.
        decide = (
            getattr(bm, "_should_force_spawn", None) or bm._should_force_openclaw
        )
        decision_ms: list[float] = []
        for _ in range(max(runs * 5, 50)):
            t = time.perf_counter_ns()
            decide(text)
            decision_ms.append((time.perf_counter_ns() - t) / 1_000_000)
        router_all.extend(decision_ms)
        by_cat_router.setdefault(cat, []).extend(decision_ms)

        line = f"{cat:<11} {_fmt(decision_ms):<26}"
        if real:
            firsts: list[float] = []
            totals: list[float] = []
            for _ in range(runs):
                try:
                    f_ms, t_ms = await _measure_real_ttft(bm, text)
                    if f_ms == f_ms:  # not NaN
                        firsts.append(f_ms)
                    totals.append(t_ms)
                except Exception as exc:  # noqa: BLE001
                    print(f"  [{cat}] real call failed: {type(exc).__name__}: {exc}")
            ttft_all.extend(firsts)
            by_cat_ttft.setdefault(cat, []).extend(firsts)
            line += f"  {_fmt(firsts):<24}{_fmt(totals)}"
        print(f"{text[:34]:<35}")
        print(f"  {line}")

    print()
    print("=" * 60)
    print("AGGREGATE")
    print("=" * 60)
    print(f"router_decision  {_fmt(router_all)} ms   (SLO: p95 < 150 ms)")
    if real:
        print(f"first_token      {_fmt(ttft_all)} ms   (SLO: p95 < 3000 ms)")
    print(f"LatencySpan/BrainTTFT events captured on bus: {len(captured)}")

    if assert_slo:
        failures = []
        r95 = _percentile(router_all, 95)
        if r95 >= 150.0:
            failures.append(f"router_decision p95 {r95:.1f}ms >= 150ms")
        if real and ttft_all:
            t95 = _percentile(ttft_all, 95)
            if t95 >= 3000.0:
                failures.append(f"first_token p95 {t95:.1f}ms >= 3000ms")
        if failures:
            print("\nSLO FAIL:")
            for f in failures:
                print(f"  ✗ {f}")
            return 1
        print("\nSLO PASS ✓")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Voice hot-path latency benchmark")
    parser.add_argument("--runs", type=int, default=3, help="repeats per scenario")
    parser.add_argument("--real", action="store_true", help="hit the live provider for TTFT")
    parser.add_argument("--assert-slo", action="store_true", help="exit 1 on SLO miss")
    args = parser.parse_args()
    return asyncio.run(run_bench(args.runs, args.real, args.assert_slo))


if __name__ == "__main__":
    from _grpc_exit import hard_exit  # noqa: E402 — sibling helper in scripts/

    # --real hits live providers, leaking non-daemon gRPC threads that would
    # otherwise hang process exit for minutes. hard_exit flushes + os._exit.
    hard_exit(main())
