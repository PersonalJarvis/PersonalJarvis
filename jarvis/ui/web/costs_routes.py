"""REST routes for the Spend & Tokens section.

Endpoints::

    GET /api/costs/summary   Totals + every breakdown for a time range
    GET /api/costs/daily     One row per calendar day — the section's ledger
    GET /api/costs/entries   The individual line items behind those totals
    GET /api/costs/pricing   The rate card each seen model was priced with

Wired in by the WebServer in ``_build_app()``::

    from .costs_routes import router as costs_router
    app.include_router(costs_router)

There is no store to inject: the section is a read model over the databases
the rest of the app already writes (see ``jarvis/costs/``). The only state
here is a small in-process cache keyed on the sources' mtime, so repeatedly
switching a filter in the UI does not re-read three SQLite files each time.

Loopback-only (the server binds to 127.0.0.1) — no auth token needed.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from jarvis.costs import CostEntry, CostSources, build_report, collect_entries, default_sources
from jarvis.costs.aggregate import bucket_ms_for, day_bounds_ms, filter_entries, group_by_day
from jarvis.costs.model import ROLES, SURFACES

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/costs", tags=["costs"])

# Default exchange rate when jarvis.toml carries no [cost] section. The UI
# labels EUR as a conversion, never as a billed amount — providers bill USD.
_FALLBACK_EUR_PER_USD = 0.92

_DAY_MS = 24 * 60 * 60 * 1000


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class Bucket(BaseModel):
    key: str
    cost_usd: float
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    tokens_total: int
    entries: int
    gap_tokens: int
    subscription_usd: float = 0.0
    chars: int = 0
    """Characters synthesised — only speech rows carry these two."""
    audio_ms: int = 0
    price_sources: list[str] = Field(default_factory=list)
    """How the bucket's rows were priced (free / derived / unknown / …)."""
    last_ts_ms: int
    cost_share: float
    token_share: float
    members: list[str] = Field(default_factory=list)
    breakdown: dict[str, float] = Field(default_factory=dict)
    """Cost per member of the bucket's second dimension (day → role, …)."""


class RefBucket(Bucket):
    label: str = ""
    surface: str = ""


class Totals(BaseModel):
    cost_usd: float
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    tokens_total: int
    entries: int
    gap_tokens: int
    """Tokens spent at a rate no price table knows — the accounting hole."""
    gap_entries: int
    free_tokens: int
    #: Of ``cost_usd``, the share a monthly seat covered — priced as the API
    #: would have, but no invoice carries it.
    subscription_usd: float = 0.0
    """Tokens on local engines, subscription seats or ``:free`` models."""
    estimated_usd: float
    """Share of ``cost_usd`` this section re-derived rather than read back."""
    first_ts_ms: int
    last_ts_ms: int


class ModelRow(BaseModel):
    model: str
    provider: str
    price_sources: list[str]
    tokens_total: int


class Facets(BaseModel):
    """Filter options, always built from the UNfiltered range.

    A filter that removes its own options is a dead end — picking a provider
    must never make the other providers disappear from the picker.
    """

    providers: list[str]
    models: list[str]
    roles: list[str]
    surfaces: list[str]


class Currency(BaseModel):
    eur_per_usd: float
    source: str
    """``config`` when jarvis.toml set a rate, ``default`` otherwise."""


class CostSummary(BaseModel):
    since_ms: int
    until_ms: int
    bucket: str
    """``day`` or ``hour`` — the granularity of ``series``."""
    totals: Totals
    by_provider: list[Bucket]
    by_model: list[Bucket]
    by_role: list[Bucket]
    by_surface: list[Bucket]
    series: list[Bucket]
    top_refs: list[RefBucket]
    models: list[ModelRow]
    refs_total: int = 0
    facets: Facets
    currency: Currency
    sources_present: list[str]
    """Which stores actually existed — an empty section is explainable."""


class DayRow(BaseModel):
    """One calendar day of spend, already broken down.

    The section shows ONE of these per day rather than one row per session:
    a session row carries the timestamp of its FIRST call, so a long morning
    run sorts below a short one started later and the day's real work reads
    as if it never happened. A day has no such ambiguity.
    """

    date: str
    """Local ``YYYY-MM-DD`` — the day as the person's own clock drew it."""
    since_ms: int
    until_ms: int
    totals: Totals
    by_model: list[Bucket]
    by_provider: list[Bucket]
    by_role: list[Bucket]
    by_surface: list[Bucket]


class DailyLedger(BaseModel):
    days: list[DayRow]
    currency: Currency


