"""Unit tests for ``jarvis.memory.wiki.extractor`` — Stage-1 fact extraction.

The extractor is ADD-only: one cheap LLM call per eligible conversation turn,
0..N atomic candidate facts appended to the journal, never a vault write.
Provider/model resolve through the same hook as the curator (the Wiki
settings card drives both stages).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    MemoryConfig,
    WikiMemoryConfig,
)
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.wiki.extractor import (
    ConversationContextTurn,
    ConversationFactExtractor,
)
from jarvis.memory.wiki.journal import CandidateJournal


class FakeBrain:
    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    def __init__(
        self,
        response_text: str,
        *,
        finish_reason: str = "stop",
        sleep_s: float = 0.0,
    ) -> None:
        self.response_text = response_text
        self.finish_reason = finish_reason
        self.sleep_s = sleep_s
        self.received_requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.received_requests.append(req)
        if self.sleep_s:
            await asyncio.sleep(self.sleep_s)
        yield BrainDelta(content=self.response_text)
        yield BrainDelta(finish_reason=self.finish_reason)

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class FakeRegistry:
    def __init__(self, brain: Any) -> None:
        self._brain = brain
        self.instantiate_calls: list[tuple[str, dict[str, Any]]] = []

    def available(self) -> set[str]:
        # Only the configured primary is reachable, so the key-aware fallback
        # chain is a single hop — the existing assertions on the first (only)
        # instantiated provider still hold.
        return {"gemini"}

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.instantiate_calls.append((name, dict(kwargs)))
        return self._brain


class ScriptedRegistry:
    """Return a distinct scripted brain for each provider attempt."""

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses
        self.tried: list[str] = []

    def available(self) -> set[str]:
        return set(self._responses)

    def instantiate(self, name: str, **_kwargs: Any) -> Any:
        self.tried.append(name)
        return FakeBrain(self._responses[name])


def _config() -> JarvisConfig:
    return JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(wiki=WikiMemoryConfig()),
    )


def _ok_facts_json(evidence_turn_id: str = "h1") -> str:
    return json.dumps(
        [
            {
                "fact": "Lena moved to Hamburg.",
                "kind": "person",
                "subjects": ["lena"],
                "evidence_turn_id": evidence_turn_id,
            },
            {
                "fact": "User prefers dark mode.",
                "kind": "preference",
                "subjects": ["ruben"],
                "evidence_turn_id": evidence_turn_id,
            },
        ]
    )


@pytest.fixture
def journal(tmp_path: Path) -> CandidateJournal:
    j = CandidateJournal(tmp_path / "jarvis.db")
    yield j
    j.close()


@pytest.mark.asyncio
async def test_happy_path_appends_parsed_facts(journal: CandidateJournal) -> None:
    brain = FakeBrain(_ok_facts_json())
    registry = FakeRegistry(brain)
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=registry,
    )

    n = await extractor.extract_and_journal(
        "My friend Lena moved to Hamburg and I prefer dark mode.",
        "Noted - Lena is in Hamburg now.",
        source_label="voice-fact:1",
        turn_hash="h1",
    )

    assert n == 2
    rows = journal.pending()
    assert [r.fact for r in rows] == [
        "Lena moved to Hamburg.",
        "User prefers dark mode.",
    ]
    assert rows[0].kind == "person"
    assert rows[0].subjects == ("lena",)
    assert rows[0].evidence_turn_id == "h1"
    assert "My friend Lena moved to Hamburg" in rows[0].evidence_excerpt
    assert "Noted - Lena" not in rows[0].evidence_excerpt
    # The cheap router-tier model was requested, not the frontier chat model.
    assert registry.instantiate_calls
    name, kwargs = registry.instantiate_calls[0]
    assert name == "gemini"
    assert kwargs.get("model") == "gemini-3-flash-preview"


@pytest.mark.parametrize(
    ("incomplete_kind", "incomplete_subjects"),
    [
        ("place", ["user"]),
        ("place", ["san-francisco"]),
        ("other", ["user", "san-francisco"]),
    ],
)
@pytest.mark.asyncio
async def test_residence_requires_named_place_subject_and_falls_back(
    journal: CandidateJournal,
    incomplete_kind: str,
    incomplete_subjects: list[str],
) -> None:
    incomplete = json.dumps(
        [
            {
                "fact": "The user lives in San Francisco.",
                "kind": incomplete_kind,
                "subjects": incomplete_subjects,
                "evidence_turn_id": "residence-turn",
            }
        ]
    )
    complete = json.dumps(
        [
            {
                "fact": "The user lives in San Francisco.",
                "kind": "place",
                "subjects": ["user", "san-francisco"],
                "evidence_turn_id": "residence-turn",
            }
        ]
    )
    registry = ScriptedRegistry(
        {"gemini": incomplete, "openrouter": complete}
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=registry,
    )

    count = await extractor.extract_and_journal(
        "Ich wohne in San Francisco.",  # i18n-allow: production residence fixture
        "Noted.",
        source_label="realtime:residence",
        turn_hash="residence-turn",
    )

    assert count == 1
    assert registry.tried == ["gemini", "openrouter"]
    row = journal.pending()[0]
    assert row.kind == "place"
    assert row.subjects == ("user", "san-francisco")


@pytest.mark.asyncio
async def test_short_input_skips_brain_entirely(journal: CandidateJournal) -> None:
    brain = FakeBrain(_ok_facts_json())
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_and_journal(
        "ok", "sure", source_label="voice-fact:2", turn_hash="h2",
    )
    assert n == 0
    assert brain.received_requests == []
    assert journal.backlog_count() == 0


@pytest.mark.asyncio
async def test_truncated_response_is_discarded(journal: CandidateJournal) -> None:
    brain = FakeBrain(_ok_facts_json(), finish_reason="length")
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_and_journal(
        "My friend Lena moved to Hamburg today.",
        "Noted.",
        source_label="voice-fact:3",
        turn_hash="h3",
    )
    assert n == 0
    assert journal.backlog_count() == 0


@pytest.mark.asyncio
async def test_malformed_json_yields_nothing(journal: CandidateJournal) -> None:
    brain = FakeBrain("I think the user likes dark mode but no JSON here.")
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_and_journal(
        "My friend Lena moved to Hamburg today.",
        "Noted.",
        source_label="voice-fact:4",
        turn_hash="h4",
    )
    assert n == 0
    assert journal.backlog_count() == 0


@pytest.mark.asyncio
async def test_ungrounded_consensus_ends_the_chain_without_a_chain_failure(
    journal: CandidateJournal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two families agreeing "nothing groundable in this turn" is a quiet
    no-op, never a chain failure. Live 2026-07-18: an ordinary no-fact turn
    burned all 8 providers (incl. dead rungs and a 30s timeout) and left a
    sticky red 'Chain failure' banner users read as 'Obsidian not connected'."""
    from jarvis.memory.wiki import health as health_module
    from jarvis.memory.wiki.health import WikiHealth

    isolated_health = WikiHealth()
    monkeypatch.setattr(health_module, "health", isolated_health)

    ungrounded = json.dumps(
        [
            {
                "fact": "The assistant guessed something about the user.",
                "kind": "asset",
                "evidence_turn_id": "assistant-turn",
            }
        ]
    )
    registry = ScriptedRegistry(
        {
            # Chain order: primary gemini, then the rest alphabetically —
            # nvidia is the second opinion, openrouter the never-asked third.
            "gemini": ungrounded,
            "nvidia": ungrounded,
            "openrouter": _ok_facts_json(),
        }
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=registry,
    )

    count = await extractor.extract_and_journal(
        "Hmm, interesting.",
        "Indeed.",
        source_label="voice-fact:consensus",
        turn_hash="h-consensus",
    )

    assert count == 0
    assert journal.backlog_count() == 0
    assert registry.tried == ["gemini", "nvidia"]  # consensus stopped the chain
    assert isolated_health.snapshot()["last_chain_failure"] is None


