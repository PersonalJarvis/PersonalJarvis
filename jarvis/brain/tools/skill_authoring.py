"""`spawn_skill_author` — Anthropic tool for main-Jarvis (Phase 7.5).

Plan-§7.5: the tool delegates skill creation via OpenClaw (Wave-4 rebrand,
frontier worker). Does not write anything itself — the `SkillAuthoringRunner`
handles the entire flow including pipeline audit.

Plan-§AD-2: exclusively main-Jarvis tier; the OpenClaw worker has no
access to this tool (recursion guard).
"""
from __future__ import annotations

import logging
from typing import Any, ClassVar

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.skills.authoring import (
    AuthoringSuccess,
    SkillAuthoringRunner,
)

_LOG = logging.getLogger(__name__)


class SpawnSkillAuthorTool:
    """Plan-§7.5 Tool: spawn_skill_author."""

    name: ClassVar[str] = "spawn_skill_author"
    risk_tier: ClassVar[str] = "ask"
    description: ClassVar[str] = (
        "Use this whenever the user asks to create, build, or design a skill, "
        "or describes a workflow they want Jarvis to learn permanently. Also "
        "for 'automate X' or 'jedes Mal wenn Y, dann Z'. Returns a draft skill "
        "that the user must explicitly activate in the UI — does NOT auto-enable."
    )
    schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "User-Beschreibung der Skill-Funktion in natürlicher Sprache."
                ),
            },
            "suggested_name": {
                "type": "string",
                "description": (
                    "Optionaler Name-Vorschlag (kebab-case, sonst leer lassen)."
                ),
            },
            "trigger_hint": {
                "type": "string",
                "description": (
                    "Optionaler Voice/Hotkey/Cron-Hinweis. Leerer String erlaubt."
                ),
            },
        },
        "required": ["intent", "suggested_name", "trigger_hint"],
        "additionalProperties": False,
        "strict": True,
        "input_examples": [
            {
                "intent": "Pause Spotify whenever I say something",
                "suggested_name": "spotify-auto-pause",
                "trigger_hint": "voice trigger 'pause spotify'",
            },
            {
                "intent": "Create a daily morning routine that reads my calendar",
                "suggested_name": "",
                "trigger_hint": "cron 0 7 * * *",
            },
        ],
    }

    def __init__(self, *, runner: SkillAuthoringRunner) -> None:
        self._runner = runner

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:  # noqa: ARG002
        if not isinstance(args, dict):
            return ToolResult(
                success=False, output=None, error="invalid_input: args must be a dict"
            )
        intent = args.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            return ToolResult(
                success=False,
                output=None,
                error="invalid_input: 'intent' (non-empty string) required",
            )
        suggested_name = args.get("suggested_name") or None
        trigger_hint = args.get("trigger_hint") or None

        result = await self._runner.author(
            intent.strip(),
            suggested_name=suggested_name if isinstance(suggested_name, str) else None,
            trigger_hint=trigger_hint if isinstance(trigger_hint, str) else None,
        )

        if isinstance(result, AuthoringSuccess):
            return ToolResult(
                success=True,
                output={
                    "skill_name": result.skill_name,
                    "slug": result.slug,
                    "draft_path": str(result.draft_path),
                    "iterations": result.iterations,
                    "forced_state_override": result.forced_state_override,
                    "review_url": result.review_url,
                    "message": (
                        f"Skill-Entwurf für '{result.skill_name}' liegt in der UI. "
                        "Schau drüber und aktivier ihn dort — bis dahin triggert er nicht."
                    ),
                },
            )
        # AuthoringFailure
        return ToolResult(
            success=False,
            output={
                "error_kind": result.error_kind,
                "iterations": result.iterations,
                "validation_errors": list(result.validation_errors),
            },
            error=f"{result.error_kind}: {result.message}",
        )
