"""Anti-drift parity (§7) for the provider->vendor wire contract.

``PROVIDER_VENDORS`` is a wire-format vocabulary shared across the client config,
the proxy, the usage rows, and the UI. This freezes the canonical expectation so
any drift in the proxy's table (a provider added/removed/re-vendored, or a base
URL changed) fails loudly here — the proxy-side half of the five-layer parity
pattern. The client side carries the mirror of this same EXPECTED table.
"""

from __future__ import annotations

from keyproxy import vendors

# The canonical wire contract — must match the client side EXACTLY.
EXPECTED_PROVIDER_VENDORS: dict[str, tuple[str, str]] = {
    "claude-api": ("anthropic", "https://api.anthropic.com"),
    "openai": ("openai_compatible", "https://api.openai.com/v1"),
    "openrouter": ("openai_compatible", "https://openrouter.ai/api/v1"),
    "grok": ("openai_compatible", "https://api.x.ai/v1"),
    "gemini": ("gemini", "https://generativelanguage.googleapis.com"),
    "groq-api": ("openai_compatible", "https://api.groq.com/openai/v1"),
}


def test_provider_vendor_map_matches_wire_contract() -> None:
    assert vendors.PROVIDER_VENDORS == EXPECTED_PROVIDER_VENDORS


def test_every_provider_vendor_is_known() -> None:
    used = {vendor for vendor, _base in vendors.PROVIDER_VENDORS.values()}
    assert used <= vendors.KNOWN_VENDORS


def test_every_known_vendor_handles_all_three_concerns() -> None:
    """extract / place / parse must each understand every known vendor."""
    for vendor in vendors.KNOWN_VENDORS:
        # extraction: an inbound credential slot exists for the vendor.
        extracted = (
            vendors.extract_inbound_token(
                vendor, {"authorization": "Bearer t"}, query={}
            )
            or vendors.extract_inbound_token(vendor, {"x-api-key": "t"}, query={})
            or vendors.extract_inbound_token(
                vendor, {"x-goog-api-key": "t"}, query={}
            )
        )
        assert extracted == "t", f"no inbound extraction for vendor {vendor!r}"

        # placement: the real key lands somewhere on the outbound request.
        headers, query = vendors.place_outbound_credential(
            vendor, headers={}, query={}, real_key="REAL"
        )
        placed = any("REAL" in v for v in headers.values()) or any(
            "REAL" in v for v in query.values()
        )
        assert placed, f"no outbound placement for vendor {vendor!r}"

        # parse: a parse miss returns None (not an exception) for the vendor.
        assert vendors.parse_usage(vendor, b"") is None


def test_no_unexpected_providers_present() -> None:
    # Guards against a provider being added to the proxy without updating the
    # client mirror (and this expectation).
    assert set(vendors.PROVIDER_VENDORS) == set(EXPECTED_PROVIDER_VENDORS)