@pytest.mark.asyncio
async def test_parseable_unusable_output_crosses_to_grounded_provider(
    journal: CandidateJournal,
) -> None:
    registry = ScriptedRegistry(
        {
            "gemini": json.dumps(
                [
                    {
                        "fact": "The assistant guessed that the user owns an aircraft.",
                        "kind": "asset",
                        "evidence_turn_id": "assistant-turn",
                    }
                ]
            ),
            "openrouter": json.dumps(
                [
                    {
                        "fact": "The user owns the yacht Aurora.",
                        "kind": "asset",
                        "subjects": ["user", "aurora"],
                        "evidence_turn_id": "grounded-turn",
                    }
                ]
            ),
        }
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=registry,
    )

    count = await extractor.extract_and_journal(
        "I own the yacht Aurora.",
        "Understood.",
        source_label="realtime:semantic-fallback",
        turn_hash="grounded-turn",
    )

    assert count == 1
    assert registry.tried == ["gemini", "openrouter"]
    assert journal.pending()[0].fact == "The user owns the yacht Aurora."


@pytest.mark.asyncio
async def test_code_fenced_json_is_tolerated(journal: CandidateJournal) -> None:
    fenced = "```json\n" + _ok_facts_json("h5") + "\n```"
    brain = FakeBrain(fenced)
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_and_journal(
        "My friend Lena moved to Hamburg and I prefer dark mode.",
        "Noted.",
        source_label="voice-fact:5",
        turn_hash="h5",
    )
    assert n == 2


