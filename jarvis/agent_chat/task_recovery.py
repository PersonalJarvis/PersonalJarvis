"""Notice an agent giving up after a recoverable tool failure.

This reads execution receipts, not the language of the assistant's answer.
It does not decide whether an arbitrary goal is complete, authorize an action,
or run a second model as a judge. The CLI runner may use its hint for ONE
continuation of the same request, on the same seat and with the same grants.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

_TERMINAL = (
    "blocked_by_policy",
    "approval_required",
    "permission_rejected",
    "approval_denied",
    "blacklist match",
    "actionblocked",
    "the person declined",
    "user denied",
    "user declined",
    "permission denied",
    "access denied",
    "access is denied",
    "unauthorized",
    "forbidden",
    "auth_failed",
    "authentication failed",
    "invalid api key",
    "invalid token",
    "not logged in",
    "not authenticated",
    "quota",
    "credit balance",
)
_DISCOVERY = frozenset({"toolsearch", "list_tools", "tools-list", "search_tools"})


def blocks_automatic_recovery(text: str | None) -> bool:
    return any(marker in (text or "").lower() for marker in _TERMINAL)


@dataclass
class ToolRecovery:
    """A successful unrelated action cannot erase an unfinished part of a task."""

    calls: dict[str, tuple[str, str]] = field(default_factory=dict)
    unresolved: dict[tuple[str, str], str] = field(default_factory=dict)
    declined: bool = False
    blocked: bool = False

    def observe(self, event: dict[str, Any]) -> None:
        payload = event.get("payload") or {}
        call_id = str(payload.get("call_id") or "")
        if event.get("kind") == "tool_call":
            arguments = json.dumps(payload.get("input") or {}, sort_keys=True, default=str)
            self.calls[call_id] = (
                str(payload.get("name") or "tool"),
                hashlib.sha256(arguments.encode("utf-8")).hexdigest(),
            )
            return
        if event.get("kind") != "tool_result":
            return
        key = self.calls.pop(call_id, (str(payload.get("name") or "tool"), call_id))
        name = key[0]
        # Descriptions, provider bodies and retrieved instructions are never
        # copied into a recovery prompt. Only a registered tool name and a
        # fixed diagnostic code can cross that boundary.
        raw = payload.get("output")
        text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
        text = text[:8000]
        bare = name.rsplit("__", 1)[-1].lower()
        discovery = bare in _DISCOVERY
        missing = discovery and text.lstrip().startswith("No matching deferred tools found")
        if payload.get("is_error") or missing:
            if blocks_automatic_recovery(text):
                self.blocked = True
                self.unresolved.pop(key, None)
            else:
                self.unresolved[key] = "discovery_empty" if missing else "tool_failed"
        else:
            self.unresolved.pop(key, None)

    def hint(self) -> str | None:
        if self.declined or self.blocked or not self.unresolved:
            return None
        failures = ", ".join(
            f"{reason} for {re.sub(r'[^a-zA-Z0-9_.:/-]', '_', key[0])[:160]}"
            for key, reason in list(self.unresolved.items())[:3]
        )
        return (
            f"The execution receipts contain unresolved failures: {failures}. "
            "A successful unrelated action does not prove every requested outcome. "
            "Check ALL parts of the original request before ending. An alternative may "
            "already have completed the task: verify that evidence without repeating "
            "completed actions, and finish if all outcomes are satisfied. "
            "Inspect the actual available tools and the current state, then use a supported "
            "path if one exists. Do not repeat an identical failed lookup. A failed write "
            "may already have taken effect: read back before repeating it. A user denial, "
            "policy block, missing login or exhausted subscription is not permission to "
            "bypass the restriction, change credentials, or switch to a paid API. "
            "If a real blocker remains, explain the unfinished part and the specific "
            "prerequisite. This is the only automatic continuation of this turn."
        )
