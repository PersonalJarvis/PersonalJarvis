"""Tests for jarvis.brain.cost — pricing table + calculate_cost_usd."""
from __future__ import annotations

import pytest

from jarvis.brain.cost import (
    PRICING_USD_PER_MTOK,
    REALTIME_AUDIO_PRICING_USD_PER_MTOK,
    calculate_cost_usd,
    calculate_realtime_cost_usd,
)


class TestCalculateCostUsd:
    def test_known_model_claude_opus(self) -> None:
        # 1M in, 1M out -> exact rates
        cost = calculate_cost_usd("claude-opus-4-7-20251022", 1_000_000, 1_000_000)
        # 15.0 * 1 + 75.0 * 1 = 90.0
        assert cost == pytest.approx(90.0)

    def test_known_model_gemini_pro(self) -> None:
        cost = calculate_cost_usd("gemini-2.5-pro", 1000, 500)
        # (1000 * 1.25 + 500 * 10.0) / 1_000_000 = (1250 + 5000) / 1e6 = 0.00625
        assert cost == pytest.approx(0.00625)

    def test_known_model_haiku(self) -> None:
        cost = calculate_cost_usd("claude-haiku-4-5-20251001", 10_000, 5_000)
        # (10000 * 0.80 + 5000 * 4.0) / 1e6 = (8000 + 20000) / 1e6 = 0.028
        assert cost == pytest.approx(0.028)

    def test_known_model_grok_fast(self) -> None:
        cost = calculate_cost_usd("grok-4.1-fast", 1_000_000, 0)
        assert cost == pytest.approx(0.40)

    def test_unknown_model_returns_zero(self) -> None:
        cost = calculate_cost_usd("not-a-real-model", 1_000_000, 1_000_000)
        assert cost == 0.0

    def test_none_model_returns_zero(self) -> None:
        cost = calculate_cost_usd(None, 1_000_000, 1_000_000)
        assert cost == 0.0

    def test_zero_tokens_returns_zero(self) -> None:
        cost = calculate_cost_usd("claude-opus-4-7-20251022", 0, 0)
        assert cost == 0.0

    def test_negative_tokens_clamped_to_zero(self) -> None:
        cost = calculate_cost_usd("claude-opus-4-7-20251022", -100, -100)
        # max(0, x) clamping: input/output beide zu 0 -> 0.0
        assert cost == 0.0

    def test_only_input_tokens(self) -> None:
        cost = calculate_cost_usd("gpt-4o", 1000, 0)
        # 1000 * 2.50 / 1e6 = 0.0025
        assert cost == pytest.approx(0.0025)

    def test_only_output_tokens(self) -> None:
        cost = calculate_cost_usd("gpt-4o", 0, 1000)
        # 1000 * 10.0 / 1e6 = 0.01
        assert cost == pytest.approx(0.01)


class TestPricingTable:
    def test_table_has_canonical_models(self) -> None:
        # Smoke: the models the worker tier lists in TIER_DEFAULTS_BY_PROVIDER
        # must all be in the pricing table — otherwise
        # worker calls get tallied as "free".
        expected_models = [
            "claude-opus-4-7-20251022",
            "gemini-2.5-pro",
            "gpt-4o",
            "grok-4.1-fast",
            "deepseek-reasoner",
            "anthropic/claude-opus-4.7",
        ]
        for m in expected_models:
            assert m in PRICING_USD_PER_MTOK, f"Pricing missing for {m}"

    def test_table_has_router_models(self) -> None:
        # Router tier — cost-relevant for cost reporting.
        expected_models = [
            "claude-haiku-4-5-20251001",
            "gemini-2.5-flash",
            "gpt-4o-mini",
        ]
        for m in expected_models:
            assert m in PRICING_USD_PER_MTOK, f"Pricing missing for {m}"

    def test_table_has_live_install_models(self) -> None:
        # 2026-07-28 cost audit: these are the models a current install
        # actually runs (fast/tool tier + realtime delegates). Missing
        # entries made the dominant spend show up as $0.00.
        expected_models = [
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.1-flash-live-preview",
            "google/gemini-3.5-flash",
            "google/gemini-3.6-flash",
            "gpt-realtime-2.1",
            "grok-4.3",
        ]
        for m in expected_models:
            assert m in PRICING_USD_PER_MTOK, f"Pricing missing for {m}"

    def test_pricing_tuple_shape(self) -> None:
        for model, rates in PRICING_USD_PER_MTOK.items():
            assert isinstance(rates, tuple), f"{model}: rates is not a tuple"
            assert len(rates) == 2, f"{model}: erwartet (in, out)"
            in_rate, out_rate = rates
            assert in_rate >= 0.0, f"{model}: input-rate negativ"
            assert out_rate >= 0.0, f"{model}: output-rate negativ"
            # Output is typically expected to be pricier than input
            # (or equal, for flat pricing). We don't warn, just a sanity check.
            assert out_rate >= in_rate * 0.5, f"{model}: output rate suspiciously low"


class TestCalculateRealtimeCostUsd:
    def test_audio_rates_apply_for_live_model(self) -> None:
        # 1M audio in + 1M audio out at gemini live rates: 3.0 + 12.0
        cost = calculate_realtime_cost_usd(
            "gemini-3.1-flash-live-preview", 0, 0, 1_000_000, 1_000_000
        )
        assert cost == pytest.approx(15.0)

    def test_text_and_audio_combined(self) -> None:
        # text: (1000 * 0.75 + 500 * 4.50) / 1e6 = 0.003
        # audio: (2000 * 3.0 + 1000 * 12.0) / 1e6 = 0.018
        cost = calculate_realtime_cost_usd(
            "gemini-3.1-flash-live-preview", 1000, 500, 2000, 1000
        )
        assert cost == pytest.approx(0.021)

    def test_openai_realtime_audio(self) -> None:
        cost = calculate_realtime_cost_usd("gpt-realtime-2.1", 0, 0, 1_000_000, 0)
        assert cost == pytest.approx(32.0)

    def test_model_without_audio_entry_falls_back_to_text_rates(self) -> None:
        # gpt-4o has no realtime audio entry -> audio share priced at text rates
        cost = calculate_realtime_cost_usd("gpt-4o", 0, 0, 1_000_000, 0)
        assert cost == pytest.approx(2.50)

    def test_unknown_model_returns_zero(self) -> None:
        assert calculate_realtime_cost_usd("nope", 1000, 1000, 1000, 1000) == 0.0
        assert calculate_realtime_cost_usd(None, 1000, 1000, 1000, 1000) == 0.0

    def test_audio_table_shape(self) -> None:
        for model, rates in REALTIME_AUDIO_PRICING_USD_PER_MTOK.items():
            assert len(rates) == 2, f"{model}: expected (in, out)"
            assert rates[0] >= 0.0 and rates[1] >= 0.0
