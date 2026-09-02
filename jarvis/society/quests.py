"""Quests — one job the person posts on the board; the society takes it.

A quest is the game-shaped front of an ``ASSIGN``: the person writes "the
five most important mails of today" on the Quest Board in the middle of the
market square, trusted Python picks the ONE agent that should take it, and
from there the ordinary machinery runs — the scheduler starts the work, the
board carries CLAIM / RESULT / VETO on the quest's trace, and this module
only *reads* those envelopes back into the quest's state. Nothing here
starts work on its own and no model decides who takes what (AP-3/AP-5).

Routing (``choose_taker``) is deterministic and free, like ``focus.py``:

1. the quest text is folded into capability ids the way an agent description
   is (``derive_focus``), then every active non-lead agent is scored — focus
   overlap, its name or title named in the quest, description words shared
   with the quest; a busy agent loses a point per running task;
2. the best agent takes it when it scores at all;
3. nobody fits → the Agent Foundry forges a teammate: the seed proposal
   whose capability the quest points at (a mail quest forges "Mailbox" when
   a mail plugin is connected), else the one generalist "Runner" that is
   created once and takes everything unclaimed. Jarvis, the lead, never
   works a quest — it delegates.

The person sees why in ``routing`` ("focus-match", "forged:plugin:gmail",
"generalist"). A refusal from the scheduler (budget, cap, kill switch) makes
the quest ``failed`` with the typed reason; ``retry`` routes it again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from jarvis.missions.ids import uuid7_str

from .capabilities import CapabilityRow
from .events import USER_ACTOR, MsgType, QuestState, SocietyEnvelope, Tier, now_ms
from .focus import derive_focus
from .roster import AgentRecord, AgentState
from .seeds import proposal_for_capability

log = logging.getLogger(__name__)

__all__ = [
    "GENERALIST",
    "QuestRecord",
    "Quests",
    "RoutingChoice",
    "choose_taker",
    "quest_title",
]

#: Trace prefix every quest's envelopes share; the listener keys on it.
TRACE_PREFIX: Final[str] = "quest:"
#: Below this score a match is noise and the foundry is the better option.
MIN_MATCH_SCORE: Final[int] = 3
#: Terminal states never change again — a late RESULT on a cancelled quest is ignored.
TERMINAL: Final[frozenset[QuestState]] = frozenset(
    {QuestState.DONE, QuestState.FAILED, QuestState.CANCELLED}
)
#: Refusals that mean "not now", not "never": the quest waits and is routed again.
WAIT_REASONS: Final[frozenset[str]] = frozenset({"target_busy", "concurrency_cap"})
#: How long a waiting quest keeps knocking: every RETRY_DELAY_S, at most MAX_WAIT_ATTEMPTS.
RETRY_DELAY_S: Final[float] = 15.0
MAX_WAIT_ATTEMPTS: Final[int] = 40
#: How many progress lines a running quest keeps (newest last).
PROGRESS_KEEP: Final[int] = 6

#: The generalist the foundry forges once when no specialist fits a quest.
GENERALIST: Final[dict[str, Any]] = {
    "name": "Runner",
    "title": "Errand runner",
    "description": (
        "You take the quests from the board that no specialist claims: small, "
        "clearly worded jobs of any kind. You work with the tools the quest needs, "
        "keep to what was asked, and hand off with what is done, where it is and "
        "what remains open."
    ),
    "permission_ceiling": "monitor",
    "daily_budget_usd": 2.0,
}

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9\-]{2,}")
_STOP: Final[frozenset[str]] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "your",
        "you",
        "are",
        "all",
        "der",
        "die",
        "das",
        "und",
        "mit",
        "von",
        "für",  # i18n-allow: speech-input vocabulary (stop word)
        "fuer",  # i18n-allow: speech-input vocabulary (stop word)
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einen",
        "meine",
        "meinen",
        "mir",
        "mich",
        "bitte",
        "please",
        "heute",
        "today",
        "agent",
        "quest",
    }
)


def _fold(text: str) -> str:
    text = text.lower()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):  # i18n-allow: folding
        text = text.replace(src, dst)
    return text


def _words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(_fold(text)) if w not in _STOP}


def quest_title(text: str, explicit: str = "") -> str:
    """The board's one-line title: the explicit one, else the first line, trimmed."""
    title = (explicit or "").strip()
    if not title:
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        title = first
    if len(title) > 72:
        title = title[:69].rstrip() + "…"
    return title or "Quest"


