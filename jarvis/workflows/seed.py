"""Seed workflows — planted into the DB on first startup.

Philosophy: **small, immediately functional, demoable.** We want the
user, after the first launch, to open the WorkflowsView and see 3
meaningful examples, be able to click "Run", and get a result right away.

- *Morning Briefing* (cron 30 7 * * *, **enabled**) — brain_prompt → speak
  chain. An isolated agent turn over the read-only tools that are connected
  (calendar, mail, memory, web/weather) produces a spoken day briefing. It
  needs no credentials to run at all, which is exactly why it is the only
  cron seed that ships switched on.
- *Code Review* (manual) — git diff capture followed by a brain review.
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
_WF_MORNING_BRIEFING = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000001")
_WF_CODE_REVIEW = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000002")
_WF_URL_SUMMARY = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000003")
_WF_EMAIL_DIGEST = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000004")
_WF_GIT_STANDUP = UUID("4a0f9e01-5c11-4c57-9c1d-10aabb000005")


def _morning_briefing() -> WorkflowDef:
    """The one scheduled workflow that ships ON.

    Every other cron seed here needs something a fresh install does not have
    (a configured Telegram bot, an authenticated ``gws`` CLI), so shipping
    them enabled would only produce failing runs. This one needs nothing but
    a brain and a voice — both of which the app already requires — so it is
    the honest default for "Jarvis does things without being asked"
    (audit AU-02: with every cron seed disabled, the scheduler polled an
    empty list forever and a fresh install did nothing on a schedule).

    Language is NOT pinned here: the prompt asks for the configured output
    language and the speak step passes ``auto``, so the one resolver decides
    (CLAUDE.md §1). The seed used to hardcode German for everyone.

    Version 2 (BUG-212): the first seed asked for "a short, friendly morning
    announcement" and got exactly that — a greeting and a motivational
    phrase, no facts. The step now runs as an isolated agent turn with a
    read-only tool allowlist (calendar, mail, memory, web + weather) and a
    prompt that grounds every sentence in tool output. Tools that are not
    connected on an install are simply skipped, so a fresh box still gets a
    briefing — an honest one about what is and is not connected.
    """
    now_ns = time.time_ns()
    return WorkflowDef(
        id=_WF_MORNING_BRIEFING,
        name="Morning Briefing",
        description=(
            "Daily 07:30 spoken briefing: today's calendar, mail that matters, "
            "what you noted for today, headlines and weather — from the tools "
            "that are connected, nothing invented."
        ),
        trigger=CronTrigger(expression="30 7 * * *"),
        steps=(
            BrainPromptStep(
                label="Compile the briefing",
                prompt=_MORNING_BRIEFING_PROMPT,
                max_output_chars=1_600,
                tools=MORNING_BRIEFING_TOOLS,
                model_tier="auto",
            ),
            SpeakStep(
                label="Speak the briefing",
                text="{{prev.output}}",
                priority="normal",
                language="auto",
            ),
        ),
        enabled=True,
        created_at_ns=now_ns,
        created_by="seed",
        tags=("demo", "brain", "speak"),
    )


#: Read-side grants for the briefing. Every name is a live tool name or a
#: plugin prefix (``grant_matches``); a name that is not connected is skipped
#: by ``BrainManager._select_task_tools``. No tool here can send, write or
#: delete anything, so an unattended 07:30 run never waits on an approval.
MORNING_BRIEFING_TOOLS: tuple[str, ...] = (
    "google_calendar", "gmail", "wiki-recall", "search_web",
)

#: Marker of the shipped v1 prompt — how the migration recognises the old
#: seed row (and ONLY that row: a user's own edit never carries it).
_LEGACY_MORNING_BRIEFING_MARKER = "Compose a short, friendly morning announcement"

_MORNING_BRIEFING_PROMPT = """\
You are Jarvis, compiling the user's daily briefing that will be SPOKEN aloud.
The current date and time are in your context: open with the greeting that fits
the actual time of day (morning, afternoon or evening) and name the weekday.

Gather the facts with the tools you actually have in this turn. Every sentence
below must come from tool output. When a tool for an area is not available,
skip that area in one short clause (e.g. "the calendar is not connected yet")
and move on. Never invent an event, a mail, a headline or a city.

1. Calendar (a calendar tool): today's events. No events: the day is free.
   One: name it with its time. Several: the count plus the next one with its
   time. If the next event starts within the hour, say so.
2. Mail (a mail tool): unread mail. Name at most three that matter, each as
   sender and subject in one clause. Never read a mail body out.
3. Memory (wiki-recall): anything the user noted as due or planned for today.
4. World (search_web): the two or three headlines that matter most for the
   user's interests, each with its source named. Weather ONLY if a tool result
   in this turn explicitly names the user's home city: then one clause from
   search_web ("weather <city> today": current conditions, high and low).
   Never choose a city yourself, never take one from a headline or a mail; if
   no home city came back from a tool, say nothing about the weather at all.

Then write the briefing: 5 to 8 short sentences, most important first, plain
prose for speech — no bullets, no headings, no emojis, no markdown, no URLs.
Close with one sentence on what matters most today. Do not describe your
process and do not say that you searched. Write in the configured output
language.
"""


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
    _morning_briefing(),
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
            legacy = (
                (wf.id == _WF_CODE_REVIEW and _is_legacy_code_review(existing))
                or (wf.id == _WF_MORNING_BRIEFING
                    and _is_legacy_morning_briefing(existing))
            )
            if legacy:
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
    return added


def _is_legacy_morning_briefing(row: dict[str, object]) -> bool:
    """Identify only the shipped v1 greeting seed, never a user's own edit."""
    if row.get("created_by") != "seed":
        return False
    try:
        definition = WorkflowDef.model_validate_json(str(row.get("def_json") or ""))
    except Exception:  # noqa: BLE001 - malformed legacy data stays user-owned
        return False
    return any(
        isinstance(step, BrainPromptStep)
        and _LEGACY_MORNING_BRIEFING_MARKER in step.prompt
        for step in definition.steps
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
