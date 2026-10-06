"""Keep an owned task alive across question cards and premature offers.

Question continuation is driven by real card state, never by parsing an answer
as consent. The prose guard is deliberately narrow: one correction for common
offer-only replies to an explicit execution request, without a second judge.
It does not infer completion of arbitrary tasks or authorize new actions.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from .questions import CANCELLED, MAX_ASKS_PER_TURN
from .task_recovery import ToolRecovery

_REQUEST = re.compile(
    r"^\s*(?:(?:hello|hi|hallo|hola)[,! ]+)?"  # i18n-allow
    r"(?:(?:please|bitte|por favor)\s+)?"  # i18n-allow
    r"(?:can you|could you|i want you to|create|write|build|make|fix|save|send|"
    r"kannst du|k[oö]nntest du|ich m[oö]chte|erstelle|schreib\w*|baue?|mach\w*|"  # i18n-allow
    r"reparier\w*|speicher\w*|sende|por favor|puedes|crea|escribe|haz|guarda)\b",  # i18n-allow
    re.I,
)
_ADVICE = re.compile(
    r"\b(?:ideas?|suggest|suggestions?|recommend|advice|explain|understand|why|how to|how do|"
    r"translate|quote|example|did you|have you|"
    r"poem|poetry|story|lyrics|fiction|draft|"
    r"ideen|vorschl[aä]g\w*|empfiehl\w*|erkl[aä]r\w*|"  # i18n-allow
    r"wie kann|wie geht|hast du|warum|wieso|"  # i18n-allow
    r"[uü]bersetz\w*|zitiere|beispiel|gedicht|geschichte|entwurf|plan|planning|"  # i18n-allow
    r"explica|traduce|ejemplo|sugerencias)\b",  # i18n-allow
    re.I,
)
_OFFER = re.compile(
    r"^(?:(?:sure|yes|okay|ok|gerne|klar|nat[uü]rlich|ja|s[ií])[.!,: —-]*\s*)?"  # i18n-allow
    r"(?:i (?:can|could|would|suggest)|if you (?:want|like)|would you like me|shall i|"
    r"ich (?:kann|k[oö]nnte|w[uü]rde|schlage)|wenn du (?:m[oö]chtest|willst)|"  # i18n-allow
    r"soll ich|m[oö]chtest du|vorschlag|puedo|podr[ií]a|si quieres|te propongo)\b",  # i18n-allow
    re.I,
)
_READS = frozenset(
    {
        "read",
        "ls",
        "glob",
        "grep",
        "toolsearch",
        "tools-list",
        "search_tools",
        "society_memory_recall",
        "society_conversation_recall",
        "society_routines",
        "society_ask_user",
    }
)


def offer_instead_of_action(request: str, response: str) -> bool:
    """Conservative guard; explanations, plans, examples and real results pass."""
    if not _REQUEST.search(request) or _ADVICE.search(request):
        return False
    answer = response.strip().lstrip("* ")
    if re.match(
        r"(?:I can confirm|Ich kann best[aä]tigen|Puedo confirmar)\b", answer, re.I  # i18n-allow: detect localized confirmation replies
    ):  # i18n-allow
        return False
    blocking_question = (
        len(answer) <= 400
        and "\n\n" not in answer
        and answer.endswith("?")
        and bool(
            re.match(
                r"(?:which|what|where|when|who|"
                r"welch\w*|was|wo|wann|wer|qu[eé]|cu[aá]l)\b",  # i18n-allow
                answer,
                re.I,
            )
        )
    )
    return len(answer) <= 2400 and (bool(_OFFER.search(answer)) or blocking_question)


class TurnCompletion:
    def __init__(
        self,
        service: Any,
        handle: Any,
        request: str,
        *,
        allow_correction: bool,
        context: str | None = None,
    ) -> None:
        self.service = service
        self.handle = handle
        self.request = request
        self.context = context or request
        self.allow_correction = allow_correction
        self.corrected = False
        self.last_text = ""
        self.finish_event: dict[str, Any] | None = None
        self.usage: dict[str, Any] = {}
        self.cost: float | None = None
        self.started = time.monotonic()
        self.answered: set[str] = set()
        self.calls: dict[str, tuple[str, dict[str, Any]]] = {}
        self.receipts = ToolRecovery()
        self.did_work = False

    async def emit(self, event: dict[str, Any]) -> None:
        payload = event.get("payload") or {}
        kind = event.get("kind")
        if kind == "turn_finished":
            self.finish_event = event
            for key, value in (payload.get("usage") or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.usage[key] = self.usage.get(key, 0) + value
                else:
                    self.usage[key] = value
            if isinstance(payload.get("cost_usd"), (int, float)):
                self.cost = (self.cost or 0) + payload["cost_usd"]
            return  # The service publishes one final event when work actually stops.
        self.receipts.observe(event)
        if kind == "assistant_text":
            self.last_text = str(payload.get("text") or "")
        elif kind == "tool_call":
            name = str(payload.get("name") or "").rsplit("__", 1)[-1].lower()
            self.calls[str(payload.get("call_id") or "")] = (name, payload.get("input") or {})
        elif kind == "tool_result":
            name, _ = self.calls.pop(str(payload.get("call_id") or ""), ("", {}))
            if name and name not in _READS and not payload.get("is_error"):
                self.did_work = True
        await self.handle.emit(event)

    async def ask(self, *args: Any) -> str:
        decision = await self.handle.request_approval(*args)
        if decision in {"deny", "cancel"}:
            self.receipts.declined = True
        return decision

    async def next_prompt(self) -> str | None:
        if self.handle.cancel.is_set():
            raise asyncio.CancelledError
        if not self.finish_event or self.finish_event["payload"].get("status") != "done":
            return None
        sid = self.handle.session.session_id
        cards = set(self.service.undelivered_questions(sid, self.handle.turn_id)) - self.answered
        if cards:
            rows = []
            for question_id in sorted(cards):
                if len(self.answered) >= MAX_ASKS_PER_TURN:
                    break
                specs = self.service.question_specs(sid, question_id)
                wait = asyncio.create_task(self.service.wait_questions(sid, question_id))
                stop = asyncio.create_task(self.handle.cancel.wait())
                try:
                    await asyncio.wait({wait, stop}, return_when=asyncio.FIRST_COMPLETED)
                    if self.handle.cancel.is_set():
                        raise asyncio.CancelledError
                    answers = await wait
                finally:
                    for task in (wait, stop):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(wait, stop, return_exceptions=True)
                for spec, answer in zip(specs, answers, strict=True):
                    if answer.source == CANCELLED:
                        raise asyncio.CancelledError
                    rows.append({"question": spec.question, **answer.to_payload()})
                self.answered.add(question_id)
            if rows:
                return (
                    "Continue the original task now using these question-card results. "
                    "They are user choices or explicitly labelled defaults, not extra permissions. "
                    "Do not ask the user to start you again or repeat answered questions. "
                    "Preserve completed work and verify the requested result.\n"
                    + json.dumps(rows, ensure_ascii=False)
                    + "\nOriginal request:\n"
                    + self.context
                )
        if (
            self.allow_correction
            and not self.corrected
            and not self.did_work
            and not self.receipts.declined
            and not self.receipts.blocked
            and not self.service.pending_approvals(sid)
            and offer_instead_of_action(self.request, self.last_text)
        ):
            self.corrected = True
            return (
                "The previous answer offered to do the requested work but did not execute it. "
                "Carry out the original request now within the existing permissions. "
                "Decide optional implementation details yourself. If essential information "
                "really is missing, use society_ask_user and continue after its answer. "
                "Do not end with another offer or plain-text confirmation question. "
                "Honor any answers already received, including a decision to stop or change scope. "
                "Do not repeat completed actions or invent success.\nOriginal request:\n"
                + self.context
            )
        return None

    async def publish(self) -> None:
        if self.finish_event is not None:
            payload = {
                **self.finish_event["payload"],
                "usage": self.usage,
                "duration_ms": int((time.monotonic() - self.started) * 1000),
            }
            if self.cost is not None:
                payload["cost_usd"] = self.cost
            await self.handle.emit({**self.finish_event, "payload": payload})
