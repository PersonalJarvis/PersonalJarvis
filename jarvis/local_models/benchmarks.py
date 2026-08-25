"""Benchmarks and freshness for the curated shortlist: proven, new, or stale?

The setup assistant must prefer the best *proven* local models and never
pick a brand-new one as a default. "Proven" is decided here from two
sources, both tolerant of being unreachable:

* public benchmark pages (Artificial Analysis, LMArena, the Open LLM
  Leaderboard) reached through a caller-supplied ``search_fn`` (the
  key-free ``search_web`` tool in production) — the snippets are mined with
  tolerant regexes into ``{family, source, metric, value, url, seen_at}``
  rows;
* the Ollama library (``ollama_library.search_library``) for pulls and the
  last update of each family.

The result is cached at ``DATA_DIR/state/local_models_benchmarks.json`` for
seven days; the assistant refreshes it only inside a guided run or on an
explicit "Refresh shortlist". Offline, the cache or a curated-only table
(with a note saying so) is the answer — never an error.

:func:`label_for` is the pure labelling rule the proposal card renders and
the tests pin.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import inspect
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "BENCHMARK_FILE_NAME",
    "CACHE_TTL_DAYS",
    "LABELS",
    "BenchmarkRow",
    "BenchmarkTable",
    "LibraryFacts",
    "extract_rows",
    "family_of",
    "label_for",
    "load_cached",
    "parse_pulls",
    "parse_updated_days",
    "refresh_benchmarks",
]

BENCHMARK_FILE_NAME = "local_models_benchmarks.json"
CACHE_TTL_DAYS = 7

#: The three labels, in the order the card explains them.
LABELS: tuple[str, ...] = ("proven", "new_little_tested", "stale")

#: Library facts that make a family "proven" without a benchmark row.
PROVEN_MIN_PULLS = 100_000
#: A family updated more recently than this is "new" whatever the numbers say.
NEW_MAX_AGE_DAYS = 60

#: Query templates, one per source; ``{family}`` is the curated family name.
QUERY_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("artificial_analysis", "{family} Artificial Analysis intelligence index"),
    ("lmarena", "{family} LMArena text arena score"),
    ("open_llm_leaderboard", "{family} Open LLM Leaderboard average score"),
)

#: Result host → source id, so a snippet from LMArena found via the
#: Artificial-Analysis query is still filed under LMArena.
_SOURCE_HOSTS: tuple[tuple[str, str], ...] = (
    ("artificialanalysis.ai", "artificial_analysis"),
    ("lmarena.ai", "lmarena"),
    ("lmsys.org", "lmarena"),
    ("huggingface.co", "open_llm_leaderboard"),
)

#: (metric id, tolerant pattern). Values are the first capture group.
_METRIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "intelligence_index",
        re.compile(r"intelligence\s+index(?:\s+of)?\s*[:=]?\s*(\d{1,3}(?:\.\d+)?)", re.I),
    ),
    (
        "arena_score",
        re.compile(r"(?:arena|elo)\s*(?:score|rating)?\s*(?:of|[:=])?\s*(\d{3,4})\b", re.I),
    ),
    (
        "leaderboard_average",
        re.compile(
            r"(?:average|avg\.?)\s*(?:score)?\s*(?:of|[:=])?\s*(\d{1,3}(?:\.\d+)?)\s*%?", re.I
        ),
    ),
    (
        "mmlu",
        re.compile(r"MMLU(?:-Pro)?\s*(?:score)?\s*(?:of|[:=])?\s*(\d{1,3}(?:\.\d+)?)\s*%?", re.I),
    ),
    (
        "gpqa",
        re.compile(r"GPQA(?:\s+Diamond)?\s*(?:score)?\s*(?:of|[:=])?\s*(\d{1,3}(?:\.\d+)?)", re.I),
    ),
)

SearchFn = Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class BenchmarkRow:
    family: str
    source: str
    metric: str
    value: float
    url: str
    seen_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "source": self.source,
            "metric": self.metric,
            "value": self.value,
            "url": self.url,
            "seen_at": self.seen_at,
        }


@dataclass(frozen=True, slots=True)
class LibraryFacts:
    family: str
    pulls: int
    #: Days since the library's "Updated … ago"; ``None`` when unknown.
    updated_days: int | None

    def to_payload(self) -> dict[str, Any]:
        return {"family": self.family, "pulls": self.pulls, "updated_days": self.updated_days}


@dataclass(frozen=True, slots=True)
class BenchmarkTable:
    fetched_at: str
    rows: tuple[BenchmarkRow, ...]
    library: dict[str, LibraryFacts]
    #: ``live`` | ``cache`` | ``curated`` — where the numbers came from.
    source: str
    #: One sentence when the table is not live ("" otherwise).
    note: str = ""

    def rows_for(self, family: str) -> tuple[BenchmarkRow, ...]:
        return tuple(r for r in self.rows if r.family == family)

    def to_payload(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at,
            "source": self.source,
            "note": self.note,
            "rows": [r.to_payload() for r in self.rows],
            "library": {f: facts.to_payload() for f, facts in self.library.items()},
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, source: str | None = None) -> BenchmarkTable:
        rows: list[BenchmarkRow] = []
        for raw in payload.get("rows") or []:
            try:
                rows.append(
                    BenchmarkRow(
                        family=str(raw["family"]),
                        source=str(raw.get("source") or ""),
                        metric=str(raw.get("metric") or ""),
                        value=float(raw.get("value") or 0.0),
                        url=str(raw.get("url") or ""),
                        seen_at=str(raw.get("seen_at") or ""),
                    )
                )
            except (KeyError, TypeError, ValueError):
                log.debug("benchmarks: dropping malformed row %r", raw)
        library: dict[str, LibraryFacts] = {}
        for family, raw in (payload.get("library") or {}).items():
            try:
                updated = raw.get("updated_days")
                library[str(family)] = LibraryFacts(
                    family=str(family),
                    pulls=int(raw.get("pulls") or 0),
                    updated_days=None if updated is None else int(updated),
                )
            except (AttributeError, TypeError, ValueError):
                log.debug("benchmarks: dropping malformed library facts %r", raw)
        return cls(
            fetched_at=str(payload.get("fetched_at") or ""),
            rows=tuple(rows),
            library=library,
            source=source or str(payload.get("source") or "cache"),
            note=str(payload.get("note") or ""),
        )


# ── Small pure helpers ────────────────────────────────────────────────────


def family_of(model_id: str) -> str:
    """``qwen3.5:4b`` → ``qwen3.5``; ``hf.co/u/r:Q4`` keeps its path."""
    return (model_id or "").strip().partition(":")[0]


def _entry_id(entry: Any) -> str:
    """The model id of a curated entry or a bare id string."""
    return str(getattr(entry, "id", None) or entry or "")


def curated_families(entries: Iterable[Any] | None = None) -> tuple[str, ...]:
    """The distinct families of the curated shortlist, in list order."""
    if entries is None:
        from jarvis.brain.ollama_pull import RECOMMENDED_MODELS

        entries = tuple(RECOMMENDED_MODELS)
    seen: list[str] = []
    for entry in entries:
        family = family_of(_entry_id(entry))
        if family and family not in seen:
            seen.append(family)
    return tuple(seen)


_PULLS_FACTOR = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def parse_pulls(text: str) -> int:
    """``"17.2M"`` → ``17200000``; anything unreadable → ``0``."""
    match = re.fullmatch(r"\s*([\d.,]+)\s*([KMB]?)\s*", (text or "").upper())
    if not match:
        return 0
    try:
        return int(float(match.group(1).replace(",", "")) * _PULLS_FACTOR[match.group(2)])
    except ValueError:
        return 0


_UNIT_DAYS = {
    "second": 0,
    "minute": 0,
    "hour": 0,
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}


def parse_updated_days(text: str) -> int | None:
    """``"2 weeks ago"`` → ``14``, ``"yesterday"`` → ``1``; unknown → ``None``."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if t in ("yesterday",):
        return 1
    if t in ("today", "just now"):
        return 0
    match = re.match(r"(a|an|\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", t)
    if not match:
        return None
    count = 1 if match.group(1) in ("a", "an") else int(match.group(1))
    return count * _UNIT_DAYS[match.group(2)]


