"""A slow writer no longer holds the brief hostage.

The serial rescue in ``test_composer_writer_rescue.py`` covers writers that
FAIL. This file covers the writer that merely stalls — the live 2026-08-12
09:54 delivery waited 71.4 s on a healthy but slow subscription call, and the
old loop was not allowed to start anyone else until that call ended or the
whole budget was gone. After ``HEDGE_AFTER_S`` the next rung now writes
ALONGSIDE the first, and the first valid brief wins.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jarvis.agentic_ide import file_index, prompt_composer


@pytest.fixture(autouse=True)
def _spoken_budget_does_not_clip_hedge_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hedge tests need a long enough ceiling that a 1.2 s spoken cap would
    cancel the primary before the second writer is allowed to start."""
    monkeypatch.setattr(prompt_composer, "FAST_BUDGET_S", 90.0)


BRIEF = "## Task\nMake the wake path faster.\n\n## Done when\n- It is faster."
HEDGE_BRIEF = "## Task\nMake the wake path quick.\n\n## Done when\n- It is quick."


@dataclass
class _Profile:
    lines: list[str] = field(default_factory=lambda: ["Folder: /work/project"])

    def summary_lines(self) -> list[str]:
        return self.lines


@dataclass
class _Session:
    folder: str
    profile: _Profile = field(default_factory=_Profile)


class _Writer:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture()
def workspace(tmp_path: Path) -> _Session:
    (tmp_path / "jarvis").mkdir(parents=True)
    (tmp_path / "jarvis" / "wake.py").write_text("x")
    file_index.reset_cache()
    file_index._CACHE[str(tmp_path)] = file_index.build_index(tmp_path)  # noqa: SLF001
    return _Session(folder=str(tmp_path))


