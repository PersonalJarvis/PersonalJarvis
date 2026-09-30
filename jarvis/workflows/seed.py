"""Seed workflows — planted into the DB on first startup.

Philosophy: **small, immediately functional, demoable.** We want the
user, after the first launch, to open the WorkflowsView and see
meaningful examples, be able to click "Run", and get a result right away.

**Nothing here runs by itself.** Every seed is either manual or a cron
example that ships switched off; a schedule is the user's own decision
(2026-09-30 — the Morning Briefing, the one cron seed that shipped on, was
retired, see ``_retire_morning_briefing``).

- *Code Review* (manual) — git diff capture followed by a brain review.
- *URL Summary* (manual, input field ``url``) — brain_prompt with the
  template variable {{input.url}}. Demos input binding.
- *Email Digest* / *Git Standup via Telegram* (cron, **off**) — need a
  configured Telegram bot before anyone would switch them on.
"""
from __future__ import annotations

import hashlib
import logging
import time
from uuid import UUID

from .schema import (
    BrainPromptStep,
    CronTrigger,
    HarnessDispatchStep,
    ManualTrigger,
    ShellCmdStep,
    SpeakStep,
    TelegramSendStep,
    WorkflowDef,
)
from .store import WorkflowStore

log = logging.getLogger(__name__)


# Fixed UUIDs, so repeated seeding is idempotent — we recognize
# existing seed entries by their ID and let user modifications
# survive (no force overwrite).
_WF_MORNING_BRIEFING = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000001")
_WF_CODE_REVIEW = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000002")
_WF_URL_SUMMARY = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000003")
_WF_EMAIL_DIGEST = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000004")
_WF_GIT_STANDUP = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000005")


#: The Morning Briefing no longer ships (2026-09-30). It was the one seed that
#: ran by itself — a full agent turn every day at 07:30 on every install — and
#: routines are the user's own to make. An existing install still carries the
#: seeded row; ``_retire_morning_briefing`` removes it when it is still exactly
#: what shipped. These are the only traces kept to recognise that row.
_MORNING_BRIEFING_NAME = "Morning Briefing"
_MORNING_BRIEFING_CRON = "30 7 * * *"

#: Marker of the shipped v1 prompt (a greeting, no tools). A user's own edit
#: never carries it.
_LEGACY_MORNING_BRIEFING_MARKER = "Compose a short, friendly morning announcement"

#: SHA-256 of each shipped v2 prompt (BUG-212: the tool-grounded briefing, and
#: its revision that stopped guessing a weather city).
_SHIPPED_MORNING_BRIEFING_PROMPTS: frozenset[str] = frozenset({
    "780f3e1405af25fe00b7683b68ae65c773b39624cc4c2b01648edb4e86b44a54",
    "c000ce3b8f2a2f6febd3efcfb1fb78f2deb5c632edd0bb76acb20839ece54fdf",
})


def _code_review() -> WorkflowDef:
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_CODE_REVIEW,
        name="Code Review",
        description=(
            "Captures the open changes on the current git branch and asks "
            "the active brain for a concise review."
        ),
        trigger=ManualTrigger(),
        steps=(
            ShellCmdStep(
                label="Capture pending diff",
                command="git diff --no-ext-diff --",
                timeout_s=30.0,
                max_output_chars=30_000,
            ),
            BrainPromptStep(
                label="Review pending diff",
                prompt=(
                    "Review the following pending git diff. Identify concrete "
                    "bugs, security issues, regressions, and missing tests. "
                    "Prioritize findings by severity, cite the affected file "
                    "and line when possible, and return concise bullet points "
                    "in the configured output language. If the diff is empty, "
                    "say that no tracked changes are pending.\n\n"
                    "{{prev.output}}"
                ),
                max_output_chars=4_000,
            ),
            SpeakStep(
                label="Announce review result",
                text="Code review complete. {{prev.output}}",
                priority="normal",
                language="auto",
            ),
        ),
        enabled=True,
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "brain", "git"),
    )


