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

# Windows-UTF8-Fix
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# Taskbar-Icon-Fix (Windows): eindeutige AUMID muss gesetzt sein BEVOR
# pywebview das Fenster erzeugt, sonst cached Windows die Python-Zuordnung.
if sys.platform == "win32":
    try:
        from jarvis.ui.icon_utils import ensure_windows_app_identity

        ensure_windows_app_identity()
    except Exception:  # noqa: BLE001
        pass


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


async def _run_headless(cfg) -> int:
    """Nur FastAPI-Backend starten, bis SIGINT.

    Enthält denselben Mock-Brain-Event-Hook wie ``DesktopApp._run_backend``,
    damit Chat-Messages auch im Headless-Mode End-to-End antworten. Ohne den
    Hook kämen User-Messages am Server an, aber kein Assistant-Reply.
    """
    # Decide primary-instance status BEFORE the WebServer boots its mission
    # stack: only the lock holder may run the crash_recovery sweep. A secondary
    # headless run marks itself non-primary here so it cannot poison the
    # primary's live missions (the 94-occurrence false-negative).
    _headless_lock = _acquire_primary_lock_for_headless()
    from jarvis.brain.factory import build_default_brain
    from jarvis.core.events import ErrorOccurred, MessageSent, ResponseGenerated
    from jarvis.state.chat_store import ChatStore, default_chats_db_path
    from jarvis.state.supervisor import Supervisor
    from jarvis.ui.web.server import WebServer

    server = WebServer(cfg)

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
    brain = None
    brain_build_error: str | None = None
    try:
        brain = build_default_brain(bus=server.bus, tier="router")
    except Exception as exc:  # noqa: BLE001
        brain_build_error = f"{type(exc).__name__}: {exc}"
    server.app.state.supervisor = supervisor
    server.app.state.chat_store = chat_store
    server.app.state.brain = brain

    async def _on_user_message(evt: MessageSent) -> None:
        if evt.role != "user":
            return
        if evt.source_layer in ("chat", "brain:mock"):
            return
        thread_id = evt.thread_id or "default"
        if brain is None:
            detail = brain_build_error or "BrainManager nicht initialisiert"
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

    await server.start()

    # Phase 9.8: Overlay-Subprocess starten wenn [overlay].enabled=true.
    # No-op wenn disabled oder JARVIS_DEPTH>0 (Sub-Agent).
    # Bus is passed so mascot-originated user events (mute toggle on
    # doubleClick) can be republished on the EventBus where the voice
    # pipeline subscribes.
    from jarvis.overlay.integration import start_overlay, stop_overlay
    await start_overlay(bus=server.bus)

    # Auto-Start aller enabled MCP-Server als fire-and-forget Task
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

            await register_mcp_tools_in_registry(
                mcp_registry,
                tool_registry,
                default_risk_tier=cfg.harness.default_risk_tier,
            )
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
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    from jarvis.core.config import load_config, refresh_persisted_env_from_user_registry

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

    # Self-healing login autostart (the 7th cross-platform port). Runs once at
    # boot, off the voice critical path: if [autostart].enabled is True and the
    # OS entry is missing or points at an old install path, (re)create it; if
    # disabled and present, remove it. On a headless host this is a no-op. Never
    # raises (reconcile_autostart swallows everything) — autostart must not block
    # or crash boot.
    try:
        from jarvis.autostart import reconcile_autostart

        reconcile_autostart(cfg)
    except Exception as exc:  # noqa: BLE001 — defense in depth; never block boot
        import logging as _logging

        _logging.getLogger(__name__).warning("Autostart reconcile skipped: %s", exc)

    if args.headless:
        return asyncio.run(_run_headless(cfg))
    return _run_desktop(cfg, use_lock=not args.no_lock)


if __name__ == "__main__":
    raise SystemExit(main())
