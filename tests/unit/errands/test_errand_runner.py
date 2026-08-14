"""Behaviour pins for the errand runner — the assistant contract, in tests.

Every test here maps to a numbered rule in docs/plans/autonomous-missions.md.
The runner is driven with SCRIPTED legs rather than a model, on purpose: what
is being tested is the control flow — when to keep going, when something counts
as proven, when to give up — and that is exactly the part which must not depend
on a model's mood.

Each rule gets its hard negative too. "Stops when nothing lands" is worthless
without "does not stop while things are landing", and a completion gate that
never completes is as broken as one that always does.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jarvis.errands.progress import STALL_THRESHOLD, assess, fingerprint
from jarvis.errands.runner import ErrandRunner, LegOutcome
from jarvis.errands.schema import Errand, ErrandState, StepRecord
from jarvis.errands.store import ErrandStore

# Phase markers, taken from the prompts. The scripted executor routes on these.
_CONTEXT = "GATHERING CONTEXT"
_CLARIFY = "deciding what you still need"
_PLAN = "you are PLANNING"
_WORK = "WORKING on the errand"
_VERIFY = "you are now the VERIFIER"
_RECHECK = "about to tell the user that something cannot be done"


class ScriptedLegs:
    """A fake brain: canned answers per phase, recorded calls."""

    def __init__(
        self,
        *,
        context: str = "FOUND: nothing useful",
        questions: list[str] | None = None,
        plan: list[str] | None = None,
        work: list[str] | None = None,
        verdicts: list[dict] | None = None,
        rechecks: list[dict] | None = None,
        tools_used: tuple[str, ...] = ("browser",),
    ) -> None:
        self.context = context
        self.questions = questions or []
        self.plan = plan or ["do the thing"]
        self.work = work or []
        self.verdicts = verdicts or []
        self.rechecks = rechecks or []
        self.tools_used = tools_used
        self.calls: list[str] = []
        #: (phase, user_utterance) per call — what the consent gates would see.
        self.utterances: list[tuple[str, str]] = []
        self._work_i = 0
        self._verdict_i = 0
        self._recheck_i = 0

    async def __call__(
        self, *, system_prompt: str, instruction: str, with_tools: bool, user_utterance: str = ""
    ) -> LegOutcome:
        self.utterances.append((self._phase_of(system_prompt), user_utterance))
        if _CONTEXT.lower() in system_prompt.lower():
            self.calls.append("context")
            return LegOutcome(text=self.context, tools_used=("calendar",))
        if _CLARIFY.lower() in system_prompt.lower():
            self.calls.append("clarify")
            return LegOutcome(text=json.dumps({"questions": self.questions}))
        if _PLAN.lower() in system_prompt.lower():
            self.calls.append("plan")
            return LegOutcome(text=json.dumps({"steps": [{"intent": s} for s in self.plan]}))
        if _VERIFY.lower() in system_prompt.lower():
            self.calls.append("verify")
            verdict = self._take(self.verdicts, self._verdict_i, {"done": False})
            self._verdict_i += 1
            return LegOutcome(text=json.dumps(verdict))
        if _RECHECK.lower() in system_prompt.lower():
            self.calls.append("recheck")
            answer = self._take(
                self.rechecks,
                self._recheck_i,
                {"route_exists": False, "why_impossible": "no route"},
            )
            self._recheck_i += 1
            return LegOutcome(text=json.dumps(answer))
        self.calls.append("work")
        text = self._take(self.work, self._work_i, "I tried something.")
        self._work_i += 1
        return LegOutcome(text=text, tools_used=self.tools_used)

    def _phase_of(self, system_prompt: str) -> str:
        lowered = system_prompt.lower()
        for phase, marker in (
            ("context", _CONTEXT),
            ("clarify", _CLARIFY),
            ("plan", _PLAN),
            ("verify", _VERIFY),
            ("recheck", _RECHECK),
        ):
            if marker.lower() in lowered:
                return phase
        return "work"

    @staticmethod
    def _take(seq, index, default):
        if not seq:
            return default
        return seq[min(index, len(seq) - 1)]


@pytest.fixture
def store(tmp_path: Path) -> ErrandStore:
    return ErrandStore(tmp_path / "errands.db")


async def settled(runner: ErrandRunner, goal: str) -> Errand:
    """Start an errand, wait out its detached loop, return the durable record.

    ``start()`` detaches the loop on purpose — a conversation turn must never
    wait for a booking. The behaviour pins below judge the FINISHED errand, so
    they wait explicitly and then read what was persisted: the same record a
    restart would see, which makes the pins honest about C1 for free.
    """
    opened = await runner.start(goal)
    await runner.join()
    return await runner.store.get(opened.id) or opened


# ----------------------------------------------------------------------
# C3 — proof, not opinion
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_requires_the_verifier(store: ErrandStore) -> None:
    legs = ScriptedLegs(
        work=["Booked it. EVIDENCE: booking reference XY123"],
        verdicts=[{"done": True, "proof": "reference XY123 exists"}],
    )
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert errand.state is ErrandState.COMPLETED
    assert "XY123" in errand.outcome
    assert "verify" in legs.calls


@pytest.mark.asyncio
async def test_a_confident_worker_does_not_finish_the_errand(store: ErrandStore) -> None:
    """The hard negative for C3, and the whole difference from a coding agent.

    The working leg declares success in the strongest words it has. The
    verifier says no. The run must continue — belief is not an outcome.
    """
    legs = ScriptedLegs(
        work=["DONE! Everything is finished and the flight is fully booked."],
        verdicts=[{"done": False, "missing": "no booking reference anywhere"}],
    )
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert errand.state is not ErrandState.COMPLETED
    # It kept working after the claim rather than accepting it.
    assert len(errand.steps) > 1


# ----------------------------------------------------------------------
# C2 — stalling is not working (and its hard negative)
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_stops_when_nothing_lands(store: ErrandStore) -> None:
    legs = ScriptedLegs(
        work=["I could not get in."],  # never any EVIDENCE line
        verdicts=[{"done": False}],
        rechecks=[{"route_exists": False, "why_impossible": "the site blocks automation"}],
    )
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert errand.state in {ErrandState.STALLED, ErrandState.IMPOSSIBLE}
    assert errand.outcome


@pytest.mark.asyncio
async def test_work_that_lands_facts_is_never_cut_off(store: ErrandStore) -> None:
    """The hard negative for C2 — and the reason there is no step budget.

    Twelve legs, each producing a real fact, far past any old 15-round ceiling.
    The run must still be going when the verifier finally accepts it.
    """
    work = [f"Step {i}. EVIDENCE: fact number {i}" for i in range(12)]
    verdicts = [{"done": False}] * 11 + [{"done": True, "proof": "all facts in place"}]
    legs = ScriptedLegs(work=work, verdicts=verdicts, plan=["a"] * 12)
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "a long errand")
    assert errand.state is ErrandState.COMPLETED
    assert len(errand.steps) == 12


def test_identical_legs_are_a_stall_however_they_are_worded() -> None:
    steps = tuple(
        StepRecord(index=i, summary="trying", fingerprint="same-digest")
        for i in range(STALL_THRESHOLD)
    )
    assert assess(steps).stalled is True


def test_fingerprint_ignores_prose_and_follows_facts() -> None:
    """A model rephrasing itself is not progress; a new fact is."""
    a = fingerprint(
        tools_used=["browser"], evidence_details=["ref XY"], summary="I opened the page"
    )
    b = fingerprint(
        tools_used=["browser"], evidence_details=["ref XY"], summary="I opened the page"
    )
    c = fingerprint(
        tools_used=["browser"], evidence_details=["ref ZZ"], summary="I opened the page"
    )
    assert a == b
    assert a != c


# ----------------------------------------------------------------------
# C4 — think before refusing
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_wall_is_rechecked_before_it_is_reported(store: ErrandStore) -> None:
    """No 'I cannot' reaches the user unexamined — and a found route is taken."""
    legs = ScriptedLegs(
        work=["Blocked."] * 3 + ["Took the other route. EVIDENCE: booking ref QQ9"],
        verdicts=[{"done": False}] * 3 + [{"done": True, "proof": "ref QQ9"}],
        rechecks=[{"route_exists": True, "next_move": "use the airline site directly"}],
    )
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert "recheck" in legs.calls
    assert errand.state is ErrandState.COMPLETED
    # The alternative route was added to the plan, so the user can see it.
    assert any("airline site" in s.intent for s in errand.plan)


@pytest.mark.asyncio
async def test_rechecks_are_bounded_so_optimism_cannot_loop(store: ErrandStore) -> None:
    """A model always has one more idea. After a bounded number of them with
    nothing landing, the run stops anyway."""
    legs = ScriptedLegs(
        work=["Still blocked."],
        verdicts=[{"done": False}],
        rechecks=[{"route_exists": True, "next_move": "try again differently"}],
    )
    runner = ErrandRunner(store=store, execute_leg=legs, max_rechecks=2)
    errand = await settled(runner, "book a flight")
    assert errand.state is ErrandState.STALLED
    assert legs.calls.count("recheck") == 2


# ----------------------------------------------------------------------
# C9 / C10 — earn the context, then ask once
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_is_gathered_before_any_question(store: ErrandStore) -> None:
    legs = ScriptedLegs(
        context="FOUND: the user flies from Hamburg, calendar is free from the 12th",
        questions=["Which cabin class?"],
    )
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert legs.calls.index("context") < legs.calls.index("clarify")
    assert "Hamburg" in errand.gathered_context
    assert errand.state is ErrandState.NEEDS_INPUT
    assert errand.open_questions == ("Which cabin class?",)


@pytest.mark.asyncio
async def test_no_questions_means_no_interruption(store: ErrandStore) -> None:
    """C10's hard negative: an errand that starts beats a perfect briefing."""
    legs = ScriptedLegs(
        questions=[],
        work=["Done it. EVIDENCE: confirmation mail received"],
        verdicts=[{"done": True, "proof": "confirmation mail"}],
    )
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert errand.state is ErrandState.COMPLETED


