"""The usage ledger and the metered brain: every model call is written down,
tagged with who asked, and the report reads it back without double counting."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jarvis.brain.usage_meter import MeteredBrain, meter_brain
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.costs import CostSources, collect_entries, ledger
from jarvis.costs.aggregate import filter_entries
from jarvis.costs.model import CostEntry


class FakeBrain:
    name = "fake"
    model = "gpt-5.5"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls += 1
        yield BrainDelta(content="hel")
        yield BrainDelta(content="lo")
        yield BrainDelta(
            finish_reason="stop",
            usage={"input_tokens": 120, "output_tokens": 7, "cache_hit_tokens": 900},
        )

    def estimate_cost(self, req: BrainRequest) -> float:
        return 0.5


@pytest.fixture()
def ledger_path(tmp_path: Path):
    path = tmp_path / "llm_usage.db"
    ledger.set_ledger_path(path)
    try:
        yield path
    finally:
        ledger.flush()
        ledger.set_ledger_path(None)


def _request() -> BrainRequest:
    return BrainRequest(messages=[])


async def _drain(stream: AsyncIterator[BrainDelta]) -> str:
    parts = [d.content or "" async for d in stream]
    return "".join(parts)


def test_a_metered_brain_writes_the_call_with_its_caller(ledger_path: Path) -> None:
    brain = meter_brain(FakeBrain(), "openrouter")
    assert isinstance(brain, MeteredBrain)

    async def run() -> str:
        with ledger.usage_context("wiki"):
            stream = brain.complete(_request())
        return await _drain(stream)

    assert asyncio.run(run()) == "hello"
    ledger.flush()
    rows = list(ledger.read_usage(ledger_path, 0, 2**62))
    assert len(rows) == 1
    row = rows[0]
    assert (row.provider, row.model, row.caller) == ("openrouter", "gpt-5.5", "wiki")
    assert (row.tokens_in, row.tokens_out, row.tokens_cached) == (120, 7, 900)


def test_the_caller_is_read_when_complete_is_called_not_when_consumed(
    ledger_path: Path,
) -> None:
    brain = meter_brain(FakeBrain(), "gemini")

    async def run() -> None:
        with ledger.usage_context("dictation"):
            stream = brain.complete(_request())
        await _drain(stream)

    asyncio.run(run())
    ledger.flush()
    assert [r.caller for r in ledger.read_usage(ledger_path, 0, 2**62)] == ["dictation"]


def test_wrapping_twice_is_wrapping_once() -> None:
    inner = FakeBrain()
    once = meter_brain(inner, "x")
    assert meter_brain(once, "x") is once
    assert once.unwrapped is inner
    assert once.name == "fake" and once.estimate_cost(_request()) == 0.5


def test_the_report_counts_background_calls_and_skips_covered_ones(ledger_path: Path) -> None:
    for caller in ("wiki", "voice-turn", "agent-chat", "mission-worker", ""):
        ledger.record_usage(
            provider="openrouter",
            model="google/gemini-3.5-flash",
            tokens_in=1_000,
            tokens_out=100,
            caller=caller,
            ts_ms=1_700_000_000_000,
        )
    ledger.flush()

    rows = collect_entries(CostSources(ledger_db=ledger_path))
    assert len(rows) == 2
    assert {e.surface for e in rows} == {"background"}
    assert {e.role for e in rows} == {"background"}
    assert {e.label for e in rows} == {"wiki", "background"}
    assert all(e.cost_usd > 0 for e in rows)


def test_a_zero_usage_call_is_not_written(ledger_path: Path) -> None:
    ledger.record_usage(provider="x", model="y", tokens_in=0, tokens_out=0)
    ledger.flush()
    assert list(ledger.read_usage(ledger_path, 0, 2**62)) == []


def _entry(source: str) -> CostEntry:
    return CostEntry(
        ts_ms=1, surface="voice", role="tool", provider="p", model="m",
        tokens_in=1, tokens_out=1, tokens_cached=0, cost_usd=1.0,
        price_source=source, ref_id="", label="",  # type: ignore[arg-type]
    )


def test_billing_filter_splits_seat_quotes_from_key_spend() -> None:
    rows = [_entry("recorded"), _entry("subscription"), _entry("derived")]
    assert len(filter_entries(rows, billing="all")) == 3
    billed = [e.price_source for e in filter_entries(rows, billing="billed")]
    assert billed == ["recorded", "derived"]
    seats = [e.price_source for e in filter_entries(rows, billing="subscription")]
    assert seats == ["subscription"]
