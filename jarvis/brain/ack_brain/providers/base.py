"""Abstract Protocol for Pre-Thinking-Ack Flash-Brain provider adapters.

Each adapter wraps a fast LLM SDK behind a uniform async surface. The
generator orchestrator (see jarvis.brain.ack_brain.generator) holds an
adapter instance and calls ``run()`` once per user utterance, then
applies the universal post-processing pipeline (scrub, truncate,
language check, telemetry).

Contract guarantees:
- Adapters NEVER raise. All exceptions are swallowed and converted to
  a ``None`` return so the generator's silent-on-failure rule holds.
- Adapters are constructable without a valid API key (lazy auth — the
  credential lookup happens inside ``run()``, not in ``__init__``).
- Adapters respect ``temperature`` and ``max_output_tokens`` from
  their config sub-model so the user can tune latency vs creativity
  via jarvis.toml alone.

Protocol uses ``@runtime_checkable`` so contract tests can verify
isinstance() compliance without forcing adapters to inherit from a
base class — keeps the plugin surface duck-typed.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AbstractAckProvider(Protocol):
    """Uniform async surface for Flash-Brain provider adapters."""

    async def run(
        self,
        utterance: str,
        language: str,
        *,
        persona_prompt: str,
    ) -> str | None:
        """Generate a single short acknowledgment sentence.

        Args:
            utterance: The user's raw utterance (after STT-final).
            language: ISO-639 language hint, "de" or "en".
            persona_prompt: The locked system prompt that primes the LLM
                with the JARVIS butler persona. Provided by
                AckGenerator from persona_prompt.py.

        Returns:
            A short acknowledgment sentence (str) or ``None`` on any
            failure: timeout, HTTP error, empty/whitespace response,
            missing credentials, network error, etc.
        """
        ...