# ------------------------------------------------------------------ routing


@dataclass(frozen=True, slots=True)
class RoutingChoice:
    """Who takes the quest and why — persisted on the row as ``routing``."""

    agent_id: str | None
    reason: str
    score: int = 0
    focus: list[str] = field(default_factory=list)
    #: A roster spec the foundry should create first (``agent_id`` is then
    #: the name's slug, created by the caller).
    forge: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "reason": self.reason,
            "score": self.score,
            "focus": list(self.focus),
            "forged": bool(self.forge),
        }


def score_agent(agent: AgentRecord, text: str, focus: list[str], busy: int = 0) -> int:
    """How well ``agent`` fits a quest — 0 means no signal at all."""
    score = 0
    for rank, cap_id in enumerate(focus):
        if cap_id in agent.focus:
            score += 6 if rank == 0 else 4
    folded = _fold(text)
    for word in _words(agent.name):
        if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", folded):
            score += 3
    for word in _words(agent.title):
        if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", folded):
            score += 2
    shared = _words(text) & _words(agent.description)
    score += min(5, len(shared))
    if agent.tier is Tier.SPECIALIST and score > 0:
        score += 1
    return max(0, score - busy)


def choose_taker(
    text: str,
    agents: list[AgentRecord],
    catalog: list[CapabilityRow],
    *,
    busy: Mapping[str, int] | None = None,
) -> RoutingChoice:
    """Pure: the taker for ``text`` among ``agents``, or what to forge."""
    focus = derive_focus("", text, catalog)
    busy = busy or {}
    candidates = [a for a in agents if a.state is AgentState.ACTIVE and a.tier is not Tier.LEAD]
    ranked = sorted(
        ((score_agent(a, text, focus, busy.get(a.agent_id, 0)), a) for a in candidates),
        key=lambda pair: (-pair[0], pair[1].tier is not Tier.SPECIALIST, pair[1].created_ms),
    )
    if ranked and ranked[0][0] >= MIN_MATCH_SCORE:
        best_score, best = ranked[0]
        reason = "focus-match" if any(c in best.focus for c in focus) else "keyword-match"
        return RoutingChoice(best.agent_id, reason, best_score, focus)
    taken = {a.name.lower() for a in agents}
    for cap_id in focus:
        spec = proposal_for_capability(cap_id, catalog)
        if spec is None or spec["name"].lower() in taken:
            continue
        return RoutingChoice(None, f"forged:{cap_id}", 0, focus, forge=spec)
    generalist = next((a for a in candidates if a.name.lower() == GENERALIST["name"].lower()), None)
    if generalist is not None:
        return RoutingChoice(generalist.agent_id, "generalist", 0, focus)
    orchestrator = next((a for a in candidates if a.tier is Tier.ORCHESTRATOR), None)
    if orchestrator is not None:
        return RoutingChoice(orchestrator.agent_id, "orchestrator", 0, focus)
    return RoutingChoice(None, "forged:generalist", 0, focus, forge=dict(GENERALIST))


# ------------------------------------------------------------------- record


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except ValueError:
            return default
    return default


