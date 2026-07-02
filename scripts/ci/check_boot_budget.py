#!/usr/bin/env python
"""Startup-budget regression guard (TTU) — fail when boot gets slow again.

Why this exists (TTU forensic 2026-07-02, docs/diagnostics/BOOT-TTU-NOTES.md):
boot regressions land silently — a stale custom wake model put a 114.7 s
model-load cascade on the critical path and nobody noticed until the user did.
This guard runs ONE isolated cold boot through the committed harness
(``scripts/measure_desktop_boot.py``) and fails when a measured anchor exceeds
its budget, so a feature that sneaks heavy work onto the startup path breaks
CI/pre-push instead of the user's day.

Anchors and budgets (override via env for slower CI boxes):
- window: spawn -> the UI shell serves (``median_wall_ms``).
  Budget ``JARVIS_BOOT_BUDGET_WINDOW_MS`` (default 8000; measured median on
  the reference box: ~1.2-2.2 s).
- voice TTU: spawn -> ``VOICE_READY_MS`` (wake armed + speech pipeline live).
  Budget ``JARVIS_BOOT_BUDGET_VOICE_MS`` (default 30000; measured median on
  the reference box: ~8.0 s). Checked only when the host can run the voice
  stack (audio device present) — a headless CI box checks the window anchor
  and skips voice honestly instead of faking a pass.

Budgets are deliberately ~4x the measured medians: loose enough that machine
variance never flakes, tight enough that every regression class we have
actually seen (114.7 s load cascade, 12 s gate timeout, 60 s overlay fallback)
blows through them.

Exit codes: 0 = within budget, 1 = budget exceeded, 78 = skipped (no python /
harness prerequisites) so callers can treat "could not measure" as neutral.

How to keep a feature off the critical startup path (the doctrine this guard
enforces): do NOT add work before the speech pipeline / VOICE_READY. New
subsystems belong behind ``_heavy_backend_bg`` (desktop_app), a deferred
registry scan, or a fire-and-forget task AFTER voice-ready. When in doubt run
``scripts/measure_desktop_boot.py --voice --runs 3`` before/after your change.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS = REPO_ROOT / "scripts" / "measure_desktop_boot.py"
TTU_LATEST = REPO_ROOT / "desktop-ttu-latest.json"
LATEST = REPO_ROOT / "desktop-boot-latest.json"

SKIP_EXIT = 78  # EX_CONFIG-ish: "could not measure" — neutral, not a failure

DEFAULT_WINDOW_BUDGET_MS = 8_000.0
DEFAULT_VOICE_BUDGET_MS = 30_000.0


def _audio_capable() -> bool:
    """True when the host can plausibly run the voice stack (mic present)."""
    try:
        import sounddevice  # noqa: PLC0415

        devices = sounddevice.query_devices()
        return any(int(d.get("max_input_channels", 0)) > 0 for d in devices)
    except Exception:  # noqa: BLE001 — no PortAudio / headless box
        return False


def main() -> int:
    window_budget = float(
        os.environ.get("JARVIS_BOOT_BUDGET_WINDOW_MS", DEFAULT_WINDOW_BUDGET_MS)
    )
    voice_budget = float(
        os.environ.get("JARVIS_BOOT_BUDGET_VOICE_MS", DEFAULT_VOICE_BUDGET_MS)
    )
    if not HARNESS.exists():
        print(f"boot-budget: harness missing ({HARNESS}) — skipping", flush=True)
        return SKIP_EXIT

    voice = _audio_capable()
    args = [
        sys.executable,
        str(HARNESS),
        "--runs",
        "1",
        "--warmup",
        "0",
    ]
    if voice:
        args.append("--voice")
    print(
        f"boot-budget: measuring one isolated cold boot (voice={voice}) ...",
        flush=True,
    )
    proc = subprocess.run(  # noqa: S603 — our own harness, fixed argv
        args, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=420
    )
    if proc.returncode != 0:
        print(proc.stdout[-2000:], flush=True)
        print(proc.stderr[-2000:], flush=True)
        print("boot-budget: harness run FAILED — treating as budget failure", flush=True)
        return 1

    latest = TTU_LATEST if voice else LATEST
    summary = json.loads(latest.read_text(encoding="utf-8"))

    failures: list[str] = []
    window_ms = summary.get("median_wall_ms")
    if window_ms is None:
        failures.append("window anchor missing from harness output")
    elif window_ms > window_budget:
        failures.append(
            f"window anchor {window_ms:.0f} ms > budget {window_budget:.0f} ms"
        )

    if voice:
        voice_ms = summary.get("median_voice_usable_wall_ms")
        if voice_ms is None:
            failures.append("VOICE_USABLE anchor missing from harness output")
        elif voice_ms > voice_budget:
            failures.append(
                f"voice TTU {voice_ms:.0f} ms > budget {voice_budget:.0f} ms"
            )
        else:
            print(
                f"boot-budget: voice TTU {voice_ms:.0f} ms <= "
                f"{voice_budget:.0f} ms OK",
                flush=True,
            )
    else:
        print("boot-budget: no audio input device — voice anchor skipped (honest)", flush=True)

    if window_ms is not None and window_ms <= window_budget:
        print(
            f"boot-budget: window {window_ms:.0f} ms <= {window_budget:.0f} ms OK",
            flush=True,
        )

    if failures:
        for f in failures:
            print(f"boot-budget FAILED: {f}", flush=True)
        print(
            "A change put work on the critical startup path. Keep new features "
            "behind _heavy_backend_bg / deferred scans (see the module docstring "
            "and docs/diagnostics/BOOT-TTU-NOTES.md).",
            flush=True,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