class EntryRow(BaseModel):
    ts_ms: int
    surface: str
    role: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    tokens_total: int
    cost_usd: float
    price_source: str
    ref_id: str
    label: str


class EntriesPage(BaseModel):
    items: list[EntryRow]
    total: int
    limit: int
    offset: int


class RateRow(BaseModel):
    model: str
    input_usd_per_mtok: float | None
    output_usd_per_mtok: float | None
    audio_input_usd_per_mtok: float | None = None
    audio_output_usd_per_mtok: float | None = None
    known: bool


class PricingResponse(BaseModel):
    rates: list[RateRow]
    currency: Currency


# ---------------------------------------------------------------------------
# Entry cache — three SQLite reads per request would be wasteful, not wrong
# ---------------------------------------------------------------------------


class _EntryCache:
    """Caches the collected entries until a source file changes.

    The key is (data dir, newest source mtime, range). A live voice turn
    updates sessions.db, the mtime moves, the next request re-reads — so the
    section is never stale, and switching a filter costs nothing.
    """

    _TTL_S = 15.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: tuple[Any, ...] | None = None
        self._entries: list[CostEntry] = []
        self._at = 0.0

    def get(self, sources: CostSources, since_ms: int, until_ms: int) -> list[CostEntry]:
        key = (
            str(sources.sessions_db),
            round(sources.newest_mtime(), 3),
            since_ms,
            until_ms,
        )
        now = time.monotonic()
        with self._lock:
            if self._key == key and (now - self._at) < self._TTL_S:
                return self._entries
        # The coding-CLI source pre-aggregates, so it has to know the grain
        # the report will draw at before it reads a row.
        _refresher.nudge(sources.cli_index_dir)
        entries = collect_entries(
            sources,
            since_ms=since_ms,
            until_ms=until_ms,
            bucket_ms=bucket_ms_for(since_ms, until_ms),
        )
        with self._lock:
            self._key = key
            self._entries = entries
            self._at = now
        return entries


_cache = _EntryCache()


# ---------------------------------------------------------------------------
# The coding-CLI index — kept current in the background, never in a request
# ---------------------------------------------------------------------------


class _IndexRefresher:
    """Nudges :mod:`jarvis.costs.cli_usage_index` forward on its own thread.

    Reading a vendor transcript is seconds of I/O on gigabytes, so it can
    never happen while a request waits. Instead every summary request asks
    this to run, and it does so at most once a minute, one thread at a time,
    reporting whatever it managed. The section always renders from what the
    index already has; a first run on a busy machine simply fills in over the
    next few refreshes rather than blocking the page (AP-26: nothing here
    starts at import or at boot either — the first request arms it).
    """

    _MIN_GAP_S = 60.0
    _BUDGET_S = 20.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._last = 0.0

    def nudge(self, data_dir: Path | None) -> None:
        now = time.monotonic()
        with self._lock:
            if self._running or (self._last and now - self._last < self._MIN_GAP_S):
                return
            self._running = True
        threading.Thread(
            target=self._run, args=(data_dir,), name="cli-usage-index", daemon=True
        ).start()

    def _run(self, data_dir: Path | None) -> None:
        try:
            from jarvis.costs.cli_usage_index import refresh

            result = refresh(data_dir=data_dir, deadline_s=self._BUDGET_S)
            if result.turns_added:
                log.debug(
                    "cli usage index: +%d turns from %d files",
                    result.turns_added,
                    result.files_scanned,
                )
        except Exception as exc:  # noqa: BLE001 — a background index must never
            # take the section down with it; the report just stays as current
            # as the last successful run.
            log.warning("cli usage index: refresh failed (%s)", exc)
        finally:
            with self._lock:
                self._running = False
                self._last = time.monotonic()


_refresher = _IndexRefresher()




# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sources(request: Request) -> CostSources:
    """Point the read model at the data dir this instance actually uses."""
    cfg = getattr(request.app.state, "config", None)
    data_dir = getattr(getattr(cfg, "memory", None), "data_dir", None)
    return default_sources(Path(data_dir) if data_dir else None)


def _currency() -> Currency:
    """Read ``[cost].eur_per_usd`` from the active config file.

    Read straight from the TOML rather than through the config model: the
    ``[cost]`` section predates the typed schema and carries no other reader,
    and a display-only exchange rate is not worth a schema migration.
    """
    try:
        import tomllib

        from jarvis.core.config import resolve_config_path

        path = resolve_config_path()
        if path.exists():
            data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
            raw = data.get("cost", {}).get("eur_per_usd")
            if isinstance(raw, int | float) and raw > 0:
                return Currency(eur_per_usd=float(raw), source="config")
    except (OSError, ValueError) as exc:
        # A malformed config must not take the section down; the fallback
        # rate is clearly labelled as such in the response.
        log.debug("costs: exchange rate not readable from config (%s)", exc)
    return Currency(eur_per_usd=_FALLBACK_EUR_PER_USD, source="default")


