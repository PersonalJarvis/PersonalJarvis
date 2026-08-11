"""The model-written pane recap, and the promises it makes around the model.

The summary itself is a model's sentence, so what is testable here is
everything around it: that a pane always has a recap even when nothing can be
summarized, that a summary is asked for only when something has actually
happened, that the model's answer is read back forgivingly, and that no failure
path reaches the state read a pane header depends on.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import recap_engine
from jarvis.agentic_ide.session import Terminal


@pytest.fixture(autouse=True)
def _clean():
    recap_engine.reset_for_tests()
    yield
    recap_engine.reset_for_tests()


def _pane(lines: int = 0, **changes: object) -> Terminal:
    term = Terminal(
        key="mika",
        name="Mika",
        agent="claude",
        display_name="Claude Code",
        index=0,
        status="live",
    )
    for field, value in changes.items():
        setattr(term, field, value)
    for number in range(lines):
        term.transcript.feed(f"Step {number}: reading jarvis/core/config.py\r\n")
    return term


def _rows(count: int) -> list[str]:
    return [f"Step {number}: reading jarvis/core/config.py" for number in range(count)]


# --------------------------------------------------------------- the fallback


def test_a_pane_with_no_summary_yet_still_has_a_recap() -> None:
    """The deterministic recap is the floor, and it is never absent."""
    term = _pane(lines=3, last_prompt="Fix the failing login test")

    answer = recap_engine.recap_for(term, lines=term.transcript.lines())

    assert answer.source == recap_engine.BY_RULES
    assert "Fix the failing login test" in answer.headline


def test_a_summarized_pane_reports_the_model_sentence() -> None:
    term = _pane(lines=3)
    state = recap_engine._state(term.key)  # noqa: SLF001 - the cache under test
    state.headline = "Wrote the swap analysis, not committed"
    state.detail = "The document is at docs/plans/agentic-ide-terminal-swap.md."
    state.generated_at = time.time()

    answer = recap_engine.recap_for(term, lines=[])

    assert answer.source == recap_engine.BY_MODEL
    assert answer.headline == "Wrote the swap analysis, not committed"


def test_a_closed_pane_forgets_its_recap() -> None:
    """Pane keys are reused, so a new pane must not inherit the old sentence."""
    term = _pane()
    recap_engine._state(term.key).headline = "Old news"  # noqa: SLF001

    recap_engine.forget(term.key)

    assert recap_engine.recap_for(term, lines=[]).source == recap_engine.BY_RULES


# ------------------------------------------------------------- when it refreshes


def test_a_pane_with_news_is_scheduled_for_a_summary(monkeypatch) -> None:
    """The positive case: enough new output, and a background summary starts."""
    monkeypatch.setattr(recap_engine, "_enabled", lambda: True)

    async def scenario() -> None:
        async def answer(*_args: object, **_kwargs: object) -> recap_engine.SmartRecap:
            return recap_engine.SmartRecap("Ran the login tests", "Three fail.")

        monkeypatch.setattr(recap_engine, "summarize_with_model", answer)
        term = _pane(lines=40)
        recap_engine.refresh_soon(term, lines=_rows(40))
        assert recap_engine._tasks  # noqa: SLF001 - a task really was created
        await asyncio.gather(*list(recap_engine._tasks))  # noqa: SLF001
        assert recap_engine.recap_for(term, lines=[]).headline == "Ran the login tests"

    asyncio.run(scenario())


def test_a_quiet_pane_is_not_summarized_again(monkeypatch) -> None:
    """A repainting spinner is not news; re-summarizing it would just cost."""
    monkeypatch.setattr(recap_engine, "_enabled", lambda: True)

    async def scenario() -> None:
        term = _pane(lines=30)
        rows = _rows(30)
        state = recap_engine._state(term.key)  # noqa: SLF001
        state.headline = "Already known"
        state.generated_at = time.time()
        state.lines_at = len(rows)
        state.prompts_at = 0
        state.status_at = "live"

        recap_engine.refresh_soon(term, lines=rows)

        assert not recap_engine._tasks  # noqa: SLF001

    asyncio.run(scenario())


def test_a_pane_that_has_printed_almost_nothing_is_left_to_the_rules() -> None:
    """Below a few readable rows there is nothing a model could add."""
    term = _pane(lines=2)

    assert not recap_engine._worth_summarizing(term, 2)  # noqa: SLF001
    assert recap_engine._worth_summarizing(term, 40)  # noqa: SLF001


def test_a_pane_that_could_not_start_is_left_to_the_rules() -> None:
    """A pane that says "Claude Code is not on PATH" needs no model."""
    term = _pane(lines=30, status="error", error="Claude Code is not on PATH.")

    assert not recap_engine._worth_summarizing(term, 30)  # noqa: SLF001


def test_a_settled_title_ignores_output_growth() -> None:
    """A navigation label names the session and stays put — like every chat UI
    the user knows. Progress changes what a pane has ACHIEVED, never what it is
    for, so once a summary has read a substantial transcript, more output alone
    must not re-word the header the user navigates by."""
    term = _pane(lines=0)
    state = recap_engine._state(term.key)  # noqa: SLF001
    state.generated_at = time.time() - 2 * recap_engine.MIN_REFRESH_S
    state.lines_at = recap_engine.STABLE_AFTER_LINES
    state.prompts_at = 0
    state.status_at = "live"

    grown = state.lines_at + 10 * recap_engine.MIN_NEW_LINES
    assert not recap_engine._material_change(term, state, grown)  # noqa: SLF001


def test_an_early_thin_summary_may_still_improve_with_output() -> None:
    """A pane summarized at its first rows knows the banner, not the work."""
    term = _pane(lines=0)
    state = recap_engine._state(term.key)  # noqa: SLF001
    state.generated_at = time.time() - 2 * recap_engine.MIN_REFRESH_S
    state.lines_at = 10
    state.prompts_at = 0
    state.status_at = "live"

    assert recap_engine._material_change(  # noqa: SLF001
        term, state, 10 + recap_engine.MIN_NEW_LINES
    )


def test_a_hand_typed_instruction_reopens_a_settled_title() -> None:
    """prompts_sent only counts what JARVIS sent. A person typing a brand-new
    task into the pane stamps last_submit_at — and repurposing a pane by hand
    must reopen its title exactly like a sent instruction does."""
    term = _pane(lines=0, last_submit_at=200.0)
    state = recap_engine._state(term.key)  # noqa: SLF001
    state.generated_at = time.time()
    state.lines_at = recap_engine.STABLE_AFTER_LINES
    state.prompts_at = 0
    state.status_at = "live"
    state.submit_at = 100.0

    assert recap_engine._material_change(term, state, state.lines_at)  # noqa: SLF001

    state.submit_at = 200.0
    assert not recap_engine._material_change(term, state, state.lines_at)  # noqa: SLF001


def test_a_settled_title_still_reopens_for_a_new_instruction_or_exit() -> None:
    """The two events that CAN change what a pane is for."""
    term = _pane(lines=0, prompts_sent=2)
    state = recap_engine._state(term.key)  # noqa: SLF001
    state.generated_at = time.time()
    state.lines_at = recap_engine.STABLE_AFTER_LINES
    state.prompts_at = 1
    state.status_at = "live"

    assert recap_engine._material_change(term, state, state.lines_at)  # noqa: SLF001

    state.prompts_at = 2
    term.status = "exited"
    assert recap_engine._material_change(term, state, state.lines_at)  # noqa: SLF001


def test_a_new_instruction_makes_a_pane_worth_re_reading() -> None:
    term = _pane(lines=30, prompts_sent=2)
    state = recap_engine._state(term.key)  # noqa: SLF001
    state.generated_at = time.time()
    state.lines_at = 30
    state.prompts_at = 1
    state.status_at = "live"

    assert recap_engine._material_change(term, state, 30)  # noqa: SLF001


def test_the_switch_turns_the_model_off(monkeypatch) -> None:
    """Off means off — the deterministic recap, and no scheduling at all."""
    monkeypatch.setattr(recap_engine, "_enabled", lambda: False)

    async def scenario() -> None:
        recap_engine.refresh_soon(_pane(lines=40), lines=_rows(40))
        assert not recap_engine._tasks  # noqa: SLF001

    asyncio.run(scenario())


def test_scheduling_outside_an_event_loop_is_a_no_op() -> None:
    """A CLI state dump has no loop; it gets the rules and no exception."""
    recap_engine.refresh_soon(_pane(lines=40), lines=_rows(40))

    assert recap_engine.recap_for(_pane(lines=40), lines=[]).source == recap_engine.BY_RULES


def test_scheduling_never_raises(monkeypatch) -> None:
    """A recap must never be the thing that breaks a workspace read."""

    def boom() -> bool:
        raise RuntimeError("config is on fire")

    monkeypatch.setattr(recap_engine, "_enabled", boom)

    recap_engine.refresh_soon(_pane(lines=40), lines=_rows(40))  # must not raise


# ----------------------------------------------------------------- the prompt


def test_the_prompt_carries_the_instruction_and_both_transcript_bookends() -> None:
    """The original request says WHY; the newest rows say where it stands."""
    term = _pane(last_prompt="Analyse swapping terminal tiles by dragging them")

    prompt = recap_engine.build_prompt(term, _rows(400), folder="C:/repo")

    assert "Analyse swapping terminal tiles" in prompt
    assert "Step 0" in prompt  # the original request is usually at this end...
    assert "Step 399" in prompt  # ...and the current state is at this one.
    assert "Step 200" not in prompt  # noisy implementation history is omitted
    assert "C:/repo" in prompt


def test_the_headline_prompt_is_an_outcome_label_that_fits_the_real_header() -> None:
    """Pin the product distinction the screenshot exposed: goal, not process."""
    system = recap_engine._SYSTEM  # noqa: SLF001 - the contract under test

    assert "navigation label" in system
    assert "HARD MAXIMUM 48 characters" in system
    assert "original user goal" in system.lower()
    assert '"Investigating"' in system
    assert "Keep file paths" in system
    assert "Workspace setup — one clear screen" in system


def test_the_headline_prompt_front_loads_the_first_two_words() -> None:
    """The second screenshot's lesson: the rail truncates after ~2 words, so
    those two must identify the session alone — and 'Code Review' does not."""
    system = recap_engine._SYSTEM  # noqa: SLF001 - the contract under test

    assert "FIRST TWO WORDS" in system
    assert "about 5 words" in system
    assert '"Code Review"' in system  # named as a banned opener
    assert "Pane titles review — 6 findings" in system  # ...and its repair


def test_a_pane_nobody_briefed_says_so_in_the_prompt() -> None:
    prompt = recap_engine.build_prompt(_pane(), _rows(10))

    assert "not been sent an instruction" in prompt


def test_the_prompt_asks_for_the_interface_language(monkeypatch) -> None:
    """The header sits among UI labels, so it follows the interface language."""
    monkeypatch.setattr(recap_engine, "_ui_language", lambda: "de")

    assert "Write both lines in German." in recap_engine.build_prompt(_pane(), _rows(10))


def test_an_unknown_interface_language_falls_back_to_english(monkeypatch) -> None:
    """A locale a newer build introduced must not produce an empty instruction."""
    monkeypatch.setattr(recap_engine, "_ui_language", lambda: "fr")

    assert "Write both lines in English." in recap_engine.build_prompt(_pane(), _rows(10))


# ------------------------------------------------------------ reading the answer


def test_the_two_labelled_lines_are_read_back() -> None:
    answer = recap_engine.parse_answer(
        "HEADLINE: Swap-analysis document written, not committed\n"
        "DETAIL: The goal was an analysis of swapping terminal tiles by dragging "
        "them. It is finished at docs/plans/agentic-ide-terminal-swap.md but the "
        "distribution denylist blocks the commit."
    )

    assert answer is not None
    assert answer.headline == "Swap-analysis document written, not committed"
    assert "distribution denylist" in answer.detail
    assert answer.source == recap_engine.BY_MODEL


def test_an_unlabelled_answer_is_split_into_the_two_lengths() -> None:
    """Models drop the labels; a recap is not worth failing over that."""
    answer = recap_engine.parse_answer(
        "Running the login tests\nThree of them fail on a missing fixture."
    )

    assert answer is not None
    assert answer.headline == "Running the login tests"
    assert "missing fixture" in answer.detail


def test_a_fenced_answer_is_read_back() -> None:
    answer = recap_engine.parse_answer(
        "```\nHEADLINE: Tagging v2.4.0\nDETAIL: The release build is green.\n```"
    )

    assert answer is not None
    assert answer.headline == "Tagging v2.4.0"


def test_an_empty_answer_is_rejected_rather_than_blanking_the_header() -> None:
    assert recap_engine.parse_answer("") is None
    assert recap_engine.parse_answer("   \n\n  ") is None


def test_the_headline_is_capped_for_transport() -> None:
    answer = recap_engine.parse_answer("HEADLINE: " + "word " * 200)

    assert answer is not None
    assert len(answer.headline) <= recap_engine.HEADLINE_CHARS


def test_a_truncated_detail_does_not_replace_a_useful_headline_with_debris() -> None:
    """Live Gemini output once ended at `DETAIL: The` after thinking used the cap."""
    answer = recap_engine.parse_answer(
        "HEADLINE: Terminal alerts — 3 reliability fixes\nDETAIL: The"
    )

    assert answer is not None
    assert answer.detail == answer.headline


@pytest.mark.parametrize(
    "answer",
    [
        "HEADLINE: Glockenfunktion)\nDETAIL: Three notification bugs are being fixed.",
        '... tests -g "*.py',
        "adr/0014-memory-trigger-contract",
    ],
)
def test_cli_debris_is_rejected_as_a_model_headline(answer: str) -> None:
    assert recap_engine.parse_answer(answer) is None


@pytest.mark.parametrize(
    "answer",
    [
        # Live 2026-08-11: with every API key dead, the recaps rode a
        # subscription CLI whose contract is only prepended text — it answered
        # conversationally, and these opened the settled title of every pane.
        "Understood! I see a Claude Code session working through German i18n keys.",
        "Hello! It looks like you have a coding agent running in this terminal.",
        "It looks like Claude Code is reviewing the pane title heuristics in this "
        "repository and has not finished that work yet.",
        "Here are the German translations this session has produced so far, with "
        "the files they came from.",
        "Here you go:",
        "HEADLINE: Understood! Reviewing the transcript now\n"
        "DETAIL: The pane is being read and a proper summary follows.",
    ],
)
def test_a_chat_shaped_answer_is_rejected_rather_than_shipped_as_a_title(
    answer: str,
) -> None:
    """A CLI agent that REACTS to the contract must not name the pane."""
    assert recap_engine.parse_answer(answer) is None


def test_a_short_unlabelled_label_is_still_accepted() -> None:
    """The chat-shape guard must not take the forgiving path down with it."""
    answer = recap_engine.parse_answer(
        "Login tests — 3 failures on one fixture\n"
        "The goal was to stabilize the login suite. Three tests still fail on a "
        "missing fixture."
    )

    assert answer is not None
    assert answer.headline == "Login tests — 3 failures on one fixture"


# ------------------------------------------------------------ the summary itself


def test_an_install_with_no_provider_keeps_the_deterministic_recap(monkeypatch) -> None:
    """§3: one key, no key, a dead key — the pane header still says something."""
    monkeypatch.setattr(recap_engine, "_resolve_brains", lambda: [])

    assert asyncio.run(recap_engine.summarize_with_model(_pane(), _rows(20))) is None


def test_the_recap_request_disables_internal_reasoning(monkeypatch) -> None:
    """A tiny extraction must leave its output budget for the visible answer."""

    class Brain:
        model = "test-model"

        def __init__(self) -> None:
            self.requests: list[object] = []

        async def complete(self, request):  # noqa: ANN001, ANN202 - protocol fake
            self.requests.append(request)
            yield SimpleNamespace(
                content=(
                    "HEADLINE: Terminal titles — clear at a glance\n"
                    "DETAIL: The goal is recognizable terminal titles. "
                    "The new labels lead with the user's outcome."
                )
            )

    brain = Brain()
    monkeypatch.setattr(recap_engine, "_resolve_brains", lambda: [brain])

    answer = asyncio.run(recap_engine.summarize_with_model(_pane(), _rows(20)))

    assert answer is not None
    assert len(brain.requests) == 1
    assert brain.requests[0].max_tokens == recap_engine.MODEL_OUTPUT_TOKENS  # type: ignore[attr-defined]
    assert brain.requests[0].reasoning_effort == "none"  # type: ignore[attr-defined]


def test_an_invalid_small_answer_retries_with_the_measured_larger_budget(monkeypatch) -> None:
    """Only malformed headlines pay for a thinking-mandatory provider retry."""

    class Brain:
        model = "test-model"

        def __init__(self) -> None:
            self.requests: list[object] = []

        async def complete(self, request):  # noqa: ANN001, ANN202 - protocol fake
            self.requests.append(request)
            content = (
                "adr/0014-memory-trigger-contract"
                if len(self.requests) == 1
                else (
                    "HEADLINE: Terminal titles — clear at a glance\n"
                    "DETAIL: The goal is recognizable terminal titles. "
                    "The new labels lead with the user's outcome."
                )
            )
            yield SimpleNamespace(content=content)

    brain = Brain()
    monkeypatch.setattr(recap_engine, "_resolve_brains", lambda: [brain])

    answer = asyncio.run(recap_engine.summarize_with_model(_pane(), _rows(20)))

    assert answer is not None
    assert [request.max_tokens for request in brain.requests] == [  # type: ignore[attr-defined]
        recap_engine.MODEL_OUTPUT_TOKENS,
        recap_engine.MODEL_RETRY_OUTPUT_TOKENS,
    ]


def test_a_depleted_provider_crosses_to_the_next_family(monkeypatch) -> None:
    """AP-22 at call time: a key that 429s must not take the recap down with it."""

    class Depleted:
        model = "gemini-depleted"

        async def complete(self, request):  # noqa: ANN001, ANN202 - protocol fake
            raise RuntimeError("429 Too Many Requests: prepayment credits are depleted")
            yield  # pragma: no cover - makes this an async generator

    class Working:
        model = "other-family"

        async def complete(self, request):  # noqa: ANN001, ANN202 - protocol fake
            yield SimpleNamespace(
                content=(
                    "HEADLINE: Pane recaps — cross-family fallback\n"
                    "DETAIL: The goal is a recap that survives a depleted key. "
                    "The second provider family wrote this one."
                )
            )

    monkeypatch.setattr(recap_engine, "_resolve_brains", lambda: [Depleted(), Working()])

    answer = asyncio.run(recap_engine.summarize_with_model(_pane(), _rows(20)))

    assert answer is not None
    assert answer.headline == "Pane recaps — cross-family fallback"
    assert answer.writer == "other-family"


def test_when_every_family_fails_the_first_failure_is_reported(monkeypatch) -> None:
    """The configured provider's error is the one the user can act on."""

    def _dead(model: str, message: str):  # noqa: ANN202
        class Dead:
            async def complete(self, request):  # noqa: ANN001, ANN202 - protocol fake
                raise RuntimeError(message)
                yield  # pragma: no cover - makes this an async generator

        Dead.model = model
        return Dead()

    monkeypatch.setattr(
        recap_engine,
        "_resolve_brains",
        lambda: [_dead("first", "429 quota exceeded"), _dead("second", "connection refused")],
    )

    with pytest.raises(RuntimeError, match="429 quota exceeded"):
        asyncio.run(recap_engine.summarize_with_model(_pane(), _rows(20)))


