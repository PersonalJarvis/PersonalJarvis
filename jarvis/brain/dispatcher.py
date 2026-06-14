"""BrainDispatcher: higher-level wrapper around the tool-use loop with config integration.

A `BrainDispatcher` holds a Brain instance + Tool-Registry + ToolExecutor
and exposes two main methods:

- `dispatch(user_text)` → complete run (with tool use, if needed)
- `stream(user_text)` → yields text chunks as they arrive

The dispatcher is intentionally simple and holds NO conversation state —
that is the responsibility of `BrainManager` (so we can switch between providers).
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from jarvis.core.protocols import Brain, BrainMessage, BrainRequest, ImageBlock, Tool
from jarvis.safety.tool_executor import ToolExecutor

from .iteration_budget import IterationBudget
from .streaming import StreamingAggregate, aggregate_with_consumer, tee_text
from .tool_use_loop import ToolUseLoop


class BrainDispatcher:
    """Single-shot dispatch: takes text, returns a StreamingAggregate."""

    def __init__(
        self,
        brain: Brain,
        *,
        tools: dict[str, Tool] | None = None,
        executor: ToolExecutor | None = None,
        system_prompt: str | None = None,
        max_turns: int = 15,
        max_tokens_total: int = 50_000,
        max_tokens: int = 8192,
    ) -> None:
        self._brain = brain
        self._tools = tools or {}
        self._executor = executor
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._max_tokens_total = max_tokens_total
        # Per-response output ceiling (BrainConfig.max_tokens). Distinct from
        # ``max_tokens_total`` — that is the loop-wide budget across all
        # tool-use turns; this is the cap on a single provider response so a
        # long reply is never read aloud truncated mid-sentence.
        self._max_tokens = max_tokens

    @property
    def brain(self) -> Brain:
        return self._brain

    def with_brain(self, brain: Brain) -> BrainDispatcher:
        """Return a new instance with the brain swapped out (for provider switching)."""
        return BrainDispatcher(
            brain,
            tools=self._tools,
            executor=self._executor,
            system_prompt=self._system_prompt,
            max_turns=self._max_turns,
            max_tokens_total=self._max_tokens_total,
            max_tokens=self._max_tokens,
        )

    def set_tools(self, tools: dict[str, Tool]) -> None:
        self._tools = dict(tools)

    def set_system_prompt(self, prompt: str | None) -> None:
        self._system_prompt = prompt

    async def dispatch(
        self,
        user_text: str,
        *,
        images: tuple[ImageBlock, ...] = (),
        history: list[BrainMessage] | None = None,
        trace_id: UUID | None = None,
        intent_level: str | None = None,
        text_consumer: Callable[[str], None] | None = None,
        ack_emitter: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_progress: Callable[[], None] | None = None,
        turn_context: str = "",
        reply_language: str = "auto",
    ) -> StreamingAggregate:
        """Execute a complete turn (including the tool-use loop if tools are configured).

        Args:
            user_text: User utterance (verbatim, not rephrased).
            images: Optional multimodal image blocks appended to the constructed
                user BrainMessage (e.g. permanent-vision frames from the RouterBrain).
                Providers without vision support drop them silently; the text
                path continues identically.
            history: Existing message history (optional).
            trace_id: Correlation ID for the flight recorder.
            intent_level: Classification from the router (``fast``/``deep``/``code``).
                Forwarded to the tool-use loop so the research guard and future
                tier-aware features can access it.
            ack_emitter: Optional callback for the perceived-latency
                acknowledgment pattern. Awaited once on the first iteration
                that schedules a tool call, with the first tool's name +
                input. Caller decides whether to publish a TTS announcement.
                ``None`` (default) preserves prior behavior — silent
                pre-execution.
        """
        tid = trace_id or uuid4()
        messages: list[BrainMessage] = list(history or [])
        # Wave 2 (omni-latency): per-turn dynamic context (date/awareness/wiki)
        # rides on the user message, NOT the cached system prompt, so the
        # provider prompt cache stays warm. It is never stored in history
        # (the manager appends the clean user_text there).
        user_content = f"{turn_context}\n\n{user_text}" if turn_context else user_text
        messages.append(BrainMessage(role="user", content=user_content, images=images))

        if self._tools and self._executor is not None:
            loop = ToolUseLoop(
                self._brain,
                self._tools,
                self._executor,
                system_prompt=self._system_prompt,
                budget=IterationBudget(
                    max_turns=self._max_turns,
                    max_tokens_total=self._max_tokens_total,
                ),
                max_tokens=self._max_tokens,
            )
            return await loop.run(
                messages,
                trace_id=tid,
                user_utterance=user_text,
                intent_level=intent_level,
                text_consumer=text_consumer,
                ack_emitter=ack_emitter,
                on_progress=on_progress,
                reply_language=reply_language,
            )

        # Simple mode: no tool use, streaming only
        req = BrainRequest(
            messages=tuple(messages),
            system=self._system_prompt,
            max_tokens=self._max_tokens,
            stream=True,
        )
        if text_consumer is not None:
            return await aggregate_with_consumer(
                self._brain.complete(req), text_consumer,
            )
        from .streaming import aggregate
        return await aggregate(self._brain.complete(req))

    async def stream_text(
        self,
        user_text: str,
        *,
        history: list[BrainMessage] | None = None,
    ) -> AsyncIterator[str]:
        """Yields text chunks. Tool use is disabled in this mode."""
        messages: list[BrainMessage] = list(history or [])
        messages.append(BrainMessage(role="user", content=user_text))
        req = BrainRequest(
            messages=tuple(messages),
            system=self._system_prompt,
            max_tokens=self._max_tokens,
            stream=True,
        )
        async for chunk in tee_text(self._brain.complete(req)):
            yield chunk

    def tools_payload(self) -> list[dict[str, Any]]:
        """Return tool schemas in the format accepted by brain plugins."""
        return [
            {
                "name": t.name,
                "description": getattr(t, "description", ""),
                "input_schema": t.schema,
            }
            for t in self._tools.values()
        ]
