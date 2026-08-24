"""Starter plans must only name providers that exist, in the tier they claim.

BUG-008 shape: the plan catalog and the provider catalog are two lists of the
same ids. A renamed provider must fail here, not on someone's first run.
"""
from __future__ import annotations

import pytest

from jarvis.core import config as cfg
from jarvis.setup import state as st
from jarvis.setup.starter_plans import (
    READY_SECTIONS_BY_MODE,
    STARTER_PLANS,
    get_plan,
    plan_ready_sections,
)
from jarvis.ui.web.provider_spec import get_spec

_SURFACE_TIER = {
    "brain": "brain",
    "computer-use": "brain",
    "subagent": "brain",
    "tts": "tts",
    "stt": "stt",
    "realtime": "realtime",
}


@pytest.mark.parametrize("plan", STARTER_PLANS, ids=lambda p: p.id)
def test_plan_assignments_point_at_real_providers_of_the_right_tier(plan) -> None:
    assert plan.mode in READY_SECTIONS_BY_MODE
    for surface, provider_id in plan.assignments.items():
        spec = get_spec(provider_id)
        assert spec is not None, f"{plan.id}: unknown provider {provider_id!r} for {surface}"
        assert spec.tier == _SURFACE_TIER[surface], (
            f"{plan.id}: {provider_id} is a {spec.tier} provider, {surface} needs "
            f"{_SURFACE_TIER[surface]}"
        )
        if surface in ("brain", "computer-use"):
            assert spec.brain_switchable is not False


@pytest.mark.parametrize("plan", STARTER_PLANS, ids=lambda p: p.id)
def test_plan_key_families_have_a_primary_slot(plan) -> None:
    for family in plan.key_families:
        assert cfg.secret_family_primary_slot(family), f"{plan.id}: {family} has no key slot"


def test_realtime_plans_assign_a_realtime_provider() -> None:
    for plan in STARTER_PLANS:
        if plan.mode == "realtime":
            assert "realtime" in plan.assignments, plan.id


def test_every_ready_section_is_a_known_section_health_key() -> None:
    from jarvis.ui.web.provider_routes import _SECTION_HEALTH_KEYS

    for sections in READY_SECTIONS_BY_MODE.values():
        for name in sections:
            assert name in _SECTION_HEALTH_KEYS, name


def test_exactly_one_recommended_plan() -> None:
    assert sum(1 for p in STARTER_PLANS if p.recommended) == 1
    assert get_plan("gemini-pipeline") is not None
    assert plan_ready_sections("pipeline") == ("brain", "computer-use", "tts", "stt")
    assert plan_ready_sections("nope") == ()


def test_state_roundtrip_and_reset(tmp_path) -> None:
    p = tmp_path / "setup_state.json"
    assert st.get_starter_plan(p) is None
    assert st.is_ready_celebrated(p) is False
    st.set_starter_plan("gemini-pipeline", p)
    st.mark_ready_celebrated(p)
    assert st.get_starter_plan(p) == "gemini-pipeline"
    assert st.is_ready_celebrated(p) is True
    removed = st.reset_onboarding(p)
    assert "starter_plan" in removed and "ready_celebrated_at" in removed
    assert st.get_starter_plan(p) is None
