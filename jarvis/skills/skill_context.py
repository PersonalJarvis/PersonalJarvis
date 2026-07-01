"""Context registry for the skill system (skills-brain integration).

Modeled on: ``jarvis/harness/computer_use_context.py``.

The speech pipeline (pre-brain hook) and the ``run_skill`` tool need
access to SkillRegistry + SkillRunner. Both components are instantiated at
different places (BrainManager factory vs. pipeline bootstrap), and the
plugin tools are loaded via ``entry_points`` without args. Instead of
threading cumbersome DI through every layer, we keep
a process-wide context.

The app calls ``set_skill_context(ctx)`` once at startup; consumers
fetch the context with ``get_skill_context()`` (raises RuntimeError
if not set) or ``try_get_skill_context()`` (None if not
set — for optional hooks that can gracefully skip).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import SkillRegistry
    from .runner import SkillRunner


@dataclass
class SkillContext:
    """All deps of the skill path in one place."""
    registry: SkillRegistry
    runner: SkillRunner


_CONTEXT: SkillContext | None = None


def set_skill_context(ctx: SkillContext | None) -> None:
    """Sets or clears (ctx=None) the global skill context."""
    global _CONTEXT
    _CONTEXT = ctx
    # Real-boot registration point for paired-skill capabilities. The brain
    # boot-seed (factory.build_default_brain) runs BEFORE the app sets this
    # context (desktop_app builds the brain, then sets the context inside
    # _start_speech_and_orb), so the boot-seed sees no context. Registering the
    # paired caps here is what makes them actually land in a real boot.
    # Best-effort: a failure must never break context setup.
    if ctx is not None:
        try:
            from jarvis.core.capabilities import get_registry
            from jarvis.skills.plugin_coupling import register_paired_capabilities

            register_paired_capabilities(get_registry(), ctx.registry.list())
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).debug(
                "paired-cap registration on set_skill_context failed", exc_info=True
            )


def get_skill_context() -> SkillContext:
    """Returns the set context, or raises a clear error message."""
    if _CONTEXT is None:
        raise RuntimeError(
            "Skill context not set. "
            "The main app must call `set_skill_context(...)` "
            "before the first skill call.",
        )
    return _CONTEXT


def try_get_skill_context() -> SkillContext | None:
    """Returns the context if set, otherwise None.

    For code paths that use skills optionally (e.g. the pre-brain hook
    in the speech pipeline may gracefully skip if the registry
    wasn't set up, such as in headless mock mode).
    """
    return _CONTEXT
