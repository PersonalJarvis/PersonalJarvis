"""Global Host, Origin, and credential guard for Jarvis web surfaces.

The desktop UI, a normal browser, ``jarvisctl``, and public provider callbacks
all share one ASGI application.  Route-local authentication is therefore not a
safe outer boundary: a newly mounted router or WebSocket can otherwise become
public by accident.  :class:`SurfaceSecurity` is the dependency-light outer
guard used by both the serve-first bootstrap and the full FastAPI app.

The module intentionally imports neither FastAPI nor Starlette.  Credential
stores are imported lazily only when a protected request presents a credential,
so applying the guard cannot add their import graphs to the boot critical path.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import sys
import threading
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from http.cookies import CookieError, SimpleCookie
from typing import Any, Literal
from urllib.parse import urlsplit

COOKIE_NAME = "jarvis_session"

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_SAFE_HTTP_METHODS = frozenset({"GET", "HEAD"})
_UNSAFE_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~-]+$")

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]
CredentialValidator = Callable[[str], bool]
AuthKind = Literal["control", "session"]
Origin = tuple[str, str, int | None]

_BOOTSTRAP_TOKEN_LOCK = threading.Lock()
_BOOTSTRAP_TOKENS: set[str] = set()


def _register_bootstrap_tokens(tokens: Iterable[str]) -> None:
    """Register process-local tokens that may only mint one UI session."""
    valid = {token for token in tokens if isinstance(token, str) and token}
    if not valid:
        return
    with _BOOTSTRAP_TOKEN_LOCK:
        _BOOTSTRAP_TOKENS.update(valid)


def _bootstrap_token_is_valid(token: str) -> bool:
    with _BOOTSTRAP_TOKEN_LOCK:
        return token in _BOOTSTRAP_TOKENS


def _consume_bootstrap_token(token: str) -> bool:
    """Atomically consume a bootstrap token so concurrent replay fails."""
    with _BOOTSTRAP_TOKEN_LOCK:
        if token not in _BOOTSTRAP_TOKENS:
            return False
        _BOOTSTRAP_TOKENS.remove(token)
        return True


def _iter_values(value: str | Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _headers(scope: Scope, name: str) -> list[str]:
    wanted = name.lower().encode("latin-1")
    values: list[str] = []
    for raw_name, raw_value in scope.get("headers", ()):
        if bytes(raw_name).lower() != wanted:
            continue
        try:
            values.append(bytes(raw_value).decode("latin-1").strip())
        except (AttributeError, UnicodeDecodeError):
            return []
    return values


def _single_header(scope: Scope, name: str) -> str | None:
    values = _headers(scope, name)
    if len(values) != 1:
        return None
    return values[0]


def _normalise_hostname(value: str) -> str | None:
    host = value.strip().rstrip(".")
    if not host or any(ch.isspace() for ch in host):
        return None
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        return host.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None


def _parse_authority(value: str) -> tuple[str, int | None] | None:
    """Parse a Host/authority value without accepting URL-like ambiguity."""
    authority = value.strip()
    if (
        not authority
        or any(ch.isspace() for ch in authority)
        or any(ch in authority for ch in "/\\@,?#")
    ):
        return None

    host_text: str
    port: int | None = None
    if authority.startswith("["):
        closing = authority.find("]")
        if closing <= 1:
            return None
        host_text = authority[1:closing]
        suffix = authority[closing + 1 :]
        if suffix:
            if not suffix.startswith(":") or not suffix[1:].isdigit():
                return None
            port = int(suffix[1:])
    elif authority.count(":") == 1:
        host_text, port_text = authority.rsplit(":", 1)
        if not port_text.isdigit():
            return None
        port = int(port_text)
    elif ":" in authority:
        # RFC 3986 requires an IPv6 literal in an authority to be bracketed.
        return None
    else:
        host_text = authority

    if port is not None and not 0 < port <= 65535:
        return None
    host = _normalise_hostname(host_text)
    if host is None:
        return None
    return host, port


def _parse_url_host(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    if "://" not in raw:
        parsed = _parse_authority(raw)
        return parsed[0] if parsed is not None else None
    try:
        split = urlsplit(raw)
        if split.scheme.lower() not in {"http", "https", "ws", "wss"}:
            return None
        if split.username is not None or split.password is not None:
            return None
        return _normalise_hostname(split.hostname or "")
    except (ValueError, UnicodeError):
        return None


def _parse_origin(value: str) -> Origin | None:
    raw = value.strip()
    if not raw or raw.lower() == "null" or any(ch.isspace() for ch in raw):
        return None
    try:
        split = urlsplit(raw)
        scheme = split.scheme.lower()
        if scheme not in {"http", "https"}:
            return None
        if split.username is not None or split.password is not None:
            return None
        if split.path not in {"", "/"} or split.query or split.fragment:
            return None
        host = _normalise_hostname(split.hostname or "")
        if host is None:
            return None
        port = split.port
    except (ValueError, UnicodeError):
        return None
    if port == (80 if scheme == "http" else 443):
        port = None
    return scheme, host, port


def _scope_authority(scope: Scope) -> tuple[str, int | None] | None:
    host = _single_header(scope, "host") or _single_header(scope, ":authority")
    return _parse_authority(host) if host is not None else None


def _scope_origin(scope: Scope) -> Origin | None:
    authority = _scope_authority(scope)
    if authority is None:
        return None
    scheme = str(scope.get("scheme", "http") or "http").lower()
    scheme = {"ws": "http", "wss": "https"}.get(scheme, scheme)
    if scheme not in {"http", "https"}:
        return None
    host, port = authority
    if port == (80 if scheme == "http" else 443):
        port = None
    return scheme, host, port


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _env_trusted_hosts() -> tuple[str, ...]:
    raw = os.environ.get("JARVIS_TRUSTED_HOSTS", "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _trusted_hostnames(
    *,
    trusted_hosts: str | Iterable[str] | None = None,
    public_urls: str | Iterable[str] | None = None,
    vite_dev_url: str | None = None,
) -> frozenset[str]:
    result = set(_LOOPBACK_HOSTS)
    for item in (*_env_trusted_hosts(), *_iter_values(trusted_hosts)):
        host = _parse_url_host(item)
        if host is not None:
            result.add(host)
    for item in (*_iter_values(public_urls), *_iter_values(vite_dev_url)):
        host = _parse_url_host(item)
        if host is not None:
            result.add(host)
    return frozenset(result)


def _trusted_origin_values(
    *,
    trusted_origins: str | Iterable[str] | None = None,
    public_urls: str | Iterable[str] | None = None,
    vite_dev_url: str | None = None,
) -> frozenset[Origin]:
    result: set[Origin] = set()
    for item in (
        *_iter_values(trusted_origins),
        *_iter_values(public_urls),
        *_iter_values(vite_dev_url),
    ):
        origin = _parse_origin(item)
        if origin is not None:
            result.add(origin)
    return frozenset(result)


def scope_host_is_trusted(
    scope: Scope,
    *,
    trusted_hosts: str | Iterable[str] | None = None,
    public_urls: str | Iterable[str] | None = None,
    vite_dev_url: str | None = None,
) -> bool:
    """Return whether ``scope`` carries a non-ambiguous trusted Host header.

    Loopback names and syntactically valid IP literals are accepted on any
    port.  DNS names must be configured by URL, constructor argument, or the
    comma-separated ``JARVIS_TRUSTED_HOSTS`` environment variable.
    """
    authority = _scope_authority(scope)
    if authority is None:
        return False
    host, _ = authority
    if _is_ip_literal(host):
        return True
    return host in _trusted_hostnames(
        trusted_hosts=trusted_hosts,
        public_urls=public_urls,
        vite_dev_url=vite_dev_url,
    )


def scope_origin_is_trusted(
    scope: Scope,
    *,
    trusted_origins: str | Iterable[str] | None = None,
    public_urls: str | Iterable[str] | None = None,
    vite_dev_url: str | None = None,
    required: bool = False,
) -> bool:
    """Validate an Origin against the exact request or configured origins.

    A missing Origin is accepted only when ``required`` is false.  ``null``,
    malformed, multi-value, and foreign origins always fail.
    """
    values = _headers(scope, "origin")
    if not values:
        return not required
    if len(values) != 1:
        return False
    origin = _parse_origin(values[0])
    if origin is None:
        return False
    if origin == _scope_origin(scope):
        return True
    return origin in _trusted_origin_values(
        trusted_origins=trusted_origins,
        public_urls=public_urls,
        vite_dev_url=vite_dev_url,
    )


def is_secure_or_loopback(scope: Scope) -> bool:
    """True for HTTPS/WSS or a direct loopback-client-to-loopback-Host request.

    Requiring both loopback endpoints avoids treating a public request forwarded
    by a same-host reverse proxy as local merely because the proxy's peer socket
    is ``127.0.0.1``.
    """
    if str(scope.get("scheme", "")).lower() in {"https", "wss"}:
        return True
    authority = _scope_authority(scope)
    if authority is None:
        return False
    request_host, _ = authority
    try:
        host_is_loopback = ipaddress.ip_address(request_host).is_loopback
    except ValueError:
        host_is_loopback = request_host == "localhost"
    if not host_is_loopback:
        return False
    client = scope.get("client")
    client_host = str(client[0]) if isinstance(client, (tuple, list)) and client else ""
    try:
        return ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        return client_host == "localhost"


def build_set_cookie_header(token: str, secure: bool) -> str:
    """Build the process-session cookie without importing a web framework."""
    if not token or not _TOKEN_RE.fullmatch(token):
        raise ValueError("session token must be a non-empty URL-safe value")
    cookie = SimpleCookie()
    cookie[COOKIE_NAME] = token
    morsel = cookie[COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Strict"
    if secure:
        morsel["secure"] = True
    return morsel.OutputString()


def append_session_cookie(response: Any, token: str, secure: bool) -> Any:
    """Append a session cookie to a Starlette-like response and return it."""
    value = build_set_cookie_header(token, secure)
    headers = getattr(response, "headers", None)
    append = getattr(headers, "append", None)
    if callable(append):
        append("set-cookie", value)
        return response
    raw_headers = getattr(response, "raw_headers", None)
    if isinstance(raw_headers, list):
        raw_headers.append((b"set-cookie", value.encode("latin-1")))
        return response
    raise TypeError("response does not expose appendable headers")


def _presented_bearer(scope: Scope) -> tuple[bool, str | None]:
    values = _headers(scope, "authorization")
    if not values:
        return False, None
    if len(values) != 1:
        return True, None
    scheme, separator, token = values[0].partition(" ")
    if separator and scheme.lower() == "bearer" and token.strip():
        return True, token.strip()
    return True, None


def _presented_session_cookie(scope: Scope) -> str | None:
    found: list[str] = []
    for header in _headers(scope, "cookie"):
        try:
            cookie = SimpleCookie()
            cookie.load(header)
        except (CookieError, ValueError):
            return None
        morsel = cookie.get(COOKIE_NAME)
        if morsel is not None and morsel.value:
            found.append(morsel.value)
    if not found or len(set(found)) != 1:
        return None
    return found[0]


def _default_control_key_validator(token: str) -> bool:
    try:
        from jarvis.core.control_key import verify_control_key

        return bool(verify_control_key(token))
    except Exception:
        return False


def _default_session_validator(token: str) -> bool:
    try:
        from jarvis.ui.web.missions_auth import validate_token

        return bool(validate_token(token))
    except Exception:
        return False


def credentials_valid(scope: Scope) -> bool:
    """Validate the credential carried by ``scope`` without Host/Origin checks.

    This is the route-level defense-in-depth helper for tool-capable sockets.
    A Bearer may be either the persistent control key or a registered ephemeral
    UI token.  Cookie credentials are ephemeral UI tokens.  Supplying any
    malformed Authorization header is authoritative and prevents cookie
    fallback.
    """
    bearer_present, bearer = _presented_bearer(scope)
    if bearer_present:
        if bearer is None:
            return False
        if _default_control_key_validator(bearer):
            return True
        return _default_session_validator(bearer)

    token = _presented_session_cookie(scope)
    if token is None:
        return False
    return _default_session_validator(token)


async def _reject(
    scope: Scope,
    send: Send,
    *,
    status_code: int,
    detail: str,
    ws_code: int,
    authenticate: bool = False,
) -> None:
    if scope.get("type") == "websocket":
        await send({"type": "websocket.close", "code": ws_code, "reason": detail})
        return
    body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    if authenticate:
        headers.append((b"www-authenticate", b"Bearer"))
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


async def reject_host(scope: Scope, send: Send) -> None:
    """Reject an untrusted Host for HTTP or WebSocket before downstream code."""
    await _reject(
        scope,
        send,
        status_code=400,
        detail="Untrusted Host header.",
        ws_code=4403,
    )


async def reject_origin(scope: Scope, send: Send) -> None:
    """Reject a foreign, null, malformed, or required-but-missing Origin."""
    await _reject(
        scope,
        send,
        status_code=403,
        detail="Untrusted Origin header.",
        ws_code=4403,
    )


async def reject_unauthorized(scope: Scope, send: Send) -> None:
    """Reject a request that lacks a valid Jarvis credential."""
    await _reject(
        scope,
        send,
        status_code=401,
        detail="Invalid or missing Jarvis credential.",
        ws_code=4401,
        authenticate=True,
    )


def _is_static_request(path: str, method: str) -> bool:
    if method not in _SAFE_HTTP_METHODS:
        return False
    protected_prefixes = ("/api", "/ws", "/internal")
    return not any(path == prefix or path.startswith(f"{prefix}/") for prefix in protected_prefixes)


def _is_conductor_hook(path: str, method: str) -> bool:
    prefix = "/api/conductor/hooks/"
    if method != "POST" or not path.startswith(prefix):
        return False
    token = path[len(prefix) :]
    return bool(token) and "/" not in token


def _external_http_auth(path: str, method: str) -> bool:
    return (
        (path == "/api/telephony/voice" and method == "POST")
        or (path == "/api/marketplace/oauth/callback" and method == "GET")
        or _is_conductor_hook(path, method)
    )


def _http_auth_exception(path: str, method: str) -> bool:
    return (
        _is_static_request(path, method)
        or (path == "/api/health" and method == "GET")
        or (path == "/api/ui/shell-painted" and method == "POST")
        # First-boot contract: onboarding must answer before any credential
        # exists (headless fresh install has no token to present). Parity with
        # the serve-first bootstrap, which serves /api/onboarding/* unauthenticated
        # via jarvis.setup.onboarding_fastpath; the routes carry setup state only.
        or path.startswith("/api/onboarding/")
        or _external_http_auth(path, method)
    )


def _mission_inner_auth_socket(path: str) -> bool:
    if path == "/api/missions/ws":
        return True
    prefix = "/api/missions/pty/"
    worker_id = path[len(prefix) :] if path.startswith(prefix) else ""
    return bool(worker_id) and "/" not in worker_id


class SurfaceSecurity:
    """Pure-ASGI outer security boundary for every Jarvis HTTP/WS surface."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        vite_dev_url: str | None = None,
        public_urls: str | Iterable[str] | None = None,
        trusted_hosts: str | Iterable[str] | None = None,
        trusted_origins: str | Iterable[str] | None = None,
        bootstrap_tokens: tuple[str, ...] = (),
        control_key_validator: CredentialValidator | None = None,
        session_validator: CredentialValidator | None = None,
    ) -> None:
        self.app = app
        self._trusted_hosts = _trusted_hostnames(
            trusted_hosts=trusted_hosts,
            public_urls=public_urls,
            vite_dev_url=vite_dev_url,
        )
        self._trusted_origins = _trusted_origin_values(
            trusted_origins=trusted_origins,
            public_urls=public_urls,
            vite_dev_url=vite_dev_url,
        )
        # Bootstrap credentials are deliberately separate from authenticated
        # sessions: they are accepted only by POST /api/ui/session, never as a
        # Bearer token or cookie on a protected API/WebSocket.
        _register_bootstrap_tokens(bootstrap_tokens)
        self._local_session_tokens: set[str] = set()
        self._control_key_validator = control_key_validator or _default_control_key_validator
        self._session_validator = session_validator or _default_session_validator

    def host_is_trusted(self, scope: Scope) -> bool:
        authority = _scope_authority(scope)
        if authority is None:
            return False
        host, _ = authority
        return _is_ip_literal(host) or host in self._trusted_hosts

    def origin_is_trusted(self, scope: Scope, *, required: bool = False) -> bool:
        values = _headers(scope, "origin")
        if not values:
            return not required
        if len(values) != 1:
            return False
        origin = _parse_origin(values[0])
        if origin is None:
            return False
        return origin == _scope_origin(scope) or origin in self._trusted_origins

    def _authenticate(self, scope: Scope) -> AuthKind | None:
        bearer_present, bearer = _presented_bearer(scope)
        if bearer_present:
            if bearer is None:
                return None
            if bearer in self._local_session_tokens:
                return "session"
            try:
                if self._control_key_validator(bearer):
                    return "control"
            except Exception:
                return None
            try:
                return "session" if self._session_validator(bearer) else None
            except Exception:
                return None
        token = _presented_session_cookie(scope)
        if token is None:
            return None
        if token in self._local_session_tokens:
            return "session"
        try:
            return "session" if self._session_validator(token) else None
        except Exception:
            return None

    def _register_local_tokens_if_available(self) -> None:
        """Mirror bootstrap-issued tokens once the normal token store is loaded.

        Merely checking a bootstrap token must not import FastAPI or the token
        store.  Once the full app has naturally imported ``missions_auth``, this
        best-effort sync lets its route-local WebSocket guards recognize tokens
        issued while only the dependency-light holding app existed.
        """
        module = sys.modules.get("jarvis.ui.web.missions_auth")
        register = getattr(module, "register_token", None) if module is not None else None
        if not callable(register):
            return
        for token in tuple(self._local_session_tokens):
            try:
                register(token)
            except Exception:  # noqa: S112 - best-effort sync must not block serving
                continue

    def sync_local_tokens(self) -> None:
        """Publish issued bootstrap sessions once the normal store is loaded."""
        self._register_local_tokens_if_available()

    def _consume_session_token(self, token: str) -> None:
        """Invalidate a one-time token after it has minted an HttpOnly session."""
        self._local_session_tokens.discard(token)
        module = sys.modules.get("jarvis.ui.web.missions_auth")
        revoke = getattr(module, "revoke_token", None) if module is not None else None
        if callable(revoke):
            try:
                revoke(token)
            except Exception:  # noqa: S110 - invalidation is best effort here
                pass

    def _issued_session_token_valid(self, token: str) -> bool:
        if token in self._local_session_tokens:
            return True
        try:
            return bool(self._session_validator(token))
        except Exception:
            return False

    async def _read_session_request(self, receive: Receive) -> dict[str, Any] | None:
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            kind = message.get("type")
            if kind == "http.disconnect":
                return None
            if kind != "http.request":
                return None
            body = bytes(message.get("body", b""))
            size += len(body)
            if size > 16 * 1024:
                return None
            chunks.append(body)
            if not message.get("more_body", False):
                break
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    async def _handle_session_request(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if not is_secure_or_loopback(scope):
            await _reject(
                scope,
                send,
                status_code=403,
                detail="UI session bootstrap requires HTTPS or direct loopback.",
                ws_code=4403,
            )
            return

        payload = await self._read_session_request(receive)
        if payload is None:
            await _reject(
                scope,
                send,
                status_code=400,
                detail="Invalid UI session request.",
                ws_code=4400,
            )
            return

        control_value = payload.get("control_key")
        session_value = payload.get("session_token")
        control_token = control_value.strip() if isinstance(control_value, str) else ""
        session_token = session_value.strip() if isinstance(session_value, str) else ""
        if bool(control_token) == bool(session_token):
            await _reject(
                scope,
                send,
                status_code=400,
                detail="Provide exactly one UI session credential.",
                ws_code=4400,
            )
            return

        valid = False
        bootstrap_session = False
        if control_token:
            try:
                valid = bool(self._control_key_validator(control_token))
            except Exception:
                valid = False
        elif session_token:
            bootstrap_session = _bootstrap_token_is_valid(session_token)
            valid = bootstrap_session or self._issued_session_token_valid(session_token)
        if not valid:
            await reject_unauthorized(scope, send)
            return
        if session_token:
            if bootstrap_session and not _consume_bootstrap_token(session_token):
                # A concurrent exchange already consumed the one-time token.
                await reject_unauthorized(scope, send)
                return
            self._consume_session_token(session_token)

        issued = secrets.token_urlsafe(32)
        self._local_session_tokens.add(issued)
        self._register_local_tokens_if_available()
        secure = str(scope.get("scheme", "")).lower() in {"https", "wss"}
        cookie = build_set_cookie_header(issued, secure)
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [
                    (b"cache-control", b"no-store"),
                    (b"content-length", b"0"),
                    (b"set-cookie", cookie.encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope.get("type")
        if kind not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        if not self.host_is_trusted(scope):
            await reject_host(scope, send)
            return

        path = str(scope.get("path", "") or "")
        method = str(scope.get("method", "GET") or "GET").upper()
        self._register_local_tokens_if_available()

        # A supplied Origin is never advisory: malformed, null, or foreign
        # values fail even on otherwise-public static and health requests.
        if not self.origin_is_trusted(scope):
            await reject_origin(scope, send)
            return

        if kind == "websocket" and path == "/api/telephony/media":
            await self.app(scope, receive, send)
            return

        if kind == "http":
            # Browser preflights do not carry cookies or Authorization. Once
            # their Origin is known-good, let the inner CORS middleware answer
            # them without weakening the actual request's credential check.
            if method == "OPTIONS":
                if not self.origin_is_trusted(scope, required=True):
                    await reject_origin(scope, send)
                    return
                await self.app(scope, receive, send)
                return
            if path == "/api/ui/session" and method == "POST":
                await self._handle_session_request(scope, receive, send)
                return
            if path == "/api/ui/shell-painted" and method == "POST":
                if not self.origin_is_trusted(scope, required=True):
                    await reject_origin(scope, send)
                    return
                await self.app(scope, receive, send)
                return
            if _http_auth_exception(path, method):
                await self.app(scope, receive, send)
                return

            auth_kind = self._authenticate(scope)
            if auth_kind is None:
                await reject_unauthorized(scope, send)
                return
            if auth_kind == "session" and method in _UNSAFE_HTTP_METHODS:
                if not self.origin_is_trusted(scope, required=True):
                    await reject_origin(scope, send)
                    return
            await self.app(scope, receive, send)
            return

        if _mission_inner_auth_socket(path):
            if not self.origin_is_trusted(scope, required=True):
                await reject_origin(scope, send)
                return
            await self.app(scope, receive, send)
            return

        auth_kind = self._authenticate(scope)
        if auth_kind is None:
            await reject_unauthorized(scope, send)
            return
        if auth_kind == "session" and not self.origin_is_trusted(scope, required=True):
            await reject_origin(scope, send)
            return
        await self.app(scope, receive, send)


__all__ = [
    "COOKIE_NAME",
    "SurfaceSecurity",
    "append_session_cookie",
    "build_set_cookie_header",
    "credentials_valid",
    "is_secure_or_loopback",
    "reject_host",
    "reject_origin",
    "reject_unauthorized",
    "scope_host_is_trusted",
    "scope_origin_is_trusted",
]
