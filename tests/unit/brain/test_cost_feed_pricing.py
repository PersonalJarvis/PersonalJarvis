"""Cost tracking prices models the static table never listed.

Background (2026-08-18): the deck's "API this session" card showed 272k
tokens on ``gemini-3.7-flash`` and ``vertex-live`` as ``$0`` — both ids were
absent from ``PRICING_USD_PER_MTOK`` and the table was the ONLY source. Now
``resolve_rates`` also reads the provider feed the model catalog caches on
disk (OpenRouter publishes a price for every model it routes), and
``ensure_pricing_for`` refreshes that feed once for an unknown model.

Fakes, not mocks: the feed is a real JSON file in ``tmp_path``; the catalog
refresh is a fake ``ModelCatalog`` that writes that file the way the real
one does.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from jarvis.brain import cost


def _write_feed(
    path: Path, models: list[dict], *, fetched_at: float | None = None, bump: int = 1
) -> None:
    payload = {
        "openrouter": {
            "fetched_at": time.time() if fetched_at is None else fetched_at,
            "models": models,
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    # A rewrite inside the same clock tick must still count as a change;
    # push the mtime forward explicitly instead of trusting the filesystem.
    stamp = 1_700_000_000 + bump
    os.utime(path, (stamp, stamp))


@pytest.fixture
def feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "model_catalog_cache.json"
    monkeypatch.setattr(cost, "_feed_path_override", path)
    monkeypatch.setattr(cost, "_feed_loaded", None)
    monkeypatch.setattr(cost, "_feed_fetched_at", 0.0)
    monkeypatch.setattr(cost, "_feed_rates", {})
    monkeypatch.setattr(cost, "_feed_by_name", {})
    monkeypatch.setattr(cost, "_refresh_attempted", set())
    return path


class TestResolveRates:
    def test_static_table_still_wins_for_origin_ids(self, feed: Path) -> None:
        # An origin id (no vendor prefix) trusts the hand-verified table even
        # when the feed lists the same model at another price.
        _write_feed(feed, [{"id": "google/gemini-3.7-flash", "pricing": [0.375, 1.875]}])
        assert (
            cost.resolve_rates("gemini-3.7-flash") == cost.PRICING_USD_PER_MTOK["gemini-3.7-flash"]
        )

    def test_unknown_origin_id_falls_back_to_the_feed_by_model_name(self, feed: Path) -> None:
        _write_feed(feed, [{"id": "google/gemini-9.9-flash", "pricing": [0.5, 2.0]}])
        assert "gemini-9.9-flash" not in cost.PRICING_USD_PER_MTOK
        assert cost.resolve_rates("gemini-9.9-flash") == (0.5, 2.0)
        assert cost.calculate_cost_usd("gemini-9.9-flash", 1_000_000, 1_000_000) == pytest.approx(
            2.5
        )

    def test_vendor_prefixed_id_prefers_the_feed_over_the_table(self, feed: Path) -> None:
        # ``vendor/model`` is the aggregator's own id: what its feed says is
        # what it bills; the static row is only the offline fallback.
        assert "google/gemini-3.6-flash" in cost.PRICING_USD_PER_MTOK
        _write_feed(feed, [{"id": "google/gemini-3.6-flash", "pricing": [0.11, 0.22]}])
        assert cost.resolve_rates("google/gemini-3.6-flash") == (0.11, 0.22)

    def test_vendor_prefixed_id_falls_back_to_the_table_when_the_feed_lacks_it(
        self, feed: Path
    ) -> None:
        _write_feed(feed, [{"id": "vendor/other", "pricing": [1.0, 1.0]}])
        assert (
            cost.resolve_rates("google/gemini-3.6-flash")
            == cost.PRICING_USD_PER_MTOK["google/gemini-3.6-flash"]
        )

    def test_vendor_prefixed_id_never_matches_by_bare_name(self, feed: Path) -> None:
        # ``other/gemini-9.9-flash`` is a DIFFERENT listing than ``google/…``.
        _write_feed(feed, [{"id": "google/gemini-9.9-flash", "pricing": [0.5, 2.0]}])
        assert cost.resolve_rates("other/gemini-9.9-flash") is None

    def test_variant_suffixes_do_not_shadow_the_plain_model(self, feed: Path) -> None:
        # ``:batch`` / ``:free`` are other SKUs — the plain name must resolve
        # to the plain listing whatever the sort order puts first.
        _write_feed(
            feed,
            [
                {"id": "google/gemini-9.9-flash:batch", "pricing": [0.1, 0.1]},
                {"id": "google/gemini-9.9-flash", "pricing": [0.5, 2.0]},
                {"id": "google/gemini-9.9-flash:free", "pricing": [0.0, 0.0]},
            ],
        )
        assert cost.resolve_rates("gemini-9.9-flash") == (0.5, 2.0)

    def test_dot_and_dash_version_spellings_match(self, feed: Path) -> None:
        # Anthropic's API says claude-sonnet-9-9, OpenRouter anthropic/claude-sonnet-9.9.
        _write_feed(feed, [{"id": "anthropic/claude-sonnet-9.9", "pricing": [3.0, 15.0]}])
        assert cost.resolve_rates("claude-sonnet-9-9") == (3.0, 15.0)

    def test_missing_feed_file_means_no_feed_rates(self, feed: Path) -> None:
        assert not feed.exists()
        assert cost.feed_rates("gemini-9.9-flash") is None
        assert cost.calculate_cost_usd("gemini-9.9-flash", 1000, 10) == 0.0

    def test_corrupt_feed_file_is_ignored(self, feed: Path) -> None:
        feed.write_text("{ not json", encoding="utf-8")
        assert cost.feed_rates("gemini-9.9-flash") is None

    def test_feed_reloads_when_the_file_changes(self, feed: Path) -> None:
        _write_feed(feed, [{"id": "google/gemini-9.9-flash", "pricing": [0.5, 2.0]}], bump=1)
        assert cost.resolve_rates("gemini-9.9-flash") == (0.5, 2.0)
        _write_feed(feed, [{"id": "google/gemini-9.9-flash", "pricing": [0.7, 3.0]}], bump=2)
        assert cost.resolve_rates("gemini-9.9-flash") == (0.7, 3.0)

    def test_empty_model_resolves_to_nothing(self, feed: Path) -> None:
        assert cost.resolve_rates("") is None
        assert cost.resolve_rates(None) is None


class _FakeCatalog:
    """Stands in for ModelCatalog: a refresh writes the feed file."""

    calls: list[tuple[str, bool]] = []
    write: list[dict] = []
    delay_s: float = 0.0
    fail: bool = False

    def __init__(self, cache_path: Path | None = None) -> None:
        self._path = cache_path

    async def list_models(self, provider: str, *, force_refresh: bool = False):
        _FakeCatalog.calls.append((provider, force_refresh))
        if _FakeCatalog.fail:
            raise RuntimeError("network down")
        if _FakeCatalog.delay_s:
            await asyncio.sleep(_FakeCatalog.delay_s)
        assert self._path is not None
        _write_feed(self._path, _FakeCatalog.write, bump=7)
        return None


@pytest.fixture
def fake_catalog(monkeypatch: pytest.MonkeyPatch):
    import jarvis.brain.model_catalog as mc

    _FakeCatalog.calls = []
    _FakeCatalog.write = []
    _FakeCatalog.delay_s = 0.0
    _FakeCatalog.fail = False
    monkeypatch.setattr(mc, "ModelCatalog", _FakeCatalog)
    return _FakeCatalog


class TestEnsurePricingFor:
    def test_known_model_needs_no_refresh(self, feed: Path, fake_catalog) -> None:
        assert asyncio.run(cost.ensure_pricing_for("gemini-3.7-flash")) is True
        assert fake_catalog.calls == []

    def test_unknown_model_refreshes_the_feed_once_and_is_then_priced(
        self, feed: Path, fake_catalog
    ) -> None:
        fake_catalog.write = [{"id": "google/gemini-9.9-flash", "pricing": [0.5, 2.0]}]
        assert asyncio.run(cost.ensure_pricing_for("gemini-9.9-flash")) is True
        assert fake_catalog.calls == [("openrouter", True)]
        assert cost.calculate_cost_usd("gemini-9.9-flash", 1_000_000, 0) == pytest.approx(0.5)
        # Priced now → the next turn is answered from the table/feed, no fetch.
        assert asyncio.run(cost.ensure_pricing_for("gemini-9.9-flash")) is True
        assert len(fake_catalog.calls) == 1

    def test_a_model_the_feed_does_not_list_is_fetched_only_once_per_process(
        self, feed: Path, fake_catalog
    ) -> None:
        fake_catalog.write = [{"id": "vendor/other", "pricing": [1.0, 1.0]}]
        assert asyncio.run(cost.ensure_pricing_for("qwen-local:9b")) is False
        assert asyncio.run(cost.ensure_pricing_for("qwen-local:9b")) is False
        assert len(fake_catalog.calls) == 1

    def test_a_fresh_feed_is_not_refetched(self, feed: Path, fake_catalog) -> None:
        # The feed was pulled a minute ago and does not list the model — it is
        # simply not on the feed (local weights, a Live-API id); no round-trip.
        _write_feed(
            feed, [{"id": "vendor/other", "pricing": [1.0, 1.0]}], fetched_at=time.time() - 60
        )
        assert asyncio.run(cost.ensure_pricing_for("gemini-9.9-flash")) is False
        assert fake_catalog.calls == []

    def test_a_stale_feed_is_refetched(self, feed: Path, fake_catalog) -> None:
        _write_feed(
            feed, [{"id": "vendor/other", "pricing": [1.0, 1.0]}], fetched_at=time.time() - 3600
        )
        fake_catalog.write = [{"id": "google/gemini-9.9-flash", "pricing": [0.5, 2.0]}]
        assert asyncio.run(cost.ensure_pricing_for("gemini-9.9-flash")) is True
        assert fake_catalog.calls == [("openrouter", True)]

    def test_airgapped_profile_makes_no_outbound_call(
        self, feed: Path, fake_catalog, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from jarvis.core import config as cfg

        monkeypatch.setattr(cfg, "profile", SimpleNamespace(name="airgapped"), raising=False)
        assert asyncio.run(cost.ensure_pricing_for("gemini-9.9-flash")) is False
        assert fake_catalog.calls == []

    def test_refresh_failure_never_raises(self, feed: Path, fake_catalog) -> None:
        fake_catalog.fail = True
        assert asyncio.run(cost.ensure_pricing_for("gemini-9.9-flash")) is False

    def test_refresh_is_capped_by_the_timeout(self, feed: Path, fake_catalog) -> None:
        fake_catalog.delay_s = 5.0
        started = time.monotonic()
        assert asyncio.run(cost.ensure_pricing_for("gemini-9.9-flash", timeout_s=0.05)) is False
        assert time.monotonic() - started < 2.0

    def test_empty_model_is_never_fetched(self, feed: Path, fake_catalog) -> None:
        assert asyncio.run(cost.ensure_pricing_for("")) is False
        assert asyncio.run(cost.ensure_pricing_for(None)) is False
        assert fake_catalog.calls == []
