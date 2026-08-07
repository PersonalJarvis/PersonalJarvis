"""End-to-end guard: the injector must not personalize unrelated questions.

Reported case (maintainer, 2026-07-25): asking for the tallest building in the
world came back as advice to drive there in the cars the user owns. The vault
genuinely contains the car fact; the defect is that an unrelated question
retrieved it and the model treated the injected block as relevant.

These tests drive the real ``WikiContextInjector`` against a fake vault search,
so they cover the wiring (gate -> search -> filter -> framing), not just the
gate helpers.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from jarvis.brain.wiki_context import WikiContextInjector

BASE_PROMPT = "You are Personal Jarvis."


@dataclass(frozen=True)
class FakeHit:
    title: str
    snippet: str
    score: float


class FakeVaultSearch:
    """Records every query it is asked for and replays canned hits."""

    def __init__(self, hits: list[FakeHit] | None = None) -> None:
        self._hits = hits or []
        self.queries: list[str] = []

    def search(self, query: str, *, k: int = 5) -> list[FakeHit]:
        self.queries.append(query)
        return list(self._hits[:k])


CAR_NOTE = FakeHit(
    title="Cars",
    snippet="The user owns six Bugattis and keeps them in the world's nicest garage.",
    score=0.9,
)


@pytest.mark.asyncio
async def test_general_knowledge_question_never_injects_unrelated_notes() -> None:
    """The reported case. Retrieval-first: the vault IS searched now (a
    world-shaped question can name a vault person), but an unrelated note
    must still never reach the prompt — the strict bar carries the defense."""
    search = FakeVaultSearch([CAR_NOTE])
    injector = WikiContextInjector(search=search, latency_budget_ms=500)

    result = await injector.maybe_inject(
        user_text="Was ist der hoechste Turm der Welt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert result == BASE_PROMPT, "an unrelated question must not be given context"
    assert search.queries, "retrieval-first: the vault is searched"
    assert "Bugatti" not in result


@pytest.mark.asyncio
async def test_english_general_knowledge_question_is_also_clean() -> None:
    search = FakeVaultSearch([CAR_NOTE])
    injector = WikiContextInjector(search=search, latency_budget_ms=500)

    result = await injector.maybe_inject(
        user_text="What is the tallest tower in the world?",
        system_prompt=BASE_PROMPT,
    )

    assert result == BASE_PROMPT
    assert search.queries, "retrieval-first: the vault is searched"
    assert "Bugatti" not in result


@pytest.mark.asyncio
async def test_world_shaped_question_about_a_vault_person_gets_its_page() -> None:
    """ "Who is X?" has a general-knowledge shape and used to be dropped
    unsearched — the exact class of miss the recall audit found. When X is a
    vault entity, the page covers the question fully and clears the strict bar."""
    bruno = FakeHit(
        title="Bruno",
        snippet="Bruno is the user's climbing partner from Hamburg.",
        score=0.9,
    )
    search = FakeVaultSearch([bruno])
    injector = WikiContextInjector(search=search, latency_budget_ms=500)

    result = await injector.maybe_inject(
        user_text="Wer ist eigentlich Bruno?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert "climbing partner" in result
    assert search.queries, "the vault is searched for the strict probe"


@pytest.mark.asyncio
async def test_umlaut_spelling_reaches_the_vault() -> None:
    """Real STT output carries umlauts. Regression for the fold defect that
    made every umlaut phrase ("Wofür", "Worüber") invisible to the gate and
    kept umlaut stopwords ("für") alive as junk keywords."""
    project = FakeHit(
        title="Zweitrechner",  # i18n-allow: German vault page title under test
        snippet="Der Zweitrechner dient als Render-Knoten.",  # i18n-allow: German vault content
        score=0.9,
    )
    search = FakeVaultSearch([project])
    injector = WikiContextInjector(search=search, latency_budget_ms=500)

    result = await injector.maybe_inject(
        user_text="Wofür brauche ich meinen Zweitrechner?",  # i18n-allow: German input
        system_prompt=BASE_PROMPT,
    )

    assert "Render-Knoten" in result
    assert search.queries == ["Zweitrechner"], (
        "the umlaut question word and the possessive must be filtered, "
        f"got {search.queries}"
    )


@pytest.mark.asyncio
async def test_a_genuine_personal_question_still_gets_its_context() -> None:
    """The gate must not be a mute button — real memory questions still work."""
    hit = FakeHit(
        title="Zahnarzt",  # i18n-allow: German vault page title under test
        snippet="Zahnarzt Dr. Meier, Praxis am Markt.",  # i18n-allow: German vault content
        score=0.9,
    )
    search = FakeVaultSearch([hit])
    injector = WikiContextInjector(search=search, latency_budget_ms=500)

    result = await injector.maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert result != BASE_PROMPT
    assert "Dr. Meier" in result
    assert search.queries, "a personal question must reach the vault"


@pytest.mark.asyncio
async def test_injected_block_carries_permission_to_ignore_it() -> None:
    hit = FakeHit(
        title="Zahnarzt", snippet="Dr. Meier", score=0.9
    )  # i18n-allow: German vault page title under test
    injector = WikiContextInjector(search=FakeVaultSearch([hit]), latency_budget_ms=500)

    result = await injector.maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    lowered = result.lower()
    assert "may have nothing to do" in lowered
    assert "ignore it completely" in lowered


@pytest.mark.asyncio
async def test_irrelevant_hit_on_a_personal_question_is_filtered_out() -> None:
    """Gate 2: the question is personal, but what came back is not an answer."""
    search = FakeVaultSearch([CAR_NOTE])
    injector = WikiContextInjector(search=search, latency_budget_ms=500)

    result = await injector.maybe_inject(
        user_text="Wann war ich zuletzt beim Zahnarzt Meier?",  # i18n-allow: German input
        system_prompt=BASE_PROMPT,
    )

    assert search.queries, "the question is personal, so the search does run"
    assert result == BASE_PROMPT, "but an unrelated hit must not be injected"
    assert "Bugatti" not in result


@pytest.mark.asyncio
async def test_gate_can_be_switched_off_from_config() -> None:
    """The escape hatch restores the old behaviour, provably."""
    search = FakeVaultSearch([CAR_NOTE])
    injector = WikiContextInjector(search=search, latency_budget_ms=500, relevance_gate=False)

    result = await injector.maybe_inject(
        user_text="Was ist der hoechste Turm der Welt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert search.queries, "gate off => the vault is searched on every turn"
    assert "Bugatti" in result, "gate off => the old unfiltered behaviour"


@pytest.mark.asyncio
async def test_a_broken_search_never_breaks_the_turn() -> None:
    class ExplodingSearch:
        def search(self, query: str, *, k: int = 5):
            raise RuntimeError("vault is on fire")

    injector = WikiContextInjector(search=ExplodingSearch(), latency_budget_ms=500)

    result = await injector.maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert result == BASE_PROMPT