def _source_for(url: str, fallback: str) -> str:
    low = (url or "").lower()
    for host, source in _SOURCE_HOSTS:
        if host in low:
            return source
    return fallback


def extract_rows(
    family: str, results: Iterable[dict[str, Any]], *, source: str, seen_at: str
) -> list[BenchmarkRow]:
    """Mine benchmark numbers out of search snippets — tolerant, never raising.

    A snippet counts only when it names the family (case-insensitive,
    ``qwen3.5`` also matches ``Qwen 3.5``) AND carries a number one of the
    metric patterns understands. One row per (source, metric) per family.
    """
    needle = re.escape(family.lower()).replace(r"\.", r"\.?").replace(r"\-", r"[\s-]?")
    needle = re.sub(r"([a-z])(\d)", r"\1\\s?\2", needle)
    family_re = re.compile(needle, re.I)
    rows: dict[tuple[str, str], BenchmarkRow] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        text = f"{result.get('title') or ''} {result.get('snippet') or ''}"
        if not family_re.search(text):
            continue
        url = str(result.get("url") or "")
        row_source = _source_for(url, source)
        for metric, pattern in _METRIC_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            key = (row_source, metric)
            if key not in rows:
                rows[key] = BenchmarkRow(family, row_source, metric, value, url, seen_at)
    return list(rows.values())


