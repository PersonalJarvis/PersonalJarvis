"""FastAPI app factory.

Referenced by the Uvicorn CLI as ``board_backend.main:app``. Tests use
``create_app(settings=...)`` with injected settings instead.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from . import __version__
from .config import Settings
from .db import init_schema, make_engine, make_session_factory

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Builds a FastAPI instance.

    ``settings`` can be overridden for tests. If None: loaded from the
    environment. Routes are imported lazily so test setups can inspect
    the settings first, without module-level code creating the DB.
    """
    if settings is None:
        settings = Settings()

    settings.require_admin_token()  # fail fast instead of a later quirk

    engine = make_engine(settings)
    init_schema(engine)
    session_factory = make_session_factory(engine)

    app = FastAPI(
        title="Jarvis Board Federation Backend",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory

    _attach_routes(app)
    _attach_error_handlers(app)
    _attach_background(app)
    return app


def _attach_background(app: FastAPI) -> None:
    """StoriesCleanup + FederationPuller as asyncio tasks via the lifespan.

    Tests can skip this by setting ``app.state.disable_background = True``
    — the hook checks for that.
    """
    from .background import FederationPuller, StoriesCleanup

    @app.on_event("startup")
    async def _bg_start() -> None:
        if getattr(app.state, "disable_background", False):
            return
        cleanup = StoriesCleanup(session_factory=app.state.session_factory)
        await cleanup.start()
        app.state.stories_cleanup = cleanup

        puller = FederationPuller(session_factory=app.state.session_factory)
        await puller.start()
        app.state.federation_puller = puller

    @app.on_event("shutdown")
    async def _bg_stop() -> None:
        cleanup = getattr(app.state, "stories_cleanup", None)
        if cleanup is not None:
            await cleanup.stop()
        puller = getattr(app.state, "federation_puller", None)
        if puller is not None:
            await puller.stop()


def _attach_routes(app: FastAPI) -> None:
    """Lazy import path — avoids cycles + allows skeleton-only tests."""
    from .routes import (
        activity as activity_route,
        forget_me as forget_me_route,
        health,
        identity,
        me,
        pair as pair_route,
        reactions as reactions_route,
        sync as sync_route,
    )

    app.include_router(health.router)
    app.include_router(identity.router)
    app.include_router(sync_route.router)
    app.include_router(me.router)
    app.include_router(pair_route.router)
    app.include_router(pair_route.friends_router)
    app.include_router(activity_route.router)
    app.include_router(activity_route.fed_router)
    app.include_router(reactions_route.router)
    app.include_router(reactions_route.fed_router)
    app.include_router(forget_me_route.router)


def _attach_error_handlers(app: FastAPI) -> None:
    """Default handler for all exceptions — never a stacktrace to the client."""
    @app.exception_handler(Exception)
    async def _unhandled(request, exc: Exception):  # noqa: ANN001
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "internal error", "code": "internal"},
        )


# Module-level app for ``uvicorn board_backend.main:app``.
#
# ASGI-lazy: if ADMIN_TOKEN isn't set yet at import time (e.g. in tests
# that call ``create_app(settings=...)`` directly), we don't build the
# app. In the production container, docker-compose sets the token via
# the environment, and the import then succeeds with an app.
app: FastAPI | None
try:
    app = create_app()
except RuntimeError as _exc:
    log.warning("module-level app deferred: %s", _exc)
    app = None
