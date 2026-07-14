"""``wiki-list`` tool — deterministic listing of the long-term Obsidian wiki.

Forensic origin (voice session 2026-07-14 09:29, 66 s turn)
-----------------------------------------------------------
Asked "what is in my wiki", the delegated router brain had no way to SEE
the vault. It probed blindly — ``wiki-recall`` → ``wiki-page-read
index.md`` (not found) → ``SOUL.md`` (not found) — burning ~14 LLM
rounds, then recited the *example* directory layout from ``schema.md``
(the vault's editing contract) as if it were the actual vault content.
Every named file in the spoken answer was invented.

A listing question needs a listing tool: this one walks the real vault
once and returns ground truth in a single round — path, size, first
heading — so the model neither probes nor guesses.

Placement rule
--------------
Router-tier only, read-only, risk ``safe``. Never in any worker tool set
(AP-5/AP-14).

Privacy rule (matches ``wiki-recall`` / ``wiki-page-read``):
    Only paths, sizes, and first headings are returned/logged — never
    page bodies.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jarvis.core.protocols import ToolResult

log = logging.getLogger(__name__)

# Upper bound on listed pages so a pathological vault cannot flood the
# brain context (the tool-use loop additionally caps every tool result).
_MAX_ENTRIES: int = 500

# Only this many leading bytes are read per file to extract the first
# heading and detect a meta page — never the full body.
_HEAD_BYTES: int = 2048


def _first_heading_and_meta(path: Path) -> tuple[str, bool]:
    """Return ``(first_heading, is_meta)`` from a page's leading bytes.

    ``is_meta`` is True when a YAML frontmatter block declares
    ``type: meta`` — those pages (e.g. ``schema.md``) are the vault's
    maintenance contract, not user content, and get flagged in the
    listing so the model cannot present the contract's example layout
    as actual vault contents (live incident 2026-07-14).
    """
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:_HEAD_BYTES]
    except OSError:
        return "", False

    is_meta = False
    lines = head.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:32]:
            if line.strip() == "---":
                break
            key, _, value = line.partition(":")
            if key.strip().lower() == "type" and value.strip().lower() == "meta":
                is_meta = True
                break

    heading = ""
    for line in lines:
        if line.startswith("# "):
            heading = line[2:].strip()
            break
    return heading, is_meta


class WikiListTool:
    """Router-tier ground-truth listing of the long-term Obsidian wiki vault."""

    name: str = "wiki-list"
    description: str = (
        "List what ACTUALLY exists in the user's long-term Obsidian wiki "
        "vault: every markdown page with its vault-relative path, size, and "
        "title. Call this FIRST for overview questions like 'what is in my "
        "wiki / notes / vault', before guessing any page path, and whenever "
        "wiki-page-read reported 'not found'. This listing is the ground "
        "truth — a page absent here does not exist, regardless of what the "
        "schema/contract page describes."
    )
    risk_tier: str = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }
    input_examples: list[dict[str, Any]] = [{}]

    def __init__(self, vault_root: Path) -> None:
        # Resolve once at construction time so symlink games cannot move
        # the vault out from under us mid-session (matches wiki-page-read).
        self._vault_root = vault_root.resolve()

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        root = self._vault_root
        if not root.is_dir():
            return ToolResult(
                success=True,
                output=(
                    "The wiki vault directory does not exist yet — the wiki "
                    "is empty. Nothing has been saved so far."
                ),
            )

        pages: list[tuple[str, int]] = []
        truncated = False
        for path in sorted(root.rglob("*.md")):
            rel = path.relative_to(root)
            # Skip hidden trees (.obsidian app config, .trash, …).
            if any(part.startswith(".") for part in rel.parts):
                continue
            if not path.is_file():
                continue
            if len(pages) >= _MAX_ENTRIES:
                truncated = True
                break
            try:
                size = path.stat().st_size
            except OSError:
                continue
            pages.append((rel.as_posix(), size))

        if not pages:
            return ToolResult(
                success=True,
                output="The wiki vault is empty — no markdown pages exist yet.",
            )

        lines = [f"Wiki vault listing ({len(pages)} page(s)):"]
        for rel_posix, size in pages:
            heading, is_meta = _first_heading_and_meta(root / rel_posix)
            suffix = f" — {heading}" if heading else ""
            marker = (
                "  [system file — the vault's editing contract, NOT user "
                "content; its example layout is not the real vault]"
                if is_meta
                else ""
            )
            lines.append(f"- {rel_posix} ({size} bytes){suffix}{marker}")
        if truncated:
            lines.append(
                f"… [truncated: more than {_MAX_ENTRIES} pages exist; "
                f"showing the first {_MAX_ENTRIES}]"
            )

        log.info("wiki-list: served %d page(s)%s",
                 len(pages), " (truncated)" if truncated else "")
        return ToolResult(success=True, output="\n".join(lines))


def _build_wiki_list_tool() -> "WikiListTool":
    """Construct a :class:`WikiListTool` with the configured vault root.

    Mirrors ``wiki_page_read._build_page_read_tool`` so the factory can
    wire all wiki tools the same way (resolves through
    :func:`jarvis.memory.wiki.vault_root.resolve_vault_root`, spec A7).
    """
    from jarvis.memory.wiki.vault_root import resolve_vault_root

    raw: str | Path | None = None
    try:
        from jarvis.core import config as cfg

        loaded = cfg.load_config()
        wiki_cfg = getattr(loaded, "wiki_integration", None)
        if wiki_cfg is not None:
            raw = wiki_cfg.vault_root
    except Exception as exc:  # noqa: BLE001
        log.debug("wiki-list: config load skipped: %s", exc)

    vault_root = resolve_vault_root(raw).path
    if raw is None:
        log.warning(
            "wiki-list: cfg.wiki_integration.vault_root not found; "
            "defaulting to %s",
            vault_root,
        )

    return WikiListTool(vault_root=vault_root)
