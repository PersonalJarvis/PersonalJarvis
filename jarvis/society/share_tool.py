"""``society_share_template`` — an agent prepares the public version of itself.

The Share sheet on an agent offers "Let <agent> prepare it": that sends the
agent a chat message asking it to write the shareable version of itself — a
summary for the store card and standing instructions with everything personal
taken out. This tool is how the agent hands that back. It writes only the
local share draft (``agent_template.save_overlay``); nothing leaves the
machine until the person presses Publish in the sheet, so the tool is
``safe``.

The agent is never the privacy boundary: whatever it writes is scrubbed by
the same deterministic pass as the agent's own fields, and the registry
checks it again.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final

from jarvis.core.protocols import ToolResult

from .agent_template import MAX_SUMMARY_CHARS, TemplateError, build_draft, save_overlay
from .failure_reasons import FailureReason, retry_action
from .roster import AgentState

log = logging.getLogger(__name__)

__all__ = ["SHARE_TOOL_NAME", "ShareTemplateTool"]

SHARE_TOOL_NAME: Final[str] = "society_share_template"
_MAX_INSTRUCTIONS_CHARS: Final[int] = 20_000


def _failure(reason: FailureReason, detail: str) -> ToolResult:
    return ToolResult(
        success=False,
        output={"reason": str(reason), "retry": str(retry_action(reason))},
        error=f"{reason}: {detail}",
    )


class ShareTemplateTool:
    """Save the public version of the calling agent as its share draft."""

    name: str = SHARE_TOOL_NAME
    risk_tier: str = "safe"
    description: str = (
        "Prepare the shareable template of YOURSELF for the Personal Jarvis marketplace. "
        "Call it when the user asks you to create, prepare or improve your template. "
        "Write 'summary': one or two plain sentences a stranger reads on the store card "
        "(what you do, for whom). Write 'instructions': your standing instructions "
        "rewritten for anyone — keep the working method, drop everything personal (names, "
        "e-mail addresses, companies, folders, accounts, private projects) and every "
        "credential; use neutral words like 'the user' or '[your company]'. Optional: "
        "'title' (your job line), 'categories' (up to 5 short tags). Call it without "
        "arguments to read your current draft. It saves a local draft only — the user "
        "reviews it and publishes it themselves. Afterwards tell the user in one sentence "
        "that the draft is ready in the Share window."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": f"Store-card summary, at most {MAX_SUMMARY_CHARS} characters.",
            },
            "instructions": {
                "type": "string",
                "description": "Standing instructions for anyone, with nothing personal in them.",
            },
            "title": {"type": "string", "description": "Your job line, short."},
            "categories": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Up to 5 short lowercase tags, e.g. ['email', 'productivity'].",
            },
        },
        "required": [],
    }

    def __init__(self, runtime: Any, agent_id: str) -> None:
        self._runtime = runtime
        self._agent_id = agent_id

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        rt = self._runtime
        agent = await rt.roster.get(self._agent_id)
        if agent is None or agent.state is not AgentState.ACTIVE:
            return _failure(FailureReason.BLOCKED_BY_POLICY, "caller is not an active agent")
        changes: dict[str, Any] = {}
        for key, limit in (
            ("summary", MAX_SUMMARY_CHARS),
            ("instructions", _MAX_INSTRUCTIONS_CHARS),
            ("title", 120),
        ):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                changes[key] = value.strip()[:limit]
        categories = args.get("categories")
        if isinstance(categories, list):
            changes["categories"] = [
                str(c).strip().lower()[:32] for c in categories if str(c).strip()
            ][:5]
        try:
            if changes:
                changes["polished_by"] = agent.name
                overlay = save_overlay(rt.data_dir, agent.agent_id, changes)
            else:
                from .agent_template import load_overlay

                overlay = load_overlay(rt.data_dir, agent.agent_id)
            draft = build_draft(agent, overlay)
        except TemplateError as exc:
            return _failure(FailureReason.BLOCKED_BY_POLICY, str(exc))
        except OSError as exc:
            log.warning("society: share draft for %s not saved", agent.agent_id, exc_info=True)
            return _failure(FailureReason.INTERNAL_ERROR, f"the draft could not be saved: {exc}")
        output = {
            "saved": bool(changes),
            "summary": draft.listing["description"],
            "removed": [f.to_dict() for f in draft.findings],
            "template": json.dumps(draft.template, ensure_ascii=False, indent=2),
            "note": "Draft saved locally. The user publishes it from the Share window.",
        }
        return ToolResult(success=True, output=output)
