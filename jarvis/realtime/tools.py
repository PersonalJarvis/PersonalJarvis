"""Provider-neutral realtime tool declarations and safe execution bridge."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

from jarvis.brain.tool_use_loop import (
    _is_instructional_question,
    _is_meta_debug_intent,
    _is_self_identification,
    _is_side_effect_tool,
    _is_stt_hallucinated,
    _should_block_action_as_research,
)
from jarvis.core import runtime_refs
from jarvis.core.protocols import (
    RiskTier,
    SupervisorToolDescriptor,
    SupervisorToolGateway,
    SupervisorToolRequest,
)
from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL
from jarvis.voice.echo_confirmation import classify_response
from jarvis.voice.tool_confirmation import format_tool_confirmation

_VALID_WIRE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_MAX_DESCRIPTION_CHARS = 4_000
_MAX_ARGUMENT_CHARS = 32_000
_MAX_RESULT_CHARS = 8_000


def _wire_name(name: str) -> str:
    """Return a deterministic identifier accepted by both provider families."""
    if _VALID_WIRE_NAME.fullmatch(name):
        return name
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_") or "tool"
    if not normalized[0].isalpha() and normalized[0] != "_":
        normalized = f"tool_{normalized}"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"{normalized[:52]}_{digest}"


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _bounded_result(success: bool, output: Any, error: str | None) -> dict[str, Any]:
    payload = {
        "success": bool(success),
        "output": _json_safe(output),
        "error": str(error) if error else None,
    }
    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    if len(serialized) <= _MAX_RESULT_CHARS:
        return payload
    return {
        "success": bool(success),
        "output": serialized[:_MAX_RESULT_CHARS],
        "error": (
            f"Tool output was truncated from {len(serialized)} characters "
            f"to {_MAX_RESULT_CHARS}."
        ),
        "truncated": True,
    }


@dataclass(slots=True)
class _PendingConfirmation:
    trace_id: UUID
    tool_name: str
    confirmed: bool = False


class RealtimeToolBridge:
    """Expose the live router tools and execute only through ``ToolExecutor``."""

    def __init__(
        self,
        *,
        tools: dict[str, Any] | None = None,
        executor: Any = None,
        gateway: SupervisorToolGateway | None = None,
        language: str,
        tools_source: Any = None,
    ) -> None:
        self._tools = dict(tools or {})
        self._tools_source = tools_source
        self._executor = executor
        self._gateway = gateway
        self._language = language
        self._descriptors: dict[str, SupervisorToolDescriptor] = (
            self._read_descriptors()
        )
        self._wire_to_name: dict[str, str] = {}
        self._declarations: tuple[dict[str, Any], ...] = self._build_declarations()
        self._pending: _PendingConfirmation | None = None
        self._vetoed_tool = ""
        self._last_user_text = ""

    @classmethod
    def from_brain(cls, _brain: Any, *, language: str) -> RealtimeToolBridge | None:
        """Build from the public supervisor gateway registered by Brain factory."""
        gateway = runtime_refs.get_supervisor_tool_gateway()
        if gateway is None or not gateway.catalog():
            return None
        return cls(gateway=gateway, language=language)

    def _read_descriptors(
        self,
        tools_override: dict[str, Any] | None = None,
    ) -> dict[str, SupervisorToolDescriptor]:
        if self._gateway is not None:
            try:
                return {item.name: item for item in self._gateway.catalog()}
            except Exception:  # noqa: BLE001 - a catalog refresh degrades safely
                return {}

        descriptors: dict[str, SupervisorToolDescriptor] = {}
        source_tools = self._tools if tools_override is None else tools_override
        for name, tool in source_tools.items():
            schema = getattr(tool, "schema", None)
            if not isinstance(schema, dict):
                continue
            raw_tier = str(getattr(tool, "risk_tier", "monitor"))
            risk_tier = cast(
                RiskTier,
                raw_tier if raw_tier in {"safe", "monitor", "ask", "block"}
                else "monitor",
            )
            descriptors[str(name)] = SupervisorToolDescriptor(
                name=str(name),
                description=str(getattr(tool, "description", "")),
                input_schema=schema,
                risk_tier=risk_tier,
                is_action_tool=bool(getattr(tool, "is_action_tool", False)),
            )
        return descriptors

    def _build_declarations(self) -> tuple[dict[str, Any], ...]:
        self._wire_to_name.clear()
        declarations: list[dict[str, Any]] = []
        for name, descriptor in sorted(self._descriptors.items()):
            wire = _wire_name(str(name))
            if wire in self._wire_to_name:
                continue
            self._wire_to_name[wire] = str(name)
            declarations.append(
                {
                    "name": wire,
                    "description": descriptor.description[
                        :_MAX_DESCRIPTION_CHARS
                    ],
                    "parameters": descriptor.input_schema,
                }
            )
        return tuple(declarations)

    @property
    def declarations(self) -> tuple[dict[str, Any], ...]:
        return self._declarations

    def set_language(self, language: str) -> None:
        self._language = language

    def refresh_from_source(self) -> bool:
        """Refresh a live BrainManager tool replacement safely.

        Returns ``True`` only when the provider-facing declarations changed.
        A tool awaiting voice confirmation is retained until that confirmation
        resolves, so a concurrent registry refresh cannot strand the pending
        ``ToolExecutor`` action.
        """
        if self._gateway is not None:
            refreshed_descriptors = self._read_descriptors()
            refreshed_tools: dict[str, Any] | None = None
        else:
            source = self._tools_source
            if not callable(source):
                return False
            current = source()
            if not isinstance(current, dict):
                return False
            try:
                refreshed_tools = dict(current)
            except RuntimeError:
                return False
            refreshed_descriptors = self._read_descriptors(refreshed_tools)
        pending = self._pending
        if (
            pending is not None
            and pending.tool_name not in refreshed_descriptors
            and pending.tool_name in self._descriptors
        ):
            refreshed_descriptors[pending.tool_name] = self._descriptors[
                pending.tool_name
            ]
            if (
                refreshed_tools is not None
                and pending.tool_name not in refreshed_tools
                and pending.tool_name in self._tools
            ):
                refreshed_tools[pending.tool_name] = self._tools[pending.tool_name]
        previous_declarations = self._declarations
        if refreshed_tools is not None:
            self._tools = refreshed_tools
        self._descriptors = refreshed_descriptors
        self._declarations = self._build_declarations()
        return self._declarations != previous_declarations

    async def handle_user_transcript(self, text: str) -> None:
        self._last_user_text = text
        self._vetoed_tool = ""
        pending = self._pending
        if pending is None:
            return
        verdict = classify_response(text, language=self._language)
        if verdict == "confirm":
            pending.confirmed = True
        elif verdict == "veto":
            await self._cancel_pending(pending.trace_id)
            self._vetoed_tool = pending.tool_name
            self._pending = None

    async def execute(
        self,
        *,
        wire_name: str,
        arguments: dict[str, Any],
        trace_id: UUID | None = None,
    ) -> tuple[str, dict[str, Any]]:
        name = self._wire_to_name.get(wire_name, "")
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            await self._publish_denied(wire_name, "unknown realtime tool")
            return wire_name, {
                "success": False,
                "error": "Tool is not available in this session.",
            }
        if self._vetoed_tool == name:
            return name, {
                "success": False,
                "error": "The user declined this action. Do not ask again in this turn.",
            }
        validation_error = self._validate_arguments(descriptor, arguments)
        if validation_error:
            await self._publish_denied(name, validation_error)
            return name, {"success": False, "error": validation_error}

        guard_error = await self._guard(descriptor, name, arguments)
        if guard_error:
            return name, {"success": False, "error": guard_error}

        pending = self._pending
        if pending is not None and pending.tool_name == name:
            if not pending.confirmed:
                return name, {
                    "success": False,
                    "confirmation_required": True,
                    "message": format_tool_confirmation(
                        name, language=self._language
                    ),
                }
            result = await self._execute_confirmed(pending.trace_id)
            self._pending = None
            return name, _bounded_result(
                bool(getattr(result, "success", False)),
                getattr(result, "output", None),
                getattr(result, "error", None),
            )

        trace_id = trace_id or uuid4()
        result = await self._execute_tool(name, arguments, trace_id)
        if (
            getattr(result, "error", None) == VOICE_CONFIRM_SENTINEL
            and isinstance(getattr(result, "output", None), dict)
        ):
            self._pending = _PendingConfirmation(trace_id=trace_id, tool_name=name)
            return name, {
                "success": False,
                "confirmation_required": True,
                "message": format_tool_confirmation(name, language=self._language),
                "instruction": (
                    "Ask the user this question. Call the same function again only "
                    "after a clear affirmative answer."
                ),
            }
        return name, _bounded_result(
            bool(getattr(result, "success", False)),
            getattr(result, "output", None),
            getattr(result, "error", None),
        )

    async def _execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        trace_id: UUID,
    ) -> Any:
        if self._gateway is not None:
            return await self._gateway.execute(
                name,
                arguments,
                SupervisorToolRequest(
                    trace_id=trace_id,
                    origin="realtime",
                    user_utterance=self._last_user_text,
                    rationale="Realtime model requested an available Jarvis tool.",
                    config_snapshot={
                        "output_language": self._language,
                        "voice_confirm": True,
                    },
                ),
            )
        tool = self._tools[name]
        return await self._executor.execute(
            tool,
            arguments,
            user_utterance=self._last_user_text,
            config_snapshot={
                "output_language": self._language,
                "voice_confirm": True,
            },
            trace_id=trace_id,
            rationale="Realtime model requested an available Jarvis tool.",
        )

    async def _execute_confirmed(self, trace_id: UUID) -> Any:
        if self._gateway is not None:
            return await self._gateway.execute_confirmed(
                trace_id,
                SupervisorToolRequest(
                    trace_id=trace_id,
                    origin="realtime",
                    user_utterance=self._last_user_text,
                    config_snapshot={"output_language": self._language},
                ),
            )
        return await self._executor.execute_confirmed(
            trace_id,
            user_utterance=self._last_user_text,
            config_snapshot={"output_language": self._language},
        )

    async def _cancel_pending(self, trace_id: UUID) -> bool:
        if self._gateway is not None:
            return await self._gateway.cancel_pending(trace_id)
        return bool(await self._executor.cancel_pending(trace_id))

    def _validate_arguments(
        self,
        descriptor: SupervisorToolDescriptor,
        arguments: Any,
    ) -> str:
        if not isinstance(arguments, dict):
            return "Tool arguments must be a JSON object."
        try:
            size = len(json.dumps(arguments, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001
            return "Tool arguments are not JSON serializable."
        if size > _MAX_ARGUMENT_CHARS:
            return f"Tool arguments exceed the {_MAX_ARGUMENT_CHARS}-character limit."
        schema = descriptor.input_schema
        required = schema.get("required", ())
        missing = [key for key in required if key not in arguments]
        if missing:
            return f"Missing required tool arguments: {', '.join(map(str, missing))}."
        return ""

    async def _guard(
        self,
        descriptor: SupervisorToolDescriptor,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        user_text = self._last_user_text
        blocked, reason = _is_stt_hallucinated(name, arguments)
        if blocked:
            message = f"Suspected speech-recognition argument error: {reason}"
        elif _is_instructional_question(user_text) and _is_side_effect_tool(descriptor):
            message = "The user asked for instructions; the side-effect tool was not run."
        elif _is_self_identification(user_text) and _is_side_effect_tool(descriptor):
            message = "The user was introducing themselves; the side-effect tool was not run."
        elif name == "spawn_worker" and _is_meta_debug_intent(user_text):
            message = "A meta/debug request must be answered directly, not delegated."
        elif _should_block_action_as_research(
            descriptor,
            name,
            user_text,
            None,
            "",
        ):
            message = "This sounds like research, not an action on a connected system."
        else:
            return ""
        await self._publish_denied(name, message)
        return message

    async def _publish_denied(self, name: str, reason: str) -> None:
        publisher = getattr(
            self._gateway if self._gateway is not None else self._executor,
            "publish_guard_denied",
            None,
        )
        if callable(publisher):
            try:
                await publisher(name, reason, trace_id=uuid4())
            except Exception:  # noqa: BLE001, S110 — observability cannot break safety
                pass

    async def close(self) -> None:
        if self._pending is not None:
            await self._cancel_pending(self._pending.trace_id)
            self._pending = None


__all__ = ["RealtimeToolBridge"]