def test_a_depleted_key_failure_reads_as_a_sentence_not_json() -> None:
    """The screenshot case: the card led with a wall of provider JSON."""
    note = recap_engine.describe_failure(
        RuntimeError(
            '429 Too Many Requests. {\'message\': \'{\\n "error": {\\n "code": 429,'
            '\\n "message": "Your prepayment credits are depleted."}}\'}'
        )
    )

    assert note.startswith("Its provider is out of credits or rate-limited.")


def test_a_connected_subscription_is_the_last_candidate(monkeypatch) -> None:
    """One depleted API key + a signed-in coding CLI must still produce recaps."""
    from jarvis.brain import resolver

    api = SimpleNamespace(model="depleted-api")
    subscription = SimpleNamespace(model="coding-cli")
    monkeypatch.setattr(resolver, "frontier_brain_candidates", lambda cfg: iter([api]))
    monkeypatch.setattr(resolver, "resolve_subscription_brain", lambda cfg, **kwargs: subscription)
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: SimpleNamespace())

    assert recap_engine._resolve_brains() == [api, subscription]  # noqa: SLF001


def test_a_full_api_chain_still_ends_at_the_subscription(monkeypatch) -> None:
    """The regression that shipped: three configured-but-dead API keys filled
    every candidate slot, and the one credential provably working — the CLI
    running in the panes — was never asked. The subscription rides behind the
    API cap, always."""
    from jarvis.brain import resolver

    brains = [SimpleNamespace(model=f"api-{n}") for n in range(recap_engine.MAX_PROVIDER_TRIES)]
    subscription = SimpleNamespace(model="coding-cli")
    monkeypatch.setattr(resolver, "frontier_brain_candidates", lambda cfg: iter(brains))
    monkeypatch.setattr(resolver, "resolve_subscription_brain", lambda cfg, **kwargs: subscription)
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: SimpleNamespace())

    assert recap_engine._resolve_brains() == [*brains, subscription]  # noqa: SLF001


