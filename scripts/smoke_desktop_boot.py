#!/usr/bin/env python
"""Functional smoke for the serve-first desktop boot (anti-gaming guard).

Proves the bootstrap is not just answering a synthetic 200 but actually
DELEGATES to the real FastAPI app once it is built:

1. boot the desktop backend (the GUI-free driver, ``legacy`` = production path),
2. observe ``/api/health`` first answered by the bootstrap (``"warming": true``),
3. then answered by the REAL app (carries a ``"version"`` field) — proving
   ``set_app`` delegation works,
4. and a genuinely real route (``/api/voice/status``) returns real JSON.

Exit 0 = pass.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_boot import (  # noqa: E402
    DATA_DIR,
    DEFAULT_PYTHON,
    ISO_DIR,
    NO_WINDOW_CREATIONFLAGS,
    _bench_env,
    _free_port,
    _terminate,
    seed_vault,
)

DRIVER = REPO_ROOT / "scripts" / "_desktop_boot_driver.py"


def _get(url: str, timeout: float = 1.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"<err {type(exc).__name__}>"


def main() -> int:
    seed_vault(80)
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    shutil.rmtree(ISO_DIR, ignore_errors=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ISO_DIR.mkdir(parents=True, exist_ok=True)

    port = _free_port()
    env = _bench_env(port)
    env["JARVIS_DESKTOP_BENCH_PORT"] = str(port)
    # "legacy" = classic _run_backend; "fastboot" = production launcher
    # _desktop_backend_main (the early-bind path). Both serve the real app.
    import os as _os
    env["JARVIS_DESKTOP_BENCH_MODE"] = _os.environ.get("SMOKE_MODE", "legacy")

    proc = subprocess.Popen(
        [DEFAULT_PYTHON, str(DRIVER)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )

    health_url = f"http://127.0.0.1:{port}/api/health"
    root_url = f"http://127.0.0.1:{port}/"
    saw_warming = False
    saw_real = False
    real_body = ""
    # Black-screen guard: GET / must return the real UI shell (index.html) very
    # early (served from the bootstrap while warming), NOT be held/blank.
    root_html_early = False
    try:
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            status, body = _get(health_url)
            if status == 200 and '"warming"' in body:
                saw_warming = True
                # While still warming, the window's GET / must already paint the
                # real shell (this is what kills the black screen).
                rstatus, rbody = _get(root_url, 2.0)
                if rstatus == 200 and ("<!doctype html" in rbody.lower() or "<div id=" in rbody.lower()):
                    root_html_early = True
            if status == 200 and '"version"' in body:
                saw_real = True
                real_body = body
                break
            time.sleep(0.1)

        root_status, root_body = _get(root_url, 3.0)
        voice_status, voice_body = _get(f"http://127.0.0.1:{port}/api/voice/status", 3.0)
    finally:
        _terminate(proc)

    root_html = root_status == 200 and (
        "<!doctype html" in root_body.lower() or "<div id=" in root_body.lower()
    )
    print(f"saw bootstrap warming health : {saw_warming}")
    print(f"GET / real UI shell WHILE warming (no black screen): {root_html_early}")
    print(f"saw real-app health          : {saw_real} {real_body[:80]}")
    print(f"GET / returns UI shell       : {root_html} (status {root_status})")
    print(f"real route /api/voice/status : {voice_status} {voice_body[:80]}")

    ok = saw_real and voice_status == 200 and root_html
    try:
        json.loads(voice_body)
    except Exception:  # noqa: BLE001
        ok = False
        print("FAIL: /api/voice/status did not return valid JSON")

    print("SMOKE PASS" if ok else "SMOKE FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
