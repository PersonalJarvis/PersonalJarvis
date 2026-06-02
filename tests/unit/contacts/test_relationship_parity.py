"""Parity guard for the contact ``relationship`` enum (five-layer anti-drift).

The Python source of truth (``jarvis/contacts/schema.py``) and the TypeScript
mirror (``frontend/src/views/contacts/constants.ts``) must never drift apart —
that is the BUG-008 multi-layer-enum-drift defense (AP-4). This test regex-reads
the TS array and compares it to the Python tuple.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.contacts.schema import RELATIONSHIPS

_REPO = Path(__file__).resolve().parents[3]
_CONSTANTS_TS = (
    _REPO / "jarvis/ui/web/frontend/src/views/contacts/constants.ts"
)


def _ts_relationships() -> set[str]:
    text = _CONSTANTS_TS.read_text(encoding="utf-8")
    block = re.search(r"RELATIONSHIPS\s*=\s*\[(.*?)\]\s*as const", text, re.DOTALL)
    assert block, "could not find RELATIONSHIPS array in contacts/constants.ts"
    return set(re.findall(r'"([a-z]+)"', block.group(1)))


def test_ts_relationships_match_python_tuple() -> None:
    assert _ts_relationships() == set(RELATIONSHIPS)


def test_ts_has_a_label_for_every_relationship() -> None:
    """Every value needs a UI label entry (Layer 5) — no unlabeled enum value."""
    text = _CONSTANTS_TS.read_text(encoding="utf-8")
    labels = set(re.findall(r"(\w+):\s*\"[^\"]+\"", text))
    for value in RELATIONSHIPS:
        assert value in labels, f"missing RELATIONSHIP_LABELS entry for {value!r}"
