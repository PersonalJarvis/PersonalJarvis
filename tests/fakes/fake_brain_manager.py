"""A stand-in for ``BrainManager`` as the agent chat's brain runner sees it.

Records every ``generate`` call's keyword arguments, streams its reply through
``text_consumer`` in chunks, optionally publishes one tool round on the bus
under the turn's trace id (so a step mirror can be tested against real
events), and fills the override's receipt. ``switch`` / ``apply_provider_model``
raise: the one thing the brain runner must never do is move the live brain.
"""

from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.events import ActionExecuted, ActionProposed


class FakeBrainManager:
    def __init__(
        self,
        *,
        bus: Any | None = None,
        reply: str = "Hello from Jarvis",
        chunks: tuple[str, ...] = ("Hello ", "from ", "Jarvis"),
        tool: tuple[str, dict[str, Any]] | None = None,
        hold: asyncio.Event | None = None,
        fail: Exception | None = None,
        fast_model: str = "fake-fast",
    ) -> None:
        self.bus = bus
        self.reply = reply
        self.chunks = chunks
        self.tool = tool
        self.hold = hold
        self.fail = fail
        self.fast_model = fast_model
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.noted_skills: list[tuple[str, str, str]] = []
        self.seen_key: str | None = None

    # -- what the runner reads ------------------------------------------------

    def _fast_model(self, provider: str) -> str:
        return self.fast_model

    def note_skill_trigger(self, name: str, *, content: str = "", source: str = "") -> None:
        self.noted_skills.append((name, content, source))

    async def generate(self, text: str, **kwargs: Any) -> str:
        self.calls.append((text, dict(kwargs)))
        from jarvis.core.config import get_provider_secret

        override = kwargs.get("turn_override")
        provider = getattr(override, "provider", "") or ""
        try:
            self.seen_key = get_provider_secret(provider) if provider else None
        except Exception:  # noqa: BLE001 — a test box may have no secret store
            self.seen_key = None
        consumer = kwargs.get("text_consumer")
        if self.hold is not None:
            await self.hold.wait()
        if self.fail is not None:
            raise self.fail
        if self.tool is not None and self.bus is not None:
            name, args = self.tool
            trace_id = kwargs.get("trace_id")
            await self.bus.publish(
                ActionProposed(
                    trace_id=trace_id, tool_name=name, args=args, rationale="Let me look."
                )
            )
            await self.bus.publish(
                ActionExecuted(
                    trace_id=trace_id,
                    tool_name=name,
                    success=True,
                    duration_ms=3,
                    output_preview="looked",
                )
            )
        if callable(consumer):
            for chunk in self.chunks:
                consumer(chunk)
                await asyncio.sleep(0)
        if override is not None:
            override.receipt.record(
                provider=provider,
                model=getattr(override, "model", None),
                tokens_in=12,
                tokens_out=4,
                cost_usd=0.002,
                finish_reason="stop",
            )
        return self.reply

    # -- what the runner must never touch --------------------------------------

    async def switch(self, *_a: Any, **_k: Any) -> None:
        raise AssertionError("the brain runner must never switch the live brain")

    def apply_provider_model(self, *_a: Any, **_k: Any) -> bool:
        raise AssertionError("the brain runner must never rewrite the provider config")
