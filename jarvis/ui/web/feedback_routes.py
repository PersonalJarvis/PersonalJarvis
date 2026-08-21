"""REST API for the in-app feedback section — bug reports AND feature requests.

Endpoints:

    GET  /api/feedback/status  →  {"configured": bool, "github_url": str,
                                   "templates": {...}, "context": {...}}
    GET  /api/feedback/board   →  {"available": bool, "ideas": [...],
                                   "bugs": [...], "detail": str}
    POST /api/feedback         →  {"ok": bool, "status": str, "detail": str,
                                   "github_url": str | None}

GitHub is the PRIMARY channel for both report kinds, not a fallback: the
repository already carries the two issue forms this section drives
(``.github/ISSUE_TEMPLATE/bug_report.yml`` and ``feature_request.yml``), and
they are what puts the ``bug`` / ``enhancement`` label and the title prefix on
a report.  ``GET /status`` therefore hands the frontend the template FILENAMES
(:data:`ISSUE_TEMPLATES`) rather than letting it hardcode them — an issue
opened without ``?template=`` lands as a blank, unlabelled issue that the
maintainer has to sort by hand.

``GET /status`` is also the capability probe.  ``configured`` reports whether
this install additionally has the operator's direct dispatch channel (see
below); it is False on every fresh download.  ``context`` carries the system
fields the POST route would attach server-side, so a GitHub issue is not a
second-class report — including ``os_choice``, which is pre-matched to the
bug form's operating-system dropdown so the value is accepted verbatim.

``GET /board`` reads the project's OPEN issues from the public GitHub API so
the section can show what other people already asked for.  That read needs no
token and no login — the point being that someone who has not signed in still
sees their wish is already tracked, instead of filing a duplicate.

The POST endpoint validates the payload, enriches it with system context (app
version, OS, Python, UTC timestamp), and forwards it to a Discord webhook as
a rich embed.  A screenshot may be included as a data-URL; if present it is
sent as a multipart upload so Discord can render it as an inline image.

Outcomes (``status`` field):
    ``"sent"``            — Discord accepted the webhook (2xx).
    ``"not_configured"``  — no webhook URL is configured; nothing was sent.
    ``"discord_error"``   — Discord returned a non-2xx status.
    ``"unreachable"``     — network / timeout error reaching Discord.

The webhook URL is read exclusively via the secret store / ENV; it is never
hardcoded in source.  It is an OPERATOR-only credential (the project
maintainer's own Discord server), never something an end user can configure:

    Credential Manager key : discord_feedback_webhook_url
    ENV fallback            : DISCORD_FEEDBACK_WEBHOOK_URL

When it is not configured — the default on every fresh install — the endpoint
degrades honestly toward the END USER instead of telling them to set an
operator credential: the response's ``detail`` and ``github_url`` fields point
at the project's public GitHub issues page so they still have somewhere to
report the bug.
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import functools
import json as _json
import logging
import os
import platform
import re
import time
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jarvis.core.branding import OFFICIAL_REPO_SLUG, OFFICIAL_REPO_URL
from jarvis.core.config import get_secret

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

# Discord embed colour per feedback type.
_TYPE_COLORS: dict[str, int] = {
    "bug": 0xED4245,       # red
    "idea": 0x5865F2,      # blurple
    "question": 0xFEE75C,  # yellow
}

# Discord API limits.
_DISCORD_EMBED_DESC_MAX = 4096

# Maximum decoded size for an attached screenshot (8 MB).
_SCREENSHOT_DECODED_MAX_BYTES = 8 * 1024 * 1024

_SECRET_KEY = "discord_feedback_webhook_url"  # noqa: S105 — key NAME, not a value
_ENV_KEY = "DISCORD_FEEDBACK_WEBHOOK_URL"

# The public issues page — the primary destination for both report kinds, and
# the honest answer when the operator-only Discord webhook is absent (the
# default: that credential belongs to the project maintainer, not to the end
# user running this install).
_GITHUB_ISSUES_URL = f"{OFFICIAL_REPO_URL}/issues"

#: Report type → issue-form filename under ``.github/ISSUE_TEMPLATE/``.
#: Handed to the frontend so a prefilled issue opens the RIGHT form, which is
#: what applies the ``bug`` / ``enhancement`` label and the title prefix.
#: ``question`` has no form on purpose — questions belong in Discord or
#: Discussions (see ``.github/ISSUE_TEMPLATE/config.yml``), not in the tracker.
ISSUE_TEMPLATES: dict[str, str | None] = {
    "bug": "bug_report.yml",
    "idea": "feature_request.yml",
    "question": None,
}

#: Labels the two issue forms apply, mirrored here so the board can query them.
_LABEL_IDEA = "enhancement"
_LABEL_BUG = "bug"

# --- Board (public issue read) ----------------------------------------------

_GITHUB_API_ISSUES = f"https://api.github.com/repos/{OFFICIAL_REPO_SLUG}/issues"

#: Unauthenticated GitHub API allows 60 requests per hour per IP. One board
#: refresh costs two (one per label), so a 15-minute cache keeps a whole day of
#: normal use inside a fraction of that budget even with several app windows.
_BOARD_CACHE_TTL_SECONDS = 15 * 60

#: Rows per list. A feedback section shows what is popular, not the full
#: tracker — the "see all on GitHub" link covers the rest.
_BOARD_LIMIT = 8

#: The board must never delay the view. A slow GitHub answers with an empty
#: board and a reason, not with a spinner that outlives the user's patience.
_BOARD_TIMEOUT = httpx.Timeout(connect=4.0, read=8.0, write=4.0, pool=4.0)


# ----------------------------------------------------------------------
# Request / response models
# ----------------------------------------------------------------------


class FeedbackPayload(BaseModel):
    type: Literal["bug", "idea", "question"]
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=4000)
    # Optional data-URL screenshot, e.g. "data:image/png;base64,<...>".
    screenshot: str | None = Field(None)


class FeedbackResult(BaseModel):
    ok: bool
    status: Literal["sent", "not_configured", "discord_error", "unreachable"]
    detail: str
    # Populated only for status == "not_configured": a public URL the frontend
    # can render as a "report it on GitHub" link/fallback. ``None`` otherwise.
    github_url: str | None = None


class FeedbackContext(BaseModel):
    """System fields the server would attach to a dispatched report."""

    app_version: str
    os: str
    python: str
    # The same OS, collapsed onto one of the bug form's dropdown options. A
    # GitHub issue form rejects a dropdown prefill that is not an exact option
    # string, so the free-form ``os`` above cannot serve that field.
    os_choice: str


class FeedbackChannelStatus(BaseModel):
    """Capability probe result for the feedback form (GET /status)."""

    # True when this install can dispatch feedback directly (operator webhook
    # present). False on every fresh download — the frontend then opens the
    # report as a prefilled GitHub issue, which is the normal path, not a
    # degraded one.
    configured: bool
    # Public issues page of the project — always present so the frontend never
    # has to hardcode it.
    github_url: str
    # Report type → issue-form filename (see ISSUE_TEMPLATES). A type mapped to
    # None has no form and must not open the tracker.
    templates: dict[str, str | None]
    context: FeedbackContext


class BoardEntry(BaseModel):
    """One open issue as the board renders it."""

    number: int
    title: str
    url: str
    # 👍 reactions — the closest thing the tracker has to a vote count, and the
    # reason the idea list is worth showing at all.
    upvotes: int
    comments: int


class FeedbackBoard(BaseModel):
    """Open issues grouped by kind (GET /board).

    ``available`` is False whenever the lists could not be refreshed AND no
    cached copy exists — the frontend then hides the board instead of showing
    an empty one that reads as "nobody ever asked for anything".
    """

    available: bool
    ideas: list[BoardEntry]
    bugs: list[BoardEntry]
    # Why the board is unavailable, for the log and a muted UI line. Empty on
    # success. Never carries a GitHub error body verbatim.
    detail: str = ""


# Process-wide board cache. Several desktop windows share one backend, so
# without the lock each open window would spend its own pair of requests from
# the same 60/hour IP budget on the identical refresh.
_board_cache: FeedbackBoard | None = None
_board_cache_at: float = 0.0
_board_lock = asyncio.Lock()


# ----------------------------------------------------------------------
# Version helper
# ----------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _app_version() -> str:
    """Return the running app version string, with several fallback strategies.

    1. ``jarvis.__version__`` if present (editable install with metadata).
    2. The ``version = "..."`` field from ``pyproject.toml`` at repo root.
    3. ``"unknown"`` if both fail.

    Memoized: the version never changes within a running process, and the
    pyproject fallback does synchronous file IO that must not repeat on every
    request handled by the async routes.
    """
    try:
        import jarvis  # type: ignore[import]

        return jarvis.__version__  # type: ignore[attr-defined]
    except (ImportError, AttributeError) as exc:
        log.debug("feedback: jarvis.__version__ unavailable — %s", exc)

    try:
        # This file lives at <repo>/jarvis/ui/web/feedback_routes.py, so the
        # repo root (where pyproject.toml sits) is three parents up.
        pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            return m.group(1)
    except Exception as exc:  # noqa: BLE001 — best-effort, never fatal
        log.debug("feedback: pyproject.toml version probe failed — %s", exc)

    return "unknown"


def _os_choice() -> str:
    """Collapse this machine onto one of the bug form's dropdown options.

    The option strings are fixed by ``.github/ISSUE_TEMPLATE/bug_report.yml``;
    GitHub drops a dropdown prefill that does not match one exactly, so this
    must stay in step with that file.

    A Linux box with no display server is reported as the headless option
    rather than plain "Linux": that distinction is the first thing a triage
    question asks, and the box itself knows the answer better than the user.
    """
    system = platform.system()
    if system == "Windows":
        return "Windows"
    if system == "Darwin":
        return "macOS"
    if system == "Linux":
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            return "Linux"
        return "Headless server / VPS"
    # Anything else (BSD, unknown) — "Linux" is the closest option on offer and
    # a wrong dropdown is better than a dropped prefill.
    return "Linux"


def _system_context() -> FeedbackContext:
    """Gather the system fields attached to every dispatched report."""
    return FeedbackContext(
        app_version=_app_version(),
        os=platform.platform(),
        python=platform.python_version(),
        os_choice=_os_choice(),
    )


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.get("/status")
async def feedback_status() -> FeedbackChannelStatus:
    """Describe the paths this install has for filing a report.

    The frontend probes this before rendering the form.  ``templates`` drives
    the primary path — a prefilled GitHub issue on the right issue form, which
    is what gives the report its label, its title prefix and its structure.
    ``configured`` reports the additional operator-only direct channel; it is
    False on every fresh download, and that is a normal state, not a fault.

    ``context`` carries the system fields (app version, OS, Python, plus the
    dropdown-ready ``os_choice``) so a GitHub issue receives the same
    enrichment the POST route performs server-side.
    """
    webhook_url = get_secret(_SECRET_KEY, env_fallback=_ENV_KEY)
    return FeedbackChannelStatus(
        configured=bool(webhook_url),
        github_url=_GITHUB_ISSUES_URL,
        templates=dict(ISSUE_TEMPLATES),
        context=_system_context(),
    )


def _entries_from_issues(raw: object) -> list[BoardEntry]:
    """Map a GitHub issues payload onto board rows, most-upvoted first.

    Pull requests are filtered out: ``/issues`` returns them too, and a PR is
    not something a user asked for.
    """
    if not isinstance(raw, list):
        return []
    entries: list[BoardEntry] = []
    for item in raw:
        if not isinstance(item, dict) or "pull_request" in item:
            continue
        number = item.get("number")
        title = item.get("title")
        url = item.get("html_url")
        if not isinstance(number, int) or not isinstance(title, str):
            continue
        if not isinstance(url, str):
            continue
        reactions = item.get("reactions")
        upvotes = 0
        if isinstance(reactions, dict) and isinstance(reactions.get("+1"), int):
            upvotes = reactions["+1"]
        comments = item.get("comments")
        entries.append(
            BoardEntry(
                number=number,
                title=title,
                url=url,
                upvotes=upvotes,
                comments=comments if isinstance(comments, int) else 0,
            )
        )
    entries.sort(key=lambda e: (e.upvotes, e.comments), reverse=True)
    return entries[:_BOARD_LIMIT]


async def _fetch_label(client: httpx.AsyncClient, label: str) -> list[BoardEntry]:
    """Fetch one label's open issues. Raises on transport / HTTP failure."""
    resp = await client.get(
        _GITHUB_API_ISSUES,
        params={
            "state": "open",
            "labels": label,
            # Fetched wide and ranked locally: the issues API cannot sort by
            # reactions (only the search API can, at a far tighter rate limit),
            # and one page covers this tracker comfortably.
            "per_page": 50,
            "sort": "updated",
            "direction": "desc",
        },
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            # GitHub rejects an API request without a User-Agent.
            "User-Agent": f"PersonalJarvis/{_app_version()}",
        },
    )
    resp.raise_for_status()
    return _entries_from_issues(resp.json())


