"""Fetch and cache the server's OpenAPI document, cross-platform & offline-safe.

The cache lives under platformdirs' user cache dir. Freshness is purely
TTL-based: within the TTL the cached spec is served from disk with no network
call; once it expires the next invocation re-fetches the full document. The
server exposes no cheap live-version probe (``/api/openapi.json`` carries no
ETag and there is no lightweight version endpoint), so we deliberately do NOT
attempt conditional/ETag revalidation -- comparing the cached spec's version
against the cached meta's version would be a no-op (they are written together),
and a real drift check would need a live probe we do not have. A newly added
endpoint becomes visible after the TTL lapses or immediately via ``clear_cache``
("jarvisctl refresh"). When the server is unreachable we degrade to the last
cached spec; with no cache at all we return None so the caller can skip the
dynamic command tree without crashing.
"""
from __future__ import annotations

import json
import time
from typing import Any

from jarvis.cli_ctl import paths
from jarvis.cli_ctl.client import ApiError, JarvisClient

OPENAPI_PATH = "/api/openapi.json"
DEFAULT_TTL = 24 * 3600


def _read_cache() -> tuple[dict[str, Any] | None, dict[str, Any]]:
    spec_p, meta_p = paths.openapi_cache_file(), paths.openapi_meta_file()
    spec = meta = None
    if spec_p.exists():
        try:
            spec = json.loads(spec_p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            spec = None
    if meta_p.exists():
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            meta = None
    return spec, (meta or {})


def _write_cache(spec: dict[str, Any]) -> None:
    paths.openapi_cache_file().write_text(
        json.dumps(spec, ensure_ascii=False), encoding="utf-8"
    )
    paths.openapi_meta_file().write_text(
        json.dumps({"fetched_at": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


def clear_cache() -> None:
    for p in (paths.openapi_cache_file(), paths.openapi_meta_file()):
        if p.exists():
            p.unlink()


def load_spec(
    client: JarvisClient, *, ttl_seconds: int = DEFAULT_TTL
) -> dict[str, Any] | None:
    spec, meta = _read_cache()
    # Freshness is TTL-only -- see the module docstring for why there is no
    # version/ETag-based revalidation. A negative age means the wall clock
    # moved backwards (VM restore, container clock drift); treat that as
    # stale rather than "fresh forever".
    age = time.time() - float(meta.get("fetched_at", 0)) if meta else None
    fresh = spec is not None and age is not None and 0 <= age < ttl_seconds
    if fresh:
        return spec
    # Stale or missing: try to (re)fetch the full document.
    try:
        fetched = client.request("GET", OPENAPI_PATH)
    except ApiError:
        return spec  # unreachable -> stale cache (may be None)
    if isinstance(fetched, dict):
        _write_cache(fetched)
        return fetched
    return spec
