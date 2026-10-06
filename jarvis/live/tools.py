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
from jarvis.speech.hangup import (
    HangupConfirmation,
    confirms_hangup,
    hangup_confirmation_question,
    user_asked_to_hang_up,
)

log = logging.getLogger(__name__)

# A session.started event includes its declarations. Keep room for prompts and
# history inside the 64 KiB message limit of smaller WebRTC clients.
_CATALOG_BYTE_BUDGET = 24_000
_BUILTIN_TOOLS = frozenset({"discover_tools", "call_tool", "confirm_action", "end_call"})
# An approval is a short answer made only of go-ahead words: "Ja, send ihn"
# approves (refused live 2026-10-01 by an exact-phrase list), while "yes,
# change the target" or "ja, aber warte" carry something else and do not.
_GO_AHEAD_WORDS = frozenset(
    {
        # English
        "yes", "yeah", "yep", "sure", "ok", "okay", "please", "confirm", "confirmed",
        "do", "it", "go", "ahead", "send", "run", "start", "that", "now",
        # German  # i18n-allow: spoken confirmation vocabulary
        "ja", "jo", "jep", "klar", "gerne", "genau", "bitte", "bestätigen",  # i18n-allow
        "bestätigt", "mach", "machs", "das", "es", "ihn", "sie",  # i18n-allow
        "schick", "schicke", "schicks",  # i18n-allow
        "sende", "senden", "los", "ab", "raus", "jetzt", "starte", "starten",  # i18n-allow
        # Spanish
        "sí", "si", "claro", "vale", "dale", "confirmo", "hazlo", "envía", "envialo",
        "envíalo", "adelante",
    }
)  # fmt: skip
_APPROVAL_LOCALES = ("de", "en", "es")
_RECENT_SEGMENTS = 4
_REQUEST_TEXT_CHARS = 1200
# Declared before every other tool so the size budget never drops them.
_PRIORITY_TOOLS = frozenset({"workspace-orchestrate", "find-app-action", "run-app-action"})
# Declared under its canonical name in every mode (see ``declarations``).
_DIRECT_TOOLS = frozenset({"computer", "take_appshot"})
_APPROVAL_NEXT_STEP = (
    "Ask the user to approve this action. After an explicit yes, call confirm_action "
    "directly (not through call_tool) with this approval_id. A yes is never a hang-up."
)


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
        model_selection: Any = None,
    ) -> None:
        self.gateway = gateway
        self.ledger = ledger
        self.session_id = session_id
        self.language = language
        self.backend_model = backend_model
        # Recent user caption segments, newest last (see ``user_text``).
        self._recent_user: list[str] = []
        self._hangup_confirmation = HangupConfirmation()
        self.ask_hangup: Any = None
        self.model_selection = model_selection
        self.user_text = ""
        self.revision = 0
        self.cancel_token = CancelToken()
        self._lock = asyncio.Lock()
        self._pending: dict[str, tuple[UUID, str, dict, int]] = {}
        self._names: dict[str, str] = {}
        self._defer_catalog = False
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
        await self._cancel_confirmations("user_cancelled")

    def declarations(self, *, defer_catalog: bool = False) -> list[dict]:
        self._defer_catalog = defer_catalog
        definitions = [
            function(
                "end_call",
                "Request voice hang-up. Jarvis asks for confirmation first; call again only "
                "after a separate explicit yes to that question. Never end a call for task "
                "completion or an action approval. Running agents keep their tasks.",
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
        # The screen is operated in many short rounds (ADR-0039): declare the
        # computer tool under its own name instead of behind discover/call_tool.
        # Keep capture available even with a deferred catalog. Otherwise a live
        # model may reuse an old image or choose computer instead of the appshot
        # path that owns privacy filtering, the shutter effect and the receipt.
        for descriptor in self.catalog():
            if descriptor.name not in _DIRECT_TOOLS:
                continue
            definitions.append(
                {
                    "type": "function",
                    "name": descriptor.name,
                    "description": descriptor.description,
                    "parameters": descriptor.input_schema,
                }
            )
        if defer_catalog:
            definitions[1]["description"] = (
                "Find tools by intent using a few English keywords, or an exact canonical name. "
                "Returns relevant complete input schemas. Reuse schemas already read; do not "
                "inventory unrelated tools. Empty query browses all tools. Pass next_offset as "
                "offset with the same query to read another page only if needed."
            )
            # A names-only index lets the same model choose an exact capability
            # without asking lexical search to infer the user's intent.
            definitions[1]["description"] += " Available tool names: " + ", ".join(
                sorted(descriptor.name for descriptor in self.catalog())
            )
            definitions[1]["parameters"]["properties"]["offset"] = {"type": "integer", "minimum": 0}
            return definitions
        # Count alone is insufficient: an imported tool may carry a large schema.
        used_bytes = _wire_size(definitions)
        # The tools that steer the app itself go first: sorted by name alone,
        # workspace-orchestrate fell past the budget and voice could not open
        # or brief a coding agent (live 2026-10-01).
        ordered = sorted(self.catalog(), key=lambda d: (d.name not in _PRIORITY_TOOLS, d.name))
        for descriptor in ordered:
            if descriptor.name in _DIRECT_TOOLS:
                continue
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
            if (self._hangup_confirmation.pending_turn is not None
                    and confirms_hangup(self.user_text) and name != "end_call"):
                return {
                    "success": False, "executed": False,
                    "error": "This yes answers the hang-up question, not an action approval.",
                }
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
        if revision != self.revision and not self._reads_only(name, args):
            # Only an action can be overtaken by what the user said meanwhile;
            # a search or a read answers the same either way (11 reads were
            # discarded live and re-bought as paid rounds).
            return {
                "success": False,
                "executed": False,
                "status": "superseded",
                "error": "The request changed before execution. Re-evaluate the latest user input.",
            }
        if ":" in name:
            prefix, suffix = name.split(":", 1)
            if prefix.isidentifier() and (suffix in self._names or suffix in _BUILTIN_TOOLS):
                name = suffix
        if name == "call_tool" and args.get("name") in _BUILTIN_TOOLS - {"call_tool"}:
            # Models wrap the session built-ins too. They are not catalog tools,
            # so the catalog lookup below answered "no longer available".
            try:
                inner = json.loads(args.get("arguments_json") or "{}")
            except ValueError:  # bad JSON is answered by the object check just below
                inner = None
            if not isinstance(inner, dict):
                return {"success": False, "error": "Tool arguments must be an object."}
            name, args = str(args["name"]), inner
        if name == "end_call":
            decision = self._hangup_confirmation.observe(self.user_text, revision)
            if decision in {"request", "waiting"}:
                question = hangup_confirmation_question(self.language)
                if decision == "request" and callable(self.ask_hangup):
                    await self.ask_hangup(question)
                    self._hangup_confirmation.arm(revision)
                return {
                    "success": False, "executed": False,
                    "confirmation_required": True,
                    "question": question,
                    "next_step": "Jarvis asks this question. Wait for a separate explicit yes "
                    "and then call end_call again. Never confirm another action with this yes.",
                }
            if decision != "confirmed":
                # Live 2026-10-01: after "Ja" to a pending approval the model
                # called end_call instead of confirm_action and the call dropped.
                refusal: dict = {
                    "success": False,
                    "executed": False,
                    "error": "The user did not ask to hang up. Stay on the call.",
                }
                if self._pending:
                    refusal["approval_ids"] = list(self._pending)
                    refusal["next_step"] = (
                        "If the user approved, call confirm_action with the approval_id."
                    )
                log.info("Live end_call refused: the user's turn holds no hang-up request")
                return refusal
            self.end_requested = True
            return {"success": True, "status": "closing_voice"}
        if name == "discover_tools":
            # One ranked search for every provider. The native path's old rule
            # (every query word in one tool) found nothing for "die Jarvis
            # Agenten" and Jarvis told the user they did not exist (2026-09-20).
            from jarvis.live.discovery import discover

            try:
                offset = max(0, int(args.get("offset") or 0))
            except (TypeError, ValueError):  # a malformed offset starts the listing from the top
                offset = 0
            return discover(self.catalog(), str(args.get("query", "")), offset)
        if name == "confirm_action":
            approval_id = str(args.get("approval_id", ""))
            if approval_id not in self._pending and len(self._pending) == 1:
                # One open question, so the yes can only mean it; a voice
                # model that mistypes the 36-character id must not void it.
                approval_id = next(iter(self._pending))
            pending = self._pending.get(approval_id)
            if pending is None or self.revision <= pending[3] or not self._user_approved():
                # Say why, or the model asks the same question again and again.
                return {
                    "success": False,
                    "executed": False,
                    "error": "This action has not been explicitly approved.",
                    "reason": "no pending approval"
                    if pending is None
                    else "the user's latest words are not a plain yes",
                    "approval_ids": list(self._pending),
                    "next_step": "Ask for a plain yes or no, then call confirm_action again.",
                }
            trace, _, _, _ = self._pending.pop(approval_id)
            from jarvis.core.model_selection import ModelSelection, use_operation_model

            if self.backend_model:
                with use_operation_model(
                    self.model_selection or ModelSelection("openai", self.backend_model)
                ):
                    result = await self.gateway.execute_confirmed(trace, self._request(trace))
            else:
                result = await self.gateway.execute_confirmed(trace, self._request(trace))
            return self._result(result)
        canonical = self._names.get(name, name)
        if name == "call_tool":
            canonical = str(args.get("name", ""))
            try:
                args = json.loads(args.get("arguments_json") or "{}")
            except ValueError:  # bad JSON is answered by the object check just below
                args = None
            if not isinstance(args, dict):
                return {
                    "success": False,
                    "executed": False,
                    "retryable": True,
                    "error": "arguments_json must be a JSON object. Correct it and call again.",
                }
        descriptor = next((d for d in self.catalog() if d.name == canonical), None)
        if descriptor is None:
            return {
                "success": False,
                "executed": False,
                "error": "Tool is no longer available. Discover the current catalog.",
            }
        import jsonschema  # type: ignore[import-untyped]

        try:
            jsonschema.validate(args, descriptor.input_schema)
        except jsonschema.ValidationError as exc:
            # Nothing ran yet, so the model may fix the argument and retry. A
            # bare "execution failed" left it guessing four times in a row.
            field = "/".join(str(part) for part in exc.absolute_path) or "arguments"
            return {
                "success": False,
                "executed": False,
                "retryable": True,
                "error": f"Invalid {field}: {exc.message[:300]}. Correct it and call again.",
            }
        trace = uuid4()
        from jarvis.core.model_selection import ModelSelection, use_operation_model

        if self.backend_model:
            with use_operation_model(
                self.model_selection or ModelSelection("openai", self.backend_model)
            ):
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
                await self.gateway.cancel_pending(trace, reason="superseded")
                return {"success": False, "status": "superseded"}
            if not self.accepting:
                await self.gateway.cancel_pending(trace, reason="voice_session_closed")
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
                "next_step": _APPROVAL_NEXT_STEP,
            }
        return self._result(result)

    @property
    def user_text(self) -> str:
        """The user's latest caption segment: what answers a question just asked."""
        return self._recent_user[-1] if self._recent_user else ""

    @user_text.setter
    def user_text(self, text: str) -> None:
        text = text or ""
        if (self._hangup_confirmation.pending_turn is not None
                and text.strip() and not confirms_hangup(text)
                and not user_asked_to_hang_up(text)):
            self._hangup_confirmation.reset()
        last = self._recent_user[-1] if self._recent_user else None
        if last is not None and (text.startswith(last) or last.startswith(text)):
            self._recent_user[-1] = text  # the same segment, transcribed further
        elif text.strip():
            self._recent_user = [*self._recent_user[-(_RECENT_SEGMENTS - 1) :], text]
        elif last is None:
            self._recent_user = [text]

    @property
    def request_text(self) -> str:
        """The user's last few segments together: what a tool was asked to do.

        The latest segment alone is often only the answer to Jarvis's question
        ("Ja, los"); a tool that checks its task against the user's words then
        sees nothing of the order and, in spawn_worker's bleed guard, even
        replaced the task with "Ja, los".
        """
        return " ".join(part.strip() for part in self._recent_user if part.strip())[
            -_REQUEST_TEXT_CHARS:
        ]

    def _reads_only(self, name: str, args: dict) -> bool:
        """True for a call that only reads: tool search or a safe-tier tool."""
        if name == "discover_tools":
            return True
        if name == "call_tool":
            name = str(args.get("name", ""))
        canonical = self._names.get(name, name)
        return any(d.name == canonical and d.risk_tier == "safe" for d in self.catalog())

    def _user_approved(self) -> bool:
        """True when the latest user text is a short, unvetoed yes in any locale.

        The session language cannot pick the locale ("auto" resolves to English
        before anyone speaks), so every supported locale vets the answer: one
        must read it as a confirmation and none as a veto. Every word must be a
        go-ahead word, so an answer that adds a condition or a new instruction
        is not an approval of the old action.
        """
        from jarvis.voice.echo_confirmation import classify_response

        text = self.user_text
        words = re.sub(r"[^\w\s]", " ", text.casefold()).split()
        if not words or any(word not in _GO_AHEAD_WORDS for word in words):
            return False
        verdicts = {classify_response(text, language=locale) for locale in _APPROVAL_LOCALES}
        return "confirm" in verdicts and "veto" not in verdicts

    def _request(self, trace: UUID) -> SupervisorToolRequest:
        return SupervisorToolRequest(
            trace_id=trace,
            origin="realtime",
            user_utterance=self.request_text,
            cancel_token=self.cancel_token,
            config_snapshot={
                "output_language": self.language,
                "voice_confirm": True,
                "live_backend_model": self.backend_model,
                "live_session_id": self.session_id,
                "task_revision": self.revision,
                "workspace_user_utterance": self.user_text,
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
        from jarvis.core.image_references import get_store

        get_store().clear_scope("live:" + self.session_id)
        self.accepting = False
        await self._cancel_confirmations("voice_session_closed")

    async def _cancel_confirmations(self, reason: str) -> None:
        pending = tuple(self._pending.values())
        self._pending.clear()
        for trace, _, _, _ in pending:
            await self.gateway.cancel_pending(trace, reason=reason)