@router.get("/board")
async def feedback_board() -> FeedbackBoard:
    """List the project's open feature requests and bugs.

    Public, unauthenticated read — no token, no GitHub login, so it works for
    everyone including someone who has no account at all.  That is the point:
    seeing an idea already tracked prevents the duplicate that the same person
    would otherwise file.

    Cached for 15 minutes process-wide.  On a failed refresh the last good
    lists are served instead of an empty board, because a rate limit or a
    network blip is no reason to tell the user nobody ever asked for anything.
    """
    global _board_cache, _board_cache_at

    async with _board_lock:
        now = time.monotonic()
        if _board_cache is not None and now - _board_cache_at < _BOARD_CACHE_TTL_SECONDS:
            return _board_cache

        try:
            async with httpx.AsyncClient(timeout=_BOARD_TIMEOUT) as client:
                ideas = await _fetch_label(client, _LABEL_IDEA)
                bugs = await _fetch_label(client, _LABEL_BUG)
        except httpx.HTTPStatusError as exc:
            # 403 here is virtually always the unauthenticated rate limit.
            reason = (
                "rate_limited"
                if exc.response.status_code in (403, 429)
                else f"http_{exc.response.status_code}"
            )
            log.info("feedback: board refresh failed (%s)", reason)
            return _stale_or_empty(reason)
        except httpx.HTTPError as exc:
            log.info("feedback: board unreachable — %s", type(exc).__name__)
            return _stale_or_empty("unreachable")
        except ValueError as exc:  # malformed JSON body
            log.warning("feedback: board payload was not valid JSON — %s", exc)
            return _stale_or_empty("bad_payload")

        _board_cache = FeedbackBoard(available=True, ideas=ideas, bugs=bugs)
        _board_cache_at = now
        return _board_cache