@pytest.mark.asyncio
async def test_answers_resume_the_errand_without_asking_again(store: ErrandStore) -> None:
    legs = ScriptedLegs(
        questions=["Which cabin class?"],
        work=["Booked economy. EVIDENCE: ref AA11"],
        verdicts=[{"done": True, "proof": "ref AA11"}],
    )
    runner = ErrandRunner(store=store, execute_leg=legs)
    errand = await settled(runner, "book a flight")
    assert errand.state is ErrandState.NEEDS_INPUT

    resumed = await runner.provide_answers(errand.id, "economy")
    assert resumed is not None
    assert "economy" in resumed.answers
    await runner.join()
    final = await store.get(errand.id)
    assert final is not None
    assert final.state is ErrandState.COMPLETED
    # Asked once, not twice.
    assert legs.calls.count("clarify") == 1


@pytest.mark.asyncio
async def test_mid_run_the_user_is_only_pulled_in_for_what_only_they_have(
    store: ErrandStore,
) -> None:
    """The single legitimate interruption: a second factor or money."""
    legs = ScriptedLegs(
        work=["NEEDS-USER: the airline wants the code from your authenticator app"],
        verdicts=[{"done": False}],
    )
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert errand.state is ErrandState.NEEDS_INPUT
    assert "authenticator" in errand.open_questions[0]


