#!/usr/bin/env python
"""Reproducible cold-boot timing harness for the Personal Jarvis **desktop** app.

Why this is separate from ``measure_boot.py``
---------------------------------------------
``measure_boot.py`` measures the *headless* path (``_run_headless``), which
already uses the fast-boot bootstrap (commit 6379222e) and serves in ~200 ms.
But the user runs the **desktop** app (``run.bat`` -> pywebview + voice + orb),
whose backend thread (``DesktopApp._run_backend``) does NOT use the bootstrap:
it runs the full ``server.start()`` synchronously before the backend serves
``/api/health``. The desktop shell (``DesktopApp.run``) blocks in
``_wait_for_backend`` (polling ``/api/health`` for a 200) before it calls
``webview.create_window`` — so "the window appears" == "the backend serves".

This harness measures exactly that gate: ``spawn -> /api/health serving``, via
``scripts/_desktop_boot_driver.py`` (which runs ``_run_backend`` with NO GUI
window and NO microphone). The anchor is the ``BOOT_READY_MS=`` sentinel the
desktop ``_run_backend`` prints (gated behind ``JARVIS_BOOT_PROFILE=1``) the
moment the backend is serving.

Isolation is identical to ``measure_boot.py`` (shared ``.boot-bench/`` dirs,
``data/`` wiped per run, seeded frozen vault) so the factor stays honest and
comparable. Writes ``desktop-boot-latest.json`` every run and freezes
``desktop-boot-baseline.json`` on the first run.

Usage
-----
    "C:\\Program Files\\Python311\\python.exe" scripts/measure_desktop_boot.py
    "...python.exe" scripts/measure_desktop_boot.py --runs 5 --warmup 1
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reuse the proven isolation + seeding helpers from the headless harness so the
# two benches do identical work and the factor is directly comparable.
from measure_boot import (  # noqa: E402
    DATA_DIR,
    DEFAULT_PAGES,
    DEFAULT_PYTHON,
    ISO_DIR,
    NO_WINDOW_CREATIONFLAGS,
    _bench_env,
    _free_port,
    _terminate,
    seed_vault,
)

DRIVER = REPO_ROOT / "scripts" / "_desktop_boot_driver.py"
BASELINE_PATH = REPO_ROOT / "desktop-boot-baseline.json"
LATEST_PATH = REPO_ROOT / "desktop-boot-latest.json"
# --voice (TTU) mode writes its own pair so the window-anchor baseline above
# stays comparable across runs that do not exercise the voice stack.
TTU_BASELINE_PATH = REPO_ROOT / "desktop-ttu-baseline.json"
TTU_LATEST_PATH = REPO_ROOT / "desktop-ttu-latest.json"


def run_one(python: str, timeout: float, mode: str = "legacy", voice: bool = False) -> dict:
    """Spawn one isolated desktop-backend cold boot and measure the HONEST
    user-perceived anchor: ``spawn -> /api/health responds 200``.

    That is the literal gate the desktop shell uses — ``DesktopApp.run`` blocks
    in ``_wait_for_backend`` (an ``/api/health`` poll) before it creates the
    pywebview window — so "the window appears" == "/api/health responds 200".
    We poll it exactly like ``_wait_for_backend`` does (a real HTTP response,
    not merely a bound socket — a bound-but-loop-blocked bootstrap would NOT
    answer, which is the point). The ``BOOT_READY_MS=`` stdout print is kept as
    a secondary in-process cross-check (it marks the bootstrap *bind*, which can
    precede a responsive health endpoint).
    """
    import shutil
    import urllib.request

    shutil.rmtree(DATA_DIR, ignore_errors=True)
    shutil.rmtree(ISO_DIR, ignore_errors=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ISO_DIR.mkdir(parents=True, exist_ok=True)

    port = _free_port()
    env = _bench_env(port)
    # The desktop driver reads the port from this env (no --port CLI exists for
    # the desktop path); _bench_env already pins isolation + JARVIS_VOICE=0.
    env["JARVIS_DESKTOP_BENCH_PORT"] = str(port)
    env["JARVIS_DESKTOP_BENCH_MODE"] = mode
    if voice:
        # TTU mode: measure the REAL "usable" anchor — the voice stack boots
        # and the app prints VOICE_READY_MS (wake loop armed, honest anchor on
        # the same clock as BOOT_READY_MS). Overrides _bench_env's voice-off.
        env["JARVIS_VOICE"] = "1"

    cmd = [python, str(DRIVER)]
    result: dict = {
        "wall_ms": None,           # spawn -> /api/health 200 (PRIMARY = window appears)
        "boot_ready_ms": None,     # in-process bootstrap-bind print (secondary)
        "boot_ready_wall_ms": None,
        "voice_ready_ms": None,        # pipeline-started print (secondary)
        "voice_ready_wall_ms": None,
        "voice_usable_ms": None,       # HONEST TTU anchor: wake model warmed
        "voice_usable_wall_ms": None,  # + VAD + TTS up (VoiceBootStatus ready)
        "phases": {},
        "port": port,
    }
    health_ok = threading.Event()
    voice_ok = threading.Event()

    t_spawn = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
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
                result["boot_ready_wall_ms"] = (time.perf_counter() - t_spawn) * 1000.0
                try:
                    result["boot_ready_ms"] = float(line.split("=", 1)[1])
                except ValueError:
                    result["boot_ready_ms"] = None
            elif line.startswith("VOICE_READY_MS="):
                result["voice_ready_wall_ms"] = (time.perf_counter() - t_spawn) * 1000.0
                try:
                    result["voice_ready_ms"] = float(line.split("=", 1)[1])
                except ValueError:
                    result["voice_ready_ms"] = None
            elif line.startswith("VOICE_USABLE_MS="):
                # The honest anchor: wake model warmed + VAD + TTS up.
                result["voice_usable_wall_ms"] = (time.perf_counter() - t_spawn) * 1000.0
                try:
                    result["voice_usable_ms"] = float(line.split("=", 1)[1])
                except ValueError:
                    result["voice_usable_ms"] = None
                voice_ok.set()

    def poller() -> None:
        # PRIMARY anchor = time until GET / returns the real UI shell (HTML).
        # That is the moment the desktop window stops being a black screen and
        # shows the UI — the user-perceived "boot done". The serve-first
        # bootstrap serves the static frontend straight from disk, so this fires
        # at bind time, not after the full app build.
        url = f"http://127.0.0.1:{port}/"
        time.sleep(0.02)
        while not health_ok.is_set() and proc.poll() is None:
            try:
                with urllib.request.urlopen(url, timeout=0.5) as r:  # noqa: S310
                    if r.status == 200:
                        body = r.read(512).decode("utf-8", "replace").lower()
                        if "<!doctype html" in body or "<div id=" in body or "<html" in body:
                            result["wall_ms"] = (time.perf_counter() - t_spawn) * 1000.0
                            health_ok.set()
                            return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.05)

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    pt = threading.Thread(target=poller, daemon=True)
    pt.start()

    got = health_ok.wait(timeout)
    if voice and got:
        # TTU mode: the run ends at the VOICE anchor, not the window anchor.
        got = voice_ok.wait(timeout)
    _terminate(proc)
    th.join(timeout=3)
    pt.join(timeout=3)

    if not got or result["wall_ms"] is None:
        raise RuntimeError(
            f"desktop cold boot did not reach its anchor within {timeout:.0f}s "
            f"(port {port}, voice={voice}) — check the driver / instrumentation"
        )
    if voice and result["voice_usable_wall_ms"] is None:
        raise RuntimeError(
            f"voice stack never printed VOICE_USABLE_MS within {timeout:.0f}s "
            f"(port {port}) — honest TTU anchor missing"
        )
    return result


def _summarize(runs: list[dict], *, python: str, pages: int) -> dict:
    walls = [r["wall_ms"] for r in runs]
    readies = [r["boot_ready_ms"] for r in runs if r["boot_ready_ms"] is not None]
    bind_walls = [r["boot_ready_wall_ms"] for r in runs if r["boot_ready_wall_ms"] is not None]
    voice_walls = [
        r["voice_ready_wall_ms"] for r in runs if r.get("voice_ready_wall_ms") is not None
    ]
    usable_walls = [
        r["voice_usable_wall_ms"] for r in runs if r.get("voice_usable_wall_ms") is not None
    ]
    phase_names = sorted({k for r in runs for k in r["phases"]})
    phase_medians = {
        name: statistics.median(
            [r["phases"][name] for r in runs if name in r["phases"]]
        )
        for name in phase_names
    }
    return {
        "path": "desktop (_run_backend, GUI-free driver)",
        "runs": len(runs),
        "python": python,
        "vault_pages": pages,
        "median_wall_ms": round(statistics.median(walls), 1),
        "median_boot_ready_ms": (
            round(statistics.median(readies), 1) if readies else None
        ),
        "median_bind_wall_ms": (
            round(statistics.median(bind_walls), 1) if bind_walls else None
        ),
        "median_voice_ready_wall_ms": (
            round(statistics.median(voice_walls), 1) if voice_walls else None
        ),
        "voice_ready_wall_ms_runs": [round(v, 1) for v in voice_walls],
        "median_voice_usable_wall_ms": (
            round(statistics.median(usable_walls), 1) if usable_walls else None
        ),
        "voice_usable_wall_ms_runs": [round(v, 1) for v in usable_walls],
        "wall_ms_runs": [round(w, 1) for w in walls],
        "boot_ready_ms_runs": [round(r, 1) for r in readies],
        "phase_medians_ms": {k: round(v, 1) for k, v in phase_medians.items()},
        "anchor": "spawn -> /api/health responds 200 (= DesktopApp._wait_for_backend success = window creation point)",
        "secondary_anchor": "median_bind_wall_ms = spawn -> BOOT_READY print (bootstrap bind; may precede a responsive health endpoint)",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Desktop cold-boot timing harness")
    ap.add_argument("--python", default=DEFAULT_PYTHON, help="interpreter for the spawned driver")
    ap.add_argument("--runs", type=int, default=5, help="measured cold starts (median)")
    ap.add_argument("--warmup", type=int, default=1, help="discarded warmup boots")
    ap.add_argument("--timeout", type=float, default=120.0, help="per-boot ready timeout (s)")
    ap.add_argument("--pages", type=int, default=DEFAULT_PAGES, help="vault pages to seed")
    ap.add_argument("--mode", default="legacy", choices=["legacy", "fastboot"], help="desktop boot path to measure")
    ap.add_argument(
        "--voice",
        action="store_true",
        help=(
            "TTU mode: boot WITH the voice stack and anchor the run on "
            "VOICE_READY_MS (wake loop armed) instead of the window anchor. "
            "Writes desktop-ttu-{baseline,latest}.json."
        ),
    )
    args = ap.parse_args(argv)
    if args.voice and args.timeout < 240.0:
        # The voice stack loads a local STT model on the stt_match path; give
        # cold runs generous headroom so a slow box does not flake the bench.
        args.timeout = 240.0
    baseline_path = TTU_BASELINE_PATH if args.voice else BASELINE_PATH
    latest_path = TTU_LATEST_PATH if args.voice else LATEST_PATH

    if not Path(args.python).exists():
        print(f"WARNING: interpreter not found at {args.python}; using as-is", flush=True)

    pages = seed_vault(args.pages)
    print(f"[harness] vault seeded: {pages} pages", flush=True)

    for i in range(args.warmup):
        print(f"[harness] warmup {i + 1}/{args.warmup} ...", flush=True)
        r = run_one(args.python, args.timeout, args.mode, voice=args.voice)
        print(f"[harness]   warmup wall={r['wall_ms']:.0f}ms", flush=True)

    runs: list[dict] = []
    for i in range(args.runs):
        r = run_one(args.python, args.timeout, args.mode, voice=args.voice)
        runs.append(r)
        _br = r["boot_ready_ms"]
        _br_s = f"{_br:.0f}ms" if _br is not None else "n/a"
        _vr = r.get("voice_usable_wall_ms")
        _vr_s = f" voice_usable={_vr:.0f}ms" if _vr is not None else ""
        print(
            f"[harness] run {i + 1}/{args.runs}: health200={r['wall_ms']:.0f}ms "
            f"bind={_br_s}{_vr_s}",
            flush=True,
        )

    summary = _summarize(runs, python=args.python, pages=pages)
    if args.voice:
        summary["ttu_anchor"] = (
            "spawn -> VOICE_USABLE_MS print (VoiceBootStatus ready=True: wake "
            "model warmed + VAD + TTS client up — the honest time-to-usable "
            "anchor; VOICE_READY_MS = pipeline task started is secondary)"
        )
    latest_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    froze_baseline = False
    if not baseline_path.exists():
        baseline_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        froze_baseline = True

    def _ms(v: float | None) -> str:
        return f"{v:.0f} ms" if v is not None else "n/a"

    print("\n=== DESKTOP BOOT TIMING SUMMARY ===", flush=True)
    print(f"median spawn->/api/health 200    : {_ms(summary['median_wall_ms'])}  (PRIMARY: window appears)", flush=True)
    print(f"median bootstrap-bind print      : {_ms(summary['median_bind_wall_ms'])}  (secondary)", flush=True)
    if args.voice:
        _vu_med = _ms(summary["median_voice_usable_wall_ms"])
        print(
            f"median spawn->VOICE_USABLE (TTU) : {_vu_med}  (wake+VAD+TTS up)",
            flush=True,
        )
        print(f"voice-usable runs: {summary['voice_usable_wall_ms_runs']}", flush=True)
    print(f"runs: {summary['wall_ms_runs']}", flush=True)
    print("per-phase medians (ms):", flush=True)
    for name, val in sorted(summary["phase_medians_ms"].items(), key=lambda kv: -kv[1]):
        print(f"  {name:24s} {val:8.1f}", flush=True)

    key = "median_voice_ready_wall_ms" if args.voice else "median_wall_ms"
    if froze_baseline:
        print(f"\nfroze baseline -> {baseline_path.name}", flush=True)
    elif baseline_path.exists():
        base = json.loads(baseline_path.read_text(encoding="utf-8"))
        base_wall = base.get(key)
        now_wall = summary.get(key)
        if base_wall and now_wall:
            factor = base_wall / now_wall
            print(
                f"\nbaseline median {base_wall:.0f} ms -> now "
                f"{now_wall:.0f} ms = {factor:.2f}x faster",
                flush=True,
            )
    print(f"wrote {latest_path.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
