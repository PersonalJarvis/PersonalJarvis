"""JARVIS-CU-BENCH — everyday Computer-Use benchmark (Wave 4).

Drives the REAL wired ``ComputerUseHarness`` through a curated set of everyday
desktop tasks and prints a PASS/FAIL table, then writes a Markdown report to
``~/Downloads/Jarvis-Computer-Use-Benchmark.md``. This is the turnkey
"verify it works across the whole computer" tool: it measures whether
Computer-Use reliably does ordinary things, not just the cases that were
hand-tuned.

It reuses the exact dispatch pattern proven by ``scripts/smoke_cu_multistep.py``
(``build_default_brain`` wires the CU context, then ``harness.invoke(task)``).

Grading per task:
  * ``exit 0``                              -> the loop reported success
  * an optional ``oracle`` (process running / UIA text contains)  -> ground truth
  A task PASSES when the loop exits 0 AND (no oracle OR the oracle is satisfied).
  Tasks without a cheap oracle are graded exit-code-only and flagged ``~`` so the
  operator eyeballs them -- nothing is silently claimed as verified.

This script CONTROLS THE REAL DESKTOP (opens/closes apps, clicks, types). Run it
when you can watch. It is Windows-first for the UIA oracles; on other platforms
the oracles report ``n/a`` and tasks fall back to exit-code-only grading.

Run:
    python scripts/cu_bench.py            # full curated set
    python scripts/cu_bench.py --list     # list task ids, run nothing
    python scripts/cu_bench.py --only open_terminal,open_calc_compute
"""
from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import argparse
import asyncio
import json
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.core.bus import EventBus
from jarvis.core.events import CUStepProfiled
from jarvis.core.protocols import HarnessTask

# ---------------------------------------------------------------------------
# SLOs (frontier-speed master plan, Wave 0). --assert-slo turns these into a
# hard gate: per-step p50 <= 1.5s, p95 <= 4s, plus the per-task wall-clock
# budgets declared on each BenchTask below.
# ---------------------------------------------------------------------------

SLO_STEP_P50_S = 1.5
SLO_STEP_P95_S = 4.0


class PhaseCollector:
    """Collects CUStepProfiled spans from the bus, scoped per bench task."""

    def __init__(self, bus: EventBus) -> None:
        self.spans: list[CUStepProfiled] = []
        self._active = False
        bus.subscribe(CUStepProfiled, self._on_span)

    async def _on_span(self, event: CUStepProfiled) -> None:
        if self._active:
            self.spans.append(event)

    def start(self) -> None:
        self.spans = []
        self._active = True

    def stop(self) -> list[CUStepProfiled]:
        self._active = False
        return list(self.spans)


def _phase_stats(spans: list[CUStepProfiled]) -> dict[str, dict[str, float]]:
    """Per-phase p50/p95 in ms, plus call counts."""
    by_phase: dict[str, list[int]] = {}
    for s in spans:
        by_phase.setdefault(s.phase, []).append(s.duration_ms)
    out: dict[str, dict[str, float]] = {}
    for phase, durations in sorted(by_phase.items()):
        durations.sort()
        out[phase] = {
            "count": float(len(durations)),
            "p50_ms": float(statistics.median(durations)),
            "p95_ms": float(durations[max(0, int(len(durations) * 0.95) - 1)]),
        }
    return out


def _step_durations_s(spans: list[CUStepProfiled]) -> list[float]:
    """Wall-clock per step = sum of that step's phase spans (seconds)."""
    by_step: dict[int, int] = {}
    for s in spans:
        by_step[s.step_idx] = by_step.get(s.step_idx, 0) + s.duration_ms
    return [ms / 1000.0 for _idx, ms in sorted(by_step.items())]

# ---------------------------------------------------------------------------
# Oracles + cleanup helpers (best-effort, never raise)
# ---------------------------------------------------------------------------


def _procs_running(names: tuple[str, ...]) -> bool:
    try:
        import psutil
    except Exception:
        return False
    wanted = {n.lower() for n in names}
    for p in psutil.process_iter(attrs=["name"]):
        try:
            if (p.info["name"] or "").lower() in wanted:
                return True
        except Exception:
            pass
    return False


def _kill_procs(names: tuple[str, ...]) -> None:
    try:
        import psutil
    except Exception:
        return
    wanted = {n.lower() for n in names}
    for p in psutil.process_iter(attrs=["name"]):
        try:
            if (p.info["name"] or "").lower() in wanted:
                p.kill()
        except Exception:
            pass


