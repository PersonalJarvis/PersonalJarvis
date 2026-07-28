"""UltraWiki identity resolution — the decision logic, free of any database.

Design doc 05 (D-10). The single most dangerous failure in a personal memory
is a **wrong merge**: fuse two people into one entity and every answer about
either is silently wrong, discovered months later. A wrong *split* is annoying
and repairable; a wrong merge is poison. The whole module exists to make that
asymmetry structural rather than a matter of tuning.

Three tiers, and only the first one is ever applied without asking:

1. :attr:`MatchTier.DETERMINISTIC` — the two identities share a **unique**
   identifier: the same e-mail address, the same phone number, the same
   address-book record. Merged automatically.
2. :attr:`MatchTier.PROBABLE` — name similarity, a nickname pattern, an exact
   *name* collision. Proposed only: the pair lands in the confirmation queue
   and both identities keep answering separately until a human decides.
3. :attr:`MatchTier.WEAK` — below the proposal threshold. Nothing happens at
   all; the queue stays short by design.

**A name never auto-merges, however close it looks.** That is not caution for
its own sake: speech-to-text produces near-name variants of one topic
("agentic-i", "gentic-ide"), and normalization aggressive enough to fuse
those would equally fuse two different people who share a name. So name
normalization here stays deliberately conservative — Unicode NFC, whitespace
collapse, edge punctuation, case folding, and nothing else. "Ultra Wiki",
"ultra-wiki" and "UltraWiki" therefore remain three keys that get *proposed*
to each other, never silently welded together.

Everything in this module is pure, deterministic, stdlib-only and offline: no
model, no network, no clock, no database. The SQL side lives in
``jarvis/ultrawiki/identity_store.py``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any

__all__ = [
    "DETERMINISTIC_KINDS",
    "FUZZY_CANDIDATE_LIMIT",
    "LEN_WINDOW",
    "MAX_NAME_CHARS",
    "MAX_PROPOSALS",
    "MERGEABLE_TIERS",
    "MIN_NAME_CHARS",
    "MIN_NICKNAME_CHARS",
    "MIN_PHONE_DIGITS",
    "NICKNAME_MAX_RATIO",
    "NICKNAME_SCORE",
    "PREFIX_BLOCK_CHARS",
    "PROPOSE_THRESHOLD",
    "EntityKind",
    "IdentifierKind",
    "IdentityError",
    "MatchEvidence",
    "MatchTier",
    "QueueStatus",
    "Resolution",
    "ResolutionKind",
    "could_match",
    "escape_like",
    "name_similarity",
    "nickname_score",
    "normalize_contact_slug",
    "normalize_email",
    "normalize_handle",
    "normalize_identifier",
    "normalize_name",
    "normalize_phone",
    "pair_key",
    "tier_for_score",
]


class IdentityError(RuntimeError):
    """Raised when an identity operation is refused (never for a bad match).

    A refusal means the *operation* is impossible — an unknown queue id, a
    merge that was already undone, an out-of-order unmerge. Uncertainty about
    a match is never an error; it is a queue row.
    """


# ---------------------------------------------------------------------------
# Canonical value sets (five-layer drift rule, AP-4/BUG-008)
# ---------------------------------------------------------------------------


class EntityKind(StrEnum):
    """What an entity *is*. Identity resolution treats every kind the same;
    the kind only scopes lookups and the People view."""

    PERSON = "person"
    PLACE = "place"
    ORG = "org"
    PROJECT = "project"
    TOPIC = "topic"


class IdentifierKind(StrEnum):
    """One raw handle mapped onto an entity.

    ``CONTACT`` is the slug of a record in the Jarvis contacts store — the
    address book the user curates by hand, and therefore an identifier as
    unique as an e-mail address.
    """

    EMAIL = "email"
    PHONE = "phone"
    CONTACT = "contact"
    HANDLE = "handle"
    NAME = "name"


#: The kinds unique enough that an exact match may merge two entities without
#: asking (design doc 05, tier 1). ``HANDLE`` is deliberately NOT here: the
#: same handle on two platforms is two different people, and the namespace
#: prefix is only as trustworthy as the connector that supplied it.
DETERMINISTIC_KINDS: frozenset[IdentifierKind] = frozenset(
    {IdentifierKind.EMAIL, IdentifierKind.PHONE, IdentifierKind.CONTACT}
)


class MatchTier(StrEnum):
    """How strong the evidence linking two identities is."""

    DETERMINISTIC = "deterministic"
    PROBABLE = "probable"
    WEAK = "weak"


#: The tiers a merge row may ever carry. ``WEAK`` is structurally excluded:
#: below the proposal threshold nothing is written at all, so a weak merge
#: cannot exist and the SQL CHECK says so.
MERGEABLE_TIERS: frozenset[MatchTier] = frozenset(
    {MatchTier.DETERMINISTIC, MatchTier.PROBABLE}
)


class QueueStatus(StrEnum):
    """State of one confirmation-queue row.

    ``REJECTED`` is permanent by design (anti-confirmation-fatigue): once the
    user has said "these are two different people", re-observing the same weak
    evidence must never ask again.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ResolutionKind(StrEnum):
    """How one observation was resolved onto an entity.

    Distinct from :class:`MatchTier`, which grades the evidence between a
    *pair* of entities. ``AMBIGUOUS`` is the honest refusal: the observation
    pointed at several entities at once, so nothing was linked and the
    colliding pairs were proposed instead.
    """

    DETERMINISTIC = "deterministic"
    NAME_ANCHOR = "name_anchor"
    CREATED = "created"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