@pytest.mark.asyncio
async def test_empty_array_is_a_clean_zero(journal: CandidateJournal) -> None:
    brain = FakeBrain("[]")
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_and_journal(
        "It is a bit cloudy today, is it not?",
        "Indeed.",
        source_label="voice-fact:6",
        turn_hash="h6",
    )
    assert n == 0
    assert journal.backlog_count() == 0


@pytest.mark.parametrize(
    "question",
    [
        "What are the benefits of Vitamin D?",
        "Tell me about Monaco.",
    ],
)
@pytest.mark.asyncio
async def test_turn_prompt_blocks_topic_question_personal_inferences(
    journal: CandidateJournal,
    question: str,
) -> None:
    brain = FakeBrain("[]")
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )

    count = await extractor.extract_and_journal(
        question,
        "Here is the requested information.",
        source_label="realtime:topic-question",
        turn_hash=f"topic-question-{len(question)}",
    )

    assert count == 0
    assert journal.pending() == []
    system = brain.received_requests[0].system
    assert "topic mention, one-off question, or request for information" in system
    assert "no basis rescues it" in system
    assert '"What are the benefits of Vitamin D?"' in system
    assert '"Tell me about Monaco."' in system
    assert "personal connection from curiosity" in system
    assert 'lived-experience inference "explicit"' in system
    assert "Score user-centrality" in system
    assert "writing junk is the worse failure" in system


@pytest.mark.parametrize(
    ("question", "proposed_fact"),
    [
        (
            "What are the benefits of Vitamin D?",
            "The user is interested in Vitamin D.",
        ),
        ("Tell me about Monaco.", "The user is interested in Monaco."),
    ],
)
@pytest.mark.asyncio
async def test_topic_question_interest_inference_is_blocked_after_model_output(
    journal: CandidateJournal,
    question: str,
    proposed_fact: str,
) -> None:
    turn_id = f"unsupported-interest-{len(question)}"
    registry = ScriptedRegistry(
        {
            "gemini": json.dumps(
                [
                    {
                        "fact": proposed_fact,
                        "kind": "preference",
                        "subjects": ["user"],
                        "evidence_turn_id": turn_id,
                    }
                ]
            ),
            "openrouter": "[]",
        }
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=registry,
    )

    count = await extractor.extract_and_journal(
        question,
        "Here is the requested information.",
        source_label="realtime:unsupported-interest",
        turn_hash=turn_id,
    )

    assert count == 0
    assert registry.tried == ["gemini", "openrouter"]
    assert journal.pending() == []
    assert journal.capture_summary()["empty"] == 1


