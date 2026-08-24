"""The chat surface's runner: Jarvis' own brain, driven by text.

This is what the front page's chat runs on. A typed turn is the same turn a
spoken one is — ``BrainManager.generate`` with its router, its tools, its
memory, its wiki and its two-turn confirmation for a consequential action —
with the keyboard in place of the microphone. Nothing here spawns a coding
CLI or a second agent loop; there is one assistant in this app and this is
how you type at it (maintainer, 2026-08-24).

What the picker changes is which MODEL Jarvis thinks with. Picking a provider
switches the live brain (``BrainManager.switch``, not persisted, so a restart
returns to the configured one), and picking a model writes that provider's
model the same way the API-Keys page does. Both are the app's ONE brain
setting: what you pick here is what answers by voice as well, which is the
honest behaviour — there is no second, hidden Jarvis.

A coding CLI (Claude Code, Codex, Antigravity, Grok Build) cannot be a brain:
it is an agent loop with its own tools, reachable only as a sub-agent
(``SUBAGENT_ONLY_BRAIN_PROVIDERS``). Those rows are therefore not in this
picker — the Agentic IDE is where a coding session belongs.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from jarvis.agent_chat.events import make_event

if TYPE_CHECKING:
    from jarvis.agent_chat.runner_api import TurnHandle

log = logging.getLogger(__name__)

#: The runner id this module answers to, in the catalog and in ``turn_started``.
RUNNER: str = "brain"

#: ``MessageSent.source_layer`` for a turn typed into the chat surface. The
#: brain's router reads it, and the desktop bridge uses it to know this turn
#: already has an owner — see ``_on_user_message``'s "chat" guard.
SOURCE_LAYER: str = "ui.chat.typed"


def brain_manager() -> Any | None:
    """The live BrainManager, or ``None`` before the brain finished building."""
    from jarvis.core import runtime_refs

    return runtime_refs.get_brain_manager()


async def apply_pick(brain: Any, provider: str, model: str) -> str:
    """Make the session's pick the live brain. Returns a human error, or "".

    Best-effort by design: a pick that cannot be applied must not lose the
    person's message. The turn then runs on whatever brain IS active, and the
    reason is reported as the turn's error line rather than swallowed.
    """
    provider = (provider or "").strip()
    if not provider:
        return ""
    try:
        from jarvis.brain.manager import SUBAGENT_ONLY_BRAIN_PROVIDERS

        if provider in SUBAGENT_ONLY_BRAIN_PROVIDERS:
            return (
                f"{provider} is a coding sub-agent, not a brain — it cannot answer "
                "in this chat. Pick a provider with an API key."
            )
    except Exception:  # noqa: BLE001 — an import failure must not block the turn
        log.debug("brain runner: could not read the subagent-only set", exc_info=True)

    if (model or "").strip():
        # The same live-apply the API-Keys page calls (PUT /providers/{id}/model
        # with persist=false): this turn's model, not a config write. A restart
        # returns to the configured one, which is what a per-chat pick should do.
        apply_model = getattr(brain, "apply_provider_model", None)
        if callable(apply_model):
            try:
                apply_model(provider, model)
            except Exception as exc:  # noqa: BLE001
                log.debug("brain runner: model %r not applied: %s", model, exc)

    try:
        active = getattr(brain, "active_provider_name", None)
        current = active() if callable(active) else getattr(brain, "_active_name", "")
        if current != provider:
            await brain.switch(provider, persist=False)
    except Exception as exc:  # noqa: BLE001
        return f"Could not switch to {provider}: {type(exc).__name__}: {exc}"
    return ""


class _StepMirror:
    """Turns the brain's bus events into the timeline's own step rows.

    The brain does not report its work to the chat; it publishes it on the app
    bus, which is where the voice lane and the classic chat column read it from
    too (``jarvis/state/turn_trace.py`` keeps the same set for the archive).
    This subscribes for the length of one turn and translates:

    * ``ActionProposed`` -> a tool row, plus its ``rationale`` as the reasoning
      text. That sentence is the model's OWN words for why it is about to
      reach for the tool, and it is the honest source for a trace (maintainer,
      2026-08-23 — never a second, slower "explain yourself" call).
    * ``ActionExecuted`` -> that row's result and how long it took. This is
      Jarvis' real tool path (``jarvis/safety/tool_executor.py``);
      ``ToolCallStarted`` / ``ToolCallCompleted`` are handled too because other
      paths publish those instead.
    * ``BrainTurnCompleted`` -> the receipt (tokens and cost) on the footer.

    Rows are paired in order: a turn runs one tool at a time, so the oldest
    open call is the one that just closed.

    Read-only and defensive: a malformed event, or an emit that fails, must
    never reach the brain (AP-18) — a missing row is a cosmetic loss, a raised
    exception would cost the answer.
    """

    __slots__ = ("_emit", "_turn_id", "_bus", "_open", "usage", "_seen_text")

    def __init__(self, emit: Any, turn_id: str, bus: Any | None) -> None:
        self._emit = emit
        self._turn_id = turn_id
        self._bus = bus
        self._open: list[str] = []
        self.usage: dict[str, Any] = {}
        self._seen_text: set[str] = set()

    def start(self) -> None:
        if self._bus is not None and hasattr(self._bus, "subscribe_all"):
            self._bus.subscribe_all(self._on_event)

    def stop(self) -> None:
        if self._bus is not None and hasattr(self._bus, "unsubscribe_all"):
            try:
                self._bus.unsubscribe_all(self._on_event)
            except Exception:  # noqa: BLE001 — detaching must never raise
                log.debug("brain runner: could not detach the step mirror", exc_info=True)

    async def _on_event(self, event: Any) -> None:
        try:
            await self._translate(event)
        except Exception:  # noqa: BLE001 — AP-18: never leave a subscriber
            log.debug("brain runner: step mirror skipped an event", exc_info=True)

    async def _open_call(self, tool_name: str, args: Any) -> None:
        call_id = uuid.uuid4().hex
        self._open.append(call_id)
        if isinstance(args, dict):
            payload_in: Any = args
        elif args:
            payload_in = {"arguments": str(args)}
        else:
            payload_in = {}
        await self._emit(
            "tool_call",
            {
                "turn_id": self._turn_id,
                "call_id": call_id,
                "name": tool_name,
                "input": payload_in,
            },
        )

    async def _close_call(self, *, ok: bool, output: str, duration_ms: Any) -> None:
        if not self._open:
            # A result with no row of its own (another turn's, or a path that
            # only reports the end) would draw a headless row; drop it.
            return
        call_id = self._open.pop(0)
        await self._emit(
            "tool_result",
            {
                "turn_id": self._turn_id,
                "call_id": call_id,
                "output": str(output)[:2000],
                "is_error": not ok,
                "duration_ms": int(duration_ms or 0),
            },
        )

    async def _translate(self, event: Any) -> None:
        name = type(event).__name__
        if name == "ActionProposed":
            text = (getattr(event, "rationale", "") or "").strip()
            # The same rationale is published per proposal; show each once.
            if text and text not in self._seen_text:
                self._seen_text.add(text)
                await self._emit("reasoning_delta", {"turn_id": self._turn_id, "text": text})
            await self._open_call(
                getattr(event, "tool_name", "") or "tool",
                getattr(event, "args", None),
            )
            return
        if name == "ActionExecuted":
            await self._close_call(
                ok=bool(getattr(event, "success", False)),
                output=(
                    getattr(event, "error", None) or getattr(event, "output_preview", "") or ""
                ),
                duration_ms=getattr(event, "duration_ms", 0),
            )
            return
        if name == "ActionDenied":
            await self._close_call(
                ok=False,
                output=str(getattr(event, "reason", "") or "denied"),
                duration_ms=0,
            )
            return
        if name == "ToolCallStarted":
            await self._open_call(
                getattr(event, "tool_name", "") or "tool",
                getattr(event, "args_preview", "") or "",
            )
            return
        if name == "ToolCallCompleted":
            await self._close_call(
                ok=bool(getattr(event, "success", False)),
                output=(
                    getattr(event, "error", None) or getattr(event, "output_preview", "") or ""
                ),
                duration_ms=getattr(event, "duration_ms", 0),
            )
            return
        if name == "BrainTurnCompleted":
            for key, attr in (
                ("input_tokens", "tokens_in"),
                ("output_tokens", "tokens_out"),
                ("cost_usd", "cost_usd"),
            ):
                value = getattr(event, attr, None)
                if isinstance(value, int | float) and value:
                    self.usage[key] = value


async def run_brain_turn(handle: TurnHandle, text: str) -> None:
    """Run one typed turn on Jarvis' brain, streaming it into the timeline."""
    started = time.monotonic()
    turn_id = handle.turn_id
    message_id = uuid.uuid4().hex

    async def emit(kind: str, payload: dict[str, Any]) -> None:
        await handle.emit(make_event(kind, payload))

    mirror = _StepMirror(emit, turn_id, getattr(handle, "bus", None))

    async def finish(status: str, error: str | None = None) -> None:
        mirror.stop()
        await emit(
            "turn_finished",
            {
                "turn_id": turn_id,
                "status": status,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "usage": dict(mirror.usage),
                "error": error,
            },
        )

    brain = brain_manager()
    if brain is None or not hasattr(brain, "generate"):
        await finish(
            "error",
            "Jarvis' brain is still starting up. Give it a moment and send again.",
        )
        return

    pick_error = await apply_pick(brain, handle.session.provider, handle.session.model)

    # The "thinking" line the timeline shows until the first words arrive, and
    # the watcher that fills it: from here on, every tool Jarvis reaches for and
    # every sentence it writes next to one lands in the timeline as it happens.
    await emit("reasoning_started", {"turn_id": turn_id, "message_id": message_id})
    mirror.start()

    loop = asyncio.get_running_loop()
    seen = ""

    def feed(chunk: str) -> None:
        """Called from the brain's own thread/task as the answer is produced."""
        nonlocal seen
        if not chunk:
            return
        seen += chunk
        # The consumer is synchronous (the brain's contract), so hand the emit
        # back to this loop rather than blocking the producer.
        asyncio.run_coroutine_threadsafe(
            emit("text_delta", {"turn_id": turn_id, "message_id": message_id, "text": seen}),
            loop,
        )

    try:
        reply = await _generate(brain, text, handle, feed)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — the timeline must never spin forever
        log.exception("brain chat turn %s failed", turn_id)
        await finish("error", f"{type(exc).__name__}: {exc}")
        return

    if handle.cancel.is_set():
        await finish("cancelled")
        return

    answer = (reply or "").strip() or seen.strip()
    if answer:
        await emit(
            "assistant_text",
            {"turn_id": turn_id, "message_id": message_id, "text": answer},
        )
    await finish("done", pick_error or None)


async def _generate(brain: Any, text: str, handle: TurnHandle, feed: Any) -> str:
    """Call the brain, degrading through the argument shapes older brains take.

    ``allow_voice_confirm=True`` is load-bearing (audit GT-12): a person typing
    IS present, so a consequential ask-tier tool asks back in the reply and the
    next message answers it — the same two-turn flow voice has. Without it the
    executor sees no channel to ask on and refuses outright.
    """
    kwargs: dict[str, Any] = {
        "conversation_id": handle.session.session_id,
        "source_layer": SOURCE_LAYER,
        "allow_voice_confirm": True,
        "text_consumer": feed,
    }
    try:
        return await brain.generate(text, **kwargs)
    except TypeError:
        # An older brain shape without the streaming hook or the confirm flag.
        kwargs.pop("text_consumer", None)
        kwargs.pop("allow_voice_confirm", None)
        return await brain.generate(text, **kwargs)