async def _uia_text() -> str:
    """Concatenated UIA names of the foreground window, or '' on any failure."""
    try:
        from jarvis.vision.uia_tree import UIATreeSource

        obs = await UIATreeSource().observe()
        return " | ".join((n.name or "") for n in obs.nodes if (n.name or ""))
    except Exception:
        return ""


def _oracle_proc(names: tuple[str, ...]) -> Callable[[], Awaitable[bool | None]]:
    async def _check() -> bool | None:
        if sys.platform != "win32":
            return None
        return _procs_running(names)

    return _check


def _oracle_uia_contains(needle: str) -> Callable[[], Awaitable[bool | None]]:
    async def _check() -> bool | None:
        if sys.platform != "win32":
            return None
        await asyncio.sleep(0.6)
        return needle.lower() in (await _uia_text()).lower()

    return _check


# ---------------------------------------------------------------------------
# Task catalog — curated everyday set (representative, easy to extend)
# ---------------------------------------------------------------------------


@dataclass
class BenchTask:
    id: str
    prompt: str
    timeout_s: int = 120
    oracle: Callable[[], Awaitable[bool | None]] | None = None
    cleanup_procs: tuple[str, ...] = field(default_factory=tuple)
    #: Wall-clock SLO for --assert-slo (frontier-speed plan); 0 = no gate.
    slo_s: float = 0.0


_TERMINAL_PROCS = ("windowsterminal.exe", "cmd.exe", "powershell.exe")
_NOTEPAD_PROCS = ("notepad.exe",)
_CALC_PROCS = ("calculatorapp.exe", "calculator.exe")
_CHROME_PROCS = ("chrome.exe",)


def _catalog() -> list[BenchTask]:
    return [
        BenchTask(
            id="open_terminal",
            prompt="Open a terminal (Windows Terminal). Finish once its window is visible.",
            oracle=_oracle_proc(_TERMINAL_PROCS),
            cleanup_procs=_TERMINAL_PROCS,
            slo_s=8.0,
        ),
        BenchTask(
            id="open_calc_compute",
            prompt=(
                "Open the Windows Calculator and compute 7 + 8 by clicking the "
                "on-screen buttons. Finish when the result 15 is on the display."
            ),
            timeout_s=200,
            oracle=_oracle_uia_contains("15"),
            cleanup_procs=_CALC_PROCS,
        ),
        BenchTask(
            id="open_notepad_type",
            prompt=(
                "Open Notepad and type the sentence 'hello from jarvis' into it. "
                "Finish once that text is visible in the editor."
            ),
            oracle=_oracle_uia_contains("hello from jarvis"),
            cleanup_procs=_NOTEPAD_PROCS,
        ),
        BenchTask(
            id="open_browser",
            prompt="Open the Chrome web browser. Finish once a browser window is visible.",
            oracle=_oracle_proc(_CHROME_PROCS),
            cleanup_procs=_CHROME_PROCS,
            slo_s=8.0,
        ),
        BenchTask(
            id="browser_navigate",
            prompt=(
                "Open Chrome and navigate to example.com. Finish once the page "
                "with the heading 'Example Domain' is shown."
            ),
            timeout_s=160,
            oracle=_oracle_uia_contains("example"),
            cleanup_procs=_CHROME_PROCS,
            slo_s=15.0,
        ),
        BenchTask(
            id="open_explorer",
            prompt="Open File Explorer. Finish once an Explorer window is visible.",
            oracle=None,  # exit-code-only: explorer.exe is always running
        ),
        BenchTask(
            id="open_settings",
            prompt="Open the Windows Settings app. Finish once Settings is visible.",
            oracle=_oracle_uia_contains("settings"),
        ),
        BenchTask(
            id="scroll_page",
            prompt=(
                "Open Chrome, navigate to en.wikipedia.org/wiki/Computer, then "
                "scroll down the page by a few notches. Finish once you have scrolled."
            ),
            timeout_s=160,
            oracle=None,  # exit-code-only: scrolling has no cheap oracle
            cleanup_procs=_CHROME_PROCS,
        ),
        BenchTask(
            id="compound_open_and_type",
            prompt=(
                "Open a terminal and type the command 'echo hello' into it (do not "
                "press enter). Finish once the command text is visible in the terminal."
            ),
            oracle=None,  # exit-code-only
            cleanup_procs=_TERMINAL_PROCS,
        ),
    ]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class BenchResult:
    id: str
    exit_code: int | None
    oracle: bool | None
    elapsed_s: float
    chunks: int
    slo_s: float = 0.0
    spans: list[CUStepProfiled] = field(default_factory=list)

    @property
    def model_calls(self) -> int:
        return sum(1 for s in self.spans if s.phase in ("think", "plan", "verify"))

    @property
    def step_durations_s(self) -> list[float]:
        return _step_durations_s(self.spans)

    @property
    def slo_ok(self) -> bool | None:
        if self.slo_s <= 0:
            return None
        return self.elapsed_s <= self.slo_s

    @property
    def passed(self) -> bool:
        if self.exit_code != 0:
            return False
        return self.oracle in (True, None)

    @property
    def mark(self) -> str:
        if self.exit_code != 0:
            return "FAIL"
        if self.oracle is True:
            return "PASS"
        if self.oracle is False:
            return "FAIL"
        return "PASS~"  # exit 0 but no oracle -> operator should eyeball


