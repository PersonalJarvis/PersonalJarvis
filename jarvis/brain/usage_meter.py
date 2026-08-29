"""Wrap a brain provider so every call it answers is written to the ledger.

Applied once, at construction, by :class:`jarvis.brain.provider_registry.
BrainProviderRegistry` — the one door every provider instance comes through
(the manager, the resolver, the health check, the vision brain). Callers keep
the object they always had: attributes forward, ``estimate_cost`` forwards,
``isinstance`` against the plugin class is the one thing that changes, and
nothing in the tree does that.

The usage block a plugin yields follows the canonical contract
(``input_tokens`` = uncached input, ``cache_hit_tokens`` = cache reads,
``output_tokens``). ``complete`` is a plain ``def`` that returns an async
generator, exactly like the plugins, so both calling conventions in the tree
keep working; the caller tag is read when ``complete`` is CALLED, so a
``usage_context`` around the call expression is enough even when the stream
is consumed later.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.costs.ledger import current_caller, record_usage

log = logging.getLogger(__name__)

_IN_KEYS = ("input_tokens", "prompt_tokens")
_OUT_KEYS = ("output_tokens", "completion_tokens")
_CACHED_KEYS = ("cache_hit_tokens", "cache_read_input_tokens", "cached_input_tokens")


def _totals(usage: dict[str, Any]) -> tuple[int, int, int]:
    def first(keys: tuple[str, ...]) -> int:
        for key in keys:
            value = usage.get(key)
            if value is not None:
                try:
                    return max(0, int(value))
                except (TypeError, ValueError):
                    # A provider that reports a non-numeric token count has told us
                    # nothing; 0 is the honest reading and metering must never
                    # break the call it is measuring.
                    return 0
        return 0

    return first(_IN_KEYS), first(_OUT_KEYS), first(_CACHED_KEYS)


class MeteredBrain:
    """A :class:`~jarvis.core.protocols.Brain` that reports what it spent."""

    __slots__ = ("_inner", "_provider_name")

    def __init__(self, inner: Any, provider_name: str) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_provider_name", provider_name)

    # -- forwarding ------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)

    def __repr__(self) -> str:  # pragma: no cover — logging nicety
        return f"MeteredBrain({self._inner!r})"

    @property
    def unwrapped(self) -> Any:
        return self._inner

    @property  # type: ignore[misc]
    def __class__(self) -> type:  # type: ignore[override]
        # ``isinstance(provider, SomePluginClass)`` keeps its answer: the
        # pipeline asks exactly that of its brain (a subscription voice brain
        # is handled differently), and a wrapper must not change it.
        return type(self._inner)

    def estimate_cost(self, req: BrainRequest) -> float:
        return float(self._inner.estimate_cost(req))

    # -- the metered call ---------------------------------------------------

    def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        caller = current_caller()
        return self._metered(self._inner.complete(req), caller, self._model_of(req))

    def _model_of(self, req: BrainRequest) -> str:
        """The model the call runs on: the request's override, else whatever
        the plugin calls its configured model."""
        for source, attr in ((req, "model"), (self._inner, "model"), (self._inner, "_model"),
                             (self._inner, "model_name"), (self._inner, "_model_name")):
            value = getattr(source, attr, None)
            if isinstance(value, str) and value:
                return value
        return ""

    async def _metered(
        self, stream: AsyncIterator[BrainDelta], caller: str, model: str
    ) -> AsyncIterator[BrainDelta]:
        tokens_in = tokens_out = tokens_cached = 0
        seen = False
        try:
            async for delta in stream:
                usage = getattr(delta, "usage", None)
                if isinstance(usage, dict) and usage:
                    # One call reports its usage once, on its last chunk; a
                    # plugin that reports running totals is covered by taking
                    # the LAST block rather than summing.
                    tokens_in, tokens_out, tokens_cached = _totals(usage)
                    seen = True
                yield delta
        finally:
            if seen:
                record_usage(
                    provider=self._provider_name,
                    model=model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    tokens_cached=tokens_cached,
                    caller=caller,
                )


def meter_brain(provider: Any, provider_name: str) -> Any:
    """Wrap once; a provider that is already metered is handed back as is."""
    if type(provider) is MeteredBrain:
        return provider
    if not callable(getattr(provider, "complete", None)):
        return provider
    return MeteredBrain(provider, provider_name)


__all__ = ["MeteredBrain", "meter_brain"]