@pytest.mark.asyncio
async def test_explicit_interest_assertion_remains_a_candidate(
    journal: CandidateJournal,
) -> None:
    turn_id = "explicit-interest"
    fact = "The user is interested in Monaco."
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": fact,
                    "kind": "preference",
                    "subjects": ["user"],
                    "evidence_turn_id": turn_id,
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )

    count = await extractor.extract_and_journal(
        "I am interested in Monaco.",
        "Understood.",
        source_label="realtime:explicit-interest",
        turn_hash=turn_id,
    )

    assert count == 1
    assert journal.pending()[0].fact == fact


@pytest.mark.parametrize(
    ("statement", "fact", "kind"),
    [
        ("I own a yacht.", "The user owns a yacht.", "asset"),
        (
            "I plan to attend Monaco.",
            "The user plans to attend Monaco.",
            "event",
        ),
    ],
)
@pytest.mark.asyncio
async def test_explicit_ownership_and_plan_self_disclosures_remain_candidates(
    journal: CandidateJournal,
    statement: str,
    fact: str,
    kind: str,
) -> None:
    turn_id = "explicit-self-disclosure"
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": fact,
                    "kind": kind,
                    "subjects": ["user"],
                    "evidence_turn_id": turn_id,
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )

    count = await extractor.extract_and_journal(
        statement,
        "Understood.",
        source_label="realtime:explicit-self-disclosure",
        turn_hash=turn_id,
    )

    assert count == 1
    assert journal.pending()[0].fact == fact


@pytest.mark.asyncio
async def test_asset_kind_and_context_are_preserved(journal: CandidateJournal) -> None:
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": "The user owns the yacht Aurora.",
                    "kind": "asset",
                    "subjects": ["ruben", "aurora"],
                    "evidence_turn_id": "turn-2",
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_and_journal(
        "It is called Aurora.",
        "That is a memorable name.",
        source_label="realtime-aggressive:2",
        turn_hash="hash-2",
        review_key="live:v2:s1:turn-2",
        session_id="s1",
        turn_id="turn-2",
        context_turns=(
            ConversationContextTurn(
                turn_id="turn-1",
                user_text="I own a yacht.",
                assistant_text="What is it called?",
            ),
        ),
    )

    assert n == 1
    row = journal.pending()[0]
    assert row.kind == "asset"
    assert row.evidence_turn_id == "turn-2"
    prompt = brain.received_requests[0].messages[0].content
    assert "USER TURN [turn-1]" in prompt
    assert "FOCUS USER TURN [turn-2]" in prompt
    assert "never evidence" in prompt


@pytest.mark.asyncio
async def test_model_subjects_are_restricted_to_safe_kebab_slugs(
    journal: CandidateJournal,
) -> None:
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": "The user owns the yacht Aurora.",
                    "kind": "asset",
                    "subjects": ["aurora", "../../.env", "C:\\private", "AURORA"],
                    "evidence_turn_id": "subject-guard",
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )

    assert await extractor.extract_and_journal(
        "I own the yacht Aurora.",
        "Understood.",
        source_label="realtime:subject-guard",
        turn_hash="subject-guard",
    ) == 1
    assert journal.pending()[0].subjects == ("aurora",)