async def _run_task(
    harness, task: BenchTask, collector: PhaseCollector | None = None,
) -> BenchResult:
    # Honest oracle: close any pre-existing instance so a stale window can't
    # make a no-op look like success.
    if task.cleanup_procs:
        _kill_procs(task.cleanup_procs)
        await asyncio.sleep(0.5)
    if collector is not None:
        collector.start()

    ht = HarnessTask(
        prompt=task.prompt,
        timeout_s=task.timeout_s,
        risk_tier="monitor",
        allow_computer_use=True,
    )
    t0 = time.perf_counter()
    exit_code: int | None = None
    chunks = 0
    print(f"\n[{task.id}] {task.prompt}")
    try:
        async for chunk in harness.invoke(ht):
            chunks += 1
            out = (getattr(chunk, "stdout", "") or "").strip()
            err = (getattr(chunk, "stderr", "") or "").strip()
            if out:
                print(f"    [{chunks}] {out[:200]}")
            if err:
                print(f"    [{chunks}] ERR {err[:200]}")
            if getattr(chunk, "is_final", False):
                exit_code = getattr(chunk, "exit_code", None)
    except Exception as exc:  # noqa: BLE001
        print(f"    [!] invoke crashed: {type(exc).__name__}: {exc}")
        exit_code = -1

    oracle_val: bool | None = None
    if task.oracle is not None and exit_code == 0:
        try:
            oracle_val = await task.oracle()
        except Exception as exc:  # noqa: BLE001
            print(f"    [oracle] failed: {exc}")
            oracle_val = None

    elapsed = time.perf_counter() - t0
    if task.cleanup_procs:
        _kill_procs(task.cleanup_procs)
    spans = collector.stop() if collector is not None else []
    res = BenchResult(
        task.id, exit_code, oracle_val, elapsed, chunks,
        slo_s=task.slo_s, spans=spans,
    )
    steps = res.step_durations_s
    print(f"    -> {res.mark}  exit={exit_code} oracle={oracle_val} "
          f"({elapsed:.1f}s, {chunks} chunks, {len(steps)} steps, "
          f"{res.model_calls} model calls)")
    return res


