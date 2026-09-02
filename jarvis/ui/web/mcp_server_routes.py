"""Jarvis' two outward MCP surfaces, on one mount.

* ``/api/control/mcp``        — Jarvis' own TOOLS, so a session Jarvis spawned
  can reach back in and drive the app: open an app, read the wiki, switch
  provider. The model's hands are the voice's hands.
* ``/api/control/mcp/agents`` — the AGENT ECOSYSTEM, for any client holding the
  control key: the roster, the board, rooms, assignments, approvals. This is
  what lets Claude Desktop, Cursor or another Jarvis talk to the team.

Mounted as a raw ASGI sub-app rather than a FastAPI router: the MCP Streamable
HTTP transport needs the untouched ``(scope, receive, send)`` triple — it
streams, negotiates its own content types and answers ``GET``/``DELETE`` as
well as ``POST``. Wrapping that in a response model would fight it.

ONE mount serves both, choosing on the rest of the path (``_surface_for``); two
Starlette mounts cannot, because a ``Mount`` requires a path segment AFTER its
prefix and a plain ``POST /api/control/mcp/agents`` would silently fall through
to the shorter one.

Authentication is the Control API's Bearer key, checked here instead of by a
dependency for the same reason. The rule is the Control API's rule (see
``control_auth``): loopback does NOT bypass it. The key is exactly what makes
these surfaces safe to exist — anything on the machine could otherwise open a
socket and start driving the person's calendar, or their agents.

Each surface keeps its own stateless session manager (a fresh transport per
request), so a chat turn that dies mid-tool leaves nothing to clean up. Their
task groups start on first use and live for the process — there is no lifespan
hook on this app to hang them from, and one background task per surface is
cheaper than one per request.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

_UNAUTHORIZED_BODY = b'{"error":"Invalid or missing Jarvis Control API key."}'
_NOT_A_SURFACE_BODY = (
    b'{"error":"No Jarvis MCP surface at this path. '
    b'Use /api/control/mcp or /api/control/mcp/agents."}'
)


class _Surface:
    """One mounted MCP server: its builder plus the session manager it runs on.

    Two surfaces share this machinery — ``tools`` (Jarvis' hands, for a session
    Jarvis spawned) and ``agents`` (the ecosystem, for any client holding the
    control key). Each keeps its OWN manager because the transport's task group
    is per server; sharing one would put both catalogs on one connection and
    make a client choose tools it was never offered.
    """

    def __init__(self, name: str, builder: Callable[[], Any]) -> None:
        self.name = name
        self._builder = builder
        self._manager: Any | None = None
        self._ready: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def manager(self) -> Any | None:
        """The session manager for the CURRENT event loop, built on first use.

        The loop identity check is not paranoia. The transport's task group is
        anyio's, and anyio task groups belong to the loop that created them: a
        manager cached across a loop restart answers every request with
        ``RuntimeError: Task group is not initialized`` and never recovers on
        its own. Rebuilding when the loop changed costs one construction and
        turns a permanently dead surface into a self-healing one.
        """
        loop = asyncio.get_running_loop()
        if self._manager is not None and self._ready is not None and self._loop is loop:
            await self._ready.wait()
            return self._manager
        if self._loop is not None and self._loop is not loop:
            log.debug("jarvis MCP: %s surface rebuilding on a new event loop", self.name)
            self._manager = None
            self._ready = None
            self._task = None
        try:
            from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

            server = self._builder()
        except Exception:  # noqa: BLE001 — no MCP library → the surface is simply absent
            log.warning("jarvis MCP: %s server unavailable", self.name, exc_info=True)
            return None
        manager = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True)
        ready = asyncio.Event()
        self._manager = manager
        self._ready = ready
        self._loop = loop
        self._task = asyncio.create_task(_run_manager(manager, ready))
        await ready.wait()
        return manager


def _build_tools_server() -> Any:
    from jarvis.mcp.jarvis_tools_server import build_server

    return build_server()


def _build_agents_server() -> Any:
    from jarvis.mcp.agents import build_server

    return build_server()


_TOOLS_SURFACE = _Surface("tools", _build_tools_server)
_AGENTS_SURFACE = _Surface("agents", _build_agents_server)


def _bearer(scope: dict[str, Any]) -> str | None:
    for raw_name, raw_value in scope.get("headers") or ():
        if raw_name == b"authorization":
            scheme, _, token = raw_value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
    return None


#: The agent-chat session header, lower-cased as ASGI hands headers over.
_SESSION_HEADER = b"x-jarvis-chat-session"


def session_ref(scope: dict[str, Any]) -> str | None:
    """The chat session id a request names, or ``None`` (any other client)."""
    for raw_name, raw_value in scope.get("headers") or ():
        if raw_name == _SESSION_HEADER:
            value = raw_value.decode("latin-1").strip()
            # A session id is a hex uuid; anything else is not ours to trust.
            if value and len(value) <= 64 and value.replace("-", "").isalnum():
                return value
    return None


async def _reject(send: Any, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _run_manager(manager: Any, ready: asyncio.Event) -> None:
    """Hold the manager's task group open for the life of the process."""
    try:
        async with manager.run():
            ready.set()
            await asyncio.Event().wait()  # never set — cancelled at shutdown
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a dead manager must not take the web server with it
        log.warning("jarvis MCP: session manager stopped", exc_info=True)
    finally:
        ready.set()  # unblock waiters so they fail fast instead of hanging


