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

from jarvis.core.http_guard import https_only_async

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


# The two frontmatter profiles a published skill can carry. ``jarvis`` is this
# app's own schema (triggers, risk policy, execution mode); ``portable`` is a
# plain Agent Skill written for the open ecosystem — the format `npx skills`
# and every SKILL.md-reading agent consumes. Anything else is treated as
# unknown and falls back to ``jarvis``, which is what every entry published
# before the split was.
SKILL_FLAVORS: frozenset[str] = frozenset({"jarvis", "portable"})

# Ceilings for the publisher-written compatibility list. It is free text from
# an auto-merged submission that lands in the store UI, so it is bounded here
# rather than trusted: a hundred entries of a thousand characters each is a
# layout attack, not a fact about the skill.
_MAX_COMPATIBLE_AGENTS = 8
_MAX_AGENT_NAME_LEN = 32


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
    #: Which frontmatter the SKILL.md carries (see ``SKILL_FLAVORS``). Absent
    #: on an index published by an older registry — the reader treats a missing
    #: value exactly like ``"jarvis"``.
    flavor: str | None = None
    #: Agents the publisher states the skill works in ("Claude Code", "Cursor",
    #: …). Display only: nothing in the app branches on it.
    compatible_agents: list[str] = Field(default_factory=list)

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

    @field_validator("flavor")
    @classmethod
    def _known_flavor(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        if cleaned in SKILL_FLAVORS:
            return cleaned
        # A newer registry may invent a third profile. Forgetting the word is
        # right: the entry stays browsable and installable under the default,
        # instead of the whole index failing over one unknown string.
        logger.warning("community index: unknown skill flavor %r", value)
        return None

    @field_validator("compatible_agents", mode="before")
    @classmethod
    def _clean_agents(cls, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            name = " ".join(item.split())[:_MAX_AGENT_NAME_LEN].strip()
            if name and name not in cleaned:
                cleaned.append(name)
            if len(cleaned) >= _MAX_COMPATIBLE_AGENTS:
                break
        return cleaned

    @property
    def is_portable(self) -> bool:
        """True when the SKILL.md is a plain Agent Skill, not a Jarvis one."""
        return self.flavor == "portable"


class CommunityWallpaperEntry(_Tolerant):
    """One installable wallpaper.

    A wallpaper is the simplest thing the registry publishes: a picture and a
    name. It carries no code, no credentials, and no manifest — installing one
    downloads an image, re-encodes it, and drops it next to the owner's own
    uploads, so it can never be more dangerous than a file the owner dragged
    in themselves.
    """

    name: str
    title: str | None = None
    description: str = ""
    publisher: str | None = None
    version: str | None = None
    published_at: str | None = None
    categories: list[str] = Field(default_factory=list)
    source_url: str | None = None
    license: str | None = None
    #: Full-size picture. This is what the published registry actually emits;
    #: ``raw_url`` is accepted alongside it so the field name the other two
    #: kinds use keeps working for a hand-written entry.
    image_url: str | None = None
    raw_url: str | None = None
    #: Small preview, for showing the picture before anything is installed.
    thumb_url: str | None = None
    width: int | None = None
    height: int | None = None
    # Light/dark hint from the publisher. Only a hint: the server re-derives
    # the theme from the actual pixels, exactly as it does for an upload, so a
    # wrong or absent value costs nothing.
    theme: str | None = None

    @field_validator("image_url", "raw_url", "thumb_url")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        # Same reasoning as the skill raw_url above: the backend fetches this
        # server-side, so anything but https is an SSRF vector.
        if value is not None and not value.lower().startswith("https://"):
            logger.warning("community index: dropping non-https wallpaper url %r", value)
            return None
        return value

    @property
    def download_url(self) -> str | None:
        """The picture to fetch, whichever field the publisher used."""
        return self.image_url or self.raw_url


class CommunityIndex(_Tolerant):
    revision: int = 0
    generated_at: str | None = None
    plugins: list[CommunityPluginEntry] = Field(default_factory=list)
    skills: list[CommunitySkillEntry] = Field(default_factory=list)
    wallpapers: list[CommunityWallpaperEntry] = Field(default_factory=list)

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

    @field_validator("wallpapers", mode="after")
    @classmethod
    def _drop_unsafe_wallpaper_names(
        cls, value: list[CommunityWallpaperEntry]
    ) -> list[CommunityWallpaperEntry]:
        # A wallpaper name never becomes a path (the store keys by a random
        # id), but it IS the identity the install-by-name route resolves and
        # the "already installed" check compares, so it obeys the same shape
        # as every other entry rather than being a free-form string.
        kept: list[CommunityWallpaperEntry] = []
        for entry in value:
            if _SKILL_NAME_RE.fullmatch(entry.name) and ".." not in entry.name:
                kept.append(entry)
            else:
                logger.warning(
                    "community index: dropping wallpaper with unsafe name %r", entry.name
                )
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
        return None  # no cache yet — the caller fetches the index
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
            # The index URL is configuration, but its redirects are not: the
            # host on the other end picks them, so the chain stays on https.
            async with httpx.AsyncClient(
                timeout=_FETCH_TIMEOUT,
                follow_redirects=True,
                transport=transport,
                **https_only_async(),
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
    "SKILL_FLAVORS",
    "CommunityIndex",
    "CommunityPluginEntry",
    "CommunitySkillEntry",
    "cached_index",
    "get_index",
    "index_url",
]
