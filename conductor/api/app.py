"""Standalone-FastAPI-App — fuer ``python -m conductor serve``.

Im Embedded-Modus (Jarvis hat einen WebServer) wird nur ``router`` aus
``routes.py`` importiert; diese App hier ist der Entry-Point fuer reinen
Conductor-Betrieb.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..core.runner import Runner
from ..core.scheduler import Scheduler
from ..core.store import ConductorStore
from .routes import router


def create_app(
    store: ConductorStore | None = None,
    start_scheduler: bool = True,
) -> FastAPI:
    """Erzeugt eine standalone FastAPI-App inkl. Store + Runner + Scheduler.

    Lifecycle wird ueber Startup/Shutdown-Events verdrahtet. ``store`` darf
    vorgegeben werden (z.B. Tests mit tmp-DB); default ist
    ``~/.conductor/conductor.sqlite``.
    """
    app = FastAPI(
        title="Conductor — Workflow & Schedule Hub",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],    # OSS-Default; per Env-Var kuerzbar in v0.2
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    active_store = store or ConductorStore()
    runner = Runner(active_store)
    scheduler = Scheduler(active_store, runner)

    app.state.conductor_store = active_store
    app.state.conductor_runner = runner
    app.state.conductor_scheduler = scheduler

    @app.on_event("startup")
    async def _startup() -> None:
        await active_store.init()
        await active_store.cleanup_interrupted_runs()
        # Seed-Jobs beim ersten Start pflanzen.
        from ..core.seed import ensure_seed_jobs
        try:
            added = await ensure_seed_jobs(active_store)
            if added:
                import logging
                logging.getLogger(__name__).info(
                    "Conductor-Seed: %d Jobs pflanzen", added,
                )
        except Exception:  # noqa: BLE001
            pass
        if start_scheduler:
            scheduler.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await scheduler.stop()
        await active_store.close()

    @app.get("/api/health")
    async def _health() -> dict[str, str]:
        return {"ok": "true", "version": "0.1.0", "tool": "conductor"}

    return app