def _range(days: int, since_ms: int | None, until_ms: int | None) -> tuple[int, int]:
    """Explicit bounds win; otherwise the last ``days`` days up to now.

    ``days=0`` means "everything ever recorded" — the section is also a
    lifetime ledger, not only a rolling window.
    """
    now = int(time.time() * 1000)
    end = until_ms if until_ms is not None else now
    if since_ms is not None:
        return since_ms, end
    if days <= 0:
        return 0, end
    return end - days * _DAY_MS, end


def _facets(entries: list[CostEntry]) -> Facets:
    providers = sorted({e.provider for e in entries if e.provider})
    models = sorted({e.model for e in entries if e.model})
    roles = [r for r in ROLES if any(e.role == r for e in entries)]
    surfaces = [s for s in SURFACES if any(e.surface == s for e in entries)]
    return Facets(providers=providers, models=models, roles=roles, surfaces=surfaces)


def _split(values: list[str] | None) -> set[str]:
    """Accept both repeated params and one comma-separated value."""
    out: set[str] = set()
    for raw in values or []:
        out.update(part.strip() for part in raw.split(",") if part.strip())
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=CostSummary, summary="Spend and token totals")
async def get_summary(
    request: Request,
    days: Annotated[int, Query(ge=0, le=3650)] = 30,
    since_ms: Annotated[int | None, Query(ge=0)] = None,
    until_ms: Annotated[int | None, Query(ge=0)] = None,
    provider: Annotated[list[str] | None, Query()] = None,
    model: Annotated[list[str] | None, Query()] = None,
    role: Annotated[list[str] | None, Query()] = None,
    surface: Annotated[list[str] | None, Query()] = None,
    ref: Annotated[list[str] | None, Query()] = None,
    billing: Annotated[str, Query(pattern="^(all|billed|subscription)$")] = "all",
    search: str = "",
    top: Annotated[int, Query(ge=1, le=100)] = 12,
) -> CostSummary:
    """Every breakdown of what the app spent: by provider, model, role, day.

    ``days=0`` reports everything ever recorded. Filters narrow the numbers
    but never the filter options themselves.
    """
    sources = _sources(request)
    start, end = _range(days, since_ms, until_ms)
    entries = _cache.get(sources, start, end)
    selected = filter_entries(
        entries,
        providers=_split(provider),
        models=_split(model),
        roles=_split(role),
        surfaces=_split(surface),
        refs=_split(ref),
        search=search,
        billing=billing,
    )
    report = build_report(selected, since_ms=start, until_ms=end, top_n=top)
    payload = report.to_dict()
    payload["facets"] = _facets(entries).model_dump()
    payload["currency"] = _currency().model_dump()
    payload["sources_present"] = [p.name for p in sources.existing()]
    return CostSummary.model_validate(payload)


# How many rows a day keeps of each dimension. A day is read at a glance —
# the models are what people scan (which one did the work), so it keeps more
# of them; providers and areas are a handful anyway.
_DAY_TOP_MODELS = 12
_DAY_TOP_PROVIDERS = 8