# ---------------------------------------------------------------------------
# Tuning constants — every one of them is a policy, not a magic number
# ---------------------------------------------------------------------------

#: Length bounds of a usable name. Below the floor a "name" is an initial or
#: stray punctuation; above the ceiling an extractor handed back a sentence.
#: Kept in lockstep with ``projection.MIN_LABEL_CHARS`` / ``MAX_LABEL_CHARS``
#: by ``tests/unit/ultrawiki/test_identity.py``.
MIN_NAME_CHARS = 2
MAX_NAME_CHARS = 80

#: A phone number shorter than this is a short code or a fragment, not a
#: globally unique identifier — it normalizes to ``None`` and therefore can
#: never merge anything.
MIN_PHONE_DIGITS = 6

#: At or above this similarity a pair is PROPOSED; below it nothing happens.
#: Calibrated against two real families of near-names: speech-to-text variants
#: of one topic ("agentic-i" ~ "gentic-ide" = 0.84) must be proposed, while
#: two distinct people sharing a surname ("john smith" ~ "jane smith" = 0.80)
#: must not even reach the queue.
PROPOSE_THRESHOLD = 0.82

#: Score assigned to a nickname/abbreviation hit ("viki" inside "viktoria").
#: Deliberately just above the proposal threshold: it is enough to ask, never
#: enough to act.
NICKNAME_SCORE = 0.84

#: A nickname candidate must be at least this long and the token it abbreviates
#: at most this many times longer — otherwise "ana" would propose itself into
#: half the address book.
MIN_NICKNAME_CHARS = 4
NICKNAME_MAX_RATIO = 3

#: Candidate blocking. A fuzzy partner is only ever looked for among names of
#: a similar LENGTH or sharing the leading characters; both halves are applied
#: identically in SQL and in :func:`could_match`, so the two layers cannot
#: disagree about what a candidate is.
LEN_WINDOW = 4
PREFIX_BLOCK_CHARS = 3

#: Hard caps so one observation can never flood the queue or the CPU.
FUZZY_CANDIDATE_LIMIT = 400
MAX_PROPOSALS = 5


