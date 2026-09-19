"""Model-selected tools with application-owned authorization and receipts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any
from uuid import UUID, uuid4

from jarvis.control.cancel import CancelToken
from jarvis.core.protocols import SupervisorToolGateway, SupervisorToolRequest
from jarvis.live.state import LiveLedger
from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL

log = logging.getLogger(__name__)

# A session.started event includes its declarations. Keep room for prompts and
# history inside the 64 KiB message limit of smaller WebRTC clients.
_CATALOG_BYTE_BUDGET = 24_000
_DISCOVERY_PAGE_SIZE = 8


def _wire_size(value: Any) -> int:
    return len(json.dumps(value).encode("utf-8"))


def take_images(result: dict) -> list[dict]:
    """Separate actual image inputs from text function outputs and stored receipts."""
    images = []
    output = result.get("output")
    if isinstance(output, dict) and "_image" in output:
        output = dict(output)
        images.append(output.pop("_image"))
        result["output"] = output
    artifacts = result.get("artifacts", [])
    images.extend(a for a in artifacts if isinstance(a, dict) and a.get("type") == "image")
    result["artifacts"] = [
        a for a in artifacts if not isinstance(a, dict) or a.get("type") != "image"
    ]
    return images


def function(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


class LiveTools:
    """Single execution owner, including when both RTC and sideband see events."""

    def __init__(
        self,
        gateway: SupervisorToolGateway,
        ledger: LiveLedger,
        session_id: str,
        *,
        language: str,
        backend_model: str,
    ) -> None:
        self.gateway = gateway
        self.ledger = ledger
        self.session_id = session_id
        self.language = language
        self.backend_model = backend_model
        self.user_text = ""
        self.revision = 0
        self.cancel_token = CancelToken()
        self._lock = asyncio.Lock()
        self._pending: dict[str, tuple[UUID, str, dict, int]] = {}
        self._names: dict[str, str] = {}
        self.end_requested = False
        self.accepting = True

    def catalog(self):
        read = getattr(self.gateway, "voice_catalog", self.gateway.catalog)
        return read()

    def accept_new_input(self) -> None:
        """New requests get a fresh token; running work keeps its cancelled token."""
        if self.cancel_token.is_cancelled():
            self.cancel_token = CancelToken()
        self.accepting = True

    async def cancel_work(self) -> None:
        self.cancel_token.cancel("user_cancelled")
        self.accepting = False
        self.revision += 1
        await self._cancel_confirmations()

    def declarations(self) -> list[dict]:
        definitions = [
            function(
                "end_call",
                "End voice when the user asks to hang up. Running agents keep their tasks.",
                {},
                [],
            ),
            function(
                "discover_tools",
                "Find Jarvis tools and their complete input schemas. Empty query browses all "
                "tools. Pass next_offset as offset to read the next page.",
                {"query": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}},
                ["query"],
            ),
            function(
                "call_tool",
                "Execute a discovered Jarvis tool using its canonical name and JSON arguments.",
                {"name": {"type": "string"}, "arguments_json": {"type": "string"}},
                ["name", "arguments_json"],
            ),
            function(
                "confirm_action",
                "Resume the exact pending action the user approved. Never infer approval.",
                {"approval_id": {"type": "string"}},
                ["approval_id"],
            ),
        ]
        # Count alone is insufficient: an imported tool may carry a large schema.
        used_bytes = _wire_size(definitions)
        for descriptor in sorted(self.catalog(), key=lambda d: d.name):
            alias = "jarvis_" + hashlib.sha256(descriptor.name.encode()).hexdigest()[:20]
            definition = {
                "type": "function",
                "name": alias,
                "description": f"{descriptor.name}: {descriptor.description}",
                "parameters": descriptor.input_schema,
            }
            size = _wire_size(definition) + 2
            if len(definitions) >= 52 or used_bytes + size > _CATALOG_BYTE_BUDGET:
                continue
            used_bytes += size
            self._names[alias] = descriptor.name
            definitions.append(definition)
        return definitions

    async def execute(self, call_id: str, name: str, args: dict, revision: int) -> dict:
        async with self._lock:
            if not self.accepting:
                return {"success": False, "status": "voice_closed_before_execution"}
            receipt = await asyncio.to_thread(
                self.ledger.claim,
                self.session_id,
                call_id,
                name,
                args,
                revision,
            )
            if receipt is not None:
                return receipt
            try:
                result = await self._execute(name, args, revision)
            except asyncio.CancelledError:
                # Leave the claim uncertain: a cancelled request may already have acted.
                raise
            except Exception:
                log.exception("Live tool execution failed for %s", name)
                result = {
                    "success": False,
                    "error": "Tool execution failed; inspect its state before retrying.",
                }
            if revision != self.revision:
                result = {
                    **result,
                    "superseded": True,
                    "task_revision": revision,
                    "current_revision": self.revision,
                }
            receipt = dict(result)
            if isinstance(receipt.get("output"), dict):
                receipt["output"] = {k: v for k, v in receipt["output"].items() if k != "_image"}
            take_images(receipt)
            await asyncio.to_thread(self.ledger.finish, self.session_id, call_id, receipt)
            return result

    async def _execute(self, name: str, args: dict, revision: int) -> dict:
        operation_token = self.cancel_token
        if operation_token.is_cancelled():
            return {"success": False, "status": "cancelled"}
        if revision != self.revision:
            return {
                "success": False,
                "status": "superseded",
                "error": "The request changed before execution. Re-evaluate the latest user input.",
            }
        if ":" in name:
            prefix, suffix = name.split(":", 1)
            if prefix.isidentifier() and (
                suffix in self._names
                or suffix
                in {
                    "discover_tools",
                    "call_tool",
                    "confirm_action",
                    "end_call",
                }
            ):
                name = suffix
        if name == "end_call":
            self.end_requested = True
            return {"success": True, "status": "closing_voice"}
        if name == "discover_tools":
            query = str(args.get("query", "")).casefold().split()
            matches = [
                d
                for d in sorted(self.catalog(), key=lambda d: d.name)
                if all(word in (d.name + " " + d.description).casefold() for word in query)
            ]
            offset = max(0, int(args.get("offset", 0)))
            page: list[dict] = []
            for descriptor in matches[offset : offset + _DISCOVERY_PAGE_SIZE]:
                item = {
                    "name": descriptor.name,
                    "description": descriptor.description,
                    "parameters": descriptor.input_schema,
                }
                if page and _wire_size([*page, item]) > _CATALOG_BYTE_BUDGET:
                    break
                page.append(item)
            next_offset = offset + len(page)
            return {
                "tools": page,
                "total": len(matches),
                "next_offset": next_offset if next_offset < len(matches) else None,
            }
        if name == "confirm_action":
            from jarvis.voice.echo_confirmation import classify_response

            approval_id = str(args.get("approval_id", ""))
            pending = self._pending.get(approval_id)
            affirmation = re.sub(r"[^\w\s]", "", self.user_text.casefold()).strip()
            if (
                pending is None
                or self.revision <= pending[3]
                or affirmation
                not in {
                    "yes",
                    "yes please",
                    "confirm",
                    "confirmed",
                    "do it",
                    "ja",  # i18n-allow: spoken confirmation vocabulary
                    "ja bitte",  # i18n-allow: spoken confirmation vocabulary
                    "mach das",  # i18n-allow: spoken confirmation vocabulary
                    "bestätigen",  # i18n-allow: spoken confirmation vocabulary
                    "sí",
                    "si",
                    "confirmo",
                }
                or len(self.user_text.split()) > 5
                or classify_response(self.user_text, language=self.language) != "confirm"
            ):
                return {"success": False, "error": "This action has not been explicitly approved."}
            trace, _, _, _ = self._pending.pop(approval_id)
            from jarvis.core.model_selection import ModelSelection, use_operation_model

            if self.backend_model:
                with use_operation_model(ModelSelection("openai", self.backend_model)):
                    result = await self.gateway.execute_confirmed(trace, self._request(trace))
            else:
                result = await self.gateway.execute_confirmed(trace, self._request(trace))
            return self._result(result)
        canonical = self._names.get(name, name)
        if name == "call_tool":
            canonical = str(args.get("name", ""))
            args = json.loads(args.get("arguments_json", "{}"))
            if not isinstance(args, dict):
                return {"success": False, "error": "Tool arguments must be an object."}
        descriptor = next((d for d in self.catalog() if d.name == canonical), None)
        if descriptor is None:
            return {
                "success": False,
                "error": "Tool is no longer available. Discover the current catalog.",
            }
        import jsonschema  # type: ignore[import-untyped]

        jsonschema.validate(args, descriptor.input_schema)
        trace = uuid4()
        from jarvis.core.model_selection import ModelSelection, use_operation_model

        if self.backend_model:
            with use_operation_model(ModelSelection("openai", self.backend_model)):
                result = await self.gateway.execute(canonical, args, self._request(trace))
        else:
            if canonical in {
                "computer_use",
                "computer-use",
                "dispatch_to_harness",
                "dispatch-to-harness",
            }:
                return {
                    "success": False,
                    "error": "Use screen_snapshot and desktop tools directly; "
                    "this voice model owns the action loop.",
                }
            result = await self.gateway.execute(canonical, args, self._request(trace))
        if result.error == VOICE_CONFIRM_SENTINEL:
            if operation_token.is_cancelled() or revision != self.revision:
                await self.gateway.cancel_pending(trace)
                return {"success": False, "status": "superseded"}
            if not self.accepting:
                await self.gateway.cancel_pending(trace)
                return {"success": False, "status": "voice_closed_before_confirmation"}
            approval_id = str(trace)
            self._pending[approval_id] = (trace, canonical, dict(args), self.revision)
            return {
                "success": False,
                "confirmation_required": True,
                "approval_id": approval_id,
                "tool": canonical,
                "arguments": args,
                "impact": result.output,
            }
        return self._result(result)

    def _request(self, trace: UUID) -> SupervisorToolRequest:
        return SupervisorToolRequest(
            trace_id=trace,
            origin="realtime",
            user_utterance=self.user_text,
            cancel_token=self.cancel_token,
            config_snapshot={
                "output_language": self.language,
                "voice_confirm": True,
                "live_backend_model": self.backend_model,
                "live_session_id": self.session_id,
                "task_revision": self.revision,
            },
        )

    @staticmethod
    def _result(result: Any) -> dict:
        from jarvis.core.redact import redact_secrets

        payload = {
            "success": result.success,
            "output": result.output,
            "error": result.error,
            "artifacts": list(getattr(result, "artifacts", ())),
            "verified": not (
                isinstance(result.output, dict) and result.output.get("verified") is False
            ),
        }
        images = take_images(payload)
        sanitized = json.loads(redact_secrets(json.dumps(payload, default=str)))
        sanitized["artifacts"].extend({**image, "type": "image"} for image in images)
        return sanitized

    async def close(self) -> None:
        self.accepting = False
        await self._cancel_confirmations()

    async def _cancel_confirmations(self) -> None:
        pending = tuple(self._pending.values())
        self._pending.clear()
        for trace, _, _, _ in pending:
            await self.gateway.cancel_pending(trace)
