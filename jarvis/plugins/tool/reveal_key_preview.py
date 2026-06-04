"""``reveal-key-preview`` tool — speak a stored API key, masked.

Router-tier, ``monitor`` (runs without nagging, but every reveal is logged).
User mandate (2026-05-31): when the user asks "what is my Gemini key", the
assistant may say a MASKED preview — the first 3 and last 3 characters only,
e.g. ``AIz...xQ2`` — never the full key, in any language.

This is a deliberate, narrow exception to AP-2 (no secrets via voice/chat): the
tool reads the stored value via :func:`jarvis.brain.app_control.masked_secret_preview`,
which returns only 6 characters and never logs the full value. On a real API key
that leaves 30+ characters hidden, so the preview alone is useless to an attacker
(the GitHub/Stripe "last 4" pattern).

Refusing the FULL key is handled in the router system prompt (a reasoned,
multilingual, non-canned refusal) — not here. This tool simply has no way to
return the full value.
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)


class RevealKeyPreviewTool:
    """Return a masked preview (first 3 + last 3 chars) of a provider's API key."""

    name: ClassVar[str] = "reveal-key-preview"
    # ``monitor``: no confirmation prompt (anti-confirmation-fatigue), but the
    # invocation is logged — appropriate for a tool that touches a secret value,
    # even though it only ever returns a 6-character mask. Mirrors wiki-ingest.
    risk_tier: ClassVar[str] = "monitor"
    description: ClassVar[str] = (
        "Tell the user a MASKED preview of one of their stored API keys: the first "
        "three and last three characters only (e.g. 'AIz...xQ2'). Use this when the "
        "user asks what their key is, e.g. 'what's my Gemini key', 'zeig mir meinen "
        "Grok-Key', 'cual es mi clave de OpenAI'. It returns only six characters and "
        "never the full key — that is intentional and safe. You must NEVER reveal the "
        "full key; if the user asks for the complete key, refuse and explain why in "
        "your own words."
    )
    schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "provider": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "The provider whose key to preview, e.g. 'gemini', 'grok', "
                    "'openai', 'claude-api', 'deepgram', 'cartesia'."
                ),
            },
        },
        "required": ["provider"],
        "additionalProperties": False,
        "input_examples": [
            {"provider": "gemini"},
            {"provider": "grok"},
        ],
    }

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:  # noqa: ARG002 — ctx required by the tool protocol
        if not isinstance(args, dict):
            return ToolResult(
                success=False, output=None, error="invalid_input: args must be an object"
            )
        provider = str(args.get("provider", "")).strip()
        if not provider:
            return ToolResult(
                success=False, output=None, error="invalid_input: 'provider' is required"
            )

        try:
            from jarvis.brain.app_control import masked_secret_preview

            result = masked_secret_preview(provider)
        except Exception as exc:  # noqa: BLE001
            log.warning("reveal-key-preview failed: %s", exc, exc_info=True)
            return ToolResult(
                success=False,
                output=None,
                error=f"could not read key preview: {type(exc).__name__}: {exc}",
            )

        if not result.get("configured"):
            return ToolResult(
                success=True,
                output={
                    "provider": provider,
                    "configured": False,
                    "message": (
                        f"No API key is stored for {provider}. You can add it in "
                        "the Settings tab."
                    ),
                },
            )
        if result.get("preview") is None:
            # Set, but too short to mask safely — never reveal it.
            return ToolResult(
                success=True,
                output={
                    "provider": provider,
                    "configured": True,
                    "preview": None,
                    "message": (
                        f"A key for {provider} is stored, but it is too short to "
                        "preview safely, so I will not read any of it out."
                    ),
                },
            )
        return ToolResult(success=True, output=result)