_WS_RE = re.compile(r"\s+")
_NON_DIGIT_RE = re.compile(r"\D")
#: Dependency-free e-mail shape check (the base install carries no validator
#: library) — the same pragmatic rule the contacts store applies.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
#: Trimmed from both ENDS of a name only. Internal punctuation is preserved on
#: purpose: collapsing "ultra-wiki" onto "ultra wiki" would be an implicit
#: merge on name similarity, which is exactly what this module refuses to do.
_EDGE_PUNCTUATION = " \t\r\n.,;:!?-–—_'\"()[]{}<>/\\|*@#"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_name(raw: object) -> str | None:
    """Canonical lookup key for a name, or ``None`` when it is unusable.

    NFC is load-bearing rather than cosmetic: macOS hands text back
    decomposed, so without it the same name becomes two entities.
    """
    if not isinstance(raw, str):
        return None
    collapsed = _WS_RE.sub(" ", raw).strip()
    text = unicodedata.normalize("NFC", collapsed).strip(_EDGE_PUNCTUATION)
    text = _WS_RE.sub(" ", text).strip()
    if not MIN_NAME_CHARS <= len(text) <= MAX_NAME_CHARS:
        return None
    return text.casefold()


def normalize_email(raw: object) -> str | None:
    """Lower-cased address without a ``mailto:`` wrapper, or ``None``."""
    if not isinstance(raw, str):
        return None
    text = raw.strip().strip("<>").strip().lower()
    if text.startswith("mailto:"):
        text = text[len("mailto:") :].strip()
    return text if _EMAIL_RE.match(text) else None


