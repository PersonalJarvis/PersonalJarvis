"""Standalone-Launcher für die Desktop-App.

Aufruf:
    python -m jarvis.ui.web.launcher           # volle Desktop-App
    python -m jarvis.ui.web.launcher --headless # nur Backend, kein Fenster
    python -m jarvis.ui.web.launcher --dev      # dev_mode=True

Dieser Launcher ist BEWUSST getrennt von jarvis/__main__.py damit Phase 1a und
Phase 1b parallel entwickelt werden können ohne Merge-Konflikte.
Die Integration in __main__.py erfolgt in einem späteren Merge-Turn.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import time

# Boot-profiling anchor (opt-in via JARVIS_BOOT_PROFILE=1). ``main()`` stamps the
# earliest in-process moment our code runs; ``_run_headless`` emits one
# authoritative ``BOOT_READY_MS=<n>`` line once the backend is fully serving so
# the boot-timing harness (scripts/measure_boot.py) has a single honest ready
# anchor. Module-global so it survives the asyncio.run boundary in the same
# process. None means "not profiling" → no line is emitted (zero prod change).
_BOOT_PROFILE_T0: float | None = None

# Windows-UTF8-Fix
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

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
    """True fuer Backend-Diagnosen, die nicht als Jarvis-Antwort gelten."""
    t = text.lower()
    return (
        t.startswith("kein brain-key gefunden")
        or t.startswith("keine brain-provider")
        or t.startswith("brain nicht verfuegbar")
        or t.startswith("brain-fehler")
        or "api-key" in t
        or "provider" in t and ("unerreichbar" in t or "nicht verfuegbar" in t)
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="jarvis-launcher",
        description="Phase 1a Desktop-App Standalone-Launcher",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="Nur FastAPI-Backend, kein Fenster (für Dev/Test)",
    )
    p.add_argument(
        "--dev",
        action="store_true",
        help="Setzt ui.dev_mode=True (lädt Frontend von Vite-Dev-Server)",
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
        help="Kein Single-Instance-Lock (für parallele Dev-Sessions)",
    )
    return p.parse_args(argv)


def _acquire_primary_lock_for_headless(
    *, lock_path=None, meta_path=None
):
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
            lock = acquire_single_instance_lock(
                lock_path=lock_path, meta_path=meta_path
            )
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
            "FileLock fallback so a sole VPS instance still stays primary", exc,
        )
        lock = _direct_filelock_fallback(lock_path)

    os.environ["JARVIS_PRIMARY_INSTANCE"] = "1" if lock is not None else "0"
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
    imports) on the time-to-serving path. Falls back to the packaged default."""
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
            path = os.path.join(repo_root, "jarvis.toml")
        if os.path.exists(path):
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
            port = data.get("ui", {}).get("admin_api_port")
            if isinstance(port, int):
                return port
    return _DEFAULT_ADMIN_PORT


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
        # http / websocket: hold until the full app is ready, then delegate.
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
                b"Jarvis backend failed to start."
                if unavailable
                else b"Jarvis is starting up. Please retry."
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
            await send({"type": "websocket.close", "code": 1013})

    _host = "127.0.0.1"
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
        cfg = cfg.model_copy(
            update={"ui": cfg.ui.model_copy(update={"admin_api_port": args.port})}
        )
    try:
        from jarvis.core import control_key

        control_key.ensure_control_key()
    except Exception as exc:  # noqa: BLE001 — never block boot on key bootstrap
        import logging as _logging

        _logging.getLogger(__name__).warning("Control API key bootstrap skipped: %s", exc)

    def _reconcile_autostart_bg() -> None:
        try:
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
    _headless_lock = _acquire_primary_lock_for_headless()
    _lx_mark("lock")

    from jarvis.brain.factory import build_default_brain
    from jarvis.core.events import ErrorOccurred, MessageSent, ResponseGenerated
    from jarvis.state.chat_store import ChatStore, default_chats_db_path
    from jarvis.state.supervisor import Supervisor
    from jarvis.ui.web.server import WebServer

    _lx_mark("imports")

    server = WebServer(cfg)
    _lx_mark("webserver_ctor")

    # Core-State an die App hängen + MessageSent-Subscriber — identisch zur
    # Desktop-App-Verdrahtung. Wichtig: source_layer-Filter gegen Loop.
    supervisor = Supervisor(bus=server.bus)
    # Persist text chats to data/chats.db (next to sessions.db) so the Chats
    # conversation manager has durable, segmented history across restarts.
    chat_store = ChatStore(bus=server.bus, db_path=default_chats_db_path(cfg.memory.data_dir))
    chat_store.open()
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
            built = await asyncio.to_thread(
                build_default_brain, bus=server.bus, tier="router"
            )
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
            detail = brain_holder["error"] or "BrainManager nicht initialisiert"
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
        try:
            generate = getattr(brain, "generate", None)
            if callable(generate):
                # source_layer lets the router exempt a drag-dropped mission
                # recap (ui.web.ws.mission_inject) from force-spawn — discussed
                # inline, never re-dispatched (doom-loop fix 2026-06-16). This
                # is the headless/web (VPS) bridge; desktop_app.py mirrors it.
                reply = await generate(
                    evt.text, trace_id=evt.trace_id, source_layer=evt.source_layer,
                )
            else:
                reply = await brain(evt.text)
        except Exception as exc:  # noqa: BLE001
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
        if reply:
            role = "system" if _is_brain_diagnostic(reply) else "assistant"
            await chat_store.add_message(thread_id=thread_id, role=role, text=reply)

    server.bus.subscribe(MessageSent, _on_user_message)

    # MCP-Registry + Tool-Registry auch im Headless-Mode aufsetzen — sonst
    # sind die /api/mcps + /api/tools Endpoints ohne registry_ready=True.
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

    # Phase 9.8: Overlay-Subprocess starten wenn [overlay].enabled=true.
    # No-op wenn disabled oder JARVIS_DEPTH>0 (Sub-Agent).
    # Bus is passed so mascot-originated user events (mute toggle on
    # doubleClick) can be republished on the EventBus where the voice
    # pipeline subscribes.
    # Orb overlay is a desktop-only extra (separate `overlay` package). On a
    # headless / cloud base install it isn't present — skip it cleanly so the
    # server still boots (cloud-first). The no-op stubs keep the shutdown path
    # (stop_overlay, below) valid. start_overlay itself no-ops when disabled.
    try:
        from jarvis.overlay.integration import start_overlay, stop_overlay
    except ModuleNotFoundError:

        async def start_overlay(*_a: object, **_k: object) -> None:  # type: ignore[misc]
            return None

        async def stop_overlay(*_a: object, **_k: object) -> None:  # type: ignore[misc]
            return None

    await start_overlay(bus=server.bus)

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

    # Wave 2 — token-refresh scheduler: keep connected OAuth plugins' access
    # tokens fresh so long sessions don't start 401-ing mid-flight. Guarded so
    # a failure here never blocks boot (mirrors the MCP autostart above).
    try:
        from jarvis.marketplace.connect_helpers import (
            build_handler_from_catalog,
            connected_plugin_ids,
        )
        from jarvis.marketplace.refresh_scheduler import RefreshScheduler
        from jarvis.marketplace.token_store import TokenStore

        _token_store = TokenStore()
        _refresh_scheduler = RefreshScheduler(
            plugin_ids_fn=lambda: connected_plugin_ids(_token_store),
            store=_token_store,
            build_handler=build_handler_from_catalog,
        )
        _refresh_scheduler.start()
        server.app.state.refresh_scheduler = _refresh_scheduler
        import logging as _logging

        _logging.getLogger(__name__).info(
            "marketplace refresh scheduler started (%d connected plugins) — "
            "tokens kept warm so connections stay alive",
            len(connected_plugin_ids(_token_store)),
        )
    except Exception:
        import logging as _logging

        # A failed scheduler = tokens silently expire = the exact failure mode we
        # are fixing. Surface it at WARNING, never swallow it at DEBUG.
        _logging.getLogger(__name__).warning(
            "marketplace refresh scheduler NOT started — connected plugins may "
            "expire; check the error",
            exc_info=True,
        )

    stop_event = asyncio.Event()

    def _stop(*_):
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    except (ValueError, AttributeError):
        pass

    print(f"Jarvis-Backend läuft auf http://127.0.0.1:{cfg.ui.admin_api_port}")
    print("Strg+C zum Beenden.")

    try:
        await stop_event.wait()
    finally:
        await stop_overlay()  # Phase 9.8: Overlay sauber beenden BEVOR server.stop
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

    return 0


def _run_desktop(cfg, use_lock: bool) -> int:
    """Vollständige Desktop-App mit pywebview-Fenster."""
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
        except SingleInstanceError:
            print("Jarvis läuft bereits.", file=sys.stderr)
            focus_existing_instance_robust()
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


def main(argv: list[str] | None = None) -> int:
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

    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # Fast-boot headless path: bind the port and start serving a minimal
    # bootstrap server FIRST, then build config + the full FastAPI app + every
    # subsystem in the background (the "serve first, init behind" contract). All
    # the heavy main() init below (cwd pin, env heal, load_config, control key,
    # autostart) is deferred into that background build so it never gates the
    # time-to-serving. The desktop path keeps the heavy init up front because the
    # pywebview window needs the resolved config before it can be shown.
    if args.headless:
        return asyncio.run(_run_headless(args))

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

    cfg = load_config()
    _m_mark("parse_cwd_env_loadconfig")

    # CLI-Overrides
    if args.dev:
        cfg = cfg.model_copy(
            update={"ui": cfg.ui.model_copy(update={"dev_mode": True})}
        )
    if args.port is not None:
        cfg = cfg.model_copy(
            update={"ui": cfg.ui.model_copy(update={"admin_api_port": args.port})}
        )

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
