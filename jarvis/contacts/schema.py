"""Single source of truth for the contact ``relationship`` vocabulary.

Why this module exists
======================

``relationship`` is an enum-like value that lives in five places at once
(see ``docs/anti-drift-three-layer.md``):

1. **Producer** — :mod:`jarvis.contacts.store` writes it into the ``<slug>.md``
   frontmatter; it imports the symbolic constants from here, never a raw string.
2. **Persistence** — the YAML frontmatter key ``relationship`` (the contact
   ``.md`` file; there is no SQL layer for contacts).
3. **Pydantic** — :mod:`jarvis.ui.web.contacts_routes` types the field with the
   :data:`Relationship` ``Literal`` so an unknown value is a 422/400, not a
   silent write.
4. **TypeScript** — ``frontend/src/views/contacts/constants.ts`` mirrors the
   tuple in ``RELATIONSHIPS``.
5. **UI label** — ``RELATIONSHIP_LABELS`` in the same TS file + the
   ``contacts.rel.*`` i18n keys.

A parity test (``tests/unit/contacts/test_relationship_parity.py``) asserts the
Python tuple equals the TS array, and the import-time assertion below fails the
process at boot if the ``Literal`` ever drifts from the tuple — the cheap
insurance against the BUG-008 multi-layer-enum-drift class (AP-4).
"""
from __future__ import annotations

from typing import Literal, get_args

# --- Layer 0: symbolic constants (import these at call sites, never the string).
REL_FAMILY = "family"
REL_FRIEND = "friend"
REL_COLLEAGUE = "colleague"
REL_PARTNER = "partner"
REL_ACQUAINTANCE = "acquaintance"
REL_OTHER = "other"

#: All accepted ``relationship`` values. Order is stable for the UI select and
#: for tests asserting against ``typing.get_args(Relationship)``.
RELATIONSHIPS: tuple[str, ...] = (
    REL_FAMILY,
    REL_FRIEND,
    REL_COLLEAGUE,
    REL_PARTNER,
    REL_ACQUAINTANCE,
    REL_OTHER,
)

#: Sensible default when none is supplied but a concrete value is required.
DEFAULT_RELATIONSHIP = REL_OTHER

#: Pydantic/typing layer. MUST stay in lockstep with :data:`RELATIONSHIPS`.
Relationship = Literal[
    "family", "friend", "colleague", "partner", "acquaintance", "other"
]

# Runtime guard: fail loudly at import time if the Literal drifts from the tuple.
assert set(get_args(Relationship)) == set(RELATIONSHIPS), (
    "Relationship Literal drifted from RELATIONSHIPS — update both layers "
    "(see docs/anti-drift-three-layer.md)."
)


def normalize_relationship(value: str | None) -> str | None:
    """Normalise a relationship string to its canonical form.

    - ``None`` or blank → ``None`` (the field is optional).
    - A known value (case-insensitive, surrounding whitespace ignored) →
      the canonical lowercase form.
    - Anything else → :class:`ValueError` (callers map this to a 400).
    """
    if value is None:
        return None
    v = value.strip().lower()
    if not v:
        return None
    if v not in set(RELATIONSHIPS):
        raise ValueError(
            f"Unknown relationship {value!r}. Valid: {', '.join(RELATIONSHIPS)}."
        )
    return v


__all__ = [
    "DEFAULT_RELATIONSHIP",
    "REL_ACQUAINTANCE",
    "REL_COLLEAGUE",
    "REL_FAMILY",
    "REL_FRIEND",
    "REL_OTHER",
    "REL_PARTNER",
    "RELATIONSHIPS",
    "Relationship",
    "normalize_relationship",
]
