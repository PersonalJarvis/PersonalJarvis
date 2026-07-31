"""Guided Supabase link flow for the UltraWiki storage slot.

The problem this solves: a Postgres store needs a connection string, and
Supabase — the most common free hosted Postgres — spreads the pieces of that
string across three dashboard pages. Asking a user to hand-assemble
``postgresql://postgres.<ref>:<password>@<region>.pooler.supabase.com:6543/postgres``
is exactly the kind of out-of-app setup step the in-app-recoverable mandate
(§3, AP-23) exists to prevent, and the region prefix in that hostname is not
derivable from the project ref — guessing it produces a string that fails to
connect for reasons the user cannot see.

So Jarvis asks Supabase instead. With a personal access token the user creates
in their already-signed-in browser, this module:

1. lists the user's projects (``GET /v1/projects``),
2. reads the project's REAL pooler host/port/user
   (``GET /v1/projects/{ref}/config/database/pooler``), falling back to the
   documented direct-connection shape when that config is unavailable,
3. assembles the connection string once the user supplies the database
   password — the one piece Supabase deliberately never returns over its API.

Everything is plain httpx (no SDK), so it behaves identically on Windows,
macOS and a headless Linux box. Nothing here writes a secret: the caller saves
the assembled URI through :func:`jarvis.core.config.set_secret` under
``ultrawiki_db_url`` (AP-12), and the token itself rides the same chain under
``supabase_access_token``.

Honesty rule for every function below: no exception escapes with a credential
in its message, and a failure says which step failed in plain words.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

__all__ = [
    "SUPABASE_API_BASE",
    "SUPABASE_TOKENS_URL",
    "SupabaseLinkError",
    "SupabaseProject",
    "PoolerEndpoint",
    "build_connection_string",
    "direct_endpoint_for",
    "list_projects",
    "pooler_endpoint",
    "resolve_endpoint",
]

SUPABASE_API_BASE = "https://api.supabase.com"

#: Where the user creates the personal access token. Opened in their browser
#: by the Connect button — they are already signed in there, which is what
#: makes this a browser login rather than a copy-paste chore.
SUPABASE_TOKENS_URL = "https://supabase.com/dashboard/account/tokens"

#: The Management API is a control-plane API: fast, but a cold project or a
#: throttled account can take a few seconds.
_TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0)

#: Transaction mode (6543) is the right default for a long-lived application
#: pool; session mode (5432) exists for tools that need session state.
_POOL_MODE_PORTS: dict[str, int] = {"transaction": 6543, "session": 5432}


class SupabaseLinkError(RuntimeError):
    """A Management-API step failed. Message is safe to show the user."""


@dataclass(frozen=True, slots=True)
class SupabaseProject:
    """One project row, trimmed to what the picker needs."""

    ref: str
    name: str
    region: str
    status: str

    def as_dict(self) -> dict[str, str]:
        return {
            "ref": self.ref,
            "name": self.name,
            "region": self.region,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class PoolerEndpoint:
    """Everything but the password, as Supabase itself reports it."""

    host: str
    port: int
    user: str
    database: str
    #: "transaction" | "session" | "direct" — what the user is connecting to.
    mode: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "database": self.database,
            "mode": self.mode,
        }


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


async def _get_json(
    token: str,
    path: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Any:
    """GET one Management-API path, mapping every failure to a plain message.

    HTTP 401/403 is by far the most common real failure (an expired or
    mistyped token), so it gets its own sentence instead of a bare status code.
    """
    url = f"{SUPABASE_API_BASE}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
            response = await client.get(url, headers=_headers(token))
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise SupabaseLinkError(
                "Supabase rejected the access token. Create a fresh personal "
                "access token and connect again."
            ) from exc
        if status == 404:
            raise SupabaseLinkError(
                "Supabase has no such project, or this token cannot see it."
            ) from exc
        raise SupabaseLinkError(
            f"Supabase answered HTTP {status} for this request."
        ) from exc
    except httpx.HTTPError as exc:
        raise SupabaseLinkError(
            f"Could not reach the Supabase API ({type(exc).__name__}). "
            "Check this machine's internet connection."
        ) from exc
    except ValueError as exc:  # body was not JSON
        raise SupabaseLinkError(
            "The Supabase API returned something that is not valid JSON."
        ) from exc


async def list_projects(
    token: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[SupabaseProject]:
    """Every project the token can see, newest-usable first.

    Active projects sort ahead of paused/inactive ones so the picker's first
    entry is a project that can actually accept a connection.
    """
    if not token.strip():
        raise SupabaseLinkError("No Supabase access token is saved yet.")
    data = await _get_json(token, "/v1/projects", transport=transport)
    if not isinstance(data, list):
        raise SupabaseLinkError(
            "The Supabase project list had an unexpected shape."
        )
    projects = [
        SupabaseProject(
            ref=str(row.get("id") or row.get("ref") or "").strip(),
            name=str(row.get("name") or "").strip() or "(unnamed project)",
            region=str(row.get("region") or "").strip(),
            status=str(row.get("status") or "").strip(),
        )
        for row in data
        if isinstance(row, dict)
    ]
    projects = [p for p in projects if p.ref]
    projects.sort(key=lambda p: (p.status.upper() != "ACTIVE_HEALTHY", p.name.lower()))
    return projects


def direct_endpoint_for(ref: str) -> PoolerEndpoint:
    """The documented direct-connection shape, derivable from the ref alone.

    Used when the pooler config is unavailable. Worth knowing before you rely
    on it: Supabase serves the direct host over IPv6 only unless the project
    has the IPv4 add-on, so on an IPv4-only network the pooler is the endpoint
    that actually connects. The caller says which one it used.
    """
    return PoolerEndpoint(
        host=f"db.{ref}.supabase.co",
        port=5432,
        user="postgres",
        database="postgres",
        mode="direct",
    )


def _endpoint_from_row(row: dict[str, Any], ref: str, mode: str) -> PoolerEndpoint | None:
    host = str(row.get("db_host") or "").strip()
    if not host:
        return None
    try:
        port = int(row.get("db_port") or _POOL_MODE_PORTS.get(mode, 6543))
    except (TypeError, ValueError):
        port = _POOL_MODE_PORTS.get(mode, 6543)
    return PoolerEndpoint(
        host=host,
        # A pooler row reports the port of ITS own mode; when the user asked
        # for the other mode the host is identical and only the port differs
        # (documented Supavisor behaviour), so honour the requested mode.
        port=_POOL_MODE_PORTS.get(mode, port),
        user=str(row.get("db_user") or f"postgres.{ref}").strip(),
        database=str(row.get("db_name") or "postgres").strip(),
        mode=mode,
    )


async def pooler_endpoint(
    token: str,
    ref: str,
    *,
    mode: str = "transaction",
    transport: httpx.AsyncBaseTransport | None = None,
) -> PoolerEndpoint | None:
    """The project's real pooler endpoint, or ``None`` when unavailable.

    ``None`` is not an error: some projects and some token scopes simply do not
    expose this config, and the caller then falls back to the direct endpoint.
    The Management API has returned both a bare object and a list of per-mode
    rows over its lifetime, so both shapes are accepted.
    """
    if mode not in _POOL_MODE_PORTS:
        mode = "transaction"
    try:
        data = await _get_json(
            token, f"/v1/projects/{ref}/config/database/pooler", transport=transport
        )
    except SupabaseLinkError:
        # An unreadable pooler config must not sink the whole link flow.
        log.debug("supabase pooler config unavailable for %s", ref, exc_info=True)
        return None
    rows: list[dict[str, Any]]
    if isinstance(data, dict):
        rows = [data]
    elif isinstance(data, list):
        rows = [row for row in data if isinstance(row, dict)]
    else:
        return None
    for row in rows:
        endpoint = _endpoint_from_row(row, ref, mode)
        if endpoint is not None:
            return endpoint
    return None


async def resolve_endpoint(
    token: str,
    ref: str,
    *,
    mode: str = "transaction",
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[PoolerEndpoint, str]:
    """The endpoint to connect with, plus a plain note about which one it is.

    Returns the pooler endpoint when Supabase reports one, otherwise the direct
    endpoint with a note naming the IPv4 caveat — so a later connection failure
    is diagnosable from what the UI already told the user.
    """
    endpoint = await pooler_endpoint(token, ref, mode=mode, transport=transport)
    if endpoint is not None:
        return endpoint, (
            f"Using the Supabase {endpoint.mode} pooler at {endpoint.host}."
        )
    fallback = direct_endpoint_for(ref)
    return fallback, (
        "Supabase did not report a pooler for this project, so the direct "
        f"connection {fallback.host} is used. Note that the direct host is "
        "IPv6-only unless your project has the IPv4 add-on."
    )


def build_connection_string(endpoint: PoolerEndpoint, password: str) -> str:
    """Assemble the Postgres URI. Percent-encodes user and password.

    Supabase passwords may legally contain ``@``, ``:``, ``/`` and ``#``, every
    one of which changes how a URI parses. Encoding both credential parts is
    what keeps "my password has a slash in it" from turning into an
    unresolvable-host error three layers down.
    """
    if not password:
        raise SupabaseLinkError("The database password is required.")
    user = quote(endpoint.user, safe="")
    secret = quote(password, safe="")
    database = endpoint.database or "postgres"
    return (
        f"postgresql://{user}:{secret}@{endpoint.host}:{endpoint.port}/{database}"
        "?sslmode=require"
    )
