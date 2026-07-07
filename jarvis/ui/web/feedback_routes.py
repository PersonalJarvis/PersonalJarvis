"""REST API for the in-app feedback / bug-report form.

Endpoint:

    POST /api/feedback  →  {"ok": bool, "status": str, "detail": str,
                            "github_url": str | None}

The endpoint validates the payload, enriches it with system context (app
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

import base64
import datetime
import json as _json
import logging
import platform
import re
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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

_SECRET_KEY = "discord_feedback_webhook_url"
_ENV_KEY = "DISCORD_FEEDBACK_WEBHOOK_URL"

# Public fallback for every downloader when the operator-only Discord webhook
# is not configured (the default — that credential belongs to the project
# maintainer, not to the end user running this install).
_GITHUB_ISSUES_URL = "https://github.com/PersonalJarvis/PersonalJarvis/issues"


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


# ----------------------------------------------------------------------
# Version helper
# ----------------------------------------------------------------------


def _app_version() -> str:
    """Return the running app version string, with several fallback strategies.

    1. ``jarvis.__version__`` if present (editable install with metadata).
    2. The ``version = "..."`` field from ``pyproject.toml`` at repo root.
    3. ``"unknown"`` if both fail.
    """
    try:
        import jarvis  # type: ignore[import]

        return jarvis.__version__  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        pass

    try:
        pyproject = Path(__file__).resolve().parents[4] / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if m:
            return m.group(1)
    except Exception:  # noqa: BLE001 — best-effort, never fatal
        pass

    return "unknown"


# ----------------------------------------------------------------------
# Endpoint
# ----------------------------------------------------------------------


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
    version = _app_version()
    os_info = platform.platform()
    py_version = platform.python_version()
    reported_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

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
            screenshot_bytes = raw
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
            {"name": "App version", "value": version, "inline": True},
            {"name": "OS", "value": os_info, "inline": False},
            {"name": "Python", "value": py_version, "inline": True},
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