def _url_summary() -> WorkflowDef:
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_URL_SUMMARY,
        name="URL Summary",
        description=(
            "Takes a URL as input, has the brain generate a short analysis "
            "(NO real fetch — the brain comments on what it can infer "
            "from the URL). Demos input binding via {{input.url}}."
        ),
        trigger=ManualTrigger(),
        steps=(
            BrainPromptStep(
                label="Analyze URL",
                prompt=(
                    "The user wants the following URL summarized: "
                    "{{input.url}}\n\n"
                    "Explain in 3-5 sentences, in German, what kind of page "
                    "this likely is (domain analysis, path heuristics). If "
                    "the URL is empty, say so clearly."
                ),
                max_output_chars=1200,
            ),
        ),
        enabled=True,
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "brain", "input"),
    )


def _email_digest_telegram() -> WorkflowDef:
    """The user story from the session: triage the Gmail inbox 5x a day,
    condense it into a compact summary, and push it via Telegram.

    Chain:
      1. ``shell_cmd``    → ``gws gmail +triage`` → JSON with unread emails
      2. ``brain_prompt`` → summarize the emails into 3-5 bullet points, in German
      3. ``telegram_send`` → push to the default chat from the config

    The ``gws`` CLI is installed and authenticated system-wide (documented in the
    global CLAUDE.md). The Telegram bot token + chat ID must be configured once
    by the user; until then the workflow stays disabled.

    Cron ``0 8,11,14,17,20 * * *`` → 8:00, 11:00, 14:00, 17:00, 20:00.
    """
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_EMAIL_DIGEST,
        name="Email Digest via Telegram",
        description=(
            "Triages the Gmail inbox 5x a day, creates an AI summary of the "
            "unread emails, and pushes it via Telegram. "
            "Demonstrates the Gmail+Brain+Telegram integration. "
            "Needs a configured Telegram bot — see "
            "[integrations.telegram] in jarvis.toml."
        ),
        trigger=CronTrigger(expression="0 8,11,14,17,20 * * *"),
        steps=(
            ShellCmdStep(
                label="Triage Gmail inbox",
                command="gws gmail +triage",
                timeout_s=30.0,
                max_output_chars=12000,
            ),
            BrainPromptStep(
                label="Summarize emails",
                prompt=(
                    "You receive the output of a Gmail triage tool. "
                    "Create a compact summary of the unread "
                    "emails in German:\n"
                    "- max. 5 bullet points, sorted by urgency.\n"
                    "- Each point: *Sender*: subject (in 1 sentence what it's about).\n"
                    "- If 0 emails: just return '✅ Inbox leer'.\n\n"
                    "Raw data:\n{{prev.output}}"
                ),
                max_output_chars=2000,
            ),
            TelegramSendStep(
                label="Push to Telegram",
                text="📬 *Email-Digest*\n\n{{prev.output}}",
            ),
        ),
        enabled=False,  # enable only once Telegram is configured
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "gmail", "telegram", "cron"),
    )


def _git_standup_telegram() -> WorkflowDef:
    """Weekdays at 9:00 — pushes the git status + commit log to the
    user via Telegram. Demos ``shell_cmd`` with an input variable + chaining.
    """
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_GIT_STANDUP,
        name="Git Standup via Telegram",
        description=(
            "Weekdays at 9:00: shows the last 5 commits in the current "
            "directory, has the brain write a standup-ready "
            "summary ('what got done yesterday') "
            "and sends it via Telegram."
        ),
        trigger=CronTrigger(expression="0 9 * * 1-5"),
        steps=(
            ShellCmdStep(
                label="Fetch latest commits",
                command="git log --since=24.hours --pretty=format:%h_%s",
                timeout_s=10.0,
                max_output_chars=4000,
            ),
            BrainPromptStep(
                label="Compose standup",
                prompt=(
                    "Here are the commits from the last 24 hours:\n"
                    "{{prev.output}}\n\n"
                    "Write a 3-sentence summary in German, standup-style "
                    "(What did I do? What's next? "
                    "Blockers?). If there are no commits, say so briefly and "
                    "kindly."
                ),
                max_output_chars=1000,
            ),
            TelegramSendStep(
                label="Push standup",
                text="🧑‍💻 *Dein Standup*\n\n{{prev.output}}",  # i18n-allow
            ),
        ),
        enabled=False,
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "git", "telegram", "cron"),
    )