def test_a_failing_provider_leaves_the_previous_sentence_in_place() -> None:
    """A provider that raises costs the refresh, never the recap on screen."""

    async def scenario() -> None:
        term = _pane(lines=40)
        state = recap_engine._state(term.key)  # noqa: SLF001
        state.headline = "The sentence already on screen"
        state.inflight = True

        async def explode(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("429")

        original = recap_engine.summarize_with_model
        recap_engine.summarize_with_model = explode  # type: ignore[assignment]
        try:
            await recap_engine._run(term, term.key, _rows(40), "")  # noqa: SLF001
        finally:
            recap_engine.summarize_with_model = original  # type: ignore[assignment]

        assert recap_engine.recap_for(term, lines=[]).headline == ("The sentence already on screen")
        assert state.failures == 1
        assert not state.inflight

    asyncio.run(scenario())


def test_repeated_failures_stop_the_pane_asking_for_a_while() -> None:
    """A depleted key fails for every pane and every poll — back off once."""

    async def scenario() -> None:
        term = _pane(lines=40)
        state = recap_engine._state(term.key)  # noqa: SLF001

        async def explode(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("no key")

        original = recap_engine.summarize_with_model
        recap_engine.summarize_with_model = explode  # type: ignore[assignment]
        try:
            for _ in range(recap_engine.FAILURES_BEFORE_QUIET):
                state.inflight = True
                await recap_engine._run(term, term.key, _rows(40), "")  # noqa: SLF001
        finally:
            recap_engine.summarize_with_model = original  # type: ignore[assignment]

        assert state.quiet_until > time.time()

    asyncio.run(scenario())


def test_a_successful_summary_becomes_the_pane_recap() -> None:
    async def scenario() -> None:
        term = _pane(lines=40, prompts_sent=1)

        async def answer(*_args: object, **_kwargs: object) -> recap_engine.SmartRecap:
            return recap_engine.SmartRecap(
                headline="Swap analysis written, not committed",
                detail="It is at docs/plans/agentic-ide-terminal-swap.md.",
                source=recap_engine.BY_MODEL,
            )

        original = recap_engine.summarize_with_model
        recap_engine.summarize_with_model = answer  # type: ignore[assignment]
        try:
            recap_engine._state(term.key).inflight = True  # noqa: SLF001
            await recap_engine._run(term, term.key, _rows(40), "")  # noqa: SLF001
        finally:
            recap_engine.summarize_with_model = original  # type: ignore[assignment]

        shown = recap_engine.recap_for(term, lines=[])
        assert shown.headline == "Swap analysis written, not committed"
        assert shown.source == recap_engine.BY_MODEL
        # And the pane is now considered summarized as of these rows, so the
        # next poll does not immediately ask again.
        state = recap_engine._state(term.key)  # noqa: SLF001
        assert state.lines_at == 40
        assert state.prompts_at == 1

    asyncio.run(scenario())
