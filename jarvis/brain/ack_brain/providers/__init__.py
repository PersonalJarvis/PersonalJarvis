"""Provider registry for the Pre-Thinking-Ack Flash-Brain.

Maps provider-name strings (as they appear in
``[ack_brain].provider`` in jarvis.toml) to adapter classes. The
factory wires the chosen adapter into the AckGenerator at startup.

Adding a new provider:
1. Define a new ``XxxAckProviderConfig`` Pydantic sub-model in
   jarvis.brain.ack_brain.config.
2. Implement ``XxxFlashAck`` here in its own module, conforming to
   the AbstractAckProvider Protocol from base.py.
3. Add the entry to REGISTRY below.
4. Add ``"xxx"`` to ``SUPPORTED_PROVIDERS`` in config.py so the
   field validator accepts it.
"""
from __future__ import annotations

from jarvis.brain.ack_brain.providers.base import AbstractAckProvider
from jarvis.brain.ack_brain.providers.gemini import GeminiFlashAck
from jarvis.brain.ack_brain.providers.ollama import OllamaFlashAck
from jarvis.brain.ack_brain.providers.openai import OpenAIMiniAck
from jarvis.brain.ack_brain.providers.openrouter import OpenRouterFlashAck

REGISTRY: dict[str, type[AbstractAckProvider]] = {
    "gemini": GeminiFlashAck,
    "openai": OpenAIMiniAck,
    "openrouter": OpenRouterFlashAck,
    "ollama": OllamaFlashAck,
}

__all__ = [
    "REGISTRY",
    "AbstractAckProvider",
    "GeminiFlashAck",
    "OpenAIMiniAck",
    "OpenRouterFlashAck",
    "OllamaFlashAck",
]
