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
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.core.bus import EventBus
from jarvis.core.protocols import HarnessTask

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


async def _run_task(harness, task: BenchTask) -> BenchResult:
    # Honest oracle: close any pre-existing instance so a stale window can't
    # make a no-op look like success.
    if task.cleanup_procs:
        _kill_procs(task.cleanup_procs)
        await asyncio.sleep(0.5)

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
    res = BenchResult(task.id, exit_code, oracle_val, elapsed, chunks)
    print(f"    -> {res.mark}  exit={exit_code} oracle={oracle_val} "
          f"({elapsed:.1f}s, {chunks} chunks)")
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


async def main() -> int:
    parser = argparse.ArgumentParser(description="Everyday Computer-Use benchmark")
    parser.add_argument("--list", action="store_true", help="list task ids and exit")
    parser.add_argument("--only", default="", help="comma-separated task ids to run")
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

    results: list[BenchResult] = []
    for task in catalog:
        results.append(await _run_task(harness, task))

    passed = sum(1 for r in results if r.passed)
    report = _write_report(results, native)
    print("\n" + "=" * 66)
    print(f"SCORE: {passed}/{len(results)} passed   (report: {report})")
    print("=" * 66)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