@pytest.mark.asyncio
async def test_secret_shaped_model_fact_never_reaches_sqlite(
    journal: CandidateJournal,
) -> None:
    secret = "sk-proj-" + "A" * 30
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": f"The user's API key is {secret}.",
                    "kind": "other",
                    "subjects": ["ruben"],
                    "evidence_turn_id": "secret-guard",
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )

    assert await extractor.extract_and_journal(
        "I accidentally read a credential aloud.",
        "I will not retain it.",
        source_label="realtime:secret-guard",
        turn_hash="secret-guard",
    ) == 0
    assert journal.pending() == []
    raw = journal._conn.execute(  # noqa: SLF001 - privacy persistence probe
        "SELECT GROUP_CONCAT(fact) FROM wiki_candidate_journal"
    ).fetchone()[0]
    assert raw is None or secret not in raw


@pytest.mark.asyncio
async def test_session_sweep_rejects_non_user_evidence(journal: CandidateJournal) -> None:
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": "The assistant guessed that the user owns an aircraft.",
                    "kind": "asset",
                    "subjects": ["ruben"],
                    "evidence_turn_id": "assistant-turn",
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    turns = (
        ConversationContextTurn(
            turn_id="user-turn",
            user_text="What do you think I own?",
            assistant_text="Perhaps an aircraft.",
        ),
    )
    n = await extractor.extract_session_and_journal(
        turns,
        session_id="s1",
        source_label="realtime-session-sweep:s1",
    )

    assert n == 0
    assert journal.backlog_count() == 0
    key = extractor.session_review_keys(turns, session_id="s1")[0]
    assert key.startswith("session:v3:s1:chunk:000:")
    # Parseable but wholly ungrounded model output is retryable provider
    # failure, never a terminal proof that the transcript contained no fact.
    assert journal.capture_seen(key) is False
    assert journal.capture_summary()["failed"] == 1


@pytest.mark.asyncio
async def test_session_sweep_prompt_blocks_topic_to_plan_inference(
    journal: CandidateJournal,
) -> None:
    brain = FakeBrain("[]")
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )

    count = await extractor.extract_session_and_journal(
        (
            ConversationContextTurn(
                "vitamin-turn",
                "What are the benefits of Vitamin D?",
            ),
            ConversationContextTurn("monaco-turn", "Tell me about Monaco."),
        ),
        session_id="topic-questions",
        source_label="realtime-session-sweep:topic-questions",
    )

    assert count == 0
    assert journal.pending() == []
    system = brain.received_requests[0].system
    assert "topic mention, one-off question, or request for information" in system
    assert "no basis rescues it" in system
    assert '"What are the benefits of Vitamin D?"' in system
    assert '"Tell me about Monaco."' in system
    assert 'lived-experience inference "explicit"' in system
    assert "writing junk is the worse failure" in system


@pytest.mark.asyncio
async def test_session_sweep_accepts_exact_user_evidence(journal: CandidateJournal) -> None:
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": "The user's yacht Aurora is moored in Kiel.",
                    "kind": "asset",
                    "subjects": ["ruben", "aurora", "kiel"],
                    "evidence_turn_id": "turn-2",
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    n = await extractor.extract_session_and_journal(
        (
            ConversationContextTurn("turn-1", "I own a yacht called Aurora."),
            ConversationContextTurn("turn-2", "It is moored in Kiel."),
        ),
        session_id="s2",
        source_label="realtime-session-sweep:s2",
    )

    assert n == 1
    assert journal.pending()[0].evidence_turn_id == "turn-2"
    assert "I own a yacht called Aurora." in journal.pending()[0].evidence_excerpt
    assert "It is moored in Kiel." in journal.pending()[0].evidence_excerpt


@pytest.mark.asyncio
async def test_long_prior_context_cannot_truncate_focus_evidence(
    journal: CandidateJournal,
) -> None:
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": "The user's yacht Aurora is moored in Kiel.",
                    "kind": "asset",
                    "subjects": ["ruben", "aurora", "kiel"],
                    "evidence_turn_id": "turn-2",
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    long_prior = "I own a yacht called Aurora. " + ("context " * 1_000)

    count = await extractor.extract_session_and_journal(
        (
            ConversationContextTurn("turn-1", long_prior),
            ConversationContextTurn("turn-2", "It is moored in Kiel."),
        ),
        session_id="long-context",
        source_label="realtime-session-sweep:long-context",
    )

    assert count == 1
    evidence = journal.pending()[0].evidence_excerpt
    assert evidence.startswith(
        "Evidence user turn [turn-2]: It is moored in Kiel."
    )
    assert "Prior user context [turn-1]: I own a yacht called Aurora." in evidence


