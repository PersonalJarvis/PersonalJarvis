"""Nothing the app ships runs on a schedule by itself.

The Morning Briefing used to be the one cron seed that shipped switched on: a
full agent turn at 07:30 every day on every install, spoken aloud. The
maintainer retired it (2026-09-30) — routines are the user's own to make. New
installs no longer get it, and an existing install's seeded row is removed
while it is still exactly what shipped; a row the user edited stays theirs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jarvis.workflows import seed as seed_module
from jarvis.workflows.schema import (
    BrainPromptStep,
    CronTrigger,
    ManualTrigger,
    SpeakStep,
    TelegramSendStep,
    WorkflowDef,
)
from jarvis.workflows.seed import SEED_WORKFLOWS, ensure_seed_workflows
from jarvis.workflows.store import WorkflowStore

_BRIEFING_ID = seed_module._WF_MORNING_BRIEFING

#: Stand-in for a shipped v2 prompt; the fixture registers its hash as shipped.
_SHIPPED_V2_PROMPT = "You are Jarvis, compiling the user's daily briefing.\n"


def _seed(name: str) -> WorkflowDef:
    return next(wf for wf in SEED_WORKFLOWS if wf.name == name)


def test_no_seed_runs_on_a_schedule_by_default() -> None:
    scheduled = [
        wf.name
        for wf in SEED_WORKFLOWS
        if isinstance(wf.trigger, CronTrigger) and wf.enabled
    ]
    assert scheduled == [], f"shipped switched on: {scheduled}"


def test_the_morning_briefing_is_no_longer_seeded() -> None:
    assert all(wf.id != _BRIEFING_ID for wf in SEED_WORKFLOWS)
    assert all(wf.name != "Morning Briefing" for wf in SEED_WORKFLOWS)


def test_the_telegram_seeds_stay_off_until_telegram_is_configured() -> None:
    for name in ("Email Digest via Telegram", "Git Standup via Telegram"):
        wf = _seed(name)
        assert isinstance(wf.trigger, CronTrigger)
        assert not wf.enabled, f"{name} needs credentials to work"
        assert any(isinstance(step, TelegramSendStep) for step in wf.steps)


def test_the_manual_seeds_are_untouched() -> None:
    for name in ("Code Review", "URL Summary"):
        wf = _seed(name)
        assert isinstance(wf.trigger, ManualTrigger)
        assert wf.enabled


# ----------------------------------------------------------------------
# Existing installs: the seeded Morning Briefing row is retired
# ----------------------------------------------------------------------


@pytest.fixture
async def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WorkflowStore:
    monkeypatch.setattr(
        seed_module,
        "_SHIPPED_MORNING_BRIEFING_PROMPTS",
        frozenset({hashlib.sha256(_SHIPPED_V2_PROMPT.encode("utf-8")).hexdigest()}),
    )
    s = WorkflowStore(tmp_path / "wf.sqlite")
    await s.init()
    yield s
    await s.close()


def _briefing_row(
    *,
    enabled: bool = True,
    prompt: str = _SHIPPED_V2_PROMPT,
    cron: str = "30 7 * * *",
    name: str = "Morning Briefing",
    created_by: str = "seed",
) -> WorkflowDef:
    """The row an installed box carries, shaped like the seed that shipped."""
    return WorkflowDef(
        id=_BRIEFING_ID,
        name=name,
        description="Daily 07:30 spoken briefing.",
        trigger=CronTrigger(expression=cron),
        steps=(
            BrainPromptStep(label="Compile the briefing", prompt=prompt),
            SpeakStep(label="Speak the briefing", text="{{prev.output}}", language="auto"),
        ),
        enabled=enabled,
        created_by=created_by,
    )


async def test_the_shipped_briefing_row_is_removed(store: WorkflowStore) -> None:
    await store.upsert_workflow(_briefing_row(enabled=True))

    added = await ensure_seed_workflows(store)

    assert added == len(SEED_WORKFLOWS)
    assert await store.get_workflow(str(_BRIEFING_ID)) is None


async def test_a_switched_off_shipped_row_is_removed_too(store: WorkflowStore) -> None:
    """It no longer ships and a seed cannot be deleted from the desktop."""
    await store.upsert_workflow(_briefing_row(enabled=False))

    await ensure_seed_workflows(store)

    assert await store.get_workflow(str(_BRIEFING_ID)) is None


async def test_the_shipped_v1_greeting_row_is_removed(store: WorkflowStore) -> None:
    v1_prompt = (
        "You are Jarvis. It's currently morning. Compose a short, friendly "
        "morning announcement (max 3 sentences) in the configured output language."
    )
    await store.upsert_workflow(_briefing_row(prompt=v1_prompt))

    await ensure_seed_workflows(store)

    assert await store.get_workflow(str(_BRIEFING_ID)) is None


@pytest.mark.parametrize(
    "edit",
    [
        {"prompt": "Read me my own notes file and nothing else."},
        {"cron": "0 6 * * 1-5"},
        {"name": "My mornings"},
        {"created_by": "user"},
    ],
    ids=["own-prompt", "own-schedule", "renamed", "user-created"],
)
async def test_a_briefing_the_user_made_their_own_stays(
    store: WorkflowStore, edit: dict[str, str]
) -> None:
    await store.upsert_workflow(_briefing_row(**edit))

    await ensure_seed_workflows(store)

    row = await store.get_workflow(str(_BRIEFING_ID))
    assert row is not None
    assert row["enabled"] == 1, "the user's switch is not touched either"


async def test_retirement_is_idempotent(store: WorkflowStore) -> None:
    await store.upsert_workflow(_briefing_row())

    await ensure_seed_workflows(store)
    added_again = await ensure_seed_workflows(store)

    assert added_again == 0
    assert await store.get_workflow(str(_BRIEFING_ID)) is None
    assert len(await store.list_workflows()) == len(SEED_WORKFLOWS)
