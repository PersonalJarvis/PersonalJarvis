"""One-time, idempotent maintenance pass over the wiki vault.

Wave-1 cleanup. The vault accumulated four classes of junk before the
session-rollup graph-connectivity fix and the FTS-purge wiring landed:

1. A prompt-template-leak page — an LLM response that dumped part of its
   own system prompt into the body (``_archive/sessions/2026-06-02-rkffieuk.md``:
   body starts ``personal-jarvis]]` if appropriate...``).
2. Session IDs present in BOTH ``sessions/`` and ``_archive/sessions/``.
   The archive is the rolled-up destination; a same-ID file still sitting in
   the live ``sessions/`` directory is a stale leftover.
3. Truncated session pages whose prose body ends mid-sentence (the brain hit
   its token cap), e.g. ``...Spanning from`` or ``...via [[PickerHost.``.
4. Dangling app ``[[wikilinks]]`` — ``[[Snipping Tool]]``, ``[[Windows Terminal]]``,
   ``[[Picker]]`` — that resolve to no vault page and render as Obsidian orphan
   nodes.

This module is DESTRUCTIVE, so it is dry-run by default: ``clean_vault`` only
reports unless ``apply=True``. When applying it (a) takes a FULL tar.gz snapshot
of the vault INCLUDING ``_archive/`` (the normal ``BackupManager.snapshot`` skips
``_archive/`` and ``attachments/``, which would make removals here irreversible),
(b) removes the junk pages, (c) rewrites survivors to drop dangling links, and
(d) purges the removed files' rows from the FTS index via
``AtomicWriter.forget_paths`` so ``wiki-recall`` stops returning ghost hits.

Re-running after an apply is a no-op: the junk is gone and survivors carry no
dangling links.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from .session_links import _WIKILINK_RE, strip_dangling_wikilinks
from .telemetry import telemetry
from .wikilink import resolve_wikilink

log = logging.getLogger(__name__)

# A body is "complete" when its last non-empty prose line ends in one of these.
# ``)`` covers the common ``(see ...)`` close; the rest are sentence terminators.
_TERMINATORS: tuple[str, ...] = (".", "!", "?", ")")

# The prompt-template-leak page: a fixed, known-bad file. Hard-coded by path so
# the script never depends on heuristically guessing "this looks like a leak".
LEAK_RELPATH: str = "_archive/sessions/2026-06-02-rkffieuk.md"


def _split_body(raw: str) -> str:
    """Return the prose body: everything after the YAML frontmatter and H1,
    up to (but not including) the ``## Related`` block. Whitespace-trimmed.

    Session pages are ``---fm---`` then ``# Session ...`` then a prose
    paragraph then ``## Related``. We measure truncation on the prose only —
    the ``## Related`` footer is always present and would mask a truncated body.
    """
    text = raw
    # Drop frontmatter.
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    # Cut at the Related footer if present.
    rel = text.find("\n## Related")
    if rel != -1:
        text = text[:rel]
    # Drop the leading H1 line(s).
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("# ")]
    return "\n".join(lines).strip()


def is_truncated_body(raw: str) -> bool:
    """True when the page's prose body ends without a sentence terminator.

    An empty body (the whole paragraph was lost, leaving only the ``## Related``
    footer) also counts as truncated. A body ending in ``.``/``!``/``?``/``)``
    is considered complete.
    """
    body = _split_body(raw)
    if not body:
        return True
    return not body.rstrip().endswith(_TERMINATORS)


def dangling_link_targets(raw: str, vault_root: Path) -> list[str]:
    """Return the wikilink targets in ``raw`` that resolve to no vault page.

    Uses the real on-disk resolver: ``[[entities/ruben]]`` resolves and is
    kept; bare app names like ``[[Snipping Tool]]`` resolve to nothing and are
    flagged. Operates on the CLOSED-link body after a dangling-fragment strip,
    so an unclosed ``[[PickerHost.`` never reaches the resolver.
    """
    cleaned = strip_dangling_wikilinks(raw)
    dangling: list[str] = []
    seen: set[str] = set()
    for m in _WIKILINK_RE.finditer(cleaned):
        target = m.group(1).split("|", 1)[0].strip()
        if not target or target in seen:
            continue
        seen.add(target)
        if resolve_wikilink(target, vault_root) is None:
            dangling.append(target)
    return dangling


def _demote_unresolvable_links(raw: str, vault_root: Path) -> str:
    """Demote every closed ``[[link]]`` that resolves nowhere to plain text.

    Handles both the bare ``[[Target]]`` and the alias ``[[Target|Display]]``
    forms (the alias form keeps the display text). Resolvable links are left
    byte-identical. Unclosed ``[[`` fragments are stripped first.
    """

    def _replace(m: re.Match[str]) -> str:
        body = m.group(1)
        target_part, sep, alias = body.partition("|")
        display = (alias if sep else target_part).strip()
        if resolve_wikilink(target_part.strip(), vault_root) is None:
            return display
        return m.group(0)

    return _WIKILINK_RE.sub(_replace, strip_dangling_wikilinks(raw))


@dataclass(slots=True)
class CleanupReport:
    """What ``clean_vault`` found (and, when ``applied``, did)."""

    applied: bool = False
    backup_path: Path | None = None
    removed_leak: list[Path] = field(default_factory=list)
    removed_duplicates: list[Path] = field(default_factory=list)
    removed_truncated: list[Path] = field(default_factory=list)
    relinked: list[Path] = field(default_factory=list)
    dangling_stripped: int = 0

    @property
    def removed_paths(self) -> list[Path]:
        """Every file this run removed (for the FTS purge)."""
        return [*self.removed_leak, *self.removed_duplicates, *self.removed_truncated]

    @property
    def total_changes(self) -> int:
        return len(self.removed_paths) + len(self.relinked)


def _session_files(vault_root: Path, *, subdir: str) -> list[Path]:
    d = vault_root / subdir
    if not d.is_dir():
        return []
    return sorted(d.glob("*.md"))


def _duplicate_live_copies(vault_root: Path) -> list[Path]:
    """Live ``sessions/<id>.md`` files whose ID also exists in
    ``_archive/sessions/``. The archive copy is canonical (it is the
    rolled-up destination); the live copy is the stale leftover to remove.
    """
    archived_ids = {p.stem for p in _session_files(vault_root, subdir="_archive/sessions")}
    return [
        p
        for p in _session_files(vault_root, subdir="sessions")
        if p.stem in archived_ids
    ]


_FULL_BACKUP_TS_FORMAT = "%Y%m%d%H%M%S"


def _full_snapshot(vault_root: Path, backup_dir: Path) -> Path:
    """Tar.gz the ENTIRE vault (including ``_archive/``) for one-shot recovery.

    Members are stored with vault-relative POSIX names, matching the
    ``BackupManager`` convention so an operator restores with the same mental
    model. Hidden dirs (``.obsidian``) are skipped.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime(_FULL_BACKUP_TS_FORMAT)
    target = backup_dir / f"wiki-cleanup-{ts}.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        for item in sorted(vault_root.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(vault_root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            tar.add(item, arcname=rel.as_posix(), recursive=False)
    return target


def clean_vault(
    vault_root: Path,
    *,
    apply: bool = False,
    backup_dir: Path | None = None,
    writer=None,
) -> CleanupReport:
    """Run the one-time cleanup pass over ``vault_root``.

    Dry-run unless ``apply=True``. ``backup_dir`` defaults to
    ``<vault_root>/../wiki-backups``. ``writer`` is an optional
    :class:`~jarvis.memory.wiki.atomic_writer.AtomicWriter` whose
    ``forget_paths`` is used to purge FTS rows for removed files; when omitted,
    one is constructed against ``vault_root`` + ``backup_dir``.

    Order matters: dedupe first (so a duplicate is counted once), then the
    truncation pass over every REMAINING session file in both directories,
    then strip dangling links from the survivors.
    """
    vault_root = Path(vault_root).resolve()
    report = CleanupReport(applied=apply)
    if not vault_root.is_dir():
        raise ValueError(f"vault root not found: {vault_root}")
    if backup_dir is None:
        backup_dir = vault_root.parent / "wiki-backups"

    # --- decide what to remove (read-only) ----------------------------------
    to_remove: list[Path] = []

    leak = vault_root / LEAK_RELPATH
    if leak.is_file():
        report.removed_leak.append(leak)
        to_remove.append(leak)

    for dup in _duplicate_live_copies(vault_root):
        if dup not in to_remove:
            report.removed_duplicates.append(dup)
            to_remove.append(dup)

    removed_set = set(to_remove)
    survivors: list[Path] = []
    for sub in ("sessions", "_archive/sessions"):
        for path in _session_files(vault_root, subdir=sub):
            if path in removed_set:
                continue
            if is_truncated_body(path.read_text(encoding="utf-8")):
                report.removed_truncated.append(path)
                to_remove.append(path)
            else:
                survivors.append(path)

    # --- decide what to relink (read-only) -----------------------------------
    relink_plan: list[tuple[Path, str]] = []
    for path in survivors:
        raw = path.read_text(encoding="utf-8")
        dangling = dangling_link_targets(raw, vault_root)
        if not dangling:
            continue
        new_raw = _demote_unresolvable_links(raw, vault_root)
        if new_raw != raw:
            relink_plan.append((path, new_raw))
            report.relinked.append(path)
            report.dangling_stripped += len(dangling)

    if not apply:
        # Dry-run: report only. The telemetry counter tracks links actually
        # demoted on a write — counting hypotheticals here would inflate it
        # (and double-count a report-then-apply sequence).
        return report

    # --- apply ---------------------------------------------------------------
    report.backup_path = _full_snapshot(vault_root, backup_dir)
    log.info("wiki cleanup: snapshot -> %s", report.backup_path)

    for path in to_remove:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - defensive
            log.error("wiki cleanup: failed to remove %s - %s", path, exc)

    for path, new_raw in relink_plan:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new_raw, encoding="utf-8", newline="")
        tmp.replace(path)

    # Purge FTS rows for removed files so search stops returning ghosts.
    if report.removed_paths:
        if writer is None:
            from .atomic_writer import AtomicWriter

            writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
        writer.forget_paths(report.removed_paths)

    telemetry.inc("wiki_links_refused_dangling", report.dangling_stripped)
    return report


__all__ = [
    "CleanupReport",
    "LEAK_RELPATH",
    "clean_vault",
    "dangling_link_targets",
    "is_truncated_body",
]
