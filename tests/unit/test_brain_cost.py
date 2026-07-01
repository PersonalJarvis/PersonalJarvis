"""Tests for jarvis.brain.cost — pricing table + calculate_cost_usd."""
from __future__ import annotations

import pytest

from jarvis.brain.cost import PRICING_USD_PER_MTOK, calculate_cost_usd


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

    def test_pricing_tuple_shape(self) -> None:
        for model, rates in PRICING_USD_PER_MTOK.items():
            assert isinstance(rates, tuple), f"{model}: rates is not a tuple"
            assert len(rates) == 2, f"{model}: erwartet (in, out)"
            in_rate, out_rate = rates
            assert in_rate >= 0.0, f"{model}: input-rate negativ"
            assert out_rate >= 0.0, f"{model}: output-rate negativ"
            # Output is typically expected to be pricier than input
            # (or equal, for flat pricing). We don't warn, just a sanity check.
            assert out_rate >= in_rate * 0.5, f"{model}: output-rate verdaechtig niedrig"
