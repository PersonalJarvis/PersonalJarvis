#!/usr/bin/env python
"""Fresh-process desktop-backend timing, without creating a browser window.

The default auto mode follows the launcher's JARVIS_DESKTOP_FASTBOOT flag. HTML
served, app-interactive (ASGI delegated), and local voice-usable are separate
anchors. The harness acknowledges shell paint synthetically; it does not
measure rendering, a real conversation or first useful audio.

Storage/ports are separate, but credentials and configured services are real.
This is not a hermetic sandbox and must not be used as a harmless second app.
For a parent-controlled real desktop restart, use the budget guard's --evidence.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reuse source pinning and separate storage from the headless harness so the
# two benches do identical work and the factor is directly comparable.
from measure_boot import (  # noqa: E402
    DEFAULT_PAGES,
    DEFAULT_PYTHON,
    NO_WINDOW_CREATIONFLAGS,
    _assert_child_source,
    _bench_env,
    _desktop_boot_mode,
    _free_port,
    _fresh_run_dirs,
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


def run_one(
    python: str, timeout: float, mode: str = "auto", voice: bool = False,
    interactive: bool = False,
) -> dict:
    """Measure HTML delivery and the requested later readiness anchors.

    Child clock marks are diagnostic only. All primary durations are observed
    from the parent's spawn timestamp; HTML delivery is not browser paint.
    """
    import urllib.request

    mode = _desktop_boot_mode(mode)
    data_dir, isolation_dir = _fresh_run_dirs()

    port = _free_port()
    env = _bench_env(port, data_dir, isolation_dir)
    # The desktop driver reads the port from this env (no --port CLI exists for
    # the desktop path); _bench_env already pins isolation + JARVIS_VOICE=0.
    env["JARVIS_DESKTOP_BENCH_PORT"] = str(port)
    env["JARVIS_DESKTOP_BENCH_MODE"] = mode
    if voice:
        # TTU mode: measure the REAL "usable" anchor — the voice stack boots
        # and the app prints VOICE_READY_MS (wake loop armed, honest anchor on
        # the same clock as BOOT_READY_MS). Overrides _bench_env's voice-off.
        env["JARVIS_VOICE"] = "1"

    identity = _assert_child_source(python, env)
    cmd = [python, str(DRIVER)]
    result: dict = {
        "wall_ms": None,           # spawn -> GET / delivers HTML; not paint
        "boot_ready_ms": None,     # in-process bootstrap-bind print (secondary)
        "boot_ready_wall_ms": None,
        "voice_ready_ms": None,        # pipeline-started print (secondary)
        "voice_ready_wall_ms": None,
        "voice_usable_ms": None,       # HONEST TTU anchor: wake model warmed
        "voice_usable_wall_ms": None,  # + VAD + TTS up (VoiceBootStatus ready)
        "app_interactive_ms": None,       # set_app: UI data requests answered
        "app_interactive_wall_ms": None,  # (spawn -> app usable, end to end)
        "phases": {},
        "port": port,
        "mode": mode,
        "source_identity": identity,
        "artifact_dir": str(data_dir.parent),
    }
    health_ok = threading.Event()
    voice_ok = threading.Event()
    interactive_ok = threading.Event()

    t_spawn = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # The app logs em dashes and arrows; without this the reader
        # decodes them as cp1252 on Windows, dies on the first one, and
        # the harness reports a boot that never reached its anchor when
        # the boot was fine (AP-1).
        encoding="utf-8",
        errors="replace",
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
            elif line.startswith("APP_INTERACTIVE_MS="):
                # End-to-end usable anchor: set_app delegated the UI's held
                # data requests — the app answers, the SPA leaves its
                # "Getting ready" state.
                result["app_interactive_wall_ms"] = (
                    time.perf_counter() - t_spawn
                ) * 1000.0
                try:
                    result["app_interactive_ms"] = float(line.split("=", 1)[1])
                except ValueError:
                    result["app_interactive_ms"] = None
                interactive_ok.set()

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
                            # A real desktop window acknowledges its first paint
                            # (POST /api/ui/shell-painted after two animation
                            # frames), which releases the backend's heavy-init
                            # gate. This GUI-free harness has no window, so
                            # WITHOUT the ack every run silently pays the full
                            # 12 s paint-timeout fallback — inflating every TTU
                            # anchor and false-failing the boot-budget gate.
                            # Ack right after the shell HTML is served: the
                            # closest honest stand-in for the window's paint.
                            try:
                                req = urllib.request.Request(
                                    f"http://127.0.0.1:{port}/api/ui/shell-painted",
                                    data=b"",
                                    method="POST",
                                )
                                with urllib.request.urlopen(req, timeout=2.0):  # noqa: S310
                                    pass
                            except Exception as exc:  # noqa: BLE001 - bounded best-effort ack
                                logging.getLogger(__name__).debug(
                                    "Synthetic paint acknowledgement failed: %s",
                                    type(exc).__name__,
                                )
                            return
            except Exception as exc:  # noqa: BLE001 - connection may not exist during startup
                logging.getLogger(__name__).debug(
                    "Shell not served yet: %s", type(exc).__name__
                )
            time.sleep(0.05)

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    pt = threading.Thread(target=poller, daemon=True)
    pt.start()

    deadline = t_spawn + timeout
    got = health_ok.wait(max(0.0, deadline - time.perf_counter()))
    if (interactive or voice) and got:
        got = interactive_ok.wait(max(0.0, deadline - time.perf_counter()))
    if voice and got:
        got = voice_ok.wait(max(0.0, deadline - time.perf_counter()))
    _terminate(proc)
    th.join(timeout=3)
    pt.join(timeout=3)

    if not got or result["wall_ms"] is None:
        raise RuntimeError(
            f"desktop cold boot did not reach its anchor within {timeout:.0f}s "
            f"(port {port}, voice={voice}) — check the driver / instrumentation"
        )
    if (interactive or voice) and result["app_interactive_wall_ms"] is None:
        raise RuntimeError("App-interactive anchor missing from desktop boot")
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
    interactive_walls = [
        r["app_interactive_wall_ms"]
        for r in runs
        if r.get("app_interactive_wall_ms") is not None
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
        "median_app_interactive_wall_ms": (
            round(statistics.median(interactive_walls), 1)
            if interactive_walls
            else None
        ),
        "app_interactive_wall_ms_runs": [round(v, 1) for v in interactive_walls],
        "wall_ms_runs": [round(w, 1) for w in walls],
        "boot_ready_ms_runs": [round(r, 1) for r in readies],
        "phase_medians_ms": {k: round(v, 1) for k, v in phase_medians.items()},
        "anchor": "spawn -> GET / delivers shell HTML (not browser paint)",
        "app_interactive_anchor": "spawn -> APP_INTERACTIVE stdout observation (ASGI delegated)",
        "voice_anchor": (
            "spawn -> VOICE_USABLE stdout observation "
            "(local readiness, not a voice round trip)"
        ),
        "mode": runs[0].get("mode"),
        "source_identities": [r.get("source_identity") for r in runs],
        "artifact_dirs": [r.get("artifact_dir") for r in runs],
        "secondary_anchor": (
            "median_bind_wall_ms = spawn -> BOOT_READY print "
            "(bootstrap bind; may precede a responsive health endpoint)"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Desktop cold-boot timing harness")
    ap.add_argument("--python", default=DEFAULT_PYTHON, help="interpreter for the spawned driver")
    ap.add_argument("--runs", type=int, default=5, help="measured cold starts (median)")
    ap.add_argument("--warmup", type=int, default=1, help="discarded warmup boots")
    ap.add_argument("--timeout", type=float, default=120.0, help="per-boot ready timeout (s)")
    ap.add_argument("--pages", type=int, default=DEFAULT_PAGES, help="vault pages to seed")
    ap.add_argument(
        "--mode", default="auto", choices=["auto", "legacy", "fastboot"],
        help="desktop boot path to measure",
    )
    ap.add_argument(
        "--voice",
        action="store_true",
        help=(
            "TTU mode: boot WITH the voice stack and anchor the run on "
            "VOICE_USABLE_MS (local readiness), plus app-interactive. "
            "Writes desktop-ttu-{baseline,latest}.json."
        ),
    )
    ap.add_argument(
        "--interactive", action="store_true",
        help="Wait for ASGI app delegation even when local voice is disabled",
    )
    ap.add_argument("--output", type=Path, help="Write the result to this explicit artifact file")
    args = ap.parse_args(argv)
    if args.voice and args.timeout < 240.0:
        # The voice stack loads a local STT model on the stt_match path; give
        # cold runs generous headroom so a slow box does not flake the bench.
        args.timeout = 240.0
    baseline_path = TTU_BASELINE_PATH if args.voice else BASELINE_PATH
    latest_path = args.output or (TTU_LATEST_PATH if args.voice else LATEST_PATH)

    if not Path(args.python).exists():
        print(f"WARNING: interpreter not found at {args.python}; using as-is", flush=True)

    pages = seed_vault(args.pages)
    print(f"[harness] vault seeded: {pages} pages", flush=True)

    for i in range(args.warmup):
        print(f"[harness] warmup {i + 1}/{args.warmup} ...", flush=True)
        r = run_one(
            args.python, args.timeout, args.mode,
            voice=args.voice, interactive=args.interactive,
        )
        print(f"[harness]   warmup wall={r['wall_ms']:.0f}ms", flush=True)

    runs: list[dict] = []
    for i in range(args.runs):
        r = run_one(
            args.python, args.timeout, args.mode,
            voice=args.voice, interactive=args.interactive,
        )
        runs.append(r)
        _br = r["boot_ready_ms"]
        _br_s = f"{_br:.0f}ms" if _br is not None else "n/a"
        _vr = r.get("voice_usable_wall_ms")
        _vr_s = f" voice_usable={_vr:.0f}ms" if _vr is not None else ""
        print(
            f"[harness] run {i + 1}/{args.runs}: shell_html={r['wall_ms']:.0f}ms "
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
    if args.output is None and not baseline_path.exists():
        baseline_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        froze_baseline = True

    def _ms(v: float | None) -> str:
        return f"{v:.0f} ms" if v is not None else "n/a"

    print("\n=== DESKTOP BOOT TIMING SUMMARY ===", flush=True)
    print(
        f"median spawn->shell HTML        : {_ms(summary['median_wall_ms'])}"
        "  (transport only; no browser paint)", flush=True,
    )
    print(
        f"median bootstrap-bind print      : {_ms(summary['median_bind_wall_ms'])}"
        "  (secondary)", flush=True,
    )
    if args.voice:
        _vu_med = _ms(summary["median_voice_usable_wall_ms"])
        print(
            f"median spawn->VOICE_USABLE (TTU) : {_vu_med}  (wake+VAD+TTS up)",
            flush=True,
        )
        print(f"voice-usable runs: {summary['voice_usable_wall_ms_runs']}", flush=True)
    if args.interactive or args.voice:
        _ai_med = _ms(summary["median_app_interactive_wall_ms"])
        print(
            f"median spawn->APP_INTERACTIVE    : {_ai_med}  (UI data answered)",
            flush=True,
        )
        print(
            f"app-interactive runs: {summary['app_interactive_wall_ms_runs']}",
            flush=True,
        )
    print(f"runs: {summary['wall_ms_runs']}", flush=True)
    print("per-phase medians (ms):", flush=True)
    for name, val in sorted(summary["phase_medians_ms"].items(), key=lambda kv: -kv[1]):
        print(f"  {name:24s} {val:8.1f}", flush=True)

    key = "median_voice_usable_wall_ms" if args.voice else "median_wall_ms"
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