@dataclass(frozen=True, slots=True)
class QuestRecord:
    quest_id: str
    title: str
    text: str
    state: QuestState
    created_by: str
    agent_id: str | None
    trace_id: str
    assign_event_id: str | None
    run_id: str
    routing: dict[str, Any]
    result: dict[str, Any]
    created_ms: int
    updated_ms: int
    done_ms: int | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> QuestRecord:
        return cls(
            quest_id=str(row["quest_id"]),
            title=str(row.get("title") or ""),
            text=str(row.get("text") or ""),
            state=QuestState(str(row.get("state") or "open")),
            created_by=str(row.get("created_by") or USER_ACTOR),
            agent_id=row.get("agent_id"),
            trace_id=str(row["trace_id"]),
            assign_event_id=row.get("assign_event_id"),
            run_id=str(row.get("run_id") or ""),
            routing=_loads(row.get("routing_json"), {}),
            result=_loads(row.get("result_json"), {}),
            created_ms=int(row.get("created_ms") or 0),
            updated_ms=int(row.get("updated_ms") or 0),
            done_ms=row.get("done_ms"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "quest_id": self.quest_id,
            "title": self.title,
            "text": self.text,
            "state": str(self.state),
            "created_by": self.created_by,
            "agent_id": self.agent_id,
            "trace_id": self.trace_id,
            "assign_event_id": self.assign_event_id,
            "run_id": self.run_id,
            "routing": dict(self.routing),
            "result": dict(self.result),
            "created_ms": self.created_ms,
            "updated_ms": self.updated_ms,
            "done_ms": self.done_ms,
        }


# ------------------------------------------------------------------ service


class Quests:
    """The quest board over the runtime: create → route → listen."""

    def __init__(
        self,
        runtime: Any,
        *,
        publish: Callable[[Any], Any] | None = None,
        retry_delay_s: float = RETRY_DELAY_S,
    ) -> None:
        self._runtime = runtime
        self._publish = publish
        self._retry_delay = retry_delay_s
        self._unsubscribe: Callable[[], None] | None = None
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._attempts: dict[str, int] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------ wiring

    def attach(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = self._runtime.store.bus.subscribe_all(self._on_envelope)

    def detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    # ------------------------------------------------------------ waiting

    def blocker_of(self, agent_id: str) -> str:
        """Why the agent cannot take work right now: ``approval`` (its chat waits
        for the person), ``busy`` (a turn runs), or an empty string."""
        get_chat = getattr(self._runtime, "_get_chat", None)
        svc = get_chat() if callable(get_chat) else None
        if svc is None:
            return ""
        session_id = f"society:{agent_id}"
        try:
            pending = getattr(svc, "pending_approvals", None)
            if callable(pending) and pending(session_id):
                return "approval"
            if svc.is_running(session_id):
                return "busy"
        except Exception:  # noqa: BLE001 - the blocker is a hint for the card, never a gate
            log.debug("society quests: blocker probe failed for %s", agent_id, exc_info=True)
        return ""

    def _arm(self, quest_id: str, delay_s: float) -> None:
        old = self._timers.pop(quest_id, None)
        if old is not None:
            old.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._timers[quest_id] = loop.call_later(delay_s, self._fire, quest_id)

    def _fire(self, quest_id: str) -> None:
        self._timers.pop(quest_id, None)
        task = asyncio.create_task(self._retry_waiting(quest_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _retry_waiting(self, quest_id: str) -> None:
        try:
            quest = await self.get(quest_id)
            if quest is None or quest.state is not QuestState.OPEN:
                return
            await self.route(quest_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a failed knock is logged; the next timer knocks again
            log.warning("society quests: retry of %s failed", quest_id, exc_info=True)

    async def _wake_waiting(self) -> None:
        """A slot was freed: the oldest waiting quests knock right away."""
        waiting = sorted(await self.list(state=QuestState.OPEN), key=lambda q: q.created_ms)
        for quest in waiting:
            if quest.result.get("status") == "waiting":
                await self.route(quest.quest_id)

    async def note_progress(self, trace_id: str, line: str, *, live: str | None = None) -> None:
        """A running quest's latest step (from the agent's turn) for the card."""
        row = await self._runtime.store.get_quest_row_by_trace(trace_id)
        if row is None:
            return
        quest = QuestRecord.from_row(row)
        if quest.state is not QuestState.RUNNING:
            return
        result = dict(quest.result)
        progress = list(result.get("progress") or [])
        line = line.strip()
        if line and (not progress or progress[-1] != line):
            progress.append(line[:160])
        result["progress"] = progress[-PROGRESS_KEEP:]
        if live is not None:
            result["live"] = live.strip()[:240]
        await self._runtime.store.update_quest(
            quest.quest_id, {"result_json": json.dumps(result), "updated_ms": now_ms()}
        )
        fresh = await self.get(quest.quest_id)
        if fresh is not None:
            await self._announce(fresh, previous=quest.state)

    # ------------------------------------------------------------- reads

    async def get(self, quest_id: str) -> QuestRecord | None:
        row = await self._runtime.store.get_quest_row(quest_id)
        return QuestRecord.from_row(row) if row else None

    async def list(
        self, *, state: QuestState | str | None = None, limit: int = 200
    ) -> list[QuestRecord]:
        rows = await self._runtime.store.list_quest_rows(
            state=str(state) if state else None, limit=limit
        )
        return [QuestRecord.from_row(r) for r in rows]

    # ------------------------------------------------------------ writes

    async def create(
        self,
        text: str,
        *,
        title: str = "",
        created_by: str = USER_ACTOR,
        lang: str | None = None,
    ) -> QuestRecord:
        """Post a quest and route it right away."""
        clean = text.strip()
        if not clean:
            raise ValueError("a quest needs text")
        quest_id = uuid7_str()
        now = now_ms()
        await self._runtime.store.insert_quest(
            {
                "quest_id": quest_id,
                "title": quest_title(clean, title),
                "text": clean,
                "state": str(QuestState.OPEN),
                "created_by": created_by,
                "trace_id": f"{TRACE_PREFIX}{quest_id}",
                "created_ms": now,
                "updated_ms": now,
            }
        )
        quest = await self.get(quest_id)
        assert quest is not None
        await self._announce(quest, previous=None)
        return await self.route(quest_id, lang=lang)

    async def route(self, quest_id: str, *, lang: str | None = None) -> QuestRecord:
        """Pick the taker (forging one when nobody fits) and put the ASSIGN on the board."""
        quest = await self.get(quest_id)
        if quest is None:
            raise KeyError(quest_id)
        if quest.state in TERMINAL and quest.state is not QuestState.FAILED:
            return quest
        if quest.state is not QuestState.OPEN:
            self._attempts.pop(quest_id, None)
        roster = self._runtime.roster
        agents = await roster.list()
        busy = {a.agent_id: self._runtime.scheduler.active_runs(a.agent_id) for a in agents}
        choice = choose_taker(quest.text, agents, self._runtime.catalog(), busy=busy)
        agent_id = choice.agent_id
        if choice.forge is not None:
            spec = dict(choice.forge)
            skip = ("name", "title", "description", "tier", "reason")
            fields = {k: v for k, v in spec.items() if k not in skip}
            if "focus" not in fields and choice.focus:
                fields["focus"] = list(choice.focus)
            record, created = await roster.create(
                name=spec["name"],
                title=spec.get("title", ""),
                description=spec.get("description", ""),
                tier=spec.get("tier", Tier.SPECIALIST),
                **fields,
            )
            agent_id = record.agent_id
            if created:
                log.info("society quests: forged %s for quest %s", record.name, quest_id)
        if agent_id is None:
            await self._set(quest, state=QuestState.OPEN, routing_json=json.dumps(choice.to_dict()))
            fresh = await self.get(quest_id)
            assert fresh is not None
            return fresh
        payload: dict[str, Any] = {
            "text": quest.text,
            "quest_id": quest_id,
            "title": quest.title,
        }
        if lang in ("de", "en"):
            payload["lang"] = lang
        await self._set(
            quest,
            state=QuestState.ASSIGNED,
            agent_id=agent_id,
            run_id="",
            result_json="{}",
            done_ms=None,
            routing_json=json.dumps({**choice.to_dict(), "agent_id": agent_id}),
        )
        env = await self._runtime.store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.ASSIGN,
                from_agent=quest.created_by or USER_ACTOR,
                to_agent=agent_id,
                trace_id=quest.trace_id,
                payload=payload,
            )
        )
        await self._runtime.store.update_quest(quest_id, {"assign_event_id": env.event_id})
        fresh = await self.get(quest_id)
        assert fresh is not None
        return fresh

    async def retry(self, quest_id: str, *, lang: str | None = None) -> QuestRecord:
        """A failed or open quest goes back on the board and is routed again."""
        quest = await self.get(quest_id)
        if quest is None:
            raise KeyError(quest_id)
        if quest.state not in (QuestState.FAILED, QuestState.OPEN):
            return quest
        return await self.route(quest_id, lang=lang)

    async def cancel(self, quest_id: str) -> QuestRecord:
        """Take the quest off the board; a run in flight frees its slot and its
        late RESULT is ignored (the turn itself finishes on its own)."""
        quest = await self.get(quest_id)
        if quest is None:
            raise KeyError(quest_id)
        if quest.state in TERMINAL:
            return quest
        if quest.run_id:
            self._runtime.scheduler.note_run_ended(quest.run_id)
        handle = self._timers.pop(quest_id, None)
        if handle is not None:
            handle.cancel()
        self._attempts.pop(quest_id, None)
        await self._set(quest, state=QuestState.CANCELLED, done_ms=now_ms())
        fresh = await self.get(quest_id)
        assert fresh is not None
        return fresh

    # ---------------------------------------------------------- listener

    async def _on_envelope(self, env: SocietyEnvelope) -> None:
        if not env.trace_id.startswith(TRACE_PREFIX):
            if env.msg_type is MsgType.RESULT:
                await self._wake_waiting()
            return
        row = await self._runtime.store.get_quest_row_by_trace(env.trace_id)
        if row is None:
            return
        quest = QuestRecord.from_row(row)
        if quest.state in TERMINAL:
            return
        if env.msg_type is MsgType.CLAIM:
            run_id = str(env.payload.get("run_id") or "")
            await self._set(quest, state=QuestState.RUNNING, agent_id=env.from_agent, run_id=run_id)
        elif env.msg_type is MsgType.RESULT:
            status = str(env.payload.get("status") or "done")
            result = {
                "status": status,
                "done": env.payload.get("done", ""),
                "output": env.payload.get("output", []),
                "open": env.payload.get("open", []),
                "text": env.text,
            }
            state = QuestState.FAILED if status == "blocked" else QuestState.DONE
            result["progress"] = list(quest.result.get("progress") or [])
            await self._set(quest, state=state, result_json=json.dumps(result), done_ms=env.ts_ms)
            self._attempts.pop(quest.quest_id, None)
            await self._wake_waiting()
        elif env.msg_type is MsgType.VETO:
            reason = str(env.payload.get("reason", ""))
            attempts = self._attempts.get(quest.quest_id, 0) + 1
            if reason in WAIT_REASONS and attempts <= MAX_WAIT_ATTEMPTS:
                # Not now, not never: the quest waits on the board and knocks again.
                self._attempts[quest.quest_id] = attempts
                result = {
                    "status": "waiting",
                    "reason": reason,
                    "blocker": self.blocker_of(quest.agent_id) if quest.agent_id else "",
                    "text": env.text,
                    "attempts": attempts,
                }
                await self._set(quest, state=QuestState.OPEN, result_json=json.dumps(result))
                self._arm(quest.quest_id, self._retry_delay)
                return
            self._attempts.pop(quest.quest_id, None)
            result = {
                "status": "vetoed",
                "reason": reason,
                "retry": env.payload.get("retry", ""),
                "text": env.text,
            }
            await self._set(
                quest, state=QuestState.FAILED, result_json=json.dumps(result), done_ms=env.ts_ms
            )

    # ----------------------------------------------------------- helpers

    async def _set(self, quest: QuestRecord, *, state: QuestState, **fields: Any) -> None:
        fields["state"] = str(state)
        fields["updated_ms"] = now_ms()
        await self._runtime.store.update_quest(quest.quest_id, fields)
        if state is not quest.state or "agent_id" in fields:
            fresh = await self.get(quest.quest_id)
            if fresh is not None:
                await self._announce(fresh, previous=quest.state)

    async def _announce(self, quest: QuestRecord, *, previous: QuestState | None) -> None:
        """``SocietyQuestChanged`` on the app bus — the WebSocket forwards it,
        the board in every open window re-reads within a second."""
        try:
            from jarvis.core.events import SocietyQuestChanged

            event = SocietyQuestChanged(
                source_layer="society",
                quest_id=quest.quest_id,
                state=str(quest.state),
                previous=str(previous) if previous is not None else "",
                agent_id=quest.agent_id or "",
                title=quest.title,
            )
            if self._publish is not None:
                maybe = self._publish(event)
            else:
                from jarvis.core.bus import get_default_bus

                maybe = get_default_bus().publish(event)
            if hasattr(maybe, "__await__"):
                await maybe
        except Exception:  # noqa: BLE001 — a window that misses the push falls back to its poll
            log.debug("society quests: change not pushed", exc_info=True)
