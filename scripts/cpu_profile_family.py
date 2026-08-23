"""Measure the running desktop app's CPU — its own processes apart from what it hosts.

The question "why does Jarvis burn CPU?" has two very different answers on a
busy box, and Task Manager folds them into one number: the app's OWN work
(backend, WebView host/renderer/GPU, local realtime server) and the coding
CLIs, MCP servers and shells it HOSTS in its terminal panes. This script
samples both sides for a window and reports every process as a share of ONE
core (what the engineering floor is measured in) and of the whole machine
(what the user sees in a task manager). ``--threads`` adds the backend's
per-thread split, named where the platform exposes thread names.

Usage::

    python scripts/cpu_profile_family.py [--pid PID] [--seconds 60] [--threads]

Without ``--pid`` the backend is found by its command line
(``jarvis.ui.web.launcher``). Cross-platform: psutil only; thread names come
from ``psutil`` on Linux and from ``GetThreadDescription`` on Windows, and are
left blank elsewhere. Read-only — it never touches the app.
"""

from __future__ import annotations

import argparse
import sys
import time

import psutil  # type: ignore[import-untyped]


def _find_backend() -> int | None:
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or [])
        except (psutil.Error, OSError):
            continue
        if "jarvis.ui.web.launcher" in cmdline:
            return proc.pid
    return None


def _family(root: psutil.Process) -> list[psutil.Process]:
    members = [root]
    try:
        members.extend(root.children(recursive=True))
    except psutil.Error:
        pass
    known = {p.pid for p in members}
    # The local realtime server may be re-parented after a supervisor restart.
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or [])
        except (psutil.Error, OSError):
            continue
        if "local_realtime" in cmdline and proc.pid not in known:
            members.append(proc)
    return members


def _label(proc: psutil.Process, root_pid: int) -> tuple[str, bool]:
    """``(label, is_own)`` — own = the app itself, not what its panes host."""
    try:
        name = proc.name()
        cmdline = proc.cmdline()
    except psutil.Error:
        return "(gone)", False
    joined = " ".join(cmdline)
    if proc.pid == root_pid:
        return "backend", True
    if name.lower() == "msedgewebview2.exe":
        kind = "host"
        for arg in cmdline:
            if arg.startswith("--type="):
                kind = arg.split("=", 1)[1]
                sub = next((a for a in cmdline if a.startswith("--utility-sub-type=")), "")
                if sub:
                    kind += ":" + sub.split("=", 1)[1].split(".")[0]
        return f"webview[{kind}]", True
    if "local_realtime" in joined:
        return "local-realtime-server", True
    return name, False


def _thread_name(tid: int) -> str:
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        import ctypes.wintypes as wt

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenThread.restype = wt.HANDLE
        k32.GetThreadDescription.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        handle = k32.OpenThread(0x0800, False, tid)  # THREAD_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            out = ctypes.c_wchar_p()
            if k32.GetThreadDescription(handle, ctypes.byref(out)) >= 0 and out.value:
                name = out.value
                k32.LocalFree(out)
                return name
        finally:
            k32.CloseHandle(handle)
    except (OSError, AttributeError):
        return ""
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pid", type=int, default=None, help="backend PID (auto-detected)")
    parser.add_argument("--seconds", type=float, default=60.0, help="sampling window")
    parser.add_argument("--threads", action="store_true", help="per-thread split of the backend")
    args = parser.parse_args()

    pid = args.pid if args.pid is not None else _find_backend()
    if pid is None:
        print("no running Jarvis backend found (pass --pid)", file=sys.stderr)
        return 2
    root = psutil.Process(pid)
    ncpu = psutil.cpu_count(logical=True) or 1
    members = _family(root)

    before: dict[int, float] = {}
    for proc in members:
        try:
            t = proc.cpu_times()
            before[proc.pid] = t.user + t.system
        except psutil.Error:
            continue
    threads_before: dict[int, float] = {}
    if args.threads:
        threads_before = {t.id: t.user_time + t.system_time for t in root.threads()}

    wall_start = time.perf_counter()
    time.sleep(args.seconds)
    wall = time.perf_counter() - wall_start

    rows: list[tuple[float, int, str, bool, float]] = []
    for proc in members:
        try:
            t = proc.cpu_times()
            rss = proc.memory_info().rss / 1e6
        except psutil.Error:
            continue
        if proc.pid not in before:
            continue
        delta = t.user + t.system - before[proc.pid]
        label, own = _label(proc, pid)
        rows.append((100.0 * delta / wall, proc.pid, label, own, rss))
    rows.sort(reverse=True)

    own_total = sum(r[0] for r in rows if r[3])
    hosted_total = sum(r[0] for r in rows if not r[3])
    print(f"window={wall:.0f}s logical_cpus={ncpu}  (% of ONE core | % of machine)")
    print(f"{'%1core':>8} {'%mach':>7} {'pid':>7} {'rss_MB':>7}  label")
    for pct, ppid, label, own, rss in rows:
        if pct < 0.05:
            continue
        tag = "" if own else "   (hosted)"
        print(f"{pct:8.2f} {pct / ncpu:7.2f} {ppid:7d} {rss:7.0f}  {label}{tag}")
    print("-" * 64)
    own_line = f"{own_total:6.2f} % of one core = {own_total / ncpu:5.2f} % of machine"
    hosted_line = f"{hosted_total:6.2f} % of one core = {hosted_total / ncpu:5.2f} % of machine"
    print(f"Jarvis OWN:    {own_line}")
    print(f"hosted CLIs:   {hosted_line}")

    if args.threads:
        after = {t.id: t.user_time + t.system_time for t in root.threads()}
        print()
        print("backend per-thread (% of one core):")
        deltas = sorted(((after[t] - threads_before.get(t, 0.0)) / wall * 100.0, t) for t in after)
        for pct, tid in reversed(deltas[-20:]):
            if pct < 0.05:
                break
            print(f"{pct:8.2f}  tid={tid:<7d} {_thread_name(tid)}")
        print(f"threads={len(after)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
