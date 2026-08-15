"""Guards for the fast lane: which orders skip the writer, and which never do.

The risk this feature carries is entirely one-sided. Admitting a vague order by
mistake hands a coding agent the shrug the composer exists to prevent, so the
tests that matter most here are the NEGATIVE ones — the sentences that must keep
waiting for a brief no matter how much faster the other lane is.

``test_the_composers_own_showcase_still_gets_a_brief`` is the load-bearing one:
it pins the exact sentence ``prompt_composer``'s module docstring uses to
explain why the feature exists. If that sentence ever reaches an agent
unbriefed, the fast lane has eaten the product.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jarvis.agentic_ide import direct_delivery, file_index, prompt_composer

# --------------------------------------------------------------------------
# The rules themselves
# --------------------------------------------------------------------------

# Orders that name both what to do and what to do it to. A writer handed one of
# these can only restate it, which is the whole 19-25 s this lane removes.
ALREADY_A_PROMPT = [
    "run pytest on tests/unit/agentic_ide",
    "rename _fuse_ranked to _merge_ranked",
    "delete jarvis/legacy/old_router.py",
    "run `npm run build` in the frontend",
    "update the version in pyproject.toml to 1.4.0",
    "add a docstring to resolve_writer",
    "open jarvis/terminal/shells.py",
    "install ruff and run it over jarvis/",
    # i18n-allow: spoken input vocabulary under test
    "führ die Tests in tests/unit/agentic_ide aus",
    # i18n-allow: spoken input vocabulary under test
    "lösch die Datei jarvis/legacy/old_router.py",
    # i18n-allow: spoken input vocabulary under test
    "benenne _fuse_ranked in _merge_ranked um",
    "ejecuta pytest en tests/unit",  # i18n-allow: spoken input vocabulary under test
    # The narrowed Spanish "crear" must still admit actual Spanish.
    "crea un archivo config.py",  # i18n-allow: spoken input vocabulary under test
]

# Orders whose ROUTE is the open question. Every one of these is what the
# composer was built for: the brief reads the code and turns the goal into a
# task with real symbols, files and an acceptance criterion.
NEEDS_A_BRIEF = [
    # The sentence prompt_composer's docstring quotes as the reason it exists.
    # i18n-allow: spoken input vocabulary under test
    "kannst du mal schnell, also ähm, das Wake-Ding angucken ob da was kaputt ist",
    "have a look at the wake word thing and see if something is broken",
    "fix the flaky test",
    "the barge-in feels wrong, sort it out",
    "investigate why the panes show done while they are working",
    "review the terminal layer",
    "improve the latency of the prompt composer",
    "clean up the agentic ide module",
    "make the voice path faster",
    "why does resolve_writer return None on my machine",
    # An operation verb in its OTHER sense. "build" is an order on its own and
    # a feature request the moment an object follows it — the false positive
    # tests/unit/agentic_ide/test_composer_progress.py caught first.
    "build a retry into the uploader",
    "add a retry to the uploader",
    "write a migration for the settings table",
    "schau dir mal die Latenz an",  # i18n-allow: spoken input vocabulary under test
    "repariere den Bug im Wake-Wort",  # i18n-allow: spoken input vocabulary under test
    # Live 2026-08-15 09:15: this reached T7 verbatim. Two regex mines at once —
    # "creative" matched the open Spanish stem ``crea\w*``, and the transcript
    # apostrophes ("Java's ... should't") matched the quoted-literal target.
    "do in deep dive and fix the AI solop UI design our Java's marketplace "
    "especially the rectangle optic which is typically for AI. He should do it "
    "remarkable. He should do it creative. And it should't look like AI.",
    # The same mines, isolated: "creative" beside a real path used to pass as
    # a Spanish "crear" order, and two contractions used to pass as a quoted
    # literal beside a genuinely executional verb.
    "make the settings.py page look creative",
    "update the marketplace so it doesn't feel boxy and isn't so sterile",
]


@pytest.mark.parametrize("instruction", ALREADY_A_PROMPT)
def test_an_executable_order_goes_out_as_it_stands(instruction: str) -> None:
    verdict = direct_delivery.decide(instruction)
    assert verdict.direct is True, f"{instruction!r} should not need a brief"
    assert verdict.reason, "a fast delivery has to be able to say why"


@pytest.mark.parametrize("instruction", NEEDS_A_BRIEF)
def test_a_goal_without_a_route_still_gets_a_brief(instruction: str) -> None:
    verdict = direct_delivery.decide(instruction)
    assert verdict.direct is False, f"{instruction!r} must still be briefed"
    assert verdict.reason, "a held order has to be able to say why"


def test_the_composers_own_showcase_still_gets_a_brief() -> None:
    """The one sentence the whole feature is documented against.

    Pinned separately from the table above so a future widening of the verb
    list cannot quietly delete it along with a parametrised case.
    """
    # i18n-allow: spoken input vocabulary under test
    said = "kannst du mal schnell, also ähm, das Wake-Ding angucken ob da was kaputt ist"
    assert direct_delivery.decide(said).direct is False


def test_a_plain_operation_needs_no_target() -> None:
    """"Commit and push" names no file and is still a complete order."""
    for said in ("commit and push", "continue", "run the tests", "build and lint"):
        assert direct_delivery.decide(said).direct is True, said


def test_an_operation_that_grows_a_tail_is_briefed_again() -> None:
    """The verb is the same; the sentence around it stopped being an order."""
    said = (
        "commit and push, but first have a look at whether the tests still make "
        "sense after everything that changed yesterday"
    )
    assert direct_delivery.decide(said).direct is False


def test_a_dropped_file_always_gets_a_brief() -> None:
    """The described image only becomes usable once something folds it in."""
    verdict = direct_delivery.decide(
        "run the build and compare it with tests/fixtures/expected.png",
        has_attachments=True,
    )
    assert verdict.direct is False
    assert "dropped file" in verdict.reason


def test_a_pointer_into_the_conversation_gets_a_brief() -> None:
    """The agent receives the brief alone — it can never open "the second one"."""
    for said in (
        "apply the second option to jarvis/core/config.py",
        "add what you just suggested to prompt_composer.py",
        # i18n-allow: spoken input vocabulary under test
        "füge die zweite Option in jarvis/core/config.py hinzu",
    ):
        assert direct_delivery.decide(said).direct is False, said


def test_saying_prompt_orders_a_written_prompt() -> None:
    """The maintainer's 2026-08-15 mandate: "prompt it to ..." means WRITE one.

    The user could type their own sentence into the pane themselves; asking
    Jarvis to prompt a terminal is asking for the composed brief. The veto must
    hold even when the instruction alone would qualify for the fast lane.
    """
    for said in (
        "prompt the terminal to run pytest on tests/unit/agentic_ide",
        # i18n-allow: spoken input vocabulary under test
        "prompte das Terminal, führ die Tests in tests/unit aus",
        # i18n-allow: spoken input vocabulary under test
        "beauftrage T4, lösch die Datei jarvis/legacy/old_router.py",
        "instruct it to rename _fuse_ranked to _merge_ranked",
    ):
        verdict = direct_delivery.decide(said)
        assert verdict.direct is False, said
        assert "prompt to be written" in verdict.reason


def test_the_handover_verb_survives_addressing_extraction() -> None:
    """The spawn/addressing parsers strip the briefing verb OUT of the
    instruction — live 2026-08-15, "prompt to do in deep dive ..." reached
    ``decide`` as "do in deep dive ...". Only the original utterance still
    carries the order, so ``decide`` must read it too.
    """
    verdict = direct_delivery.decide(
        "run pytest on tests/unit/agentic_ide",
        utterance="open another terminal and prompt it to run pytest on tests/unit/agentic_ide",
    )
    assert verdict.direct is False
    assert "prompt to be written" in verdict.reason


def test_the_handover_verbs_match_the_intent_modules_list() -> None:
    """One vocabulary, two modules — parity is what the veto's comment claims."""
    from jarvis.agentic_ide import intent

    assert (
        direct_delivery._HANDOVER_RE.pattern  # noqa: SLF001
        == intent._BRIEFING_VERB_RE.pattern  # noqa: SLF001
    )


