"""Fetch a server-scoped OpenAPI schema, with same-server offline fallback.

Within the TTL a matching cache is used without network access. Expired entries
are refreshed from the selected server; only a transport failure may fall back
to that server's previous schema. A different origin/base path or an unbound cache
can never supply its command tree. ``jarvisctl refresh`` clears all entries.

Each entry stores its spec and server metadata atomically in one file. URL
normalization drops userinfo, query strings and fragments; no control key or
credential-bearing URL is written to the cache or diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from jarvis.cli_ctl import paths
from jarvis.cli_ctl.client import ApiError, JarvisClient

OPENAPI_PATH = "/api/openapi.json"
DEFAULT_TTL = 24 * 3600
log = logging.getLogger(__name__)


def server_origin(base_url: str) -> str:
    """Canonical HTTP origin without credentials or URL parameters."""
    try:
        parsed = urlsplit(base_url)
        scheme, hostname, port = parsed.scheme.lower(), parsed.hostname, parsed.port
        if scheme not in {"http", "https"} or not hostname:
            raise ValueError("unsupported server URL")
    except ValueError:
        # urllib errors may include user input; do not propagate their text.
        raise ValueError("invalid server origin") from None
    host = hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if scheme == "http" else 443
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{scheme}://{host}{suffix}"


def _cache_path(base_url: str) -> Path:
    identity = server_origin(base_url) + _base_path(base_url)
    fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return paths.cache_dir() / f"openapi-{fingerprint}.json"


def _base_path(base_url: str) -> str:
    """Match httpx's normalized, slash-terminated request prefix exactly."""
    try:
        raw_path = httpx.URL(base_url).copy_with(query=None, fragment=None).raw_path
    except (ValueError, httpx.InvalidURL):
        raise ValueError("invalid server base path") from None
    # Do not strip multiple trailing slashes or unquote escaped path separators:
    # httpx preserves both in actual requests, so different mounts stay isolated.
    if not raw_path.endswith(b"/"):
        raw_path += b"/"
    return raw_path.decode("ascii")


def _valid_spec(value: Any) -> bool:
    """Reject login/error payloads before they become a dynamic command tree."""
    return (
        isinstance(value, dict)
        and isinstance(value.get("openapi"), str)
        and value["openapi"].startswith("3.")
        and isinstance(value.get("info"), dict)
        and isinstance(value.get("paths"), dict)
    )


def _read_cache(*, base_url: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        entry = json.loads(_cache_path(base_url).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None, {}
    if (
        not isinstance(entry, dict)
        or entry.get("origin") != server_origin(base_url)
        or entry.get("base_path") != _base_path(base_url)
    ):
        return None, {}
    spec = entry.get("spec")
    if not _valid_spec(spec):
        return None, {}
    return spec, {"origin": entry["origin"], "fetched_at": entry.get("fetched_at")}


def _write_cache(spec: dict[str, Any], *, base_url: str) -> None:
    if not _valid_spec(spec):
        raise ValueError("invalid OpenAPI schema")
    path = _cache_path(base_url)
    entry = {
        "origin": server_origin(base_url),
        "base_path": _base_path(base_url),
        "fetched_at": time.time(),
        "spec": spec,
    }
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="openapi-",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(entry, stream, ensure_ascii=False)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def clear_cache() -> None:
    # Legacy unbound entries are never loaded, but refresh still removes them.
    entries = [paths.openapi_cache_file(), paths.openapi_meta_file()]
    entries.extend(paths.cache_dir().glob("openapi-*.json"))
    for path in entries:
        path.unlink(missing_ok=True)


def load_spec(client: JarvisClient, *, ttl_seconds: int = DEFAULT_TTL) -> dict[str, Any] | None:
    spec, meta = _read_cache(base_url=client.base_url)
    try:
        age = time.time() - float(meta.get("fetched_at", 0)) if meta else None
    except (TypeError, ValueError):
        age = None
    # A backward wall-clock jump must not keep an entry fresh forever.
    fresh = spec is not None and age is not None and 0 <= age < ttl_seconds
    if fresh:
        return spec
    # Stale or missing: try to (re)fetch the full document.
    try:
        fetched = client.request("GET", OPENAPI_PATH)
    except ApiError as exc:
        # An authentication refusal is not offline access to private metadata.
        return spec if exc.status_code is None else None
    if _valid_spec(fetched):
        try:
            _write_cache(fetched, base_url=client.base_url)
        except OSError as exc:
            # A read-only cache directory must not hide a successfully fetched API.
            log.debug("OpenAPI cache write unavailable (%s)", type(exc).__name__)
        return fetched
    # A reachable login page, redirect body or malformed schema is not an
    # offline condition. Never hide it behind stale commands from an old build.
    return None