@router.get("/daily", response_model=DailyLedger, summary="One row per calendar day")
async def get_daily(
    request: Request,
    days: Annotated[int, Query(ge=0, le=3650)] = 30,
    since_ms: Annotated[int | None, Query(ge=0)] = None,
    until_ms: Annotated[int | None, Query(ge=0)] = None,
    provider: Annotated[list[str] | None, Query()] = None,
    model: Annotated[list[str] | None, Query()] = None,
    role: Annotated[list[str] | None, Query()] = None,
    surface: Annotated[list[str] | None, Query()] = None,
    ref: Annotated[list[str] | None, Query()] = None,
    billing: Annotated[str, Query(pattern="^(all|billed|subscription)$")] = "all",
    search: str = "",
) -> DailyLedger:
    """The daily ledger: one entry per day, newest first.

    Every source feeds it — an API-billed voice turn, a mission worker and a
    coding CLI on a monthly seat all land in the same day. Nothing here is
    vendor-specific: the day names whichever models it actually saw.

    The per-day breakdowns come from :func:`build_report` over that day's own
    rows, so a day's numbers are computed exactly the way the section's
    headline numbers are and cannot drift from them.
    """
    sources = _sources(request)
    start, end = _range(days, since_ms, until_ms)
    entries = filter_entries(
        _cache.get(sources, start, end),
        providers=_split(provider),
        models=_split(model),
        roles=_split(role),
        surfaces=_split(surface),
        refs=_split(ref),
        search=search,
        billing=billing,
    )
    rows: list[DayRow] = []
    for key, day_entries in group_by_day(entries):
        day_start, day_end = day_bounds_ms(key)
        # ``build_report`` reads first/last from the ends of the list, and the
        # collector concatenates one source after another rather than merging
        # them, so a day has to be put in order before it can say when it
        # started and when it stopped.
        day_entries.sort(key=lambda e: e.ts_ms)
        report = build_report(
            day_entries,
            since_ms=day_start,
            until_ms=day_end,
            top_n=_DAY_TOP_MODELS,
        )
        rows.append(
            DayRow(
                date=key,
                since_ms=day_start,
                until_ms=day_end,
                totals=Totals.model_validate(report.totals),
                by_model=[Bucket.model_validate(b) for b in report.by_model],
                by_provider=[
                    Bucket.model_validate(b) for b in report.by_provider[:_DAY_TOP_PROVIDERS]
                ],
                by_role=[Bucket.model_validate(b) for b in report.by_role],
                by_surface=[Bucket.model_validate(b) for b in report.by_surface],
            )
        )
    return DailyLedger(days=rows, currency=_currency())


@router.get("/entries", response_model=EntriesPage, summary="Individual spend line items")
async def get_entries(
    request: Request,
    days: Annotated[int, Query(ge=0, le=3650)] = 30,
    since_ms: Annotated[int | None, Query(ge=0)] = None,
    until_ms: Annotated[int | None, Query(ge=0)] = None,
    provider: Annotated[list[str] | None, Query()] = None,
    model: Annotated[list[str] | None, Query()] = None,
    role: Annotated[list[str] | None, Query()] = None,
    surface: Annotated[list[str] | None, Query()] = None,
    ref: Annotated[list[str] | None, Query()] = None,
    billing: Annotated[str, Query(pattern="^(all|billed|subscription)$")] = "all",
    search: str = "",
    sort: Annotated[str, Query(pattern="^(recent|cost|tokens)$")] = "recent",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EntriesPage:
    """The rows behind the totals — one per model call, newest first."""
    sources = _sources(request)
    start, end = _range(days, since_ms, until_ms)
    entries = filter_entries(
        _cache.get(sources, start, end),
        providers=_split(provider),
        models=_split(model),
        roles=_split(role),
        surfaces=_split(surface),
        refs=_split(ref),
        search=search,
        billing=billing,
    )
    if sort == "cost":
        entries = sorted(entries, key=lambda e: -e.cost_usd)
    elif sort == "tokens":
        entries = sorted(entries, key=lambda e: -e.tokens_total)
    else:
        entries = sorted(entries, key=lambda e: -e.ts_ms)
    page = entries[offset : offset + limit]
    return EntriesPage(
        items=[EntryRow.model_validate(e.to_dict()) for e in page],
        total=len(entries),
        limit=limit,
        offset=offset,
    )


@router.get("/pricing", response_model=PricingResponse, summary="Rate card per model")
async def get_pricing(
    request: Request,
    days: Annotated[int, Query(ge=0, le=3650)] = 30,
) -> PricingResponse:
    """What each model seen in the range is priced at, per million tokens.

    A model with ``known=false`` is why a ``gap_tokens`` number is not zero:
    it was used, and neither the built-in table nor the provider feed has a
    rate for it.
    """
    from jarvis.brain.cost import REALTIME_AUDIO_PRICING_USD_PER_MTOK, resolve_rates

    sources = _sources(request)
    start, end = _range(days, None, None)
    # A row without a model id is exactly what makes gap_tokens non-zero,
    # so it is listed by its provider rather than silently dropped.
    seen = {
        e.model or f"({e.provider} — no model recorded)"
        for e in _cache.get(sources, start, end)
    }
    rows: list[RateRow] = []
    for name in sorted(seen):
        rates = resolve_rates(name)
        audio = REALTIME_AUDIO_PRICING_USD_PER_MTOK.get(name)
        rows.append(
            RateRow(
                model=name,
                input_usd_per_mtok=rates[0] if rates else None,
                output_usd_per_mtok=rates[1] if rates else None,
                audio_input_usd_per_mtok=audio[0] if audio else None,
                audio_output_usd_per_mtok=audio[1] if audio else None,
                known=rates is not None or audio is not None,
            )
        )
    return PricingResponse(rates=rows, currency=_currency())
