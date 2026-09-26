"""Read-only resource snapshot of a running Jarvis desktop session.

Usage::

    python scripts/perf_live_snapshot.py --seconds 60 --interval 5
    python scripts/perf_live_snapshot.py --pid 1234 --seconds 30 --json out.json
    python scripts/perf_live_snapshot.py --pid 1234 --health-url http://127.0.0.1:47821/api/health

The process tree is sampled without starting, stopping, or modifying processes.
CPU percentages use one logical core as 100%; divide by ``logical_cpus`` for
whole-machine percentage. RSS is resident memory and is not additive across
shared pages. On Windows, commit is psutil's pagefile (private committed bytes);
other platforms report it as unavailable. HTTP probes are optional GETs to a
caller-supplied health URL, not representative chat or voice workflow latency.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil  # type: ignore[import-untyped]


def _identity(proc: psutil.Process) -> tuple[int, float]:
    return proc.pid, proc.create_time()


def _backend_pids() -> list[int]:
    result = []
    for proc in psutil.process_iter(["cmdline"]):
        try:
            args = proc.info["cmdline"] or []
            if any("jarvis.ui.web.launcher" in arg for arg in args):
                result.append(proc.pid)
        except (psutil.Error, OSError):
            continue
    return result


def _role(proc: psutil.Process, root_pid: int) -> str:
    if proc.pid == root_pid:
        return "backend"
    try:
        name = proc.name().lower()
        args = proc.cmdline()
    except (psutil.Error, OSError):
        return "unknown"
    if name == "msedgewebview2.exe":
        kind = next((arg.partition("=")[2] for arg in args if arg.startswith("--type=")), "host")
        return f"webview:{kind}"
    if any("local_realtime" in arg for arg in args):
        return "local-realtime"
    if any("dictation" in arg.lower() and "preview" in arg.lower() for arg in args):
        return "dictation-preview"
    if any(token in name for token in ("codex", "claude", "antigravity", "gemini", "agy")):
        return "coding-agent"
    if name in {"ollama.exe", "ollama", "llama-server.exe", "llama-server"}:
        return "model-server"
    if name in {"npm", "npm.cmd", "npm.exe", "vite", "vite.exe"}:
        return "build"
    if name in {"node.exe", "node"} and any("vite" in arg.lower() for arg in args):
        return "build"
    return "other-child"


def _snapshot(proc: psutil.Process, root_pid: int, scope: str) -> dict[str, Any] | None:
    try:
        mem = proc.memory_info()
        cpu = proc.cpu_times()
        connections: dict[str, int] | None = None
        try:
            connections = {"established": 0, "listen": 0, "other": 0}
            for conn in proc.net_connections(kind="inet"):
                status = conn.status.lower()
                key = status if status in ("established", "listen") else "other"
                connections[key] += 1
        except (psutil.AccessDenied, OSError, NotImplementedError):
            pass  # Permission or platform does not expose per-process sockets.
        return {
            "pid": proc.pid,
            "created": proc.create_time(),
            "role": _role(proc, root_pid),
            "scope": scope,
            "cpu_seconds": cpu.user + cpu.system,
            "rss_mb": round(mem.rss / 1e6, 2),
            "commit_mb": round(mem.pagefile / 1e6, 2)
            if sys.platform == "win32" and hasattr(mem, "pagefile")
            else None,
            "connections": connections,
        }
    except (psutil.Error, OSError):
        return None


def _health_probe(url: str) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:  # noqa: S310
            response.read(4096)
            return {
                "status": response.status,
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
            }
    except (OSError, ValueError) as exc:
        return {
            "error": type(exc).__name__,
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        }


def _capture(root: psutil.Process) -> dict[tuple[int, float], dict[str, Any]]:
    try:
        family = [root, *root.children(recursive=True)]
    except psutil.Error:
        family = [root]
    family_pids = {proc.pid for proc in family}
    records = [_snapshot(proc, root.pid, "jarvis-family") for proc in family]
    # Keep unrelated coding agents, builds, model servers and other Jarvis
    # instances visible without charging their resource use to this backend.
    for proc in psutil.process_iter(["name", "cmdline"]):
        if proc.pid in family_pids:
            continue
        role = _role(proc, root.pid)
        if role in {"coding-agent", "model-server", "build", "dictation-preview", "local-realtime"}:
            records.append(_snapshot(proc, root.pid, "external"))
    return {(row["pid"], row["created"]): row for row in records if row is not None}


def measure(
    root_pid: int, seconds: float, interval: float, health_url: str | None
) -> dict[str, Any]:
    root = psutil.Process(root_pid)
    root_identity = _identity(root)
    start = time.perf_counter()
    previous_time = start
    previous = _capture(root)
    samples: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    while time.perf_counter() - start < seconds:
        time.sleep(min(interval, max(0, seconds - (time.perf_counter() - start))))
        now = time.perf_counter()
        current = _capture(root)
        if _identity(root) != root_identity:
            raise RuntimeError("Jarvis backend exited or PID was reused during sampling")
        wall = now - previous_time
        rows = []
        for key, row in current.items():
            old = previous.get(key)
            if old is None:
                continue  # A new process has no CPU baseline for this interval.
            measured = {k: v for k, v in row.items() if k != "cpu_seconds"}
            measured["cpu_one_core_pct"] = round(
                max(0, row["cpu_seconds"] - old["cpu_seconds"]) / wall * 100, 2
            )
            rows.append(measured)
        samples.append(
            {
                "elapsed_seconds": round(now - start, 2),
                "window_seconds": round(wall, 2),
                "processes": rows,
            }
        )
        if health_url:
            probes.append(_health_probe(health_url))
        previous, previous_time = current, now
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "platform": sys.platform,
        "logical_cpus": psutil.cpu_count(logical=True),
        "root_pid": root_pid,
        "root_created": root_identity[1],
        "other_backend_pids": [pid for pid in _backend_pids() if pid != root_pid],
        "duration_seconds": round(time.perf_counter() - start, 2),
        "samples": samples,
        "health_probes": probes,
    }


def _summary(result: dict[str, Any]) -> str:
    samples = result["samples"]
    roles: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in sample["processes"]:
            grouped.setdefault(f"{row['scope']}/{row['role']}", []).append(row)
        for role, rows in grouped.items():
            commit = [row["commit_mb"] for row in rows]
            connections = [row["connections"] for row in rows if row["connections"]]
            roles.setdefault(role, []).append(
                {
                    "cpu_one_core_pct": sum(row["cpu_one_core_pct"] for row in rows),
                    "rss_mb": sum(row["rss_mb"] for row in rows),
                    "commit_mb": round(sum(commit), 2)
                    if all(value is not None for value in commit)
                    else None,
                    "connections": {
                        key: sum(conn[key] for conn in connections)
                        for key in ("established", "listen", "other")
                    }
                    if connections
                    else None,
                    "count": len(rows),
                }
            )
    lines = [
        (
            f"backend={result['root_pid']} duration={result['duration_seconds']}s "
            f"logical_cpus={result['logical_cpus']}"
        ),
        f"other_backends={result['other_backend_pids']}",
        "scope/role                        count  CPU%/core avg/max  RSS MB  commit MB  TCP",
    ]
    for role, rows in sorted(roles.items()):
        cpus = [row["cpu_one_core_pct"] for row in rows]
        latest = rows[-1]
        conn = latest["connections"]
        lines.append(
            f"{role:<33} {latest['count']:5d} {statistics.mean(cpus):6.2f}/{max(cpus):6.2f}"
            f" {latest['rss_mb']:7.1f} {str(latest['commit_mb']):>10} {str(conn):>20}"
        )
    if result["health_probes"]:
        ok = [p["latency_ms"] for p in result["health_probes"] if p.get("status") == 200]
        median = statistics.median(ok) if ok else "unavailable"
        lines.append(
            f"health GET: {len(ok)}/{len(result['health_probes'])} HTTP 200, "
            f"latency median={median} ms"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--pid", type=int, help="Jarvis backend PID; required when multiple are running"
    )
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument(
        "--health-url",
        help="optional GET URL for health latency, e.g. http://127.0.0.1:47821/api/health",
    )
    parser.add_argument("--json", type=Path, help="write raw samples to JSON")
    args = parser.parse_args()
    if args.seconds <= 0 or args.interval <= 0:
        parser.error("--seconds and --interval must be positive")
    backends = _backend_pids()
    if args.pid is None and len(backends) != 1:
        parser.error(f"expected one Jarvis backend; found {backends}. Pass --pid")
    pid = args.pid if args.pid is not None else backends[0]
    try:
        result = measure(pid, args.seconds, args.interval, args.health_url)
    except (psutil.Error, RuntimeError) as exc:
        parser.exit(2, f"sampling failed: {exc}\n")
    print(_summary(result))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
