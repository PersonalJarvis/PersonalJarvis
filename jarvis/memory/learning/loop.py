"""The loop: collect finished turns, keep what matters, cheaply and reliably.

What it costs:

* nothing for an ordinary turn. A deterministic filter
  (:mod:`jarvis.memory.learning.signals`) marks the few turns in which the
  user talks about themselves, a preference, a correction or a plan; a
  conversation without such a turn is never shown to a model;
* nothing for "remember that X": an explicit request with its content is
  saved at once in the user's own words, without a model, so it can never be
  lost to a provider failure;
* one small model call per conversation that did hold a signal, when the
  call ends (``VoiceSessionEnded``), after ``idle_review_seconds`` of quiet,
  or after ``review_every_turns`` turns in a very long conversation. A bare
  "remember that" (pointing at something said before) is reviewed at once.

Voice turns arrive as ``VoiceTurnCompleted`` (every voice engine publishes it).
Typed turns on Jarvis' own chat arrive through the chat surface's completion
hook (:meth:`JarvisLearningLoop.chat_turn_completed`). Society agents have
their own loop and never feed this one.

Turns stay pending until a review has really looked at them: a failed or
cancelled review hands them back, and a failing reviewer is retried after a
growing pause, never on every turn. Nothing here runs on the voice path.
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
from jarvis.memory.learning.review import Proposal, Turn, build_prompt, excerpt, validate
from jarvis.memory.learning.signals import has_signal

log = logging.getLogger(__name__)

Reviewer = Callable[[str], Awaitable[list[dict[str, Any]] | None]]

#: Turns kept per conversation (enough for any review window).
_KEEP_TURNS: Final[int] = 40
#: Signal turns shown to one review; a conversation rarely has more.
_MAX_SIGNAL_TURNS: Final[int] = 8
#: Conversations tracked at once; the least recently active one goes beyond this.
_MAX_CONVERSATIONS: Final[int] = 32
#: Pause after a failed review, doubled per consecutive failure up to the cap.
_BACKOFF_S: Final[float] = 60.0
_BACKOFF_CAP_S: Final[float] = 3_600.0


@dataclass
class _Conversation:
    turns: list[Turn] = field(default_factory=list)
    #: Parallel to ``turns``: did the signal filter pick this turn?
    signals: list[bool] = field(default_factory=list)
    unreviewed: int = 0
    timer: asyncio.TimerHandle | None = None
    last_seen: float = 0.0
    failures: int = 0
    retry_at: float = 0.0
    reviewing: bool = False

    def picked(self) -> list[int]:
        """Indices of the unreviewed turns the signal filter picked."""
        start = len(self.turns) - self.unreviewed
        return [i for i in range(start, len(self.turns)) if self.signals[i]]


class JarvisLearningLoop:
    """See the module docstring."""

    def __init__(
        self,
        notebook: JarvisNotebook,
        reviewer: Reviewer,
        *,
        review_every_turns: int = 30,
        idle_review_seconds: float = 300.0,
        compactor: Reviewer | None = None,
    ) -> None:
        self.notebook = notebook
        self._reviewer = reviewer
        self._compactor = compactor
        self._every = max(1, int(review_every_turns))
        self._idle_s = max(0.0, float(idle_review_seconds))
        self._conversations: dict[str, _Conversation] = {}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._subscriptions: list[tuple[type[Any], Any]] = []
        self._bus: Any = None
        self._stopped = False
        #: Model calls made (diagnostics and tests).
        self.review_calls = 0
        self.compact_calls = 0

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
        self._spawn(self._warm_and_compact(), name="jarvis-learning-warm")

    async def _warm_and_compact(self) -> None:
        await asyncio.to_thread(self.notebook.warm)
        await self.compact()

    async def stop(self, *, timeout_s: float = 5.0) -> None:
        """Detach and let running work finish briefly; explicit requests are already saved."""
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
        if tasks:
            # Let a deterministic save that is already writing finish; a model
            # review still waiting on its provider is cancelled.
            _, pending = await asyncio.wait(tasks, timeout=min(1.0, timeout_s))
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=timeout_s)

    def _spawn(self, work: Awaitable[Any], *, name: str) -> None:
        try:
            task = asyncio.get_running_loop().create_task(self._guarded(work), name=name)
        except RuntimeError:  # no event loop (shutdown / sync caller): drop the work cleanly
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
        request = requested_memory(turn.user)
        # A self-contained request is saved right here; only a bare "remember
        # that" still needs the model to see what "that" was.
        signal = request == "" or (request is None and has_signal(turn.user))
        conversation.turns.append(turn)
        conversation.signals.append(signal)
        del conversation.turns[:-_KEEP_TURNS]
        del conversation.signals[:-_KEEP_TURNS]
        conversation.unreviewed = min(conversation.unreviewed + 1, _KEEP_TURNS)
        if request == "" and len(conversation.turns) > 1:
            # "Remember that" points at what was said just before: show it too.
            conversation.signals[-2] = True
            conversation.unreviewed = max(conversation.unreviewed, 2)
        if request:
            self._spawn(self._remember(turn.user, request), name="jarvis-learning-remember")
        if request == "":
            self._schedule(key, reason="remember request", force=True)
        elif conversation.unreviewed >= self._every:
            self._schedule(key, reason=f"{conversation.unreviewed} turns")
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
        except RuntimeError:  # no event loop: no idle timer to arm, nothing lost
            return
        conversation.timer = loop.call_later(
            wait, lambda: self._schedule(key, reason="quiet conversation", force=True)
        )

    def _schedule(
        self, key: str, *, reason: str, drop: bool = False, force: bool = False
    ) -> None:
        """Start a background review; ``drop`` forgets the conversation after it.

        A conversation whose unreviewed turns hold no signal is settled here,
        without a task and without a model call. ``force`` ignores the pause
        after a failed review (a call ended, the conversation went quiet).
        """
        conversation = self._conversations.get(key)
        if conversation is None or self._stopped:
            return
        if conversation.timer is not None:
            conversation.timer.cancel()
            conversation.timer = None
        if conversation.unreviewed and not conversation.picked() and not conversation.reviewing:
            conversation.unreviewed = 0  # Nothing worth a model call was said.
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
        """Review the picked turns of ``key``; returns the changes written."""
        async with self._lock:  # One writer; a later review sees the earlier result.
            conversation = self._conversations.get(key)
            if conversation is None or not conversation.unreviewed:
                return 0
            count = conversation.unreviewed
            picked = conversation.picked()[-_MAX_SIGNAL_TURNS:]
            if not picked:
                conversation.unreviewed = 0
                return 0
            said = excerpt(conversation.turns, picked)
            conversation.unreviewed = 0
            conversation.reviewing = True
            answered = False
            written = 0
            try:
                written, answered = await self._review_said(said, reason)
            finally:
                conversation.reviewing = False
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
                await self._compact_locked()
                await asyncio.to_thread(self.notebook.warm)
            return written

    async def _review_said(self, said: list[dict[str, str]], reason: str) -> tuple[int, bool]:
        """``(changes written, a reviewer answered)``; raises only on I/O faults."""
        entries = await asyncio.to_thread(self.notebook.entries)
        prompt = build_prompt(said, entries=entries, usage=self.notebook.usage(entries))
        log.info("learning: reviewing %d picked turn(s) (%s)", len(said), reason)
        self.review_calls += 1
        try:
            raw = await self._reviewer(prompt)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a provider fault is a failed review, retried later
            log.warning("learning: reviewer failed", exc_info=True)
            raw = None
        if raw is None:
            return 0, False
        # Evidence may come from the picked turns only: nothing else was shown.
        accepted, rejected = validate(
            raw, user_texts=[row["user"] for row in said], entries=entries
        )
        for why in rejected:
            log.info("learning: rejected a proposal — %s", why)
        written = 0
        for proposal in accepted:
            written += await asyncio.to_thread(self._apply_proposal, proposal, reason)
        return written, True

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

    def _keep_request(self, said: str, content: str) -> int:
        """Save an explicit remember request in the user's own words, dated."""
        from jarvis.memory.learning.guard import MAX_ENTRY_CHARS, refusal
        from jarvis.society.memory_books import classify

        prefix = f"{date.today().isoformat()} (the user's words): "
        room = MAX_ENTRY_CHARS - len(prefix)
        if len(content) > room:
            content = content[: room - 1].rsplit(" ", 1)[0] + "…"
        text = prefix + content
        if refusal(text):
            log.info("learning: explicit remember request refused by the guard")
            return 0
        return self._write(
            target="user" if classify(content) == "user" else "memory",
            operation="add",
            text=text,
            importance=9,
            origin="user",
            evidence=said,
            source="explicit remember request",
            # The user asked for it: a full notebook merges later, it never
            # refuses this.
            enforce_budget=False,
        )

    async def _remember(self, said: str, content: str) -> None:
        if await asyncio.to_thread(self._keep_request, said, content):
            await self.compact()

    # ── compaction ───────────────────────────────────────────────────────

    async def compact(self, *, force: bool = False) -> int:
        """Drop duplicates (free) and merge a notebook past its fill trigger."""
        async with self._lock:
            return await self._compact_locked(force=force)

    async def _compact_locked(self, *, force: bool = False) -> int:
        from jarvis.memory.learning import compact

        changed = 0
        books = await asyncio.to_thread(self.notebook.entries)
        for target, rows in books.items():
            for dropped, kept in compact.duplicates(rows):
                changed += await asyncio.to_thread(
                    self._write,
                    target=target,
                    operation="remove",
                    entry_id=dropped,
                    source=f"compaction: duplicate of {kept}",
                )
        if changed:
            books = await asyncio.to_thread(self.notebook.entries)
        if self._compactor is None:
            return changed
        state = await asyncio.to_thread(self.notebook.read_state)
        now = time.time()
        for target, (used, budget) in self.notebook.usage(books).items():
            last = float(state.get(f"compacted:{target}", 0.0))
            if used < budget * compact.FILL_TRIGGER:
                continue
            if not force and now - last < compact.COOLDOWN_S:
                continue
            state[f"compacted:{target}"] = now
            await asyncio.to_thread(self.notebook.write_state, state)
            changed += await self._merge(target, books[target], used, budget)
        return changed

    async def _merge(self, target: str, rows: list[Any], used: int, budget: int) -> int:
        from jarvis.memory.learning import compact

        prompt = compact.build_prompt(rows, target=target, used=used, budget=budget)
        self.compact_calls += 1
        log.info("learning: compacting %s (%d/%d chars)", target, used, budget)
        try:
            raw = await self._compactor(prompt) if self._compactor else None
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a failed merge leaves the notebook as it was
            log.warning("learning: compaction failed", exc_info=True)
            return 0
        if not isinstance(raw, dict):
            return 0
        merges, outdated, rejected = compact.validate(raw, rows)
        for why in rejected:
            log.info("learning: rejected a compaction — %s", why)
        texts = {e.id: e.text for e in rows}
        changed = 0
        for merge in merges:
            added = await asyncio.to_thread(
                self._write,
                target=target,
                operation="add",
                text=merge.text,
                importance=merge.importance,
                origin="compaction",
                evidence=", ".join(merge.sources),
                source="compaction: merge",
                enforce_budget=False,
            )
            if not added:
                continue  # Keep the sources when the merged entry did not land.
            changed += added
            for source in merge.sources:
                changed += await asyncio.to_thread(
                    self._write,
                    target=target,
                    operation="remove",
                    entry_id=source,
                    expected=texts[source],
                    source="compaction: merged",
                )
        for entry_id in outdated:
            changed += await asyncio.to_thread(
                self._write,
                target=target,
                operation="remove",
                entry_id=entry_id,
                expected=texts[entry_id],
                source="compaction: outdated",
            )
        return changed

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
    from jarvis.memory.learning import compact
    from jarvis.memory.learning import notebook as notebook_module
    from jarvis.memory.learning.review import ModelReviewer

    cfg = config.memory.learning
    if not cfg.enabled:
        notebook_module.set_active(None)
        return None
    if _loop is not None:
        return _loop
    book = notebook_module.notebook_from_config(config)
    _loop = JarvisLearningLoop(
        book,
        ModelReviewer(config),
        review_every_turns=cfg.review_every_turns,
        idle_review_seconds=cfg.idle_review_seconds,
        compactor=ModelReviewer(
            config,
            system=compact.system_prompt,
            parser=compact.parse,
            max_tokens=compact.COMPACT_MAX_TOKENS,
            label="JarvisLearningCompaction",
        ),
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
