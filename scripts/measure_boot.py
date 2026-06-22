#!/usr/bin/env python
"""Reproducible cold-boot timing harness for the Personal Jarvis headless app.

Why this exists
---------------
The cold-boot bottleneck is the chain of blocking, sequential ``_init_*`` steps
inside ``WebServer.start()`` (mission stack, screenshot retention, wiki
integration, the FTS5 boot index, the session + task stacks) plus the overlay
start. ``GET /api/health`` returns ``{"ok": true}`` the moment uvicorn listens —
BEFORE those steps finish — so it is not an honest "fully ready" marker. This
harness instead anchors on the ``BOOT_READY_MS=<n>`` sentinel that the launcher
prints once the backend is actually serving and every subsystem the first real
request needs is ready or cleanly deferred (see ``jarvis/ui/web/launcher.py`` and
the ``[BOOT_PROFILE]`` marks in ``jarvis/ui/web/server.py``, both gated behind
``JARVIS_BOOT_PROFILE=1`` so production stdout is unchanged).

Isolation contract (NEVER touches the running production instance)
------------------------------------------------------------------
* A dedicated ``.boot-bench/`` directory holds an isolated ``data/`` dir and a
  representative ``vault/`` (seeded once, frozen identical across passes so the
  factor is honest).
* ``data/`` is wiped before every run, so every cold boot does *identical* work:
  fresh DB schema creation + an FTS5 index build over the seeded vault.
* ``JARVIS__MEMORY__DATA_DIR`` / ``JARVIS__WIKI_INTEGRATION__VAULT_ROOT`` /
  ``JARVIS_ISOLATION_ROOT`` redirect every store, the vault, and the mission
  worktree container into ``.boot-bench/`` — the last one is critical because
  the mission startup sweep is filesystem-driven (mtime, not DB-gated) and would
  otherwise delete real mission outputs from the shared production
  ``sub-agents-outputs/``.
* The flight-recorder blob sweep is disabled for the bench
  (``flight_recorder_retention_days=-1``): its directory is hardcoded relative to
  the CWD, which ``ensure_project_root_cwd()`` pins to the production repo root,
  so it cannot be isolated without a code change. Excluding it makes the baseline
  *smaller* (a conservative lower bound on any speed-up factor), never inflated.
* An ephemeral free port per run — never the production port.

Usage
-----
    "C:\\Program Files\\Python311\\python.exe" scripts/measure_boot.py
    "...python.exe" scripts/measure_boot.py --runs 5 --warmup 1

Writes ``boot-latest.json`` every run and freezes ``boot-baseline.json`` on the
first run (so later optimization passes compare against the original baseline).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = REPO_ROOT / ".boot-bench"
VAULT_DIR = BENCH_DIR / "vault"
DATA_DIR = BENCH_DIR / "data"
ISO_DIR = BENCH_DIR / "sub-agents-outputs"
BASELINE_PATH = REPO_ROOT / "boot-baseline.json"
LATEST_PATH = REPO_ROOT / "boot-latest.json"

DEFAULT_PYTHON = r"C:\Program Files\Python311\python.exe"
DEFAULT_PAGES = 80

# Canonical no-window flag (AP-1) with a safe fallback if jarvis is not importable
# in the harness interpreter.
try:
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
except Exception:  # noqa: BLE001
    NO_WINDOW_CREATIONFLAGS = (
        subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]
    )

_WORDS = (
    "mission router brain provider latency vault index session worker critic "
    "isolation worktree overlay speech pipeline warmup cloud headless config "
    "telemetry retention screenshot wiki recall ingest scheduler channel friend "
    "contact telephony marketplace plugin frontier autoswitch board profile "
    "achievement bio rollup curator atomic writer repository fts sqlite anchor "
    "ack preamble spawn announcement dispatcher manager factory streaming tool "
    "approval risk tier whitelist blacklist event bus frozen dataclass trace"
).split()


def seed_vault(n_pages: int) -> int:
    """Seed (once) a representative Obsidian-style vault of *n_pages* ``*.md``
    files in nested folders so the boot FTS5 index build has real work.

    Idempotent and frozen: if the vault already holds >= *n_pages* markdown
    files it is left untouched, keeping the measurement identical across passes.
    """
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    existing = list(VAULT_DIR.rglob("*.md"))
    if len(existing) >= n_pages:
        return len(existing)

    subdirs = ["people", "notes", "projects", "reference"]
    for i in range(n_pages):
        sub = subdirs[i % len(subdirs)]
        d = VAULT_DIR / sub
        d.mkdir(parents=True, exist_ok=True)
        # Deterministic but varied body so the FTS index has distinct content.
        paragraphs = []
        for p in range(6):
            start = (i * 7 + p * 13) % len(_WORDS)
            words = [_WORDS[(start + k) % len(_WORDS)] for k in range(40)]
            paragraphs.append(" ".join(words) + ".")
        body = "\n\n".join(paragraphs)
        page = d / f"page_{i:03d}.md"
        page.write_text(
            f"---\ntitle: Bench Page {i}\ntags: [bench, {sub}]\n---\n\n"
            f"# Bench Page {i}\n\n{body}\n",
            encoding="utf-8",
        )
    return n_pages


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _bench_env(port: int) -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.update(
        {
            "JARVIS_BOOT_PROFILE": "1",
            # Exercise mission recovery + startup cleanup over the ISOLATED dirs.
            "JARVIS_PRIMARY_INSTANCE": "1",
            "JARVIS_ISOLATION_ROOT": str(ISO_DIR),
            "JARVIS__MEMORY__DATA_DIR": str(DATA_DIR),
            "JARVIS__WIKI_INTEGRATION__ENABLED": "true",
            "JARVIS__WIKI_INTEGRATION__VAULT_ROOT": str(VAULT_DIR),
            # -1 (a real int) disables the CWD-bound prod blob sweep; "0" would
            # coerce to bool False but -1 keeps the int field clean.
            "JARVIS__TELEMETRY__FLIGHT_RECORDER_RETENTION_DAYS": "-1",
            "JARVIS__OVERLAY__ENABLED": "false",
            "JARVIS_VOICE": "0",
        }
    )
    return env


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    with contextlib.suppress(Exception):
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            proc.kill()


def run_one(python: str, timeout: float) -> dict:
    """Spawn one isolated cold boot, measure spawn->BOOT_READY wall-clock, and
    capture the per-phase ``[BOOT_PROFILE]`` breakdown."""
    # Fresh data + isolation dirs => every boot does identical work.
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    shutil.rmtree(ISO_DIR, ignore_errors=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ISO_DIR.mkdir(parents=True, exist_ok=True)

    port = _free_port()
    cmd = [
        python,
        "-m",
        "jarvis.ui.web.launcher",
        "--headless",
        "--no-lock",
        "--port",
        str(port),
    ]

    result: dict = {"wall_ms": None, "boot_ready_ms": None, "phases": {}, "port": port}
    ready = threading.Event()

    t_spawn = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=_bench_env(port),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )

    def reader() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if line.startswith("[BOOT_PROFILE] "):
                name, _, val = line[len("[BOOT_PROFILE] "):].partition("=")
                try:
                    result["phases"][name] = float(val)
                except ValueError:
                    pass
            elif line.startswith("BOOT_READY_MS="):
                result["wall_ms"] = (time.perf_counter() - t_spawn) * 1000.0
                try:
                    result["boot_ready_ms"] = float(line.split("=", 1)[1])
                except ValueError:
                    result["boot_ready_ms"] = None
                ready.set()
                return

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    got = ready.wait(timeout)
    _terminate(proc)
    th.join(timeout=3)

    if not got or result["wall_ms"] is None:
        raise RuntimeError(
            f"cold boot did not reach BOOT_READY within {timeout:.0f}s "
            f"(port {port}) — check the launcher / instrumentation"
        )
    return result


def _summarize(runs: list[dict], *, python: str, pages: int) -> dict:
    walls = [r["wall_ms"] for r in runs]
    readies = [r["boot_ready_ms"] for r in runs if r["boot_ready_ms"] is not None]
    phase_names = sorted({k for r in runs for k in r["phases"]})
    phase_medians = {
        name: statistics.median(
            [r["phases"][name] for r in runs if name in r["phases"]]
        )
        for name in phase_names
    }
    return {
        "runs": len(runs),
        "python": python,
        "vault_pages": pages,
        "median_wall_ms": round(statistics.median(walls), 1),
        "median_boot_ready_ms": (
            round(statistics.median(readies), 1) if readies else None
        ),
        "wall_ms_runs": [round(w, 1) for w in walls],
        "boot_ready_ms_runs": [round(r, 1) for r in readies],
        "phase_medians_ms": {k: round(v, 1) for k, v in phase_medians.items()},
        "notes": (
            "isolated via JARVIS__MEMORY__DATA_DIR + JARVIS__WIKI_INTEGRATION__"
            "VAULT_ROOT + JARVIS_ISOLATION_ROOT; data/ wiped per run; blob sweep "
            "excluded (CWD-bound to prod, not isolatable without a code change)."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cold-boot timing harness")
    ap.add_argument("--python", default=DEFAULT_PYTHON, help="interpreter for the spawned app")
    ap.add_argument("--runs", type=int, default=5, help="measured cold starts (median)")
    ap.add_argument("--warmup", type=int, default=1, help="discarded warmup boots")
    ap.add_argument("--timeout", type=float, default=120.0, help="per-boot ready timeout (s)")
    ap.add_argument("--pages", type=int, default=DEFAULT_PAGES, help="vault pages to seed")
    args = ap.parse_args(argv)

    if not Path(args.python).exists():
        print(f"WARNING: interpreter not found at {args.python}; using as-is", flush=True)

    pages = seed_vault(args.pages)
    print(f"[harness] vault seeded: {pages} pages at {VAULT_DIR}", flush=True)

    for i in range(args.warmup):
        print(f"[harness] warmup {i + 1}/{args.warmup} ...", flush=True)
        r = run_one(args.python, args.timeout)
        print(f"[harness]   warmup wall={r['wall_ms']:.0f}ms", flush=True)

    runs: list[dict] = []
    for i in range(args.runs):
        r = run_one(args.python, args.timeout)
        runs.append(r)
        print(
            f"[harness] run {i + 1}/{args.runs}: wall={r['wall_ms']:.0f}ms "
            f"boot_ready={r['boot_ready_ms']:.0f}ms",
            flush=True,
        )

    summary = _summarize(runs, python=args.python, pages=pages)
    LATEST_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    froze_baseline = False
    if not BASELINE_PATH.exists():
        BASELINE_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        froze_baseline = True

    print("\n=== BOOT TIMING SUMMARY ===", flush=True)
    print(f"median wall-clock spawn->ready : {summary['median_wall_ms']:.0f} ms", flush=True)
    print(f"median in-process BOOT_READY_MS: {summary['median_boot_ready_ms']:.0f} ms", flush=True)
    print(f"runs: {summary['wall_ms_runs']}", flush=True)
    print("per-phase medians (ms):", flush=True)
    for name, val in sorted(summary["phase_medians_ms"].items(), key=lambda kv: -kv[1]):
        print(f"  {name:24s} {val:8.1f}", flush=True)

    if froze_baseline:
        print(f"\nfroze baseline -> {BASELINE_PATH.name}", flush=True)
    elif BASELINE_PATH.exists():
        base = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        base_wall = base.get("median_wall_ms")
        if base_wall:
            factor = base_wall / summary["median_wall_ms"]
            print(
                f"\nbaseline median {base_wall:.0f} ms -> now "
                f"{summary['median_wall_ms']:.0f} ms = {factor:.2f}x faster",
                flush=True,
            )
    print(f"wrote {LATEST_PATH.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