@pytest.mark.asyncio
async def test_session_chunk_boundary_keeps_user_reference_context(
    journal: CandidateJournal,
) -> None:
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": "The user's yacht is named Aurora.",
                    "kind": "asset",
                    "subjects": ["user", "aurora"],
                    "evidence_turn_id": "turn-17",
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    turns = tuple(
        ConversationContextTurn(
            f"turn-{index}",
            (
                "I own a yacht."
                if index == 16
                else "It is called Aurora."
                if index == 17
                else "No durable statement here."
            ),
        )
        for index in range(1, 18)
    )

    count = await extractor.extract_session_and_journal(
        turns,
        session_id="boundary",
        source_label="realtime-session-sweep:boundary",
    )

    assert count == 1
    assert len(extractor.session_review_keys(turns, session_id="boundary")) == 2
    second_prompt = brain.received_requests[1].messages[0].content
    assert "BOUNDARY USER CONTEXT" in second_prompt
    assert "USER TURN [turn-16]:\nI own a yacht." in second_prompt
    assert "FOCUS SESSION TURNS" in second_prompt
    assert "USER TURN [turn-17]:\nIt is called Aurora." in second_prompt
    evidence = journal.pending()[0].evidence_excerpt
    assert "Prior user context [turn-16]: I own a yacht." in evidence
    assert "Evidence user turn [turn-17]: It is called Aurora." in evidence


# ---------------------------------------------------------------------------
# Evidence-tiered extraction: basis, salience floor, and the behavioral path
# ---------------------------------------------------------------------------

_GOLF_TURN = (
    "I love being out on golf courses with my buddies, "
    "playing this sport actively."
)


def _config_with_extractor(**overrides: Any) -> JarvisConfig:
    from jarvis.core.config import ExtractorConfig

    return JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(
            wiki=WikiMemoryConfig(extractor=ExtractorConfig(**overrides))
        ),
    )


@pytest.mark.asyncio
async def test_behavioral_lived_experience_yields_behavioral_candidate(
    journal: CandidateJournal,
) -> None:
    turn_id = "golf-behavioral"
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": "The user plays golf actively with friends and enjoys it.",
                    "kind": "activity",
                    "subjects": ["user", "golf"],
                    "evidence_turn_id": turn_id,
                    "basis": "behavioral",
                    "salience": 4,
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )

    count = await extractor.extract_and_journal(
        _GOLF_TURN,
        "Sounds like a great weekend.",
        source_label="realtime:golf",
        turn_hash=turn_id,
    )

    assert count == 1
    row = journal.pending()[0]
    assert row.fact == "The user plays golf actively with friends and enjoys it."
    assert row.kind == "activity"
    assert row.basis == "behavioral"
    assert row.salience == 4


@pytest.mark.asyncio
async def test_model_claimed_explicit_basis_is_downgraded_to_behavioral(
    journal: CandidateJournal,
) -> None:
    turn_id = "golf-downgrade"
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": "The user plays golf regularly with friends.",
                    "kind": "activity",
                    "subjects": ["user", "golf"],
                    "evidence_turn_id": turn_id,
                    "basis": "explicit",
                    "salience": 4,
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )

    count = await extractor.extract_and_journal(
        "I was out on the golf course again on Saturday with my buddies.",
        "Nice.",
        source_label="realtime:golf-downgrade",
        turn_hash=turn_id,
    )

    assert count == 1
    # The evidence is a lived-experience report, not a literal assertion:
    # the model must not launder the inference into an explicit basis.
    assert journal.pending()[0].basis == "behavioral"


