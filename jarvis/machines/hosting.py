"""Bind a hosted agent loop to its canonical hub chat and existing authorization path."""

from __future__ import annotations

import asyncio
import contextvars
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from jarvis.agent_chat.events import make_event
from jarvis.core.protocols import BrainMessage, BrainRequest

from .service import machine_service


async def run_if_hosted(handle: Any, text: str, *, bridge: Any, always_allowed: set[str]) -> bool:
    """Return False for a local agent. A remote failure never falls back to the hub's shell."""
    if handle.session.surface != "society":
        return False
    from jarvis.society.runtime import current_runtime

    runtime = current_runtime()
    if runtime is None:
        return False
    agent_id = handle.session.session_id.removeprefix("society:")
    hub = machine_service(runtime.data_dir)
    await hub.start()
    rows = await hub.store.rows(
        "SELECT host_id,moving FROM placements WHERE agent_id=?", (agent_id,)
    )
    if not rows or (rows[0]["host_id"] == "local" and not rows[0]["moving"]):
        return False
    started = time.monotonic()
    status, error = "done", None
    usage: dict[str, int] = {}
    context = None
    try:
        if rows[0]["moving"]:
            raise PermissionError("This agent is moving; new work is paused")
        host_id = rows[0]["host_id"]
        context = HostedContext(runtime, handle, host_id, bridge, always_allowed, usage)
        await context.prepare()
        if agent_id in hub.rpc_handlers:
            raise PermissionError("This agent already has a hosted turn")
        hub.rpc_handlers[agent_id] = (host_id, context.dispatch)
        from jarvis.agent_chat.runner_api import messages_from_events

        messages = [asdict(m) for m in messages_from_events(handle.history)]
        messages.append({"role": "user", "content": text})
        result = await hub.execute_on_machine(
            agent_id=agent_id,
            machine_id=host_id,
            operation="agent_turn",
            args={"messages": messages},
            trace_id=str(handle.trace_id),
            timeout_s=900,
        )
        if not result["success"]:
            raise RuntimeError(result.get("error") or "Hosted turn failed")
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except Exception as exc:  # noqa: BLE001 — same terminal event as every other chat runner
        status, error = "error", str(exc)
    finally:
        hub.rpc_handlers.pop(agent_id, None)
        if context is not None:
            await context.close()
        await handle.emit(
            make_event(
                "turn_finished",
                {
                    "turn_id": handle.turn_id,
                    "status": status,
                    "error": error,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "usage": usage,
                    "output_language": context.language if context is not None else "",
                },
            )
        )
    return True


