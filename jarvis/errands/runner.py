"""ErrandRunner — the loop that finishes an errand (C1..C11).

The shape, in one view::

    start(goal)
      ├─ gather context from the user's own tools            (C9)
      ├─ collect open questions and ask them ONCE            (C10)  ─> NEEDS_INPUT
      └─ write the plan                                      (C8)
           │
           └─ loop, with no step cap and no clock:           (C1)
                ├─ carry out the next leg with real tools
                ├─ persist immediately                       (C1: survives restart)
                ├─ ask the verifier whether it is done       (C3: proof, not claim)
                ├─ measure progress                          (C2: evidence, not counters)
                └─ on a wall: think again before giving up   (C4)

Why the loop is written here rather than handed to the model as a long prompt:
every one of those rules is a *decision the model would otherwise make about
itself*. A model asked "are you finished?" says yes; a model asked "should you
keep going?" eventually says no. The rules that matter are therefore enforced
by this code — the model supplies judgement inside each phase, never the
control flow between them. That is the same reasoning as
``jarvis/brain/spawn_gate.py``: a tool description is advice, enforcement is
enforcement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from . import context_gate, progress
from .context_gate import MAX_GATHER_ROUNDS, ContextVerdict
from .prompts import (
    clarify_prompt,
    context_prompt,
    plan_prompt,
    recheck_prompt,
    score_prompt,
    step_prompt,
    verify_prompt,
)
from .schema import ContextFact, Errand, ErrandState, Evidence, PlanStep, StepRecord
from .store import ErrandStore

log = logging.getLogger(__name__)

#: A leg writes proof behind this marker. Deliberately a literal marker rather
#: than a model-chosen JSON shape: the working phase also makes real tool calls,
#: and forcing structured output there would cost the tool loop.
#:
#: The marker is matched ANYWHERE in a line, not only at its start. The prompt
#: asks for its own line and a model usually complies — but "Booked it.
#: EVIDENCE: ref XY123" is the natural way to write a sentence, and a
#: line-anchored pattern silently recorded no proof for it. Every leg then
#: looked fruitless, so healthy runs were declared stalled and reported as
#: impossible. Caught by test_every_leg_is_persisted_as_it_happens.
_EVIDENCE_RE: Final[re.Pattern[str]] = re.compile(
    r"EVIDENCE:\s*(.+)$", re.IGNORECASE | re.MULTILINE
)

#: The ONE escape hatch out of an autonomous run (C10 + C11): something that
#: only the user can supply — money authorisation, or a second factor that
#: lives on their phone. Everything else is an obstacle, and obstacles are
#: sub-goals (C5), so this marker is narrow on purpose.
_NEEDS_USER_RE: Final[re.Pattern[str]] = re.compile(
    r"NEEDS-USER:\s*(.+)$", re.IGNORECASE | re.MULTILINE
)

#: How many times a stall may be answered with "there is another route" before
#: the run stops anyway. Each re-check is followed by real attempts, so this is
#: roughly three times the stall threshold in legs — enough for a genuine
#: detour, bounded for a model that will always claim one more idea exists.
#: The counter resets whenever new evidence lands, so a re-check that actually
#: works costs nothing.
MAX_RECHECKS: Final[int] = 2

#: Runaway backstop. This is NOT a work budget and must never be read as one —
#: C1 is explicit that an errand ends on an outcome, not on a counter, and the
#: stall detector is what stops fruitless work.
#:
#: It exists for one failure mode the stall detector cannot see: a model that
#: emits a fresh EVIDENCE line every single leg. Every leg then looks like
#: progress, nothing ever completes, and the run bills forever. Set far beyond
#: any plausible real errand, so reaching it means something is broken rather
#: than merely long — which is why the outcome text says exactly that instead
#: of pretending the task was too big.
MAX_LEGS_BACKSTOP: Final[int] = 200


@dataclass(frozen=True, slots=True)
class LegOutcome:
    """What one phase produced."""

    text: str
    tools_used: tuple[str, ...] = ()


class LegExecutor(Protocol):
    """Runs one phase against a brain.

    A protocol rather than a concrete dependency so the whole control flow is
    testable without a model: the tests drive the runner with scripted legs and
    assert the DECISIONS, which is where the behaviour lives.
    """

    async def __call__(
        self,
        *,
        system_prompt: str,
        instruction: str,
        with_tools: bool,
    ) -> LegOutcome: ...


@dataclass
class ErrandRunner:
    """Drives errands from a goal to a proven outcome."""

    store: ErrandStore
    execute_leg: LegExecutor
    #: Called whenever the errand changes state, for the UI and for voice.
    #: Never allowed to break a run — exceptions are swallowed and logged.
    on_update: Callable[[Errand], Awaitable[None]] | None = None
    #: Renders the code-assembled inventory of places context could live
    #: (C13 move 1). Optional and never allowed to break a run: without it the
    #: gather prompt simply carries no SOURCES block, the pre-C13 behaviour.
    context_sources: Callable[[], Awaitable[str]] | None = None
    #: Persists what a finished errand learned (C14) — durable facts and the
    #: user's own answers. Called once per terminal state, after the terminal
    #: persist; the callable owns all filtering (e.g. skipping CANCELLED).
    keep_learnings: Callable[[Errand], Awaitable[None]] | None = None
    max_rechecks: int = MAX_RECHECKS
    max_legs_backstop: int = MAX_LEGS_BACKSTOP
    max_gather_rounds: int = MAX_GATHER_ROUNDS
    _cancelled: set[str] = field(default_factory=set)
    #: Strong references to in-flight loops. The runner owns its background
    #: work: a caller that had to remember create_task-and-hold would be the
    #: classic fire-and-forget asyncio bug waiting to happen (and was — the
    #: start_errand tool held a detach branch that was never reachable).
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, goal: str, *, trace_id: str = "", language: str = "") -> Errand:
        """Open a new errand: gather context, ask once, plan — then detach.

        Returns as soon as the errand is under way (or has questions). The
        loop itself continues as a background task owned by this runner —
        "book me a flight" must never hold the conversation turn hostage
        until the flight is booked.
        """
        errand = Errand(
            id=str(uuid.uuid4()), goal=goal.strip(), trace_id=trace_id, language=language
        )
        await self._persist(errand)

        errand, verdict = await self._gather_context(errand)  # C9 + C13
        errand = await self._clarify(errand, must_ask=verdict.questions)  # C10
        if errand.state is ErrandState.NEEDS_INPUT:
            return errand
        errand = await self._plan(errand)
        self._detach(self.run(errand), errand.id)
        return errand

    async def provide_answers(self, errand_id: str, answers: str) -> Errand | None:
        """The user answered the one clarification round — now work alone.

        Same contract as ``start``: returns once the errand is planned and
        moving again; the loop runs detached.
        """
        errand = await self.store.get(errand_id)
        if errand is None:
            return None
        combined = f"{errand.answers}\n{answers}".strip() if errand.answers else answers
        errand = errand.with_state(ErrandState.PLANNING, answers=combined, open_questions=())
        await self._persist(errand)
        errand = await self._plan(errand)
        self._detach(self.run(errand), errand.id)
        return errand

    async def cancel(self, errand_id: str, reason: str = "the user stopped it") -> Errand | None:
        """Stop an errand. The running loop notices at its next leg boundary."""
        self._cancelled.add(errand_id)
        errand = await self.store.get(errand_id)
        if errand is None:
            return None
        errand = errand.with_state(ErrandState.CANCELLED, outcome=reason)
        await self._persist(errand)
        return errand

    async def resume_open(self) -> list[Errand]:
        """Pick up every unfinished errand after a restart (C1).

        A restart is not a failure and must not read as one: an errand that was
        mid-flight goes straight back to running (detached, so a boot never
        waits on a booking), and one that was waiting on the user stays
        waiting.
        """
        resumed: list[Errand] = []
        for errand in await self.store.list_open():
            if errand.state is ErrandState.NEEDS_INPUT:
                resumed.append(errand)  # still the user's move
                continue
            log.info("errand %s: resuming after restart", errand.id)
            self._detach(self._plan_and_run(errand), errand.id)
            resumed.append(errand)
        return resumed

    async def join(self) -> None:
        """Wait for every detached loop to finish. Tests and shutdown only."""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------------
    # Phases
    # ------------------------------------------------------------------

    async def _gather_context(self, errand: Errand) -> tuple[Errand, ContextVerdict]:
        """C9 + C13 — look it up, measure it, and look AGAIN before asking.

        Each round: gather with tools, score with a separate tool-less leg,
        then let the gate judge. A round that leaves decisive facts uncertain
        earns exactly one more look — pointed at the unresolved facts and at
        sources not yet probed — and the loop stops early when another look
        changed nothing, because at that point asking the user is the honest
        move, not a failure.
        """
        sources = await self._render_sources()
        verdict = context_gate.assess(())
        unresolved: tuple[str, ...] = ()
        for _ in range(self.max_gather_rounds):
            outcome = await self._leg(
                system_prompt=context_prompt(errand, sources=sources, unresolved=unresolved),
                instruction=f"Find out what you can about: {errand.goal}",
                with_tools=True,
            )
            found = _after_marker(outcome.text, "FOUND:") or outcome.text.strip()
            errand = errand.model_copy(
                update={
                    "gathered_context": _strip_ledger(found),
                    "facts": _parse_facts(outcome.text),
                }
            )
            if errand.facts:
                errand = await self._score_facts(errand)
            await self._persist(errand)
            verdict = context_gate.assess(errand.facts)
            if verdict.proceed:
                return errand, verdict
            still_uncertain = tuple(f.statement for f in verdict.uncertain)
            if still_uncertain == unresolved:
                break  # another look moved nothing — stop burning rounds
            unresolved = still_uncertain
        return errand, verdict

    async def _score_facts(self, errand: Errand) -> Errand:
        """C13 — a separate leg rates the ledger; the gate decides on the numbers.

        A fact the scorer failed to rate keeps confidence 0.0 and therefore
        reads as "ask" when decisive — garbage from the scorer must never
        green-light an assumption, the same safe direction as an unparseable
        verifier verdict never completing an errand.
        """
        outcome = await self._leg(
            system_prompt=score_prompt(errand),
            instruction="Rate each gathered fact.",
            with_tools=False,
        )
        data = _parse_json(outcome.text)
        facts = list(errand.facts)
        for entry in data.get("scores") or []:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("index", -1))
                confidence = float(entry.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(facts):
                continue
            facts[idx] = facts[idx].model_copy(
                update={
                    "confidence": min(max(confidence, 0.0), 1.0),
                    "question": str(entry.get("question", "") or "").strip(),
                }
            )
        return errand.model_copy(update={"facts": tuple(facts)})

    async def _render_sources(self) -> str:
        if self.context_sources is None:
            return ""
        try:
            return await self.context_sources()
        except Exception:  # noqa: BLE001 — the inventory must never break a start
            log.warning("errand: context source inventory failed", exc_info=True)
            return ""

    async def _clarify(self, errand: Errand, *, must_ask: tuple[str, ...] = ()) -> Errand:
        """C10 — one round of questions, or none at all.

        The gate's questions (``must_ask``) are asked unconditionally — the
        model may ADD what only the user's head holds, but it can never veto a
        measured uncertainty by returning an empty list.
        """
        outcome = await self._leg(
            system_prompt=clarify_prompt(errand, must_ask=must_ask),
            instruction="What do you still need from the user?",
            with_tools=False,
        )
        data = _parse_json(outcome.text)
        extra = tuple(str(q).strip() for q in (data.get("questions") or []) if str(q).strip())
        questions = tuple(dict.fromkeys((*must_ask, *extra)))
        if not questions:
            return errand
        errand = errand.with_state(
            ErrandState.NEEDS_INPUT,
            open_questions=questions,
            asked_questions=tuple(dict.fromkeys((*errand.asked_questions, *questions))),
            outcome="Waiting for the user to answer the opening questions.",
        )
        await self._persist(errand)
        return errand

    async def _plan(self, errand: Errand) -> Errand:
        """C8 — write the step plan (a fast judgement leg) and mark it running."""
        if not errand.plan:
            outcome = await self._leg(
                system_prompt=plan_prompt(errand),
                instruction="Write the plan.",
                with_tools=False,
            )
            data = _parse_json(outcome.text)
            steps = tuple(
                PlanStep(intent=str(s.get("intent", "")).strip())
                for s in (data.get("steps") or [])
                if str(s.get("intent", "")).strip()
            )
            errand = errand.model_copy(update={"plan": steps})
        errand = errand.with_state(ErrandState.RUNNING)
        await self._persist(errand)
        return errand

    async def _plan_and_run(self, errand: Errand) -> Errand:
        """Plan (if the record has none yet), then run alone. Resume path."""
        return await self.run(await self._plan(errand))

    def _detach(self, loop: Coroutine[Any, Any, Errand], errand_id: str) -> None:
        """Continue an errand as a background task the runner holds on to."""
        task = asyncio.create_task(loop, name=f"errand-{errand_id[:8]}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def run(self, errand: Errand) -> Errand:
        """The loop. No step cap, no clock — it ends on an outcome (C1)."""
        rechecks_used = 0

        while True:
            if errand.id in self._cancelled:
                return await self._finish(errand, ErrandState.CANCELLED, "the user stopped it")

            if len(errand.steps) >= self.max_legs_backstop:
                return await self._finish(
                    errand,
                    ErrandState.STALLED,
                    (
                        f"Stopped after {len(errand.steps)} steps. Every step "
                        "reported progress but the goal was never reached, "
                        "which means the run was not measuring itself "
                        "honestly — this is a fault to investigate, not a task "
                        "that was too large."
                    ),
                )

            errand = await self._work_one_leg(errand)

            # The one legitimate mid-run interruption (C10/C11).
            pending = _NEEDS_USER_RE.search(errand.steps[-1].summary if errand.steps else "")
            if pending:
                question = pending.group(1).strip()
                errand = errand.with_state(
                    ErrandState.NEEDS_INPUT,
                    open_questions=(question,),
                    asked_questions=tuple(dict.fromkeys((*errand.asked_questions, question))),
                    outcome=f"Needs the user: {question}",
                )
                await self._persist(errand)
                return errand

            verdict = await self._verify(errand)  # C3
            if verdict.get("done") is True:
                proof = str(verdict.get("proof", "")).strip()
                return await self._finish(
                    errand,
                    ErrandState.COMPLETED,
                    proof or "The goal was reached.",
                )

            errand = _mark_step_done(errand)

            assessment = progress.assess(errand.steps)  # C2
            if not assessment.stalled:
                if errand.steps and errand.steps[-1].evidence:
                    rechecks_used = 0  # real movement re-earns the re-checks
                continue

            # C4 — nothing is reported impossible until it has been re-checked.
            if rechecks_used >= self.max_rechecks:
                return await self._finish(errand, ErrandState.STALLED, assessment.reason)
            rechecks_used += 1
            recheck = await self._recheck(errand, assessment.reason)
            if recheck.get("route_exists") is True:
                move = str(recheck.get("next_move", "")).strip()
                log.info("errand %s: re-check found a route — %s", errand.id, move)
                errand = _append_plan_step(errand, move or "try the alternative route")
                continue
            return await self._finish(
                errand,
                ErrandState.IMPOSSIBLE,
                str(recheck.get("why_impossible", "")).strip() or assessment.reason,
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _work_one_leg(self, errand: Errand) -> Errand:
        """Carry out the next step and record it — durably, immediately."""
        intent = _next_intent(errand)
        outcome = await self._leg(
            system_prompt=step_prompt(errand, intent),
            instruction=intent,
            with_tools=True,
        )
        evidence = tuple(
            Evidence(
                kind="observed",
                detail=detail.strip(),
                source=", ".join(outcome.tools_used),
            )
            for detail in _EVIDENCE_RE.findall(outcome.text)
        )
        summary = _EVIDENCE_RE.sub("", outcome.text).strip()
        step = StepRecord(
            index=len(errand.steps),
            intent=intent,
            summary=summary or outcome.text.strip(),
            tools_used=outcome.tools_used,
            evidence=evidence,
            fingerprint=progress.fingerprint(
                tools_used=outcome.tools_used,
                evidence_details=[e.detail for e in evidence],
                summary=summary,
            ),
        )
        errand = errand.model_copy(update={"steps": (*errand.steps, step)}).with_state(errand.state)
        # C1's restart guarantee is exactly as good as this line.
        await self._persist(errand)
        return errand

    async def _verify(self, errand: Errand) -> dict[str, Any]:
        outcome = await self._leg(
            system_prompt=verify_prompt(errand),
            instruction="Is this errand genuinely finished?",
            with_tools=False,
        )
        return _parse_json(outcome.text)

    async def _recheck(self, errand: Errand, obstacle: str) -> dict[str, Any]:
        outcome = await self._leg(
            system_prompt=recheck_prompt(errand, obstacle),
            instruction="Is there really no way?",
            with_tools=False,
        )
        return _parse_json(outcome.text)

    async def _leg(self, *, system_prompt: str, instruction: str, with_tools: bool) -> LegOutcome:
        try:
            return await self.execute_leg(
                system_prompt=system_prompt,
                instruction=instruction,
                with_tools=with_tools,
            )
        except Exception as exc:  # noqa: BLE001 — a failed leg is data, not a crash
            log.warning("errand leg failed: %s", exc, exc_info=True)
            # Returned as an ordinary fruitless leg: the stall detector then
            # sees no evidence and the run winds down honestly, instead of a
            # provider hiccup taking the whole errand with it.
            return LegOutcome(text=f"This step failed: {exc}")

    async def _finish(self, errand: Errand, state: ErrandState, outcome_text: str) -> Errand:
        errand = errand.with_state(
            state,
            outcome=outcome_text,
            result_evidence=tuple(ev for s in errand.steps for ev in s.evidence),
        )
        await self._persist(errand)
        log.info("errand %s finished: %s — %s", errand.id, state, outcome_text[:120])
        if self.keep_learnings is not None:
            # C14 — what was learned outlives the errand. Strictly after the
            # terminal persist, and never allowed to break an outcome: a note
            # that fails to save costs a memory, not a booking.
            try:
                await self.keep_learnings(errand)
            except Exception:  # noqa: BLE001 — keeping notes is best-effort
                log.warning("errand %s: keep_learnings failed", errand.id, exc_info=True)
        return errand

    async def _persist(self, errand: Errand) -> None:
        await self.store.save(errand)
        if self.on_update is None:
            return
        try:
            await self.on_update(errand)
        except Exception:  # noqa: BLE001 — a UI listener must never break a run
            log.debug("errand on_update listener failed", exc_info=True)


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def _next_intent(errand: Errand) -> str:
    for step in errand.plan:
        if not step.done:
            return step.intent
    return "Continue toward the goal and finish what is still missing."


def _mark_step_done(errand: Errand) -> Errand:
    plan = list(errand.plan)
    for i, step in enumerate(plan):
        if not step.done:
            plan[i] = step.model_copy(update={"done": True})
            break
    return errand.model_copy(update={"plan": tuple(plan)})


def _append_plan_step(errand: Errand, intent: str) -> Errand:
    """A re-check that found a route adds it to the plan — revising a plan is
    normal (C8), and the new route must be visible to the user, not implicit."""
    return errand.model_copy(update={"plan": (*errand.plan, PlanStep(intent=intent))})


#: Ledger cap. A rambling gather leg must not flood every later prompt; thirty
#: facts is far beyond any real briefing, so the cut is a guard, not a budget.
_MAX_FACTS: Final[int] = 30


def _parse_facts(text: str) -> tuple[ContextFact, ...]:
    """Read the gather leg's fact ledger out of mixed prose-and-JSON output.

    Prefers the object that literally starts the ledger (the FOUND: prose may
    contain braces of its own); anything unreadable degrades to NO ledger,
    which the gate treats as the pre-C13 contract rather than as a block.
    """
    idx = text.find('{"facts"')
    data = _parse_json(text[idx:]) if idx >= 0 else _parse_json(text)
    raw = data.get("facts")
    if not isinstance(raw, list):
        return ()
    facts: list[ContextFact] = []
    for entry in raw[:_MAX_FACTS]:
        if not isinstance(entry, dict):
            continue
        statement = str(entry.get("statement", "")).strip()
        if not statement:
            continue
        facts.append(
            ContextFact(
                statement=statement,
                source=str(entry.get("source", "") or "").strip(),
                decisive=bool(entry.get("decisive")),
                durable=bool(entry.get("durable")),
            )
        )
    return tuple(facts)


def _strip_ledger(text: str) -> str:
    """Drop the JSON ledger from the FOUND: prose kept as ``gathered_context``.

    Cosmetic only: the facts live structured on the record, and repeating them
    as raw JSON inside every later prompt would just be noise. Best-effort —
    a ledger this misses costs readability, never behaviour.
    """
    text = re.sub(r"```(?:json)?\s*\{.*?\}\s*```", "", text, flags=re.DOTALL)
    idx = text.find('{"facts"')
    if idx >= 0:
        text = text[:idx]
    return text.strip()


def _after_marker(text: str, marker: str) -> str:
    idx = text.upper().find(marker.upper())
    if idx < 0:
        return ""
    return text[idx + len(marker) :].strip()


def _parse_json(text: str) -> dict[str, Any]:
    """Tolerant JSON read of a model answer.

    Models fence JSON, prefix it with prose, or add a trailing sentence. None
    of that is worth failing a run over, so the first balanced object in the
    text wins and an unparseable answer degrades to an empty dict — which every
    caller already treats as "no, not done / no route", the safe reading.
    """
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    if start < 0:
        return {}
    depth = 0
    for i, ch in enumerate(candidate[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(candidate[start : i + 1])
                except json.JSONDecodeError:
                    return {}
                return parsed if isinstance(parsed, dict) else {}
    return {}


__all__ = ["MAX_RECHECKS", "ErrandRunner", "LegExecutor", "LegOutcome"]
