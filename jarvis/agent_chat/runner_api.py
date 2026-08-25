"""The in-process agent loop — one turn on a provider's chat API.

This is "our own Claude Code": the provider's own streaming chat API
(``jarvis.plugins.brain.*``) driven in a tool-use loop over
:mod:`jarvis.agent_chat.tools`. Per round the model streams text (forwarded
live as ``text_delta``), may request tool calls, each call is gated by the
session's permission mode, executed, and its result fed back; the loop ends
when a round produces no tool calls, the round budget is spent, or the
person stops it.

Provider-agnostic by construction (AP-21): the provider id selects the brain
class through a capability-style map, the reasoning effort goes through the
``BrainRequest.reasoning_effort`` hint every plugin already reads, and the
agent-tier credential is applied through ``override_provider_secrets`` so a
dedicated Agents-tab key wins over the shared brain key without touching the
process environment.

Conversation memory is the session's persisted event log: the turn
reconstructs the provider messages from it (user text, assistant text + tool
calls, tool results), so a reopened session continues with its whole
history and nothing is kept in process between turns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from jarvis.agent_chat.effort import normalize_effort
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.permissions import normalize_permission
from jarvis.agent_chat.store import AgentChatSession
from jarvis.agent_chat.tools import (
    EDIT_TOOLS,
    READ_ONLY_TOOLS,
    TOOL_SPECS,
    execute_tool,
    shell_label,
    summarize_call,
)
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest

log = logging.getLogger(__name__)

# Provider slug -> (module, class). Lazy-imported so a turn on one provider
# never imports another's SDK. Mirrors the agent worker's map; the chat has
# no reason to diverge from the set the Agents tab offers.
BRAIN_BY_PROVIDER: Final[dict[str, tuple[str, str]]] = {
    "openai": ("jarvis.plugins.brain.openai", "OpenAIBrain"),
    "openrouter": ("jarvis.plugins.brain.openrouter", "OpenRouterBrain"),
    "grok": ("jarvis.plugins.brain.grok", "GrokBrain"),
    "nvidia": ("jarvis.plugins.brain.nvidia", "NvidiaBrain"),
    "claude-api": ("jarvis.plugins.brain.claude_api", "ClaudeAPIBrain"),
    "gemini": ("jarvis.plugins.brain.gemini", "GeminiBrain"),
    "vertex": ("jarvis.plugins.brain.vertex", "VertexBrain"),
    "ollama": ("jarvis.plugins.brain.ollama", "OllamaBrain"),
    "local-openai": ("jarvis.plugins.brain.local_openai", "LocalOpenAIBrain"),
}

MAX_ROUNDS: Final[int] = 40
MAX_TOKENS: Final[int] = 8192
# Tool output fed back to the model is capped here (the UI gets the full
# output from the tool_result event, which carries the same text).
_TOOL_RESULT_CAP: Final[int] = 24_000
# How many past events are replayed into the provider messages. A long
# session still works; this bounds the request size.
_HISTORY_MAX_EVENTS: Final[int] = 600

_ROUND_BUDGET_DIRECTIVE: Final[str] = (
    "[round budget exhausted] Summarize what you did and what is still open. "
    "Do not call any more tools in this reply."
)


def supports_api_runner(provider: str) -> bool:
    return (provider or "").strip().lower() in BRAIN_BY_PROVIDER


def build_brain(provider: str, model: str) -> Any:
    mod_name, cls_name = BRAIN_BY_PROVIDER[provider]
    mod = __import__(mod_name, fromlist=[cls_name])
    return getattr(mod, cls_name)(model=model or None)


def system_prompt(*, cwd: Path, assistant_name: str, plan: bool = False) -> str:
    """The Claude-Code-shaped operating instructions for the API runner.

    ``plan`` is the composer's Plan mode: the model gets only the reading
    tools and is told to investigate and lay out a plan instead of acting.
    """
    plan_note = (
        "\n\nPLAN MODE is on: you may read, search and list, but you have no tools that "
        "change anything, and you must not try to. Investigate, then answer with a "
        "concrete, numbered plan of the changes you would make (files, functions, the "
        "order), plus the open questions. The person switches to Build mode to have it "
        "carried out."
        if plan
        else ""
    )
    return (
        f"You are {assistant_name}, an expert software engineer and general assistant "
        "working as an interactive coding agent in the person's own environment — the "
        "same job Claude Code does in a terminal, here inside a chat window.\n\n"
        f"Working directory: {cwd}\n"
        f"Operating system: {platform.system()} {platform.release()}\n"
        f"Shell for RunCommand: {shell_label()}\n"
        f"Date: {time.strftime('%Y-%m-%d')}\n\n"
        "How to work:\n"
        "- Use the tools to look before you answer: read the files you talk about, "
        "run the tests you claim pass, check git state before describing it.\n"
        "- Prefer Edit over Write for existing files; keep changes minimal and in the "
        "style of the surrounding code.\n"
        "- A tool result marked denied means the person declined that action. Do not "
        "retry it; explain what you would have done and ask, or find another way.\n"
        "- Be concise. Lead with the outcome. Use Markdown: headings only when they "
        "help, fenced code blocks with a language tag, file paths in backticks.\n"
        "- Never invent file contents, command output or results you did not observe.\n"
        "- When the task is done, stop calling tools and give a short summary of what "
        "changed and anything the person should do next." + plan_note
    )


@dataclass(slots=True)
class TurnHandle:
    """What the service hands a runner: where to emit, how to ask, when to stop.

    ``trace_id`` is the turn's correlation id on the app bus (the brain runner
    passes it to ``generate`` and reads its own tool events back by it);
    ``bus`` is the app bus, ``None`` when the service runs without one;
    ``surface`` and ``stance`` are the session's chat and, on the Jarvis
    surface, its permission stance (``jarvis.agent_chat.permissions``).
    """

    session: AgentChatSession
    turn_id: str
    emit: Callable[[dict[str, Any]], Awaitable[None]]
    request_approval: Callable[[str, str, dict[str, Any], str], Awaitable[str]]
    cancel: asyncio.Event
    history: list[dict[str, Any]] = field(default_factory=list)
    assistant_name: str = "Jarvis"
    trace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    bus: Any | None = None
    surface: str = "agent"
    stance: str = ""


# ------------------------------------------------------------ history


def messages_from_events(events: list[dict[str, Any]]) -> list[BrainMessage]:
    """Rebuild the provider conversation from the persisted event log.

    ``user_message`` -> user; inside a turn, ``assistant_text`` and
    ``tool_call`` fold into ONE assistant message of content blocks (a text
    block, then the tool_use blocks in order) per round; ``tool_result``
    becomes a tool message keyed by ``call_id``. Rounds are split where a
    tool_result is followed by more assistant output.
    """
    out: list[BrainMessage] = []
    pending_blocks: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal pending_blocks
        if not pending_blocks:
            return
        only_text = all(b.get("type") == "text" for b in pending_blocks)
        if only_text:
            text = "".join(str(b.get("text") or "") for b in pending_blocks)
            if text.strip():
                out.append(BrainMessage(role="assistant", content=text))
        else:
            out.append(BrainMessage(role="assistant", content=list(pending_blocks)))
        pending_blocks = []

    for ev in events[-_HISTORY_MAX_EVENTS:]:
        kind = ev.get("kind")
        payload = ev.get("payload") or {}
        if kind == "user_message":
            flush()
            text = str(payload.get("text") or "")
            if text:
                out.append(BrainMessage(role="user", content=text))
        elif kind == "assistant_text":
            text = str(payload.get("text") or "")
            if text:
                pending_blocks.append({"type": "text", "text": text})
        elif kind == "tool_call":
            block: dict[str, Any] = {
                "type": "tool_use",
                "id": str(payload.get("call_id") or f"call_{uuid.uuid4().hex[:8]}"),
                "name": str(payload.get("name") or ""),
                "input": payload.get("input") or {},
            }
            meta = payload.get("meta") or {}
            if meta.get("thought_signature"):
                block["thought_signature"] = meta["thought_signature"]
            pending_blocks.append(block)
        elif kind == "tool_result":
            # Plain string content on purpose: the Anthropic adapter wraps a
            # string into its tool_result block, Gemini and the OpenAI family
            # pass it through — an envelope list would reach the OpenAI family
            # as a JSON-serialised wrapper.
            flush()
            call_id = str(payload.get("call_id") or "")
            output = str(payload.get("output") or "")
            if payload.get("is_error"):
                output = f"[error] {output}"
            out.append(
                BrainMessage(
                    role="tool",
                    content=_cap(output, _TOOL_RESULT_CAP),
                    tool_call_id=call_id,
                    name=str(payload.get("name") or ""),
                )
            )
        elif kind == "turn_finished":
            flush()
    flush()
    # A provider rejects a conversation that ends on a tool message or an
    # assistant message with unanswered tool calls (a cancelled turn); the
    # service appends the new user message after this, so close any open
    # assistant tool round with a synthetic "cancelled" result first.
    return _close_dangling_tool_calls(out)


def _close_dangling_tool_calls(messages: list[BrainMessage]) -> list[BrainMessage]:
    answered: set[str] = set()
    for m in messages:
        if m.role == "tool" and m.tool_call_id:
            answered.add(m.tool_call_id)
    out: list[BrainMessage] = []
    for m in messages:
        out.append(m)
        if m.role == "assistant" and isinstance(m.content, list):
            for block in m.content:
                if block.get("type") == "tool_use" and block.get("id") not in answered:
                    out.append(
                        BrainMessage(
                            role="tool",
                            content="[cancelled before it ran]",
                            tool_call_id=str(block["id"]),
                            name=str(block.get("name") or ""),
                        )
                    )
    return out


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated, {len(text) - limit} more characters]"


# ---------------------------------------------------------------- turn


async def run_api_turn(handle: TurnHandle, user_text: str) -> None:
    """Run one turn. Emits events through ``handle.emit``; never raises to the
    caller except ``asyncio.CancelledError``."""
    session = handle.session
    provider = session.provider
    cwd = Path(session.cwd or Path.home())
    effort = normalize_effort(provider, session.effort)
    t0 = time.perf_counter()
    usage_total: dict[str, int] = {}

    try:
        brain = build_brain(provider, session.model)
    except Exception as exc:  # noqa: BLE001 — reported to the person, not raised
        await handle.emit(
            make_event(
                "turn_finished",
                {
                    "turn_id": handle.turn_id,
                    "status": "error",
                    "duration_ms": 0,
                    "usage": {},
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        )
        return

    messages = messages_from_events(handle.history)
    messages.append(BrainMessage(role="user", content=user_text))
    permission_mode = normalize_permission("api", session.permission_mode)
    system = system_prompt(
        cwd=cwd, assistant_name=handle.assistant_name, plan=permission_mode == "plan"
    )
    # Plan mode hands the model only the reading tools: it cannot change a
    # thing, and it is told so in the system prompt.
    tools: tuple[dict[str, Any], ...] = (
        tuple(spec for spec in TOOL_SPECS if spec["name"] in READ_ONLY_TOOLS)
        if permission_mode == "plan"
        else TOOL_SPECS
    )
    status = "done"
    error_text: str | None = None

    from jarvis.core.config import get_jarvis_agent_secret, override_provider_secrets

    secret = get_jarvis_agent_secret(provider)
    overrides = {provider: secret} if secret else {}

    try:
        with override_provider_secrets(overrides):
            for round_no in range(MAX_ROUNDS):
                if handle.cancel.is_set():
                    status = "cancelled"
                    break
                req = BrainRequest(
                    messages=tuple(messages),
                    tools=tools,
                    system=system,
                    temperature=0.2,
                    max_tokens=MAX_TOKENS,
                    stream=True,
                    reasoning_effort=effort or None,  # type: ignore[arg-type]
                )
                message_id = uuid.uuid4().hex
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                try:
                    async for delta in _stream(brain, req, handle.cancel):
                        if delta.content:
                            text_parts.append(delta.content)
                            await handle.emit(
                                make_event(
                                    "text_delta",
                                    {
                                        "turn_id": handle.turn_id,
                                        "message_id": message_id,
                                        "text": delta.content,
                                    },
                                )
                            )
                        if delta.tool_call:
                            tool_calls.append(dict(delta.tool_call))
                        if delta.usage:
                            for k, v in delta.usage.items():
                                usage_total[k] = usage_total.get(k, 0) + int(v or 0)
                            # Tokens as they are counted, so the live line can
                            # say what the turn has spent so far. Providers
                            # report usage a handful of times per turn, not per
                            # token, so this needs no throttling of its own.
                            await handle.emit(
                                make_event(
                                    "usage_delta",
                                    {"turn_id": handle.turn_id, "usage": dict(usage_total)},
                                )
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — provider error ends the turn honestly
                    status = "error"
                    error_text = f"{type(exc).__name__}: {exc}"
                    log.warning("agent chat turn %s: provider error: %s", handle.turn_id, exc)
                    break

                text = "".join(text_parts)
                if handle.cancel.is_set():
                    if text.strip():
                        await handle.emit(
                            make_event(
                                "assistant_text",
                                {"turn_id": handle.turn_id, "message_id": message_id, "text": text},
                            )
                        )
                    status = "cancelled"
                    break

                if text.strip():
                    await handle.emit(
                        make_event(
                            "assistant_text",
                            {"turn_id": handle.turn_id, "message_id": message_id, "text": text},
                        )
                    )

                if not tool_calls:
                    break

                # Record the assistant round (text + tool_use blocks) for the
                # next request.
                blocks: list[dict[str, Any]] = []
                if text:
                    blocks.append({"type": "text", "text": text})
                for tc in tool_calls:
                    tc.setdefault("id", f"call_{uuid.uuid4().hex[:8]}")
                    block = {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc.get("name", ""),
                        "input": tc.get("input", {}) or {},
                    }
                    if tc.get("thought_signature"):
                        block["thought_signature"] = tc["thought_signature"]
                    blocks.append(block)
                messages.append(BrainMessage(role="assistant", content=blocks))

                for tc in tool_calls:
                    if handle.cancel.is_set():
                        status = "cancelled"
                        break
                    name = str(tc.get("name") or "")
                    args = tc.get("input") or {}
                    if not isinstance(args, dict):
                        try:
                            args = json.loads(str(args))
                        except (TypeError, ValueError):
                            args = {}
                    call_id = str(tc["id"])
                    meta = (
                        {"thought_signature": tc["thought_signature"]}
                        if tc.get("thought_signature")
                        else {}
                    )
                    await handle.emit(
                        make_event(
                            "tool_call",
                            {
                                "turn_id": handle.turn_id,
                                "call_id": call_id,
                                "name": name,
                                "input": args,
                                "summary": summarize_call(name, args),
                                "meta": meta,
                            },
                        )
                    )
                    t_tool = time.perf_counter()
                    output, is_error = await _run_gated_tool(
                        handle, permission_mode, call_id, name, args, cwd
                    )
                    if output == _PERMISSION_UPGRADED:
                        permission_mode = "auto"
                        output, is_error = await execute_tool(name, args, cwd=cwd)
                    await handle.emit(
                        make_event(
                            "tool_result",
                            {
                                "turn_id": handle.turn_id,
                                "call_id": call_id,
                                "name": name,
                                "output": output,
                                "is_error": is_error,
                                "duration_ms": int((time.perf_counter() - t_tool) * 1000),
                            },
                        )
                    )
                    fed = f"[error] {output}" if is_error else output
                    messages.append(
                        BrainMessage(
                            role="tool",
                            content=_cap(fed, _TOOL_RESULT_CAP),
                            tool_call_id=call_id,
                            name=name,
                        )
                    )
                if status == "cancelled":
                    break
                if round_no == MAX_ROUNDS - 2:
                    # Last round: no tools, summarize.
                    tools = ()
                    messages.append(BrainMessage(role="user", content=_ROUND_BUDGET_DIRECTIVE))
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    finally:
        await handle.emit(
            make_event(
                "turn_finished",
                {
                    "turn_id": handle.turn_id,
                    "status": status,
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                    "usage": usage_total,
                    "error": error_text,
                },
            )
        )


_PERMISSION_UPGRADED: Final[str] = "\0permission-upgraded\0"


async def _run_gated_tool(
    handle: TurnHandle,
    permission_mode: str,
    call_id: str,
    name: str,
    args: dict[str, Any],
    cwd: Path,
) -> tuple[str, bool]:
    """Ask first when the mode says so; run otherwise.

    The ladder (jarvis/agent_chat/permissions.py): ``ask`` asks for every
    mutating tool; ``accept-edits`` lets Write/Edit through and asks for
    RunCommand; ``auto`` asks for nothing; ``plan`` never reaches here (the
    model has no mutating tools) but is treated like ``ask`` if it did.
    """
    if name in READ_ONLY_TOOLS or permission_mode == "auto":
        return await execute_tool(name, args, cwd=cwd)
    if permission_mode == "accept-edits" and name in EDIT_TOOLS:
        return await execute_tool(name, args, cwd=cwd)
    decision = await handle.request_approval(call_id, name, args, summarize_call(name, args))
    if decision == "deny":
        return "Denied by the user.", True
    if decision == "cancel":
        return "Cancelled.", True
    if decision == "allow_always":
        return _PERMISSION_UPGRADED, False
    return await execute_tool(name, args, cwd=cwd)


async def _stream(brain: Any, req: BrainRequest, cancel: asyncio.Event):
    """Iterate the brain stream, ending early when ``cancel`` is set."""
    agen = brain.complete(req)
    try:
        while True:
            if cancel.is_set():
                return
            nxt = asyncio.ensure_future(agen.__anext__())
            waiter = asyncio.ensure_future(cancel.wait())
            done, _ = await asyncio.wait({nxt, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if waiter in done and nxt not in done:
                nxt.cancel()
                return
            waiter.cancel()
            try:
                delta: BrainDelta = nxt.result()
            except StopAsyncIteration:
                return
            yield delta
    finally:
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception as exc:  # noqa: BLE001 — closing a half-read stream may raise
                log.debug("agent chat: closing the provider stream raised %s", exc)
