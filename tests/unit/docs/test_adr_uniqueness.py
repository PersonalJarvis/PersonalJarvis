"""ADR-uniqueness regression test.

Walks ``docs/adr/`` and asserts every ADR number appears in at most one
filename. Legacy duplicates (0009, 0010, 0014) predate this test and are
allow-listed below so the test enforces the contract from this point
forward without forcing a renumber sweep that would invalidate every
existing inbound link.

Adding a new ADR to the duplicates list is a deliberate, reviewable
act — the diff that adds an entry is what catches an accidental
collision.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path



_ADR_DIR = Path(__file__).resolve().parents[3] / "docs" / "adr"
_NUMBER_RE = re.compile(r"^(\d{4})-")


# Legacy duplicates that pre-date this test. Adding to this list MUST
# be accompanied by an ADR explaining why a renumber was not feasible
# (typically: published inbound links from CLAUDE.md, BUGS.md, or other
# ADRs). The Wave-4 doc flags these for a follow-up renumber sweep.
_ALLOWED_DUPLICATES: frozenset[str] = frozenset({
    "0009",  # awareness-architecture + self-healing-worker-critic
    "0010",  # output-filter-pattern-based + window-focus-watcher-msgwait
    "0014",  # flash-brain-suppress-if-fast + memory-trigger-contract
})


def _collect_adr_numbers() -> dict[str, list[str]]:
    """Map each 4-digit prefix to the list of files using it."""
    by_number: dict[str, list[str]] = defaultdict(list)
    for path in sorted(_ADR_DIR.glob("*.md")):
        match = _NUMBER_RE.match(path.name)
        if not match:
            continue
        by_number[match.group(1)].append(path.name)
    return by_number


def test_adr_files_have_4_digit_prefix() -> None:
    """Every Markdown file in docs/adr/ starts with NNNN-."""
    bad: list[str] = []
    for path in sorted(_ADR_DIR.glob("*.md")):
        if not _NUMBER_RE.match(path.name):
            bad.append(path.name)
    assert not bad, (
        f"ADR files without 4-digit prefix: {bad}. "
        "Rename or move them out of docs/adr/."
    )


def test_no_new_adr_number_collisions() -> None:
    """Every ADR number is unique except for the explicit legacy
    allow-list. New collisions fail the test until renumbered.
    """
    by_number = _collect_adr_numbers()
    duplicates = {
        num: files for num, files in by_number.items() if len(files) > 1
    }
    unexpected = {
        num: files
        for num, files in duplicates.items()
        if num not in _ALLOWED_DUPLICATES
    }
    assert not unexpected, (
        f"New ADR number collision(s) detected: {unexpected}. "
        f"Either renumber the offending file(s) or add an entry to "
        f"_ALLOWED_DUPLICATES in this test alongside an ADR explaining why."
    )
