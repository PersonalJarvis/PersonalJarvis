"""Client side of one Agent Client Protocol (ACP) prompt turn.

ACP is JSON-RPC 2.0 over the agent process' stdio, one JSON object per line
(https://agentclientprotocol.com). Hermes (``hermes acp``) and OpenClaw
(``openclaw acp``) both speak it, and it is the interface their projects
version and document for embedding hosts — which is why Jarvis drives them
through it instead of their CLI flags or internal APIs: an upstream update
keeps working as long as the protocol does.

One :class:`AcpTurn` drives one process from handshake to the prompt's
response:

1. ``initialize`` (protocol version + client capabilities),
2. ``session/load`` of the stored conversation, or ``session/new``,
3. ``session/prompt`` with the turn's text,

and translates every ``session/update`` notification into agent-chat events
(``assistant_text``, ``reasoning``, ``tool_call``, ``tool_result``,
``usage_delta``). The agent's own ``session/request_permission`` requests are
answered from the chat's approval card. History replayed during
``session/load`` is swallowed: the chat already shows it.

The class is transport-free: the CLI runner pumps lines into
:meth:`AcpTurn.on_message` and supplies an :class:`AcpIO` for writing frames,
emitting events and asking the person. That keeps process ownership (job
objects, cancellation, remote placement, rollover) in one place.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from jarvis.agent_chat.events import make_event

log = logging.getLogger(__name__)

#: ACP protocol major version this client speaks.
PROTOCOL_VERSION: Final[int] = 1

_INITIALIZE_ID: Final[int] = 1
_SESSION_ID: Final[int] = 2
_PROMPT_ID: Final[int] = 3

#: Error text the CLI runner's resume-lost detection recognises
#: (``runner_cli._RESUME_LOST_MARKERS``) so a vanished conversation is
#: retried fresh with the transcript in front.
RESUME_LOST_ERROR: Final[str] = "ACP session not found"

#: JSON-RPC "method not found".
_METHOD_NOT_FOUND: Final[int] = -32601

#: ``mcp_<server>_<tool>`` (Hermes) and ``<server>__<tool>`` spellings of a
#: Jarvis MCP tool, normalised to the ``mcp__jarvis__<tool>`` form the
#: society checkpoints and quests read.
_JARVIS_TOOL_PREFIXES: Final[tuple[str, ...]] = ("mcp_jarvis_", "jarvis__", "jarvis.")
_IDENTIFIER: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][\w.:-]{0,120}$")


class AcpIO(Protocol):
    """What a turn needs from its host."""

    async def write(self, frame: dict[str, Any]) -> None: ...

    async def emit(self, event: dict[str, Any]) -> None: ...

    async def ask(self, call_id: str, name: str, args: dict[str, Any], summary: str) -> str:
        """Answer ``allow`` / ``allow_always`` / ``deny`` / ``cancel``."""
        ...


@dataclass(slots=True)
class McpServer:
    """One MCP server handed to the agent with ``session/new``/``session/load``."""

    name: str
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def to_acp(self) -> dict[str, Any]:
        if self.command:
            return {
                "name": self.name,
                "command": self.command,
                "args": list(self.args),
                "env": [{"name": k, "value": v} for k, v in self.env.items()],
            }
        return {
            "type": "http",
            "name": self.name,
            "url": self.url,
            "headers": [{"name": k, "value": v} for k, v in self.headers.items()],
        }


def normalize_tool_name(name: str) -> str:
    """Give a Jarvis MCP tool the ``mcp__jarvis__<tool>`` name Jarvis reads."""
    if name.startswith("mcp__"):
        return name
    for prefix in _JARVIS_TOOL_PREFIXES:
        if name.startswith(prefix):
            return "mcp__jarvis__" + name[len(prefix) :]
    return name


def _content_text(content: Any) -> str:
    """Text of an ACP content block, a list of them, or a tool-call content list."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_content_text(item) for item in content)
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text") or "")
        if content.get("type") == "content":
            return _content_text(content.get("content"))
        if content.get("type") == "diff":
            return f"edited {content.get('path') or 'a file'}"
        if "text" in content:
            return str(content.get("text") or "")
    return ""


