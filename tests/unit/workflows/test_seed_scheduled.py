"""A fresh install must actually do something on a schedule.

Audit AU-02: all three cron seeds shipped ``enabled=False`` and the only
enabled ones were ManualTrigger, so the WorkflowScheduler polled an empty list
every 60 seconds forever. Jarvis had a scheduler and nothing to schedule.

Exactly one cron seed is now on — the Morning Briefing, because it is the only
one that needs nothing a fresh install does not have. The other two need a
configured Telegram bot (and an authenticated ``gws`` CLI), so enabling them
would just manufacture failing runs on somebody else's machine.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.workflows.schema import (
    BrainPromptStep,
    CronTrigger,
    ManualTrigger,
    SpeakStep,
    WorkflowDef,
)
from jarvis.workflows.seed import (
    MORNING_BRIEFING_TOOLS,
    SEED_WORKFLOWS,
    ensure_seed_workflows,
)
from jarvis.workflows.store import WorkflowStore

_CREDENTIAL_FREE_STEPS = (BrainPromptStep, SpeakStep)


def _seed(name: str):
    return next(wf for wf in SEED_WORKFLOWS if wf.name == name)


def test_a_fresh_install_has_something_on_the_clock() -> None:
    scheduled = [
        wf
        for wf in SEED_WORKFLOWS
        if isinstance(wf.trigger, CronTrigger) and wf.enabled
    ]
    assert scheduled, "no enabled cron seed — the scheduler polls an empty list"


def test_the_morning_briefing_is_the_one_that_ships_on() -> None:
    briefing = _seed("Morning Briefing")
    assert briefing.enabled
    assert isinstance(briefing.trigger, CronTrigger)

    others = [
        wf.name
        for wf in SEED_WORKFLOWS
        if isinstance(wf.trigger, CronTrigger) and wf.enabled
    ]
    assert others == ["Morning Briefing"]


def test_the_morning_briefing_needs_no_credentials() -> None:
    """Only brain + speak. Nothing that reaches an external account, a shell,
    or a tool — so nothing that can stall on an approval nobody is there to
    give during an unattended 07:30 run."""
    briefing = _seed("Morning Briefing")
    assert briefing.steps
    for step in briefing.steps:
        assert isinstance(step, _CREDENTIAL_FREE_STEPS), (
            f"{step.kind} step needs something a fresh install may not have"
        )


def test_the_morning_briefing_pins_no_language() -> None:
    """The one resolver decides the output language, not the seed
    (CLAUDE.md §1). This seed used to hardcode German for every downloader."""
    briefing = _seed("Morning Briefing")
    speak = next(s for s in briefing.steps if isinstance(s, SpeakStep))
    assert speak.language == "auto"
    prompts = " ".join(
        s.prompt for s in briefing.steps if isinstance(s, BrainPromptStep)
    )
    assert "in German" not in prompts


def test_the_telegram_seeds_stay_off_until_telegram_is_configured() -> None:
    for name in ("Email Digest via Telegram", "Git Standup via Telegram"):
        assert not _seed(name).enabled, f"{name} needs credentials to work"


def test_the_manual_seeds_are_untouched() -> None:
    for name in ("Code Review", "URL Summary"):
        wf = _seed(name)
        assert isinstance(wf.trigger, ManualTrigger)
        assert wf.enabled


# ----------------------------------------------------------------------
# BUG-212 — version 2: a briefing, not a greeting
# ----------------------------------------------------------------------

#: Every grant is a read-side tool: nothing here can send, write or delete,
#: so an unattended run never waits on an approval nobody is there to give.
_READ_ONLY_GRANTS = frozenset({"google_calendar", "gmail", "wiki-recall", "search_web"})


def test_the_briefing_step_is_an_isolated_turn_with_read_only_tools() -> None:
    briefing = _seed("Morning Briefing")
    brain = next(s for s in briefing.steps if isinstance(s, BrainPromptStep))
    assert brain.tools == MORNING_BRIEFING_TOOLS
    assert set(brain.tools) <= _READ_ONLY_GRANTS
    assert brain.model_tier in ("auto", "fast", "deep")


def test_the_briefing_prompt_grounds_itself_and_greets_by_time_of_day() -> None:
    briefing = _seed("Morning Briefing")
    prompt = next(s.prompt for s in briefing.steps if isinstance(s, BrainPromptStep))
    assert "tool output" in prompt
    assert "Never invent" in prompt
    assert "time of day" in prompt
    assert "not connected" in prompt, "a disconnected area is skipped, not faked"
    assert "Compose a short, friendly morning announcement" not in prompt


@pytest.fixture
async def store(tmp_path: Path) -> WorkflowStore:
    s = WorkflowStore(tmp_path / "wf.sqlite")
    await s.init()
    yield s
    await s.close()


def _v1_morning_briefing(*, enabled: bool) -> WorkflowDef:
    """The shipped v1 row, the shape an installed box carries in its DB."""
    v2 = _seed("Morning Briefing")
    return v2.model_copy(update={
        "enabled": enabled,
        "steps": (
            BrainPromptStep(
                label="Generate daily summary",
                prompt=(
                    "You are Jarvis. It's currently morning. Compose a short, "
                    "friendly morning announcement (max 3 sentences) in the "
                    "configured output language."
                ),
                max_output_chars=500,
            ),
            SpeakStep(label="Play announcement", text="{{prev.output}}", language="auto"),
        ),
    })


async def test_the_shipped_v1_row_is_migrated_and_keeps_its_switch(
    store: WorkflowStore,
) -> None:
    """An installed box has the greeting seed in its DB; it must become the
    briefing without flipping the user's on/off choice."""
    await store.upsert_workflow(_v1_morning_briefing(enabled=False))

    added = await ensure_seed_workflows(store)

    assert added == len(SEED_WORKFLOWS) - 1
    row = await store.get_workflow(str(_seed("Morning Briefing").id))
    assert row is not None
    assert row["enabled"] == 0, "the user had it off — still off"
    definition = WorkflowDef.model_validate_json(row["def_json"])
    brain = next(s for s in definition.steps if isinstance(s, BrainPromptStep))
    assert brain.tools == MORNING_BRIEFING_TOOLS


async def test_a_users_own_edit_of_the_briefing_is_left_alone(
    store: WorkflowStore,
) -> None:
    edited = _seed("Morning Briefing").model_copy(update={
        "steps": (
            BrainPromptStep(prompt="Read me my own notes file and nothing else."),
            SpeakStep(text="{{prev.output}}", language="auto"),
        ),
    })
    await store.upsert_workflow(edited)

    await ensure_seed_workflows(store)

    row = await store.get_workflow(str(edited.id))
    assert row is not None
    definition = WorkflowDef.model_validate_json(row["def_json"])
    assert definition.steps[0].prompt == "Read me my own notes file and nothing else."
