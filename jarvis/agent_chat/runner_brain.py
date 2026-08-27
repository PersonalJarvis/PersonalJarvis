"""The Jarvis surface's runner: Jarvis' own brain, driven by text.

This is what the front page's chat runs on for an API-key or local seat. A
typed turn is the same turn a spoken one is — ``BrainManager.generate`` with
its router, its tools, its memory, its wiki and its safety tiers — with the
keyboard in place of the microphone. Nothing here spawns a coding CLI or a
second agent loop; there is one assistant in this app and this is how you
type at it (maintainer, 2026-08-25).

What the composer's pick changes is which model Jarvis thinks with FOR THIS
CHAT, and how hard: provider, model and effort travel as a
:class:`jarvis.brain.turn_override.TurnOverride` on the one ``generate``
call, and nothing else. The live brain the voice runs on — its active
provider, its config, its dead-lists — is never touched; the first version
of this runner (2026-08-24) switched it and was reverted for exactly that.

The chat's folder is Jarvis' working folder for the turn: the folder tools
(:mod:`jarvis.agent_chat.folder_tools`) join Jarvis' own tools on the turn's
surface, every call goes through ``ToolExecutor``, and the chat's permission
stance decides what asks — through a clickable card, not a spoken "yes"
(:mod:`jarvis.agent_chat.approval_bridge`).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, get_args

from jarvis.agent_chat.approval_bridge import ChatApprovalBridge, ChatGrant, approval_ref
from jarvis.agent_chat.effort import normalize_effort
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.folder_tools import PLAN_STANCE, folder_tools, plan_filter
from jarvis.agent_chat.runner_api import messages_from_events
from jarvis.agent_chat.surface_kits import kit_for
from jarvis.agent_chat.tools import summarize_call
from jarvis.brain.turn_override import TurnOverride
from jarvis.core.protocols import BrainMessage, ReasoningEffort, Tool

if TYPE_CHECKING:
    from uuid import UUID

    from jarvis.agent_chat.runner_api import TurnHandle
    from jarvis.agent_chat.store import AgentChatSession

log = logging.getLogger(__name__)

#: The runner id this module answers to, in the catalog and in ``turn_started``.
RUNNER: Final[str] = "brain"

#: ``MessageSent.source_layer`` for a turn typed into the chat surface. The
#: brain's router reads it; it is a conversational layer, so the router may
#: still spawn a worker on an explicit request (``_NON_SPAWN_SOURCE_LAYERS``
#: does not list it — typing at Jarvis is not a background channel).
SOURCE_LAYER: Final[str] = "ui.chat.typed"

#: How long a card may stay open. A person reading a chat is not on a spoken
#: turn's clock; the executor clamps this to its own ceiling.
APPROVAL_TIMEOUT_S: Final[float] = 600.0

#: The tool-loop ceiling of a chat turn — the API runner's own round budget.
MAX_TURNS: Final[int] = 40

#: How much of the chat's own log rides along as history.
_HISTORY_MAX: Final[int] = 40

_EFFORTS: Final[frozenset[str]] = frozenset(get_args(ReasoningEffort))


def brain_manager() -> Any | None:
    """The live BrainManager, or ``None`` before the brain finished building."""
    from jarvis.core import runtime_refs

    return runtime_refs.get_brain_manager()


# ------------------------------------------------------------------ history


def brain_history_from_events(events: list[dict[str, Any]]) -> list[BrainMessage]:
    """The chat's own log as the turn's history: what was said, in prose.

    User and assistant TEXT only. A tool round is folded into the assistant
    message as one line per call ("[used tool X: …]") and its results are
    dropped: Jarvis' loop rebuilds its own tool rounds, and replaying a
    foreign provider's ``tool_use`` ids into a different provider fails its
    validation. Capped to the last ``_HISTORY_MAX`` messages.
    """
    out: list[BrainMessage] = []
    for message in messages_from_events(events):
        if message.role == "user":
            text = message.content if isinstance(message.content, str) else ""
            if text.strip():
                out.append(BrainMessage(role="user", content=text))
        elif message.role == "assistant":
            text = _assistant_prose(message.content)
            if not text.strip():
                continue
            if out and out[-1].role == "assistant":
                # One answer per turn: the tool round and the prose after it
                # are two provider messages but one thing Jarvis said.
                merged = f"{out[-1].content}\n{text}"
                out[-1] = BrainMessage(role="assistant", content=merged)
            else:
                out.append(BrainMessage(role="assistant", content=text))
    return out[-_HISTORY_MAX:]


def _assistant_prose(content: str | list[dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    lines: list[str] = []
    for block in content:
        kind = block.get("type")
        if kind == "text":
            text = str(block.get("text") or "")
            if text.strip():
                lines.append(text)
        elif kind == "tool_use":
            name = str(block.get("name") or "tool")
            args = block.get("input") or {}
            summary = summarize_call(name, args) if isinstance(args, dict) else ""
            lines.append(f"[used tool {name}: {summary}]" if summary else f"[used tool {name}]")
    return "\n".join(lines)


# ----------------------------------------------------------------- override


def build_override(
    session: AgentChatSession,
    brain: Any,
    *,
    stance: str,
    cwd: Path,
    ref: str,
    kit_tools: Mapping[str, Tool] | None = None,
    system_extra: str = "",
) -> TurnOverride:
    """The pick for this turn: the session's provider / model / effort, the
    surface's hands (its kit's tools, else the folder's), and the tool context
    that routes approvals to the card."""
    kit = kit_for(session.surface)
    provider = session.provider
    model = (session.model or "").strip() or _default_model(brain, provider)
    effort = normalize_effort(provider, session.effort)
    reasoning_effort: ReasoningEffort | None = (
        effort if effort in _EFFORTS else None  # type: ignore[assignment]
    )
    return TurnOverride(
        provider=provider,
        model=model,
        reasoning_effort=reasoning_effort,
        tools_extra=(
            dict(kit_tools) if kit_tools is not None else folder_tools(cwd, stance=stance)
        ),
        tool_filter=_compose_filters(
            kit.tool_filter, plan_filter if stance == PLAN_STANCE else None
        ),
        credential_scope=kit.credential_scope,
        system_extra=system_extra,
        tool_context={
            # A typed answer is read, not heard: no spoken two-liners.
            "delivery": "written",
            # The executor asks the card, never the voice sentinel.
            "approval_surface": "interactive",
            "approval_ref": ref,
            "approval_timeout_s": APPROVAL_TIMEOUT_S,
            "tool_origin": kit.tool_origin,
            "cwd": str(cwd),
        },
        max_turns=kit.max_turns or MAX_TURNS,
    )


def _compose_filters(
    *filters: Callable[[dict[str, Tool]], dict[str, Tool]] | None,
) -> Callable[[dict[str, Tool]], dict[str, Tool]] | None:
    """The kit's filter first, then the stance's; ``None`` when neither applies."""
    active = [f for f in filters if f is not None]
    if not active:
        return None

    def _apply(tools: dict[str, Tool]) -> dict[str, Tool]:
        for f in active:
            tools = f(tools)
        return tools

    return _apply


async def kit_payload(session: AgentChatSession, brain: Any) -> tuple[dict[str, Tool] | None, str]:
    """The surface kit's per-turn hands and briefing: ``(tools, system_extra)``.

    ``(None, "")`` on a surface without its own kit tools (the folder tools
    then apply). A kit builder that fails loses only its part — the turn
    still runs, and the failure is in the log.
    """
    kit = kit_for(session.surface)
    cfg = getattr(brain, "_config", None)
    tools: dict[str, Tool] | None = None
    extra = ""
    if kit.tools is not None:
        try:
            tools = kit.tools(cfg, brain)
        except Exception as exc:  # noqa: BLE001 — the turn runs without the kit's hands
            log.warning("surface %s: kit tools not built: %s", session.surface, exc, exc_info=True)
            tools = {}
    if kit.system_extra is not None:
        try:
            extra = await kit.system_extra(cfg, brain)
        except Exception as exc:  # noqa: BLE001 — the turn runs without the briefing
            log.warning(
                "surface %s: kit briefing not built: %s", session.surface, exc, exc_info=True
            )
    return tools, extra


def _default_model(brain: Any, provider: str) -> str | None:
    fast = getattr(brain, "_fast_model", None)
    if callable(fast):
        try:
            return fast(provider) or None
        except Exception:  # noqa: BLE001 — the provider's own default then
            return None
    return None


# ------------------------------------------------------------- step mirror


class _StepMirror:
    """Turns THIS turn's bus events into the timeline's step rows.

    The brain does not report its work to the chat; it publishes it on the
    app bus, where the voice lane and the run inspector read it too. This
    subscribes for the length of one turn and translates — for the events
    that carry this turn's trace only, so a voice turn running alongside
    never draws rows in the chat:

    * ``ActionProposed`` -> a tool row, plus its ``rationale`` as reasoning
      text. That sentence is the model's OWN words for why it reaches for the
      tool — never a second "explain yourself" call.
    * ``ActionExecuted`` / ``ActionDenied`` -> that row's result.
    * ``ToolCallStarted`` / ``ToolCallCompleted`` -> the same for the paths
      that publish those instead.

    Rows are paired per tool name, oldest open first. Read-only and
    defensive: a malformed event never reaches the brain (AP-18).
    """

    __slots__ = ("_bus", "_children", "_emit", "_open", "_seen_text", "_trace_id", "_turn_id")

    def __init__(self, emit: Any, turn_id: str, bus: Any | None, trace_id: UUID) -> None:
        self._emit = emit
        self._turn_id = turn_id
        self._bus = bus
        self._trace_id = trace_id
        self._open: list[tuple[str, str]] = []  # (tool name, call id)
        self._children: set[UUID] = set()
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

    def open_call_id(self, tool_name: str) -> str:
        """The open row for ``tool_name`` (the bridge's card hangs off it), else ""."""
        for name, call_id in self._open:
            if name == tool_name:
                return call_id
        return ""

    async def _on_event(self, event: Any) -> None:
        try:
            await self._translate(event)
        except Exception:  # noqa: BLE001 — AP-18: never leave a subscriber
            log.debug("brain runner: step mirror skipped an event", exc_info=True)

    def _mine(self, event: Any) -> bool:
        return getattr(event, "trace_id", None) == self._trace_id

    async def _open_call(self, tool_name: str, args: Any) -> None:
        call_id = uuid.uuid4().hex
        self._open.append((tool_name, call_id))
        if isinstance(args, dict):
            payload_in: Any = args
        elif args:
            payload_in = {"arguments": str(args)}
        else:
            payload_in = {}
        await self._emit(
            "tool_call",
            {"turn_id": self._turn_id, "call_id": call_id, "name": tool_name, "input": payload_in},
        )

    async def _close_call(self, tool_name: str, *, ok: bool, output: str, duration_ms: Any) -> None:
        index = next((i for i, (name, _) in enumerate(self._open) if name == tool_name), None)
        if index is None:
            # A result with no row of its own (another path's, or a report
            # without a start) would draw a headless row; drop it.
            return
        _, call_id = self._open.pop(index)
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
        if name == "ActionProposed" and self._mine(event):
            text = (getattr(event, "rationale", "") or "").strip()
            if text and text not in self._seen_text:
                self._seen_text.add(text)
                await self._emit("reasoning_delta", {"turn_id": self._turn_id, "text": text})
            await self._open_call(
                getattr(event, "tool_name", "") or "tool", getattr(event, "args", None)
            )
        elif name == "ActionExecuted" and self._mine(event):
            await self._close_call(
                getattr(event, "tool_name", "") or "tool",
                ok=bool(getattr(event, "success", False)),
                output=getattr(event, "error", None) or getattr(event, "output_preview", "") or "",
                duration_ms=getattr(event, "duration_ms", 0),
            )
        elif name == "ActionDenied" and self._mine(event):
            await self._close_call(
                getattr(event, "tool_name", "") or "tool",
                ok=False,
                output=str(getattr(event, "reason", "") or "denied"),
                duration_ms=0,
            )
        elif (
            name == "ToolCallStarted" and getattr(event, "parent_trace_id", None) == self._trace_id
        ):
            self._children.add(event.trace_id)
            await self._open_call(
                getattr(event, "tool_name", "") or "tool", getattr(event, "args_preview", "") or ""
            )
        elif name == "ToolCallCompleted" and getattr(event, "trace_id", None) in self._children:
            self._children.discard(event.trace_id)
            tool_name = getattr(event, "tool_name", "") or (
                self._open[0][0] if self._open else "tool"
            )
            await self._close_call(
                tool_name,
                ok=bool(getattr(event, "success", False)),
                output=getattr(event, "error", None) or getattr(event, "output_preview", "") or "",
                duration_ms=getattr(event, "duration_ms", 0),
            )


# ------------------------------------------------------------------- turn


async def run_brain_turn(
    handle: TurnHandle,
    text: str,
    *,
    bridge: ChatApprovalBridge | None,
    always_allowed: set[str],
) -> None:
    """Run one typed turn on Jarvis' brain, streaming it into the timeline."""
    started = time.monotonic()
    turn_id = handle.turn_id
    message_id = uuid.uuid4().hex
    session = handle.session

    async def emit(kind: str, payload: dict[str, Any]) -> None:
        await handle.emit(make_event(kind, payload))

    mirror = _StepMirror(emit, turn_id, handle.bus, handle.trace_id)
    ref = approval_ref(session.session_id)

    async def finish(status: str, usage: dict[str, Any], error: str | None = None) -> None:
        await emit(
            "turn_finished",
            {
                "turn_id": turn_id,
                "status": status,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "usage": usage,
                "error": error,
            },
        )

    brain = brain_manager()
    if brain is None or not callable(getattr(brain, "generate", None)):
        await finish(
            "error", {}, "Jarvis' brain is still starting up. Give it a moment and send again."
        )
        return

    cwd = Path(session.cwd or Path.home())
    stance = handle.stance or "ask"
    kit_tools, system_extra = await kit_payload(session, brain)
    override = build_override(
        session,
        brain,
        stance=stance,
        cwd=cwd,
        ref=ref,
        kit_tools=kit_tools,
        system_extra=system_extra,
    )
    _note_skill_trigger(brain, text)

    if bridge is not None:
        bridge.arm(
            ref,
            ChatGrant(
                session_id=session.session_id,
                turn_id=turn_id,
                stance=stance,
                always_allowed=always_allowed,
                ask=handle.request_approval,
                call_id_for=mirror.open_call_id,
            ),
        )

    # The "thinking" line the timeline shows until the first words arrive,
    # and the watcher that fills it: every tool Jarvis reaches for and every
    # sentence it writes next to one lands in the timeline as it happens.
    await emit("reasoning_started", {"turn_id": turn_id, "message_id": message_id})
    mirror.start()

    loop = asyncio.get_running_loop()
    seen: list[str] = []

    def feed(chunk: str) -> None:
        """Called by the brain as the answer is produced — a CHUNK, not the whole so far."""
        if not chunk:
            return
        seen.append(chunk)
        # The consumer is synchronous (the brain's contract); hand the emit
        # back to the loop rather than blocking the producer.
        asyncio.run_coroutine_threadsafe(
            emit("text_delta", {"turn_id": turn_id, "message_id": message_id, "text": chunk}),
            loop,
        )

    status = "done"
    error: str | None = None
    reply = ""
    try:
        reply = await _generate(brain, text, handle, override, feed)
    except asyncio.CancelledError:
        status = "cancelled"
    except Exception as exc:  # noqa: BLE001 — the timeline must never spin forever
        log.exception("brain chat turn %s failed", turn_id)
        status, error = "error", f"{type(exc).__name__}: {exc}"
    finally:
        if bridge is not None:
            bridge.disarm(ref)
        mirror.stop()

    if status == "done" and handle.cancel.is_set():
        status = "cancelled"
    if status == "done":
        answer = (reply or "").strip() or "".join(seen).strip()
        if answer:
            await emit(
                "assistant_text", {"turn_id": turn_id, "message_id": message_id, "text": answer}
            )
    await finish(status, override.receipt.usage(), error)


async def _generate(
    brain: Any,
    text: str,
    handle: TurnHandle,
    override: TurnOverride,
    feed: Callable[[str], None],
) -> str:
    """The one ``generate`` call, under the chat's credentials, cancellable.

    ``allow_voice_confirm=False`` is load-bearing: the executor must never
    park a chat's consequential action in the manager-wide voice slot that
    the NEXT spoken turn would consume. The chat's stance and card decide
    (``approval_surface`` in the tool context).
    """
    from jarvis.core.config import get_jarvis_agent_secret, override_provider_secrets

    session = handle.session
    history = brain_history_from_events(handle.history)
    kwargs: dict[str, Any] = {
        "use_history": False,
        "history_override": history,
        "conversation_id": session.session_id,
        "source_layer": SOURCE_LAYER,
        "trace_id": handle.trace_id,
        "allow_voice_confirm": False,
        "emit_tool_ack": False,
        "publish_response": False,
        "text_consumer": feed,
        "turn_override": override,
    }
    secret = _agent_secret(get_jarvis_agent_secret, session.provider)
    overrides = {session.provider: secret} if secret else {}
    # The task inherits the credential override through its context copy, so
    # the scoped brain instance resolves the Agents-tab key on first use.
    with override_provider_secrets(overrides):
        task: asyncio.Task[str] = asyncio.create_task(
            brain.generate(text, **kwargs), name=f"agent-chat-brain-{handle.turn_id[:8]}"
        )
    waiter = asyncio.create_task(handle.cancel.wait())
    try:
        done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return await task
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001 — the turn is over
            log.debug("brain runner: cancelled turn ended with %r", exc)
        raise asyncio.CancelledError
    finally:
        waiter.cancel()


def _agent_secret(resolver: Callable[[str], str | None], provider: str) -> str | None:
    try:
        return resolver(provider)
    except Exception:  # noqa: BLE001 — no dedicated key: the brain resolves as usual
        log.debug("brain runner: no agent-tier secret for %s", provider, exc_info=True)
        return None


#: A leading ``/slug`` — the composer's slash pick — and whatever follows it.
_EXPLICIT_SKILL_RE = re.compile(r"^\s*/([A-Za-z0-9][\w.:-]*)(?:\s+|$)(.*)$", re.DOTALL)


def explicit_skill(text: str) -> tuple[str, str] | None:
    """``("slug", "the rest")`` when the message opens with ``/slug``, else None.

    The composer's ``/`` typeahead lists the registry's active skills by
    slug; picking one puts ``/slug`` at the head of the sentence. That pick
    is an order, not a hint, so it is honoured before any trigger-phrase
    matching — the same precedence a CLI gives its slash commands.
    """
    m = _EXPLICIT_SKILL_RE.match(text or "")
    if m is None:
        return None
    return m.group(1), m.group(2).strip()


def _note_skill_trigger(brain: Any, text: str) -> None:
    """The desktop text path's pre-brain hook, verbatim in effect: a skill
    whose trigger matches is noted on the brain, which injects its
    instructions into this turn (desktop_app._on_user_message). A leading
    ``/slug`` names the skill outright and wins over the trigger matcher."""
    try:
        from jarvis.skills.skill_context import try_get_skill_context
        from jarvis.skills.trigger_matcher import TriggerMatcher

        skill_ctx = try_get_skill_context()
        note = getattr(brain, "note_skill_trigger", None)
        if skill_ctx is None or not callable(note):
            return
        explicit = explicit_skill(text)
        if explicit is not None:
            slug, rest = explicit
            try:
                skill = skill_ctx.registry.resolve(slug)
            except KeyError:  # not a skill's name: the trigger matcher gets its turn
                log.debug("brain runner: /%s names no active skill", slug)
            else:
                note(skill.name, content=rest, source="chat")
                return
        match_result = TriggerMatcher(skill_ctx.registry).match_voice_with_match(text, lang="auto")
        if match_result is None:
            return
        matched, regex_match = match_result
        content = ""
        for group in reversed(regex_match.groups()):
            if group and group.strip():
                content = group.strip()
                break
        note(matched.name, content=content, source="chat")
    except Exception:  # noqa: BLE001 — the hook is defensive; a crash must never cost the turn
        log.debug("brain runner: skill pre-hook skipped", exc_info=True)


__all__ = [
    "APPROVAL_TIMEOUT_S",
    "MAX_TURNS",
    "RUNNER",
    "SOURCE_LAYER",
    "brain_history_from_events",
    "brain_manager",
    "build_override",
    "kit_payload",
    "run_brain_turn",
]
