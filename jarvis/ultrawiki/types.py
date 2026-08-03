"""UltraWiki shared contracts: states, raw items, connector + provider protocols.

This module is the spine every UltraWiki component builds against. It is
deliberately dependency-free (stdlib only) so connectors, the store, the
pipeline, and tests can all import it without pulling anything heavy.

Five-layer drift discipline (AP-4 / BUG-008): the state and consent value sets
below are the CANONICAL lists. SQL CHECK constraints (schema.sql), Pydantic
route models, the TypeScript union in the frontend, and UI labels must all be
derived from / parity-tested against these enums — never retyped by hand.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ItemState",
    "DocType",
    "ConsentState",
    "ExploreReason",
    "WordSearchStatus",
    "IncrementalMode",
    "AuthKind",
    "RawItem",
    "ConnectorCapabilities",
    "ConnectorContext",
    "UWConnector",
    "EmbeddingBackend",
    "SearchResult",
    "PipelineCounts",
    "STATE_ORDER",
    "content_hash_for",
]


class ItemState(StrEnum):
    """Staged-pipeline state of one raw item (design doc 02).

    The value is the LAST COMPLETED stage; workers advance items whose state
    precedes their target stage. ``failed`` is the dead-letter terminal after
    retries are exhausted; retryable errors keep the last good state and set
    ``next_retry_at`` instead.
    """

    CAPTURED = "captured"
    KEYWORD_INDEXED = "keyword_indexed"
    EMBEDDED = "embedded"
    DISTILLED = "distilled"
    FAILED = "failed"


#: Forward order of the good states; ``FAILED`` sits outside the ladder.
STATE_ORDER: tuple[ItemState, ...] = (
    ItemState.CAPTURED,
    ItemState.KEYWORD_INDEXED,
    ItemState.EMBEDDED,
    ItemState.DISTILLED,
)


class DocType(StrEnum):
    """Kind of derived document stored in ``uw_documents``."""

    RAW = "raw"  # lightly normalized full text (pre-distillation embedding basis)
    SUMMARY = "summary"  # distilled normalized document (primary semantic doc)
    BURST = "burst"  # high-signal fragment embedded with parent context


class ConsentState(StrEnum):
    """Per-source user consent. No connector pulls a single byte before
    the source is explicitly approved in the UI/CLI (maintainer mandate)."""

    PENDING = "pending"
    APPROVED = "approved"
    REVOKED = "revoked"


class ExploreReason(StrEnum):
    """Why the Explore view has nothing to show — or that it has.

    "The knowledge base looks empty" has FOUR different causes and the user
    cannot tell them apart by looking: no source configured, a source that
    never imported, imported items nothing has distilled yet, or distilled
    items that named no entity. A previous forensic found exactly one of them
    (consent granted but never fetched) undiagnosed for days behind a blank
    screen, so the server names the cause instead of leaving the surface to
    guess from counts.

    Five-layer discipline (AP-4 / BUG-008): this is the CANONICAL list. The
    TypeScript union in ``lib/ultrawikiApi.ts`` is parity-tested against it.
    """

    OK = "ok"
    NO_SOURCES = "no_sources"
    NOTHING_IMPORTED = "nothing_imported"
    NOTHING_DISTILLED = "nothing_distilled"
    NO_ENTITIES = "no_entities"


class WordSearchStatus(StrEnum):
    """Why a word search returned what it returned.

    A word search can come back empty for four unrelated reasons, and the
    user cannot tell them apart by looking at an empty list: the store holds
    nothing yet, the word is not in this corpus at all, the word IS known but
    nothing it points at survived ranking, or the lexicon that turns a word
    into its neighbourhood has not been built (no embedding provider, or the
    background pass has not reached it). Each one has a different next step,
    so the server names the cause rather than leaving the surface to guess.

    ``OK`` means hits were returned. ``NEIGHBOURS_UNAVAILABLE`` is NOT a
    failure: the search still ran on the word itself and may well have hits —
    it only says the meaning-neighbourhood could not be computed.

    Five-layer discipline (AP-4 / BUG-008): this is the CANONICAL list. The
    TypeScript union in ``lib/ultrawikiApi.ts`` is parity-tested against it.
    """

    OK = "ok"
    EMPTY_INDEX = "empty_index"
    UNKNOWN_WORD = "unknown_word"
    NO_MATCHES = "no_matches"
    NEIGHBOURS_UNAVAILABLE = "neighbours_unavailable"


class IncrementalMode(StrEnum):
    """How a connector keeps a source fresh after backfill."""

    PUSH = "push"  # platform pushes events (webhook/socket)
    WATCH = "watch"  # local file watcher
    CURSOR = "cursor"  # cursor/token polling
    NONE = "none"  # backfill/re-import only (e.g. export files)


class AuthKind(StrEnum):
    """What a connector needs before it can run."""

    NONE = "none"
    LOCAL_PATH = "local-path"
    EXPORT_FILE = "export-file"
    OAUTH2 = "oauth2"
    APIKEY = "apikey"


@dataclass(frozen=True, slots=True)
class RawItem:
    """The ONLY thing a connector may yield (design doc 02).

    ``external_id`` must be stable per source (idempotent re-runs upsert on
    ``UNIQUE (source_id, external_id)``); ``permalink`` is mandatory from item
    one — evidence must always deep-link back to where it lives.
    ``timestamp_utc`` is an ISO-8601 UTC string ("YYYY-MM-DDTHH:MM:SSZ" or with
    offset); connectors resolve source-local times before yielding.
    """

    external_id: str
    body: str
    permalink: str
    timestamp_utc: str
    title: str = ""
    thread_key: str = ""
    author_raw: str = ""
    deleted: bool = False  # tombstone signal from reconcile-capable connectors
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConnectorCapabilities:
    """Declaration that drives the scheduler; connector authors never
    write scheduling logic themselves.

    ``refresh_interval_s`` asks the runtime to run the cheapest available
    incremental read. ``reconcile_interval_s`` asks for a from-scratch walk
    that repairs missed edits and, when ``deletes`` is true, tombstones
    source-side deletions. ``None`` means manual-only for that lane.
    """

    backfill: bool = True
    incremental: IncrementalMode = IncrementalMode.NONE
    deletes: bool = False
    refresh_interval_s: float | None = None
    reconcile_interval_s: float | None = None


@dataclass(slots=True)
class ConnectorContext:
    """Everything a connector receives. Deliberately NO database handle and
    NO provider handle: connectors are pure I/O (design doc 02, hard rule 1).

    ``config`` carries the per-source settings the user entered (paths,
    folder filters, ...). ``secret_get`` resolves a credential by slot name
    through the Jarvis secret chain without exposing the store itself.
    """

    source_id: str
    config: dict[str, Any] = field(default_factory=dict)
    secret_get: Any = None  # Callable[[str], str | None]; Any avoids typing dep


@runtime_checkable
class UWConnector(Protocol):
    """The connector contract (design doc 02).

    Yields ``RawItem`` streams and nothing else — never touches the store,
    never embeds, never calls an LLM. Third-party connectors register under
    the ``jarvis.uw_connector`` entry-point group and must not import
    ``jarvis.*`` at module level (structural typing keeps them decoupled);
    built-in connectors under ``jarvis/ultrawiki/connectors/`` are core code
    and may import freely.
    """

    id: str
    label: str
    auth: AuthKind
    capabilities: ConnectorCapabilities

    def backfill(
        self, ctx: ConnectorContext, checkpoint: str | None = None
    ) -> AsyncIterator[RawItem]: ...

    def incremental(
        self, ctx: ConnectorContext, cursor: str | None = None
    ) -> AsyncIterator[RawItem]: ...


@runtime_checkable
class EmbeddingBackend(Protocol):
    """One embedding slot implementation (local Ollama or a cloud provider).

    The slot is a ONE-TIME deliberate choice (decision D-3): unlike chat
    tiers there is NO silent cross-family fallback — mixing vector spaces
    would corrupt search. A dead backend pauses the embed stage honestly;
    keyword search keeps working.
    """

    name: str

    def ready(self) -> tuple[bool, str]:
        """(usable, honest_reason_if_not) — probes key/endpoint, never raises."""
        ...

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One fused hybrid-search hit with its citation.

    ``score`` is the ORDINAL fused rank score (RRF): it orders candidates
    against each other and can never say "nothing here is relevant".
    ``rerank_score`` is the ABSOLUTE 0-10 relevance grade from the rerank
    stage — the number an unsolicited surface gates on — and stays ``None``
    whenever that optional stage was skipped or failed. ``context`` carries
    the neighbouring evidence pulled back in after ranking (design doc 03,
    "context expansion"); empty when expansion was skipped or unavailable.

    **Two clocks, deliberately.** ``timestamp_utc`` is what this hit is ABOUT
    and is what the surface shows — for an event hit that is when the event
    happened, not when the message describing it was written.
    ``recorded_utc`` is when the underlying item entered the corpus, i.e. how
    old the RECORD is, and is the only stamp ranking may decay by. Conflating
    them punishes a note written yesterday about a dinner three years ago as
    if the note itself were three years old. Empty means "identical to
    ``timestamp_utc``", which is the case for every leg that reads items
    directly.
    """

    item_id: int
    source_id: str
    title: str
    snippet: str
    permalink: str
    timestamp_utc: str
    score: float
    matched_by: tuple[str, ...] = ()  # e.g. ("keyword", "vector")
    rerank_score: float | None = None  # absolute 0-10 grade, None = not reranked
    context: tuple[str, ...] = ()  # neighbouring sections/messages
    recorded_utc: str = ""  # when the ITEM was recorded; "" = same as timestamp_utc
    # -- passage provenance --------------------------------------------------
    # WHICH passage of the item answered (migration 0001: an item holds many
    # documents, one per chunk of its text). The vector leg has always known
    # this and threw it away, so a hit on page 40 of a file was reported as a
    # hit on "the file" and the reader had to search it by hand. ``None``
    # means the leg genuinely cannot say — a keyword hit before passage
    # localization, an event card, a third-party store.
    document_id: int | None = None
    chunk_index: int | None = None
    char_start: int | None = None
    char_end: int | None = None


@dataclass(frozen=True, slots=True)
class PipelineCounts:
    """Honest per-stage backlog counts for the import-progress surface."""

    captured: int = 0
    keyword_indexed: int = 0
    embedded: int = 0
    distilled: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return (
            self.captured
            + self.keyword_indexed
            + self.embedded
            + self.distilled
            + self.failed
        )


def content_hash_for(*parts: str) -> str:
    """Stable content hash used for idempotency and the distillation cache."""

    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()
