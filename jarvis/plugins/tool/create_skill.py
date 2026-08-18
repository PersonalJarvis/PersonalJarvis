"""``create-skill`` — the brain writes a NEW skill from the user's spoken description.

Why this tool exists (voice session 2026-08-18 17:51): the user asked, in
German, for a new "morning routine" skill — every morning at six read the new
mail, the Linear tickets and the day's calendar, then play an 80s classic on
YouTube Music.
The brain had NO way to write a skill — the only live authoring path was the
UI dialog (``POST /api/skills/creator/*``) and, indirectly, ``jarvisctl skills
draft`` + ``jarvisctl skills commit`` with JSON piped between them. The model
spent three tool rounds reading ``jarvisctl --help`` and the turn timed out.
The router tool documented as "deliberately unreachable" since 2026-07-25
(``jarvis/brain/tools/skill_authoring.py``) was unreachable because it needed
a worker runner the entry-point loader could not supply. This tool needs
nothing the loader cannot give: it resolves the live registry and the live
brain lazily at execute time (the wiki-ingest / spawn-worker resolver pattern).

What it does — ONE bounded call, no spawn:

1. builds the authoring context (attached tools + installed skills) so the
   generated steps name connectors that actually exist,
2. asks the brain ladder (active provider → Tool Model → quality → frontier)
   for a complete SKILL.md draft — name, description, voice trigger phrase,
   schedule cron when a time was named, numbered steps per tool, spoken answer
   format — via :class:`jarvis.skills.creator_service.SkillCreatorService`,
3. commits it as ``state: draft`` (AP-15 — never auto-active): the Skills view
   is where the user reads it and switches it on,
4. returns what was written so the answer can say it in one sentence.

It never writes a template with the sentence pasted in: when no brain could
author the draft, the result is an honest failure and nothing lands on disk
(``SkillCreatorUnavailable``). Direct gated action, monitor tier — an inactive
file in the user's skills folder, instantly deletable — never a spawn, so it
never enters a worker tool set (AP-5/AP-14).
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from jarvis.core.protocols import ExecutionContext, ToolResult

_LOG = logging.getLogger(__name__)

#: Bound on the free-text arguments the model may pass. A spoken description is
#: rarely over 1 000 characters; a runaway model must not ship a novel.
_MAX_INTENT_CHARS = 4000
_MAX_HINT_CHARS = 300


class CreateSkillTool:
    """Router-tier tool: author + commit a new (draft) skill from a description."""

    name: ClassVar[str] = "create-skill"
    risk_tier: ClassVar[str] = "monitor"
    description: ClassVar[str] = (
        "Create a NEW skill for this assistant from the user's description — use "
        "this when the user asks to create, build, or set up a skill, routine, or "
        "automation ('erstell mir einen Skill, der …', "  # i18n-allow: quoted phrase
        "'create a skill that …', 'jeden Morgen um 6 sollst du …'). "  # i18n-allow: quoted phrase
        "Pass the COMPLETE description in "
        "'intent': what it should do, in which order, at what time or on which "
        "phrase, which services (mail, calendar, tickets, music, …) and every "
        "preference the user mentioned. The skill is written by the assistant "
        "itself, lands as a DRAFT the user activates in the Skills view, and the "
        "result tells you its name and triggers. Do NOT use this to run an "
        "existing skill (that is run-skill) and never call it more than once per "
        "request."
    )
    schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "The full description of what the skill should do, restated "
                    "completely in the user's own words and language: actions in "
                    "order, services involved, timing/recurrence, trigger phrase, "
                    "preferences and examples the user gave."
                ),
            },
            "name": {
                "type": "string",
                "description": (
                    "Name the user gave the skill, if any (e.g. 'Morgenroutine'); "
                    "empty string when they did not name it."
                ),
            },
            "trigger_phrase": {
                "type": "string",
                "description": (
                    "The spoken phrase that should start the skill, if the user "
                    "said one ('wenn ich Morgenroutine sage'); empty string "
                    "otherwise."
                ),
            },
            "schedule": {
                "type": "string",
                "description": (
                    "When it should run by itself, as a 5-field cron expression "
                    "in local time when the user named a time or recurrence "
                    "('jeden Morgen um 6' → '0 6 * * *', 'montags um 9' → "
                    "'0 9 * * 1'); empty string when no schedule was requested."
                ),
            },
        },
        "required": ["intent", "name", "trigger_phrase", "schedule"],
        "additionalProperties": False,
        "input_examples": [
            {
                "intent": (
                    "Morgenroutine: jeden Morgen um 6 Uhr meine neuen "  # i18n-allow: example
                    "E-Mails, meine Linear-Tickets und die wichtigen "  # i18n-allow: example
                    "Kalendereinträge des Tages kurz vorlesen und danach "  # i18n-allow: example
                    "ein Lied zum Aufstehen auf YouTube Music spielen — "  # i18n-allow: example
                    "ein 80er-Klassiker wie Country Roads, jedes Mal ein "  # i18n-allow: example
                    "anderer."  # i18n-allow: example
                ),
                "name": "Morgenroutine",
                "trigger_phrase": "",
                "schedule": "0 6 * * *",
            },
            {
                "intent": (
                    "When I say 'focus time', pause the music, set Slack to do not "
                    "disturb and tell me my next meeting."
                ),
                "name": "",
                "trigger_phrase": "focus time",
                "schedule": "",
            },
        ],
    }

    def __init__(self, *, bus: Any | None = None, config: Any | None = None, **_: Any) -> None:
        # ``bus`` lets the writer publish SkillCreated for the live Skills view;
        # ``config`` feeds the brain resolvers when the live BrainManager is not
        # reachable yet. Both optional — the loader may call ``cls()``.
        self._bus = bus
        self._config = config

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        if not isinstance(args, dict):
            return ToolResult(
                success=False, output=None, error="invalid_input: args must be a dict"
            )
        intent = args.get("intent")
        if not isinstance(intent, str) or not intent.strip():
            return ToolResult(
                success=False,
                output=None,
                error="invalid_input: 'intent' (the full description) is required",
            )
        intent = intent.strip()[:_MAX_INTENT_CHARS]
        name_hint = _clean(args.get("name"))
        trigger_hint = _clean(args.get("trigger_phrase"))
        schedule = _clean(args.get("schedule"))
        language = _clean((ctx.config or {}).get("output_language")) if ctx is not None else ""

        try:
            from jarvis.skills.skill_context import try_get_skill_context

            skill_ctx = try_get_skill_context()
        except Exception:  # noqa: BLE001 — a broken skill subsystem is reported, not raised
            skill_ctx = None
        if skill_ctx is None:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "skills_unavailable: the skill system is not loaded in this "
                    "runtime — the skill was not created"
                ),
            )

        try:
            from jarvis.core import runtime_refs
            from jarvis.skills.creator_service import (
                SkillCreatorInput,
                SkillCreatorService,
                SkillCreatorUnavailable,
                build_authoring_context,
            )

            manager = runtime_refs.get_brain_manager()
            config = self._config if self._config is not None else getattr(manager, "_config", None)
            service = SkillCreatorService(
                brain=manager,
                registry=skill_ctx.registry,
                bus=self._bus,
                config=config,
                context=build_authoring_context(brain_manager=manager, registry=skill_ctx.registry),
            )
            authored = await service.author(
                SkillCreatorInput(
                    intent=intent,
                    name_hint=name_hint,
                    trigger_hint=trigger_hint,
                    schedule_hint=schedule,
                    language=language,
                )
            )
        except SkillCreatorUnavailable as exc:
            _LOG.warning("create-skill: %s", exc)
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "author_unavailable: no language model could write the skill "
                    "right now, so nothing was created — tell the user plainly and "
                    "offer to try again in a moment"
                ),
            )
        except Exception as exc:  # noqa: BLE001 — surface the writer's own message
            _LOG.warning("create-skill failed: %s", exc, exc_info=True)
            return ToolResult(success=False, output=None, error=f"create_failed: {exc}")

        draft = authored.result.draft
        triggers = [dict(t) for t in draft.get("triggers", []) or []]
        voice = [t.get("pattern") for t in triggers if t.get("type") == "voice"]
        schedules = [t.get("cron") for t in triggers if t.get("type") == "schedule"]
        output = {
            "skill_name": authored.name,
            "slug": authored.slug,
            "state": "draft",
            "path": str(getattr(authored.skill, "path", "") or ""),
            "description": str(draft.get("description", "")),
            "voice_triggers": voice,
            "schedule_crons": schedules,
            "steps_preview": _steps_preview(str(draft.get("body", ""))),
            "assumptions": list(draft.get("assumptions", []) or []),
            "questions": list(draft.get("questions", []) or []),
            "authored_by": authored.result.brain_source,
            "ui_section": "skills",
            "message": (
                f"Skill '{authored.name}' is written and waiting as a DRAFT in the "
                "Skills view. Tell the user in one or two sentences what it does "
                "and when it fires, and that they activate it there — until "
                "then it does not run. Do not read the instructions aloud."
            ),
        }
        _LOG.info(
            "create-skill: wrote %r (voice=%s, cron=%s, by=%s)",
            authored.name,
            voice,
            schedules,
            authored.result.brain_source,
        )
        return ToolResult(success=True, output=output)


def _clean(value: Any) -> str:
    return str(value).strip()[:_MAX_HINT_CHARS] if isinstance(value, str) else ""


def _steps_preview(body: str, *, limit: int = 6) -> list[str]:
    """The numbered steps of the body, trimmed — enough for the answer to name
    what the skill will do without dumping the whole card into the context."""
    steps: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if len(stripped) > 2 and stripped[0].isdigit() and stripped[1] in ".)":
            text = stripped[2:].strip().lstrip(") ").strip()
            steps.append(text[:140])
            if len(steps) >= limit:
                break
    return steps


__all__ = ["CreateSkillTool"]
