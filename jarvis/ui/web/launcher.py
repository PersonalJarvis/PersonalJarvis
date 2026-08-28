"""Standalone launcher for the desktop app.

Usage:
    python -m jarvis.ui.web.launcher           # full desktop app
    python -m jarvis.ui.web.launcher --headless # backend only, no window
    python -m jarvis.ui.web.launcher --dev      # dev_mode=True

This launcher is DELIBERATELY separate from jarvis/__main__.py so Phase 1a and
Phase 1b can be developed in parallel without merge conflicts.
Integration into __main__.py happens in a later merge turn.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import signal
import sys
import time

from jarvis.core.branding import CONFIG_FILE_NAME
from jarvis.core.branding import PRODUCT_NAME as APP_DISPLAY_NAME
from jarvis.core.process_utils import ensure_standard_streams
from jarvis.core.win32_dpi import ensure_dpi_awareness as _ensure_dpi_awareness

# Boot-profiling anchor (opt-in via JARVIS_BOOT_PROFILE=1). ``main()`` stamps the
# earliest in-process moment our code runs; ``_run_headless`` emits one
# authoritative ``BOOT_READY_MS=<n>`` line once the backend is fully serving so
# the boot-timing harness (scripts/measure_boot.py) has a single honest ready
# anchor. Module-global so it survives the asyncio.run boundary in the same
# process. None means "not profiling" → no line is emitted (zero prod change).
_BOOT_PROFILE_T0: float | None = None

# A windowed interpreter (pythonw / a GUI PyInstaller build) has no standard
# streams. Uvicorn probes stdout while configuring its formatter, so repair the
# streams before any desktop/backend construction can begin.
ensure_standard_streams()

# DPI awareness — claim PER_MONITOR_AWARE for the whole process BEFORE anything
# imports pywebview. Windows honours only the FIRST process-awareness claim, and
# pywebview's webview.start() downgrades an unclaimed process to SYSTEM-aware at
# runtime — on a multi-monitor desktop with mixed scale factors that virtualizes
# every window rect, so Computer-Use captured and clicked hundreds of pixels off
# on the secondary monitor (live forensic 2026-07-02: probe window reported at
# x=-1989 while physically at x=-1326; clicks 1/4 hits vs 4/4 with the early
# claim). Same root as the JarvisBar HiDPI shrink (commit 7a6e7d17). No-op off
# Windows.
_ensure_dpi_awareness()


# Taskbar-Icon-Fix (Windows): the unique AUMID must be set BEFORE pywebview
# creates the window. Done in ``_run_desktop`` (the only path with a window) so
# the module import — which the headless fast-boot path pays on the
# time-to-serving path — does not run the ~50 ms COM identity call.
def _ensure_windows_app_identity() -> None:
    if sys.platform != "win32":
        return
    import contextlib

    with contextlib.suppress(Exception):
        from jarvis.ui.icon_utils import ensure_windows_app_identity

        ensure_windows_app_identity()


def _is_brain_diagnostic(text: str) -> bool:
    """True for backend diagnostics that don't count as a Jarvis reply."""
    t = text.lower()
    return (
        t.startswith("kein brain-key gefunden")  # i18n-allow
        or t.startswith("keine brain-provider")  # i18n-allow
        or t.startswith("brain nicht verfuegbar")  # i18n-allow
        or t.startswith("brain-fehler")  # i18n-allow
        or "api-key" in t
        or ("provider" in t and ("unerreichbar" in t or "nicht verfuegbar" in t))  # i18n-allow
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="jarvis-launcher",
        description="Phase 1a desktop app standalone launcher",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="FastAPI backend only, no window (for dev/test)",
    )
    p.add_argument(
        "--dev",
        action="store_true",
        help="Sets ui.dev_mode=True (loads the frontend from the Vite dev server)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override admin_api_port",
    )
    p.add_argument(
        "--no-lock",
        action="store_true",
        help="No single-instance lock (for parallel dev sessions)",
    )
    p.add_argument(
        "--instance",
        default=None,
        metavar="NAME",
        help=(
            "Run as a named instance. 'dev' starts a second, freely restartable "
            "desktop app beside the default one (own data dir, ports, icon, no "
            "wake word / hotkeys / channels). Same as JARVIS_INSTANCE=NAME."
        ),
    )
    return p.parse_args(argv)


def _select_instance_early(argv: list[str]) -> str | None:
    """Pin ``--instance`` into the environment BEFORE anything reads it.

    ``jarvis.core.config`` binds ``DATA_DIR`` and ``jarvis.ui.icon_utils`` binds
    the Windows identity at import, so the choice must be in ``os.environ`` before
    the first of those imports — hence a hand scan of argv here rather than the
    argparse pass further down (which still accepts the flag). Returns an error
    sentence when the name is unknown (the caller reports it and exits), else
    ``None``.
    """
    from jarvis.core.instance import (
        INSTANCE_ENV_VAR,
        InstanceNameError,
        resolve_instance,
        select_instance,
    )

    name: str | None = None
    for i, arg in enumerate(argv):
        if arg == "--instance" and i + 1 < len(argv):
            name = argv[i + 1]
        elif arg.startswith("--instance="):
            name = arg.split("=", 1)[1]
    try:
        if name is not None:
            select_instance(name)
        else:
            resolve_instance()  # validate an inherited JARVIS_INSTANCE
    except InstanceNameError as exc:
        return f"{exc}. Use --instance dev, or unset {INSTANCE_ENV_VAR}."
    return None


def _acquire_primary_lock_for_headless(*, lock_path=None, meta_path=None):
    """Claim primary-instance status for a headless run and set the env flag.

    Decides ``JARVIS_PRIMARY_INSTANCE`` the SAME way the desktop path does:
    whoever holds the single-instance lock is primary and may run the mission
    ``crash_recovery`` sweep. Returns the held lock (release at shutdown) or
    ``None`` when another instance already holds it.

    Why this exists (the 94-occurrence ``crash_recovery`` false-negative,
    live forensic 2026-05-31, missions 019e6fea / 019e7095): headless NEVER
    set ``JARVIS_PRIMARY_INSTANCE``, so ``server.py:_init_mission_stack``
    defaulted it to ``"1"`` (primary) and a parallel headless boot ran
    ``startup_recover`` against the shared ``missions.db`` — sweeping the
    DESKTOP instance's actively-running missions to ``FAILED('crash_recovery')``.

    A headless run that is the SOLE instance (the €5-VPS case) holds the lock
    and stays primary, so genuine orphans are still recovered. A secondary
    headless run (desktop app or another run already holds the lock) marks
    itself NON-primary and must not sweep — but it still boots, because
    headless is explicitly meant to coexist with a primary (tests, parallel
    dev, smoke probes). Unlike the desktop path it therefore never exits on
    a lock conflict.

    ``lock_path`` / ``meta_path`` are test overrides forwarded to
    ``acquire_single_instance_lock``; production uses the defaults.
    """
    lock = None
    try:
        from jarvis.ui.desktop_app import (
            SingleInstanceError,
            acquire_single_instance_lock,
        )

        try:
            lock = acquire_single_instance_lock(lock_path=lock_path, meta_path=meta_path)
        except SingleInstanceError:
            lock = None
    except Exception as exc:  # noqa: BLE001 — lock infra must never block boot
        # Cloud-first guard: if desktop_app cannot be imported (a future GUI
        # top-level import, a trimmed VPS install), do NOT silently fall to
        # non-primary — that would disable crash_recovery on the SOLE €5-VPS
        # instance. Fall back to a direct FileLock on the same path so a lone
        # headless instance still claims primary. ``filelock`` is a base dep.
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "headless lock via desktop_app failed (%s) — trying a direct "
            "FileLock fallback so a sole VPS instance still stays primary",
            exc,
        )
        lock = _direct_filelock_fallback(lock_path)

    os.environ["JARVIS_PRIMARY_INSTANCE"] = "1" if lock is not None else "0"
    return lock


def _claim_headless_primary_lock(args, *, lock_path=None, meta_path=None, port=None):
    """Claim the headless primary lock unless this is an explicit no-lock run.

    When the lock is taken, the PID sidecar is written so a later desktop
    launch can find this process. Headless used to skip that write, which
    left the desktop start with a held lock and no pid to ask about.
    """
    if bool(getattr(args, "no_lock", False)):
        os.environ["JARVIS_PRIMARY_INSTANCE"] = "0"
        return None
    lock = _acquire_primary_lock_for_headless(
        lock_path=lock_path,
        meta_path=meta_path,
    )
    if lock is not None and port is not None:
        try:
            from jarvis.ui.desktop_app import _write_meta

            _write_meta(int(port), os.getpid(), meta_path=meta_path)
        except Exception:  # noqa: BLE001 — sidecar is recovery, not boot
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "headless could not write the instance sidecar",
                exc_info=True,
            )
    return lock