def _stale_or_empty(reason: str) -> FeedbackBoard:
    """Serve the last good board after a failed refresh, else an empty one.

    Called with ``_board_lock`` held.  The cache timestamp is deliberately NOT
    advanced: the next request retries rather than sitting on stale data for a
    full TTL after a one-off blip.
    """
    if _board_cache is not None:
        return _board_cache
    return FeedbackBoard(available=False, ideas=[], bugs=[], detail=reason)


@router.post("")
async def submit_feedback(body: FeedbackPayload) -> FeedbackResult:
    """Submit user feedback or a bug report to the configured Discord webhook.

    Enriches the payload with app version, OS, Python version, and a UTC
    timestamp before dispatching.  If a screenshot data-URL is included, the
    request is sent as a Discord multipart upload so the image is rendered
    inline inside the embed.

    Returns a structured result describing whether the submission was accepted
    (``"sent"``), skipped because no webhook is configured
    (``"not_configured"``), or failed (``"discord_error"`` / ``"unreachable"``).
    The ``ok`` field is ``True`` only when Discord returned a 2xx response.
    """
    webhook_url = get_secret(_SECRET_KEY, env_fallback=_ENV_KEY)
    if not webhook_url:
        # No operator webhook on this install (the common case for every
        # downloader — that credential is the project maintainer's own, never
        # something an end user can meaningfully set). Degrade honestly:
        # point them at the public GitHub issues page instead of a message
        # that tells them to configure a credential they have no use for.
        return FeedbackResult(
            ok=False,
            status="not_configured",
            detail=(
                "This server has no feedback channel configured. "
                f"Please report this directly on GitHub: {_GITHUB_ISSUES_URL}"
            ),
            github_url=_GITHUB_ISSUES_URL,
        )

    # Gather server-side context so the client does not have to send it.
    ctx = _system_context()
    reported_at = datetime.datetime.now(datetime.UTC).isoformat()

    # Decode and size-check the screenshot if the client provided one.
    screenshot_bytes: bytes | None = None
    if body.screenshot:
        try:
            # Strip the data-URL header: "data:image/png;base64,<payload>".
            _header, _sep, b64_data = body.screenshot.partition(",")
            raw = base64.b64decode(b64_data)
            if len(raw) > _SCREENSHOT_DECODED_MAX_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Screenshot exceeds the 8 MB limit "
                        f"({len(raw):,} bytes decoded)."
                    ),
                )
            # A data-URL without a payload decodes to b"" — treat that as "no
            # screenshot" rather than attaching an empty file to Discord.
            screenshot_bytes = raw or None
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — bad b64 → skip screenshot
            log.warning("feedback: could not decode screenshot — %s", exc)
            screenshot_bytes = None

    # Build the Discord embed.
    color = _TYPE_COLORS.get(body.type, 0x99AAB5)
    embed: dict = {
        "title": body.title,
        "description": body.description[:_DISCORD_EMBED_DESC_MAX],
        "color": color,
        "fields": [
            {"name": "Type", "value": body.type.capitalize(), "inline": True},
            {"name": "App version", "value": ctx.app_version, "inline": True},
            {"name": "OS", "value": ctx.os, "inline": False},
            {"name": "Python", "value": ctx.python, "inline": True},
            {"name": "Reported at", "value": reported_at, "inline": True},
        ],
        "footer": {"text": "Personal Jarvis · in-app feedback"},
    }
    if screenshot_bytes is not None:
        embed["image"] = {"url": "attachment://screenshot.png"}

    payload_json: dict = {"embeds": [embed]}

    # Dispatch to Discord.
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if screenshot_bytes is not None:
                # Multipart upload: embed JSON in `payload_json` field + image
                # in `files[0]` so Discord renders it as the embed image.
                resp = await client.post(
                    webhook_url,
                    data={"payload_json": _json.dumps(payload_json)},
                    files={
                        "files[0]": (
                            "screenshot.png",
                            screenshot_bytes,
                            "image/png",
                        )
                    },
                )
            else:
                resp = await client.post(webhook_url, json=payload_json)
    except httpx.TimeoutException as exc:
        log.warning("feedback: timeout reaching Discord — %s", exc)
        return FeedbackResult(
            ok=False,
            status="unreachable",
            detail=f"Request timed out: {exc}",
        )
    except httpx.HTTPError as exc:
        log.warning("feedback: network error reaching Discord — %s", exc)
        return FeedbackResult(
            ok=False,
            status="unreachable",
            detail=f"Network error: {exc}",
        )

    if resp.is_success:
        log.info(
            "feedback: sent to Discord (type=%s, title=%r)", body.type, body.title
        )
        return FeedbackResult(
            ok=True,
            status="sent",
            detail="Feedback delivered to Discord.",
        )

    log.warning(
        "feedback: Discord returned %d — %s", resp.status_code, resp.text[:200]
    )
    return FeedbackResult(
        ok=False,
        status="discord_error",
        detail=f"Discord returned HTTP {resp.status_code}: {resp.text[:200]}",
    )
