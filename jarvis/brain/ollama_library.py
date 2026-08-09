"""Browse the FULL public Ollama library from inside the app.

The curated shortlist in :mod:`jarvis.brain.ollama_pull` answers "what should
I run?". This module answers "what else exists?" — the user picks any model
from the public library without leaving the app or knowing the exact tag name
in advance.

Ollama publishes no JSON search API, and the registry does NOT expose the
Docker ``tags/list`` endpoint (404, verified 2026-08-09). Both surfaces here
therefore parse the public web pages:

- search:   ``https://ollama.com/search?q=…``           (name, blurb, badges)
- tag list: ``https://ollama.com/library/{name}/tags``  (tag, size, context)

Scraping is a liability, so the contract is honest degradation: any fetch or
parse failure answers ``{"models"/"tags": [], "error": "<English sentence>"}``
and the panel keeps its free-text pull field, which needs no ollama.com at
all. The pull itself never depends on this module — the local server's
``/api/pull`` remains the authority on whether a name exists, so a stale or
wrong search result still ends in a clear "not in the library" message rather
than a broken install.

Parsers are deliberately structure-light: they anchor on ``/library/{name}``
hrefs and plain-text markers ("GB", "context window", "Pulls"), not on the
page's CSS classes, so a Tailwind reshuffle upstream does not blank the panel.
Guard: ``tests/integration/test_ollama_library_live.py`` parses the LIVE pages
and fails loudly when ollama.com changes shape for real.
"""

from __future__ import annotations

import html as html_lib
import logging
import re
import time
from typing import Any

import httpx

from jarvis.brain.ollama_pull import (
    _is_installed,
    accelerator_gb,
    fit_verdict,
    installed_models,
    total_memory_gb,
)

log = logging.getLogger(__name__)

_LIBRARY_ROOT = "https://ollama.com"

#: Short enough that a dead network fails the panel fast, long enough for the
#: ~250 KB tags page of a large model family on a slow line.
_FETCH_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=10.0)

#: The library moves slowly (new models land daily, not per-second), and the
#: panel re-queries on every keystroke burst. Ten minutes keeps browsing snappy
#: without ever showing a week-old catalog.
_CACHE_TTL_SECONDS = 600.0

#: Search answers at most this many models. The page itself shows about this
#: many above the fold, and a narrower query beats scrolling page two.
_SEARCH_LIMIT = 25

#: Library model names as they appear in ``/library/{name}`` URLs. No slash on
#: purpose: community-namespaced models (``/{user}/{model}``) are out of scope
#: here, and the pattern doubles as the URL-injection guard for the tags fetch.
_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")

#: Capability badges the search page renders, in display order. "cloud" is
#: handled separately: it is a hosting fact, not a model capability.
_CAPABILITY_BADGES = ("vision", "tools", "thinking", "embedding")

_search_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_tags_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_get(cache: dict[str, tuple[float, dict[str, Any]]], key: str) -> dict[str, Any] | None:
    hit = cache.get(key)
    if hit and (time.monotonic() - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]
    return None


def _text(fragment: str) -> str:
    """Visible text of an HTML fragment: tags out, entities decoded, one space."""
    plain = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html_lib.unescape(plain)).strip()


async def _fetch_page(
    path: str, params: dict[str, str] | None = None
) -> tuple[str | None, str | None]:
    """``(html, error)`` for one ollama.com page — exactly one is non-None."""
    url = f"{_LIBRARY_ROOT}{path}"
    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Jarvis-Agents local model browser"},
        ) as client:
            resp = await client.get(url, params=params or {})
    except Exception as exc:  # noqa: BLE001 — offline is a normal state here
        log.info("ollama-library: %s unreachable (%s)", url, type(exc).__name__)
        return None, (
            "ollama.com did not answer, so the library cannot be browsed right "
            "now. Downloads by exact name still work."
        )
    if resp.status_code == 404:
        return None, "The Ollama library does not know this model."
    if resp.status_code != 200:
        return None, (
            f"ollama.com answered {resp.status_code}, so the library cannot be "
            "browsed right now. Downloads by exact name still work."
        )
    return resp.text, None


# ── search ──────────────────────────────────────────────────────────────────


def parse_search_html(page: str) -> list[dict[str, Any]]:
    """Model entries from a ``/search`` (or ``/library``) page, page order kept.

    Pure and offline-testable. Anchors on ``<li`` blocks that link to
    ``/library/{name}``; everything else about the markup is treated as noise.
    An entry that lost its blurb or badges upstream still lists with what could
    be read — a partial row beats a vanished model.
    """
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block_match in re.finditer(r"<li\b(.*?)</li>", page, re.DOTALL):
        block = block_match.group(1)
        name_match = re.search(r'href="/library/([^"/:?#]+)"', block)
        if not name_match:
            continue
        name = name_match.group(1)
        if name in seen:
            continue
        seen.add(name)

        description = ""
        for para in re.finditer(r"<p\b[^>]*>(.*?)</p>", block, re.DOTALL):
            text = _text(para.group(1))
            # The stats line ("17.2M Pulls · 64 Tags · Updated …") is also a
            # <p>; the blurb is the first paragraph that is not it.
            if text and "Pulls" not in text and "Updated" not in text:
                description = text
                break

        spans = [_text(m.group(1)) for m in re.finditer(r"<span\b[^>]*>([^<]*)</span>", block)]
        spans = [s for s in spans if s]
        capabilities = [badge for badge in _CAPABILITY_BADGES if badge in spans]
        sizes = [s for s in spans if re.fullmatch(r"\d+(?:\.\d+)?[bm]", s)]

        pulls_match = re.search(
            r">\s*([\d.,]+[KMB]?)\s*</span>\s*<span[^>]*>(?:&nbsp;|\s)*Pulls", block
        )
        updated = next((s for s in spans if s.endswith(" ago") or s == "yesterday"), "")

        entries.append(
            {
                "name": name,
                "description": description,
                "capabilities": capabilities,
                "cloud": "cloud" in spans,
                "sizes": sizes,
                "pulls": pulls_match.group(1) if pulls_match else "",
                "updated": updated,
            }
        )
        if len(entries) >= _SEARCH_LIMIT:
            break
    return entries


