"""FastAPI application factory for the keyproxy service.

:func:`create_app` builds the app, wiring the config / token store / usage store
/ outbound httpx client onto ``app.state`` (all injectable so tests can supply
an in-memory store and a fake upstream). It mounts:

    - ``GET /healthz``                         — open liveness probe.
    - ``/admin/*``                             — bearer-guarded admin surface.
    - ``/p/{provider_id}/{path:path}``         — the generic streaming passthrough
                                                 (all HTTP methods).

Boot-time HTTPS posture (§6): the proxy authenticates clients with bearer
tokens, which must not travel over plaintext HTTP. Since TLS is terminated by
the platform/reverse-proxy (not by this process), the operator asserts that with
``KEYPROXY_TLS_TERMINATED=1``. If neither that nor ``KEYPROXY_ALLOW_INSECURE=1``
is set, :func:`build_app_from_env` refuses to start.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response

from .admin import build_admin_router
from .config import ProxyConfig, load_config
from .passthrough import handle_passthrough
from .store import Store, default_db_path
from .tokens import TokenStore
from .usage import UsageStore

_ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


class InsecureStartupError(RuntimeError):
    """Raised when token auth would run over plaintext HTTP without an opt-out."""


def create_app(
    *,
    config: ProxyConfig,
    tokens: TokenStore,
    usage: UsageStore,
    upstream: httpx.AsyncClient,
) -> FastAPI:
    """Build the app from explicit dependencies (the test + composition seam)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.upstream.aclose()

    app = FastAPI(title="keyproxy", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.tokens = tokens
    app.state.usage = usage
    app.state.upstream = upstream

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "providers": config.configured_providers(),
        }

    app.include_router(build_admin_router())

    # One generic handler for every method on the passthrough path. Registered
    # explicitly per-method so OPTIONS/HEAD also route here.
    async def _passthrough(request: Request, provider_id: str, path: str) -> Response:
        return await handle_passthrough(request, provider_id, path)

    app.add_api_route(
        "/p/{provider_id}/{path:path}",
        _passthrough,
        methods=_ALL_METHODS,
        include_in_schema=False,
    )

    return app


def _https_posture_ok(cfg: ProxyConfig, env: dict[str, str]) -> bool:
    """True when it is safe to serve token auth (TLS in front, or dev opt-out)."""
    if cfg.allow_insecure:
        return True
    flag = (env.get("KEYPROXY_TLS_TERMINATED") or "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def build_app_from_env(env: dict[str, str] | None = None) -> FastAPI:
    """Production entry point — load config from ENV and refuse insecure boot."""
    src = os.environ if env is None else env
    cfg = load_config(src)

    if not _https_posture_ok(cfg, src):
        raise InsecureStartupError(
            "Refusing to start: token auth requires TLS in front of the proxy. "
            "Set KEYPROXY_TLS_TERMINATED=1 once TLS is terminated by your "
            "platform/reverse-proxy, or KEYPROXY_ALLOW_INSECURE=1 for local dev."
        )

    db_path = (src.get("KEYPROXY_DB_PATH") or "").strip() or str(default_db_path())
    store = Store(db_path)
    tokens = TokenStore(store)
    usage = UsageStore(store)
    upstream = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    return create_app(config=cfg, tokens=tokens, usage=usage, upstream=upstream)


# Module-level ASGI app for ``uvicorn keyproxy.app:app``. Built lazily so that
# importing the module (e.g. for tests) never triggers the insecure-boot guard.
_app: FastAPI | None = None


def __getattr__(name: str) -> object:
    if name == "app":
        global _app
        if _app is None:
            _app = build_app_from_env()
        return _app
    raise AttributeError(name)