def _direct_filelock_fallback(lock_path=None):
    """Acquire the single-instance lock without importing ``desktop_app``.

    Last-resort path for ``_acquire_primary_lock_for_headless`` so a sole VPS
    instance stays primary even if ``desktop_app`` is unimportable. Uses the
    same on-disk lock path (``DATA_DIR / "jarvis.lock"``) so it still coordinates
    with a desktop instance. No PID-sidecar / stale-detection here — that lives
    in ``acquire_single_instance_lock``; this is only reached when that import
    failed. Returns the held lock or ``None`` (already held / unavailable).
    """
    try:
        from filelock import FileLock, Timeout

        from jarvis.core.config import DATA_DIR

        lp = lock_path or (DATA_DIR / "jarvis.lock")
        lp.parent.mkdir(parents=True, exist_ok=True)
        fl = FileLock(str(lp))
        try:
            fl.acquire(timeout=0.0)
            return fl
        except Timeout:
            return None
    except Exception:  # noqa: BLE001 — never block boot on the fallback either
        return None


_DEFAULT_ADMIN_PORT = 47821


def _fast_admin_port() -> int:
    """Read ``[ui].admin_api_port`` from jarvis.toml with a raw tomllib read (a
    few ms) so the fast-boot bootstrap can bind the REAL port without paying the
    ~240 ms full ``load_config`` (which drags pydantic + the brain/awareness
    imports) on the time-to-serving path. Falls back to the packaged default.

    The configured value is the *base*: a non-default instance (``--instance
    dev``) binds ``InstanceIdentity.port_offset`` above it, exactly as
    ``load_config`` resolves it for the full app — the two must agree or the
    bootstrap and the real server would bind different ports."""
    from jarvis.core.instance import current_instance

    return current_instance().port(_fast_admin_base_port())


def _fast_admin_base_port() -> int:
    import contextlib

    with contextlib.suppress(Exception):  # any failure → packaged default
        import tomllib

        override = os.environ.get("JARVIS_CONFIG")
        if override:
            path = override
        else:
            # launcher.py → jarvis/ui/web/ → repo root is three dirs up.
            repo_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            )
            path = os.path.join(repo_root, CONFIG_FILE_NAME)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
            port = data.get("ui", {}).get("admin_api_port")
            if isinstance(port, int):
                return port
    return _DEFAULT_ADMIN_PORT


def _fast_auth_token_env() -> str:
    """Read ``[ui].auth_token_env`` raw (no ``load_config``) so the fast-boot
    desktop path can generate + set the session token BEFORE the heavy config /
    DesktopApp imports. Falls back to the packaged default ``JARVIS_UI_TOKEN``."""
    import contextlib

    with contextlib.suppress(Exception):
        import tomllib

        override = os.environ.get("JARVIS_CONFIG")
        if override:
            path = override
        else:
            repo_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            )
            path = os.path.join(repo_root, CONFIG_FILE_NAME)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
            name = data.get("ui", {}).get("auth_token_env")
            if isinstance(name, str) and name:
                return name
    return "JARVIS_UI_TOKEN"


def _fast_vite_dev_url(force_dev: bool) -> str | None:
    """Read the trusted Vite origin without loading the full config graph."""
    import contextlib

    configured_dev = False
    vite_url = "http://localhost:5173"
    with contextlib.suppress(Exception):
        import tomllib

        override = os.environ.get("JARVIS_CONFIG")
        if override:
            path = override
        else:
            repo_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            )
            path = os.path.join(repo_root, CONFIG_FILE_NAME)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                ui = tomllib.load(fh).get("ui", {})
            configured_dev = bool(ui.get("dev_mode", False))
            value = ui.get("vite_dev_url")
            if isinstance(value, str) and value:
                vite_url = value
    return vite_url if force_dev or configured_dev else None


