"""Turns real app events into XP and announces every award on the bus.

The service listens to the core ``EventBus`` for the handful of events that
mean "something was achieved" (a finished Jarvis turn, a finished agent task,
a completed quest) and maps each to one or two rules of
:mod:`jarvis.progression.rules`. The Verse reports its own small world
actions (petting the dog, an arcade round) through
:meth:`ProgressionService.report_action`; the server still applies every
cooldown and cap, so a client can never award itself XP.

Every handler is an observer: a failure is logged and swallowed, never
raised into the bus (AP-18), and the database is opened on the first award,
not at boot (AP-26). Writes run off the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .rules import (
    LEAD_AGENT_ID,
    PERSON_ID,
    RULES_BY_SOURCE,
    WORLD_ACTIONS,
    agent_subject,
    pet_subject,
    progress_for_xp,
    rewards_between,
    subject_kind,
    title_for,
)
from .store import Award, ProgressionStore

log = logging.getLogger(__name__)

#: The floors of the Verse a ``floor_discovered`` report may name.
VERSE_FLOORS = frozenset({"agents", "coding", "arcade"})


class UnknownAction(ValueError):
    """A world action the rulebook does not know."""


class ProgressionService:
    def __init__(
        self,
        db_path: Path,
        *,
        bus: Any | None = None,
        pet_id: Callable[[], str] | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self.store = ProgressionStore(db_path, clock=clock)
        self._bus = bus
        self._pet_id = pet_id or (lambda: "gigi")
        self._attached = False
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------ wiring

    def attach(self) -> None:
        """Subscribe to the bus. Idempotent; opens nothing."""
        if self._bus is None or self._attached:
            return
        from jarvis.core.events import (
            JarvisChatTurnFinished,
            MissionCompleted,
            SocietyMessageSent,
            SocietyQuestChanged,
            SocietyResultPosted,
            VoiceTurnCompleted,
        )

        self._bus.subscribe(JarvisChatTurnFinished, self._on_chat_turn)
        self._bus.subscribe(VoiceTurnCompleted, self._on_voice_turn)
        self._bus.subscribe(SocietyQuestChanged, self._on_quest)
        self._bus.subscribe(SocietyResultPosted, self._on_result)
        self._bus.subscribe(SocietyMessageSent, self._on_message)
        self._bus.subscribe(MissionCompleted, self._on_mission)
        self._attached = True

    def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self.store.close()

    async def drain(self) -> None:
        """Wait for every award the bus handlers queued (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _later(self, *awards: tuple[str, str, str]) -> None:
        """Queue awards off the publisher's path.

        Typed bus handlers are awaited by the publisher (the voice pipeline
        among them), so a handler only schedules its writes and returns.
        """

        async def run() -> None:
            for subject_id, source, ref in awards:
                await self._safe_award(subject_id, source, ref)

        task = asyncio.get_running_loop().create_task(run(), name="progression-award")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def current_pet(self) -> str:
        try:
            pet = str(self._pet_id() or "").strip()
        except Exception:  # noqa: BLE001 - a broken getter falls back to the default pet
            log.debug("progression: pet id unavailable", exc_info=True)
            pet = ""
        return pet if pet and pet != "none" else "gigi"

    # ------------------------------------------------------------ paying

    async def award(self, subject_id: str, source: str, *, ref: str = "") -> Award | None:
        rule = RULES_BY_SOURCE.get(source)
        if rule is None:
            raise UnknownAction(source)
        result = await asyncio.to_thread(self.store.award, subject_id, rule, ref=ref)
        if result is not None:
            await self._announce(result)
        return result

    async def _safe_award(self, subject_id: str, source: str, ref: str = "") -> None:
        try:
            await self.award(subject_id, source, ref=ref)
        except Exception:  # noqa: BLE001 - XP is a garnish; a failed write never breaks a turn
            log.warning("progression: %s for %s not stored", source, subject_id, exc_info=True)

    async def _announce(self, award: Award) -> None:
        if self._bus is None:
            return
        from jarvis.core.events import ProgressionAwarded

        kind = subject_kind(award.subject_id) or "person"
        unlocked = tuple(
            r.reward_id for r in rewards_between(kind, award.level_before, award.level_after)
        )
        try:
            await self._bus.publish(
                ProgressionAwarded(
                    source_layer="progression",
                    seq=award.seq,
                    subject_id=award.subject_id,
                    subject_kind=kind,
                    xp_source=award.source,
                    xp=award.xp,
                    total_xp=award.total_xp,
                    level=award.level_after,
                    previous_level=award.level_before,
                    title=title_for(kind, award.level_after),
                    unlocked=unlocked,
                )
            )
        except Exception:  # noqa: BLE001 - the award is stored; the Verse catches up on its next read
            log.debug("progression: award %s not pushed", award.seq, exc_info=True)

    # ------------------------------------------------------------ world actions

    async def report_action(self, action: str, *, ref: str = "") -> Award | None:
        """A small action inside the Verse, reported by the client."""
        if action not in WORLD_ACTIONS:
            raise UnknownAction(action)
        rule = RULES_BY_SOURCE[action]
        if action == "daily_visit":
            # The server's own calendar day, whatever the client sent.
            ref = datetime.now().astimezone().date().isoformat()
        elif action == "floor_discovered":
            if ref not in VERSE_FLOORS:
                raise UnknownAction(f"floor_discovered:{ref}")
        else:
            ref = ""
        subject = PERSON_ID if rule.kind == "person" else pet_subject(self.current_pet())
        return await self.award(subject, action, ref=ref)

    async def note_agent_hired(self, agent_id: str) -> None:
        if agent_id and agent_id != LEAD_AGENT_ID:
            self._later((PERSON_ID, "agent_hired", f"agent:{agent_id}"))

    # ------------------------------------------------------------ bus handlers

    async def _on_chat_turn(self, event: Any) -> None:
        if getattr(event, "status", "done") != "done":
            return
        turn = str(getattr(event, "turn_id", "") or getattr(event, "session_id", ""))
        ref = f"chat:{turn}" if turn else ""
        self._later(
            (PERSON_ID, "chat_turn", ref),
            (pet_subject(self.current_pet()), "jarvis_answered", ref),
        )

    async def _on_voice_turn(self, event: Any) -> None:
        turn = str(getattr(event, "turn_id", "") or "")
        ref = f"voice:{getattr(event, 'session_id', '')}:{turn}" if turn else ""
        self._later(
            (PERSON_ID, "voice_turn", ref),
            (pet_subject(self.current_pet()), "jarvis_answered", ref),
        )

    async def _on_quest(self, event: Any) -> None:
        quest_id = str(getattr(event, "quest_id", "") or "")
        if not quest_id:
            return
        state = str(getattr(event, "state", ""))
        previous = str(getattr(event, "previous", ""))
        awards: list[tuple[str, str, str]] = []
        if not previous:
            awards.append((PERSON_ID, "quest_posted", quest_id))
        if state == "done" and previous != "done":
            awards.append((PERSON_ID, "quest_completed", quest_id))
            agent_id = str(getattr(event, "agent_id", "") or "")
            if agent_id and agent_id != LEAD_AGENT_ID:
                awards.append((agent_subject(agent_id), "quest_done", quest_id))
        if awards:
            self._later(*awards)

    async def _on_result(self, event: Any) -> None:
        agent_id = str(getattr(event, "agent_id", "") or "")
        if not agent_id or agent_id == LEAD_AGENT_ID or agent_id in {"user", "scheduler"}:
            return
        source = "task_done" if getattr(event, "status", "") == "done" else "task_blocked"
        self._later((agent_subject(agent_id), source, str(getattr(event, "event_id", ""))))

    async def _on_message(self, event: Any) -> None:
        kind = str(getattr(event, "msg_type", ""))
        sender = str(getattr(event, "from_agent", "") or "")
        ref = str(getattr(event, "event_id", "") or "")
        if kind == "ASSIGN" and sender == LEAD_AGENT_ID:
            self._later((pet_subject(self.current_pet()), "delegated", ref))
        elif kind == "ANSWER" and sender and sender not in {LEAD_AGENT_ID, "user", "scheduler"}:
            self._later((agent_subject(sender), "answered_teammate", ref))

    async def _on_mission(self, event: Any) -> None:
        if getattr(event, "status", "") != "approved":
            return
        mission = str(getattr(event, "mission_id", "") or "")
        self._later((PERSON_ID, "mission_completed", mission))

    # ------------------------------------------------------------ reads

    def snapshot(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.store.subjects():
            kind = row["kind"]
            progress = progress_for_xp(int(row["xp"]))
            rows.append(
                {
                    "subject_id": row["subject_id"],
                    "kind": kind,
                    "xp": progress.xp,
                    "level": progress.level,
                    "xp_into_level": progress.into_level,
                    "xp_for_next": progress.for_next,
                    "title": title_for(kind, progress.level),
                    "updated_ms": int(row["updated_ms"]),
                }
            )
        return rows