@pytest.mark.asyncio
async def test_low_salience_world_trivia_is_dropped(
    journal: CandidateJournal,
) -> None:
    from jarvis.memory.wiki.telemetry import telemetry

    turn_id = "trivia"
    blocked_before = telemetry.get("wiki_candidates_blocked_low_salience")
    registry = ScriptedRegistry(
        {
            "gemini": json.dumps(
                [
                    {
                        "fact": "The Eiffel Tower is located in Paris.",
                        "kind": "other",
                        "subjects": ["eiffel-tower"],
                        "evidence_turn_id": turn_id,
                        "basis": "explicit",
                        "salience": 1,
                    }
                ]
            ),
            "openrouter": "[]",
        }
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=registry,
    )

    count = await extractor.extract_and_journal(
        "We talked about the Eiffel Tower being in Paris for my project notes.",
        "Indeed it is.",
        source_label="realtime:trivia",
        turn_hash=turn_id,
    )

    assert count == 0
    assert journal.pending() == []
    assert telemetry.get("wiki_candidates_blocked_low_salience") > blocked_before


@pytest.mark.asyncio
async def test_min_salience_floor_is_configurable(
    journal: CandidateJournal,
) -> None:
    turn_id = "salience-floor"
    brain = FakeBrain(
        json.dumps(
            [
                {
                    "fact": "The user keeps project notes about Paris landmarks.",
                    "kind": "project",
                    "subjects": ["user"],
                    "evidence_turn_id": turn_id,
                    "basis": "explicit",
                    "salience": 2,
                }
            ]
        )
    )
    extractor = ConversationFactExtractor(
        config=_config_with_extractor(min_salience=1),
        journal=journal,
        registry=FakeRegistry(brain),
    )

    count = await extractor.extract_and_journal(
        "I keep project notes about Paris landmarks.",
        "Noted.",
        source_label="realtime:salience-floor",
        turn_hash=turn_id,
    )

    assert count == 1
    assert journal.pending()[0].salience == 2


@pytest.mark.asyncio
async def test_behavioral_inference_flag_off_restores_blocking(
    journal: CandidateJournal,
) -> None:
    turn_id = "golf-flag-off"
    registry = ScriptedRegistry(
        {
            "gemini": json.dumps(
                [
                    {
                        "fact": "The user plays golf regularly with friends.",
                        "kind": "activity",
                        "subjects": ["user", "golf"],
                        "evidence_turn_id": turn_id,
                        "basis": "behavioral",
                        "salience": 4,
                    }
                ]
            ),
            "openrouter": "[]",
        }
    )
    extractor = ConversationFactExtractor(
        config=_config_with_extractor(behavioral_inference=False),
        journal=journal,
        registry=registry,
    )

    count = await extractor.extract_and_journal(
        "I was out on the golf course again on Saturday with my buddies.",
        "Nice.",
        source_label="realtime:golf-flag-off",
        turn_hash=turn_id,
    )

    assert count == 0
    assert journal.pending() == []


@pytest.mark.asyncio
async def test_monaco_question_never_gains_behavioral_basis(
    journal: CandidateJournal,
) -> None:
    """A topic question grounds nothing even when the model claims behavioral."""
    turn_id = "monaco-behavioral-claim"
    registry = ScriptedRegistry(
        {
            "gemini": json.dumps(
                [
                    {
                        "fact": "The user is interested in Monaco.",
                        "kind": "preference",
                        "subjects": ["user", "monaco"],
                        "evidence_turn_id": turn_id,
                        "basis": "behavioral",
                        "salience": 4,
                    }
                ]
            ),
            "openrouter": "[]",
        }
    )
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=registry,
    )

    count = await extractor.extract_and_journal(
        "Tell me about Monaco.",
        "Monaco is a city-state on the French Riviera.",
        source_label="realtime:monaco-behavioral-claim",
        turn_hash=turn_id,
    )

    assert count == 0
    assert registry.tried == ["gemini", "openrouter"]
    assert journal.pending() == []