def test_a_described_task_is_too_long_to_take_at_face_value() -> None:
    said = "run " + " ".join(f"thing{i}" for i in range(direct_delivery.MAX_DIRECT_WORDS + 5))
    assert direct_delivery.decide(said).direct is False


def test_nothing_at_all_is_not_a_direct_order() -> None:
    assert direct_delivery.decide("").direct is False
    assert direct_delivery.decide("   ").direct is False


def test_a_capitalised_word_is_not_a_concrete_target() -> None:
    """Every sentence starts with one, so it must not count as evidence."""
    assert direct_delivery.has_concrete_target("Run The Thing") is False
    assert direct_delivery.has_concrete_target("run shells.py") is True
    assert direct_delivery.has_concrete_target("run the _fuse_ranked helper") is True


# --------------------------------------------------------------------------
# The wiring: a direct order must not touch a provider at all
# --------------------------------------------------------------------------


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
    """Stands in for a resolved quality-tier Brain."""

    name = "test-writer"


@pytest.fixture()
def workspace(tmp_path: Path) -> _Session:
    (tmp_path / "jarvis" / "terminal").mkdir(parents=True)
    (tmp_path / "jarvis" / "terminal" / "shells.py").write_text("def default_shell(): ...")
    file_index.reset_cache()
    session = _Session(folder=str(tmp_path))
    file_index._CACHE[str(Path(session.folder))] = file_index.build_index(  # noqa: SLF001
        session.folder
    )
    return session


