"""Fetch and cache the community marketplace index.

The registry repo (PersonalJarvis/marketplace) compiles every published
plugin and skill into one static ``index.json`` served from GitHub Pages.
This module is the app's only reader of that feed: fetch with short
timeouts, validate into typed models, and persist the result under
``data/marketplace_index.json`` so browsing keeps working offline and on a
headless box (same doctrine as the seed catalogs).

Never called on the boot critical path (contract §7) — the Plugins view
triggers a TTL-gated refresh when it opens, plus an explicit refresh button.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

# Repo-root data/ — same resolution as jarvis/marketplace/catalog_data.py.
_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "marketplace_index.json"

_FETCH_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=10.0)
# How long a fetched index stays fresh before a view-open refetches it. The
# refresh endpoint bypasses this; publishing latency is CI-bound (~minutes),
# so a quarter hour keeps browsing snappy without hammering the host.
_CACHE_TTL_SECONDS = 900.0

# One in-process fetch at a time — a double view-open must not race two
# downloads into the same cache file.
_fetch_lock = asyncio.Lock()


class _Tolerant(BaseModel):
    """``extra="allow"``: the index is produced by a NEWER registry than this
    client may be — unknown fields must never break browsing (BUG-016 class
    is catalog files rejected wholesale over one new field)."""

    model_config = ConfigDict(extra="allow")


class CommunityPluginEntry(_Tolerant):
    name: str
    publisher: str | None = None
    version: str | None = None
    published_at: str | None = None
    source_url: str | None = None
    # The raw Agent Plugins v1.0.0 manifests, embedded verbatim. Conversion
    # to a PluginSpec happens at INSTALL time (agent_plugins_loader), so a
    # malformed community entry degrades to one uninstallable card instead of
    # taking the whole index down.
    plugin_json: dict[str, Any]
    mcp_json: dict[str, Any] | None = None
    usage_card: str | None = None


# The Agent Plugins name rules (same as agent_plugins_loader.validate_spec_name
# and the registry CI). A skill's name later becomes a DIRECTORY under the
# user's skills folder, so this is a security boundary, not cosmetics: a name
# like "../../evil" or "C:/anywhere" must never survive index validation.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")


class CommunitySkillEntry(_Tolerant):
    name: str
    title: str | None = None
    description: str = ""
    publisher: str | None = None
    version: str | None = None
    published_at: str | None = None
    categories: list[str] = Field(default_factory=list)
    source_url: str | None = None
    # Direct download of the SKILL.md — consumed by the existing
    # /api/skills/catalog/install route.
    raw_url: str | None = None

    @field_validator("raw_url")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        # The backend fetches this URL server-side at install time. Anything
        # but https is an SSRF vector (metadata endpoints, LAN admin pages),
        # so a non-https URL degrades the entry to "Manual" instead of being
        # fetched. The finder re-checks before the actual download.
        if value is not None and not value.lower().startswith("https://"):
            logger.warning("community index: dropping non-https raw_url %r", value)
            return None
        return value


class CommunityIndex(_Tolerant):
    revision: int = 0
    generated_at: str | None = None
    plugins: list[CommunityPluginEntry] = Field(default_factory=list)
    skills: list[CommunitySkillEntry] = Field(default_factory=list)

    @field_validator("skills", mode="after")
    @classmethod
    def _drop_unsafe_skill_names(
        cls, value: list[CommunitySkillEntry]
    ) -> list[CommunitySkillEntry]:
        kept: list[CommunitySkillEntry] = []
        for entry in value:
            if _SKILL_NAME_RE.fullmatch(entry.name) and ".." not in entry.name:
                kept.append(entry)
            else:
                # One malicious or malformed entry must cost exactly itself,
                # never the whole index (that would be a delisting DoS).
                logger.warning("community index: dropping skill with unsafe name %r", entry.name)
        return kept


def index_url() -> str:
    """The configured index URL (empty string = community section disabled)."""
    from jarvis.core.config import load_config

    try:
        return str(load_config().marketplace.community_index_url).strip()
    except Exception:  # noqa: BLE001 - config trouble must not kill browsing
        logger.warning("community index: could not read config, using default")
        from jarvis.core.config import MarketplaceConfig

        return MarketplaceConfig().community_index_url


def _read_cache() -> tuple[float, CommunityIndex] | None:
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8-sig"))
        return float(raw["fetched_at"]), CommunityIndex.model_validate(raw["index"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, ValidationError) as exc:
        logger.warning("community index: unreadable cache (%s) — refetching", exc)
        return None


def _write_cache(index: CommunityIndex) -> None:
    """Atomic tmp+replace, like every other data/ writer in this repo."""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {"fetched_at": time.time(), "index": index.model_dump(mode="json")},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(_CACHE_PATH)
    except OSError as exc:
        # A read-only disk costs persistence, not the current browse.
        logger.warning("community index: cache write failed: %s", exc)


def cached_index() -> CommunityIndex | None:
    """The last fetched index regardless of age, or None if never fetched."""
    hit = _read_cache()
    return hit[1] if hit else None


async def get_index(
    *,
    force: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[CommunityIndex | None, str]:
    """Return ``(index, status)`` with status one of:

    - ``"disabled"``  — no index URL configured
    - ``"fresh"``     — served straight from a within-TTL cache
    - ``"fetched"``   — downloaded and cached just now
    - ``"stale"``     — network failed; serving the outdated cache honestly
    - ``"unavailable"`` — network failed and there is no cache at all
    """
    url = index_url()
    if not url:
        return None, "disabled"

    async with _fetch_lock:
        hit = _read_cache()
        if hit and not force and (time.time() - hit[0]) < _CACHE_TTL_SECONDS:
            return hit[1], "fresh"

        try:
            async with httpx.AsyncClient(
                timeout=_FETCH_TIMEOUT, follow_redirects=True, transport=transport
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                index = CommunityIndex.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning("community index: fetch failed (%s)", exc)
            if hit:
                return hit[1], "stale"
            return None, "unavailable"

        _write_cache(index)
        return index, "fetched"


__all__ = [
    "CommunityIndex",
    "CommunityPluginEntry",
    "CommunitySkillEntry",
    "cached_index",
    "get_index",
    "index_url",
]