# ── The labelling rule ────────────────────────────────────────────────────


def label_for(entry: Any, table: BenchmarkTable | None, today: _dt.date) -> str:
    """``proven`` | ``new_little_tested`` | ``stale`` for one curated entry.

    In order:

    1. ``stale`` — the curated list was last reviewed more than a year before
       ``today`` (nothing on it may be called proven any more).
    2. ``new_little_tested`` — the library updated the family less than
       :data:`NEW_MAX_AGE_DAYS` ago (a brand-new build is never a default,
       whatever last month's numbers said).
    3. ``proven`` — at least one benchmark row, or :data:`PROVEN_MIN_PULLS`
       pulls in the library.
    4. Otherwise ``new_little_tested`` (no row, no pull count to speak of).

    ``entry`` is a curated :class:`~jarvis.brain.ollama_pull.RecommendedModel`
    or a model id string; ``table`` may be ``None`` (curated-only mode).
    """
    from jarvis.brain.ollama_pull import CURATED_MAX_AGE_DAYS, CURATED_REVIEWED_ON

    if (today - CURATED_REVIEWED_ON).days > CURATED_MAX_AGE_DAYS:
        return "stale"
    family = family_of(_entry_id(entry))
    facts = table.library.get(family) if table is not None else None
    updated_days = facts.updated_days if facts is not None else None
    if updated_days is not None and updated_days < NEW_MAX_AGE_DAYS:
        return "new_little_tested"
    if table is not None and table.rows_for(family):
        return "proven"
    if facts is not None and facts.pulls >= PROVEN_MIN_PULLS:
        return "proven"
    return "new_little_tested"


# ── Cache ─────────────────────────────────────────────────────────────────


def cache_path() -> Path:
    from jarvis.core import config as cfg_mod  # lazy: DATA_DIR is monkeypatched by tests

    return Path(cfg_mod.DATA_DIR) / "state" / BENCHMARK_FILE_NAME


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> bool:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return True
    except OSError:
        log.warning("benchmarks: cache %s not written", path, exc_info=True)
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            log.debug("benchmarks: temporary cache cleanup failed", exc_info=True)


