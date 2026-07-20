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

# Evidence basis of one candidate fact ("explicit" = user asserted it,
# "behavioral" = first-person lived-experience report, "inferred" = reserved
# for a future cross-session reflection pass — nothing emits it yet). SQL
# CHECK lives in 0009_wiki_candidate_basis.sql. Like the decisions, these
# strings never render in the UI, so there is no TypeScript layer; add the TS
# mirror + extend the parity test before ever surfacing them (BUG-008).
FACT_BASES: tuple[str, ...] = ("explicit", "behavioral", "inferred")
FactBasis = Literal["explicit", "behavioral", "inferred"]

# Default personal-salience score for candidates without one. Mirrored by the
# SQL DEFAULT in 0009_wiki_candidate_basis.sql — keep both in sync.
DEFAULT_SALIENCE = 3

# The literal bullet suffix marking a behavioral/inferred page line. The
# consolidator prompt teaches it, the preservation guard exempts exactly it,
# and the recurate prompt preserves it — prompt.py/recurate.py assert their
# prompt text still carries this exact token (drift guard).
INFERRED_MARKER = "*(inferred)*"

# Runtime drift assertions (mirror the jarvis/memory/constants.py pattern):
# importing this module with a drifted tuple/Literal pair fails immediately.
assert set(CANDIDATE_STATUSES) == set(CandidateStatus.__args__)  # type: ignore[attr-defined]
assert set(CURATOR_DECISIONS) == set(CuratorDecision.__args__)  # type: ignore[attr-defined]
assert set(FACT_BASES) == set(FactBasis.__args__)  # type: ignore[attr-defined]

__all__ = [
    "CANDIDATE_STATUSES",
    "CURATOR_DECISIONS",
    "DEFAULT_SALIENCE",
    "FACT_BASES",
    "INFERRED_MARKER",
    "CandidateStatus",
    "CuratorDecision",
    "FactBasis",
]
