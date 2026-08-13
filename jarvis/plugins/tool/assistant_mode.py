"""``switch_mode`` / ``save_mode`` — the assistant changes its own character.

Two tools, one feature. ``switch_mode`` is how "be my friend for a bit" or
"back to normal" works by voice. ``save_mode`` is the end of the mode-builder
interview: the assistant has asked what kind of company the user wants, and
this is where it writes the answer down.

They live here rather than as bespoke realtime declarations because the
realtime bridge builds its tool list from this same registry
(``jarvis/realtime/tools.py``). One tool file therefore reaches the speaking
brain, the typing brain and the CLI at once — a mode built by voice is
immediately visible on the modes screen, with no second code path to keep in
step.

Risk tiers, and why they differ:

* ``switch_mode`` is ``monitor``. It changes tone, nothing else, it is visible
  on screen the moment it happens, and undoing it is one sentence. Making the
  user confirm a change of voice they just asked for out loud would be
  paperwork.
* ``save_mode`` is ``ask``. It writes a file the assistant will read on every
  future turn, and the name is one the model chose from a conversation. The
  confirmation is not friction here — it is the natural last beat of the
  interview ("shall I save this as Night Owl?"), and it is what stops a
  half-heard sentence from becoming a permanent character.

Neither tool is a spawn vehicle and neither belongs in a worker set
(AP-5/AP-14): both are direct, gated, in-process actions.
"""

from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


class SwitchModeTool:
    """Switch the active assistant mode. Applies from the next turn."""

    name: str = "switch_mode"
    risk_tier: str = "monitor"
    description: str = (
        "Switch how you behave by selecting one of the user's assistant modes "
        "(for example assistant, friend, coach, focus, coding, or one they "
        "created). Use this when the user asks you to be different — warmer, "
        "shorter, tougher, back to normal. Call list_modes first if you are "
        "not sure which modes exist. The change applies from the next turn."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": (
                    "The mode id to switch to, e.g. 'friend'. Must be one that already exists."
                ),
            },
        },
        "required": ["slug"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        del ctx
        from jarvis.brain import modes

        slug = str(args.get("slug", "")).strip()
        if not slug:
            return ToolResult(success=False, output=None, error="No mode id given.")
        try:
            mode = modes.set_active(slug)
        except modes.ModeError as exc:
            available = ", ".join(m.slug for m in modes.list_modes())
            return ToolResult(
                success=False,
                output=None,
                # The model gets the real list back rather than a bare refusal,
                # so a near-miss ("friendly") is recoverable in the same turn
                # instead of becoming an apology to the user.
                error=f"{exc} Available modes: {available}.",
            )

        override = modes.section_override()
        if override and override != mode.slug:
            # Honest rather than convenient: the switch was stored, but it is
            # not what the user will hear until they leave the section that is
            # holding the override.
            return ToolResult(
                success=True,
                output=(
                    f"Saved {mode.name} as the chosen mode, but the {override} mode "
                    "is in force while that section is open."
                ),
                error=None,
            )
        return ToolResult(success=True, output=f"Now in {mode.name} mode.", error=None)


class SaveModeTool:
    """Write a new assistant mode from what the user described."""

    name: str = "save_mode"
    risk_tier: str = "ask"
    description: str = (
        "Save a new assistant mode the user has described, or replace one they "
        "already have. Use this at the end of a conversation about how they "
        "want you to behave. Write 'character' in the second person, addressed "
        "to yourself ('You are talking to a friend, not serving a client'), and "
        "make it concrete: how to greet them, whether to have opinions, how "
        "long answers should be, what to never do. Do not restate rules about "
        "honesty or safety — those always apply and are not part of a mode."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short display name, e.g. 'Night Owl'.",
            },
            "character": {
                "type": "string",
                "description": (
                    "How you should behave in this mode, in the second person. "
                    "A few short paragraphs."
                ),
            },
            "description": {
                "type": "string",
                "description": "One line describing the mode, shown on its card.",
            },
            "emoji": {"type": "string", "description": "A single emoji for the card."},
            "verbosity": {
                "type": "string",
                "enum": ["brief", "normal", "rich"],
                "description": "How long the answers should be.",
            },
            "proactivity": {
                "type": "string",
                "enum": ["reactive", "normal", "forward"],
                "description": (
                    "How much to volunteer: 'reactive' answers only what was "
                    "asked, 'forward' thinks a step ahead."
                ),
            },
            "activate": {
                "type": "boolean",
                "description": "Switch to the mode straight after saving it.",
            },
        },
        "required": ["name", "character"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        del ctx
        from jarvis.brain import modes

        name = str(args.get("name", "")).strip()
        character = str(args.get("character", "")).strip()
        if not name or not character:
            return ToolResult(
                success=False,
                output=None,
                error="A mode needs both a name and a description of how to behave.",
            )

        try:
            mode = modes.save_mode(
                slug=modes.normalize_slug(name),
                name=name,
                character=character,
                emoji=str(args.get("emoji", "") or ""),
                description=str(args.get("description", "") or ""),
                verbosity=str(args.get("verbosity") or modes.VERBOSITY_NORMAL),
                proactivity=str(args.get("proactivity") or modes.PROACTIVITY_NORMAL),
            )
        except modes.ModeError as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except OSError as exc:
            return ToolResult(success=False, output=None, error=f"Could not save the mode: {exc}")

        if not bool(args.get("activate")):
            return ToolResult(success=True, output=f"Saved the {mode.name} mode.", error=None)
        try:
            modes.set_active(mode.slug)
        except modes.ModeError as exc:
            # The mode IS saved; only the switch failed. Reporting success
            # here would promise a character the user is not going to get.
            return ToolResult(
                success=True,
                output=f"Saved the {mode.name} mode, but could not switch to it: {exc}",
                error=None,
            )
        return ToolResult(
            success=True, output=f"Saved the {mode.name} mode and switched to it.", error=None
        )


class ListModesTool:
    """Read-only: which modes exist and which one is active."""

    name: str = "list_modes"
    risk_tier: str = "safe"
    description: str = (
        "List the user's assistant modes and which one is currently active. "
        "Use it before switching modes, or when the user asks what modes they "
        "have."
    )
    schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        del args, ctx
        from jarvis.brain import modes

        active = modes.active_slug()
        listing = [
            {
                "slug": m.slug,
                "name": m.name,
                "description": m.description,
                "active": m.slug == active,
            }
            for m in modes.list_modes()
        ]
        return ToolResult(success=True, output={"modes": listing, "active": active}, error=None)


__all__ = ["ListModesTool", "SaveModeTool", "SwitchModeTool"]