def load_cached(
    *, max_age_days: int | None = CACHE_TTL_DAYS, now: _dt.datetime | None = None
) -> BenchmarkTable | None:
    """The cached table when it exists and is younger than ``max_age_days``.

    ``max_age_days=None`` returns whatever is on disk, however old — the
    offline fallback prefers a stale table over none.
    """
    path = cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.info("benchmarks: cache %s unreadable", path, exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None
    table = BenchmarkTable.from_payload(payload, source="cache")
    if max_age_days is not None:
        fetched = _parse_iso(table.fetched_at)
        current = now or _dt.datetime.now(_dt.UTC)
        if fetched is None or (current - fetched).days >= max_age_days:
            return None
    return table


def _parse_iso(text: str) -> _dt.datetime | None:
    try:
        value = _dt.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=_dt.UTC)


def curated_only(note: str = "") -> BenchmarkTable:
    """An empty table that says so — the honest offline answer."""
    return BenchmarkTable(
        fetched_at="",
        rows=(),
        library={},
        source="curated",
        note=note or "No benchmark data reachable; showing the curated shortlist only.",
    )


# ── Refresh ───────────────────────────────────────────────────────────────


async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call a sync or async seam uniformly."""
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _library_facts(
    family: str, library_fn: Callable[[str], Awaitable[dict[str, Any]]]
) -> LibraryFacts | None:
    try:
        payload = await library_fn(family)
    except Exception:  # noqa: BLE001 — the library is advisory; log and go on
        log.info("benchmarks: library lookup for %s failed", family, exc_info=True)
        return None
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    for entry in payload.get("models") or []:
        if isinstance(entry, dict) and str(entry.get("name") or "") == family:
            return LibraryFacts(
                family=family,
                pulls=parse_pulls(str(entry.get("pulls") or "")),
                updated_days=parse_updated_days(str(entry.get("updated") or "")),
            )
    return None


async def _default_library_fn(family: str) -> dict[str, Any]:
    from jarvis.brain.ollama_library import search_library

    return await search_library(family, limit=10)


async def refresh_benchmarks(
    search_fn: SearchFn,
    *,
    families: Iterable[str] | None = None,
    library_fn: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    now: _dt.datetime | None = None,
    persist: bool = True,
) -> BenchmarkTable:
    """Rebuild the table: three concurrent searches per family plus the library.

    ``search_fn(query)`` returns (or resolves to) a list of
    ``{title, url, snippet}`` dicts; one failing query loses only its rows.
    When EVERY query failed or answered nothing and the library is silent too,
    the cached table (any age) or a curated-only table is returned with a
    note — the shortlist must render offline.
    """
    fams = tuple(families) if families is not None else curated_families()
    stamp = (now or _dt.datetime.now(_dt.UTC)).isoformat(timespec="seconds")
    lib_fn = library_fn or _default_library_fn

    async def _search(family: str, source: str, template: str) -> list[BenchmarkRow]:
        query = template.format(family=family)
        try:
            results = await _call(search_fn, query)
        except Exception:  # noqa: BLE001 — one failed query loses only its rows
            log.info("benchmarks: search %r failed", query, exc_info=True)
            return []
        if not isinstance(results, list):
            return []
        return extract_rows(family, results, source=source, seen_at=stamp)

    searches = [
        _search(family, source, template) for family in fams for source, template in QUERY_TEMPLATES
    ]
    lookups = [_library_facts(family, lib_fn) for family in fams]
    search_results, library_results = await asyncio.gather(
        asyncio.gather(*searches), asyncio.gather(*lookups)
    )

    rows: list[BenchmarkRow] = [row for batch in search_results for row in batch]
    library = {facts.family: facts for facts in library_results if facts is not None}

    if not rows and not library:
        cached = load_cached(max_age_days=None)
        if cached is not None:
            note = "Benchmark sources unreachable; showing the cached table."
            return BenchmarkTable(cached.fetched_at, cached.rows, cached.library, "cache", note)
        return curated_only()

    table = BenchmarkTable(fetched_at=stamp, rows=tuple(rows), library=library, source="live")
    if persist:
        _atomic_write_json(cache_path(), table.to_payload())
    return table
