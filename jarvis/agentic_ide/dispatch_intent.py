"""Bind coding dispatch to the user's request, never to a model-written brief."""

from __future__ import annotations

import hashlib
import json
import re

_NEW_PANE = re.compile(
    r"\b(?:new|fresh|neu(?:e[nsrm]?|en)?|weiteren?)\s+"
    r"(?P<cli>(?:[\w-]+\s+){0,4}?)"
    r"(?:agents?|agenten|sessions?|terminals?|panes?)\b",
    re.IGNORECASE,
)
_NEGATED = re.compile(
    r"\b(?:no|not|never|don't|do not|kein\w*|nicht)\b[^.!?;]{0,60}$",  # i18n-allow
    re.IGNORECASE,
)


def dispatch_context(utterance: str, config: dict, trace_id: str) -> dict:
    """Keep retries in one request scope; earlier voice requests are not intent.

    Live supplies the latest caption separately from its rolling context. Other
    callers already provide the current user turn as their execution context.
    This guard deliberately never examines the generated task prompt.
    """
    current = str(config.get("workspace_user_utterance", utterance) or "")
    from .intent import canonical_agent, detect_spawn

    match = _NEW_PANE.search(current)
    cli = match.group("cli").strip() if match else ""
    vehicle = not cli or cli.casefold() in {"coding", "cli"} or canonical_agent(cli) is not None
    requires_new = bool(
        (
            match
            and vehicle
            and not _NEGATED.search(current[max(0, match.start() - 80) : match.start()])
        )
        or detect_spawn(current, names=[]) is not None
    )
    conversation = str(config.get("live_session_id") or config.get("chat_session_id") or "")
    # A host-owned turn ID takes precedence over wording and model call IDs.
    turn = config.get("task_revision", config.get("turn_id", trace_id))
    seed = json.dumps([conversation, turn], ensure_ascii=False)
    return {
        "_dispatch_scope": hashlib.sha256(seed.encode()).hexdigest(),
        "_requires_new": requires_new,
    }