@dataclass(slots=True)
class AcpTurn:
    """State of one ACP turn; mirrors the CLI runner's per-shape state fields."""

    turn_id: str
    cwd: str
    prompt_text: str
    resume: str | None = None
    mcp_servers: list[McpServer] = field(default_factory=list)
    #: Answer the agent's permission requests with "allow" without asking
    #: (the chat's Bypass stance). Jarvis' own gates on MCP tools still apply.
    auto_allow: bool = False
    #: Refuse every permission request without asking (Plan mode: read only).
    auto_deny: bool = False
    client_name: str = "personal-jarvis"
    client_version: str = ""
    #: What the chat stores as its vendor session instead of the ACP session
    #: id (OpenClaw addresses the conversation by a fixed session key).
    report_session: str | None = None

    # --- fields the CLI runner reads after the turn ---
    vendor_session: str | None = None
    status: str = "done"
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None
    emitted_text: bool = False
    result_text: str = ""
    saw_result: bool = False
    stop_reason: str = ""
    emitted_tool_ids: set[str] = field(default_factory=set)

    # --- what the agent announced in ``initialize`` ---
    agent_name: str = ""
    agent_version: str = ""
    can_load: bool = False

    _acp_session: str = field(default="", init=False)
    _replaying: bool = field(default=False, init=False)
    _text_id: str = field(default="", init=False)
    _text_parts: list[str] = field(default_factory=list, init=False)
    _thought_parts: list[str] = field(default_factory=list, init=False)
    _thought_started: float | None = field(default=None, init=False)
    _tool_names: dict[str, str] = field(default_factory=dict, init=False)
    _tool_started: dict[str, float] = field(default_factory=dict, init=False)
    _finished_tools: set[str] = field(default_factory=set, init=False)

    # ------------------------------------------------------------------ frames

    def opening_frame(self) -> str:
        """The ``initialize`` request, written as soon as the process starts."""
        return frame_line(
            {
                "jsonrpc": "2.0",
                "id": _INITIALIZE_ID,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    # No fs / terminal capability: the agent uses its own tools
                    # in its own working folder; Jarvis is not an editor.
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                    "clientInfo": {
                        "name": self.client_name,
                        "version": self.client_version or "0",
                    },
                },
            }
        )

    def _session_params(self) -> dict[str, Any]:
        return {"cwd": self.cwd, "mcpServers": [s.to_acp() for s in self.mcp_servers]}

    # ---------------------------------------------------------------- dispatch

    async def on_message(self, obj: dict[str, Any], io: AcpIO) -> None:
        """Handle one JSON object read from the agent's stdout."""
        method = obj.get("method")
        if isinstance(method, str):
            if "id" in obj and obj.get("id") is not None:
                await self._on_agent_request(obj, method, io)
            elif method == "session/update":
                params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
                if not self._replaying:
                    await self._on_update(params, io)
            return
        if "id" in obj:
            await self._on_response(obj, io)

    async def _on_response(self, obj: dict[str, Any], io: AcpIO) -> None:
        rid = obj.get("id")
        error = obj.get("error") if isinstance(obj.get("error"), dict) else None
        result = obj.get("result")
        if rid == _INITIALIZE_ID:
            if error is not None:
                self._fail(f"ACP initialize failed: {_error_text(error)}")
                return
            res = result if isinstance(result, dict) else {}
            info = res.get("agentInfo") if isinstance(res.get("agentInfo"), dict) else {}
            self.agent_name = str(info.get("name") or "")
            self.agent_version = str(info.get("version") or "")
            caps = (
                res.get("agentCapabilities")
                if isinstance(res.get("agentCapabilities"), dict)
                else {}
            )
            self.can_load = bool(caps.get("loadSession"))
            if self.resume and self.can_load:
                self._replaying = True
                params = self._session_params() | {"sessionId": self.resume}
                await io.write(_request(_SESSION_ID, "session/load", params))
            elif self.resume:
                # The agent cannot reopen conversations: let the runner start
                # fresh with the transcript in front instead.
                self._fail(f"{RESUME_LOST_ERROR}: this agent cannot load sessions")
            else:
                await io.write(_request(_SESSION_ID, "session/new", self._session_params()))
            return
        if rid == _SESSION_ID:
            self._replaying = False
            # Hermes answers an unknown id with an empty result ({}), the
            # spec with null or an error; a real load always describes the
            # session (models, modes, _meta).
            if error is not None or (self.resume and not result):
                if self.resume:
                    detail = _error_text(error) if error else "unknown id"
                    self._fail(f"{RESUME_LOST_ERROR}: {detail}")
                else:
                    self._fail(f"ACP session/new failed: {_error_text(error or {})}")
                return
            res = result if isinstance(result, dict) else {}
            self._acp_session = str(res.get("sessionId") or self.resume or "")
            if not self._acp_session:
                self._fail("ACP session/new returned no session id")
                return
            self.vendor_session = self.report_session or self._acp_session
            await io.write(
                _request(
                    _PROMPT_ID,
                    "session/prompt",
                    {
                        "sessionId": self._acp_session,
                        "prompt": [{"type": "text", "text": self.prompt_text}],
                    },
                )
            )
            return
        if rid == _PROMPT_ID:
            await self._flush_thought(io)
            await self._flush_text(io)
            self.saw_result = True
            if error is not None:
                self._fail(_error_text(error))
                return
            res = result if isinstance(result, dict) else {}
            self.stop_reason = str(res.get("stopReason") or "")
            self._take_usage(res.get("usage"))
            if self.stop_reason == "refusal" and not self.emitted_text:
                self._fail("The model refused this request.")
            elif self.stop_reason in {"max_tokens", "max_turn_requests"}:
                self._fail(f"The agent stopped early ({self.stop_reason}).")
            return

    async def cancel(self, io: AcpIO) -> None:
        """Abort the upstream session before its stdio bridge is terminated."""
        if self._acp_session and not self.saw_result:
            await io.write({
                "jsonrpc": "2.0", "method": "session/cancel",
                "params": {"sessionId": self._acp_session},
            })

    async def _on_agent_request(self, obj: dict[str, Any], method: str, io: AcpIO) -> None:
        rid = obj.get("id")
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
        if method == "session/request_permission":
            outcome = await self._permission(params, io)
            await io.write({"jsonrpc": "2.0", "id": rid, "result": {"outcome": outcome}})
            return
        # fs/*, terminal/* and anything newer: not offered in initialize.
        await io.write(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": _METHOD_NOT_FOUND, "message": f"{method} is not supported"},
            }
        )

    async def _permission(self, params: dict[str, Any], io: AcpIO) -> dict[str, Any]:
        options = [o for o in params.get("options") or [] if isinstance(o, dict)]
        by_kind: dict[str, str] = {}
        for option in options:
            kind = str(option.get("kind") or "")
            if kind and kind not in by_kind:
                by_kind[kind] = str(option.get("optionId") or "")
        call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        call_id = str(call.get("toolCallId") or uuid.uuid4().hex)
        if call_id not in self.emitted_tool_ids and not (self.auto_allow or self.auto_deny):
            # The approval card sits on a tool row; give it one.
            await self._flush_text(io)
            await self._tool_start(call | {"toolCallId": call_id}, io)
        name = self._tool_names.get(call_id) or _tool_name(call)
        args = call.get("rawInput") if isinstance(call.get("rawInput"), dict) else {}
        summary = str(call.get("title") or name)[:200]
        if self.auto_deny:
            decision = "deny"
        elif self.auto_allow:
            decision = "allow"
        else:
            await self._flush_text(io)
            decision = await io.ask(call_id, name, args, summary)
        wanted: tuple[str, ...]
        if decision == "allow_always":
            wanted = ("allow_always", "allow_once")
        elif decision == "allow":
            wanted = ("allow_once", "allow_always")
        elif decision == "deny":
            wanted = ("reject_once", "reject_always")
        else:
            return {"outcome": "cancelled"}
        for kind in wanted:
            if by_kind.get(kind):
                return {"outcome": "selected", "optionId": by_kind[kind]}
        return {"outcome": "cancelled"}

    # ----------------------------------------------------------------- updates

    async def _on_update(self, params: dict[str, Any], io: AcpIO) -> None:
        update = params.get("update") if isinstance(params.get("update"), dict) else {}
        kind = str(update.get("sessionUpdate") or "")
        if kind == "agent_message_chunk":
            await self._flush_thought(io)
            text = _content_text(update.get("content"))
            if text:
                if not self._text_id:
                    self._text_id = f"acp-{self.turn_id}-{uuid.uuid4().hex[:8]}"
                self._text_parts.append(text)
                await io.emit(
                    make_event(
                        "text_delta",
                        {"turn_id": self.turn_id, "message_id": self._text_id, "text": text},
                    )
                )
            return
        if kind == "agent_thought_chunk":
            text = _content_text(update.get("content"))
            if text:
                if self._thought_started is None:
                    self._thought_started = time.perf_counter()
                    await io.emit(
                        make_event(
                            "reasoning_started",
                            {"turn_id": self.turn_id, "message_id": f"think-{self.turn_id}"},
                        )
                    )
                self._thought_parts.append(text)
                await io.emit(
                    make_event("reasoning_delta", {"turn_id": self.turn_id, "text": text})
                )
            return
        if kind == "tool_call":
            await self._flush_thought(io)
            await self._flush_text(io)
            await self._tool_start(update, io)
            await self._tool_finish_if_done(update, io)
            return
        if kind == "tool_call_update":
            call_id = str(update.get("toolCallId") or "")
            if call_id and call_id not in self.emitted_tool_ids:
                await self._flush_text(io)
                await self._tool_start(update, io)
            await self._tool_finish_if_done(update, io)
            return
        if kind == "usage_update":
            self._take_usage(update)
            if self.usage:
                await io.emit(
                    make_event("usage_delta", {"turn_id": self.turn_id, "usage": dict(self.usage)})
                )
            return
        # plan, available_commands_update, current_mode_update,
        # session_info_update, user_message_chunk: nothing the timeline shows.

    async def _tool_start(self, update: dict[str, Any], io: AcpIO) -> None:
        call_id = str(update.get("toolCallId") or uuid.uuid4().hex)
        if call_id in self.emitted_tool_ids:
            return
        name = normalize_tool_name(_tool_name(update))
        args = update.get("rawInput") if isinstance(update.get("rawInput"), dict) else {}
        self.emitted_tool_ids.add(call_id)
        self._tool_names[call_id] = name
        self._tool_started[call_id] = time.perf_counter()
        await io.emit(
            make_event(
                "tool_call",
                {
                    "turn_id": self.turn_id,
                    "call_id": call_id,
                    "name": name,
                    "input": args,
                    "summary": str(update.get("title") or name)[:200],
                },
            )
        )

    async def _tool_finish_if_done(self, update: dict[str, Any], io: AcpIO) -> None:
        call_id = str(update.get("toolCallId") or "")
        status = str(update.get("status") or "")
        if status not in {"completed", "failed"} or call_id in self._finished_tools:
            return
        self._finished_tools.add(call_id)
        output = _content_text(update.get("content"))
        raw = update.get("rawOutput")
        if not output and raw is not None:
            output = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        is_error = status == "failed"
        started = self._tool_started.get(call_id)
        await io.emit(
            make_event(
                "tool_result",
                {
                    "turn_id": self.turn_id,
                    "call_id": call_id,
                    "output": (output or ("failed" if is_error else "done"))[:20_000],
                    "is_error": is_error,
                    "duration_ms": int((time.perf_counter() - started) * 1000)
                    if started
                    else None,
                },
            )
        )

    async def _flush_text(self, io: AcpIO) -> None:
        text = "".join(self._text_parts)
        message_id = self._text_id
        self._text_parts.clear()
        self._text_id = ""
        if not text.strip():
            return
        self.emitted_text = True
        self.result_text = text
        await io.emit(
            make_event(
                "assistant_text",
                {"turn_id": self.turn_id, "message_id": message_id, "text": text},
            )
        )

    async def _flush_thought(self, io: AcpIO) -> None:
        if self._thought_started is None:
            return
        text = "".join(self._thought_parts)
        duration = int((time.perf_counter() - self._thought_started) * 1000)
        self._thought_parts.clear()
        self._thought_started = None
        await io.emit(
            make_event(
                "reasoning",
                {
                    "turn_id": self.turn_id,
                    "message_id": f"think-{self.turn_id}",
                    "text": text,
                    "duration_ms": duration,
                },
            )
        )

    def _take_usage(self, raw: Any) -> None:
        """Read ACP usage (``usage_update`` or the prompt response's ``usage``)."""
        if not isinstance(raw, dict):
            return
        for theirs, ours in (
            ("inputTokens", "input_tokens"),
            ("outputTokens", "output_tokens"),
            ("cachedReadTokens", "cached_input_tokens"),
            ("cachedWriteTokens", "cache_write_input_tokens"),
            ("thoughtTokens", "reasoning_output_tokens"),
        ):
            value = raw.get(theirs)
            if isinstance(value, int | float):
                self.usage[ours] = int(value)
        cost = raw.get("cost")
        if isinstance(cost, dict):
            amount = cost.get("amount")
            if isinstance(amount, int | float) and str(cost.get("currency") or "USD") == "USD":
                self.cost_usd = float(amount)

    def _fail(self, message: str) -> None:
        self.status = "error"
        self.error = message
        self.saw_result = True