# ----------------------------------------------------------------------
# C1 — durable across a restart
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_leg_is_persisted_as_it_happens(store: ErrandStore) -> None:
    legs = ScriptedLegs(
        work=["Step done. EVIDENCE: page shows seat selected"],
        verdicts=[{"done": True, "proof": "seat selected"}],
    )
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    reloaded = await store.get(errand.id)
    assert reloaded is not None
    assert reloaded.state is ErrandState.COMPLETED
    assert len(reloaded.steps) == len(errand.steps)
    assert reloaded.steps[0].evidence[0].detail == "page shows seat selected"


@pytest.mark.asyncio
async def test_a_restart_resumes_work_instead_of_failing_it(tmp_path: Path) -> None:
    """The point of C1: a restart mid-booking must not start from zero."""
    db = tmp_path / "errands.db"
    interrupted = Errand(
        id="err-1",
        goal="book a flight",
        state=ErrandState.RUNNING,
        plan=(),
    )
    writer = ErrandStore(db)
    await writer.save(interrupted)
    await writer.close()

    fresh_store = ErrandStore(db)
    legs = ScriptedLegs(
        work=["Carried on. EVIDENCE: ref RR7"],
        verdicts=[{"done": True, "proof": "ref RR7"}],
    )
    runner = ErrandRunner(store=fresh_store, execute_leg=legs)
    resumed = await runner.resume_open()
    assert len(resumed) == 1
    # Resume is detached too — a boot must never wait on a booking.
    await runner.join()
    final = await fresh_store.get("err-1")
    assert final is not None
    assert final.state is ErrandState.COMPLETED
    # It continued the SAME errand rather than opening a new one.
    assert resumed[0].id == "err-1"


