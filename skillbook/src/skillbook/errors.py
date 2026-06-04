"""Skillbook-wide error classes.

Construction-contract errors live here so they can be imported without pulling
in any module-specific dependencies. ``MissingAdapterError`` is raised by
factories (notably :meth:`skillbook.agent.AgentInstance.build`) when a required
adapter argument is missing — closing the loophole that previously allowed
production factories to silently fall back to in-tree test doubles.
"""

from __future__ import annotations


class SkillbookError(Exception):
    """Base class for all skillbook errors."""


class MissingAdapterError(SkillbookError):
    """Raised when a factory is invoked without a required adapter.

    The skillbook ports-and-adapters layout requires that every external
    integration (LLM, transport, IP-Symcon actor) be wired explicitly by the
    caller. Defaults that fall back to in-tree test doubles are forbidden by
    ADR-0010. Raising this error makes the missing wiring visible to the
    caller instead of silently degrading into mock-mode.
    """

    def __init__(self, adapter_name: str, *, hint: str | None = None) -> None:
        self.adapter_name = adapter_name
        msg = (
            f"Missing required adapter {adapter_name!r}. "
            f"Pass an explicit implementation; production factories do not fall back to fakes."
        )
        if hint:
            msg = f"{msg} {hint}"
        super().__init__(msg)