async def _run_headless(args) -> int:
    """Headless **fast boot** (the "serve first, init behind" contract).

    A minimal bootstrap ASGI server binds the port and starts serving in a few
    hundred ms; config + the full FastAPI app + every subsystem then build in the
    background. Requests that arrive during the warm-up are HELD server-side
    until the full app is ready and then delegated to it — so the first request
    cleanly waits (never fails), which the functional smoke proves. This keeps
    the heavy ``import fastapi`` (~450 ms) + load_config + the _init chain OFF the
    time-to-serving path.
    """
    _bp = os.environ.get("JARVIS_BOOT_PROFILE") == "1"
    _bp_last = time.perf_counter()

    def _lx_mark(_name: str) -> None:
        nonlocal _bp_last
        _now = time.perf_counter()
        if _bp:
            print(f"[BOOT_PROFILE] lx_{_name}={(_now - _bp_last) * 1000.0:.1f}", flush=True)
        _bp_last = _now

    # Same contract as the desktop loop: a default executor that is already at
    # full size, so ``asyncio.to_thread`` never grows one under the loop (a
    # synchronous ``Thread.start()`` ON the loop — see
    # ``jarvis/core/loop_executor.py``). A headless host runs the same 420
    # ``to_thread`` call sites and has no window to hide the stall behind.
    try:
        from jarvis.core.loop_executor import install_prewarmed_default_executor

        install_prewarmed_default_executor(asyncio.get_running_loop())
    except Exception:  # noqa: BLE001,S110 - a loop that can still stall is the
        # behaviour we had yesterday; a boot that fails is not.
        pass

    # The single-instance lock (and its heavy ``desktop_app`` import — pywebview +
    # win32, ~420 ms) is acquired in the deferred section below, OFF the
    # time-to-serving path. It only needs to set JARVIS_PRIMARY_INSTANCE before
    # the mission stack init, which is also deferred.
    _headless_lock = None
    _lx_mark("lock")

    # === Fast-boot bootstrap: bind the port and serve a holding app NOW ===
    import uvicorn

    _lx_mark("import_uvicorn")

    _full: dict[str, object | None] = {"app": None}
    _full_ready = asyncio.Event()

    async def _bootstrap_app(scope, receive, send):  # noqa: ANN001, ANN202
        kind = scope["type"]
        if kind == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        # Websocket while warming: answer NOW with accept-then-close 1013.
        # Holding the handshake open makes browsers time out after tens of
        # seconds and report an opaque 1006, so a headless boot watched from a
        # browser rendered as a long spurious OFFLINE instead of "starting"
        # (the same contract FastBootstrap serves on the desktop; BUG-065).
        if kind == "websocket" and not _full_ready.is_set():
            await receive()  # consume the websocket.connect event
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1013})
            return

        # http: hold until the full app is ready, then delegate.
        if not _full_ready.is_set():
            try:
                await asyncio.wait_for(_full_ready.wait(), timeout=120.0)
            except TimeoutError:
                await _bootstrap_warming(scope, send)
                return
        app = _full["app"]
        if app is None:
            await _bootstrap_warming(scope, send, unavailable=True)
            return
        await app(scope, receive, send)

    async def _bootstrap_warming(scope, send, *, unavailable: bool = False):  # noqa: ANN001, ANN202
        kind = scope["type"]
        if kind == "http":
            body = (
                b"The assistant backend failed to start."
                if unavailable
                else b"The assistant is starting up. Please retry."
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"retry-after", b"1"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
        elif kind == "websocket":
            # 1013 = "try again later" → clients reconnect once the app is up.
            # Accept first so browsers read the code instead of an opaque 1006.
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1013})

    # Cloud-first: a headless VPS / container must be reachable by a remote
    # browser, which a 127.0.0.1-only listener is not. ``JARVIS_BIND_HOST`` opts
    # into a non-loopback bind (e.g. ``0.0.0.0`` inside Docker); the default
    # stays loopback so desktop installs are byte-for-byte unchanged. The
    # Control-API key is the security boundary on any non-loopback bind, so
    # ``assert_bind_safe`` fails closed without one — mirroring WebServer.start().
    _host = (os.environ.get("JARVIS_BIND_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    if _host not in ("127.0.0.1", "::1", "localhost"):
        from jarvis.core import control_key as _control_key
        from jarvis.ui.web.control_auth import assert_bind_safe

        assert_bind_safe(_host, _control_key.get_control_key())
    _port = args.port if args.port is not None else _fast_admin_port()
    _bootstrap_server = uvicorn.Server(
        uvicorn.Config(
            app=_bootstrap_app,
            host=_host,
            port=_port,
            log_level="warning",
            lifespan="on",
            loop="asyncio",
        )
    )
    _bootstrap_task = asyncio.create_task(_bootstrap_server.serve())
    _deadline = asyncio.get_running_loop().time() + 8.0
    while not _bootstrap_server.started:
        if asyncio.get_running_loop().time() > _deadline:
            raise TimeoutError(f"bootstrap server not ready on {_host}:{_port}")
        if _bootstrap_task.done():
            _exc = _bootstrap_task.exception()
            if _exc is not None:
                raise _exc
            raise RuntimeError("bootstrap serve() ended before 'started'")
        await asyncio.sleep(0.01)

    _lx_mark("bootstrap_serve")

    # === BOOT_READY: the process is serving (full app warms up behind it) ===
    if _BOOT_PROFILE_T0 is not None:
        print(
            f"BOOT_READY_MS={(time.perf_counter() - _BOOT_PROFILE_T0) * 1000.0:.1f}",
            flush=True,
        )

    # === Deferred heavy init (off the time-to-serving path) ===
    from jarvis.core.config import (
        ensure_project_root_cwd,
        load_config,
        refresh_persisted_env_from_user_registry,
    )

    ensure_project_root_cwd()
    refresh_persisted_env_from_user_registry()
    cfg = load_config()
    if args.dev:
        cfg = cfg.model_copy(update={"ui": cfg.ui.model_copy(update={"dev_mode": True})})
    if args.port is not None:
        cfg = cfg.model_copy(update={"ui": cfg.ui.model_copy(update={"admin_api_port": args.port})})
    try:
        from jarvis.core import control_key

        control_key.ensure_control_key()
    except Exception as exc:  # noqa: BLE001 — never block boot on key bootstrap
        import logging as _logging

        _logging.getLogger(__name__).warning("Control API key bootstrap skipped: %s", exc)

    def _reconcile_autostart_bg() -> None:
        try:
            from jarvis.core.instance import current_instance

            if not current_instance().owns_ambient_duties:
                # The login autostart entry belongs to the default app; a dev
                # instance reconciling it would rewrite (or remove) the real one.
                return
            from jarvis.autostart import reconcile_autostart

            reconcile_autostart(cfg)
        except Exception as exc:  # noqa: BLE001 — defense in depth; never block boot
            import logging as _logging

            _logging.getLogger(__name__).warning("Autostart reconcile skipped: %s", exc)

    import threading

    threading.Thread(
        target=_reconcile_autostart_bg, name="autostart-reconcile", daemon=True
    ).start()

    # Single-instance lock — sets JARVIS_PRIMARY_INSTANCE for the mission stack
    # init below. Deferred off the time-to-serving path (its desktop_app import
    # is ~420 ms). After a host crash a stale lock is reclaimed here via the
    # PID-sidecar in acquire_single_instance_lock.
    _headless_lock = _claim_headless_primary_lock(args, port=_port)
    _lx_mark("lock")

    from jarvis.brain.factory import build_default_brain
    from jarvis.core.events import ErrorOccurred, MessageSent, ResponseGenerated
    from jarvis.state.chat_store import ChatStore, default_chats_db_path
    from jarvis.state.supervisor import Supervisor
    from jarvis.ui.web.server import WebServer

    _lx_mark("imports")

    server = WebServer(cfg)
    _lx_mark("webserver_ctor")

    # Attach core state to the app + MessageSent subscriber — identical to the
    # desktop-app wiring. Important: the source_layer filter guards against a loop.
    supervisor = Supervisor(bus=server.bus)
    # Persist text chats to data/chats.db (next to sessions.db) so the Chats
    # conversation manager has durable, segmented history across restarts.
    chat_store = ChatStore(bus=server.bus, db_path=default_chats_db_path(cfg.memory.data_dir))
    chat_store.open()
    # The reasoning trace of a chat turn, stored next to the reply (the desktop
    # bridge does the same) so the history shows the steps behind an answer.
    from jarvis.state.turn_trace import TurnTraceCollector

    turn_traces = TurnTraceCollector(server.bus)
    # Cap unbounded growth at startup (mirrors the session-store prune in
    # sessions/init.py). 365d is deliberately generous — the user wants "all my
    # chats"; voice sessions already prune at 30d and text is tiny — so this only
    # ever clears year-plus-old threads.
    chat_store.prune_older_than(365)
    _lx_mark("chat_store")
    # Brain build (~850 ms) is the single biggest remaining pre-serve step and is
    # NOT needed before uvicorn serves — only the first chat needs it. Build it in
    # a background thread so it overlaps server.start()'s _init chain instead of
    # gating BOOT_READY; the chat path awaits readiness (anti-gaming: a deferred
    # subsystem makes the first request WAIT, never fail). Safe off-loop:
    # build_default_brain is synchronous, BrainManager.__init__ schedules no
    # asyncio work, and EventBus.publish snapshots its subscriber lists
    # (bus.py:82-83) so a subscribe from this thread cannot race a live dispatch.
    brain_holder: dict[str, object | None] = {"brain": None, "error": None}
    brain_ready = asyncio.Event()

    async def _build_brain_bg() -> None:
        try:
            built = await asyncio.to_thread(build_default_brain, bus=server.bus, tier="router")
            brain_holder["brain"] = built
            server.app.state.brain = built
            # Re-wire the late-built brain into the task runner: _init_task_stack
            # (inside server.start, running concurrently) may have captured a None
            # agent_brain because the build was deferred. ``_brain`` is read live
            # at task-execution time (jarvis/tasks/runner.py), so this is safe.
            _runner = getattr(server.app.state, "task_runner", None)
            if (
                _runner is not None
                and getattr(_runner, "_brain", None) is None
                and hasattr(built, "run_task")
            ):
                _runner._brain = built
        except Exception as exc:  # noqa: BLE001
            brain_holder["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            brain_ready.set()

    _lx_mark("brain_build_dispatch")
    server.app.state.supervisor = supervisor
    server.app.state.chat_store = chat_store
    server.app.state.brain = None  # populated by _build_brain_bg when ready
    # Headless/VPS: native file actions would open on the SERVER's desktop, not
    # the user's. Disable them (the frontend hides the buttons; the routes 404).
    server.app.state.native_file_actions = False

    async def _on_user_message(evt: MessageSent) -> None:
        if evt.role != "user":
            return
        if evt.source_layer in ("chat", "brain:mock"):
            return
        thread_id = evt.thread_id or "default"
        turn_started_ms = time.time_ns() // 1_000_000
        # Persist the initiating turn before the reply. Older builds stored only
        # the assistant half, producing anonymous "New Thread" rows that looked
        # like text messages the user never sent.
        await chat_store.add_message(
            thread_id=thread_id,
            role="user",
            text=evt.text,
            publish_event=False,
        )
        # The brain is built in the background (off the boot critical path); a
        # first turn that arrives before it finishes waits (bounded) for it
        # rather than erroring — the honest deferral contract.
        if not brain_ready.is_set():
            try:
                await asyncio.wait_for(brain_ready.wait(), timeout=30.0)
            except TimeoutError:
                pass
        brain = brain_holder["brain"]
        if brain is None:
            detail = brain_holder["error"] or "BrainManager not initialized"
            message = f"Brain unavailable: {detail}"
            await server.bus.publish(
                ErrorOccurred(
                    layer="brain",
                    error_type="BrainUnavailable",
                    message=detail,
                    recoverable=True,
                    source_layer="brain",
                )
            )
            await server.bus.publish(
                ResponseGenerated(
                    trace_id=evt.trace_id,
                    text=message,
                    language="de",
                    source_layer="brain",
                )
            )
            await chat_store.add_message(
                thread_id=thread_id,
                role="system",
                text=message,
            )
            return
        # Instant acknowledgment (2026-08-17): the same first-sign-of-life
        # line the voice engines speak, as a muted pre-ack bubble; cancelled
        # the moment the reply is in (desktop_app.py mirrors it).
        ack_task = None
        try:
            from jarvis.brain.assistant_name import agent_brand
            from jarvis.voice.instant_ack import start_chat_instant_ack

            ack_task = start_chat_instant_ack(
                server.bus,
                text=evt.text,
                thread_id=thread_id,
                trace_id=evt.trace_id,
                brain=brain,
                agent_brand=agent_brand(server.cfg),
            )
        except Exception:  # noqa: BLE001 — a missing ack must never block chat
            import logging as _ack_logging

            _ack_logging.getLogger(__name__).debug("chat instant ack not armed", exc_info=True)
        from jarvis.core.text_stream import TextDeltaPublisher

        delta_publisher = TextDeltaPublisher(
            server.bus,
            channel="chat",
            thread_id=thread_id,
            trace_id=evt.trace_id,
            source_layer="ui.web.chat",
        )
        try:
            generate = getattr(brain, "generate", None)
            if callable(generate):
                # source_layer lets the router exempt a drag-dropped mission
                # recap (ui.web.ws.mission_inject) from force-spawn — discussed
                # inline, never re-dispatched (doom-loop fix 2026-06-16). This
                # is the headless/web (VPS) bridge; desktop_app.py mirrors it.
                try:
                    reply = await generate(
                        evt.text,
                        trace_id=evt.trace_id,
                        source_layer=evt.source_layer,
                        conversation_id=thread_id,
                        text_consumer=delta_publisher.feed,
                    )
                except TypeError:
                    # An older brain shape without the streaming hook.
                    reply = await generate(
                        evt.text,
                        trace_id=evt.trace_id,
                        source_layer=evt.source_layer,
                        conversation_id=thread_id,
                    )
            else:
                reply = await brain(evt.text)
        except Exception as exc:  # noqa: BLE001
            delta_publisher.cancel()
            if ack_task is not None and not ack_task.done():
                ack_task.cancel()
            detail = f"{type(exc).__name__}: {exc}"
            message = f"Brain error: {detail}"
            await server.bus.publish(
                ResponseGenerated(
                    trace_id=evt.trace_id,
                    text=message,
                    language="de",
                    source_layer="brain",
                )
            )
            await chat_store.add_message(
                thread_id=thread_id,
                role="system",
                text=message,
            )
            return
        if ack_task is not None and not ack_task.done():
            ack_task.cancel()
        await delta_publisher.flush(done=True)
        if reply:
            role = "system" if _is_brain_diagnostic(reply) else "assistant"
            trace = turn_traces.snapshot(turn_started_ms) if role == "assistant" else None
            await chat_store.add_message(thread_id=thread_id, role=role, text=reply, trace=trace)

    server.bus.subscribe(MessageSent, _on_user_message)

    # Set up the MCP registry + tool registry in headless mode too — otherwise
    # the /api/mcps + /api/tools endpoints never see registry_ready=True.
    from jarvis.mcp import state as mcp_state
    from jarvis.mcp.registry import MCPRegistry

    mcp_registry = MCPRegistry()
    mcp_registry.load_from_mcp_json()
    server.app.state.mcp_registry = mcp_registry
    # App-Control: expose the live registry to the ``manage-mcp-server`` tool.
    from jarvis.core import runtime_refs

    runtime_refs.set_mcp_registry(mcp_registry)
    tool_registry: dict = {}
    server.app.state.tool_registry = tool_registry

    # Wave 2 — apply the hosted OAuth callback base URL (headless/VPS). Empty
    # keeps loopback/desktop mode; when set, browser-redirect connectors
    # complete via GET /api/marketplace/oauth/callback on this app instead of
    # a 127.0.0.1 listener the VPS browser can't reach.
    from jarvis.marketplace.hosted_callback import set_public_callback_base_url

    set_public_callback_base_url(cfg.marketplace.public_callback_base_url)

    _lx_mark("mcp_registry_and_wiring")

    await server.start(start_serving=False)
    _lx_mark("server_start_total")

    # The full app's init chain is done and the chat handler is subscribed — hand
    # the real ASGI app to the already-listening bootstrap server, which now
    # delegates held + new requests to it. (The brain still builds below; the
    # chat handler awaits brain_ready, so an early first chat cleanly waits.)
    _full["app"] = server.app
    _full_ready.set()

    # Dispatch the brain build only AFTER server.start() so its ~850 ms of
    # CPU-bound import + construction never contends with the boot critical path
    # for the GIL (overlapping it with the _init chain only interleaved CPU work
    # and inflated mission_stack). The task is created here but does not run until
    # the loop next yields (the stop_event wait below), i.e. just after BOOT_READY
    # is emitted — so it builds during the post-serve idle window. The chat path
    # awaits brain_ready (anti-gaming: a cleanly-deferred subsystem makes the
    # first request WAIT, which the functional smoke proves).
    asyncio.create_task(_build_brain_bg(), name="brain-build")

    # (BOOT_READY was already emitted the moment the bootstrap server began
    # serving — the full app built above warms up behind it.)

    # Auto-start all enabled MCP servers as a fire-and-forget task
    async def _autostart_mcps() -> None:
        enabled = mcp_state.get_enabled_names()
        if not enabled:
            return
        try:
            await mcp_registry.start_enabled(enabled)
        except Exception:  # noqa: BLE001
            pass
        try:
            from jarvis.mcp.adapter import register_mcp_tools_in_registry

            adapters = await register_mcp_tools_in_registry(
                mcp_registry,
                tool_registry,
                default_risk_tier=cfg.harness.default_risk_tier,
            )
        except Exception:  # noqa: BLE001
            return

        # Notify the live brain so it picks up MCP tools without restart.
        if adapters:
            try:
                from jarvis.core.events import BrainToolsChanged

                event = BrainToolsChanged(
                    source_layer="launcher._autostart_mcps",
                    reason="mcp_autostart",
                )
                if asyncio.iscoroutinefunction(server.bus.publish):
                    await server.bus.publish(event)
                else:
                    server.bus.publish(event)
            except Exception:  # noqa: BLE001
                pass

    asyncio.create_task(_autostart_mcps())

    stop_event = asyncio.Event()

    def _stop(*_):
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    except (ValueError, AttributeError):
        pass

    # Show the actual bind host (JARVIS_BIND_HOST may be 0.0.0.0 on a VPS);
    # bracket IPv6 literals so the printed URL stays valid.
    _url_host = f"[{_host}]" if ":" in _host else _host
    print(f"Jarvis backend is running at http://{_url_host}:{cfg.ui.admin_api_port}")
    print("Ctrl+C to quit.")

    try:
        await stop_event.wait()
    finally:
        # Stop the fast-boot bootstrap server (it owns the listening socket).
        _bootstrap_server.should_exit = True
        try:
            await asyncio.wait_for(_bootstrap_task, timeout=5.0)
        except Exception:  # noqa: BLE001 — best-effort on shutdown
            _bootstrap_task.cancel()
        await server.stop()
        # Release the single-instance lock on the normal (run-then-SIGINT) path.
        # A crash during the setup phase ABOVE (e.g. server.start raising) skips
        # this and leaks the lock until the next boot, where the PID-sidecar
        # stale-detection in acquire_single_instance_lock reclaims it — so the
        # leak is self-healing, not permanent.
        if _headless_lock is not None:
            try:
                _headless_lock.release()
            except Exception as exc:  # noqa: BLE001 — best-effort release on shutdown
                import logging as _logging

                _logging.getLogger(__name__).debug(
                    "headless lock release failed on shutdown: %s", exc
                )
            try:
                from jarvis.ui.desktop_app import META_FILE_PATH

                META_FILE_PATH.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001 — next boot reclaims a leftover sidecar
                import logging as _logging

                _logging.getLogger(__name__).debug(
                    "headless sidecar cleanup failed on shutdown: %s", exc
                )

    return 0


def _show_error_dialog(title: str, message: str) -> None:
    """Native modal box on every desktop OS — see ``jarvis.ui.native_dialog``.

    Kept as a module-level seam so tests can assert the DECISION to show a box
    without a real one opening: an unattended modal blocks until someone clicks
    it, which in CI is a hung run.
    """
    from jarvis.ui.native_dialog import show_error_dialog

    show_error_dialog(title, message)


def _report_startup_failure(message: str) -> None:
    """Put a desktop-start failure somewhere the user will actually see it.

    A windowless interpreter is the whole problem here. ``pythonw`` — what the
    Start-Menu shortcut and ``run.bat`` use, so no console flashes up — has no
    standard streams, so the reason the app refused to start is written into the
    null device ``ensure_standard_streams`` substituted. From the user's side
    the app "just doesn't start any more": no window, no error, nothing to
    search for. When stderr demonstrably went nowhere, say it in a dialog.

    Whenever stderr IS readable — a terminal, a pipe, a redirected build log —
    the print below is the whole job and a modal box would only be in the way.
    The log line is written either way, so the reason survives for a support
    question even if the box is clicked away.
    """
    print(message, file=sys.stderr, flush=True)
    with contextlib.suppress(Exception):
        from loguru import logger as _slog

        _slog.error(message)
    with contextlib.suppress(Exception):
        from jarvis.core.process_utils import standard_error_is_visible

        if not standard_error_is_visible():
            _show_error_dialog(f"{APP_DISPLAY_NAME} could not start", message)


def _missing_window_toolkit() -> str | None:
    """Explain a missing pywebview, or ``None`` when the window can be built.

    The base install ships without a window toolkit on purpose (it lives in the
    ``[full]`` extra), and the shortcut can also end up aimed at a foreign
    interpreter. Both end in the same ``ModuleNotFoundError`` deep inside the
    boot — checked here so the user gets a sentence instead of a traceback, and
    is told about the window-less mode that does work on their install.
    """
    import importlib.util

    try:
        if importlib.util.find_spec("webview") is not None:
            return None
    except (ImportError, ValueError):  # broken/partial install
        pass
    return (
        f"{APP_DISPLAY_NAME} cannot open its window: the pywebview package is "
        f"missing from this Python install.\n\n"
        f"Python: {sys.executable}\n\n"
        f"Install the desktop extra:\n"
        f"    pip install 'personal-jarvis[full]'\n\n"
        f"Or start it without a window:\n"
        f"    jarvis serve"
    )


def _import_backend_server() -> None:
    # Import, do not find_spec: uvicorn's import is what loads click, and a
    # venv that lost click is a Start-Menu click that opens nothing.
    import uvicorn as _uvicorn  # noqa: F401


def _missing_backend_server() -> str | None:
    """Explain a missing uvicorn/click, or ``None`` when the backend can bind.

    The window never appears until ``/api/health`` answers, and that bind is
    ``import uvicorn`` — which itself ``import click``s. ``find_spec('uvicorn')``
    stays green while click is gone, so the Start-Menu launch sits on a 45 s
    health poll, writes the traceback into pythonw's null stderr, and exits.
    Same shape as :func:`_missing_window_toolkit`; checked up front so the
    reason is a dialog, not silence.
    """
    try:
        _import_backend_server()
    except ModuleNotFoundError as exc:
        missing = exc.name or "uvicorn"
        return (
            f"{APP_DISPLAY_NAME} cannot start: the package '{missing}' is "
            f"missing from this Python install.\n\n"
            f"Python: {sys.executable}\n\n"
            f"The window needs this package to serve its UI. Restore it with:\n"
            f"    {sys.executable} -m pip install -r requirements.txt"
        )
    return None


def _install_boot_trace(raw_argv: list[str]) -> None:
    """Start the desktop log file NOW, so every launch leaves a trace.

    Until 2026-08-25 the file sink was installed by ``DesktopApp.__init__`` —
    after the branded re-exec, the elevation check, config, the control key and
    the single-instance lock. A launch that ended in any of those (an "already
    running" bounce, a crash on an import, a lock held by a stuck earlier
    instance) ran under ``pythonw`` with no console and left NOTHING behind: the
    user clicked, nothing appeared, and the log could not say why (live incident
    2026-08-25, an 11-minute hole between two launches). The sink is cheap
    (loguru + stdlib) and idempotent, so the later ``DesktopApp`` call is a
    no-op against it. Best-effort: a log that cannot be opened never blocks boot.
    """
    try:
        from loguru import logger

        from jarvis.ui.desktop_log import _install_desktop_log_sink, desktop_log_path

        _install_desktop_log_sink(desktop_log_path())
        logger.info(
            "launcher: start pid={} argv={} cwd={}",
            os.getpid(),
            raw_argv,
            os.getcwd(),
        )
    except Exception:  # noqa: BLE001, S110 — a mute launch is what we had before
        pass


_HOLDER_PID_RE = re.compile(r"pid=(\d+)")


def _holder_pid_from_error(error: Exception) -> int | None:
    """Read a pid out of a ``SingleInstanceError`` message, if one was named."""
    match = _HOLDER_PID_RE.search(str(error))
    if match is None:
        return None
    pid = int(match.group(1))
    return pid if pid > 0 else None


def _discover_holder_pid(error: Exception, meta) -> int | None:
    """Name the process that is blocking this desktop launch.

    Order: sidecar, the lock-error text, then whoever is bound to the admin
    port. A headless boot that never wrote the sidecar still has a pid on
    the port; without this the user got "running process is unknown" and
    no window.
    """
    pid = None
    with contextlib.suppress(Exception):
        if meta and meta.get("pid") is not None:
            pid = int(meta["pid"])
    if pid is None:
        with contextlib.suppress(Exception):
            pid = _holder_pid_from_error(error)
    if pid is None:
        with contextlib.suppress(Exception):
            from jarvis.ui.desktop_app import (
                _fallback_admin_port,
                _pid_listening_on_port,
            )

            port = None
            if meta and meta.get("port") is not None:
                port = int(meta["port"])
            if port is None:
                port = _fallback_admin_port()
            found = _pid_listening_on_port(port)
            if found is not None:
                pid = int(found)
    if pid is None or pid <= 0 or pid == os.getpid():
        return None
    return pid


def _process_age_seconds(pid: int) -> float | None:
    """How long ``pid`` has been running, or ``None`` when we cannot tell."""
    if pid <= 0:
        return None
    try:
        import psutil

        return max(0.0, time.time() - float(psutil.Process(pid).create_time()))
    except Exception:  # noqa: BLE001 — unknown / gone / no psutil → not "young"
        return None


def _health_answers(port: int, *, timeout: float = 2.0) -> bool:
    """True only when ``/api/health`` returns 200. A timeout is False.

    Deliberately stricter than ``desktop_app._default_lock_holder_health``,
    which treats a bound-but-silent port as alive so a GIL hitch during boot
    is not evicted. Recovery needs the opposite: a window we raised whose
    server never answers is the frozen-screen case (live 2026-08-28: title
    focus succeeded, health timed out, every further click just raised the
    corpse).
    """
    try:
        import httpx
    except Exception:  # noqa: BLE001 — cannot probe → do not claim a freeze
        return True
    try:
        response = httpx.get(
            f"http://127.0.0.1:{int(port)}/api/health",
            timeout=timeout,
        )
    except Exception:  # noqa: BLE001 — connect fail, timeout, reset
        return False
    return response.status_code == 200


def _recover_from_already_running(
    error: Exception,
    *,
    focus=None,
    read_meta=None,
    ask=None,
    terminate=None,
    acquire=None,
    process_age=None,
    sleep=None,
    now=None,
    booting_grace: float = 20.0,
    discover_pid=None,
    health=None,
):
    """The lock is held. Bring the holder forward — or, with consent, evict it.

    Returns a freshly acquired lock when the user chose to stop a stuck holder
    and the lock could then be taken, else ``None`` (the caller exits 3).

    The honest case — a healthy instance with a window — is handled by focusing
    it, exactly as before. Two stuck cases used to be silent:

    * a holder that is alive but has NO window to bring forward (teardown
      wedged, or a lock the eviction logic could not reclaim);
    * a holder whose window IS still on screen but whose server no longer
      answers (a GIL stall, a hung overlay thread). Focusing that window
      is not recovery — it is the frozen screen the user already has.

    Both turn into one native Yes/No box, and Yes terminates the holder and
    takes the lock. No consent, no kill: the default button is No and any
    failure to ask counts as No.

    A holder that is only a few seconds old and has no window yet (or has a
    window that has not answered health yet) is not stuck — it is still
    booting. Wait out the remaining boot grace before asking.
    """
    from loguru import logger

    from jarvis.ui import desktop_app as _desktop_app

    focus = focus or _desktop_app.focus_existing_instance_robust
    read_meta = read_meta or _desktop_app._read_meta
    terminate = terminate or _desktop_app._terminate_pid
    acquire = acquire or _desktop_app.acquire_single_instance_lock
    process_age = process_age or _process_age_seconds
    sleep = sleep or time.sleep
    now = now or time.monotonic
    if ask is None:
        from jarvis.ui.native_dialog import ask_yes_no

        ask = ask_yes_no

    focused = False
    try:
        focused = bool(focus())
    except Exception as exc:  # noqa: BLE001
        logger.warning("launcher: focusing the running instance failed: {}", exc)

    meta = None
    with contextlib.suppress(Exception):
        meta = read_meta()
    port = None
    if isinstance(meta, dict) and meta.get("port") is not None:
        with contextlib.suppress(Exception):
            port = int(meta["port"])

    def _answers() -> bool | None:
        """None = not probed (no port / no checker). False = frozen."""
        if health is None or port is None:
            return None
        try:
            return bool(health(port))
        except Exception:  # noqa: BLE001 — a probe crash is not a freeze
            return None

    answering = _answers()
    frozen_window = bool(focused) and answering is False
    logger.warning(
        "launcher: {} — {}",
        error,
        "window is frozen (raised, health silent)"
        if frozen_window
        else ("brought its window forward" if focused else "NO window found"),
    )
    # A raised window is recovery only when the holder still answers, or
    # when we did not probe (tests / no port). A silent health check is
    # the frozen desktop: keep going so we can ask.
    if focused and not frozen_window:
        return None

    discover = discover_pid or _discover_holder_pid
    pid = None
    with contextlib.suppress(Exception):
        pid = discover(error, meta)
    if pid is not None and pid != os.getpid() and booting_grace > 0:
        age = None
        with contextlib.suppress(Exception):
            age = process_age(pid)
        if isinstance(age, int | float) and 0 <= float(age) < booting_grace:
            remaining = booting_grace - float(age)
            deadline = now() + remaining
            logger.info(
                "launcher: holder pid={} is only {:.1f}s old with no window yet — "
                "waiting up to {:.1f}s for it to finish booting",
                pid,
                age,
                remaining,
            )
            while now() < deadline:
                sleep(0.5)
                try:
                    focused = bool(focus())
                except Exception as exc:  # noqa: BLE001
                    logger.warning("launcher: focusing the booting instance failed: {}", exc)
                    focused = False
                answering = _answers()
                frozen_window = bool(focused) and answering is False
                if focused and not frozen_window:
                    logger.info(
                        "launcher: booting holder pid={} grew a window — focusing it",
                        pid,
                    )
                    return None
            try:
                focused = bool(focus())
            except Exception as exc:  # noqa: BLE001
                logger.warning("launcher: final focus of booting holder failed: {}", exc)
                focused = False
            answering = _answers()
            frozen_window = bool(focused) and answering is False
            if focused and not frozen_window:
                return None
            logger.info(
                "launcher: booting holder pid={} {}",
                pid,
                "grew a frozen window" if frozen_window else "never grew a window",
            )
    if pid is None or pid == os.getpid():
        _report_startup_failure(
            f"{APP_DISPLAY_NAME} reports that it is already running, but no window "
            "could be found and the running process is unknown.\n\n"
            f"Detail: {error}"
        )
        return None

    if frozen_window:
        stuck_detail = (
            f"{APP_DISPLAY_NAME} is already running (process {pid}), but its "
            "window is frozen and not answering. It is probably stuck.\n\n"
            "Stop that process and start fresh?"
        )
    else:
        stuck_detail = (
            f"{APP_DISPLAY_NAME} is already running (process {pid}), but it has no "
            "window that could be brought to the front. It is probably stuck.\n\n"
            "Stop that process and start fresh?"
        )
    consented = False
    try:
        consented = bool(
            ask(
                f"{APP_DISPLAY_NAME} is already running",
                stuck_detail,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("launcher: could not ask about the stuck instance: {}", exc)
    logger.info(
        "launcher: stuck holder pid={} — user {}",
        pid,
        "consented" if consented else "declined",
    )
    if not consented:
        return None

    if not terminate(pid):
        _report_startup_failure(
            f"{APP_DISPLAY_NAME} could not stop the running process {pid}. "
            "End it in the task manager, then start the app again."
        )
        return None
    try:
        lock = acquire()
    except Exception as exc:  # noqa: BLE001 — SingleInstanceError or lock I/O
        _report_startup_failure(
            f"{APP_DISPLAY_NAME} stopped process {pid}, but the start lock is still "
            f"held. Start the app again in a few seconds.\n\nDetail: {exc}"
        )
        return None
    logger.info("launcher: evicted stuck holder pid={} and took the lock", pid)
    return lock


def _run_desktop(cfg, use_lock: bool) -> int:
    """Full desktop app with a pywebview window.

    ``main()`` has already established that pywebview is importable
    (``_missing_window_toolkit``), so the window layer can be built here.
    """
    # AUMID must be set before pywebview creates the window (taskbar icon).
    _ensure_windows_app_identity()
    from jarvis.ui.desktop_app import (
        DesktopApp,
        SingleInstanceError,
        acquire_single_instance_lock,
        focus_existing_instance_robust,
    )

    lock = None
    if use_lock:
        try:
            lock = acquire_single_instance_lock()
        except SingleInstanceError as exc:
            print(f"{APP_DISPLAY_NAME} is already running.", file=sys.stderr)
            lock = _recover_from_already_running(
                exc,
                focus=focus_existing_instance_robust,
                health=_health_answers,
            )
            if lock is None:
                return 3

    # Fix #2 (2026-05-29): tell the backend whether this is the PRIMARY
    # instance. Only the lock holder may run the mission crash_recovery sweep;
    # a --no-lock parallel-dev instance (lock is None) must NOT sweep, else its
    # boot marks the primary's in-flight missions as crash_recovery and kills
    # live work. The server reads JARVIS_PRIMARY_INSTANCE in _init_mission_stack.
    os.environ["JARVIS_PRIMARY_INSTANCE"] = "1" if lock is not None else "0"

    try:
        return DesktopApp(cfg).run()
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:
                pass


def _serve_bootstrap_with_retry(
    loop,
    host: str,
    port: int,
    *,
    session_token: str | None = None,
    vite_dev_url: str | None = None,
    attempts: int = 5,
    delay: float = 0.4,
    _factory=None,
    _sleep=time.sleep,
):
    """Bind the serve-first bootstrap, retrying a transient post-restart bind race.

    A bind failure on the admin port immediately after an in-app self-restart is
    almost always *transient*: the just-exited old process is still releasing the
    socket, not a live second instance holding it. Treating that first failure as
    "already running" (and bouncing) is the "shuts down but never comes back" bug
    — so retry the bind a few times before giving up. The single-instance lock
    (acquired later in :func:`_desktop_backend_main`) stays the authoritative
    "is another instance live?" check; this only prevents the transient race from
    being misread.

    A fresh ``FastBootstrap`` is built per attempt (a failed ``serve`` leaves a
    spent uvicorn server on the object — never reuse it). Returns the bound
    bootstrap, or ``None`` if every attempt failed (the caller then maps that to
    "already running"). The normal start (free port) binds on the first attempt
    with no delay; only a genuinely, persistently-bound port pays the full backoff.
    """
    from jarvis.ui.web.fast_bootstrap import FastBootstrap

    factory = _factory if _factory is not None else FastBootstrap
    for attempt in range(attempts):
        bootstrap = (
            factory(session_token=session_token, vite_dev_url=vite_dev_url)
            if session_token is not None
            else factory()
        )
        try:
            loop.run_until_complete(bootstrap.serve(host, port))
            return bootstrap
        except Exception:  # noqa: BLE001 — bind failed; retry then treat as busy
            if attempt < attempts - 1:
                _sleep(delay)
    return None


def _desktop_backend_main(args, port: int, token: str, holder: dict, app_ready) -> None:
    """Backend thread for the fast-boot desktop path.

    Binds the serve-first bootstrap FIRST (light imports only), then does the
    heavy config + ``DesktopApp`` build and serves the real app behind the
    bootstrap on the same loop. Communicates back to the main thread via
    *holder* (``app`` / ``err`` / ``lock`` / ``already_running``) + *app_ready*.
    On a post-bind failure it frees the port so the classic fallback can bind.
    """
    import asyncio as _asyncio
    import contextlib as _contextlib
    import threading as _threadmod

    def _t_current():
        return _threadmod.current_thread()

    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)
    bootstrap = None
    try:
        # A bind failure right after a self-restart is usually the old process
        # still releasing the port, NOT a live second instance — so retry the
        # bind before concluding "already running" (the lock below is the real
        # arbiter). Without this, the fresh restart instance bounces and the app
        # "shuts down but never comes back".
        bootstrap = _serve_bootstrap_with_retry(
            loop,
            "127.0.0.1",
            port,
            session_token=token,
            vite_dev_url=_fast_vite_dev_url(bool(args.dev)),
        )
        if bootstrap is None:
            holder["already_running"] = True
            app_ready.set()
            return
        if os.environ.get("JARVIS_BOOT_PROFILE") == "1" and _BOOT_PROFILE_T0 is not None:
            print(
                f"BOOT_READY_MS={(time.perf_counter() - _BOOT_PROFILE_T0) * 1000.0:.1f}",
                flush=True,
            )

        from jarvis.core.config import (
            ensure_project_root_cwd,
            load_config,
            refresh_persisted_env_from_user_registry,
        )

        ensure_project_root_cwd()
        refresh_persisted_env_from_user_registry()
        cfg = load_config()
        if args.dev:
            cfg = cfg.model_copy(update={"ui": cfg.ui.model_copy(update={"dev_mode": True})})
        if args.port is not None:
            cfg = cfg.model_copy(
                update={"ui": cfg.ui.model_copy(update={"admin_api_port": args.port})}
            )
        try:
            from jarvis.core import control_key

            control_key.ensure_control_key()
        except Exception:  # noqa: BLE001 — never block boot on key bootstrap
            pass

        from jarvis.ui.desktop_app import (
            DesktopApp,
            SingleInstanceError,
            acquire_single_instance_lock,
        )

        if not args.no_lock:
            try:
                holder["lock"] = acquire_single_instance_lock()
                os.environ["JARVIS_PRIMARY_INSTANCE"] = "1"
            except SingleInstanceError:
                holder["already_running"] = True
                with _contextlib.suppress(Exception):
                    loop.run_until_complete(bootstrap.stop())
                app_ready.set()
                return
        else:
            os.environ["JARVIS_PRIMARY_INSTANCE"] = "0"

        def _reconcile() -> None:
            try:
                from jarvis.core.instance import current_instance

                if not current_instance().owns_ambient_duties:
                    return  # the login autostart entry belongs to the default app
                from jarvis.autostart import reconcile_autostart

                reconcile_autostart(cfg)
            except Exception:  # noqa: BLE001 — defense in depth; never block boot
                pass

        import threading as _t

        _t.Thread(target=_reconcile, name="autostart-reconcile", daemon=True).start()

        app = DesktopApp(cfg, session_token=token)
        # Pre-publish the backend handles BEFORE app_ready so the main-thread
        # window's shutdown path can never observe them as None (the window path
        # itself only needs cfg + session_token, both set in __init__, so there
        # is no race there — this is belt-and-suspenders for an early close).
        app._backend_loop = loop
        app._bootstrap = bootstrap
        app._backend_thread = _t_current()
        holder["app"] = app
    except Exception as exc:  # noqa: BLE001
        holder["err"] = repr(exc)
        # Free the port so the classic fallback can bind it.
        if bootstrap is not None:
            with _contextlib.suppress(Exception):
                loop.run_until_complete(bootstrap.stop())
        with _contextlib.suppress(Exception):
            loop.close()
        app_ready.set()
        return
    app_ready.set()
    # Build + serve the real app on this loop, reusing the already-bound bootstrap.
    app._run_backend(prebound=(loop, bootstrap))


def _run_desktop_fast(args) -> int | None:
    """Fast-boot desktop entry. Returns the exit code on success, or ``None`` on
    any setup failure so ``main()`` falls back to the classic boot."""
    import logging as _log
    import secrets
    import threading as _threading

    try:
        _ensure_windows_app_identity()  # AUMID before the window (main thread)
        port = args.port if args.port is not None else _fast_admin_port()
        token = secrets.token_urlsafe(32)
        os.environ[_fast_auth_token_env()] = token

        holder: dict = {"app": None, "err": None, "lock": None, "already_running": False}
        app_ready = _threading.Event()
        backend = _threading.Thread(
            target=_desktop_backend_main,
            args=(args, port, token, holder, app_ready),
            name="jarvis-backend",
            daemon=True,
        )
        backend.start()
        if not app_ready.wait(timeout=60.0):
            _log.getLogger(__name__).error("fast-boot backend did not signal in 60s")
            return None
        if holder["already_running"]:
            from loguru import logger

            from jarvis.ui.desktop_app import focus_existing_instance_robust

            print(f"{APP_DISPLAY_NAME} is already running.", file=sys.stderr)
            focused = focus_existing_instance_robust()
            logger.warning(
                "launcher(fast): already running — {}",
                "brought its window forward" if focused else "NO window found",
            )
            return 3
        app = holder["app"]
        if app is None:
            _log.getLogger(__name__).warning(
                "fast-boot backend init failed (%s) — classic fallback", holder["err"]
            )
            return None
        app._backend_thread = backend
    except Exception:  # noqa: BLE001
        _log.getLogger(__name__).exception("fast-boot setup raised — classic fallback")
        return None

    try:
        return app.run_window_only()
    finally:
        lock = holder.get("lock")
        if lock is not None:
            try:
                lock.release()
            except Exception:  # noqa: BLE001
                pass


def main(argv: list[str] | None = None) -> int:
    """Boot the desktop app; a crash before the window is logged AND shown.

    Everything happens in ``_main``. This wrapper exists for the failure that
    used to be invisible: an exception on the way to the window, under a
    ``pythonw`` that has no stderr, ended the process with no trace at all.
    """
    try:
        return _main(argv)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 — last line of defence, must report
        with contextlib.suppress(Exception):
            from loguru import logger

            logger.opt(exception=exc).critical("launcher: crashed before the window")
        _report_startup_failure(
            f"{APP_DISPLAY_NAME} crashed while starting, before its window could "
            f"open.\n\n{type(exc).__name__}: {exc}\n\nThe log file has the details."
        )
        return 1


def _main(argv: list[str] | None = None) -> int:
    # Stamp the boot-profiling t0 as early as possible (only when opted in) so
    # BOOT_READY_MS reflects nearly the full in-process cold-start cost. The
    # harness's spawn→ready wall-clock remains the authoritative headline; this
    # in-process number is the cross-check that excludes interpreter startup.
    _bp_main = os.environ.get("JARVIS_BOOT_PROFILE") == "1"
    if _bp_main:
        global _BOOT_PROFILE_T0
        _BOOT_PROFILE_T0 = time.perf_counter()

    _m_last = time.perf_counter()

    def _m_mark(_name: str) -> None:
        nonlocal _m_last
        _now = time.perf_counter()
        if _bp_main:
            print(f"[BOOT_PROFILE] m_{_name}={(_now - _m_last) * 1000.0:.1f}", flush=True)
        _m_last = _now

    _raw_argv = argv if argv is not None else sys.argv[1:]

    # Which instance this process IS — decided first, before any module that
    # derives a path, a port or an OS identity from it gets imported.
    _instance_error = _select_instance_early(_raw_argv)
    if _instance_error is not None:
        _report_startup_failure(_instance_error)
        return 2
    try:
        from pathlib import Path as _IPath

        import jarvis as _jarvis_pkg
        from jarvis.core.instance_data import ensure_instance_data_dir

        ensure_instance_data_dir(_IPath(_jarvis_pkg.__file__).resolve().parent.parent)
    except Exception as exc:  # noqa: BLE001 — a missing seed is a blank start, not a failed one
        import logging as _ilog

        _ilog.getLogger(__name__).warning("instance data dir could not be prepared: %s", exc)

    # GUI/desktop launches carry a minimal PATH (macOS launchd, Windows tray
    # relaunch) — append the well-known CLI install dirs before any provider
    # probe or worker spawn resolves binaries (stat-only, AP-26-safe).
    from jarvis.core.path_augment import ensure_cli_paths

    ensure_cli_paths()

    # An app launched once from a coding-agent or CI shell inherits that shell's
    # "colour is impossible here" declaration and hands it to every terminal it
    # hosts — panes, and the external terminals opened through
    # `jarvis.clis.external_terminal`, which pass no env of their own. The
    # restart chain re-inherits it, so it survives indefinitely until dropped
    # here. Dict lookups only, no import and no I/O (AP-26-safe).
    from jarvis.core.colour_env import sanitize_process_environment

    _dropped_colour_claims = sanitize_process_environment()
    if _dropped_colour_claims:
        # loguru, not stdlib logging: this runs before anything configures the
        # stdlib root logger, whose default level then discards an INFO record
        # outright — so the one line explaining why the app rewrote its own
        # environment would never reach console or log file. loguru carries a
        # live sink from import.
        from loguru import logger as _clog

        _clog.info(
            "Dropped inherited colour-suppressing variables ({}) so hosted "
            "terminals render in colour; this app was started from a shell that "
            "declared it had none.",
            ", ".join(_dropped_colour_claims),
        )

    args = _parse_args(_raw_argv)

    # From here on every desktop launch is written down — see the helper.
    if not args.headless:
        _install_boot_trace(_raw_argv)

    # Refuse a window we cannot build, BEFORE paying for the boot. Everything
    # below this line — config, control key, autostart, backend, voice — takes
    # up to a minute and would all be thrown away at the pywebview import. Worse,
    # under `pythonw` that import error has nowhere to print, so the app just
    # never appears. Check the one import that decides it, and say so out loud.
    if not args.headless:
        _no_window = _missing_window_toolkit()
        if _no_window is not None:
            _report_startup_failure(_no_window)
            return 4

    # uvicorn (and its import-time click) — every mode, because headless also
    # binds through FastBootstrap. Checked here so a missing dep is a sentence
    # instead of a 45 s mute Start-Menu click.
    _no_server = _missing_backend_server()
    if _no_server is not None:
        _report_startup_failure(_no_server)
        return 4

    # Drop administrator rights BEFORE booting, not after.
    #
    # An elevated window is unreachable for dictation apps, text expanders and
    # password-manager auto-type (Windows UIPI — see
    # ``jarvis/platform/input_isolation.py``), and elevation survives every
    # in-app restart. The app can already escape it, but only once there is a
    # window to restart — so an elevated launch used to pay for a COMPLETE boot
    # and throw it away. Measured 2026-07-29: 102 s discarded, then 18 s to come
    # back. Here it costs one token probe.
    #
    # Desktop only (UIPI is about a window; a headless host has none), and fully
    # best-effort: anything other than "the replacement is starting" boots on in
    # this process, where the input-isolation banner remains the fallback.
    if not args.headless:
        try:
            from pathlib import Path as _Path

            import jarvis as _jarvis
            from jarvis.platform.deescalate import maybe_relaunch_unelevated
            from jarvis.ui.relauncher import detached_creationflags, fresh_user_env

            _drop = maybe_relaunch_unelevated(
                [sys.executable, "-m", "jarvis.ui.web.launcher", *_raw_argv],
                cwd=str(_Path(_jarvis.__file__).resolve().parent.parent),
                env=fresh_user_env(),
                creationflags=detached_creationflags(),
            )
            if _drop is not None:
                import logging as _dlog

                if _drop.ok:
                    _dlog.getLogger(__name__).info(
                        "Started with administrator rights — handing this boot to an "
                        "unelevated copy so dictation and text expanders can reach the "
                        "window (%s). Set %s=1 to keep the elevation instead.",
                        _drop.detail,
                        "JARVIS_KEEP_ELEVATION",
                    )
                    return 0
                _dlog.getLogger(__name__).warning(
                    "Running with administrator rights and could not drop them (%s) — "
                    "booting elevated. Dictation apps and text expanders will not be "
                    "able to type into this window.",
                    _drop.detail,
                )
        except Exception:  # noqa: BLE001,S110 - an app that boots elevated is
            # degraded; one that fails to boot is not an app. The banner still
            # reports the condition, and the in-app restart still repairs it.
            pass

    # Windows taskbar branding: the taskbar button icon is the LAUNCHING EXE's
    # embedded icon, which no window-icon / class-icon / AUMID / Start-Menu /
    # registry / icon-cache work can override (all verified to have no effect on
    # the button). Under a bare ``pythonw.exe`` that icon is the Python logo. Re-
    # exec the SAME launcher through ``PersonalJarvis.exe`` — a pythonw copy
    # carrying the mascot icon — so the taskbar shows the Jarvis ghost. Desktop
    # only (headless has no taskbar), once (env-guarded), and fully best-effort:
    # if branding is unavailable it returns None and we boot in-process as before.
    if not args.headless:
        try:
            from jarvis.ui.icon_utils import maybe_reexec_through_branded_launcher

            _reexec = maybe_reexec_through_branded_launcher(list(_raw_argv))
            if _reexec is not None:
                with contextlib.suppress(Exception):
                    from loguru import logger as _rlog

                    _rlog.info(
                        "launcher: handed this boot to the branded launcher exe "
                        "(exit {}); the child logs from here on",
                        _reexec,
                    )
                return _reexec
        except Exception:  # noqa: BLE001 — never let branding block boot
            pass

    # Fast-boot headless path: bind the port and start serving a minimal
    # bootstrap server FIRST, then build config + the full FastAPI app + every
    # subsystem in the background (the "serve first, init behind" contract). All
    # the heavy main() init below (cwd pin, env heal, load_config, control key,
    # autostart) is deferred into that background build so it never gates the
    # time-to-serving. The desktop path keeps the heavy init up front because the
    # pywebview window needs the resolved config before it can be shown.
    if args.headless:
        return asyncio.run(_run_headless(args))

    # Desktop boot: CLASSIC path (proven + GUI-safe). The serve-first bootstrap
    # + static-shell + boot-splash (the black-screen fix) live in
    # ``DesktopApp._run_backend``, so this path still opens with the real UI
    # shell and no black screen. The "early-bind" launcher path
    # (``_run_desktop_fast``) is kept but NOT the default — it was disabled
    # 2026-06-25 after a no-boot incident under parallel sessions; re-enable only
    # after a real-desktop window sign-off (set JARVIS_DESKTOP_FASTBOOT=1).
    if os.environ.get("JARVIS_DESKTOP_FASTBOOT") == "1":
        _fast_exit = _run_desktop_fast(args)
        if _fast_exit is not None:
            return _fast_exit
        import logging as _flog

        _flog.getLogger(__name__).warning(
            "fast-boot desktop unavailable — falling back to classic boot"
        )

    from jarvis.core.config import (
        ensure_project_root_cwd,
        load_config,
        refresh_persisted_env_from_user_registry,
    )

    # Pin the CWD to the project root BEFORE anything resolves a data/ path. The
    # desktop app is not guaranteed to start from the repo root (autostart task
    # sets a WorkingDirectory, but a manual start / restart-app inherits the user
    # home), and several stores (setup_state.json, the SQLite DBs, flight recorder,
    # audit logs) are CWD-relative — an unpinned CWD re-showed the first-run guide
    # and split Chats/Sessions/Missions across two folders.
    ensure_project_root_cwd()

    # Heal a stale inherited provider env BEFORE load_config: an ancestor process
    # (Explorer at login) can freeze an outdated JARVIS__*__PROVIDER value and
    # pass it to us, where it would override the persisted choice (env > toml) —
    # e.g. a TTS switch to cartesia reverting to gemini-flash-tts on every boot.
    healed = refresh_persisted_env_from_user_registry()
    if healed:
        import logging as _logging

        _logging.getLogger(__name__).info(
            "Healed stale inherited provider env from registry: %s", healed
        )

    # One-time worker-tier heal BEFORE load_config: merge the legacy
    # [brain.sub_jarvis] table into the canonical [brain.worker] so the two
    # can never disagree again (config split-brain). Cheap no-op on healed
    # files; internally best-effort and never blocks boot.
    try:
        from jarvis.core.config_writer import migrate_worker_tier_table

        migrate_worker_tier_table()
    except Exception:  # noqa: BLE001, S110 — boot heal must never block startup
        pass

    cfg = load_config()
    _m_mark("parse_cwd_env_loadconfig")

    # CLI-Overrides
    if args.dev:
        cfg = cfg.model_copy(update={"ui": cfg.ui.model_copy(update={"dev_mode": True})})
    if args.port is not None:
        cfg = cfg.model_copy(update={"ui": cfg.ui.model_copy(update={"admin_api_port": args.port})})

    # Per-user Jarvis Control API key — generate-once BEFORE the app serves so
    # it exists by the time a local agent (Codex CLI / Claude Code) hits
    # /api/control/*. Idempotent; never blocks boot. The clear value is only
    # ever revealed via the loopback Settings panel / the key file — we log the
    # masked form here so the key never lands in a logfile.
    try:
        from jarvis.core import control_key

        _ck = control_key.ensure_control_key()
        import logging as _logging

        _logging.getLogger(__name__).info(
            "Jarvis Control API key ready (%s)", control_key.mask_control_key(_ck)
        )
    except Exception as exc:  # noqa: BLE001 — never block boot on key bootstrap
        import logging as _logging

        _logging.getLogger(__name__).warning("Control API key bootstrap skipped: %s", exc)
    _m_mark("control_key")

    # Self-healing login autostart (the 7th cross-platform port). Runs once at
    # boot, off the voice critical path: if [autostart].enabled is True and the
    # OS entry is missing or points at an old install path, (re)create it; if
    # disabled and present, remove it. On a headless host this is a no-op.
    #
    # Boot-speed fix (measured ~870 ms): this is a fire-and-forget OS-login-entry
    # sync with ZERO dependency on serving — nothing in *this* boot reads the
    # entry (it only matters at the next login) — yet run synchronously here it
    # was the single biggest blocking step in `main()`. Move it to a daemon
    # thread so it overlaps the rest of cold start instead of gating it. It is
    # self-contained (reads the frozen `cfg`, writes an OS entry), touches no
    # asyncio loop and no shared app state, and already swallows all errors
    # (reconcile_autostart) — so a thread is safe; if the process exits before it
    # finishes, the next boot self-heals the entry.
    def _reconcile_autostart_bg() -> None:
        try:
            from jarvis.core.instance import current_instance

            if not current_instance().owns_ambient_duties:
                # The login autostart entry belongs to the default app; a dev
                # instance reconciling it would rewrite (or remove) the real one.
                return
            from jarvis.autostart import reconcile_autostart

            reconcile_autostart(cfg)
        except Exception as exc:  # noqa: BLE001 — defense in depth; never block boot
            import logging as _logging

            _logging.getLogger(__name__).warning("Autostart reconcile skipped: %s", exc)

    import threading

    threading.Thread(
        target=_reconcile_autostart_bg, name="autostart-reconcile", daemon=True
    ).start()
    _m_mark("autostart_dispatch")

    return _run_desktop(cfg, use_lock=not args.no_lock)


if __name__ == "__main__":
    raise SystemExit(main())
