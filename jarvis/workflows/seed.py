"""Seed workflows — planted into the DB on first startup.

Philosophy: **small, immediately functional, demoable.** We want the
user, after the first launch, to open the WorkflowsView and see 3
meaningful examples, be able to click "Run", and get a result right away.

- *Morning Briefing* (cron 30 7 * * *) — brain_prompt → speak chain. Produces
  a mini standup announcement. No external service needed.
- *Code Review* (manual) — harness_dispatch to OpenClaw.
- *URL Summary* (manual, input field ``url``) — brain_prompt with the
  template variable {{input.url}}. Demos input binding.
"""
from __future__ import annotations

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
_WF_MORGEN_BRIEFING = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000001")
_WF_CODE_REVIEW = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000002")
_WF_URL_SUMMARY = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000003")
_WF_EMAIL_DIGEST = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000004")
_WF_GIT_STANDUP = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000005")


def _morgen_briefing() -> WorkflowDef:
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_MORGEN_BRIEFING,
        name="Morning Briefing",
        description=(
            "Daily 7:30 announcement: current time, day of week, and a short, "
            "friendly greeting. Demonstrates a brain_prompt → speak chain."
        ),
        trigger=CronTrigger(expression="30 7 * * *"),
        steps=(
            BrainPromptStep(
                label="Generate daily summary",
                prompt=(
                    "You are Jarvis. It's currently morning. Compose a short, "
                    "friendly morning announcement (max 3 sentences, in German). Include "
                    "the day of the week and a short motivating remark. NO "
                    "emojis, NO stating the time — the user can already see that."
                ),
                max_output_chars=500,
            ),
            SpeakStep(
                label="Play announcement",
                text="{{prev.output}}",
                priority="normal",
                language="de",
            ),
        ),
        enabled=False,  # cron default off — user must enable deliberately
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "brain", "speak"),
    )


def _code_review() -> WorkflowDef:
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_CODE_REVIEW,
        name="Code Review",
        description=(
            "Analyzes the open changes on the current git branch via the "
            "OpenClaw harness."
        ),
        trigger=ManualTrigger(),
        steps=(
            HarnessDispatchStep(
                label="OpenClaw dispatch",
                harness="openclaw",
                prompt=(
                    "Review all pending changes on the current git branch. "
                    "Identify potential bugs, security issues, or style "
                    "inconsistencies. Return a bullet-point summary in German."
                ),
                allow_computer_use=False,
            ),
            SpeakStep(
                label="Announce review result",
                text="Code-Review fertig. {{prev.output}}",  # i18n-allow
                priority="normal",
                language="de",
            ),
        ),
        enabled=True,
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "harness", "git"),
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
    _morgen_briefing(),
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
    for wf in SEED_WORKFLOWS:
        existing = await store.get_workflow(str(wf.id))
        if existing is not None:
            continue
        await store.upsert_workflow(wf)
        added += 1
    if added:
        log.info("Seed workflows written: %d new", added)
    return added