def normalize_phone(raw: object) -> str | None:
    """Best-effort E.164 key, mirroring ``jarvis.contacts.store``.

    ``"+49 151 2345-6789"`` and ``"0049 151 23456789"`` collapse onto the same
    key, which is what makes "same phone number" a deterministic match across
    two sources that formatted it differently. Numbers with fewer than
    :data:`MIN_PHONE_DIGITS` digits are refused rather than trusted.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    digits = _NON_DIGIT_RE.sub("", text)
    if text.startswith("+"):
        normalized = "+" + digits
    elif digits.startswith("00"):
        normalized = "+" + digits[2:]
    else:
        normalized = digits
    if len(_NON_DIGIT_RE.sub("", normalized)) < MIN_PHONE_DIGITS:
        return None
    return normalized


def normalize_handle(raw: object) -> str | None:
    """``"Slack:@RL"`` → ``"slack:rl"``; a bare handle keeps its own name."""
    if not isinstance(raw, str):
        return None
    text = _WS_RE.sub("", raw).strip().lower().lstrip("@")
    if ":" in text:
        namespace, _, rest = text.partition(":")
        text = f"{namespace.strip()}:{rest.strip().lstrip('@')}"
        if not namespace.strip() or not rest.strip().lstrip("@"):
            return None
    return text if len(text) >= 2 else None


def normalize_contact_slug(raw: object) -> str | None:
    """The address-book slug, lower-cased and whitespace-free."""
    if not isinstance(raw, str):
        return None
    text = _WS_RE.sub("", raw).strip().lower()
    return text or None


_NORMALIZERS: dict[IdentifierKind, Any] = {
    IdentifierKind.EMAIL: normalize_email,
    IdentifierKind.PHONE: normalize_phone,
    IdentifierKind.CONTACT: normalize_contact_slug,
    IdentifierKind.HANDLE: normalize_handle,
    IdentifierKind.NAME: normalize_name,
}


def normalize_identifier(kind: IdentifierKind | str, raw: object) -> str | None:
    """Canonical stored value for one identifier, or ``None`` to drop it."""
    try:
        resolved = IdentifierKind(kind)
    except ValueError:
        return None
    return _NORMALIZERS[resolved](raw)


# ---------------------------------------------------------------------------
# Similarity — deterministic, stdlib only, no model anywhere near it
# ---------------------------------------------------------------------------


def _token_sorted(name: str) -> str:
    return " ".join(sorted(name.split()))


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(char in it for char in short)


def nickname_score(a: str, b: str) -> float:
    """:data:`NICKNAME_SCORE` when one name abbreviates a token of the other.

    "Viki" is not similar to "Viktoria Novak" by any string metric, yet it is
    the canonical example of a probable match. The rule is deliberately narrow:
    the short form must be a single word, start on the same character, be a
    subsequence of one token of the long form, and not be dwarfed by it.
    """
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < MIN_NICKNAME_CHARS or " " in short or short == long:
        return 0.0
    for token in long.split():
        if not len(short) < len(token) <= NICKNAME_MAX_RATIO * len(short):
            continue
        if token[0] != short[0]:
            continue
        if _is_subsequence(short, token):
            return NICKNAME_SCORE
    return 0.0


def name_similarity(a: str, b: str) -> float:
    """0..1 similarity of two ALREADY NORMALIZED names.

    Three deterministic views, best one wins: raw character similarity, the
    same over token-sorted forms (so "novak viktoria" matches "viktoria
    novak"), and the nickname rule.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    best = SequenceMatcher(None, a, b).ratio()
    sorted_a, sorted_b = _token_sorted(a), _token_sorted(b)
    if (sorted_a, sorted_b) != (a, b):
        best = max(best, SequenceMatcher(None, sorted_a, sorted_b).ratio())
    return max(best, nickname_score(a, b))


def could_match(a: str, b: str) -> bool:
    """Cheap O(len) gate applied before the quadratic ratio.

    The exact predicate the candidate SQL uses, so a partner the database
    returns is never discarded by a rule the database did not know about.
    """
    if not a or not b:
        return False
    if abs(len(a) - len(b)) <= LEN_WINDOW:
        return True
    prefix = a[:PREFIX_BLOCK_CHARS]
    return len(prefix) == PREFIX_BLOCK_CHARS and b.startswith(prefix)


def tier_for_score(score: float) -> MatchTier:
    """Map a similarity score onto its tier. Never returns DETERMINISTIC —
    a score can only ever propose, never decide."""
    return MatchTier.PROBABLE if score >= PROPOSE_THRESHOLD else MatchTier.WEAK


def pair_key(left_id: int, right_id: int) -> str:
    """Order-independent key of an entity pair (one open proposal per pair)."""
    low, high = sorted((int(left_id), int(right_id)))
    return f"{low}:{high}"


def escape_like(text: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` for a ``LIKE ... ESCAPE '\\'`` pattern."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    """One reason two identities were linked — or proposed.

    Persisted verbatim into ``uw_merge_log.evidence_json`` and
    ``uw_confirm_queue.evidence_json``, because "why did this merge happen"
    must still be answerable months later, by a human, without the code.
    """

    tier: MatchTier
    kind: str
    value: str
    score: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": str(self.tier),
            "kind": self.kind,
            "value": self.value,
            "score": round(float(self.score), 4),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MatchEvidence:
        return cls(
            tier=MatchTier(str(payload.get("tier", MatchTier.PROBABLE))),
            kind=str(payload.get("kind", "")),
            value=str(payload.get("value", "")),
            score=float(payload.get("score", 0.0) or 0.0),
        )


@dataclass(frozen=True, slots=True)
class Resolution:
    """What resolving one observation did.

    ``entity_id`` is ``None`` exactly when nothing was linked: either the
    evidence was ambiguous (``kind == AMBIGUOUS``, the colliding entities are
    in ``ambiguous``) or nothing matched and creation was not requested.
    """

    entity_id: int | None
    kind: ResolutionKind
    created: bool = False
    merged: tuple[int, ...] = ()  # uw_merge_log ids written by this call
    queued: tuple[int, ...] = ()  # uw_confirm_queue ids created/refreshed
    ambiguous: tuple[int, ...] = ()
    evidence: tuple[MatchEvidence, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "kind": str(self.kind),
            "created": self.created,
            "merged": list(self.merged),
            "queued": list(self.queued),
            "ambiguous": list(self.ambiguous),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class IdentifierResult:
    """Outcome of attaching one identifier to an entity."""

    entity_id: int | None
    identifier_id: int | None = None
    created: bool = False
    merged: tuple[int, ...] = ()
    queued: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class SeedReport:
    """What one contacts seeding pass did (idempotent, so a second pass is
    all ``linked`` and zero ``created``)."""

    created: int = 0
    linked: int = 0
    identifiers_added: int = 0
    merged: int = 0
    queued: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "linked": self.linked,
            "identifiers_added": self.identifiers_added,
            "merged": self.merged,
            "queued": self.queued,
            "skipped": self.skipped,
        }
