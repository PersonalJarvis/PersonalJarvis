"""One model budget for the runtime config and gateway, without inference calls."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelLimits:
    context_window: int = 32_768
    max_output_tokens: int | None = None

    def wire(self) -> dict[str, int]:
        # Hermes probes context_length / max_output_tokens; OpenClaw receives
        # the same values as contextWindow / maxTokens in its provider config.
        result = {"context_length": self.context_window}
        if self.max_output_tokens is not None:
            result["max_output_tokens"] = self.max_output_tokens
        return result


def positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def resolve_limits(config: Any, provider: str, model: str, metadata: Any = None) -> ModelLimits:
    """Prefer declared per-model limits; use the plugin's budget when unknown.

    Ollama uses the native window unless the user selected a smaller num_ctx.
    The gateway configures that allocation on the provider before inference.
    No model names, vendor guesses, credentials or model calls are involved.
    """
    from jarvis.agent_chat.runner_api import BRAIN_BY_PROVIDER

    defaults = ModelLimits()
    context = defaults.context_window
    output = defaults.max_output_tokens
    plugin = BRAIN_BY_PROVIDER.get(provider)
    if plugin is not None:
        module, name = plugin
        cls = getattr(importlib.import_module(module), name)
        context = positive_int(getattr(cls, "context_window", None)) or context
        output = positive_int(getattr(cls, "max_output_tokens", None)) or output
    elif provider == "openai-codex":
        from jarvis.live.subscription_reasoning import SubscriptionReasoningBrain

        context = SubscriptionReasoningBrain.context_window
    declared_context = positive_int(getattr(metadata, "context_length", None))
    declared_output = positive_int(getattr(metadata, "max_output_tokens", None))
    if declared_context is not None:
        context = declared_context
    if declared_output is not None:
        output = declared_output
    if provider == "ollama":
        providers = getattr(getattr(config, "brain", None), "providers", {})
        options = getattr(providers.get(provider), "models", {})
        selected = options.get(model) or options.get(f"{model}:latest")
        allocated = positive_int(getattr(selected, "num_ctx", None))
        context = min(allocated, declared_context or allocated) if allocated else context
        requested_output = positive_int(getattr(selected, "num_predict", None))
        if requested_output is not None:
            output = min(requested_output, declared_output or requested_output)
    # Report capacity, not an arbitrary input/output split. Unknown output
    # capacity stays unknown instead of inventing an 8k ceiling.
    return ModelLimits(context, min(output, context) if output is not None else None)