@pytest.mark.asyncio
async def test_an_errand_waiting_on_the_user_stays_waiting_after_a_restart(
    tmp_path: Path,
) -> None:
    db = tmp_path / "errands.db"
    waiting = Errand(
        id="err-2",
        goal="book a flight",
        state=ErrandState.NEEDS_INPUT,
        open_questions=("Which airport?",),
    )
    writer = ErrandStore(db)
    await writer.save(waiting)
    await writer.close()

    legs = ScriptedLegs()
    resumed = await ErrandRunner(store=ErrandStore(db), execute_leg=legs).resume_open()
    assert resumed[0].state is ErrandState.NEEDS_INPUT
    assert legs.calls == []  # a restart must not answer the user's question for them


# ----------------------------------------------------------------------
# Robustness — a run must not die of a bad answer or a provider hiccup
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_leg_is_recorded_not_raised(store: ErrandStore) -> None:
    class Exploding(ScriptedLegs):
        async def __call__(
            self,
            *,
            system_prompt: str,
            instruction: str,
            with_tools: bool,
            user_utterance: str = "",
        ):
            if _WORK.lower() in system_prompt.lower():
                raise RuntimeError("provider went away")
            return await super().__call__(
                system_prompt=system_prompt,
                instruction=instruction,
                with_tools=with_tools,
                user_utterance=user_utterance,
            )

    legs = Exploding(verdicts=[{"done": False}])
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    # It wound down honestly instead of crashing the errand.
    assert errand.state in {ErrandState.STALLED, ErrandState.IMPOSSIBLE}


@pytest.mark.asyncio
async def test_unparseable_verdict_is_read_as_not_done(store: ErrandStore) -> None:
    """The safe reading: garbage from the judge must never complete an errand."""

    class Garbled(ScriptedLegs):
        async def __call__(
            self,
            *,
            system_prompt: str,
            instruction: str,
            with_tools: bool,
            user_utterance: str = "",
        ):
            if _VERIFY.lower() in system_prompt.lower():
                self.calls.append("verify")
                return LegOutcome(text="Sure, looks done to me!")
            return await super().__call__(
                system_prompt=system_prompt,
                instruction=instruction,
                with_tools=with_tools,
                user_utterance=user_utterance,
            )

    legs = Garbled(work=["tried"], rechecks=[{"route_exists": False, "why_impossible": "x"}])
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert errand.state is not ErrandState.COMPLETED


