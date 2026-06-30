"""``switch-provider`` tool — change the active brain/TTS/STT/subagent provider.

Router-tier, ``monitor`` (runs immediately, audited — no up-front confirmation).
This is the voice/chat path for "switch from Grok to Gemini", "use OpenAI for the
brain", "change TTS to Cartesia", etc.

A provider switch is REVERSIBLE and the tool speaks an honest post-change readback
(old -> new), so an STT mishear of the provider name is caught *after* the fact —
there is no need to block on an up-front yes/no, which would violate the
anti-confirmation-fatigue mandate. Forensic 2026-06-26: with ``ask`` a voice
"switch the subagent brain to antigravity" asked "really do that?" and the
two-turn voice-confirm flow then ended the session before the user could answer.
Irreversible actions (gmail send, place a call) stay ``ask``.

It switches *which provider is active* — it never sets a raw API key. The target
provider's key must already be stored (Settings tab / wizard); if it is missing
the tool returns a clean message instead of switching (AP-2: no secrets via
voice; the self-mod ``FORBIDDEN_PATTERNS`` doctrine).

Brain and TTS apply live (no restart); STT and subagent are wired once at
bootstrap and report ``requires_restart: true`` honestly (AD-OE6: no silent
drops). The actual switch reuses :mod:`jarvis.brain.app_control`, the same
3-layer persist + live-apply path the REST endpoints use.
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

_TIERS = ("brain", "tts", "stt", "subagent")


class SwitchProviderTool:
    """Switch the active provider for one tier (brain/tts/stt/subagent)."""

    name: ClassVar[str] = "switch-provider"
    # ``monitor`` → runs immediately (audited), no up-front confirmation. A
    # provider switch is reversible and the result already carries an honest
    # old -> new readback, so an STT mishear is caught after the fact instead of
    # by nagging the user before every switch (anti-confirmation-fatigue mandate;
    # ``ask`` is the one tier in ``always_confirm_tiers``). Forensic 2026-06-26.
    risk_tier: ClassVar[str] = "monitor"
    description: ClassVar[str] = (
        "Switch which AI provider is active for a given tier: 'brain' (the main "
        "assistant model), 'tts' (text-to-speech voice), 'stt' (speech-to-text), or "
        "'subagent' (the heavy background worker). Use this for requests like "
        "'switch from Grok to Gemini', 'use OpenAI for the brain', or 'change the "
        "voice to Cartesia'. This only changes which provider is ACTIVE — the "
        "target provider's API key must already be saved (it does NOT set keys). "
        "If the key is missing, it says so. Brain and TTS take effect immediately; "
        "STT and subagent need a restart."
    )
    schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "tier": {
                "type": "string",
                "enum": list(_TIERS),
                "description": "Which tier to switch: brain, tts, stt, or subagent.",
            },
            "provider": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "The provider id to activate, e.g. 'gemini', 'grok', 'openai', "
                    "'claude-api', 'cartesia', 'grok-voice', 'deepgram'."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Short reason for the switch (for the audit trail / echo).",
            },
        },
        "required": ["tier", "provider", "reason"],
        "additionalProperties": False,
        "input_examples": [
            {"tier": "brain", "provider": "gemini", "reason": "switch from Grok to Gemini"},
            {"tier": "tts", "provider": "cartesia", "reason": "user wants Cartesia voice"},
        ],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:  # noqa: ARG002
        if not isinstance(args, dict):
            return ToolResult(
                success=False, output=None, error="invalid_input: args must be an object"
            )

        tier = str(args.get("tier", "")).strip().lower()
        provider = str(args.get("provider", "")).strip()
        reason = str(args.get("reason", "")).strip()

        if tier not in _TIERS:
            return ToolResult(
                success=False,
                output=None,
                error=f"invalid_input: tier must be one of {', '.join(_TIERS)}",
            )
        if tier == "brain":
            # The active brain provider is the user's HARD choice. Jarvis (which
            # is what invokes this tool) must NOT switch it — only the user, via
            # the control CLI or the manual provider switch in the desktop app's
            # API-Keys section. TTS / STT / subagent stay voice-switchable. This
            # mirrors the self-mod writer's provider-selection lock so a brain
            # switch is refused on EVERY Jarvis-initiated path.
            return ToolResult(
                success=False,
                output={
                    "error_kind": "provider_switch_locked",
                    "tier": tier,
                    "provider": provider,
                },
                error=(
                    "provider_switch_locked: I can't switch the brain provider "
                    "myself. The active brain provider is your choice — change it "
                    "in the desktop app's API-Keys section or with the CLI."
                ),
            )
        if not provider:
            return ToolResult(
                success=False, output=None, error="invalid_input: 'provider' is required"
            )
        if not reason:
            return ToolResult(
                success=False, output=None, error="invalid_input: 'reason' is required"
            )

        try:
            from jarvis.brain.app_control import apply_provider_switch, resolve_running_cfg

            result = await apply_provider_switch(tier, provider, cfg=resolve_running_cfg())
        except Exception as exc:  # noqa: BLE001
            log.warning("switch-provider failed: %s", exc, exc_info=True)
            return ToolResult(
                success=False,
                output=None,
                error=f"switch failed: {type(exc).__name__}: {exc}",
            )

        if not result.get("ok"):
            # Validation / credential failures come back as a clean message the
            # brain reads aloud — not an exception.
            return ToolResult(
                success=False,
                output=result,
                error=result.get("error", "switch failed"),
            )

        log.info(
            "switch-provider: tier=%s %s -> %s (persisted=%s live=%s restart=%s) reason=%r",
            tier,
            result.get("old_provider"),
            result.get("new_provider"),
            result.get("persisted"),
            result.get("applied_live"),
            result.get("requires_restart"),
            reason,
        )
        return ToolResult(success=True, output=result)
