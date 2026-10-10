"""Recognize explicit memory intent without treating quoted content as authority."""

from __future__ import annotations

import json
import re
from typing import Any

_REMEMBER = re.compile(
    r"^\s*(?:(?:please|bitte|por favor)[, ]+)?"  # i18n-allow: input vocabulary
    r"(?:(?:can|could) you (?:please )?)?"
    r"(?:remember|(?:merk|merke)\s+dir(?:\s+bitte)?|"  # i18n-allow
    r"(?:speichere|speicher)\s+(?:dir\s+)?(?:bitte\s+)?|recuerda(?:\s+que)?|"  # i18n-allow
    r"lembra-te(?:\s+de)?(?:\s+que)?|memoriza(?:\s+que)?)"  # i18n-allow: input vocabulary
    r"[\s,:]+(?P<content>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_REFERENCE_ONLY = re.compile(
    r"^(?:this|that|it|das|dies|dieses|es|esto|eso|isto|isso|disto|disso)"  # i18n-allow
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


#: "Remember that not" declines; "remember not to call late" is a request.
_NEGATED = re.compile(
    r"^(?:(?:das|dies|es|this|that|it|eso|isso|isto)\s+"  # i18n-allow
    r"(?:nicht|not|no|n[ãa]o)|nicht)\b",  # i18n-allow
    re.IGNORECASE,
)
#: One spoken or typed sentence: the request may sit anywhere in a longer turn.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _request_in(sentence: str) -> str | None:
    match = _REMEMBER.fullmatch(sentence)
    if not match:
        return None
    content = match["content"].strip()
    if _REFERENCE_ONLY.fullmatch(content):
        return ""
    if _NEGATED.match(content):
        return None
    return re.sub(r"^that\s+", "", content, flags=re.IGNORECASE)


def requested_memory(text: str) -> str | None:
    """None means no request; an empty string needs context instead of a guessed fact.

    The request may open the turn ("Remember that I ...") or follow what it
    points at ("I want short reports. Remember that."): a bare "remember
    that" then takes the sentence just before it in the same turn.
    """
    whole = _request_in(text)
    if whole:
        return whole
    sentences = [part.strip() for part in _SENTENCE_END.split(text or "") if part.strip()]
    for index, sentence in enumerate(sentences):
        content = _request_in(sentence)
        if content is None:
            continue
        if content == "" and index > 0:
            return sentences[index - 1].rstrip(" .!")
        return content
    return whole


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
