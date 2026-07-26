"""The context injector in UltraWiki mode — same discipline, other memory.

Two things must hold at once here, and they pull in opposite directions:

1. In UltraWiki mode the injector must actually SEE the ingested store,
   otherwise the brain keeps claiming ignorance of thousands of items.
2. It must stay as silent as the vault path was after the "Bugatti case" fix
   (2026-07-25): an unsolicited surface never volunteers a personal fact the
   question did not ask for. UltraWiki's fused score is ordinal and cannot
   certify relevance, so the gates that survive here are the ones that never
   depended on a score — the keyword leg and query-term coverage.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from jarvis.brain.wiki_context import WikiContextInjector
from jarvis.ultrawiki.service import set_active_service

BASE_PROMPT = "You are Personal Jarvis."


@dataclass(frozen=True)
class _VaultHit:
    title: str
    snippet: str
    score: float


class _SpyVaultSearch:
    def __init__(self, hits: list[_VaultHit] | None = None) -> None:
        self._hits = hits or []
        self.queries: list[str] = []

    def search(self, query: str, *, k: int = 5) -> list[_VaultHit]:
        self.queries.append(query)
        return list(self._hits[:k])


@dataclass(frozen=True)
class _UltraHit:
    """Shaped like ``jarvis.ultrawiki.types.SearchResult``."""

    title: str
    snippet: str
    score: float = 0.031
    source_id: str = "jarvis-conversations"
    matched_by: tuple[str, ...] = field(default_factory=lambda: ("keyword", "vector"))
    permalink: str = "uw://item/1"
    timestamp_utc: str = "2026-07-20T10:00:00Z"


DENTIST = _UltraHit(
    title="Zahnarzt",  # i18n-allow: German vault page title under test
    snippet="Zahnarzt Dr. Meier, Praxis am Markt.",  # i18n-allow: German content
)
CAR_NOTE = _UltraHit(
    title="Cars",
    snippet="The user owns six Bugattis and keeps them in a very nice garage.",
)


class _FakeUltraService:
    def __init__(
        self,
        hits: list[Any] | None = None,
        *,
        error: Exception | None = None,
        delay_s: float = 0.0,
    ) -> None:
        self._hits = hits if hits is not None else []
        self._error = error
        self._delay_s = delay_s
        self._store = object()
        self.calls: list[dict[str, Any]] = []

    def _uw_enabled(self) -> bool:
        return True

    async def search(self, **kwargs: Any) -> list[Any]:
        self.calls.append(dict(kwargs))
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._error is not None:
            raise self._error
        return list(self._hits)


@pytest.fixture(autouse=True)
def _clean_seam(monkeypatch: pytest.MonkeyPatch):
    from jarvis.core import runtime_refs

    monkeypatch.setattr(runtime_refs, "get_web_app", lambda: None)
    set_active_service(None)
    yield
    set_active_service(None)


def _injector(vault: _SpyVaultSearch, **kwargs: Any) -> WikiContextInjector:
    kwargs.setdefault("latency_budget_ms", 500)
    kwargs.setdefault("ultra_latency_budget_ms", 500)
    return WikiContextInjector(search=vault, **kwargs)


# ---------------------------------------------------------------------------
# Retrieval switches, gates do not
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ultra_mode_injects_from_the_store_and_skips_the_vault() -> None:
    vault = _SpyVaultSearch([_VaultHit("Stale", "old vault text", 0.9)])
    ultra = _FakeUltraService([DENTIST])
    set_active_service(ultra)

    result = await _injector(vault).maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert "Dr. Meier" in result
    assert vault.queries == [], "Ultra mode must not also read the vault (D-5)"
    assert "old vault text" not in result


@pytest.mark.asyncio
async def test_retrieval_stays_cheap_no_rerank_no_expansion() -> None:
    ultra = _FakeUltraService([DENTIST])
    set_active_service(ultra)

    await _injector(_SpyVaultSearch()).maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert ultra.calls, "the store must be queried"
    call = ultra.calls[0]
    assert call["rerank"] is False, "a rerank call is a model call on the voice path"
    assert call["expand_context"] is False
    assert call["enforce_floor"] is False
    assert call["k"] <= 5
    # The vector leg gets HALF the overall budget: a slow query embedding
    # must cost the consensus ordering, never the keyword hits (the outer
    # wait_for would otherwise cancel the whole search on a slow provider).
    assert call["vector_timeout_s"] == pytest.approx(0.25)
    assert isinstance(call["timings"], dict)


@pytest.mark.asyncio
async def test_the_injected_block_still_carries_permission_to_ignore_it() -> None:
    set_active_service(_FakeUltraService([DENTIST]))

    result = await _injector(_SpyVaultSearch()).maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    lowered = result.lower()
    assert "may have nothing to do" in lowered
    assert "ignore it completely" in lowered


@pytest.mark.asyncio
async def test_general_knowledge_never_reaches_the_store() -> None:
    """Gate 1 is retrieval-agnostic — it runs before either memory."""
    ultra = _FakeUltraService([CAR_NOTE])
    set_active_service(ultra)

    result = await _injector(_SpyVaultSearch()).maybe_inject(
        user_text="What is the tallest tower in the world?",
        system_prompt=BASE_PROMPT,
    )

    assert result == BASE_PROMPT
    assert ultra.calls == [], "an unrelated question must not even query the store"


@pytest.mark.asyncio
async def test_an_off_topic_store_hit_is_not_injected() -> None:
    """The Bugatti case, through UltraWiki: retrieval always returns something."""
    ultra = _FakeUltraService([CAR_NOTE])
    set_active_service(ultra)

    result = await _injector(_SpyVaultSearch()).maybe_inject(
        user_text="Wann war ich zuletzt beim Zahnarzt Meier?",  # i18n-allow: German input
        system_prompt=BASE_PROMPT,
    )

    assert ultra.calls, "the question is personal, so the store IS queried"
    assert result == BASE_PROMPT
    assert "Bugatti" not in result


@pytest.mark.asyncio
async def test_a_vector_only_hit_is_not_injected() -> None:
    """No lexical evidence and an ordinal score cannot certify relevance."""
    hit = _UltraHit(
        title="Zahnarzt",  # i18n-allow: German vault page title under test
        snippet="Zahnarzt Dr. Meier, Praxis am Markt.",  # i18n-allow: German content
        matched_by=("vector",),
    )
    set_active_service(_FakeUltraService([hit]))

    result = await _injector(_SpyVaultSearch()).maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert result == BASE_PROMPT
    assert "Dr. Meier" not in result


@pytest.mark.asyncio
async def test_a_hit_that_reports_no_legs_is_judged_on_coverage_alone() -> None:
    """A store that does not label its legs must not be silently muted."""
    set_active_service(_FakeUltraService([replace(DENTIST, matched_by=())]))

    result = await _injector(_SpyVaultSearch()).maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert "Dr. Meier" in result


# ---------------------------------------------------------------------------
# The turn is never blocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_slow_store_injects_nothing_and_raises_nothing() -> None:
    ultra = _FakeUltraService([DENTIST], delay_s=1.0)
    set_active_service(ultra)
    injector = _injector(_SpyVaultSearch(), ultra_latency_budget_ms=20)

    result = await injector.maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert result == BASE_PROMPT


@pytest.mark.asyncio
async def test_a_broken_store_injects_nothing_and_raises_nothing() -> None:
    set_active_service(_FakeUltraService(error=RuntimeError("store gone")))

    result = await _injector(_SpyVaultSearch()).maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert result == BASE_PROMPT


@pytest.mark.asyncio
async def test_an_empty_store_injects_nothing() -> None:
    set_active_service(_FakeUltraService([]))

    result = await _injector(_SpyVaultSearch()).maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert result == BASE_PROMPT


# ---------------------------------------------------------------------------
# Mode off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_off_still_reads_the_vault() -> None:
    vault = _SpyVaultSearch(
        [
            _VaultHit(
                title="Zahnarzt",  # i18n-allow: German vault page title under test
                snippet="Zahnarzt Dr. Meier, Praxis am Markt.",  # i18n-allow: German
                score=0.9,
            )
        ]
    )

    result = await _injector(vault).maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert vault.queries, "with the mode off the vault is the memory"
    assert "Dr. Meier" in result


@pytest.mark.asyncio
async def test_a_registered_but_disabled_service_leaves_the_vault_in_charge() -> None:
    class _Disabled(_FakeUltraService):
        def _uw_enabled(self) -> bool:
            return False

    vault = _SpyVaultSearch(
        [
            _VaultHit(
                title="Zahnarzt",  # i18n-allow: German vault page title under test
                snippet="Zahnarzt Dr. Meier, Praxis am Markt.",  # i18n-allow: German
                score=0.9,
            )
        ]
    )
    ultra = _Disabled([CAR_NOTE])
    set_active_service(ultra)

    result = await _injector(vault).maybe_inject(
        user_text="Wie heisst mein Zahnarzt?",  # i18n-allow: German user input under test
        system_prompt=BASE_PROMPT,
    )

    assert ultra.calls == []
    assert vault.queries
    assert "Dr. Meier" in result
