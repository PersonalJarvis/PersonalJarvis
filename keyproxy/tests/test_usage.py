"""usage.py — record best-effort usage rows + per-token/period report."""

# ruff: noqa: S105, S106 — fixture "token_id" values are opaque ids, not secrets
from __future__ import annotations

import time

import pytest

from keyproxy.store import Store
from keyproxy.usage import UsageStore
from keyproxy.vendors import ParsedUsage


@pytest.fixture()
def usage() -> UsageStore:
    return UsageStore(Store(":memory:"))


def test_record_with_parsed_usage_lands_a_row(usage: UsageStore) -> None:
    usage.record(
        token_id="tok-1",
        provider_id="openai",
        parsed=ParsedUsage(
            model="gpt-4o-mini",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
        ),
    )
    rows = usage.recent(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["token_id"] == "tok-1"
    assert row["provider_id"] == "openai"
    assert row["model"] == "gpt-4o-mini"
    assert row["prompt_tokens"] == 10
    assert row["completion_tokens"] == 20
    assert row["total_tokens"] == 30
    assert row["est_cost"] is not None  # known model -> a cost estimate


def test_record_parse_miss_lands_null_counts(usage: UsageStore) -> None:
    # A parse miss (parsed=None) still records the call with null counts.
    usage.record(token_id="tok-1", provider_id="openai", parsed=None)
    rows = usage.recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["prompt_tokens"] is None
    assert rows[0]["total_tokens"] is None
    assert rows[0]["est_cost"] is None


def test_record_unknown_model_cost_null(usage: UsageStore) -> None:
    usage.record(
        token_id="tok-1",
        provider_id="openai",
        parsed=ParsedUsage(
            model="some-future-model-xyz",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        ),
    )
    assert usage.recent()[0]["est_cost"] is None


def test_record_never_raises_on_store_path(usage: UsageStore) -> None:
    # Even a None token_id (untracked) records cleanly.
    usage.record(token_id=None, provider_id="gemini", parsed=None)
    assert len(usage.recent()) == 1


def test_report_aggregates_per_token(usage: UsageStore) -> None:
    for _ in range(3):
        usage.record(
            token_id="tok-A",
            provider_id="openai",
            parsed=ParsedUsage("gpt-4o-mini", 10, 20, 30),
        )
    usage.record(
        token_id="tok-B",
        provider_id="gemini",
        parsed=ParsedUsage("gemini-2.0-flash", 5, 5, 10),
    )

    report = usage.report()
    by_token = {r["token_id"]: r for r in report}
    assert by_token["tok-A"]["calls"] == 3
    assert by_token["tok-A"]["total_tokens"] == 90
    assert by_token["tok-B"]["calls"] == 1
    assert by_token["tok-B"]["total_tokens"] == 10


def test_report_filter_by_token(usage: UsageStore) -> None:
    usage.record(
        token_id="tok-A", provider_id="openai",
        parsed=ParsedUsage("gpt-4o-mini", 1, 1, 2),
    )
    usage.record(
        token_id="tok-B", provider_id="openai",
        parsed=ParsedUsage("gpt-4o-mini", 1, 1, 2),
    )
    report = usage.report(token_id="tok-A")
    assert len(report) == 1
    assert report[0]["token_id"] == "tok-A"


def test_report_filter_by_period(usage: UsageStore) -> None:
    now = int(time.time())
    # An old row (well before the window) and a fresh one.
    usage.record(
        token_id="tok-A", provider_id="openai",
        parsed=ParsedUsage("gpt-4o-mini", 1, 1, 2), ts=now - 10_000,
    )
    usage.record(
        token_id="tok-A", provider_id="openai",
        parsed=ParsedUsage("gpt-4o-mini", 1, 1, 2), ts=now,
    )
    report = usage.report(since=now - 100)
    assert len(report) == 1
    assert report[0]["calls"] == 1
