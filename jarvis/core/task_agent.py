"""Scoped subscription task bridge, registered by the application layer."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

Runner = Callable[..., Awaitable[str]]
_runner: Runner | None = None


@dataclass(frozen=True)
class TaskToolScope:
    names: frozenset[str]
    trace_id: UUID
    user_text: str


_scopes: dict[str, TaskToolScope] = {}


def register_runner(runner: Runner) -> None:
    global _runner
    _runner = runner


async def run_selected(**kwargs: Any) -> str:
    if _runner is None:
        raise RuntimeError("The selected subscription task runner is not ready.")
    return await _runner(**kwargs)


def register_scope(session_id: str, scope: TaskToolScope) -> None:
    _scopes[session_id] = scope


def scope_for(session_id: str | None) -> TaskToolScope | None:
    return _scopes.get(session_id or "")


def release_scope(session_id: str) -> None:
    _scopes.pop(session_id, None)


def subscription_seat(provider: str) -> tuple[str, str] | None:
    """Identity aliases, not model or feature gates."""
    return {
        "codex": ("openai-codex", "codex-cli"),
        "claude-cli": ("claude-api", "claude-cli"),
        "antigravity": ("antigravity", "agy-cli"),
        "grok-build": ("grok-build", "grok-cli"),
    }.get(provider)
