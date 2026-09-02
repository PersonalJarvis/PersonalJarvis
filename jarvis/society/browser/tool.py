"""``society_browser`` — the agent's browser hand.

One call = one browser-use run in the agent's own profile (or its attached
Chrome), capped in steps and time, one at a time per agent. The run is
observable: a ``DIGEST`` per step batch and a final ``DIGEST`` with the cost
land on the board, so the chat, the ledger and the world see it. Tasks whose
wording sends, buys, deletes or publishes escalate to ``ask``; the agent's
approval rules and ceiling apply through ``approvals.decide`` on the
capability ``core:browser``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

from jarvis.core.protocols import ToolResult

from ..events import MsgType, SocietyEnvelope
from ..failure_reasons import FailureReason, retry_action
from ..roster import AgentState
from .llm import LLMUnavailable, llm_spec_for
from .session import DEFAULT_MAX_STEPS, MAX_STEPS_CEILING, BrowserJobs, BrowserUnavailable

log = logging.getLogger(__name__)

__all__ = ["BROWSER_TOOL_NAME", "BrowserTool", "task_needs_approval"]

BROWSER_TOOL_NAME: Final[str] = "society_browser"
CAPABILITY_ID: Final[str] = "core:browser"

_ASK_VERBS: Final[re.Pattern[str]] = re.compile(
    r"\b(send|submit|post|publish|tweet|reply|buy|purchase|order|pay|checkout|delete|remove|"
    r"cancel|unsubscribe|transfer|book|sign up|register|accept|agree|"
    r"senden|abschicken|posten|veröffentlichen|kaufen|bestellen|bezahlen|löschen|"  # i18n-allow
    r"kündigen|überweisen|buchen|registrieren|zustimmen)\b",  # i18n-allow: verb list
    re.I,
)


def task_needs_approval(task: str) -> bool:
    """Whether the task text asks for an action a person should confirm."""
    return bool(_ASK_VERBS.search(task or ""))


def _failure(reason: FailureReason, detail: str, **extra: Any) -> ToolResult:
    return ToolResult(
        success=False,
        output={"reason": str(reason), "retry": str(retry_action(reason)), **extra},
        error=f"{reason}: {detail}",
    )


class BrowserTool:
    name: str = BROWSER_TOOL_NAME
    risk_tier: str = "monitor"
    description: str = (
        "Use YOUR browser to do something on the web: read a page, search, fill a form, "
        "collect information from a site, work inside a web app the user is logged into. "
        "Give one clear task (what to achieve, where, what to return) and optionally the "
        "URL to start at. The run is capped (max_steps); it returns the final result, the "
        "pages visited and any errors. Tasks that send, buy, delete or publish ask the user "
        "first. Prefer a connected plugin or CLI when one exists for the service."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "What to do and what to bring back."},
            "url": {"type": "string", "description": "Where to start (optional)."},
            "max_steps": {
                "type": "integer",
                "description": f"Step cap (default {DEFAULT_MAX_STEPS}, max {MAX_STEPS_CEILING}).",
            },
        },
        "required": ["task"],
    }
    is_action_tool: bool = True

    def __init__(self, runtime: Any, agent_id: str, jobs: BrowserJobs) -> None:
        self._runtime = runtime
        self._agent_id = agent_id
        self._jobs = jobs

    def risk_tier_for_args(self, args: dict[str, Any]) -> str | None:
        return "ask" if task_needs_approval(str(args.get("task") or "")) else None

    def describe_args(self, args: dict[str, Any]) -> dict[str, str] | None:
        task = str(args.get("task") or "").strip()
        if not task:
            return None
        return {"task": task[:300], "url": str(args.get("url") or "")[:200]}

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        from ..approvals import Verdict, decide

        rt = self._runtime
        caller = await rt.roster.get(self._agent_id)
        if caller is None or caller.state is not AgentState.ACTIVE:
            return _failure(FailureReason.BLOCKED_BY_POLICY, "caller is not an active agent")
        if await rt.store.kill_switch():
            return _failure(FailureReason.KILL_SWITCH, "the society is halted")
        task = str(args.get("task") or "").strip()
        if not task:
            return _failure(FailureReason.BLOCKED_BY_POLICY, "task is required")
        if not self._jobs.is_installed():
            return _failure(
                FailureReason.BLOCKED_BY_POLICY,
                "the browser is not set up yet; the user can install it from the agent card",
                install_action="POST /api/society/browser/install",
            )
        tier = "ask" if task_needs_approval(task) else "monitor"
        verb = "act" if tier == "ask" else "read"
        verdict = decide(caller, CAPABILITY_ID, tier, verb=verb)
        if verdict is Verdict.BLOCK:
            return _failure(FailureReason.BLOCKED_BY_POLICY, "browser use is blocked for you")
        trace_id = f"browser:{caller.agent_id}:{getattr(ctx, 'trace_id', '')}"[:120]
        if verdict is Verdict.QUEUE:
            item = await rt.approvals.enqueue(
                agent_id=caller.agent_id,
                trace_id=trace_id,
                capability=CAPABILITY_ID,
                action={"task": task[:2000], "url": str(args.get("url") or "")[:500]},
                summary=f"Browser: {task[:200]}",
            )
            return _failure(
                FailureReason.APPROVAL_REQUIRED,
                "the user has to allow this browser task",
                approval_id=item.id,
            )
        try:
            spec = llm_spec_for(caller.provider or _default_provider(rt), caller.model)
        except LLMUnavailable as exc:
            return _failure(exc.reason, str(exc))
        steps_seen: list[dict[str, Any]] = []

        async def _on_step(event: dict[str, Any]) -> None:
            if event.get("kind") != "step":
                return
            steps_seen.append(event)
            if len(steps_seen) % 5 == 0:
                await rt.store.append_and_publish(
                    SocietyEnvelope(
                        msg_type=MsgType.DIGEST,
                        from_agent=caller.agent_id,
                        trace_id=trace_id,
                        payload={
                            "kind": "browser_steps",
                            "steps": len(steps_seen),
                            "url": str(event.get("url") or ""),
                            "text": f"browser step {len(steps_seen)}",
                        },
                    )
                )

        try:
            outcome = await self._jobs.run(
                caller,
                task=task,
                llm=spec.to_request(),
                max_steps=int(args.get("max_steps") or DEFAULT_MAX_STEPS),
                start_url=str(args.get("url") or "").strip(),
                on_step=_on_step,
            )
        except BrowserUnavailable as exc:
            return _failure(exc.reason, str(exc))
        await rt.store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.DIGEST,
                from_agent=caller.agent_id,
                trace_id=trace_id,
                cost_usd=outcome.cost_usd or 0.0,
                payload={
                    "kind": "browser_done",
                    "ok": outcome.ok,
                    "steps": outcome.steps,
                    "urls": outcome.urls[:10],
                    "text": (outcome.final_result or outcome.error or "browser run ended")[:500],
                },
            )
        )
        body = outcome.to_dict()
        body["provider"] = spec.provider
        body["model"] = spec.model
        if not outcome.ok:
            return ToolResult(
                success=False, output=body, error=outcome.error or "browser run failed"
            )
        return ToolResult(success=True, output=body)


def _default_provider(runtime: Any) -> str:
    """The Agents tier's provider when the roster row has none."""
    try:
        from jarvis.local_models.assistant_session import agents_tier

        cfg = runtime._get_cfg()  # noqa: SLF001 — the tool is part of the runtime's surface
        tier = agents_tier(cfg)
        return tier.provider if tier.ready else ""
    except Exception:  # noqa: BLE001 — no tier means the typed "no browser model" answer
        return ""
