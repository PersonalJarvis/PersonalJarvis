"""One model budget for the runtime config and gateway, without inference calls."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

#: The Ollama context an agent gets when the person chose none. A model's
#: native window is often 256k, and Ollama allocates the whole KV cache up
#: front: a 30B model at 256k needs about 45 GB and froze a 32 GB desktop.
#: 64k is the smallest window Hermes accepts (its minimum is 64,000) and fits
#: ordinary machines; a larger ``num_ctx`` on the model card is honoured.
OLLAMA_DEFAULT_CONTEXT = 65_536


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

    Ollama uses the person's ``num_ctx`` (bounded by the native window), and
    otherwise :data:`OLLAMA_DEFAULT_CONTEXT` (bounded the same way), never the
    whole native window by default. The gateway configures that allocation on
    the provider before inference.
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
        if allocated:
            context = min(allocated, declared_context or allocated)
        else:
            context = min(context, OLLAMA_DEFAULT_CONTEXT)
        requested_output = positive_int(getattr(selected, "num_predict", None))
        if requested_output is not None:
            output = min(requested_output, declared_output or requested_output)
    # Report capacity, not an arbitrary input/output split. Unknown output
    # capacity stays unknown instead of inventing an 8k ceiling.
    return ModelLimits(context, min(output, context) if output is not None else None)
