"""Recognize explicit memory intent without treating quoted content as authority."""

from __future__ import annotations

import json
import re
from typing import Any

_REMEMBER = re.compile(
    r"^\s*(?:(?:please|bitte|por favor)[, ]+)?"  # i18n-allow: input vocabulary
    r"(?:(?:can|could) you (?:please )?)?"
    r"(?:remember|(?:merk|merke)\s+dir(?:\s+bitte)?|"  # i18n-allow
    r"(?:speichere|speicher)\s+(?:dir\s+)?(?:bitte\s+)?|recuerda(?:\s+que)?)"  # i18n-allow
    r"[\s,:]+(?P<content>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_REFERENCE_ONLY = re.compile(
    r"^(?:this|that|it|das|dies|dieses|es|esto|eso)"  # i18n-allow
    r"(?:\s+(?:please|bitte|por favor))?[.!]*$",  # i18n-allow
    re.IGNORECASE,
)


def user_evidence(event: dict[str, Any]) -> str:
    """Use what the person typed, not attached documents or transport scaffolding."""
    payload = event.get("payload") or {}
    if isinstance(payload.get("typed"), str):
        return payload["typed"]
    return re.split(r"\n\n\[(?:agent mentions|tools:)", str(payload.get("text") or ""), maxsplit=1)[
        0
    ]


def requested_memory(text: str) -> str | None:
    """None means no request; an empty string needs context instead of a guessed fact."""
    match = _REMEMBER.fullmatch(text)
    if not match:
        return None
    content = match["content"].strip()
    if _REFERENCE_ONLY.fullmatch(content):
        return ""
    return re.sub(r"^that\s+", "", content, flags=re.IGNORECASE)


def has_write_receipt(events: list[dict[str, Any]]) -> bool:
    names = {
        str(e.get("payload", {}).get("call_id")): e.get("payload", {}).get("name")
        for e in events
        if e.get("kind") == "tool_call"
    }
    for event in events:
        payload = event.get("payload") or {}
        if event.get("kind") != "tool_result" or payload.get("is_error"):
            continue
        name = names.get(str(payload.get("call_id")))
        value = payload.get("output")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                continue  # Prose promises are not durable-write receipts.
        if not isinstance(value, dict):
            continue
        if name == "society_wiki_note" and value.get("path") and "after" in value:
            return True
        if (
            name == "society_propose_change"
            and value.get("kind") == "rule"
            and value.get("applied")
        ):
            return True
    return False