#: Rest-path suffixes that select a surface, matched after the mount strips
#: ``/api/control/mcp``. Empty (or ``/``) is the tools surface, which is what
#: every already-configured client sends.
_SUFFIX_SURFACES: dict[str, _Surface] = {
    "": _TOOLS_SURFACE,
    "agents": _AGENTS_SURFACE,
}


def surface_suffix(scope: dict[str, Any]) -> str:
    """The path left after the mount prefix, without slashes.

    Starlette ≥ 0.33 leaves ``scope["path"]`` as the FULL request path and
    reports the mount prefix separately in ``root_path``; older releases handed
    the sub-app the remainder in ``path``. Both are handled here, because
    guessing wrong routes every client to the wrong catalog.
    """
    path = scope.get("path") or ""
    root = scope.get("root_path") or ""
    if root and path.startswith(root):
        path = path[len(root) :]
    return path.strip("/")


def _surface_for(scope: dict[str, Any]) -> _Surface | None:
    """Which surface a request is for, from the path left after the mount.

    ONE mount serves both, dispatched here rather than by two Starlette mounts.
    A ``Mount`` on ``/api/control/mcp/agents`` builds the path regex
    ``^/api/control/mcp/agents(?P<path>/.*)$`` — it requires something AFTER
    the mount path, so a plain ``POST /api/control/mcp/agents`` does not match
    it at all and falls through to the shorter mount. The client would then get
    the tools catalog on the agents URL: same transport, same auth, wrong
    tools, and no error anywhere to notice it by.
    """
    return _SUFFIX_SURFACES.get(surface_suffix(scope))


def _authorized(scope: dict[str, Any], surface: _Surface | None) -> bool:
    """Whether this request may proceed.

    Two credentials open the agent surface: the Control API key (the owner at
    their own machine, full scope) and a per-client MCP token (named, scoped,
    individually revocable). Everything else, including the tools surface,
    takes the control key alone — a scoped token has no meaning for a catalog
    that is Jarvis' own hands.
    """
    from jarvis.core import control_key as ck

    presented = _bearer(scope)
    if ck.verify_control_key(presented):
        return True
    if surface is not _AGENTS_SURFACE:
        return False
    from jarvis.mcp.agents import tokens

    return tokens.store().verify(presented) is not None


def _agent_identity(scope: dict[str, Any]) -> tuple[str, str]:
    """``(scope_name, client_name)`` for the credential this request carries.

    The control key is the owner, so it is ``full`` with no client name. A
    token carries whatever scope it was issued with — verified a second time
    here rather than threaded through, because the check is a dict lookup and
    one compare, and a single source of truth beats a faster hand-off.
    """
    from jarvis.core import control_key as ck

    presented = _bearer(scope)
    if ck.verify_control_key(presented):
        return "full", ""
    from jarvis.mcp.agents import tokens

    token = tokens.store().verify(presented)
    if token is None:  # pragma: no cover — _authorized already rejected it
        return "read", ""
    return token.scope, token.name


def build_mcp_asgi_app() -> Any:
    """The ASGI app to mount at ``/api/control/mcp`` — both surfaces.

    * ``/api/control/mcp``         — Jarvis' own tools, for a session it spawned.
    * ``/api/control/mcp/agents``  — the agent ecosystem, for any client holding
      the control key: roster, board, rooms, assignments, approvals.

    Authentication is the Control API's for both, and loopback does NOT bypass
    it: anything on the machine could otherwise open a socket and start driving
    the person's team.
    """

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            return
        # The surface is resolved BEFORE authentication because which
        # credentials are accepted depends on it: scoped MCP tokens open the
        # agent surface, the control key opens both.
        surface = _surface_for(scope)
        if surface is None:
            await _reject(send, 404, _NOT_A_SURFACE_BODY)
            return
        if not _authorized(scope, surface):
            await _reject(send, 401, _UNAUTHORIZED_BODY)
            return
        manager = await surface.manager()
        if manager is None:
            await _reject(send, 503, b'{"error":"Jarvis MCP server is not available."}')
            return
        if surface is _AGENTS_SURFACE:
            # A remote client is not one of Jarvis' own spawned sessions, so
            # there is no chat card to route an approval to; parked actions
            # surface through the approvals_list tool instead.
            #
            # The scope travels in ContextVars the same way the chat session
            # does on the other surface: the stateless transport runs the tool
            # call in a task started from this request, which inherits the
            # context, and the server reads it when it lists and dispatches.
            from jarvis.mcp.agents.server import REQUEST_CLIENT, REQUEST_SCOPE

            scope_name, client_name = _agent_identity(scope)
            scope_token = REQUEST_SCOPE.set(scope_name)
            client_token = REQUEST_CLIENT.set(client_name)
            try:
                await manager.handle_request(scope, receive, send)
            finally:
                REQUEST_SCOPE.reset(scope_token)
                REQUEST_CLIENT.reset(client_token)
            return
        # The session travels in a ContextVar: the stateless transport runs
        # the tool call in a task started from this request, which inherits
        # the context, and the tool server reads it when it builds the
        # executor's approval surface (jarvis_tools_server.approval_snapshot).
        from jarvis.mcp.jarvis_tools_server import CHAT_SESSION_REF

        token = CHAT_SESSION_REF.set(session_ref(scope))
        try:
            await manager.handle_request(scope, receive, send)
        finally:
            CHAT_SESSION_REF.reset(token)

    return app


__all__ = ["build_mcp_asgi_app", "session_ref", "surface_suffix"]