class HostedContext:
    def __init__(
        self,
        runtime: Any,
        handle: Any,
        host_id: str,
        bridge: Any,
        always_allowed: set[str],
        usage: dict[str, int],
    ) -> None:
        self.runtime, self.handle, self.host_id = runtime, handle, host_id
        self.bridge, self.always_allowed, self.usage = bridge, always_allowed, usage
        self.agent_id = handle.session.session_id.removeprefix("society:")
        self.tools: dict[str, Any] = {}
        self.system = ""
        self.executor: Any = None
        self.provider: Any = None
        self.override: Any = None
        self.rounds = 0
        self._armed = False
        self._call_id = ""
        self.allowed_calls: dict[str, dict[str, Any]] = {}
        self.images: list[Any] = []
        self.origin_context = contextvars.copy_context()
        self.active_rpcs: set[asyncio.Task[Any]] = set()
        self.language = ""
        from jarvis.core.chat_turn import current_chat_turn

        origin = current_chat_turn.get()
        self.user_text = origin.user_text if origin is not None else ""

    async def dispatch(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        task = asyncio.create_task(self.answer(method, payload), context=self.origin_context.copy())
        self.active_rpcs.add(task)
        try:
            return await task
        finally:
            self.active_rpcs.discard(task)

    async def prepare(self) -> None:
        from jarvis.agent_chat.approval_bridge import ChatGrant, approval_ref
        from jarvis.agent_chat.runner_api import supports_api_runner
        from jarvis.agent_chat.runner_brain import brain_manager, build_override, kit_payload
        from jarvis.agent_chat.service import resolve_runner

        handle = self.handle
        session = handle.session
        if not supports_api_runner(session.provider) or resolve_runner(
            session.provider, surface="society"
        ) not in {"brain", "api"}:
            raise PermissionError(
                "The selected runner needs a host-local CLI login; "
                "API hosting is required on this connector"
            )
        manager = brain_manager()
        self.executor = getattr(manager, "_tool_executor", None)
        if manager is None or self.executor is None:
            raise RuntimeError("Hub tool authorization is not ready")
        kit, extra = await kit_payload(session, manager)
        self.override = build_override(
            session,
            manager,
            stance=handle.stance or "ask",
            cwd=Path(session.cwd),
            ref=approval_ref(session.session_id),
            kit_tools=kit,
            system_extra=extra,
        )
        # Device-bound tools replace all hub-local file/shell/browser hands.
        tools = dict(kit or {})
        from .tools import MachineTool

        tools[MachineTool.name] = MachineTool(
            self.runtime, self.agent_id, default_machine=self.host_id
        )
        excluded = {
            "coding-session",
            "Read",
            "Write",
            "Edit",
            "Ls",
            "Glob",
            "Grep",
            "RunCommand",
            "society_shell",
            "society_browser",
            "society_run_skill",
        }
        tools = {name: tool for name, tool in tools.items() if name not in excluded}
        if self.override.tool_filter:
            tools = self.override.tool_filter(tools)
        self.tools = tools
        self.system = extra + (
            f"\nYou are running on connected computer {self.host_id}. "
            "Use remote-machine for all file and shell work, specifying that machine_id "
            "unless the user explicitly chose another permitted device. "
            "The hub retains your memory and chat. Do not assume the hub's local paths exist here."
        )
        from jarvis.core.turn_language import resolve_output_language

        previous_language = next(
            (
                event.get("payload", {}).get("output_language", "")
                for event in reversed(handle.history)
                if event.get("payload", {}).get("output_language")
            ),
            "",
        )
        self.language = resolve_output_language(
            getattr(getattr(self.runtime.config(), "brain", None), "reply_language", "auto"),
            "unknown",
            self.user_text,
            conversation_language=previous_language,
        )
        self.system += f"\nResponse language for this turn: {self.language}."
        from jarvis.core.config import get_jarvis_agent_secret, override_provider_secrets

        provider_factory = getattr(manager, "_get_brain", None)
        if not callable(provider_factory):
            raise RuntimeError("Hub provider factory is unavailable")
        secret = get_jarvis_agent_secret(session.provider)
        with override_provider_secrets({session.provider: secret} if secret else {}):
            self.provider = provider_factory(
                session.provider,
                self.override.model or None,
                scope=f"machine-hosted:{session.session_id}",
            )
        if self.bridge is not None:
            self.bridge.arm(
                approval_ref(session.session_id),
                ChatGrant(
                    session_id=session.session_id,
                    turn_id=handle.turn_id,
                    stance=handle.stance or "ask",
                    always_allowed=self.always_allowed,
                    ask=handle.request_approval,
                    call_id_for=lambda _name: self._call_id,
                ),
            )
            self._armed = True

    async def check(self) -> None:
        from jarvis.society.events import AgentState

        caller = await self.runtime.roster.get(self.agent_id)
        if (
            caller is None
            or caller.state != AgentState.ACTIVE
            or await self.runtime.store.kill_switch()
        ):
            raise PermissionError("Agent is inactive or the society is halted")
        if self.handle.cancel.is_set():
            raise asyncio.CancelledError

    async def answer(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.check()
        if method == "model":
            return await self.model(payload)
        if method == "tool":
            call_id = str(payload.get("id", ""))
            call = self.allowed_calls.pop(call_id, None)
            if (
                call is None
                or call.get("name") != payload.get("name")
                or call.get("input", {}) != payload.get("input", {})
            ):
                raise PermissionError(
                    "Tool call was not issued by the hub model or was already executed"
                )
            return await self.tool(call)
        raise PermissionError("Unsupported hosted RPC")

    async def model(self, payload: dict[str, Any]) -> dict[str, Any]:
        from jarvis.agent_chat.runner_api import _stream
        from jarvis.core.config import get_jarvis_agent_secret, override_provider_secrets
        from jarvis.costs.ledger import usage_context

        if self.allowed_calls:
            raise PermissionError("Resolve the previous model's tool calls first")
        self.rounds += 1
        if self.rounds > 40:
            raise PermissionError("Hosted turn round budget exhausted")
        session = self.handle.session
        messages = tuple(
            BrainMessage(**{k: v for k, v in row.items() if k != "images"})
            for row in payload["messages"]
        )
        if self.images:
            messages += (
                BrainMessage(
                    role="user",
                    content="Current remote desktop observation.",
                    images=tuple(self.images),
                ),
            )
            self.images.clear()
        request = BrainRequest(
            messages=messages,
            system=self.system,
            tools=tuple(
                {"name": tool.name, "description": tool.description, "input_schema": tool.schema}
                for tool in self.tools.values()
            ),
            reasoning_effort=self.override.reasoning_effort,
        )
        secret = get_jarvis_agent_secret(session.provider)
        message_id = uuid4().hex
        chunks, calls = [], []
        with (
            override_provider_secrets({session.provider: secret} if secret else {}),
            usage_context("agent-chat"),
        ):
            async for delta in _stream(self.provider, request, self.handle.cancel):
                if delta.content:
                    chunks.append(delta.content)
                    await self.emit("text_delta", {"message_id": message_id, "text": delta.content})
                if delta.tool_call:
                    call = dict(delta.tool_call)
                    call.setdefault("id", uuid4().hex)
                    raw_input = call.get("input") or {}
                    call["input"] = (
                        raw_input if isinstance(raw_input, dict) else json.loads(str(raw_input))
                    )
                    if not isinstance(call["input"], dict):
                        raise ValueError("Model tool arguments must be an object")
                    calls.append(call)
                    self.allowed_calls[call["id"]] = call
                if delta.usage:
                    for key, value in delta.usage.items():
                        self.usage[key] = self.usage.get(key, 0) + int(value or 0)
                    await self.emit("usage_delta", {"usage": dict(self.usage)})
        text = "".join(chunks)
        if text:
            await self.emit("assistant_text", {"message_id": message_id, "text": text})
        return {"text": text, "calls": calls}

    async def tool(self, call: dict[str, Any]) -> dict[str, Any]:
        name = call["name"]
        tool = self.tools.get(name)
        if tool is None:
            raise PermissionError("Tool is not granted to this hosted agent")
        self._call_id = call["id"]
        await self.emit(
            "tool_call",
            {
                "call_id": call["id"],
                "name": name,
                "input": call.get("input", {}),
                "meta": {"thought_signature": call["thought_signature"]}
                if call.get("thought_signature")
                else {},
            },
        )
        result = await self.executor.execute(
            tool,
            call.get("input", {}),
            config_snapshot={**self.override.tool_context, "output_language": self.language},
            user_utterance=self.user_text,
            trace_id=self.handle.trace_id,
        )
        from jarvis.brain.tool_use_loop import _images_from_artifacts

        self.images = _images_from_artifacts(result.artifacts)
        await self.emit(
            "tool_result",
            {
                "call_id": call["id"],
                "name": name,
                "output": json.dumps(result.output, ensure_ascii=False),
                "is_error": not result.success,
            },
        )
        return {"success": result.success, "output": result.output, "error": result.error}

    async def emit(self, kind: str, payload: dict[str, Any]) -> None:
        await self.handle.emit(
            make_event(
                kind, {"turn_id": self.handle.turn_id, "machine_id": self.host_id, **payload}
            )
        )

    async def close(self) -> None:
        tasks = list(self.active_rpcs)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._armed:
            from jarvis.agent_chat.approval_bridge import approval_ref

            self.bridge.disarm(approval_ref(self.handle.session.session_id))
        # The hub's scoped factory owns provider clients and configured endpoints.
        # A completed turn must not close a client that the next turn will reuse.
