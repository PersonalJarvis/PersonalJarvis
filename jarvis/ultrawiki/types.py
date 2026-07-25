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
    write scheduling logic themselves."""

    backfill: bool = True
    incremental: IncrementalMode = IncrementalMode.NONE
    deletes: bool = False


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