@pytest.mark.asyncio
async def test_fenced_json_is_accepted(store: ErrandStore) -> None:
    class Fenced(ScriptedLegs):
        async def __call__(
            self,
            *,
            system_prompt: str,
            instruction: str,
            with_tools: bool,
            user_utterance: str = "",
        ):
            if _VERIFY.lower() in system_prompt.lower():
                self.calls.append("verify")
                return LegOutcome(
                    text='Here you go:\n```json\n{"done": true, "proof": "ref P1"}\n```'
                )
            return await super().__call__(
                system_prompt=system_prompt,
                instruction=instruction,
                with_tools=with_tools,
                user_utterance=user_utterance,
            )

    legs = Fenced(work=["did it. EVIDENCE: ref P1"])
    errand = await settled(ErrandRunner(store=store, execute_leg=legs), "book a flight")
    assert errand.state is ErrandState.COMPLETED


@pytest.mark.asyncio
async def test_runaway_backstop_reports_itself_as_a_fault(store: ErrandStore) -> None:
    """A model that fakes evidence forever must not bill forever — and the
    message must say it is a fault, not a task that was simply too big."""

    class NeverRepeats(ScriptedLegs):
        """Fresh-looking proof on every leg — so the stall detector, which
        judges by facts, correctly sees progress and never fires. This is the
        exact hole the backstop exists to cover."""

        async def __call__(
            self,
            *,
            system_prompt: str,
            instruction: str,
            with_tools: bool,
            user_utterance: str = "",
        ):
            if _WORK.lower() in system_prompt.lower():
                self.calls.append("work")
                self._work_i += 1
                return LegOutcome(
                    text=f"EVIDENCE: brand new fact number {self._work_i}",
                    tools_used=self.tools_used,
                )
            return await super().__call__(
                system_prompt=system_prompt,
                instruction=instruction,
                with_tools=with_tools,
                user_utterance=user_utterance,
            )

    legs = NeverRepeats(verdicts=[{"done": False}])
    runner = ErrandRunner(store=store, execute_leg=legs, max_legs_backstop=5)
    errand = await settled(runner, "an endless errand")
    assert errand.state is ErrandState.STALLED
    assert "investigate" in errand.outcome


@pytest.mark.asyncio
async def test_cancel_stops_the_run(store: ErrandStore) -> None:
    legs = ScriptedLegs(work=["working"], verdicts=[{"done": False}])
    runner = ErrandRunner(store=store, execute_leg=legs)
    errand = await settled(runner, "book a flight")
    cancelled = await runner.cancel(errand.id)
    assert cancelled is not None
    assert cancelled.state is ErrandState.CANCELLED
    await runner.join()


@pytest.mark.asyncio
async def test_start_returns_while_the_loop_is_still_working(store: ErrandStore) -> None:
    """The turn must never wait for the booking (pin for the detach itself).

    The working leg blocks on a gate the test controls. If start() ever runs
    the loop inline again, this test deadlocks instead of merely failing —
    which is exactly the severity the regression deserves.
    """
    gate = asyncio.Event()

    class Gated(ScriptedLegs):
        async def __call__(
            self,
            *,
            system_prompt: str,
            instruction: str,
            with_tools: bool,
            user_utterance: str = "",
        ):
            if _WORK.lower() in system_prompt.lower():
                await asyncio.wait_for(gate.wait(), timeout=10)
            return await super().__call__(
                system_prompt=system_prompt,
                instruction=instruction,
                with_tools=with_tools,
                user_utterance=user_utterance,
            )

    legs = Gated(
        work=["Booked. EVIDENCE: ref GG5"],
        verdicts=[{"done": True, "proof": "ref GG5"}],
    )
    runner = ErrandRunner(store=store, execute_leg=legs)
    opened = await runner.start("book a flight")
    assert opened.state is ErrandState.RUNNING  # back before any work happened
    assert opened.plan  # but already planned, so the user can be told the shape

    gate.set()
    await runner.join()
    final = await store.get(opened.id)
    assert final is not None
    assert final.state is ErrandState.COMPLETED
