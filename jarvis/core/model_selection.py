"""Per-operation model selection; never mutate the shared BrainManager for a call."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelSelection:
    provider: str
    model: str | None
    reasoning_effort: str = ""


operation_model: ContextVar[ModelSelection | None] = ContextVar("operation_model", default=None)


def worker_selection(config: Any) -> ModelSelection | None:
    worker = getattr(getattr(config, "brain", None), "worker", None)
    if worker is None or not getattr(worker, "provider", ""):
        return None
    from jarvis.missions.worker_runtime.provider_map import CODEX_SUBAGENT_SLUGS

    provider = worker.provider
    if provider in CODEX_SUBAGENT_SLUGS:
        provider = "codex"
    elif provider in {"claude-subscription", "claude-code"}:
        provider = "claude-cli"
    elif provider == "openai-api":
        provider = "openai"
    return ModelSelection(provider, worker.model, str(getattr(worker, "reasoning_effort", "")))


@contextmanager
def use_operation_model(selection: ModelSelection):
    token = operation_model.set(selection)
    try:
        yield
    finally:
        operation_model.reset(token)
