"""Parity gate: every switchable brain provider has tier-default rows.

``TIER_DEFAULTS_BY_PROVIDER`` feeds the wizard, the provider test, the wiki
curator, and the CU brain-call path. A switchable provider card without rows
is invisible-to-broken in those surfaces, so the parity is pinned here —
including the local providers whose empty string means "plugin-side model
discovery" (first installed model), which is a valid default, not a gap.
"""

from __future__ import annotations

import pytest

from jarvis.brain.manager import TIER_DEFAULTS_BY_PROVIDER
from jarvis.ui.web.provider_spec import PROVIDERS

_SWITCHABLE_BRAIN_IDS = [
    spec.id for spec in PROVIDERS if spec.tier == "brain" and spec.brain_switchable
]


def test_switchable_brain_providers_exist() -> None:
    assert _SWITCHABLE_BRAIN_IDS, "provider_spec lists no switchable brain providers"


@pytest.mark.parametrize("provider_id", _SWITCHABLE_BRAIN_IDS)
@pytest.mark.parametrize("tier", ["router", "deep"])
def test_every_switchable_brain_provider_has_tier_row(tier: str, provider_id: str) -> None:
    assert provider_id in TIER_DEFAULTS_BY_PROVIDER[tier], (
        f"provider {provider_id!r} is switchable in provider_spec but has no "
        f"{tier!r} row in TIER_DEFAULTS_BY_PROVIDER — add one (empty string = "
        "plugin-side discovery for local providers)"
    )


def test_local_providers_use_discovery_defaults() -> None:
    """Local providers must NOT pin a hardcoded model — no server-side catalog
    is knowable ahead of time; the plugin discovers the first installed one."""
    for tier in ("router", "deep"):
        for provider_id in ("ollama",):
            assert TIER_DEFAULTS_BY_PROVIDER[tier].get(provider_id) == ""