SEED_WORKFLOWS: tuple[WorkflowDef, ...] = (
    _code_review(),
    _url_summary(),
    _email_digest_telegram(),
    _git_standup_telegram(),
)


async def ensure_seed_workflows(store: WorkflowStore) -> int:
    """Plants any missing seed workflows. Returns the number of newly created ones.

    Idempotent — if a seed workflow (by UUID) already exists, we leave it
    untouched, even if the user has changed the name/steps. This prevents
    updates to the seed code from overwriting user edits.
    """
    added = 0
    migrated = 0
    for wf in SEED_WORKFLOWS:
        existing = await store.get_workflow(str(wf.id))
        if existing is not None:
            if wf.id == _WF_CODE_REVIEW and _is_legacy_code_review(existing):
                await store.upsert_workflow(wf)
                # The user's on/off choice outlives a seed upgrade: an upsert
                # writes the seed's ``enabled``, so restore the row's own.
                await store.set_enabled(str(wf.id), bool(existing.get("enabled")))
                migrated += 1
            continue
        await store.upsert_workflow(wf)
        added += 1
    if added:
        log.info("Seed workflows written: %d new", added)
    if migrated:
        log.info("Legacy unavailable seed workflows migrated: %d", migrated)
    await _retire_morning_briefing(store)
    return added


async def _retire_morning_briefing(store: WorkflowStore) -> bool:
    """Remove the seeded Morning Briefing while it is still exactly what shipped.

    Removed rather than switched off: it no longer ships, a seed cannot be
    deleted from the desktop, and a switched-off row would be re-disabled on
    every boot the moment its owner turned it back on. A row the user edited
    (name, schedule, steps or prompt) is theirs and stays untouched.
    """
    wid = str(_WF_MORNING_BRIEFING)
    row = await store.get_workflow(wid)
    if row is None or not _is_shipped_morning_briefing(row):
        return False
    removed = await store.delete_workflow(wid)
    if removed:
        log.info(
            "Retired the shipped Morning Briefing seed (unedited, was %s)",
            "on" if row.get("enabled") else "off",
        )
    return removed


def _is_shipped_morning_briefing(row: dict[str, object]) -> bool:
    """True only for the seeded briefing as it shipped, never a user's edit."""
    if row.get("created_by") != "seed":
        return False
    try:
        definition = WorkflowDef.model_validate_json(str(row.get("def_json") or ""))
    except Exception:  # noqa: BLE001 - malformed legacy data stays user-owned
        return False
    trigger = definition.trigger
    if (
        definition.name != _MORNING_BRIEFING_NAME
        or not isinstance(trigger, CronTrigger)
        or trigger.expression != _MORNING_BRIEFING_CRON
        or len(definition.steps) != 2
    ):
        return False
    prompt_step, speak_step = definition.steps
    if not isinstance(prompt_step, BrainPromptStep) or not isinstance(speak_step, SpeakStep):
        return False
    prompt = prompt_step.prompt
    return (
        _LEGACY_MORNING_BRIEFING_MARKER in prompt
        or hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        in _SHIPPED_MORNING_BRIEFING_PROMPTS
    )


def _is_legacy_code_review(row: dict[str, object]) -> bool:
    """Identify only the shipped dead seed, never an arbitrary user workflow."""
    if row.get("created_by") != "seed":
        return False
    try:
        definition = WorkflowDef.model_validate_json(str(row.get("def_json") or ""))
    except Exception:  # noqa: BLE001 - malformed legacy data stays user-owned
        return False
    return any(
        isinstance(step, HarnessDispatchStep) and step.harness == "openclaw"
        for step in definition.steps
    )
