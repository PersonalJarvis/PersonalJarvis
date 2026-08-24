"""Repo Pulse — a daily developer briefing for one GitHub repository:
yesterday's commits, the open pull requests waiting on the user, and the
issues opened since the last run."""
from __future__ import annotations

from jarvis.tasks.schema import PluginGrant
from jarvis.tasks.templates import (
    AutomationTemplate,
    LocalizedText,
    TemplateInput,
    TemplateSchedule,
)

_PROMPT = """\
You are compiling the daily Repo Pulse for the GitHub repository `{repo}` \
(format owner/name; split it at the slash into the `owner` and `repo` tool \
arguments).

Procedure — use ONLY the GitHub tools, never memory or guesses. Compute the \
cutoff as now minus 24 hours in ISO 8601 (UTC) and pass it as `since`:
1. Commits: `github/list_commits` with `since` = cutoff, `perPage` 30, \
`fields` ["sha", "commit", "author"]. Note the count, the authors and the \
first line of each message.
2. Pull requests: `github/list_pull_requests` with `state` "open", `sort` \
"updated", `direction` "desc", `perPage` 20, `fields` ["number", "title", \
"draft", "user", "requested_reviewers", "mergeable_state", "created_at"]. \
Flag a PR when it has requested reviewers, is a draft, or has \
`mergeable_state` "dirty" or "blocked". For at most 3 flagged PRs you may \
call `github/pull_request_read` with `method` "get_check_runs" to name the \
failing checks.
3. Issues: `github/list_issues` with `state` "OPEN", `since` = cutoff, \
`orderBy` "CREATED_AT", `direction` "DESC", `perPage` 20, `fields` \
["number", "title", "user", "labels", "created_at"]. Keep only issues whose \
`created_at` is after the cutoff.

Output rules:
- Write in the configured output language.
- Exactly three short sections in this order: commits, pull requests, \
issues. Each starts with a one-line heading, followed by at most 3 \
one-line bullets naming the concrete item (short SHA or #number, message or \
title, author) — most important first.
- When a section has nothing in the window, write exactly one line saying \
so under its heading — never pad with older activity.
- At most 12 lines in total. No preamble, no closing remarks, no emojis, no \
markdown tables.
- Only state facts that appear in the tool results. If a tool call fails, \
say so in one line for that section instead of guessing.
"""

TEMPLATE = AutomationTemplate(
    key="repo_pulse",
    category="developer",
    icon="git-branch",
    name=LocalizedText(
        en="Repo Pulse",
        de="Repo-Puls",  # i18n-allow
        es="Pulso del repositorio",
    ),
    description=LocalizedText(
        en=(
            "Yesterday's commits, open pull requests waiting on you, and new "
            "issues for one repository."
        ),
        de=(  # i18n-allow
            "Die Commits von gestern, offene Pull Requests, die auf dich "
            "warten, und neue Issues für ein Repository."
        ),
        es=(
            "Los commits de ayer, los pull requests abiertos que te esperan y "
            "los issues nuevos de un repositorio."
        ),
    ),
    schedule=TemplateSchedule(kind="daily", time="09:00"),
    prompt=_PROMPT,
    # A prefix grant: covers every bridged ``github/<tool>``.
    plugin_grants=(PluginGrant(plugin_id="github", scope="read"),),
    requires=("github",),
    inputs=(
        TemplateInput(
            key="repo",
            label=LocalizedText(
                en="Repository",
                de="Repository",  # i18n-allow
                es="Repositorio",
            ),
            placeholder=LocalizedText(
                en="owner/name",
                de="owner/name",  # i18n-allow
                es="owner/name",
            ),
            required=True,
        ),
    ),
    model_tier="auto",
    tags=("developer", "github", "daily"),
)
