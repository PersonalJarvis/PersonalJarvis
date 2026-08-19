"""Anti-drift parity: Grok Build slugs are a SINGLE source of truth."""
from __future__ import annotations

from jarvis.missions.init import _select_subagent_worker_kind
from jarvis.missions.worker_runtime.provider_map import (
    GROK_BUILD_SUBAGENT_CANONICAL,
    GROK_BUILD_SUBAGENT_SLUGS,
)
from jarvis.ui.web import provider_routes


def test_provider_routes_reexports_the_single_source() -> None:
    assert provider_routes._GROK_BUILD_SUBAGENT_SLUGS is GROK_BUILD_SUBAGENT_SLUGS
    assert provider_routes._GROK_BUILD_SUBAGENT_CANONICAL == GROK_BUILD_SUBAGENT_CANONICAL


def test_accepted_slugs_all_route_to_grok_build() -> None:
    for slug in GROK_BUILD_SUBAGENT_SLUGS:
        assert _select_subagent_worker_kind(slug, "") == "grok_build", slug


def test_canonical_value_in_set_and_routes() -> None:
    assert GROK_BUILD_SUBAGENT_CANONICAL in GROK_BUILD_SUBAGENT_SLUGS
    assert _select_subagent_worker_kind(GROK_BUILD_SUBAGENT_CANONICAL, "") == "grok_build"


def test_api_grok_slug_is_not_grok_build() -> None:
    """The xAI API-key card must stay a different worker than Grok Build."""
    assert _select_subagent_worker_kind("grok", "") != "grok_build"
