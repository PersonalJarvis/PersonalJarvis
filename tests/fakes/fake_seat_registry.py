"""A brain-provider registry that records every instance it builds.

Stands in for ``BrainProviderRegistry`` behind ``jarvis.brain.resolver.
_get_registry`` so the agent-seat contract can be proven: which provider was
built, with which model and constructor flags, and what credential override
and usage tag were live when the seat answered. No network, no subprocess.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from jarvis.core.protocols import BrainDelta

REVIEW_REPLY = '{"memories": [], "skill": null}'


@dataclass
class Built:
    """One instantiation: provider name and the constructor keyword arguments."""

    name: str
    kwargs: dict[str, Any]


@dataclass
class Answered:
    """One answered call: who answered, and the context it answered in."""

    name: str
    model: str
    secrets: dict[str, Any]
    caller: str
    system: str


@dataclass
class RecordingRegistry:
    """``available`` / ``get_class`` / ``instantiate`` like the real registry.

    ``cli`` names take ``structured_prompts`` (and ``prefer_subscription``
    for ``codex``) like the CLI brains; the rest take only ``model``.
    ``failing`` names raise a RuntimeError carrying a fake provider body when
    they answer; ``replies`` overrides the text a name answers with.
    """

    names: Iterable[str]
    cli: Iterable[str] = ("claude-cli", "codex", "antigravity")
    failing: Iterable[str] = ()
    replies: dict[str, str] = field(default_factory=dict)
    on_instantiate: Callable[[str], None] | None = None
    built: list[Built] = field(default_factory=list)
    answered: list[Answered] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.names = tuple(self.names)
        self.cli = frozenset(self.cli)
        self.failing = frozenset(self.failing)

    def available(self) -> list[str]:
        return sorted(self.names)

    def get_class(self, name: str) -> type:
        if name not in self.names:
            raise KeyError(f"Brain provider '{name}' not found.")
        return _brain_class(self, name)

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        cls = self.get_class(name)
        if self.on_instantiate is not None:
            self.on_instantiate(name)
        self.built.append(Built(name, dict(kwargs)))
        return cls(**kwargs)

    def built_names(self) -> list[str]:
        return [item.name for item in self.built]


class _Brain:
    def __init__(self, registry: RecordingRegistry, name: str, model: str | None) -> None:
        self._registry = registry
        self.name = name
        self._model = model or ""

    async def complete(self, request: Any):
        from jarvis.core import config
        from jarvis.costs.ledger import current_caller

        overrides = config._PROVIDER_SECRET_OVERRIDES.get()
        self._registry.answered.append(
            Answered(
                self.name,
                self._model,
                dict(overrides or {}),
                current_caller(),
                str(getattr(request, "system", "") or ""),
            )
        )
        if self.name in self._registry.failing:
            raise RuntimeError(f"{self.name}: 401 private provider body sk-leak")
        yield BrainDelta(content=self._registry.replies.get(self.name, REVIEW_REPLY))


def _brain_class(registry: RecordingRegistry, name: str) -> type:
    if name == "codex":

        class CodexLike(_Brain):
            def __init__(
                self,
                model: str | None = None,
                structured_prompts: bool = False,
                prefer_subscription: bool = False,
                subscription_text_only: bool = False,
            ) -> None:
                super().__init__(registry, name, model)

        return CodexLike
    if name in registry.cli:

        class CliLike(_Brain):
            def __init__(self, model: str | None = None, structured_prompts: bool = False) -> None:
                super().__init__(registry, name, model)

        return CliLike

    class ApiLike(_Brain):
        def __init__(self, model: str | None = None) -> None:
            super().__init__(registry, name, model)

    return ApiLike
