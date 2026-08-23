"""Grouping and totals — the shape the section actually renders.

One pass over the entries fills every breakdown at once (provider, model,
role, surface, day, and the most expensive references), because the list is
small enough to hold in memory and re-scanning it per dimension would only
add ways for two numbers on the same screen to disagree.

The totals deliberately keep three quantities apart that a naive sum blurs:

- what was **billed** (a recorded or re-derived price),
- what is **free** (local engines, subscription seats, ``:free`` models),
- what is **unaccounted** — tokens at a rate nobody publishes.

A dashboard that folds the third into the first reads "cheap" when it means
"unknown", which is the exact failure this section exists to prevent.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .model import CostEntry

# Range shorter than this many days → hourly buckets, so "today" is a curve
# rather than a single bar.
_HOURLY_RANGE_MS = 3 * 24 * 60 * 60 * 1000


@dataclass(slots=True)
class Bucket:
    """Running totals for one group key."""

    key: str
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    entries: int = 0
    gap_tokens: int = 0
    last_ts_ms: int = 0
    #: Secondary dimension — which providers/models/roles fed this bucket.
    members: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def add(self, entry: CostEntry, member: str = "") -> None:
        self.cost_usd += entry.cost_usd
        self.tokens_in += entry.tokens_in
        self.tokens_out += entry.tokens_out
        self.tokens_cached += entry.tokens_cached
        self.entries += 1
        if entry.is_gap:
            self.gap_tokens += entry.tokens_total
        self.last_ts_ms = max(self.last_ts_ms, entry.ts_ms)
        if member:
            self.members[member] += entry.cost_usd

    def to_dict(self, total_cost: float, total_tokens: int) -> dict[str, Any]:
        tokens = self.tokens_in + self.tokens_out + self.tokens_cached
        ranked = sorted(self.members.items(), key=lambda kv: -kv[1])
        return {
            "key": self.key,
            "cost_usd": round(self.cost_usd, 6),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_cached": self.tokens_cached,
            "tokens_total": tokens,
            "entries": self.entries,
            "gap_tokens": self.gap_tokens,
            "last_ts_ms": self.last_ts_ms,
            "cost_share": round(self.cost_usd / total_cost, 6) if total_cost > 0 else 0.0,
            "token_share": round(tokens / total_tokens, 6) if total_tokens > 0 else 0.0,
            "members": [m for m, _ in ranked[:3]],
            # Second dimension of the same bucket: what a day cost per
            # role, what a provider cost per model. The stacked chart and
            # the row tooltips read from this — without it the section
            # would need a second request per dimension to say anything
            # about composition.
            "breakdown": {m: round(v, 6) for m, v in ranked[:8]},
        }


@dataclass(slots=True)
class CostReport:
    """Everything one request of the section needs, already summed."""

    since_ms: int
    until_ms: int
    bucket: str
    totals: dict[str, Any]
    by_provider: list[dict[str, Any]]
    by_model: list[dict[str, Any]]
    by_role: list[dict[str, Any]]
    by_surface: list[dict[str, Any]]
    series: list[dict[str, Any]]
    top_refs: list[dict[str, Any]]
    models: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "since_ms": self.since_ms,
            "until_ms": self.until_ms,
            "bucket": self.bucket,
            "totals": self.totals,
            "by_provider": self.by_provider,
            "by_model": self.by_model,
            "by_role": self.by_role,
            "by_surface": self.by_surface,
            "series": self.series,
            "top_refs": self.top_refs,
            "models": self.models,
        }


def filter_entries(
    entries: list[CostEntry],
    *,
    providers: set[str] | None = None,
    models: set[str] | None = None,
    roles: set[str] | None = None,
    surfaces: set[str] | None = None,
    search: str = "",
) -> list[CostEntry]:
    """Narrow the line items. An empty filter set means "everything"."""
    needle = search.strip().casefold()

    def keep(e: CostEntry) -> bool:
        if providers and e.provider not in providers:
            return False
        if models and e.model not in models:
            return False
        if roles and e.role not in roles:
            return False
        if surfaces and e.surface not in surfaces:
            return False
        if needle and needle not in f"{e.model} {e.provider} {e.label}".casefold():
            return False
        return True

    return [e for e in entries if keep(e)]


def build_report(
    entries: list[CostEntry],
    *,
    since_ms: int,
    until_ms: int,
    top_n: int = 12,
) -> CostReport:
    """Roll the entries up into every breakdown the section shows."""
    providers: dict[str, Bucket] = {}
    models: dict[str, Bucket] = {}
    roles: dict[str, Bucket] = {}
    surfaces: dict[str, Bucket] = {}
    series: dict[str, Bucket] = {}
    refs: dict[str, Bucket] = {}
    ref_labels: dict[str, str] = {}
    ref_surface: dict[str, str] = {}
    price_sources: dict[str, dict[str, Any]] = {}

    total_cost = 0.0
    tokens_in = tokens_out = tokens_cached = 0
    gap_tokens = gap_entries = 0
    free_tokens = 0
    derived_cost = 0.0

    hourly = (until_ms - since_ms) <= _HOURLY_RANGE_MS
    bucket_kind = "hour" if hourly else "day"

    for e in entries:
        total_cost += e.cost_usd
        tokens_in += e.tokens_in
        tokens_out += e.tokens_out
        tokens_cached += e.tokens_cached
        if e.is_gap:
            gap_tokens += e.tokens_total
            gap_entries += 1
        if e.price_source == "free":
            free_tokens += e.tokens_total
        if e.price_source == "derived":
            derived_cost += e.cost_usd

        _bucket(providers, e.provider).add(e, member=e.model or e.role)
        _bucket(models, e.model or f"{e.provider} (unnamed)").add(e, member=e.provider)
        _bucket(roles, e.role).add(e, member=e.provider)
        _bucket(surfaces, e.surface).add(e, member=e.role)
        _bucket(series, _stamp(e.ts_ms, hourly)).add(e, member=e.role)

        if e.ref_id:
            _bucket(refs, e.ref_id).add(e, member=e.model or e.provider)
            if e.label and not ref_labels.get(e.ref_id):
                ref_labels[e.ref_id] = e.label
            ref_surface.setdefault(e.ref_id, e.surface)

        if e.model:
            slot = price_sources.setdefault(
                e.model,
                {"model": e.model, "provider": e.provider, "sources": set(), "tokens": 0},
            )
            slot["sources"].add(e.price_source)
            slot["tokens"] += e.tokens_total

    total_tokens = tokens_in + tokens_out + tokens_cached
    totals = {
        "cost_usd": round(total_cost, 6),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_cached": tokens_cached,
        "tokens_total": total_tokens,
        "entries": len(entries),
        "gap_tokens": gap_tokens,
        "gap_entries": gap_entries,
        "free_tokens": free_tokens,
        "estimated_usd": round(derived_cost, 6),
        "first_ts_ms": entries[0].ts_ms if entries else 0,
        "last_ts_ms": entries[-1].ts_ms if entries else 0,
    }

    return CostReport(
        since_ms=since_ms,
        until_ms=until_ms,
        bucket=bucket_kind,
        totals=totals,
        by_provider=_ranked(providers, total_cost, total_tokens, top_n),
        by_model=_ranked(models, total_cost, total_tokens, top_n),
        by_role=_ranked(roles, total_cost, total_tokens, len(roles) or 1),
        by_surface=_ranked(surfaces, total_cost, total_tokens, len(surfaces) or 1),
        series=_series(series, total_cost, total_tokens, hourly),
        top_refs=[
            {
                **row,
                "label": ref_labels.get(row["key"], ""),
                "surface": ref_surface.get(row["key"], ""),
            }
            for row in _ranked(refs, total_cost, total_tokens, top_n)
        ],
        models=sorted(
            (
                {
                    "model": slot["model"],
                    "provider": slot["provider"],
                    "price_sources": sorted(slot["sources"]),
                    "tokens_total": slot["tokens"],
                }
                for slot in price_sources.values()
            ),
            key=lambda m: -int(m["tokens_total"]),
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bucket(store: dict[str, Bucket], key: str) -> Bucket:
    key = key or "unknown"
    slot = store.get(key)
    if slot is None:
        slot = Bucket(key=key)
        store[key] = slot
    return slot


def _stamp(ts_ms: int, hourly: bool) -> str:
    """Local-time bucket key — the user reasons in their own day, not UTC."""
    dt = datetime.fromtimestamp(max(0, ts_ms) / 1000)
    return dt.strftime("%Y-%m-%dT%H:00") if hourly else dt.strftime("%Y-%m-%d")


def _ranked(
    store: dict[str, Bucket], total_cost: float, total_tokens: int, limit: int
) -> list[dict[str, Any]]:
    rows = sorted(
        store.values(),
        # Cost first; a bucket that spent nothing still ranks by the tokens it
        # burned, which is what makes unpriced models visible at all.
        key=lambda b: (-b.cost_usd, -(b.tokens_in + b.tokens_out + b.tokens_cached)),
    )
    return [b.to_dict(total_cost, total_tokens) for b in rows[:limit]]


# A chart that only draws the days something happened compresses quiet
# stretches: three idle days look like one. Past this many buckets the gap
# filling stops — a multi-year range would otherwise render thousands of
# empty bars nobody can read anyway.
_MAX_SERIES_BUCKETS = 400


def _series(
    store: dict[str, Bucket], total_cost: float, total_tokens: int, hourly: bool
) -> list[dict[str, Any]]:
    """The time series with every empty bucket in between filled in.

    Filled from the FIRST bucket that has data, not from the start of the
    requested window: a 90-day window whose data begins on day 60 should not
    open with 60 empty bars.
    """
    keys = sorted(store)
    if not keys:
        return []
    for key in _gap_keys(keys[0], keys[-1], hourly):
        store.setdefault(key, Bucket(key=key))
    return [store[key].to_dict(total_cost, total_tokens) for key in sorted(store)]


def _gap_keys(first: str, last: str, hourly: bool) -> list[str]:
    """Every bucket key strictly between two stamps, in order."""
    fmt = "%Y-%m-%dT%H:00" if hourly else "%Y-%m-%d"
    step = timedelta(hours=1) if hourly else timedelta(days=1)
    try:
        cursor = datetime.strptime(first, fmt)
        end = datetime.strptime(last, fmt)
    except ValueError:
        # Silence is right: an unparsable stamp only costs the gap filling.
        # The series itself is already complete, so a chart with a few
        # missing zero-bars beats failing the whole report.
        return []
    keys: list[str] = []
    while cursor < end and len(keys) < _MAX_SERIES_BUCKETS:
        cursor += step
        keys.append(cursor.strftime(fmt))
    return keys
