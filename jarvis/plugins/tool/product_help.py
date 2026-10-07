"""``product_help`` — the live voice model reads the built-in product guide.

The always-on product brief (``jarvis.live.product``) only says what Personal
Jarvis is. Exact answers — how to connect a provider, where a setting lives,
what to do when voice will not start — come from the same curated
``docs/product/`` library the Docs view shows, fetched only when asked. That
keeps the live instructions small while every detail stays one call away.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_GUIDE_CHARS = 6000
_MAX_MATCHES = 4
_FALLBACK_REGISTRY: list[Any] = []
_FALLBACK_LOCK = asyncio.Lock()


async def _registry() -> Any:
    """The app's Docs registry, or a private one when the server is not up."""
    from jarvis.core.runtime_refs import get_web_app

    app = get_web_app()
    registry = getattr(getattr(app, "state", None), "doc_registry", None)
    if registry is None:
        async with _FALLBACK_LOCK:
            if not _FALLBACK_REGISTRY:
                from jarvis.core.paths import default_doc_roots
                from jarvis.docs.registry import DocRegistry

                _FALLBACK_REGISTRY.append(DocRegistry(roots=default_doc_roots(), index_db=None))
        registry = _FALLBACK_REGISTRY[0]
    await registry.ensure_loaded()
    return registry


def _any_term(query: str) -> str:
    """OR the words: the index ANDs bare terms, so a spoken question finds nothing."""
    words = [word for word in re.findall(r"\w+", query) if len(word) > 1]
    return " OR ".join(f'"{word}"' for word in words[:12])


def _guide(doc: Any) -> dict[str, Any]:
    body = doc.body.strip()
    truncated = len(body) > _GUIDE_CHARS
    return {
        "slug": doc.slug,
        "title": doc.frontmatter.title,
        "summary": doc.frontmatter.summary,
        "text": body[:_GUIDE_CHARS],
        "truncated": truncated,
    }


class ProductHelpTool:
    read_only = True

    name = "product_help"
    description = (
        "Read Personal Jarvis's built-in user guide: what a feature does, how to set it up, "
        "where a setting is, and how to fix a problem. Pass a few English keywords as query "
        "(for example 'connect claude subscription' or 'wake word not working'), or a slug "
        "from an earlier result to read that whole guide."
    )
    risk_tier = "safe"
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A few English keywords."},
            "slug": {"type": "string", "description": "Exact guide slug to read in full."},
        },
        "additionalProperties": False,
    }

    async def execute(self, args: dict[str, Any], ctx: Any) -> Any:
        from jarvis.core.protocols import ToolResult

        del ctx
        query = str(args.get("query") or "").strip()
        slug = str(args.get("slug") or "").strip()
        if not query and not slug:
            return ToolResult(False, None, "Pass a query or a guide slug.")
        try:
            registry = await _registry()
        except Exception as exc:
            log.warning("product_help could not load the guide: %s", exc)
            return ToolResult(False, None, "The built-in guide is not available right now.")

        if slug:
            doc = registry.get(slug)
            if doc is not None:
                return ToolResult(True, {"guide": _guide(doc)})
        hits = (
            await asyncio.to_thread(registry.search_query, _any_term(query), limit=_MAX_MATCHES)
            if query
            else []
        )
        if not hits:
            # Better a table of contents than a dead end: the model picks a slug.
            index = [
                {"slug": doc.slug, "title": doc.frontmatter.title}
                for doc in sorted(registry.list(), key=lambda d: d.frontmatter.title)
            ]
            return ToolResult(True, {"matches": [], "available_guides": index})
        matches = []
        for hit in hits:
            doc = registry.get(hit.slug)
            matches.append(
                {
                    "slug": hit.slug,
                    "title": hit.title,
                    "summary": doc.frontmatter.summary if doc is not None else "",
                    "excerpt": re.sub(r"</?mark>", "", hit.snippet),
                }
            )
        best = registry.get(hits[0].slug)
        output: dict[str, Any] = {"matches": matches}
        if best is not None:
            output["guide"] = _guide(best)
        return ToolResult(True, output)
