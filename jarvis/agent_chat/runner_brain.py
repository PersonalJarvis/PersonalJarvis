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


async def run_brain_turn(handle: TurnHandle, text: str) -> None:
    """Run one typed turn on Jarvis' brain, streaming it into the timeline."""
    started = time.monotonic()
    turn_id = handle.turn_id
    message_id = uuid.uuid4().hex

    async def emit(kind: str, payload: dict[str, Any]) -> None:
        await handle.emit(make_event(kind, payload))

    async def finish(status: str, error: str | None = None) -> None:
        await emit(
            "turn_finished",
            {
                "turn_id": turn_id,
                "status": status,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "usage": {},
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

    # The "thinking" line the timeline shows until the first words arrive. The
    # brain streams its answer but not its reasoning, so the time IS the fact —
    # the same shape a vendor that redacts its thinking gets.
    await emit("reasoning_started", {"turn_id": turn_id, "message_id": message_id})

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