async def test_a_stalled_writer_gets_a_parallel_second_and_the_fast_one_wins(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tail-latency case itself: first writer hangs, hedge delivers."""
    stalled = asyncio.Event()
    started: list[str] = []
    excluded: list[tuple[str, ...]] = []

    async def _compose(*, brain: object, **_kwargs: object) -> str:
        name = getattr(brain, "name", "?")
        started.append(name)
        if name == "slow":
            await stalled.wait()  # never set — cancelled by the winner
        return HEDGE_BRIEF

    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_S", 0.05)
    monkeypatch.setattr(
        prompt_composer,
        "_resolve_writer",
        lambda: (_Writer("slow"), "subscription:slow"),
    )
    monkeypatch.setattr(
        prompt_composer,
        "_rescue_writer",
        lambda tried: excluded.append(tuple(tried)) or (_Writer("fast"), "tool_model:fast"),
    )
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    notices: list[prompt_composer.ComposeNotice] = []
    result = await prompt_composer.compose(
        "make the wake path faster",
        session=workspace,
        terminal_name="Kai",
        on_progress=notices.append,
    )

    assert result.composed_by == "llm"
    assert "quick" in result.text
    assert started == ["slow", "fast"]
    # The stalled rung is named so the hedge cannot land on the same tier.
    assert excluded == [("subscription:slow",)]
    hedges = [n for n in notices if n.stage == prompt_composer.STAGE_HEDGE]
    assert len(hedges) == 1
    assert "fast" in hedges[0].message
    assert hedges[0].terminal == "Kai"


async def test_an_api_writer_hedges_on_its_own_faster_clock(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A direct API call has no process cold-start to excuse, so its insurance
    starts at ``HEDGE_AFTER_API_S`` — waiting out the CLI-sized window would
    hand the already-fast path the longest silence."""
    stalled = asyncio.Event()
    started: list[str] = []

    async def _compose(*, brain: object, **_kwargs: object) -> str:
        name = getattr(brain, "name", "?")
        started.append(name)
        if name == "slow-api":
            await stalled.wait()  # never set — cancelled by the winner
        return HEDGE_BRIEF

    # The CLI threshold is far beyond this test's patience on purpose: if the
    # source-aware pick ever regresses to HEDGE_AFTER_S, no hedge fires and
    # the started-writers assertion fails instead of flaking.
    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_S", 60.0)
    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_API_S", 0.05)
    monkeypatch.setattr(
        prompt_composer,
        "_resolve_writer",
        lambda: (_Writer("slow-api"), "tool_model:grok"),
    )
    monkeypatch.setattr(
        prompt_composer,
        "_rescue_writer",
        lambda tried: (_Writer("fast"), "subscription:fast"),
    )
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    result = await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert result.composed_by == "llm"
    assert "quick" in result.text
    assert started == ["slow-api", "fast"]


async def test_the_original_writer_still_wins_when_it_answers_first(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hedging is insurance, not a substitution: a first writer that delivers
    keeps the brief even though a second one was already drafting."""
    parked = asyncio.Event()

    async def _compose(*, brain: object, **_kwargs: object) -> str:
        if getattr(brain, "name", "") == "laggard":
            await parked.wait()  # the hedge never finishes
        await asyncio.sleep(0.15)
        return BRIEF

    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_S", 0.01)
    monkeypatch.setattr(
        prompt_composer,
        "_resolve_writer",
        lambda: (_Writer("original"), "subscription:original"),
    )
    monkeypatch.setattr(
        prompt_composer, "_rescue_writer", lambda tried: (_Writer("laggard"), "api")
    )
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    result = await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert result.composed_by == "llm"
    assert "faster" in result.text


async def test_no_hedge_fires_while_the_writer_is_healthy(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A brief that arrives inside the window costs exactly one provider call."""

    async def _compose(**_kwargs: object) -> str:
        return BRIEF

    monkeypatch.setattr(prompt_composer, "_resolve_writer", lambda: (_Writer("prompt"), "api"))
    monkeypatch.setattr(
        prompt_composer,
        "_rescue_writer",
        lambda tried: pytest.fail("hedged a healthy writer"),
    )
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    notices: list[prompt_composer.ComposeNotice] = []
    result = await prompt_composer.compose(
        "make the wake path faster",
        session=workspace,
        terminal_name="Kai",
        on_progress=notices.append,
    )

    assert result.composed_by == "llm"
    assert not [n for n in notices if n.stage == prompt_composer.STAGE_HEDGE]


async def test_a_pinned_brain_is_never_hedged(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``brain=`` is the caller deciding; a stall must not recruit a stranger."""
    stalled = asyncio.Event()

    async def _hang(**_kwargs: object) -> str:
        await stalled.wait()
        return BRIEF

    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_S", 0.01)
    monkeypatch.setattr(prompt_composer, "COMPOSE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(prompt_composer, "_MIN_ATTEMPT_S", 0.0)
    monkeypatch.setattr(
        prompt_composer,
        "_rescue_writer",
        lambda tried: pytest.fail("substituted a pin"),
    )
    monkeypatch.setattr(prompt_composer, "_llm_compose", _hang)

    result = await prompt_composer.compose(
        "make the wake path faster",
        session=workspace,
        terminal_name="Kai",
        brain=_Writer("pinned"),
    )

    assert result.composed_by == "fallback"


async def test_cancelling_compose_reaps_every_background_task(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The attempt tasks and the context read are compose()'s own children.

    Today's callers shield the whole call, so a hangup never cancels compose
    mid-flight — but that is their defence. A future unshielded caller must
    not leak a running provider call and a pending disk read into the loop.
    """
    entered = asyncio.Event()
    reaped = asyncio.Event()

    async def _hang_compose(**_kwargs: object) -> str:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            reaped.set()
            raise
        return BRIEF

    monkeypatch.setattr(prompt_composer, "_resolve_writer", lambda: (_Writer("w"), "api"))
    monkeypatch.setattr(prompt_composer, "_rescue_writer", lambda tried: (None, ""))
    monkeypatch.setattr(prompt_composer, "_llm_compose", _hang_compose)

    work = asyncio.create_task(
        prompt_composer.compose("make the wake path faster", session=workspace, terminal_name="Kai")
    )
    await entered.wait()
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    await asyncio.wait_for(reaped.wait(), timeout=2.0)

    # Nothing of compose()'s survives it: no attempt task, no context read.
    for _ in range(20):
        pending = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        if not pending:
            break
        await asyncio.sleep(0.05)
    assert not pending


async def test_cancelling_compose_mid_resolution_reaps_the_context_read(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window BEFORE any attempt exists: the writer probe is still being
    awaited and the context read runs as a sibling task nobody else knows."""
    import threading

    hold = threading.Event()
    ctx_entered = asyncio.Event()
    ctx_reaped = asyncio.Event()

    async def _hanging_context(session: object, candidates: object) -> object:
        ctx_entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            ctx_reaped.set()
            raise
        return "", {}

    def _slow_resolve() -> tuple[object, str]:
        hold.wait(timeout=5.0)
        return (_Writer("w"), "api")

    monkeypatch.setattr(prompt_composer, "_read_context", _hanging_context)
    monkeypatch.setattr(prompt_composer, "_resolve_writer", _slow_resolve)
    monkeypatch.setattr(
        prompt_composer,
        "_llm_compose",
        lambda **_kwargs: pytest.fail("no attempt may start"),
    )

    work = asyncio.create_task(
        prompt_composer.compose("make the wake path faster", session=workspace, terminal_name="Kai")
    )
    await ctx_entered.wait()
    work.cancel()
    hold.set()  # let the probe thread finish so the cancellation lands
    with pytest.raises(asyncio.CancelledError):
        await work
    await asyncio.wait_for(ctx_reaped.wait(), timeout=2.0)


async def test_the_hedge_respects_the_shared_budget(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second writer that cannot finish inside the budget never starts —
    starting one would spend a provider on a brief that ships as the fallback."""
    stalled = asyncio.Event()

    async def _hang(**_kwargs: object) -> str:
        await stalled.wait()
        return BRIEF

    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_S", 0.05)
    monkeypatch.setattr(prompt_composer, "COMPOSE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(prompt_composer, "_MIN_ATTEMPT_S", 0.28)
    monkeypatch.setattr(
        prompt_composer,
        "_resolve_writer",
        lambda: (_Writer("slow"), "subscription:slow"),
    )
    monkeypatch.setattr(
        prompt_composer,
        "_rescue_writer",
        lambda tried: pytest.fail("hedged past the budget"),
    )
    monkeypatch.setattr(prompt_composer, "_llm_compose", _hang)

    result = await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert result.composed_by == "fallback"


# ---------------------------------------------------------------------------
# The threshold follows the writer actually in use (2026-08-18).
#
# ``HEDGE_AFTER_S`` / ``HEDGE_AFTER_API_S`` are ceilings: a writer with recent
# valid briefs in this process is insured at a multiple of ITS OWN median, never
# below the floor and never above its family's ceiling. Without history the
# ceilings apply unchanged — which is what every test above relies on.
# ---------------------------------------------------------------------------


def test_the_api_first_boot_ceiling_is_one_second() -> None:
    """A no-thinking Flash brief is supposed to land inside the spoken
    budget. The old 8 s first-boot ceiling let a hung writer sit through
    the whole wait before insurance started, and the next rung was a CLI."""
    assert prompt_composer.HEDGE_AFTER_API_S == 1.0


def test_without_history_the_ceilings_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_S", 30.0)
    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_API_S", 20.0)

    assert prompt_composer._hedge_after_s("subscription:claude-cli") == 30.0  # noqa: SLF001
    assert prompt_composer._hedge_after_s("tool_model:vertex") == 20.0  # noqa: SLF001
    assert prompt_composer._hedge_after_s("api") == 20.0  # noqa: SLF001


def test_a_fast_writers_history_pulls_insurance_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Tool Model that writes a brief in 3 s is insured at 7.5 s, not 20 s —
    the fixed API ceiling would let a hung fast writer sit through seven times
    its normal wait before a second writer was allowed to start."""
    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_API_S", 20.0)
    for _ in range(3):
        prompt_composer._remember_writer_seconds("tool_model:vertex", 3.0)  # noqa: SLF001

    assert prompt_composer._hedge_after_s("tool_model:vertex") == pytest.approx(7.5)  # noqa: SLF001


def test_the_floor_keeps_one_quick_brief_from_arming_a_hair_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One 0.8 s rewrite must not insure the next, bigger brief at 2 s: a brief
    that reads five outlines legitimately takes longer than a plain rewrite.

    The spoken ceiling (1 s) would hide this floor; lift it so the floor is
    the binding constraint, which is what this test names.
    """
    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_API_S", 20.0)
    monkeypatch.setattr(prompt_composer, "_HEDGE_FLOOR_S", 6.0)
    prompt_composer._remember_writer_seconds("api", 0.8)  # noqa: SLF001

    assert prompt_composer._hedge_after_s("api") == 6.0  # noqa: SLF001


def test_history_never_lifts_the_threshold_above_the_family_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow writer keeps its measured ceiling; the calibration only ever
    tightens insurance, it never loosens it past what the constants promise."""
    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_S", 30.0)
    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_API_S", 20.0)
    for _ in range(3):
        prompt_composer._remember_writer_seconds("subscription:claude-cli", 25.0)  # noqa: SLF001
        prompt_composer._remember_writer_seconds("api", 25.0)  # noqa: SLF001

    assert prompt_composer._hedge_after_s("subscription:claude-cli") == 30.0  # noqa: SLF001
    assert prompt_composer._hedge_after_s("api") == 20.0  # noqa: SLF001


def test_each_writer_keeps_its_own_clock() -> None:
    """A different model never inherits another's history — the CLI's 18 s
    must not slow the Tool Model's insurance, nor the reverse."""
    for _ in range(3):
        prompt_composer._remember_writer_seconds("subscription:claude-cli", 18.0)  # noqa: SLF001

    assert (
        prompt_composer._hedge_after_s("tool_model:vertex")  # noqa: SLF001
        == prompt_composer.HEDGE_AFTER_API_S
    )


def test_only_a_valid_brief_is_recorded() -> None:
    prompt_composer._remember_writer_seconds("", 3.0)  # noqa: SLF001
    prompt_composer._remember_writer_seconds("api", 0.0)  # noqa: SLF001
    prompt_composer._remember_writer_seconds("api", -1.0)  # noqa: SLF001

    assert not prompt_composer._recent_writer_seconds  # noqa: SLF001


def test_the_history_holds_the_last_few_briefs_only() -> None:
    """A writer that got faster is not held hostage by an old slow spell."""
    for seconds in (30.0, 30.0, 30.0, 30.0, 30.0, 2.0, 2.0, 2.0, 2.0, 2.0):
        prompt_composer._remember_writer_seconds("api", seconds)  # noqa: SLF001

    assert list(prompt_composer._recent_writer_seconds["api"]) == [2.0] * 5  # noqa: SLF001


async def test_a_delivered_brief_teaches_the_writers_threshold(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop feeds the calibration: a valid brief from ``tool_model:fast``
    leaves one measured duration behind under exactly that source."""

    async def _compose(**_kwargs: object) -> str:
        return BRIEF

    monkeypatch.setattr(
        prompt_composer, "_resolve_writer", lambda: (_Writer("fast"), "tool_model:fast")
    )
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    result = await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert result.composed_by == "llm"
    history = prompt_composer._recent_writer_seconds  # noqa: SLF001
    assert list(history) == ["tool_model:fast"]
    assert len(history["tool_model:fast"]) == 1
    assert history["tool_model:fast"][0] > 0


async def test_a_defective_brief_teaches_nothing(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer whose answer was thrown away must not look fast for it: only
    the rescue that actually delivered is measured."""

    async def _compose(*, brain: object, **_kwargs: object) -> str:
        if getattr(brain, "name", "") == "broken":
            return "ok"  # far too short to be a brief — a defect, not a brief
        return BRIEF

    monkeypatch.setattr(
        prompt_composer, "_resolve_writer", lambda: (_Writer("broken"), "tool_model:broken")
    )
    monkeypatch.setattr(prompt_composer, "_rescue_writer", lambda tried: (_Writer("fine"), "api"))
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    result = await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert result.composed_by == "llm"
    assert list(prompt_composer._recent_writer_seconds) == ["api"]  # noqa: SLF001


async def test_history_tightens_the_live_hedge(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: with the API ceiling far away, a writer whose recent briefs
    were quick is insured on its own clock, and the second writer wins."""
    stalled = asyncio.Event()
    started: list[str] = []

    async def _compose(*, brain: object, **_kwargs: object) -> str:
        name = getattr(brain, "name", "?")
        started.append(name)
        if name == "hung":
            await stalled.wait()  # never set — cancelled by the winner
        return HEDGE_BRIEF

    # The ceiling would never fire inside this test; only the calibrated
    # threshold can. The floor is lowered to make that threshold reachable.
    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_API_S", 60.0)
    monkeypatch.setattr(prompt_composer, "_HEDGE_FLOOR_S", 0.02)
    for _ in range(3):
        prompt_composer._remember_writer_seconds("tool_model:hung", 0.02)  # noqa: SLF001
    monkeypatch.setattr(
        prompt_composer, "_resolve_writer", lambda: (_Writer("hung"), "tool_model:hung")
    )
    monkeypatch.setattr(prompt_composer, "_rescue_writer", lambda tried: (_Writer("fast"), "api"))
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    result = await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert result.composed_by == "llm"
    assert "quick" in result.text
    assert started == ["hung", "fast"]


async def test_a_hedge_that_dies_is_replaced_by_the_next_rung(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live 2026-08-18: the primary stalled, the hedge (a keyless API rung)
    died in milliseconds, and the brief then sat out the whole budget behind
    the stall although a signed-in CLI was idle. A dead hedge is no insurance:
    the next rung takes its place at once, still one extra writer at a time."""
    stalled = asyncio.Event()
    started: list[str] = []
    asked: list[tuple[str, ...]] = []

    async def _compose(*, brain: object, **_kwargs: object) -> str:
        name = getattr(brain, "name", "?")
        started.append(name)
        if name == "slow":
            await stalled.wait()  # never set — cancelled by the winner
        if name == "dead":
            raise RuntimeError("No API key found")
        return HEDGE_BRIEF

    def _rescue(tried: list[str]) -> tuple[_Writer, str]:
        asked.append(tuple(tried))
        if len(asked) == 1:
            return _Writer("dead"), "api"
        return _Writer("fast"), "subscription:fast"

    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_API_S", 0.05)
    monkeypatch.setattr(
        prompt_composer, "_resolve_writer", lambda: (_Writer("slow"), "tool_model:slow")
    )
    monkeypatch.setattr(prompt_composer, "_rescue_writer", _rescue)
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    notices: list[prompt_composer.ComposeNotice] = []
    result = await prompt_composer.compose(
        "make the wake path faster",
        session=workspace,
        terminal_name="Kai",
        on_progress=notices.append,
    )

    assert result.composed_by == "llm"
    assert "quick" in result.text
    assert started == ["slow", "dead", "fast"]
    # Every rung is asked for once, and the dead one is named as tried.
    assert asked == [("tool_model:slow",), ("tool_model:slow", "api")]
    assert len([n for n in notices if n.stage == prompt_composer.STAGE_HEDGE]) == 2


async def test_a_healthy_hedge_recruits_nobody_else(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound still holds: while the hedge is alive and writing, no third
    writer is started, however long the primary stalls."""
    stalled = asyncio.Event()
    started: list[str] = []

    async def _compose(*, brain: object, **_kwargs: object) -> str:
        name = getattr(brain, "name", "?")
        started.append(name)
        if name == "slow":
            await stalled.wait()
        await asyncio.sleep(0.2)  # the hedge takes its time — but it is alive
        return HEDGE_BRIEF

    monkeypatch.setattr(prompt_composer, "HEDGE_AFTER_API_S", 0.05)
    monkeypatch.setattr(
        prompt_composer, "_resolve_writer", lambda: (_Writer("slow"), "tool_model:slow")
    )
    monkeypatch.setattr(
        prompt_composer,
        "_rescue_writer",
        lambda tried: (
            (_Writer("hedge"), "api")
            if len(tried) == 1
            else pytest.fail("recruited a third writer while the hedge was alive")
        ),
    )
    monkeypatch.setattr(prompt_composer, "_llm_compose", _compose)

    result = await prompt_composer.compose(
        "make the wake path faster", session=workspace, terminal_name="Kai"
    )

    assert result.composed_by == "llm"
    assert started == ["slow", "hedge"]