@pytest.fixture()
def refuse_every_provider(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make any provider contact a test failure, and record the attempts.

    The point of the fast lane is not that it is quicker at asking a writer —
    it is that it never asks. Asserting on elapsed time would pin a machine's
    speed; asserting that nothing was resolved and nothing was composed pins the
    behaviour that MAKES it quick.
    """
    contacted: list[str] = []

    def _no_writer():  # noqa: ANN202 - mirrors _resolve_writer's untyped pair
        contacted.append("resolve_writer")
        return _Writer(), "test"

    async def _no_compose(**kwargs: object) -> str:
        contacted.append("llm_compose")
        # Shaped so ``_brief_defect`` accepts it: a heading, and a last line
        # that ends on punctuation. A fake the composer rejects would send
        # every one of these tests down the fallback path and hide what they
        # are actually asserting.
        return "## Task\nWrite the thing the order asked for."

    monkeypatch.setattr(prompt_composer, "_resolve_writer", _no_writer)
    monkeypatch.setattr(prompt_composer, "_llm_compose", _no_compose)
    return contacted


async def test_a_direct_order_never_resolves_a_writer(
    workspace: _Session, refuse_every_provider: list[str]
) -> None:
    result = await prompt_composer.compose(
        "run pytest on jarvis/terminal/shells.py",
        session=workspace,
        terminal_name="T4",
    )
    assert result.composed_by == "direct"
    assert refuse_every_provider == [], "the fast lane contacted a provider"
    assert "run pytest" in result.text
    assert result.text.startswith("## Task")


async def test_a_direct_order_carries_the_file_it_named(
    workspace: _Session, refuse_every_provider: list[str]
) -> None:
    """A path the user said out loud still reaches the agent as an `@` reference."""
    result = await prompt_composer.compose(
        "open jarvis/terminal/shells.py and add a docstring to default_shell",
        session=workspace,
        terminal_name="T4",
    )
    assert result.composed_by == "direct"
    assert result.files == ["jarvis/terminal/shells.py"]
    assert "@jarvis/terminal/shells.py" in result.text


async def test_a_direct_order_attaches_nothing_it_did_not_name(
    workspace: _Session, refuse_every_provider: list[str]
) -> None:
    """Unfiltered index suggestions are worse than none — the anti-noise guard.

    On the briefed path a model reads the candidates and keeps the ones that
    matter. Nothing does that here, so "commit and push" pulled in
    ``@mcps/ollama/tools/push.json`` and ``@scripts/auto-push-eod.ps1`` on the
    real repository — under a heading telling the agent to start with them.
    """
    result = await prompt_composer.compose(
        "commit and push", session=workspace, terminal_name="T4"
    )
    assert result.composed_by == "direct"
    assert result.files == []
    assert "Key files" not in result.text
    assert result.text.strip() == "## Task\ncommit and push"


async def test_a_named_path_that_does_not_exist_is_dropped(
    workspace: _Session, refuse_every_provider: list[str]
) -> None:
    """A misheard path must not become a dead `@ref` the agent wastes a turn on."""
    result = await prompt_composer.compose(
        "open jarvis/terminal/shellz.py and read it",
        session=workspace,
        terminal_name="T4",
    )
    assert result.composed_by == "direct"
    assert result.files == []
    assert "@" not in result.text


async def test_a_vague_order_still_goes_to_the_writer(
    workspace: _Session, refuse_every_provider: list[str]
) -> None:
    result = await prompt_composer.compose(
        "have a look at the wake word thing and see if something is broken",
        session=workspace,
        terminal_name="T4",
    )
    assert result.composed_by == "llm"
    assert refuse_every_provider == ["resolve_writer", "llm_compose"]


async def test_prompting_by_voice_still_gets_a_written_brief(
    workspace: _Session, refuse_every_provider: list[str]
) -> None:
    """The live 2026-08-15 shape: the spawn parser hands ``compose`` an
    instruction with the briefing verb already stripped, so only the utterance
    shows that the user ordered a written prompt. The fast lane must stand down.
    """
    result = await prompt_composer.compose(
        "open another terminal and prompt it to run pytest on jarvis/terminal/shells.py",
        session=workspace,
        terminal_name="T7",
        instruction="run pytest on jarvis/terminal/shells.py",
    )
    assert result.composed_by == "llm"
    assert refuse_every_provider == ["resolve_writer", "llm_compose"]


async def test_the_switch_puts_every_order_back_through_the_writer(
    workspace: _Session,
    refuse_every_provider: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prompt_composer, "_direct_delivery_enabled", lambda: False)
    result = await prompt_composer.compose(
        "run pytest on jarvis/terminal/shells.py",
        session=workspace,
        terminal_name="T4",
    )
    assert result.composed_by == "llm"
    assert "resolve_writer" in refuse_every_provider


async def test_a_pinned_writer_is_never_skipped(
    workspace: _Session, refuse_every_provider: list[str]
) -> None:
    """Naming a writer is a decision about this prompt, not ours to skip."""
    result = await prompt_composer.compose(
        "run pytest on jarvis/terminal/shells.py",
        session=workspace,
        terminal_name="T4",
        brain=_Writer(),
    )
    assert result.composed_by == "llm"
    assert refuse_every_provider == ["llm_compose"]


async def test_the_fast_lane_costs_no_measurable_time(
    workspace: _Session, refuse_every_provider: list[str]
) -> None:
    """A regression guard on the ONE property the lane exists for.

    The bound is deliberately loose — two seconds against a measured 1 ms on the
    real repository — because a tight one would pin the speed of whichever
    machine runs CI rather than the behaviour. What it actually catches is the
    shape of the regression that would matter: something reintroducing a
    provider call, a directory walk or a file-index build onto this path, all of
    which cost orders of magnitude more than the margin here.
    """
    started = time.perf_counter()
    for _ in range(10):
        result = await prompt_composer.compose(
            "run pytest on jarvis/terminal/shells.py",
            session=workspace,
            terminal_name="T4",
        )
        assert result.composed_by == "direct"
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"ten direct deliveries took {elapsed:.2f}s"


async def test_the_progress_beat_says_it_was_sent_as_spoken(
    workspace: _Session, refuse_every_provider: list[str]
) -> None:
    """A fast delivery and a degraded one must not look the same on screen."""
    seen: list[tuple[str, str]] = []
    await prompt_composer.compose(
        "commit and push",
        session=workspace,
        terminal_name="T4",
        on_progress=lambda notice: seen.append((notice.stage, notice.message)),
    )
    stages = [stage for stage, _ in seen]
    assert stages == [prompt_composer.STAGE_DIRECT]
    assert prompt_composer.STAGE_FALLBACK not in stages
    assert "T4" in seen[0][1]
