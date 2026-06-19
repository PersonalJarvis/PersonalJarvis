#!/usr/bin/env python
"""One-shot, deterministic re-linker for existing wiki session pages.

Session rollups written before the graph-connectivity fix scatter the
Obsidian graph: they link Title-Case display names of ephemeral apps
(``[[Brave Browser]]``, ``[[PowerShell]]``) that resolve to no page, carry
the odd token-truncated ``[[PickerHost.`` fragment, and never link a durable
hub — so each session floats as an isolated 2-node pair. New rollups are
already fixed at the source; this tool cleans the pages that already exist.

It reuses the same tested, LLM-free logic the live worker applies
(``jarvis.memory.wiki.session_links.relink_session_body``): strip dangling
fragments, canonicalise links that resolve to a real page, demote the rest to
plain text, and append a ``## Related`` backbone linking the user entity and
the concepts/projects the body actually references.

Dry-run by default — prints what WOULD change and touches nothing. Pass
``--apply`` to write. The vault is a git repo, so changes are reversible.

    python scripts/wiki_relink_sessions.py                 # dry-run, default vault
    python scripts/wiki_relink_sessions.py --apply
    python scripts/wiki_relink_sessions.py --vault path/to/vault --user-slug ruben --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows stdout is cp1252 by default; the report contains "→" and "✓".
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001, S110 — cosmetic stdout tweak; safe to ignore
    pass

# Make the repo importable when run as a bare script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jarvis.memory.wiki.session_links import SlugIndex, relink_session_body  # noqa: E402
from jarvis.memory.wiki.session_rollup import _DURABLE_DIRS, _read_aliases  # noqa: E402

_FM_BOUNDARY = "---"


def build_index(vault_root: Path) -> SlugIndex:
    """Build a :class:`SlugIndex` from the durable pages of ``vault_root``."""
    pages: list[tuple[str, str, list[str]]] = []
    for directory in _DURABLE_DIRS:
        page_dir = vault_root / directory
        if not page_dir.is_dir():
            continue
        for md_path in sorted(page_dir.glob("*.md")):
            if md_path.name.startswith("."):
                continue
            pages.append((directory, md_path.stem, _read_aliases(md_path)))
    return SlugIndex.from_pages(pages)


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split a page into ``(verbatim_frontmatter_block, body)``.

    The frontmatter block (including both ``---`` fences) is preserved
    byte-for-byte; only the body is re-linked. Returns ``("", text)`` when no
    closing fence is found.
    """
    if not text.startswith(_FM_BOUNDARY):
        return "", text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == _FM_BOUNDARY:
            header = "\n".join(lines[: i + 1])
            body = "\n".join(lines[i + 1:])
            return header, body
    return "", text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vault",
        type=Path,
        default=_REPO_ROOT / "wiki" / "obsidian-vault",
        help="vault root (default: wiki/obsidian-vault)",
    )
    parser.add_argument(
        "--user-slug",
        default="ruben",
        help="slug of the user entity to link in every session footer",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes (default: dry-run, print only)",
    )
    args = parser.parse_args()

    vault: Path = args.vault.resolve()
    sessions_dir = vault / "sessions"
    if not sessions_dir.is_dir():
        print(f"No sessions/ directory under {vault} — nothing to do.")
        return 1

    index = build_index(vault)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] re-linking session pages under {sessions_dir}")
    print(f"  durable pages indexed: {len(index._by_slug)}  user hub: [[{args.user_slug}]]\n")

    scanned = changed = 0
    for md_path in sorted(sessions_dir.glob("*.md")):
        scanned += 1
        try:
            raw = md_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"  ! skip {md_path.name}: {exc}")
            continue
        header, body = split_frontmatter(raw)
        new_body, stats = relink_session_body(body, index, user_slug=args.user_slug)
        if not stats["changed"]:
            continue
        changed += 1
        new_raw = (header + "\n" + new_body) if header else new_body
        print(f"  {'✓ wrote' if args.apply else '→ would fix'} {md_path.name}")
        if args.apply:
            md_path.write_text(new_raw, encoding="utf-8")

    print(f"\n{mode}: {changed}/{scanned} session page(s) "
          f"{'updated' if args.apply else 'need re-linking'}.")
    if changed and not args.apply:
        print("Re-run with --apply to write the changes (vault is git-tracked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
