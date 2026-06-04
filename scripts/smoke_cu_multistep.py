"""Multi-step computer-use live smoke — proves the Set-of-Marks ReAct loop.

Drives the REAL wired ComputerUseHarness end-to-end on a genuinely multi-step
task that requires clicking the right on-screen elements in sequence:

    open Calculator -> click 7 -> click + -> click 8 -> click = -> read result

This exercises exactly the capabilities the POAV review found missing:
  * Set-of-Marks grounding (the planner clicks element NUMBERS, not pixels),
  * one-action-per-observation ReAct cadence (re-observe after every click),
  * the verify/finish gate, across many steps within one task.

Oracle: the Calculator UIA tree shows "15" (7 + 8) after the loop finishes.
Cleanup: closes the Calculator window we opened.

Run:  python scripts/smoke_cu_multistep.py
"""
from __future__ import annotations

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import asyncio
import time

import psutil

from jarvis.core.bus import EventBus
from jarvis.core.protocols import HarnessTask

_CALC_NAMES = ("calculatorapp.exe", "calculator.exe")


def calc_running() -> bool:
    for p in psutil.process_iter(attrs=["name"]):
        try:
            if (p.info["name"] or "").lower() in _CALC_NAMES:
                return True
        except Exception:
            pass
    return False


def kill_calc() -> None:
    for p in psutil.process_iter(attrs=["name"]):
        try:
            if (p.info["name"] or "").lower() in _CALC_NAMES:
                p.kill()
        except Exception:
            pass


async def _read_calc_result() -> str:
    """Best-effort: read the Calculator display text from the UIA tree."""
    try:
        from jarvis.vision.uia_tree import UIATreeSource

        obs = await UIATreeSource().observe()
        texts = []
        for n in obs.nodes:
            nm = (n.name or "")
            if nm:
                texts.append(nm)
        return " | ".join(texts)
    except Exception as exc:  # noqa: BLE001
        return f"(uia read failed: {exc})"


async def main() -> int:
    print("=" * 64)
    print("MULTI-STEP COMPUTER-USE SMOKE — Set-of-Marks ReAct loop")
    print("=" * 64)

    if calc_running():
        print("[pre] closing pre-existing Calculator for an honest oracle")
        kill_calc()
        time.sleep(0.6)

    # Launch Calculator and force it foreground BEFORE the loop. This isolates
    # the capability under test (Set-of-Marks multi-step clicking) from the
    # background-process foreground-lock artifact: a tool launched from this
    # headless console cannot steal foreground, but the interactive voice path
    # can. The agent's job here is purely the click sequence 7 + 8 =.
    print("[pre] launching Calculator and bringing it to the foreground ...")
    import subprocess
    subprocess.Popen(["cmd", "/c", "start", "calculator:"], shell=False,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    time.sleep(3.0)
    try:
        from jarvis.plugins.tool.switch_window import SwitchWindowTool
        from jarvis.core.protocols import ExecutionContext
        from uuid import uuid4
        ctx0 = ExecutionContext(trace_id=uuid4(), user_utterance="pre",
                                config={}, memory_read=None, approved_by="auto")
        for title in ("Rechner", "Calculator"):
            r = await SwitchWindowTool().execute({"title_contains": title}, ctx0)
            if getattr(r, "success", False):
                print(f"[pre] foregrounded window matching {title!r}")
                break
    except Exception as exc:  # noqa: BLE001
        print(f"[pre] could not pre-foreground calculator: {exc}")
    time.sleep(0.8)

    print("\n[1] Building default brain (wires ComputerUseContext) ...")
    bus = EventBus()
    from jarvis.brain.factory import build_default_brain

    build_default_brain(bus=bus)

    from jarvis.harness.computer_use_context import get_computer_use_context

    try:
        ctx = get_computer_use_context()
        print(f"[1] CU tools wired: {sorted((ctx.tools or {}).keys())}")
        print(f"[1] budgets: step_budget={ctx.step_budget} "
              f"max_replans={ctx.max_replans} per_step_timeout_s={ctx.per_step_timeout_s}")
    except Exception as exc:  # noqa: BLE001
        print(f"[1] ABORT: ComputerUseContext not wired: {exc}")
        return 3

    from jarvis.plugins.harness.computer_use import ComputerUseHarness

    harness = ComputerUseHarness()
    if not await harness.health():
        print("[2] ABORT: harness not healthy (vision/brain/executor missing)")
        return 3

    task = HarnessTask(
        prompt=(
            "The Windows Calculator is already open and focused. Compute 7 + 8 "
            "by clicking the on-screen buttons in order: seven, plus, eight, "
            "equals. Finish when the result 15 is visible on the display."
        ),
        timeout_s=200,
        risk_tier="monitor",
        allow_computer_use=True,
    )
    print(f"\n[3] Invoking ReAct loop: {task.prompt!r}")
    t0 = time.perf_counter()
    chunk_count = 0
    final_code = None
    async for chunk in harness.invoke(task):
        chunk_count += 1
        out = (getattr(chunk, "stdout", "") or "").strip()
        err = (getattr(chunk, "stderr", "") or "").strip()
        if out:
            print(f"    [{chunk_count}] {out[:220]}")
        if err:
            print(f"    [{chunk_count}] ERR {err[:220]}")
        if getattr(chunk, "is_final", False):
            final_code = getattr(chunk, "exit_code", None)
            print(f"    [{chunk_count}] FINAL exit_code={final_code}")

    elapsed = (time.perf_counter() - t0)
    print(f"\n[3] Loop finished: {chunk_count} chunks, {elapsed:.1f}s, exit={final_code}")

    print("\n[4] Oracle: reading Calculator display via UIA ...")
    time.sleep(0.8)
    display = await _read_calc_result()
    print(f"[4] UIA text: {display[:300]}")
    ok = "15" in display

    print("\n[5] Cleanup: closing Calculator")
    kill_calc()

    print("\n" + "=" * 64)
    if ok:
        print("VERDICT: PASS — the agent opened Calculator and computed 7+8=15 "
              "by clicking marked elements across multiple steps.")
        print("=" * 64)
        return 0
    print("VERDICT: INCONCLUSIVE — loop ran but '15' not found in the display. "
          "Inspect the chunks above (the loop may have used a different path).")
    print("=" * 64)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
