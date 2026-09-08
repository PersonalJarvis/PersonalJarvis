"""Router-tier society tools: ``delegate-to-agent`` and ``society-status``.

The voice front door of the agent society (MASTERPLAN §3.2, agent-definition
§4.1). Both tools answer inside the 5-second voice budget:

* ``delegate-to-agent`` appends ONE ``ASSIGN`` envelope to the society board
  on behalf of Jarvis (the lead) and returns a spoken acknowledgement at
  once. The scheduler — trusted Python — decides whether and how the target
  starts working; the tool never spawns anything itself and never waits for
  completion. Completions re-enter voice through the existing announcement
  path. Risk ``monitor``: a dispatch like ``spawn-worker``, never in a worker
  set (AP-5/AP-14).
* ``society-status`` reads the roster and the last events — no model call,
  no spend — and answers "what is Scout doing?" (risk ``safe``).

Both reach the runtime through a lazy resolver (AD-OC1): the society is
built on first use by the server, after the brain exists.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Final

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

RuntimeResolver = Callable[[], Any | None]

_ACK: Final[dict[str, str]] = {
    "de": "{name} ist dran, ich sage Bescheid.",  # i18n-allow: spoken ack
    "en": "{name} is on it, I will let you know.",
    "es": "{name} se encarga, te aviso.",
}
_QUEUED: Final[dict[str, str]] = {
    "de": "Aufgabe für {name} erfasst; Start noch nicht bestätigt.",  # i18n-allow: spoken ack
    "en": "The task for {name} is recorded; its start is not confirmed yet.",
    "es": "La tarea para {name} está registrada; su inicio aún no está confirmado.",
}
_NO_AGENT: Final[dict[str, str]] = {
    "de": "Ich kenne keinen Agenten namens {target}.",  # i18n-allow: spoken reply
    "en": "I do not know an agent called {target}.",
    "es": "No conozco ningún agente llamado {target}.",
}
_NO_FIT: Final[dict[str, str]] = {
    "de": "Keiner deiner Agenten passt zu dieser Aufgabe.",  # i18n-allow: spoken reply
    "en": "None of your agents fits this task.",
    "es": "Ninguno de tus agentes encaja con esta tarea.",
}
_REFUSED: Final[dict[str, str]] = {
    "de": "{name} kann das gerade nicht übernehmen: {reason}.",  # i18n-allow: spoken reply
    "en": "{name} cannot take that right now: {reason}.",
    "es": "{name} no puede encargarse ahora: {reason}.",
}
_NOT_READY: Final[dict[str, str]] = {
    "de": "Die Agenten sind noch nicht bereit.",  # i18n-allow: spoken reply
    "en": "The agents are not ready yet.",
    "es": "Los agentes aún no están listos.",
}
_STATUS_IDLE: Final[dict[str, str]] = {
    "de": "{name} hat gerade nichts zu tun.",  # i18n-allow: spoken reply
    "en": "{name} has nothing to do right now.",
    "es": "{name} no tiene nada que hacer ahora.",
}
_STATUS_WORKING: Final[dict[str, str]] = {
    "de": "{name} arbeitet gerade: {text}",  # i18n-allow: spoken reply
    "en": "{name} is working on: {text}",
    "es": "{name} está trabajando en: {text}",
}
_STATUS_LAST: Final[dict[str, str]] = {
    "de": "{name}: zuletzt {kind}, {text}",  # i18n-allow: spoken reply
    "en": "{name}: last {kind}, {text}",
    "es": "{name}: último {kind}, {text}",
}
_ROSTER: Final[dict[str, str]] = {
    "de": "Deine Agenten: {names}.",  # i18n-allow: spoken reply
    "en": "Your agents: {names}.",
    "es": "Tus agentes: {names}.",
}
_EMPTY_ROSTER: Final[dict[str, str]] = {
    "de": "Du hast noch keine Team-Agenten.",  # i18n-allow: spoken reply
    "en": "You do not have any team agents yet.",
    "es": "Todavía no tienes agentes en tu equipo.",
}


def _lang(args: dict[str, Any], ctx: Any) -> str:
    """The turn's output language: ``ctx.config["output_language"]`` as the
    tool-use loop stamps it (decided once per turn by ``turn_language.py``),
    else a ``turn_language`` argument, else the ambient answer language. This
    layer never re-derives a language from the utterance (CLAUDE.md §1)."""
    config = getattr(ctx, "config", None) or {}
    value = (
        str(
            (config.get("output_language") if isinstance(config, dict) else "")
            or args.get("turn_language")
            or ""
        )
        .strip()
        .lower()
    )
    if not value:
        try:
            from jarvis.voice.action_phrases import resolve_ambient_language

            value = resolve_ambient_language()
        except Exception:  # noqa: BLE001 — a spoken fallback beats a crash on the voice path
            value = "en"
    return value if value in _ACK else "en"


class DelegateToAgentTool:
    """Hand a task to a named society agent; acknowledge at once."""

    name: str = "delegate_to_agent"
    risk_tier: str = "monitor"
    description: str = (
        "Hand a task to one of the user's named agents (their agent society, listed on your "
        "team card): 'let Scout research X', 'Mailbox, answer the invoice mail', 'give that "
        "to the team'. Use when a background task fits the persistent team; leave `agent` "
        "empty to let the lead pick the agent whose hands fit the task. The agent works in "
        "the background; you acknowledge now and its result is announced when it lands. "
        "Never for inventory/status questions or tasks the user wants done right here. "
        "Include relevant conversation context and completion criteria so the agent "
        "can act independently. Report the acknowledgement and state exactly. Keep the "
        "returned assignment_id and trace_id for society_status; never speak those ids."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "description": (
                    "The agent's name as the user said it; empty when the user did not "
                    "name one (the best-fitting agent is picked from the task)."
                ),
            },
            "task": {"type": "string", "description": "The task, in full, in the user's words."},
            "context": {
                "type": "string",
                "description": "Relevant prior decisions, constraints and facts; omit secrets.",
            },
            "completion_criteria": {
                "type": "string",
                "description": "Required output and the evidence needed to establish completion.",
            },
            "refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Relevant file, wiki or conversation references.",
            },
        },
        "required": ["task"],
    }
    is_action_tool: bool = True

    def __init__(self, *, runtime_resolver: RuntimeResolver) -> None:
        self._resolve = runtime_resolver

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        lang = _lang(args, ctx)
        target_key = str(args.get("agent") or "").strip()
        task = str(args.get("task") or "").strip()
        if not task:
            return ToolResult(success=False, output=_NOT_READY[lang], error="task required")
        runtime = await self._runtime()
        if runtime is None:
            return ToolResult(success=False, output=_NOT_READY[lang], error="society unavailable")
        target = await runtime.roster.resolve(target_key) if target_key else None
        if not target_key:
            target = runtime.pick_agent(task)
        if target is None:
            if target_key:
                return ToolResult(
                    success=False,
                    output=_NO_AGENT[lang].format(target=target_key),
                    error="target_unknown",
                )
            return ToolResult(success=False, output=_NO_FIT[lang], error="no_agent_fits")
        from jarvis.society.events import MsgType

        context = str(args.get("context") or "").strip()
        criteria = str(args.get("completion_criteria") or "").strip()
        brief = task
        if context:
            brief += "\n\nRelevant context:\n" + context
        if criteria:
            brief += "\n\nCompletion criteria:\n" + criteria
        refs = args.get("refs") or []
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            return ToolResult(success=False, output=None, error="refs must be a list of strings")
        if len(brief) > 20_000 or len(refs) > 20:
            return ToolResult(success=False, output=None, error="assignment context exceeds limit")
        env = await runtime.say(
            from_agent=runtime.lead_id,
            to_agent=target.agent_id,
            text=brief,
            trace_id=f"voice:{ctx.trace_id.hex[:12]}",
            msg_type=MsgType.ASSIGN,
            payload={"text": brief, "lang": lang, "refs": refs},
        )
        # The scheduler answered synchronously on the same trace: a CLAIM
        # means the agent took it, a VETO says why not — say so, no waiting.
        outcome = None
        for event in await runtime.store.events_for_trace(env.trace_id):
            if event.parent_event_id == env.event_id and event.msg_type in (
                MsgType.CLAIM,
                MsgType.VETO,
            ):
                outcome = event
                break
        tracking = {
            "agent_id": target.agent_id,
            "agent_name": target.name,
            "assignment_id": env.event_id,
            "trace_id": env.trace_id,
        }
        if outcome is not None and outcome.msg_type is MsgType.VETO:
            reason = str(outcome.payload.get("text") or outcome.payload.get("reason") or "")
            return ToolResult(
                success=False,
                output={
                    **tracking,
                    "state": "refused",
                    "acknowledgement": _REFUSED[lang].format(name=target.name, reason=reason),
                },
                error=str(outcome.payload.get("reason") or "vetoed"),
            )
        return ToolResult(
            success=True,
            output={
                **tracking,
                "state": "running" if outcome is not None else "recorded",
                "acknowledgement": (_ACK if outcome is not None else _QUEUED)[lang].format(
                    name=target.name,
                ),
            },
            artifacts=(
                f"agent:{target.agent_id}",
                f"assignment:{env.event_id}",
                f"trace:{env.trace_id}",
            ),
        )

    async def _runtime(self) -> Any | None:
        try:
            runtime = self._resolve()
            if runtime is not None:
                prepare = getattr(runtime, "prepare_context", None)
                if callable(prepare):
                    if not await prepare():
                        return None
                else:
                    await runtime.ensure_started()
        except Exception:  # noqa: BLE001 — a missing society is a spoken "not ready"
            log.warning("delegate_to_agent: society runtime unavailable", exc_info=True)
            return None
        return runtime


class SocietyStatusTool:
    """What an agent (or the whole team) is doing — read-only, no model call."""

    name: str = "society_status"
    risk_tier: str = "safe"
    description: str = (
        "Answer 'what is <agent> doing?', 'is Scout done?', 'who is on the team?' from the "
        "agent society's board. Read-only. Pass the agent's name, or nothing for the team. "
        "Set details=true for responsibilities, capabilities, state and recorded activity/cost "
        "statistics (these are observations, not quality scores). Pass assignment_id "
        "to inspect that exact assignment; trace_id is optional. If prior tool ids are "
        "no longer in context, use agent + latest_assignment=true to retrieve the newest "
        "assignment Jarvis sent that agent from the stored board, including its task and "
        "result. This means newest assignment, not an arbitrary recent event. If 'that task' "
        "could mean an older task or several agents, clarify the agent/task first."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "An agent's name; omit for the team."},
            "details": {"type": "boolean", "description": "Include actual roster and evidence."},
            "assignment_id": {"type": "string", "description": "Assignment event id."},
            "trace_id": {"type": "string", "description": "Optional expected assignment trace."},
            "latest_assignment": {
                "type": "boolean",
                "description": (
                    "With agent: inspect the newest assignment sent by Jarvis. "
                    "Do not combine with assignment_id."
                ),
            },
        },
    }

    def __init__(self, *, runtime_resolver: RuntimeResolver) -> None:
        self._resolve = runtime_resolver

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        lang = _lang(args, ctx)
        try:
            from jarvis.society.events import MsgType

            runtime = self._resolve()
            if runtime is not None:
                prepare = getattr(runtime, "prepare_context", None)
                if callable(prepare):
                    if not await prepare():
                        runtime = None
                else:
                    await runtime.ensure_started()
        except Exception:  # noqa: BLE001 — see DelegateToAgentTool._runtime
            log.warning("society_status: runtime resolver failed", exc_info=True)
            runtime = None
        if runtime is None:
            return ToolResult(success=False, output=_NOT_READY[lang], error="society unavailable")
        assignment_id = str(args.get("assignment_id") or "").strip()
        trace_id = str(args.get("trace_id") or "").strip()
        target_key = str(args.get("agent") or "").strip()
        latest_assignment = args.get("latest_assignment") is True
        if assignment_id or trace_id or latest_assignment:
            if latest_assignment and assignment_id:
                return ToolResult(
                    success=False, output=None, error="choose assignment_id or latest_assignment"
                )
            if latest_assignment:
                if not target_key:
                    return ToolResult(success=False, output=None, error="agent required")
                target = await runtime.roster.resolve(target_key)
                if target is None:
                    return ToolResult(success=False, output=None, error="target_unknown")
                assignment = await runtime.store.latest_assignment_for_agent(
                    target.agent_id,
                    from_agent=runtime.lead_id,
                )
            elif assignment_id:
                assignment = await runtime.store.get_event(assignment_id)
            else:
                return ToolResult(success=False, output=None, error="assignment_id required")
            if assignment is None or assignment.msg_type is not MsgType.ASSIGN:
                return ToolResult(success=False, output=None, error="assignment_unknown")
            if trace_id and trace_id != assignment.trace_id:
                return ToolResult(success=False, output=None, error="assignment_trace_mismatch")
            assignment_id, trace_id = assignment.event_id, assignment.trace_id
            events = await runtime.store.events_for_trace(trace_id)
            related = [
                e
                for e in events
                if e.parent_event_id == assignment_id
                and e.msg_type in (MsgType.CLAIM, MsgType.RESULT, MsgType.VETO)
            ]
            # The fallback mission runner's bridge publishes its terminal result
            # on mission:<run_id>, without the original assignment parent. Follow
            # only the scheduler's matching claim and verify both run and owner;
            # another mission or agent must never complete this assignment.
            run_ids = {
                str(event.payload["run_id"])
                for event in related
                if event.msg_type is MsgType.CLAIM
                and event.from_agent == assignment.to_agent
                and event.payload.get("run_id")
            }
            for run_id in run_ids:
                if run_id.startswith("turn:"):
                    continue
                mission_events = await runtime.store.events_for_trace(f"mission:{run_id}")
                related.extend(
                    event
                    for event in mission_events
                    if event.msg_type is MsgType.RESULT
                    and event.from_agent == assignment.to_agent
                    and event.payload.get("run_id") == run_id
                )
            related.sort(key=lambda event: event.seq or 0)
            last = related[-1] if related else None
            state = "recorded"
            if last is not None:
                state = {
                    MsgType.CLAIM: "running",
                    MsgType.VETO: "refused",
                    MsgType.RESULT: str(last.payload.get("status") or "finished"),
                }[last.msg_type]
            return ToolResult(
                success=True,
                output={
                    "assignment_id": assignment_id,
                    "trace_id": trace_id,
                    "state": state,
                    "assignment": assignment.model_dump(mode="json"),
                    "events": [e.model_dump(mode="json") for e in related],
                },
            )
        if args.get("details") is True:
            targets = await runtime.roster.list()
            if target_key:
                target = await runtime.roster.resolve(target_key)
                if target is None:
                    return ToolResult(
                        success=False,
                        output=_NO_AGENT[lang].format(target=target_key),
                        error="target_unknown",
                    )
                targets = [target]
            rows = []
            for agent in targets:
                if agent.agent_id == runtime.lead_id:
                    continue
                recent = await runtime.store.events_for_agent(agent.agent_id, limit=10)
                rows.append(
                    {
                        "agent": agent.to_dict(),
                        "active_runs": runtime.scheduler.active_runs(agent.agent_id),
                        "stats": await runtime.store.agent_stats(agent.agent_id),
                        "recent_events": [e.model_dump(mode="json") for e in recent],
                    }
                )
            return ToolResult(
                success=True,
                output={
                    "agents": rows,
                    "measurement_note": (
                        "Runs count recorded RESULT events, including failures; costs are "
                        "recorded costs only. No independent quality rating is available."
                    ),
                },
            )
        if not target_key:
            agents = await runtime.roster.list()
            names = ", ".join(a.name for a in agents if a.agent_id != runtime.lead_id)
            if not names:
                return ToolResult(success=True, output=_EMPTY_ROSTER[lang])
            return ToolResult(success=True, output=_ROSTER[lang].format(names=names))
        target = await runtime.roster.resolve(target_key)
        if target is None:
            return ToolResult(
                success=False,
                output=_NO_AGENT[lang].format(target=target_key),
                error="target_unknown",
            )
        if runtime.scheduler.active_runs(target.agent_id) > 0:
            events = await runtime.store.events_for_agent(target.agent_id, limit=5)
            text = next((e.text for e in reversed(events) if e.text), "")
            return ToolResult(
                success=True, output=_STATUS_WORKING[lang].format(name=target.name, text=text)
            )
        events = await runtime.store.events_for_agent(target.agent_id, limit=1)
        if not events:
            return ToolResult(success=True, output=_STATUS_IDLE[lang].format(name=target.name))
        last = events[-1]
        return ToolResult(
            success=True,
            output=_STATUS_LAST[lang].format(
                name=target.name, kind=str(last.msg_type).lower(), text=last.text[:200]
            ),
        )
