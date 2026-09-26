"""Deferred Society questions with a durable five-minute default."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

log = logging.getLogger(__name__)
QUESTION_TIMEOUT_S = 300


class AgentQuestions:
    def __init__(self, service: Any) -> None:
        self._service = service
        self._pending: dict[str, tuple[str, dict[str, Any]]] = {}
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._continuations: set[asyncio.Task[None]] = set()
        self._paused_turns: set[tuple[str, str]] = set()
        self._recovering: asyncio.Task[None] | None = None

    def start_recovery(self) -> None:
        if self._recovering is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._recovering = loop.create_task(self._recover(), name="agent-question-recovery")

    def take_paused_turn(self, session_id: str, turn_id: str) -> bool:
        key = (session_id, turn_id)
        if key not in self._paused_turns:
            return False
        self._paused_turns.remove(key)
        return True

    async def _recover(self) -> None:
        try:
            sessions = await asyncio.to_thread(
                self._service.store.list_sessions, 100_000, surface="society"
            )
            for session in sessions:
                if ":routine:" in session.session_id:
                    continue
                events = await asyncio.to_thread(
                    self._service.store.list_events, session.session_id
                )
                questions: dict[str, dict[str, Any]] = {}
                resolved: dict[str, dict[str, Any]] = {}
                resumed: set[str] = set()
                for event in events:
                    payload = event.get("payload") or {}
                    if event.get("kind") == "user_message" and payload.get("origin") == "question":
                        resumed.add(str(payload.get("question_id") or ""))
                    if event.get("kind") != "notice":
                        continue
                    question_id = str(payload.get("question_id") or "")
                    if payload.get("kind") == "agent_question" and question_id:
                        questions[question_id] = payload
                    elif payload.get("kind") == "question_resolved":
                        resolved[question_id] = payload
                for question_id, payload in questions.items():
                    if question_id in resumed:
                        continue
                    if question_id in resolved:
                        self._start_continuation(session.session_id, payload, resolved[question_id])
                    elif question_id not in self._pending:
                        self._schedule(session.session_id, payload)
        except Exception:
            log.exception("agent question recovery failed")

    def _schedule(self, session_id: str, payload: dict[str, Any]) -> None:
        question_id = str(payload["question_id"])
        self._pending[question_id] = (session_id, payload)
        self._timers[question_id] = asyncio.create_task(
            self._expire(question_id), name=f"agent-question-{question_id[:8]}"
        )

    async def create(
        self,
        session_id: str,
        *,
        agent_name: str,
        question: str,
        options: list[dict[str, str]],
        recommended_index: int,
        recommendation_reason: str,
    ) -> str:
        session = self._service.store.get_session(session_id)
        if session is None or session.surface != "society" or ":routine:" in session_id:
            raise ValueError("Questions are available only in an agent's direct chat")
        if any(sid == session_id for sid, _ in self._pending.values()):
            raise ValueError("This agent already has an unanswered question")
        question_id = uuid.uuid4().hex
        payload: dict[str, Any] = {
            "kind": "agent_question",
            "question_id": question_id,
            "agent_name": agent_name,
            "question": question,
            "options": options,
            "recommended_index": recommended_index,
            "recommendation_reason": recommendation_reason,
            "deadline_ms": int(time.time() * 1000) + QUESTION_TIMEOUT_S * 1000,
            "text": question,
        }
        await self._service.post_notice(session_id, payload)
        running = self._service._running.get(session_id)
        if running is not None:
            self._paused_turns.add((session_id, running.turn_id))
        self._schedule(session_id, payload)
        return question_id

    async def _expire(self, question_id: str) -> None:
        try:
            entry = self._pending.get(question_id)
            if entry is None:
                return
            deadline_ms = int(entry[1]["deadline_ms"])
            await asyncio.sleep(max(0, (deadline_ms - int(time.time() * 1000)) / 1000))
            await self.resolve(entry[0], question_id, source="timeout")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("agent question timeout failed for %s", question_id)

    async def resolve(
        self,
        session_id: str,
        question_id: str,
        *,
        selected_index: int | None = None,
        custom_text: str = "",
        source: str = "user",
    ) -> dict[str, Any] | None:
        if self._recovering is not None and self._recovering is not asyncio.current_task():
            await self._recovering
        entry = self._pending.get(question_id)
        if entry is None or entry[0] != session_id:
            return None
        payload = entry[1]
        options = payload["options"]
        if source == "user" and int(time.time() * 1000) >= int(payload["deadline_ms"]):
            source = "timeout"
        if source == "timeout":
            selected_index = int(payload["recommended_index"])
            custom_text = ""
        elif (selected_index is None) == (not custom_text.strip()):
            raise ValueError("Choose one option or enter your own answer")
        elif selected_index is not None and (selected_index < 0 or selected_index >= len(options)):
            raise ValueError("Unknown option")
        custom_text = custom_text.strip()
        if len(custom_text) > 2_000:
            raise ValueError("Answer is too long")
        self._pending.pop(question_id, None)
        timer = self._timers.pop(question_id, None)
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        answer = custom_text if selected_index is None else options[selected_index]["label"]
        result = {
            "kind": "question_resolved",
            "question_id": question_id,
            "source": source,
            "selected_index": selected_index,
            "answer": answer,
            "text": answer,
        }
        await self._service.post_notice(session_id, result)
        self._start_continuation(session_id, payload, result)
        return result

    def _start_continuation(
        self, session_id: str, question: dict[str, Any], result: dict[str, Any]
    ) -> None:
        task = asyncio.create_task(
            self._continue(session_id, question, result),
            name=f"agent-question-resume-{str(question['question_id'])[:8]}",
        )
        self._continuations.add(task)
        task.add_done_callback(self._continuations.discard)

    async def _continue(
        self, session_id: str, question: dict[str, Any], result: dict[str, Any]
    ) -> None:
        from jarvis.agent_chat.service import SessionBusy

        answer = result["answer"]
        provenance = (
            "the recommended default after five minutes"
            if result["source"] == "timeout"
            else "the user's answer"
        )
        prompt = (
            f"[Answer to your pending question {question['question_id']}]\n"
            f"Question: {question['question']}\n"
            f"Decision ({provenance}): {answer}\n"
            "Continue the original task with this decision. Existing tool permissions and "
            "approval requirements still apply. Do not ask again unless a new material "
            "ambiguity arises."
        )
        while True:
            try:
                await self._service.wait_turn(session_id)
                await self._service.send(
                    session_id,
                    prompt,
                    direct_user=False,
                    question_owned=True,
                    question_id=str(question["question_id"]),
                )
                return
            except SessionBusy:
                await asyncio.sleep(1)
            except Exception:
                log.exception("agent question continuation failed for %s", session_id)
                return

    def close(self) -> None:
        self._paused_turns.clear()
        if self._recovering is not None:
            self._recovering.cancel()
        for task in [*self._timers.values(), *self._continuations]:
            task.cancel()
