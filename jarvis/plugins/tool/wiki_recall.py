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
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.core.protocols import ToolResult

if TYPE_CHECKING:
    from jarvis.memory.wiki.search import VaultSearch

log = logging.getLogger(__name__)


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
        {"query": "Sam birthday"},
        {"query": "Personal Jarvis architecture"},
    ]

    def __init__(self, search: "VaultSearch") -> None:
        self._search = search

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        """Run the vault search and return a compact markdown block.

        Output format::

            ## Wiki hits for "<query>"
            - **<title>** — <snippet>… (10-notes/Sam.md)
            - …

        Paths are rendered relative to ``vault_root`` so the brain sees
        ``10-notes/Sam.md``, not an absolute Windows path.

        Returns
        -------
        ToolResult(success=True, output=<markdown>)
            When hits are found.
        ToolResult(success=True, output="No matches …")
            When the vault is available but nothing matches.
        ToolResult(success=False, error="vault unavailable")
            When the vault root does not exist at execute time.
        """
        query = str(args.get("query", "")).strip()
        k = int(args.get("k", 5))
        # Clamp defensively — schema validation may not have run upstream.
        k = max(1, min(k, 10))

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


def _build_search_instance() -> "VaultSearch":
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
