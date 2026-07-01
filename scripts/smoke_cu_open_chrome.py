"""Live Computer-Use smoke — the user-reported "Öffne Chrome" failure case.  # i18n-allow: quotes the real German user command that reproduces the bug

Drives the REAL wired ComputerUseHarness end-to-end with the exact goal that
used to fail (the agent typed "Chrome" into a search box, never clicked the
result, and reported success). After the 2026-06-09 shippability waves the
loop must:

  * launch Chrome via the open_app action (or an equivalent visible path),
  * verify completion against the screenshot via the strict done-judge,
  * finish with exit_code 0 ONLY when a Chrome window is really open.

Oracle: a NEW chrome.exe process appears (PID set difference), or a Chrome
window is in the foreground after the run. Chrome is deliberately NOT killed
before/after the run — this is the maintainer's live desktop and Chrome may
hold real tabs; if Chrome is already running, the oracle notes the weaker
evidence honestly.

Run:  python scripts/smoke_cu_open_chrome.py
"""
from __future__ import annotations

import contextlib
import sys

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import asyncio
import time

import psutil

from jarvis.core.bus import EventBus
from jarvis.core.protocols import HarnessTask

_CHROME_NAME = "chrome.exe"


def chrome_pids() -> set[int]:
    pids: set[int] = set()
    for p in psutil.process_iter(attrs=["name", "pid"]):
        with contextlib.suppress(Exception):
            if (p.info["name"] or "").lower() == _CHROME_NAME:
                pids.add(int(p.info["pid"]))
    return pids


def foreground_title() -> str:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:  # noqa: BLE001
        return ""


async def main() -> int:
    print("=" * 64)
    print('LIVE COMPUTER-USE SMOKE — goal: "Öffne Chrome"')  # i18n-allow: quotes the real German user command under test
    print("=" * 64)

    pids_before = chrome_pids()
    if pids_before:
        print(f"[pre] Chrome already running ({len(pids_before)} processes) — "
              "NOT killing it (live desktop). Oracle falls back to "
              "new-PID/foreground evidence.")

    print("\n[1] Building default brain (wires ComputerUseContext) ...")
    bus = EventBus()
    from jarvis.brain.factory import build_default_brain

    build_default_brain(bus=bus)

    from jarvis.harness.computer_use_context import get_computer_use_context

    try:
        ctx = get_computer_use_context()
        print(f"[1] CU tools wired: {sorted((ctx.tools or {}).keys())}")
        print(f"[1] verify_after_each_step={ctx.verify_after_each_step} "
              f"step_budget={ctx.step_budget}")
    except Exception as exc:  # noqa: BLE001
        print(f"[1] ABORT: ComputerUseContext not wired: {exc}")
        return 3

    from jarvis.plugins.harness.computer_use import ComputerUseHarness

    harness = ComputerUseHarness()
    if not await harness.health():
        print("[2] ABORT: harness not healthy (vision/brain/executor missing)")
        return 3

    task = HarnessTask(
        prompt="Öffne Chrome",  # i18n-allow: simulated German user command under test
        timeout_s=180,
        risk_tier="monitor",
        allow_computer_use=True,
    )
    print(f"\n[3] Invoking loop: {task.prompt!r}")
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

    elapsed = time.perf_counter() - t0
    print(f"\n[3] Loop finished: {chunk_count} chunks, {elapsed:.1f}s, "
          f"exit={final_code}")

    print("\n[4] Oracle: process + foreground evidence ...")
    await asyncio.sleep(1.5)
    pids_after = chrome_pids()
    new_pids = pids_after - pids_before
    fg = foreground_title()
    print(f"[4] new chrome PIDs: {sorted(new_pids) if new_pids else 'none'}")
    print(f"[4] foreground window: {fg!r}")

    chrome_open = bool(new_pids) or "chrome" in fg.lower()

    print("\n" + "=" * 64)
    if final_code == 0 and chrome_open:
        print("VERDICT: PASS — loop reported verified done AND Chrome is "
              "observably open.")
        print("=" * 64)
        return 0
    if final_code == 0 and not chrome_open:
        print("VERDICT: FAIL — loop claimed success but Chrome is NOT "
              "observably open (false done).")
        print("=" * 64)
        return 1
    print(f"VERDICT: FAIL — loop exit_code={final_code} (no false success; "
          "inspect the chunk log above).")
    print("=" * 64)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
