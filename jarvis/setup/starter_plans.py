"""Starter plans for the live voice portion of first-run setup.

A starter plan names the live voice provider and the thinking model it needs.
Sub-Agent access is connected separately in onboarding through an API key or
subscription. The frontend applies each plan through the ordinary switch
routes, so nothing here duplicates the switch logic.

Provider ids are the catalog ids from ``jarvis.ui.web.provider_spec``; a
unit test pins every id to an existing spec of the right tier so a renamed
provider fails the build instead of a fresh install.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Sections the section-health rollup must report ``ok`` for before a mode
#: counts as "ready": Pipeline = brain + tool model + voice out + voice in,
#: Realtime = live voice + tool model + agents.
READY_SECTIONS_BY_MODE: dict[str, tuple[str, ...]] = {
    "pipeline": ("brain", "computer-use", "tts", "stt"),
    "realtime": ("realtime", "computer-use", "subagents"),
}


@dataclass(frozen=True, slots=True)
class StarterPlan:
    id: str
    label: str
    summary: str
    mode: str  # "pipeline" | "realtime"
    #: Provider families whose primary key the plan needs (config families).
    key_families: tuple[str, ...]
    #: Surface → provider id. Applied in this order by the frontend.
    assignments: dict[str, str] = field(default_factory=dict)
    recommended: bool = False


STARTER_PLANS: tuple[StarterPlan, ...] = (
    StarterPlan(
        id="openai-live",
        label="OpenAI GPT-Live",
        summary="Live conversation and its thinking model use one OpenAI API key.",
        mode="realtime",
        key_families=("openai",),
        assignments={"brain": "openai", "computer-use": "openai", "realtime": "openai-live"},
        recommended=True,
    ),
    StarterPlan(
        id="gemini-live",
        label="Gemini Live",
        summary="Live conversation uses a Gemini Live API key.",
        mode="realtime",
        key_families=("gemini",),
        assignments={"brain": "gemini", "computer-use": "gemini", "realtime": "gemini-live"},
    ),
)

#: The escape hatch: no assignments, the full provider list, no auto-apply.
CUSTOM_PLAN_ID = "custom"


def get_plan(plan_id: str) -> StarterPlan | None:
    for plan in STARTER_PLANS:
        if plan.id == plan_id:
            return plan
    return None


def plan_ready_sections(mode: str) -> tuple[str, ...]:
    return READY_SECTIONS_BY_MODE.get(mode, ())


__all__ = [
    "CUSTOM_PLAN_ID",
    "READY_SECTIONS_BY_MODE",
    "STARTER_PLANS",
    "StarterPlan",
    "get_plan",
    "plan_ready_sections",
]
