#!/usr/bin/env python
"""GUI-free driver for the desktop cold-boot harness.

Two modes (selected by env ``JARVIS_DESKTOP_BENCH_MODE``):

* ``legacy`` (default) — runs the existing ``DesktopApp._run_backend`` so the
  harness can measure today's ``spawn -> /api/health 200`` baseline (the literal
  gate ``DesktopApp._wait_for_backend`` uses before creating the pywebview
  window).
* ``fastboot`` — runs the serve-first early-bind path: bind the
  :class:`FastBootstrap` on the backend loop *before* the heavy jarvis imports +
  ``WebServer`` build, then build the real app behind it and delegate. This is
  the prototype of the production fix; the harness measures how fast
  ``/api/health`` responds with it.

NOT a production entry point — used only by ``scripts/measure_desktop_boot.py``.
"""

from __future__ import annotations

import asyncio
import os
import time

_T0 = time.perf_counter()


def _boot_ready() -> None:
    if os.environ.get("JARVIS_BOOT_PROFILE") == "1":
        print(f"BOOT_READY_MS={(time.perf_counter() - _T0) * 1000.0:.1f}", flush=True)


def _legacy() -> int:
    from jarvis.core.config import ensure_project_root_cwd, load_config

    ensure_project_root_cwd()
    cfg = load_config()
    port_env = os.environ.get("JARVIS_DESKTOP_BENCH_PORT")
    if port_env:
        cfg = cfg.model_copy(
            update={"ui": cfg.ui.model_copy(update={"admin_api_port": int(port_env)})}
        )
    from jarvis.ui.desktop_app import DesktopApp

    app = DesktopApp(cfg)
    app._run_backend()
    return 0


def _fastboot() -> int:
    """Exercise the REAL production early-bind path (launcher
    ``_desktop_backend_main``) so the harness measures production, not a stub.
    Blocks in run_forever; the parent harness reads the anchor and kills us."""
    import threading
    from types import SimpleNamespace

    port = int(os.environ.get("JARVIS_DESKTOP_BENCH_PORT", "47821"))
    import jarvis.ui.web.launcher as _launcher

    # Give the production BOOT_READY print a t0 (it reads this module global).
    _launcher._BOOT_PROFILE_T0 = _T0
    args = SimpleNamespace(dev=False, port=port, no_lock=True, headless=False)
    holder = {"app": None, "err": None, "lock": None, "already_running": False}
    app_ready = threading.Event()
    # Runs the bootstrap bind → heavy build → app._run_backend(prebound) →
    # run_forever (blocks). Same code the real desktop backend thread runs.
    _launcher._desktop_backend_main(args, port, "bench-token", holder, app_ready)
    return 0


def main() -> int:
    mode = os.environ.get("JARVIS_DESKTOP_BENCH_MODE", "legacy")
    if mode == "fastboot":
        return _fastboot()
    return _legacy()


if __name__ == "__main__":
    raise SystemExit(main())
