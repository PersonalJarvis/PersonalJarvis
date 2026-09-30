"""The loop: collect finished turns, review them in the background, write, refresh.

Triggers (every one fire-and-forget, never on the voice path, AP-9):

* ``review_every_turns`` unreviewed user turns in one conversation;
* an explicit "remember ..." request, reviewed right away so the next turn
  already knows it;
* a call ending (``VoiceSessionEnded``) or ``idle_review_seconds`` of quiet.

Voice turns arrive as ``VoiceTurnCompleted`` (every voice engine publishes it).
Typed turns on Jarvis' own chat arrive through the chat surface's completion
hook (:meth:`JarvisLearningLoop.chat_turn_completed`). Society agents have
their own loop and never feed this one.

Turns stay pending until a review has really looked at them: a failed or
cancelled review hands them back, and a reviewer that keeps failing is retried
with a growing pause instead of on every turn. An explicit remember request
is never lost: without any reviewer, and on shutdown, it is kept in the
user's own words.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

from jarvis.memory.learning.notebook import JarvisNotebook
from jarvis.memory.learning.review import Proposal, Turn, build_prompt, validate

log = logging.getLogger(__name__)

Reviewer = Callable[[str], Awaitable[list[dict[str, Any]] | None]]

#: Turns kept per conversation (reviewed ones serve as context for the next review).
_KEEP_TURNS: Final[int] = 40
#: Earlier, already reviewed turns shown to the reviewer as context.
_CONTEXT_TURNS: Final[int] = 4
#: Below this many characters of user text a review is not worth a model call
#: ("ja", "stopp"); such turns stay pending until more is said.
_MIN_REVIEW_CHARS: Final[int] = 12
#: Conversations tracked at once; the least recently active one goes beyond this.
_MAX_CONVERSATIONS: Final[int] = 32
#: Pause after a failed review, doubled per consecutive failure up to the cap.
_BACKOFF_S: Final[float] = 60.0
_BACKOFF_CAP_S: Final[float] = 3_600.0


@dataclass
class _Conversation:
    turns: list[Turn] = field(default_factory=list)
    unreviewed: int = 0
    requests: list[str] = field(default_factory=list)
    timer: asyncio.TimerHandle | None = None
    last_seen: float = 0.0
    failures: int = 0
    retry_at: float = 0.0
    reviewing: bool = False


class JarvisLearningLoop:
    """See the module docstring."""

    def __init__(
        self,
        notebook: JarvisNotebook,
        reviewer: Reviewer,
        *,
        review_every_turns: int = 6,
        idle_review_seconds: float = 300.0,
        already_known: Callable[[], str] | None = None,
    ) -> None:
        self.notebook = notebook
        self._reviewer = reviewer
        self._every = max(1, int(review_every_turns))
        self._idle_s = max(0.0, float(idle_review_seconds))
        self._already_known = already_known or (lambda: "")
        self._conversations: dict[str, _Conversation] = {}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._subscriptions: list[tuple[type[Any], Any]] = []
        self._bus: Any = None
        self._stopped = False

    # ── wiring ───────────────────────────────────────────────────────────

    def start(self, bus: Any) -> None:
        from jarvis.core.events import VoiceSessionEnded, VoiceTurnCompleted

        if self._bus is not None:
            return
        self._bus = bus
        self._stopped = False
        for event_type, handler in (
            (VoiceTurnCompleted, self._on_voice_turn),
            (VoiceSessionEnded, self._on_voice_ended),
        ):
            bus.subscribe(event_type, handler)
            self._subscriptions.append((event_type, handler))
        self._spawn(asyncio.to_thread(self.notebook.warm), name="jarvis-learning-warm")

    async def stop(self, *, timeout_s: float = 5.0) -> None:
        """Detach, cancel reviews, and keep every explicit request that is still open."""
        self._stopped = True
        for event_type, handler in self._subscriptions:
            try:
                self._bus.unsubscribe(event_type, handler)
            except Exception:  # noqa: BLE001 — teardown reports, never raises
                log.debug("learning: could not detach %s", event_type.__name__, exc_info=True)
        self._subscriptions.clear()
        self._bus = None
        for conversation in self._conversations.values():
            if conversation.timer is not None:
                conversation.timer.cancel()
                conversation.timer = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_s)
        # A cancelled review handed its requests back; keep them now, no model.
        for conversation in self._conversations.values():
            for request in conversation.requests:
                await asyncio.to_thread(self._keep_request, request)
            conversation.requests.clear()

    def _spawn(self, work: Awaitable[Any], *, name: str) -> None:
        try:
            task = asyncio.get_running_loop().create_task(self._guarded(work), name=name)
        except RuntimeError:
            if asyncio.iscoroutine(work):
                work.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _guarded(work: Awaitable[Any]) -> None:
        try:
            await work
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — background learning must never surface
            log.warning("learning: background work failed", exc_info=True)

    # ── inputs ───────────────────────────────────────────────────────────

    @staticmethod
    def _voice_key(event: Any) -> str:
        session = str(getattr(event, "session_id", "") or "").strip()
        return "voice:" + (session or "unknown")

    async def _on_voice_turn(self, event: Any) -> None:
        tools = tuple(str(t) for t in (getattr(event, "tool_calls", ()) or ()))
        self.record(
            self._voice_key(event),
            Turn(
                user=str(getattr(event, "user_text", "") or "").strip(),
                assistant=str(getattr(event, "jarvis_text", "") or "").strip(),
                channel="voice",
                tools=tools,
            ),
        )

    async def _on_voice_ended(self, event: Any) -> None:
        self._schedule(self._voice_key(event), reason="call ended", drop=True, force=True)

    async def chat_turn_completed(self, session: Any, completion: Any) -> None:
        """Jarvis chat surface hook: one typed turn with its tool names."""
        from jarvis.society.memory_intent import user_evidence

        if self._stopped:
            return
        turn = getattr(completion, "turn", None)
        if turn is not None and not getattr(turn, "direct_user", True):
            return  # Routine and agent-injected turns are not the user speaking.
        try:
            events = json.loads(completion.events_json)
        except (TypeError, ValueError):
            log.debug("learning: unreadable chat completion", exc_info=True)
            return
        # What the person typed, never attachments or transport scaffolding:
        # only those words may later serve as evidence.
        typed = [user_evidence(e) for e in events if e.get("kind") == "user_message"]
        user = typed[-1].strip() if typed else ""
        if not user:
            user = user_evidence({"payload": {"text": getattr(turn, "user_text", "")}}).strip()
        answer = "\n".join(
            str((e.get("payload") or {}).get("text") or "")
            for e in events
            if e.get("kind") == "assistant_text"
        ).strip()
        tools = tuple(
            str((e.get("payload") or {}).get("name") or "")
            for e in events
            if e.get("kind") == "tool_call"
        )
        self.record(
            "chat:" + str(getattr(session, "session_id", "") or "unknown"),
            Turn(user=user, assistant=answer, channel="chat", tools=tuple(t for t in tools if t)),
        )

    def record(self, key: str, turn: Turn) -> None:
        """File one finished turn and fire whichever trigger it completes."""
        if not turn.user or self._stopped:
            return
        from jarvis.society.memory_intent import requested_memory

        conversation = self._conversations.get(key)
        if conversation is None:
            self._make_room()
            conversation = self._conversations[key] = _Conversation()
        conversation.last_seen = time.monotonic()
        conversation.turns.append(turn)
        del conversation.turns[:-_KEEP_TURNS]
        conversation.unreviewed = min(conversation.unreviewed + 1, _KEEP_TURNS)
        if requested_memory(turn.user) is not None:
            conversation.requests.append(turn.user)
            self._schedule(key, reason="explicit remember request", force=True)
        elif conversation.unreviewed >= self._every:
            self._schedule(key, reason=f"{conversation.unreviewed} new turns")
        else:
            self._arm_idle(key, conversation)

    def _make_room(self) -> None:
        """Past the cap, review and forget the least recently active conversation."""
        idle = [key for key, c in self._conversations.items() if not c.reviewing]
        if len(self._conversations) < _MAX_CONVERSATIONS or not idle:
            return
        oldest = min(idle, key=lambda key: self._conversations[key].last_seen)
        self._schedule(oldest, reason="evicted", drop=True, force=True)

    # ── triggers ─────────────────────────────────────────────────────────

    def _arm_idle(self, key: str, conversation: _Conversation, delay: float | None = None) -> None:
        if conversation.timer is not None:
            conversation.timer.cancel()
            conversation.timer = None
        wait = self._idle_s if delay is None else delay
        if not wait or self._stopped:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        conversation.timer = loop.call_later(
            wait, lambda: self._schedule(key, reason="quiet conversation", force=True)
        )

    def _schedule(
        self, key: str, *, reason: str, drop: bool = False, force: bool = False
    ) -> None:
        """Start a background review; ``drop`` forgets the conversation after it.

        ``force`` ignores the failure pause (a call ended, the user asked to
        remember, the conversation went quiet); turn counting respects it.
        """
        conversation = self._conversations.get(key)
        if conversation is None or self._stopped:
            return
        if conversation.timer is not None:
            conversation.timer.cancel()
            conversation.timer = None
        if not conversation.unreviewed:
            if drop and not conversation.reviewing:
                self._conversations.pop(key, None)
            return
        if not force and time.monotonic() < conversation.retry_at:
            self._arm_idle(key, conversation, conversation.retry_at - time.monotonic())
            return
        self._spawn(self._review_then(key, reason, drop), name=f"jarvis-learning-{key[:24]}")

    async def _review_then(self, key: str, reason: str, drop: bool) -> None:
        try:
            await self.review(key, reason=reason)
        finally:
            conversation = self._conversations.get(key)
            if drop and conversation is not None:
                if conversation.unreviewed and not self._stopped:
                    # The review failed; keep the turns until the pause is over.
                    self._arm_idle(
                        key, conversation, max(1.0, conversation.retry_at - time.monotonic())
                    )
                elif not conversation.unreviewed:
                    self._conversations.pop(key, None)

    # ── the review ───────────────────────────────────────────────────────

    async def review(self, key: str, *, reason: str = "manual") -> int:
        """Review the unreviewed turns of ``key``; returns the changes written."""
        async with self._lock:  # One writer; a later review sees the earlier result.
            conversation = self._conversations.get(key)
            if conversation is None or not conversation.unreviewed:
                return 0
            count = conversation.unreviewed
            turns = conversation.turns[-count:]
            requests = list(conversation.requests)
            if not requests and sum(len(t.user) for t in turns) < _MIN_REVIEW_CHARS:
                return 0  # Too little said; wait for more.
            context = conversation.turns[-count - _CONTEXT_TURNS : -count]
            conversation.unreviewed = 0
            conversation.requests.clear()
            conversation.reviewing = True
            answered = handled = False
            try:
                written, answered = await self._review_turns(turns, context, requests, reason)
                handled = True  # Every request was saved by the review or kept verbatim.
            finally:
                conversation.reviewing = False
                if not handled:
                    conversation.requests[:0] = requests
                if not answered:
                    # Hand the turns back for the next attempt, after a pause.
                    conversation.unreviewed = min(conversation.unreviewed + count, _KEEP_TURNS)
                    conversation.failures += 1
                    conversation.retry_at = time.monotonic() + min(
                        _BACKOFF_CAP_S, _BACKOFF_S * 2 ** (conversation.failures - 1)
                    )
                else:
                    conversation.failures = 0
                    conversation.retry_at = 0.0
            if written:
                log.info("learning: %d notebook change(s) from %s", written, key)
                await asyncio.to_thread(self.notebook.warm)
            return written

    async def _review_turns(
        self, turns: list[Turn], context: list[Turn], requests: list[str], reason: str
    ) -> tuple[int, bool]:
        """``(changes written, a reviewer answered)``; raises only on I/O faults."""
        entries = await asyncio.to_thread(self.notebook.entries)
        try:
            known = await asyncio.to_thread(self._already_known)
        except Exception:  # noqa: BLE001 — the known profile only reduces duplicates
            log.debug("learning: known profile unavailable", exc_info=True)
            known = ""
        prompt = build_prompt(
            turns,
            context=context,
            entries=entries,
            usage=self.notebook.usage(entries),
            already_known=known,
        )
        log.info("learning: reviewing %d turn(s) (%s)", len(turns), reason)
        try:
            raw = await self._reviewer(prompt)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a provider fault is a failed review, retried later
            log.warning("learning: reviewer failed", exc_info=True)
            raw = None
        accepted, rejected = validate(
            raw or [],
            user_texts=[t.user for t in (*context, *turns)],
            entries=entries,
            trusted=known,
        )
        for why in rejected:
            log.info("learning: rejected a proposal — %s", why)
        written = 0
        satisfied: set[str] = set()
        for proposal in accepted:
            written += await asyncio.to_thread(self._apply_proposal, proposal, reason)
            folded = " ".join(proposal.evidence.split()).casefold()
            satisfied.update(r for r in requests if folded in " ".join(r.split()).casefold())
        for request in requests:
            if request not in satisfied:
                written += await asyncio.to_thread(self._keep_request, request)
        return written, raw is not None

    def _apply_proposal(self, proposal: Proposal, reason: str) -> int:
        return self._write(
            target=proposal.target,
            operation=proposal.operation,
            text=proposal.text,
            entry_id=proposal.entry_id,
            importance=proposal.importance,
            origin="review",
            evidence=proposal.evidence,
            source=f"review: {reason}",
            expected=proposal.before if proposal.operation != "add" else None,
        )

    def _write(self, **change: Any) -> int:
        try:
            written = self.notebook.apply(**change)
        except (ValueError, OSError) as exc:  # filelock.Timeout is an OSError subclass too
            log.info("learning: could not apply a %s — %s", change.get("operation"), exc)
            return 0
        return 1 if written is not None else 0

    def _keep_request(self, request: str) -> int:
        """Keep an explicit remember request verbatim when no review covered it."""
        from jarvis.memory.learning.guard import refusal
        from jarvis.society.memory_books import classify
        from jarvis.society.memory_intent import requested_memory

        content = requested_memory(request) or ""
        if not content:
            return 0  # "remember that" without a resolvable referent: never guess.
        text = f"On {date.today().isoformat()} the user asked to remember: {content}"
        if refusal(text):
            log.info("learning: explicit remember request refused by the guard")
            return 0
        return self._write(
            target="user" if classify(content) == "user" else "memory",
            operation="add",
            text=text,
            importance=9,
            origin="user",
            evidence=request,
            source="explicit remember request",
        )

    def pending(self) -> dict[str, int]:
        """Unreviewed turns per conversation (diagnostics and tests)."""
        return {key: c.unreviewed for key, c in self._conversations.items() if c.unreviewed}


# ── the process-wide loop ─────────────────────────────────────────────────

_loop: JarvisLearningLoop | None = None


def current_loop() -> JarvisLearningLoop | None:
    return _loop


def start_learning(config: Any, bus: Any) -> JarvisLearningLoop | None:
    """Wire the loop for this process; ``None`` when it is switched off."""
    global _loop
    from jarvis.memory.learning import notebook as notebook_module
    from jarvis.memory.learning.review import ModelReviewer

    cfg = config.memory.learning
    if not cfg.enabled:
        notebook_module.set_active(None)
        return None
    if _loop is not None:
        return _loop
    book = notebook_module.notebook_from_config(config)

    def _known() -> str:
        from jarvis.brain.identity_card import identity_card_block

        return identity_card_block(config) or ""

    _loop = JarvisLearningLoop(
        book,
        ModelReviewer(config),
        review_every_turns=cfg.review_every_turns,
        idle_review_seconds=cfg.idle_review_seconds,
        already_known=_known,
    )
    _loop.start(bus)
    notebook_module.set_active(book)
    return _loop


async def stop_learning() -> None:
    global _loop
    from jarvis.memory.learning import notebook as notebook_module

    loop, _loop = _loop, None
    notebook_module.set_active(None)
    if loop is not None:
        await loop.stop()