async def search_library(query: str) -> dict[str, Any]:
    """Search the public library; an empty query lists the popular models.

    ``installed`` is judged against the LOCAL server's inventory, so a model
    already pulled (any tag of it) reads as such directly in the results.
    """
    q = (query or "").strip()
    if cached := _cache_get(_search_cache, q):
        return cached

    page, error = await _fetch_page("/search", params={"q": q} if q else None)
    if page is None:
        return {"query": q, "models": [], "error": error}

    models = parse_search_html(page)
    if not models:
        # An empty answer for a nonsense query is correct; empty for a page
        # that no longer parses would silently kill the feature. Only the
        # latter is an error, and only the live guard can tell them apart —
        # here both honestly say "nothing found".
        log.info("ollama-library: search %r parsed 0 entries", q)

    installed, _inventory_error = await installed_models()
    for model in models:
        name = model["name"]
        model["installed"] = name in {i.partition(":")[0] for i in installed} or _is_installed(
            name, installed
        )

    result = {"query": q, "models": models, "error": None}
    _search_cache[q] = (time.monotonic(), result)
    return result


# ── tags ────────────────────────────────────────────────────────────────────


def parse_tags_html(page: str, name: str) -> list[dict[str, Any]]:
    """Tag entries from a ``/library/{name}/tags`` page, page order kept.

    The page renders each tag twice (a mobile and a desktop block). Instead of
    depending on either block's classes, this walks the FIRST occurrence of
    each distinct ``/library/{name}:{tag}`` href and reads the plain-text facts
    ("6.6GB", "256K context window", "Text, Image input", "5 months ago") from
    the window up to the next distinct tag.
    """
    href_re = re.compile(rf'href="/library/{re.escape(name)}:([^"?#]+)"')
    matches = list(href_re.finditer(page))
    first_seen: list[tuple[str, int]] = []
    seen: set[str] = set()
    for match in matches:
        tag = match.group(1)
        if tag not in seen:
            seen.add(tag)
            first_seen.append((tag, match.start()))

    entries: list[dict[str, Any]] = []
    for index, (tag, start) in enumerate(first_seen):
        end = first_seen[index + 1][1] if index + 1 < len(first_seen) else len(page)
        window = page[start:end]

        size_match = re.search(r"([\d.]+)\s*GB", window)
        context_match = re.search(r"([\d.]+[KM])\s+context window", window)
        inputs_match = re.search(r"([A-Za-z][A-Za-z, ]*?)\s+input\b", window)
        updated_match = re.search(r"(\d+ \w+ ago|yesterday)", window)

        entries.append(
            {
                "tag": tag,
                "id": f"{name}:{tag}",
                "size_gb": float(size_match.group(1)) if size_match else None,
                "context": context_match.group(1) if context_match else "",
                "inputs": _text(inputs_match.group(1)) if inputs_match else "",
                "updated": updated_match.group(1) if updated_match else "",
                # A hosting fact from the tag NAME, never inferred from a
                # missing size: parse drift must degrade to "size unknown",
                # not to hiding a local tag as cloud-only.
                "cloud": tag == "cloud" or tag.endswith("-cloud"),
            }
        )
    return entries


async def library_tags(model: str) -> dict[str, Any]:
    """Every tag of ``model`` with size, fit verdict and installed state.

    The fit verdict reuses the SAME rule the curated shortlist applies
    (:func:`jarvis.brain.ollama_pull.fit_verdict`), so "tight" means the same
    thing on both halves of the panel.
    """
    name = (model or "").strip()
    if not name or not _NAME_RE.fullmatch(name):
        return {"model": name, "tags": [], "error": "Not a valid library model name."}
    if cached := _cache_get(_tags_cache, name):
        return cached

    page, error = await _fetch_page(f"/library/{name}/tags")
    if page is None:
        return {"model": name, "tags": [], "error": error}

    tags = parse_tags_html(page, name)
    if not tags:
        return {
            "model": name,
            "tags": [],
            "error": f"No tags could be read for '{name}' — check it on ollama.com/library.",
        }

    installed, _inventory_error = await installed_models()
    memory_gb = total_memory_gb()
    accel, _accel_source = accelerator_gb()
    for entry in tags:
        entry["installed"] = _is_installed(entry["id"], installed)
        if entry["cloud"] or entry["size_gb"] is None:
            entry["fit"], entry["fit_note"] = "unknown", ""
        else:
            entry["fit"], entry["fit_note"] = fit_verdict(entry["size_gb"], memory_gb, accel)

    result = {"model": name, "tags": tags, "error": None}
    _tags_cache[name] = (time.monotonic(), result)
    return result
