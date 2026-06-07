"""Anti-drift parity (BUG-008 class): the Codex subagent slugs are a SINGLE
source of truth (``provider_map.CODEX_SUBAGENT_SLUGS``) and every site that
accepts/dispatches them references that one object — so a new alias cannot land
in one place and be silently ignored in another.

Sites pinned:
  * provider_routes (REST ``/api/subagent/switch``) — re-exports the constant.
  * init._select_subagent_worker_kind (worker dispatch) — routes the whole set
    to the direct Codex worker.
The brain-tool path (app_control._switch_subagent) and the worker-env builder
(init._env_builder) import the same constant, so they cannot diverge by
construction.
"""
from __future__ import annotations

from jarvis.missions.init import _select_subagent_worker_kind
from jarvis.missions.worker_runtime.provider_map import (
    CODEX_SUBAGENT_CANONICAL,
    CODEX_SUBAGENT_SLUGS,
)
from jarvis.ui.web import provider_routes


def test_provider_routes_reexports_the_single_source() -> None:
    # Same object — not a second copy that could drift.
    assert provider_routes._CODEX_SUBAGENT_SLUGS is CODEX_SUBAGENT_SLUGS
    assert provider_routes._CODEX_SUBAGENT_CANONICAL == CODEX_SUBAGENT_CANONICAL


def test_accepted_slugs_all_route_to_codex_direct() -> None:
    for slug in CODEX_SUBAGENT_SLUGS:
        assert _select_subagent_worker_kind(slug, "") == "codex_direct", slug


def test_canonical_value_in_set_and_routes() -> None:
    assert CODEX_SUBAGENT_CANONICAL in CODEX_SUBAGENT_SLUGS
    assert _select_subagent_worker_kind(CODEX_SUBAGENT_CANONICAL, "") == "codex_direct"