def _tool_name(update: dict[str, Any]) -> str:
    """Best tool name an ACP tool call carries.

    ACP has no tool-name field; agents put it in ``_meta`` or use it as the
    ``title`` for tools they do not render specially (Hermes does both for
    MCP tools). A descriptive title falls back to the tool ``kind``.
    """
    meta = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
    for key in ("toolName", "tool_name", "name"):
        value = meta.get(key)
        if isinstance(value, str) and value:
            return value
    title = str(update.get("title") or "").strip()
    if title and _IDENTIFIER.match(title):
        return title
    if title:
        head = title.split("(", 1)[0].split(":", 1)[0].strip()
        if head and _IDENTIFIER.match(head):
            return head
    return str(update.get("kind") or "tool")


def _error_text(error: dict[str, Any]) -> str:
    message = str(error.get("message") or "error")
    data = error.get("data")
    if isinstance(data, dict):
        # Hermes' ACP SDK nests this local configuration failure under
        # details. Translate only the known numeric contract; never expose
        # arbitrary upstream details (which may contain provider bodies).
        detail = str(data.get("details") or "")
        bounds = re.search(
            r"context window of ([\d,]+) tokens, which is below the minimum "
            r"([\d,]+) required by Hermes Agent\.", detail,
        )
        if bounds:
            actual, minimum = bounds.groups()
            return (
                f"Hermes requires at least {minimum} context tokens; this model has {actual}. "
                "Increase the model context in Settings or choose a model with a larger window."
            )
    if isinstance(data, str) and data:
        return f"{message}: {data}"
    if isinstance(data, dict) and data.get("message"):
        return f"{message}: {data['message']}"
    return message


def _request(rid: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


def frame_line(frame: dict[str, Any]) -> str:
    """One frame as the newline-terminated JSON line ACP expects on stdin."""
    return json.dumps(frame, ensure_ascii=False) + "\n"