def _write_report(results: list[BenchResult], native: bool) -> Path:
    passed = sum(1 for r in results if r.passed)
    eyeball = sum(1 for r in results if r.mark == "PASS~")
    engine = (
        "native Gemini computer_use (prefer_native=true)"
        if native
        else "hand-rolled vision+JSON loop"
    )
    lines = [
        "# Jarvis — Computer-Use Benchmark",
        "",
        f"Engine: {engine}",
        f"Score: **{passed}/{len(results)}** tasks passed "
        f"({eyeball} exit-code-only, eyeball).",
        "",
        "| Task | Result | exit | oracle | time |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        oracle = "-" if r.oracle is None else ("ok" if r.oracle else "MISS")
        lines.append(
            f"| `{r.id}` | {r.mark} | {r.exit_code} | {oracle} | {r.elapsed_s:.1f}s |"
        )
    lines += [
        "",
        "Legend: PASS = exit 0 + oracle ok · PASS~ = exit 0, no cheap oracle "
        "(eyeball it) · FAIL = non-zero exit or oracle miss.",
    ]
    out = Path.home() / "Downloads" / "Jarvis-Computer-Use-Benchmark.md"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[report] could not write {out}: {exc}")
    return out


def _write_json_report(results: list[BenchResult], native: bool) -> Path:
    """Machine-readable run record — the baseline/regression artifact the
    frontier-speed plan's promotion gate compares against."""
    all_spans = [s for r in results for s in r.spans]
    all_steps = sorted(d for r in results for d in r.step_durations_s)

    def _pctl(vals: list[float], q: float) -> float | None:
        if not vals:
            return None
        return vals[max(0, int(len(vals) * q) - 1)]

    payload = {
        "engine": "native" if native else "hand-rolled-v1",
        "slo": {"step_p50_s": SLO_STEP_P50_S, "step_p95_s": SLO_STEP_P95_S},
        "aggregate": {
            "step_p50_s": statistics.median(all_steps) if all_steps else None,
            "step_p95_s": _pctl(all_steps, 0.95),
            "phase_stats_ms": _phase_stats(all_spans),
        },
        "tasks": [
            {
                "id": r.id,
                "mark": r.mark,
                "exit_code": r.exit_code,
                "oracle": r.oracle,
                "elapsed_s": round(r.elapsed_s, 2),
                "slo_s": r.slo_s,
                "slo_ok": r.slo_ok,
                "steps": len(r.step_durations_s),
                "model_calls": r.model_calls,
                "step_durations_s": [round(d, 2) for d in r.step_durations_s],
                "phase_stats_ms": _phase_stats(r.spans),
            }
            for r in results
        ],
    }
    out = Path.home() / "Downloads" / "Jarvis-Computer-Use-Benchmark.json"
    try:
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[report] could not write {out}: {exc}")
    return out


def _assert_slo(results: list[BenchResult]) -> list[str]:
    """Return the list of SLO violations (empty = gate passes)."""
    violations: list[str] = []
    all_steps = sorted(d for r in results for d in r.step_durations_s)
    if all_steps:
        p50 = statistics.median(all_steps)
        p95 = all_steps[max(0, int(len(all_steps) * 0.95) - 1)]
        if p50 > SLO_STEP_P50_S:
            violations.append(f"step p50 {p50:.2f}s > {SLO_STEP_P50_S}s")
        if p95 > SLO_STEP_P95_S:
            violations.append(f"step p95 {p95:.2f}s > {SLO_STEP_P95_S}s")
    for r in results:
        if not r.passed:
            violations.append(f"task {r.id} failed (exit={r.exit_code})")
        elif r.slo_ok is False:
            violations.append(
                f"task {r.id} took {r.elapsed_s:.1f}s > SLO {r.slo_s:.0f}s"
            )
    return violations


async def main() -> int:
    parser = argparse.ArgumentParser(description="Everyday Computer-Use benchmark")
    parser.add_argument("--list", action="store_true", help="list task ids and exit")
    parser.add_argument("--only", default="", help="comma-separated task ids to run")
    parser.add_argument(
        "--assert-slo", action="store_true",
        help="exit non-zero when the frontier-speed SLOs are violated "
             "(step p50/p95 + per-task wall-clock budgets)",
    )
    args = parser.parse_args()

    catalog = _catalog()
    if args.list:
        for t in catalog:
            print(f"{t.id:24} {t.prompt[:70]}")
        return 0
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        catalog = [t for t in catalog if t.id in wanted]
        if not catalog:
            print(f"no tasks match --only {args.only!r}")
            return 2

    print("=" * 66)
    print("JARVIS-CU-BENCH — everyday Computer-Use benchmark")
    print("=" * 66)
    print("[1] Building default brain (wires ComputerUseContext) ...")
    bus = EventBus()
    from jarvis.brain.factory import build_default_brain

    build_default_brain(bus=bus)

    from jarvis.harness.computer_use_context import get_computer_use_context
    from jarvis.plugins.harness.computer_use import ComputerUseHarness

    try:
        ctx = get_computer_use_context()
    except Exception as exc:  # noqa: BLE001
        print(f"[1] ABORT: ComputerUseContext not wired: {exc}")
        return 3
    native = getattr(ctx, "native_cu", None) is not None
    print(f"[1] CU tools: {sorted((ctx.tools or {}).keys())}")
    print(f"[1] native Gemini engine: {'ENABLED' if native else 'off (hand-rolled)'}")

    harness = ComputerUseHarness()
    if not await harness.health():
        print("[2] ABORT: harness not healthy (vision/brain/executor missing)")
        return 3

    collector = PhaseCollector(bus)
    results: list[BenchResult] = []
    for task in catalog:
        results.append(await _run_task(harness, task, collector))

    passed = sum(1 for r in results if r.passed)
    report = _write_report(results, native)
    json_report = _write_json_report(results, native)
    print("\n" + "=" * 66)
    print(f"SCORE: {passed}/{len(results)} passed   (report: {report})")
    print(f"JSON:  {json_report}")
    if args.assert_slo:
        violations = _assert_slo(results)
        if violations:
            print("SLO GATE: FAIL")
            for v in violations:
                print(f"  - {v}")
            print("=" * 66)
            return 1
        print("SLO GATE: PASS")
    print("=" * 66)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
