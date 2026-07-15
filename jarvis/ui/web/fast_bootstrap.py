"""Reusable serve-first fast-boot bootstrap (the "serve first, init behind" core).

A tiny ASGI holding server that binds the admin port in a few hundred ms and
answers ``GET /api/health`` with 200 the instant it is up — so a shell that
gates on health (the desktop ``DesktopApp._wait_for_backend`` poll, or the
headless harness) sees the process as serving immediately. Every other request
is HELD until the real FastAPI app is registered via :meth:`set_app`, then
delegated to it. The first such request cleanly waits (never fails); everything
after is full speed.

Dependency-light on purpose: nothing heavy is imported at module load and
``uvicorn`` is imported lazily inside :meth:`serve`, so a caller can construct
and bind a :class:`FastBootstrap` *before* paying for ``import fastapi`` /
``load_config`` / the ``jarvis.brain`` import graph — which is exactly what
keeps those costs off the time-to-serving path.

Contract: the real app delegated to runs ON THE SAME EVENT LOOP that serves the
bootstrap. :meth:`set_app` must therefore be called from a coroutine on that
loop (the headless and desktop backends both build the app on the bootstrap's
own loop), so the readiness :class:`asyncio.Event` is set loop-locally.
"""

from __future__ import annotations

import asyncio
import mimetypes
import secrets
from contextlib import suppress
from pathlib import Path
from typing import Any

from .surface_security import SurfaceSecurity

# The built React frontend lives next to this module (jarvis/ui/web/dist),
# the same directory the real FastAPI app serves it from. Resolving it here —
# with NO config / FastAPI import — lets the bootstrap serve the UI shell from
# disk while the heavy app warms up, so the desktop window shows the real UI
# instead of a black screen.
_DEFAULT_DIST_DIR = Path(__file__).resolve().parent / "dist"


