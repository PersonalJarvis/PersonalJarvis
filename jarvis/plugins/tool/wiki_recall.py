"""``wiki-recall`` tool — keyword search over the long-term Obsidian wiki.

B5 Agent B (recall-tool).  Router-tier read-only tool.

The tool exposes ``VaultSearch`` (``jarvis.memory.wiki.search``) to the
router brain, so the brain can look up information that Jarvis or the
user has previously stored in the wiki vault.

Usage pattern:
    Brain calls ``wiki-recall`` with 1–4 keywords whenever the user asks
    "what do we know about X", "who is Y", or references a past project,
    person, or decision by name.  The tool returns a compact markdown
    block with up to *k* ranked hits, ready to paste into the system
    prompt.

Placement rule:
    This tool is router-visible. Jarvis-Agents may reach the live tool only
    through ADR-0025's mission-scoped supervisor broker. It must **never**
    appear in any ``SUB_TOOLS`` frozenset or direct worker tool set (AP-D9).

Privacy rule (AP-7 / §5 overview):
    Query text is logged only at DEBUG level.  Hit count summary is
    INFO level.

Vault-root resolution (§6 AGENT-B, spec A7):
    ``cfg.wiki_integration.vault_root`` is resolved through the canonical
    :func:`jarvis.memory.wiki.vault_root.resolve_vault_root` — a relative
    root anchors to the repo root, never the process CWD. If the config
    field is unavailable at construction time, the tool falls back to the
    resolver's default vault location and logs a single WARNING.

Mode awareness — UltraWiki (decision D-5, either-or):
    While UltraWiki mode is ON and its store is open, this tool searches the
    UltraWiki store instead of the vault: that store is where the user's
    knowledge actually lives in Ultra mode, and answering from the vault would
    make the brain claim ignorance of thousands of ingested items. The two
    memories are never merged — the mode picks exactly one.

    No relevance floor is applied here. The user (or the brain acting on the
    user's explicit question) ASKED for this lookup, which is the Ask-view
    semantics of design doc 03: hiding evidence from someone who searched is a
    different defect than volunteering it unasked. The unsolicited surface —
    the system-prompt context injector — carries the gate instead.

    Every UltraWiki failure mode degrades to the vault search with one log
    line, and the whole UltraWiki call is bounded by
    :data:`_ULTRA_TIMEOUT_S`, so a cold store or a dead embedding endpoint can
    never leave the brain with a dead tool. With the mode off, nothing below
    runs and the behaviour is exactly what it was before.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.core.protocols import ToolResult

if TYPE_CHECKING:
    from jarvis.memory.wiki.search import VaultSearch

log = logging.getLogger(__name__)

#: Hard ceiling for the whole UltraWiki lookup (fan-out + rerank + context
#: expansion). Generous compared to the injector's budget because this is a
#: deliberate tool call the brain is already waiting on, but still bounded:
#: past it the vault answers instead of the turn stalling.
_ULTRA_TIMEOUT_S = 2.5

#: Per-hit snippet cap. Vault snippets are short by construction; an UltraWiki
#: snippet can be a whole message or wiki section, and five of them unbounded
#: would crowd the router prompt.
_ULTRA_SNIPPET_CHARS = 240


class WikiRecallTool:
    """Router-tier keyword search over the long-term Obsidian wiki vault."""

    name: str = "wiki-recall"
    description: str = (
        "Search the user's long-term Obsidian wiki for notes matching keywords. "
        "Returns a compact markdown summary with up to 5 ranked hits. Use this "
        "when the user asks 'what do we know about X', 'who is Y', or references "
        "a past project, person, or decision by name."
    )
    risk_tier: str = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "1-4 keywords; matched against frontmatter + body",
            },
            "k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
        },
        "required": ["query"],
    }
    input_examples: list[dict[str, Any]] = [
        {"query": "Harald birthday"},
        {"query": "Personal Jarvis architecture"},
    ]

    def __init__(self, search: VaultSearch) -> None:
        self._search = search

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        """Run the vault search and return a compact markdown block.

        Output format::

            ## Wiki hits for "<query>"
            - **<title>** — <snippet>… (10-notes/Harald.md)
            - …

        Paths are rendered relative to ``vault_root`` so the brain sees
        ``10-notes/Harald.md``, not an absolute Windows path.

        In UltraWiki mode the same block is rendered from the UltraWiki store,
        with the source, timestamp and permalink in the citation slot the
        vault path fills with a relative page path.

        Returns
        -------
        ToolResult(success=True, output=<markdown>)
            When hits are found.
        ToolResult(success=True, output="No matches …")
            When the memory is available but nothing matches.
        ToolResult(success=False, error="vault unavailable")
            When UltraWiki is not the active memory and the vault root does
            not exist at execute time.
        """
        query = str(args.get("query", "")).strip()
        k = int(args.get("k", 5))
        # Clamp defensively — schema validation may not have run upstream.
        k = max(1, min(k, 10))

        # UltraWiki mode owns the answer when it is on and ready (D-5); a
        # None here is either "mode off" or "UltraWiki could not answer", and
        # both continue into the unchanged vault path below.
        ultra_output = await _ultrawiki_block(query, k)
        if ultra_output is not None:
            return ToolResult(success=True, output=ultra_output)

        vault_root = self._search._root  # type: ignore[attr-defined]

        # Privacy: query is DEBUG only.
        log.debug("wiki-recall: query=%r k=%d vault=%s", query, k, vault_root)

        if not vault_root.exists():
            return ToolResult(
                success=False,
                output="",
                error="vault unavailable",
            )

        hits = self._search.search(query, k=k)

        log.info("wiki-recall: %d hit(s) for query (k=%d)", len(hits), k)

        if not hits:
            return ToolResult(
                success=True,
                output=f'No wiki matches found for "{query}".',
            )

        lines: list[str] = [f'## Wiki hits for "{query}"']
        for hit in hits:
            # Render path relative to vault_root so the brain sees a short,
            # portable path rather than an absolute Windows path.
            try:
                rel_path = hit.path.relative_to(vault_root)
            except ValueError:
                rel_path = hit.path

            snippet_part = f" — {hit.snippet}" if hit.snippet else ""
            lines.append(f"- **{hit.title}**{snippet_part} ({rel_path})")

        return ToolResult(success=True, output="\n".join(lines))


def _active_ultrawiki_service() -> Any | None:
    """The live UltraWiki service while it can answer, else ``None``.

    Lazy import (AP-26 + the plugin no-module-level-``jarvis``-import rule):
    with UltraWiki off this costs one module lookup and nothing else.
    """
    try:
        from jarvis.ultrawiki.service import active_search_service

        return active_search_service()
    except Exception:  # noqa: BLE001 — an unavailable mode is never an error here
        log.debug("wiki-recall: UltraWiki service lookup failed", exc_info=True)
        return None


async def _ultrawiki_block(query: str, k: int) -> str | None:
    """The rendered UltraWiki answer, or ``None`` to fall back to the vault.

    ``None`` means one of: the mode is off, the store is not open, the lookup
    timed out, or it raised. An UltraWiki search that ran and found nothing is
    NOT a fallback — it is an honest empty answer from the active memory, and
    returns the same "no matches" line the vault path would.
    """
    service = _active_ultrawiki_service()
    if service is None:
        return None
    try:
        hits = await asyncio.wait_for(
            service.search(
                query=query,
                k=k,
                rerank=True,
                enforce_floor=False,
                expand_context=True,
            ),
            timeout=_ULTRA_TIMEOUT_S,
        )
    except TimeoutError:
        log.warning(
            "wiki-recall: UltraWiki search exceeded %.1fs — answering from the "
            "wiki vault instead",
            _ULTRA_TIMEOUT_S,
        )
        return None
    except Exception as exc:  # noqa: BLE001 — degrade to the vault, never a dead tool
        log.warning(
            "wiki-recall: UltraWiki search failed (%s) — answering from the "
            "wiki vault instead",
            exc,
        )
        return None

    hits = list(hits or ())
    log.info("wiki-recall: %d UltraWiki hit(s) for query (k=%d)", len(hits), k)
    if not hits:
        return f'No wiki matches found for "{query}".'
    lines = [f'## Wiki hits for "{query}"']
    lines.extend(_render_ultrawiki_hit(hit) for hit in hits[:k])
    return "\n".join(lines)


def _render_ultrawiki_hit(hit: Any) -> str:
    """One hit in the tool's established line shape.

    ``- **Title** — snippet (citation)``, where the citation slot carries the
    source, the timestamp and the permalink instead of the vault's relative
    page path. Keeping the shape stable matters: the router prompt has been
    tuned against it.
    """
    source = str(getattr(hit, "source_id", "") or "")
    title = str(getattr(hit, "title", "") or "").strip() or source or "(untitled)"
    text = " ".join(str(getattr(hit, "snippet", "") or "").split())
    if not text:
        # A hit whose own snippet is empty still carries its neighbouring
        # evidence from the context-expansion stage — better than a bare title.
        context = getattr(hit, "context", ()) or ()
        text = " ".join(str(context[0]).split()) if context else ""
    if len(text) > _ULTRA_SNIPPET_CHARS:
        text = text[:_ULTRA_SNIPPET_CHARS].rstrip() + "…"
    citation = " · ".join(
        part
        for part in (
            source,
            str(getattr(hit, "timestamp_utc", "") or ""),
            str(getattr(hit, "permalink", "") or ""),
        )
        if part
    )
    snippet_part = f" — {text}" if text else ""
    citation_part = f" ({citation})" if citation else ""
    return f"- **{title}**{snippet_part}{citation_part}"


def _build_search_instance() -> VaultSearch:
    """Build a ``VaultSearch`` with the configured vault root.

    Resolves through :func:`jarvis.memory.wiki.vault_root.resolve_vault_root`
    (spec A7), so a relative root anchors to the repo root, never the
    process CWD.  Falls back to the resolver's default vault location when
    the config field is absent, logging a single WARNING in that case.
    """
    from jarvis.memory.wiki.search import VaultSearch
    from jarvis.memory.wiki.vault_root import resolve_vault_root

    raw: str | Path | None = None
    try:
        from jarvis.core import config as cfg

        loaded = cfg.load_config()
        # Agent A defines wiki_integration.vault_root; it may not exist yet.
        wiki_cfg = getattr(loaded, "wiki_integration", None)
        if wiki_cfg is not None:
            raw = wiki_cfg.vault_root
    except Exception as exc:  # noqa: BLE001
        log.debug("wiki-recall: config load skipped: %s", exc)

    resolved = resolve_vault_root(raw).path
    if raw is None:
        log.warning(
            "wiki-recall: cfg.wiki_integration.vault_root not found; "
            "defaulting to %s",
            resolved,
        )

    return VaultSearch(resolved)
