"""One-shot OAuth callback listener on `127.0.0.1:<random>`.

Used by every browser-redirect handler (`HostedMcpDcrHandler`,
`PkceLoopbackHandler`). Self-contained: binds an ephemeral free port on
the loopback interface, serves a single `/callback` route, validates the
OAuth `state` parameter for CSRF, hands the captured code to the handler
via an asyncio Future, then shuts itself down.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from contextlib import suppress
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)


_SUCCESS_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Personal Jarvis</title>
<style>body{font-family:system-ui,sans-serif;background:#0a0a0a;color:#eee;
display:grid;place-items:center;height:100vh;margin:0}
main{text-align:center;max-width:32rem;padding:2rem}
h1{font-weight:500;margin:0 0 1rem;color:#FFD60A}
p{opacity:.7}</style></head>
<body><main><h1>Connected.</h1>
<p>You can close this tab. Personal Jarvis received the authorization.</p>
</main></body></html>"""


_ERROR_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Personal Jarvis</title>
<style>body{{font-family:system-ui,sans-serif;background:#0a0a0a;color:#eee;
display:grid;place-items:center;height:100vh;margin:0}}
main{{text-align:center;max-width:32rem;padding:2rem}}
h1{{font-weight:500;margin:0 0 1rem;color:#f87171}}
p{{opacity:.7}}</style></head>
<body><main><h1>Authorization failed.</h1>
<p>{reason}</p></main></body></html>"""


@dataclass(frozen=True, slots=True)
class CallbackResult:
    code: str
    state: str


class CallbackTimeoutError(TimeoutError):
    pass


def provider_callback_error(error: str) -> str:
    """Only expose known protocol codes, never provider-controlled descriptions."""
    if error == "access_denied":
        return "Authorization was denied (access_denied)."
    return "The provider could not authorize this connection. Please try again."


class OAuthCallbackServer:
    """One-shot loopback listener.

    Lifecycle:
        srv = OAuthCallbackServer(expected_state="...")
        await srv.start()
        # ... user is redirected to srv.redirect_uri with ?code=&state=
        result = await srv.await_callback()
        await srv.stop()
    """

    def __init__(
        self,
        expected_state: str,
        timeout_seconds: float = 300.0,
        callback_path: str = "/callback",
        port: int | None = None,
    ) -> None:
        """`port=None` picks a random free ephemeral port at start() time.
        `port=N` binds a specific port — required for plugins like Slack
        whose registered redirect_uri is fixed (e.g. localhost:3118).
        """
        if not expected_state:
            raise ValueError("expected_state must be non-empty")
        if callback_path and not callback_path.startswith("/"):
            raise ValueError("callback_path must be empty or start with '/'")
        self._expected_state = expected_state
        self._timeout = timeout_seconds
        self._path = callback_path
        self._port: int | None = port
        self._future: asyncio.Future[CallbackResult] | None = None
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._socket: socket.socket | None = None

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("server not started")
        return self._port

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}{self._path}"

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("already started")

        # Retain the bound socket so another process cannot steal an ephemeral
        # port between selection and uvicorn startup. Port zero requests the
        # OS-selected port, which must also appear in the redirect URI.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", self._port or 0))
            listener.setblocking(False)
        except BaseException:
            listener.close()
            raise
        self._socket = listener
        self._port = listener.getsockname()[1]
        self._future = asyncio.get_running_loop().create_future()

        config = uvicorn.Config(
            self._build_app(),
            host="127.0.0.1",
            port=self._port,
            log_level="critical",
            lifespan="off",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

        self._serve_task = asyncio.create_task(
            self._server.serve(sockets=[listener]), name=f"oauth-callback:{self._port}"
        )

        try:
            for _ in range(50):
                if self._server.started:
                    return
                if self._serve_task.done():
                    await self._serve_task
                    raise RuntimeError("callback listener stopped during startup")
                await asyncio.sleep(0.1)
            raise RuntimeError("uvicorn failed to start within 5 seconds")
        except BaseException:
            await self.stop()
            raise

    async def await_callback(self) -> CallbackResult:
        if self._future is None:
            raise RuntimeError("server not started")
        try:
            return await asyncio.wait_for(self._future, timeout=self._timeout)
        except TimeoutError as exc:
            raise CallbackTimeoutError(f"no callback received within {self._timeout}s") from exc

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        try:
            if self._serve_task is not None:
                with suppress(asyncio.CancelledError):
                    await asyncio.wait_for(self._serve_task, timeout=5.0)
        finally:
            self._serve_task = None
            self._server = None
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            if self._future is not None:
                if not self._future.done():
                    self._future.cancel()
                elif not self._future.cancelled():
                    # Retrieve abandoned failures to avoid unhandled-future logs.
                    self._future.exception()

    def _build_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        route_path = self._path or "/"

        @app.get(route_path, response_model=None)
        async def callback(request: Request):
            params = request.query_params
            error = params.get("error")
            state = params.get("state", "")
            code = params.get("code", "")

            future = self._future
            if future is None or future.done():
                return HTMLResponse(_SUCCESS_HTML)

            if state != self._expected_state:
                future.set_exception(RuntimeError("state mismatch — possible CSRF; aborted"))
                return HTMLResponse(
                    _ERROR_HTML.format(reason="State parameter mismatch."),
                    status_code=400,
                )

            if error:
                reason = provider_callback_error(error)
                future.set_exception(RuntimeError(reason))
                return HTMLResponse(_ERROR_HTML.format(reason=reason), status_code=400)

            if not code:
                future.set_exception(RuntimeError("missing 'code' parameter"))
                return HTMLResponse(
                    _ERROR_HTML.format(reason="Missing authorization code."),
                    status_code=400,
                )

            future.set_result(CallbackResult(code=code, state=state))
            return HTMLResponse(_SUCCESS_HTML)

        return app
