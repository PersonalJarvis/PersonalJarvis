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

from jarvis.workflows.schema import (
    BrainPromptStep,
    CronTrigger,
    ManualTrigger,
    SpeakStep,
)
from jarvis.workflows.seed import SEED_WORKFLOWS

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
