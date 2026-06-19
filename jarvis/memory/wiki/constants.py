"""Wire-format vocabulary for the two-stage conversation curator.

Single source of truth (five-layer-enum discipline,
``docs/anti-drift-three-layer.md``): the Python tuples here -> the SQL CHECK
constraints in ``jarvis/memory/migrations/0005_wiki_candidate_journal.sql`` ->
the typing ``Literal`` aliases below. ``tests/unit/memory/wiki/
test_curator_decision_parity.py`` pins all layers against each other.

These strings surface to the UI only as telemetry counter names
(``wiki_consolidator_<decision>``), so there is deliberately no TypeScript
layer for them; if a future UI ever renders the decision/status strings
directly, add the TS mirror + extend the parity test (BUG-008 defense).
"""
from __future__ import annotations

from typing import Literal

# Lifecycle of one candidate fact in the journal.
CANDIDATE_STATUSES: tuple[str, ...] = ("pending", "consolidated", "rejected", "skipped")
CandidateStatus = Literal["pending", "consolidated", "rejected", "skipped"]

# Stage-2 judge decision per candidate.
CURATOR_DECISIONS: tuple[str, ...] = ("add", "update", "noop", "invalidate")
CuratorDecision = Literal["add", "update", "noop", "invalidate"]

# Runtime drift assertions (mirror the jarvis/memory/constants.py pattern):
# importing this module with a drifted tuple/Literal pair fails immediately.
assert set(CANDIDATE_STATUSES) == set(CandidateStatus.__args__)  # type: ignore[attr-defined]
assert set(CURATOR_DECISIONS) == set(CuratorDecision.__args__)  # type: ignore[attr-defined]

__all__ = [
    "CANDIDATE_STATUSES",
    "CURATOR_DECISIONS",
    "CandidateStatus",
    "CuratorDecision",
]