class FastBootstrap:
    """Hold-and-delegate ASGI bootstrap server. See module docstring."""

    def __init__(
        self,
        *,
        hold_timeout: float = 120.0,
        dist_dir: Path | None = None,
        session_token: str | None = None,
        vite_dev_url: str | None = None,
    ) -> None:
        self._full: dict[str, Any] = {"app": None}
        self._ready = asyncio.Event()
        self._hold_timeout = hold_timeout
        self._server: Any = None
        self._task: asyncio.Task | None = None
        self._dist_dir = (dist_dir or _DEFAULT_DIST_DIR).resolve()
        self._session_token = session_token or secrets.token_urlsafe(32)
        self._secured_app = SurfaceSecurity(
            self._asgi,
            vite_dev_url=vite_dev_url,
            bootstrap_tokens=(self._session_token,),
        )
        # Listener self-healing (see the "lifecycle" section). The bootstrap
        # OWNS the listening socket for the whole process lifetime (it delegates
        # to the real app via set_app but never hands off the socket), so when a
        # transient OS error kills the accept loop and closes the socket, only
        # the bootstrap can bring the port back. These carry the bind target +
        # the supervisor task so it can re-bind on the same host:port.
        self._host: str | None = None
        self._port: int | None = None
        self._stopping = False
        self._supervisor_task: asyncio.Task | None = None
        self._supervise_interval = 5.0
        # Set once the window's critical shell assets (index.html + the entry
        # JS bundle) have been served. This only proves that bytes left the
        # server; it does not prove that the browser had a chance to paint.
        self._shell_served = asyncio.Event()
        # Set by the boot page after two requestAnimationFrame callbacks. The
        # desktop backend waits on this stronger signal before starting heavy,
        # GIL-holding imports, so slow WebView initialization cannot regress to
        # a blank native window while the browser main thread is still cold.
        self._shell_painted = asyncio.Event()

    # ---- the ASGI callable -------------------------------------------------

    @property
    def app(self) -> Any:
        """The bootstrap ASGI app (handed to uvicorn / driven directly in tests)."""
        return self._entry_app

    async def _entry_app(self, scope: dict, receive: Any, send: Any) -> None:
        """Guard warm-up, then delegate directly to the secured full app."""
        if self._ready.is_set() and self._full["app"] is not None:
            await self._full["app"](scope, receive, send)
            return
        await self._secured_app(scope, receive, send)

    async def _asgi(self, scope: dict, receive: Any, send: Any) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self._handle_lifespan(receive, send)
            return

        # Real app already registered → delegate everything to it (incl. health).
        if self._ready.is_set():
            app = self._full["app"]
            if app is None:
                await self._warming(scope, send, unavailable=True)
                return
            await app(scope, receive, send)
            return

        # Warming: answer health 200 NOW so the window can appear; hold the rest.
        if (
            kind == "http"
            and scope.get("method") == "GET"
            and scope.get("path") == "/api/health"
        ):
            await self._ok_health(send)
            return

        # The static boot page sends this only after the browser has completed
        # at least one visual frame. Keep it dependency-free and available
        # during warm-up; once the real app is registered, the delegation branch
        # above owns all requests again.
        if (
            kind == "http"
            and scope.get("method") == "POST"
            and scope.get("path") == "/api/ui/shell-painted"
        ):
            self._shell_painted.set()
            await self._no_content(send)
            return

        # First-run onboarding must render from the first second — the gate's
        # state/terms/step/complete calls are answered here (stdlib-only
        # handler, shared with the real routes) instead of being held. Once
        # set_app runs, the delegation branch above owns these paths again.
        if kind == "http" and scope.get("path", "").startswith("/api/onboarding"):
            from jarvis.setup.onboarding_fastpath import handle as _onboarding_handle

            if await _onboarding_handle(scope, receive, send):
                return

        # A websocket during warming must NOT hold the handshake open: a
        # browser times out a pending WS handshake (tens of seconds) and its
        # client then escalates its reconnect backoff, so the desktop window
        # shows a long spurious "OFFLINE" after every restart. Accept-then-close
        # with 1013 ("try again later") instead — the client receives a readable
        # close code and reconnects fast once the real app is registered.
        if kind == "websocket":
            await receive()  # consume the websocket.connect event
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1013})
            return

        # Serve the STATIC frontend (index.html + assets + SPA fallback) straight
        # from disk while warming, so the window shows the real UI shell — not a
        # black screen — the instant it opens. Only the dynamic surface (/api/*,
        # /ws) is held below; the SPA's data calls then resolve once the real app
        # is registered. This serves the genuine build (no fake splash).
        path = scope.get("path", "/")
        if (
            kind == "http"
            and scope.get("method") in ("GET", "HEAD")
            and not path.startswith("/api")
            and not path.startswith("/ws")
        ):
            served = await self._serve_static(scope, send)
            if served:
                return
            # No build on disk → fall through to hold (the real app may still
            # render a server-side placeholder once it is up).

        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self._hold_timeout)
        except TimeoutError:
            await self._warming(scope, send)
            return

        app = self._full["app"]
        if app is None:
            await self._warming(scope, send, unavailable=True)
            return
        await app(scope, receive, send)

    @staticmethod
    async def _handle_lifespan(receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    @staticmethod
    async def _ok_health(send: Any) -> None:
        body = b'{"ok": true, "warming": true}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _no_content(send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [(b"cache-control", b"no-store")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    @staticmethod
    async def _warming(scope: dict, send: Any, *, unavailable: bool = False) -> None:
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

    # ---- static frontend (served while warming, no black screen) -----------

    async def _serve_static(self, scope: dict, send: Any) -> bool:
        """Serve the built frontend file for *scope*'s path. Returns False when
        no build is on disk (the caller then holds the request)."""
        target = self._resolve_static_file(scope.get("path", "/"))
        if target is None:
            return False
        try:
            data = await asyncio.to_thread(target.read_bytes)
        except OSError:
            return False
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        headers = [
            (b"content-type", ctype.encode("latin-1")),
            (b"content-length", str(len(data)).encode("latin-1")),
        ]
        if target.name == "index.html":
            # The shell must never be cached stale across a rebuild/restart
            # (mirrors the real app's index response).
            headers.append((b"cache-control", b"no-store, max-age=0"))
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        body = b"" if scope.get("method") == "HEAD" else data
        await send({"type": "http.response.body", "body": body})
        # Mark the transport milestone once an entry JS bundle has gone out.
        # This remains useful for diagnostics, but visible readiness uses the
        # separate browser-originated paint acknowledgment below.
        if target.name.endswith(".js"):
            self._shell_served.set()
        return True

    async def wait_shell_served(self, timeout: float) -> bool:  # noqa: ASYNC109 — bounded readiness wait, conventional param
        """Wait until the window has fetched the shell's entry JavaScript.

        This is a transport diagnostic only: callers that need visual
        readiness must use :meth:`wait_shell_painted`. The wait remains bounded
        so a client that never requests JavaScript cannot stall its caller.
        """
        if self._shell_served.is_set():
            return True
        try:
            await asyncio.wait_for(self._shell_served.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    async def wait_shell_painted(self, timeout: float) -> bool:  # noqa: ASYNC109 — bounded readiness wait, conventional param
        """Wait until the browser confirms that the boot shell was painted.

        Unlike :meth:`wait_shell_served`, this signal comes from the rendered
        page after two animation frames. It therefore remains honest when a
        cold WebView or a busy machine receives the JS bytes before its browser
        main thread has displayed anything. The wait is bounded so a missing or
        broken GUI never prevents the backend from eventually continuing.
        """
        if self._shell_painted.is_set():
            return True
        try:
            await asyncio.wait_for(self._shell_painted.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    def _resolve_static_file(self, path: str) -> Path | None:
        """Map a request path to a real file under dist, or the SPA index.html
        fallback. Returns None when no build exists (caller holds instead).

        Mirrors ``WebServer._register_static_or_spa``: a real file under dist is
        served as-is; any other (client-side route) path falls back to
        index.html so the SPA router can take over.
        """
        index = self._dist_dir / "index.html"
        rel = path.lstrip("/")
        if rel:
            try:
                target = (self._dist_dir / rel).resolve()
                if target.is_file() and self._dist_dir in target.parents:
                    return target
            except (OSError, ValueError):
                pass
        return index if index.is_file() else None

    # ---- lifecycle ---------------------------------------------------------

    def set_app(self, app: Any) -> None:
        """Register the real ASGI app; held + future requests delegate to it.

        Must be called on the loop that serves the bootstrap (see module
        docstring) so the readiness event is set loop-locally.
        """
        self._full["app"] = app
        self._secured_app.sync_local_tokens()
        self._ready.set()

    async def serve(
        self,
        host: str,
        port: int,
        *,
        supervise: bool = True,
        supervise_interval: float = 5.0,
    ) -> None:
        """Bind *port* and start serving the bootstrap on the current loop.

        Returns once the server is accepting connections. ``uvicorn`` is
        imported here (lazily) so the module stays dependency-light.

        When *supervise* is true (the default), a background watchdog keeps the
        listening socket alive: if a transient OS error kills the accept loop
        and closes the socket (the WinError-64 lock-zombie forensic), the
        watchdog re-binds the same host:port so the port never stays dead while
        the process lives on. *supervise_interval* is the probe period.
        """
        self._host = host
        self._port = port
        self._supervise_interval = supervise_interval
        await self._start_server(host, port)
        if supervise and self._supervisor_task is None:
            self._supervisor_task = asyncio.create_task(self._supervise())

    async def _start_server(self, host: str, port: int) -> None:
        """Build + start a fresh uvicorn server on *host:port* and wait until it
        is accepting connections. Shared by :meth:`serve` and :meth:`_rebind`."""
        import uvicorn

        # Pass a plain async function (NOT the bound method ``self._asgi``):
        # uvicorn's ASGI-version probe mis-detects a bound method as ASGI2
        # (``iscoroutinefunction(method.__call__)`` is False), which would call
        # it as ``app(scope)`` and crash. A module-level-style closure is
        # correctly detected as ASGI3.
        _self = self

        async def _asgi3(scope: dict, receive: Any, send: Any) -> None:
            await _self._asgi(scope, receive, send)

        self._server = uvicorn.Server(
            uvicorn.Config(
                app=_asgi3,
                host=host,
                port=port,
                log_level="warning",
                lifespan="on",
                loop="asyncio",
            )
        )
        self._task = asyncio.create_task(self._server.serve())
        deadline = asyncio.get_running_loop().time() + 8.0
        while not self._server.started:
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(f"bootstrap server not ready on {host}:{port}")
            if self._task.done():
                exc = self._task.exception()
                if exc is not None:
                    raise exc
                raise RuntimeError("bootstrap serve() ended before 'started'")
            await asyncio.sleep(0.01)

    async def _listener_alive(self) -> bool:
        """True if the bound port still accepts a TCP connection.

        An active loopback connect — not an inspection of uvicorn/asyncio
        internals — so it is version-proof and tests the one thing that matters:
        can a request still reach us. The proactor's ``sock.close()`` on
        WinError 64 makes this connect fail with ECONNREFUSED, which is exactly
        the dead-listener signal the watchdog acts on.
        """
        if self._host is None or self._port is None:
            return True  # never bound → nothing to supervise
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), timeout=2.0
            )
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
            return True
        except (OSError, TimeoutError):
            return False

    async def _supervise(self) -> None:
        """Watchdog: keep the listening socket alive for the process lifetime.

        Probes the bound port every ``_supervise_interval`` seconds; on a
        confirmed dead listener (two probes, to ride out a one-off transient) it
        re-binds. Self-contained + error-swallowing so it can never crash the
        backend loop; it simply retries on the next tick.
        """
        from loguru import logger

        while not self._stopping:
            try:
                await asyncio.sleep(self._supervise_interval)
                if self._stopping:
                    return
                if await self._listener_alive():
                    continue
                # Confirm with a second probe so a single transient blip does
                # not trigger an unnecessary re-bind.
                await asyncio.sleep(0.2)
                if self._stopping or await self._listener_alive():
                    continue
                logger.warning(
                    "fast-boot: listening socket on {}:{} is dead — re-binding "
                    "(transient OS socket error / WinError 64 recovery)",
                    self._host,
                    self._port,
                )
                await self._rebind()
                logger.info(
                    "fast-boot: listener re-bound on {}:{} — port recovered",
                    self._host,
                    self._port,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — never let the watchdog die
                logger.opt(exception=exc).error(
                    "fast-boot: listener watchdog tick failed (will retry next tick)"
                )

    async def _rebind(self) -> None:
        """Tear down the dead uvicorn server and start a fresh one on the same
        host:port. The registered real app + readiness state are untouched, so
        delegation survives the listener swap.
        """
        if self._host is None or self._port is None:
            return
        await self._teardown_server()
        await self._start_server(self._host, self._port)

    async def _teardown_server(self) -> None:
        """Stop the current uvicorn server + serve task (no effect on the
        socket already closed by the OS; just reaps the dead task)."""
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await self._task
        self._server = None
        self._task = None

    async def _kill_listener_for_test(self) -> None:
        """Test-only: close the uvicorn listening socket exactly as the asyncio
        proactor does on WinError 64, so a test can drive the self-heal path
        without a real network fault."""
        server = self._server
        for sub in getattr(server, "servers", []) or []:
            sub.close()
            with suppress(Exception):
                await sub.wait_closed()

    async def stop(self) -> None:
        """Stop the bootstrap server (it owns the listening socket)."""
        self._stopping = True
        if self._supervisor_task is not None:
            self._supervisor_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._supervisor_task
            self._supervisor_task = None
        await self._teardown_server()
