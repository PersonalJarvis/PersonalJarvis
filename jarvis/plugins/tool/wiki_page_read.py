"""``wiki-page-read`` tool — read a single page from the long-term Obsidian wiki.

B5 follow-up (post-merge).  Router-tier read-only tool.

Why a second wiki tool exists
-----------------------------
``wiki-recall`` returns up to 5 hits with a 240-character snippet each.
That is enough for "what do we know about X" but truncates anything
longer.  When the brain has already narrowed the answer down to one page
(e.g. user asks "read me everything about Joy") it needs the *full*
markdown content, not a snippet.  This tool fills that gap.

Placement rule
--------------
Router-tier only.  Never include in any ``SUB_TOOLS`` frozenset — that
constraint is enforced structurally by Welle 4 (only ``"router"`` exists
as a tier) and re-stated here so future maintainers do not re-introduce
a second tier without re-evaluating this placement.

Path-traversal safety
---------------------
The ``path`` argument is treated as **vault-relative**.  After joining
with ``vault_root`` we re-resolve the absolute path and assert it stays
inside ``vault_root``.  Anything outside (e.g. ``../../etc/passwd`` or
absolute paths) is rejected with ``"path outside vault"``.

Privacy rule (matches ``wiki-recall``):
    The requested path is logged at INFO; the page body is **never**
    logged.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jarvis.core.protocols import ToolResult

log = logging.getLogger(__name__)


# Hard cap so a 50 MB stray file cannot blow up the brain context.  A
# normal wiki page is a few KB; 64 KB is generous head-room.
_MAX_PAGE_BYTES: int = 64 * 1024


class WikiPageReadTool:
    """Router-tier full-page reader for the long-term Obsidian wiki vault."""

    name: str = "wiki-page-read"
    description: str = (
        "Read a single page from the user's long-term Obsidian wiki, in full. "
        "Use this after wiki-recall when you need the complete content of one "
        "page (e.g. the user asks to 'read me everything about Joy' or wants "
        "a summary that needs more than the 240-char snippet). The path is "
        "vault-relative, e.g. 'people/harald.md'."
    )
    risk_tier: str = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Vault-relative path to the page, e.g. 'people/harald.md'. "
                    "Use the path returned by wiki-recall verbatim."
                ),
            },
        },
        "required": ["path"],
    }
    input_examples: list[dict[str, Any]] = [
        {"path": "people/harald.md"},
        {"path": "people/joy.md"},
    ]

    def __init__(self, vault_root: Path) -> None:
        # Resolve once at construction time so symlink games cannot move
        # the vault out from under us mid-session.
        self._vault_root = vault_root.resolve()

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        raw_path = str(args.get("path", "")).strip()
        if not raw_path:
            return ToolResult(success=False, output="", error="missing 'path' argument")

        # Reject absolute paths and obvious traversal attempts up front —
        # cleaner error message than relying on the relative-to check.
        candidate = Path(raw_path)
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            log.info("wiki-page-read: rejected non-vault-relative path: %r", raw_path)
            return ToolResult(
                success=False,
                output="",
                error="path must be vault-relative (no '..' or absolute paths)",
            )

        full_path = (self._vault_root / candidate).resolve()

        # Defence in depth: even after resolving, ensure the path stayed
        # inside the vault.  ``is_relative_to`` is Python 3.9+.
        if not full_path.is_relative_to(self._vault_root):
            log.warning(
                "wiki-page-read: path resolved outside vault: %r -> %s",
                raw_path,
                full_path,
            )
            return ToolResult(success=False, output="", error="path outside vault")

        if not full_path.exists():
            log.info("wiki-page-read: not found: %s", full_path.relative_to(self._vault_root))
            return ToolResult(
                success=False,
                output="",
                error=f"page not found: {raw_path}",
            )

        if not full_path.is_file():
            return ToolResult(
                success=False,
                output="",
                error=f"path is not a file: {raw_path}",
            )

        try:
            size = full_path.stat().st_size
        except OSError as exc:
            log.warning("wiki-page-read: stat failed for %s: %s", full_path, exc)
            return ToolResult(success=False, output="", error="cannot stat page")

        if size > _MAX_PAGE_BYTES:
            return ToolResult(
                success=False,
                output="",
                error=f"page too large ({size} bytes; max {_MAX_PAGE_BYTES})",
            )

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.warning("wiki-page-read: read failed for %s: %s", full_path, exc)
            return ToolResult(success=False, output="", error="cannot read page")

        rel = full_path.relative_to(self._vault_root)
        log.info("wiki-page-read: served %s (%d bytes)", rel, size)

        # Prefix with the relative path so the brain has unambiguous
        # provenance in the prompt without having to remember the request.
        header = f"# {rel.as_posix()}\n\n"
        return ToolResult(success=True, output=header + content)


def _build_page_read_tool() -> "WikiPageReadTool":
    """Construct a :class:`WikiPageReadTool` with the configured vault root.

    Mirrors :func:`jarvis.plugins.tool.wiki_recall._build_search_instance` so
    the factory can wire both tools the same way.  Resolves through
    :func:`jarvis.memory.wiki.vault_root.resolve_vault_root` (spec A7), so a
    relative root anchors to the repo root, never the process CWD.  Falls
    back to the resolver's default vault location when the config field is
    absent, and logs a single WARNING in that case.
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
        log.debug("wiki-page-read: config load skipped: %s", exc)

    if raw is None:
        log.warning(
            "wiki-page-read: cfg.wiki_integration.vault_root not found; "
            "defaulting to the standard vault location",
        )

    vault_root = resolve_vault_root(raw).path

    return WikiPageReadTool(vault_root=vault_root)
