"""Starter plans — "one key and you are done" setups for the first run.

A starter plan names a voice mode and the provider for every surface that
mode needs, all served by ONE provider family (or two). Choosing a plan in
onboarding filters the key list down to the families it needs; once every
family key is saved, the frontend applies the plan through the ordinary
switch routes (brain / tool model / agents / tts / stt / realtime / voice
mode), so nothing here duplicates the switch logic — a plan is data.

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
        id="gemini-pipeline",
        label="Pipeline with Gemini",
        summary=(
            "One Google Gemini key runs everything: thinking, the tool model, "
            "the agents, voice in and voice out."
        ),
        mode="pipeline",
        key_families=("gemini",),
        assignments={
            "brain": "gemini",
            "computer-use": "gemini",
            "subagent": "gemini",
            "tts": "gemini-flash-tts",
            "stt": "gemini-api",
        },
        recommended=True,
    ),
    StarterPlan(
        id="gemini-realtime",
        label="Realtime with Gemini",
        summary=(
            "One Google Gemini key, spoken live: Gemini Live answers in real "
            "time; tool model and agents run on the same key."
        ),
        mode="realtime",
        key_families=("gemini",),
        assignments={
            "brain": "gemini",
            "computer-use": "gemini",
            "subagent": "gemini",
            "tts": "gemini-flash-tts",
            "stt": "gemini-api",
            "realtime": "gemini-live",
        },
    ),
    StarterPlan(
        id="gemini-openai-realtime",
        label="Gemini + OpenAI",
        summary=(
            "Two keys, both engines covered: OpenAI Realtime speaks live, "
            "Gemini thinks, plans and runs the agents — and the Pipeline "
            "engine stays ready as a fallback."
        ),
        mode="realtime",
        key_families=("gemini", "openai"),
        assignments={
            "brain": "gemini",
            "computer-use": "gemini",
            "subagent": "gemini",
            "tts": "gemini-flash-tts",
            "stt": "gemini-api",
            "realtime": "openai-realtime",
        },
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
