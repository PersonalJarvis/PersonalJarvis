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
    "de": "Aktive Agenten: {names}.",  # i18n-allow: spoken reply
    "en": "Active agents: {names}.",
    "es": "Agentes activos: {names}.",
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
        "to the team'. Use when the user names an agent or asks for the team; leave `agent` "
        "empty to let the lead pick the agent whose hands fit the task. The agent works in "
        "the background; you acknowledge now and its result is announced when it lands. "
        "Never for tasks the user wants done right here in this turn."
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
        if target is None:
            # No name, or a name the roster does not know ("email agent" for
            # the Gmail agent): the task's own words pick the agent whose
            # hands fit. Nobody fits → say so; never guess a stranger.
            target = runtime.pick_agent(f"{target_key} {task}".strip())
        if target is None:
            if target_key:
                return ToolResult(
                    success=False,
                    output=_NO_AGENT[lang].format(target=target_key),
                    error="target_unknown",
                )
            return ToolResult(success=False, output=_NO_FIT[lang], error="no_agent_fits")
        from jarvis.society.events import MsgType

        env = await runtime.say(
            from_agent=runtime.lead_id,
            to_agent=target.agent_id,
            text=task,
            trace_id=f"voice:{ctx.trace_id.hex[:12]}",
            msg_type=MsgType.ASSIGN,
            payload={"text": task, "lang": lang},
        )
        # The scheduler answered synchronously on the same trace: a CLAIM
        # means the agent took it, a VETO says why not — say so, no waiting.
        outcome = None
        for event in await runtime.store.events_for_trace(env.trace_id):
            if event.seq and env.seq and event.seq > env.seq:
                outcome = event
                break
        if outcome is not None and outcome.msg_type is MsgType.VETO:
            reason = str(outcome.payload.get("text") or outcome.payload.get("reason") or "")
            return ToolResult(
                success=False,
                output=_REFUSED[lang].format(name=target.name, reason=reason),
                error=str(outcome.payload.get("reason") or "vetoed"),
            )
        return ToolResult(
            success=True,
            output=_ACK[lang].format(name=target.name),
            artifacts=(f"agent:{target.agent_id}",),
        )

    async def _runtime(self) -> Any | None:
        try:
            runtime = self._resolve()
        except Exception:  # noqa: BLE001 — a missing society is a spoken "not ready"
            log.warning("delegate_to_agent: society runtime resolver failed", exc_info=True)
            return None
        if runtime is None:
            return None
        await runtime.ensure_started()
        return runtime


class SocietyStatusTool:
    """What an agent (or the whole team) is doing — read-only, no model call."""

    name: str = "society_status"
    risk_tier: str = "safe"
    description: str = (
        "Answer 'what is <agent> doing?', 'is Scout done?', 'who is on the team?' from the "
        "agent society's board. Read-only. Pass the agent's name, or nothing for the team."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "An agent's name; omit for the team."},
        },
    }

    def __init__(self, *, runtime_resolver: RuntimeResolver) -> None:
        self._resolve = runtime_resolver

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        lang = _lang(args, ctx)
        try:
            runtime = self._resolve()
        except Exception:  # noqa: BLE001 — see DelegateToAgentTool._runtime
            runtime = None
        if runtime is None:
            return ToolResult(success=False, output=_NOT_READY[lang], error="society unavailable")
        await runtime.ensure_started()
        target_key = str(args.get("agent") or "").strip()
        if not target_key:
            agents = await runtime.roster.list()
            names = ", ".join(a.name for a in agents if a.agent_id != runtime.lead_id) or "—"
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
