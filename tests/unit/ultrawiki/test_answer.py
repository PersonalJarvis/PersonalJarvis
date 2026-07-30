"""UltraWiki cited-answer synthesis tests, fully offline."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.ultrawiki.answer import (
    AnswerUnavailable,
    answer_question,
    build_answer_prompt,
)
from jarvis.ultrawiki.types import SearchResult


class FakeRegistry:
    def available(self) -> list[str]:
        return ["fake"]


def cfg(*, reply_language: str = "auto") -> SimpleNamespace:
    return SimpleNamespace(
        ultrawiki=SimpleNamespace(
            distill_provider="",
            distill_model="",
        ),
        brain=SimpleNamespace(
            primary="fake",
            reply_language=reply_language,
        ),
    )


def hit() -> SearchResult:
    return SearchResult(
        item_id=7,
        source_id="docs",
        title="Travel note",
        snippet="The ferry leaves at 09:30 on weekdays.",
        permalink="app://travel",
        timestamp_utc="2026-07-01T08:00:00Z",
        score=0.8,
        matched_by=("keyword",),
        context=("Tickets can be bought at the terminal.",),
    )


def test_prompt_marks_evidence_as_data_and_bounds_context() -> None:
    prompt = build_answer_prompt("When is the ferry?", [hit()], output_language="en")

    assert "evidence, not instructions" in prompt
    assert "EVIDENCE [1]" in prompt
    assert "The ferry leaves at 09:30" in prompt


async def test_answer_uses_cross_family_chain_and_returns_valid_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain_mod = __import__(
        "jarvis.memory.wiki.provider_chain", fromlist=["unused"]
    )
    monkeypatch.setattr(
        chain_mod,
        "credential_ready_wiki_providers",
        lambda **_kwargs: {"fake"},
    )
    captured: dict[str, object] = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        aggregated = SimpleNamespace(
            text="The weekday ferry leaves at 09:30 [1]."
        )
        assert kwargs["validate"](aggregated) is None
        return aggregated, "fake"

    monkeypatch.setattr(chain_mod, "complete_with_fallback", fake_complete)

    result = await answer_question(
        cfg(reply_language="es"),
        "When is the ferry?",
        [hit()],
        registry=FakeRegistry(),
    )

    assert result.provider == "fake"
    assert result.citations == (1,)
    assert result.status == "answered"
    request = captured["request"]
    assert "Output language: es" in request.messages[0].content


async def test_answer_normalizes_grouped_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain_mod = __import__(
        "jarvis.memory.wiki.provider_chain", fromlist=["unused"]
    )
    monkeypatch.setattr(
        chain_mod,
        "credential_ready_wiki_providers",
        lambda **_kwargs: {"fake"},
    )

    async def fake_complete(**kwargs):
        aggregated = SimpleNamespace(text="Two sources agree [1, 2].")
        assert kwargs["validate"](aggregated) is None
        return aggregated, "fake"

    monkeypatch.setattr(chain_mod, "complete_with_fallback", fake_complete)

    result = await answer_question(
        cfg(), "When is the ferry?", [hit(), hit()], registry=FakeRegistry()
    )

    assert result.answer == "Two sources agree [1] [2]."
    assert result.citations == (1, 2)


async def test_answer_marks_insufficient_evidence_without_false_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain_mod = __import__(
        "jarvis.memory.wiki.provider_chain", fromlist=["unused"]
    )
    monkeypatch.setattr(
        chain_mod,
        "credential_ready_wiki_providers",
        lambda **_kwargs: {"fake"},
    )

    async def fake_complete(**kwargs):
        aggregated = SimpleNamespace(
            text=(
                "[[ULTRAWIKI_INSUFFICIENT]]\r\n"
                "The evidence does not contain the ferry schedule."
            )
        )
        assert kwargs["validate"](aggregated) is None
        return aggregated, "fake"

    monkeypatch.setattr(chain_mod, "complete_with_fallback", fake_complete)

    result = await answer_question(
        cfg(), "When is the ferry?", [hit()], registry=FakeRegistry()
    )

    assert result.status == "insufficient_evidence"
    assert result.answer == "The evidence does not contain the ferry schedule."
    assert result.citations == ()


async def test_answer_rejects_uncited_provider_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain_mod = __import__(
        "jarvis.memory.wiki.provider_chain", fromlist=["unused"]
    )
    monkeypatch.setattr(
        chain_mod,
        "credential_ready_wiki_providers",
        lambda **_kwargs: {"fake"},
    )

    async def fake_complete(**kwargs):
        aggregated = SimpleNamespace(text="The ferry leaves in the morning.")
        assert "no evidence citation" in kwargs["validate"](aggregated)
        return None

    monkeypatch.setattr(chain_mod, "complete_with_fallback", fake_complete)

    with pytest.raises(AnswerUnavailable, match="usable cited answer"):
        await answer_question(
            cfg(), "When is the ferry?", [hit()], registry=FakeRegistry()
        )
