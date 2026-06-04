"""POAV live test: open 5 terminals + start a Claude Code session in each.

Drives the REAL ComputerUseHarness (POAV engine) five times (decomposed,
one terminal per invocation) per the agent-team finding that step_budget=12
/ max_replans=2 cannot carry a 15-action single run, and that identical
window titles make per-window addressing unreliable in one shot.

SAFETY (critical): the desktop already has the user's own Windows Terminal
windows and ~14 live claude.exe processes (one of which may be THIS session).
This script is strictly DIFF-SCOPED:
  * snapshot existing WT window handles + claude PIDs BEFORE the run,
  * only ever inspects/closes windows and PIDs that are NEW after the run,
  * cleanup closes ONLY the HWNDs we opened (WM_CLOSE), never a global
    Stop-Process / taskkill. The user's sessions are never touched.

QUOTA: we type `claude` + Enter to open the REPL, but never submit a prompt.
An idle Claude REPL costs nothing regardless of auth source, so no quota is
burned. The oracle only READS the screen / process table.

Run:  python scripts/smoke_poav_5terminals.py
      python scripts/smoke_poav_5terminals.py --no-cleanup   (leave windows open)
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
from contextlib import suppress

import psutil
import win32con
import win32gui
import win32process

from jarvis.core.bus import EventBus
from jarvis.core.protocols import HarnessTask

N_TERMINALS = 5
WT_PROC = "windowsterminal.exe"
CLAUDE_PROC = "claude.exe"


# --------------------------------------------------------------------------
# Window / process snapshotting (diff-scoped, never global)


def wt_window_handles() -> dict[int, str]:
    """Visible top-level windows owned by WindowsTerminal.exe -> {hwnd: title}."""
    out: dict[int, str] = {}
    wt_pids = {
        p.pid for p in psutil.process_iter(attrs=["name"])
        if (p.info["name"] or "").lower() == WT_PROC
    }

    def cb(hwnd: int, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in wt_pids:
                title = win32gui.GetWindowText(hwnd) or ""
                # WT host can have hidden helper windows; keep only ones with a title
                if title:
                    out[hwnd] = title
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    return out


def claude_pids() -> set[int]:
    pids: set[int] = set()
    for p in psutil.process_iter(attrs=["name"]):
        try:
            if (p.info["name"] or "").lower() == CLAUDE_PROC:
                pids.add(p.pid)
        except Exception:
            pass
    return pids


def read_window_uia_text(hwnd: int) -> str:
    """Best-effort UIA text of a WT window (claude banner detection). Flaky on
    WT buffers; used only as a bonus signal, never as the hard gate."""
    try:
        from pywinauto import Desktop
        win = Desktop(backend="uia").window(handle=hwnd)
        parts: list[str] = []
        for d in win.descendants():
            with suppress(Exception):
                t = d.window_text()
                if t:
                    parts.append(t)
        return "\n".join(parts)
    except Exception:
        return ""


# --------------------------------------------------------------------------


async def open_one_terminal_with_claude(harness, idx: int) -> tuple[bool, str]:
    """One decomposed harness invocation: open a terminal, type claude, enter."""
    task = HarnessTask(
        prompt=(
            "Open a new Windows Terminal window. Use the open_terminal action. "
            "Then use the wait action for 2 seconds so the shell prompt is "
            "ready. Then use run_terminal_command_through_ui with "
            "command 'claude' to start a Claude Code session in that terminal. "
            "Then emit done. Do not open more than one terminal."
        ),
        timeout_s=90,
        risk_tier="monitor",
        allow_computer_use=True,
    )
    last = ""
    final_code = None
    try:
        async for chunk in harness.invoke(task):
            out = (getattr(chunk, "stdout", "") or "").strip()
            err = (getattr(chunk, "stderr", "") or "").strip()
            if out:
                last = out
                print(f"      [{idx}] {out[:160]}")
            if err:
                print(f"      [{idx}] ERR {err[:160]}")
            if getattr(chunk, "is_final", False):
                final_code = getattr(chunk, "exit_code", None)
    except Exception as exc:
        return False, f"invoke raised: {type(exc).__name__}: {exc}"
    ok = final_code == 0
    return ok, f"exit_code={final_code} last={last[:120]!r}"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cleanup", action="store_true",
                    help="leave the 5 opened terminals running")
    args = ap.parse_args()

    print("=" * 64)
    print("POAV LIVE: 5 terminals, each with a Claude Code session")
    print("=" * 64)

    # --- SNAPSHOT (diff scope) --------------------------------------------
    pre_wins = wt_window_handles()
    pre_claude = claude_pids()
    print(f"[snapshot] pre-existing WT windows: {len(pre_wins)} "
          f"| pre-existing claude.exe: {len(pre_claude)}")
    print("[snapshot] these are the USER's — they will NOT be touched.")

    # --- WIRE the Computer-Use system -------------------------------------
    print("\n[wire] build_default_brain (sets ComputerUseContext) ...")
    bus = EventBus()
    from jarvis.brain.factory import build_default_brain
    build_default_brain(bus=bus)
    from jarvis.plugins.harness.computer_use import ComputerUseHarness
    harness = ComputerUseHarness()
    if not await harness.health():
        print("[wire] ABORT: CU context unhealthy (vision/brain/executor).")
        return 3
    print("[wire] harness healthy.")

    # --- DRIVE: 5 decomposed invocations ----------------------------------
    print(f"\n[run] opening {N_TERMINALS} terminals via the POAV loop "
          "(one invocation each):")
    per_run: list[tuple[bool, str]] = []
    t0 = time.perf_counter()
    for i in range(1, N_TERMINALS + 1):
        print(f"  -> terminal {i}/{N_TERMINALS}")
        ok, info = await open_one_terminal_with_claude(harness, i)
        per_run.append((ok, info))
        print(f"     result: {'OK' if ok else 'FAIL'} ({info})")
        time.sleep(2.0)  # let the new window settle before the next opens
    elapsed = time.perf_counter() - t0
    print(f"[run] all invocations done in {elapsed:.0f}s")

    # --- ORACLE (diff-scoped) ---------------------------------------------
    print("\n[oracle] waiting up to 15s for claude REPLs to reach ready ...")
    new_wins: dict[int, str] = {}
    new_claude: set[int] = set()
    banner_hits = 0
    deadline = time.time() + 15.0
    while time.time() < deadline:
        cur_wins = wt_window_handles()
        new_wins = {h: t for h, t in cur_wins.items() if h not in pre_wins}
        new_claude = claude_pids() - pre_claude
        if len(new_wins) >= N_TERMINALS and len(new_claude) >= N_TERMINALS:
            break
        time.sleep(1.0)

    # Bonus: per-new-window UIA banner read (best-effort).
    for hwnd in new_wins:
        txt = read_window_uia_text(hwnd).lower()
        if "welcome to claude code" in txt or "? for shortcuts" in txt:
            banner_hits += 1

    print(f"[oracle] NEW WT windows opened by us: {len(new_wins)}")
    print(f"[oracle] NEW claude.exe processes:    {len(new_claude)}")
    print(f"[oracle] windows showing claude banner (UIA, best-effort): {banner_hits}")

    runs_ok = sum(1 for ok, _ in per_run if ok)
    # Hard gate: 5 new windows AND 5 new claude processes.
    # (UIA banner is a bonus; WT buffer text via UIA is unreliable.)
    success = len(new_wins) >= N_TERMINALS and len(new_claude) >= N_TERMINALS

    # --- CLEANUP (only OUR windows) ---------------------------------------
    if not args.no_cleanup:
        print(f"\n[cleanup] closing ONLY the {len(new_wins)} windows we opened "
              "(WM_CLOSE per handle; user windows untouched) ...")
        for hwnd in new_wins:
            with suppress(Exception):
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        time.sleep(2.0)
        # Verify our claude PIDs exited; if a few linger, terminate ONLY those PIDs.
        still = [pid for pid in new_claude if psutil.pid_exists(pid)]
        for pid in still:
            with suppress(Exception):
                psutil.Process(pid).terminate()
        print(f"[cleanup] done (force-terminated {len(still)} lingering OUR-pids).")
    else:
        print("\n[cleanup] skipped (--no-cleanup): 5 terminals left running.")

    # --- VERDICT ----------------------------------------------------------
    print("\n" + "=" * 64)
    print(f"per-invocation OK: {runs_ok}/{N_TERMINALS}")
    print(f"new terminals:     {len(new_wins)}/{N_TERMINALS}")
    print(f"new claude REPLs:  {len(new_claude)}/{N_TERMINALS}")
    if success:
        print("VERDICT: PASS — Computer-Use opened 5 terminals and started "
              "a Claude session in each.")
        print("=" * 64)
        return 0
    print("VERDICT: FAIL — see per-invocation results above.")
    print("=" * 64)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
