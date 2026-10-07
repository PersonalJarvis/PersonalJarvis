"""An agent's memory updates run on exactly that agent's seat, and nothing else.

Maintainer decision (2026-09-30): the Society turn review, Society skill
learning and Jarvis' own learning loop use the provider, model and auth mode
the agent's chat runs on. A seat that cannot answer makes the work wait and
finally drop; it never falls through to another provider. The registry here
records every brain it builds, so "nothing else was asked" is checked by what
was instantiated, not by what answered.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from jarvis.society.review import _ask
from tests.fakes.fake_seat_registry import RecordingRegistry

ALL = ["openai", "gemini", "claude-api", "claude-cli", "codex", "antigravity", "vertex"]


@pytest.fixture
def registry(monkeypatch) -> RecordingRegistry:
    from jarvis.brain import resolver

    reg = RecordingRegistry(ALL)
    monkeypatch.setattr(resolver, "_get_registry", lambda: reg)
    return reg


@pytest.fixture(autouse=True)
def agent_keys(monkeypatch):
    """The Agents-tab key of each family, and a trap for the old fallbacks."""
    from jarvis.brain import resolver
    from jarvis.core import config, runtime_refs

    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: f"agent-key-{provider}")
    monkeypatch.setattr(runtime_refs, "get_brain_manager", lambda: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("a background memory update asked a subscription resolver")

    monkeypatch.setattr(resolver, "resolve_subscription_brain", forbidden)


def _agent(provider: str, model: str = "", agent_id: str = "scout") -> SimpleNamespace:
    return SimpleNamespace(
        provider=provider, model=model, effort="", agent_id=agent_id, account_id=""
    )


def _runtime(cfg=None) -> SimpleNamespace:
    return SimpleNamespace(config=lambda: cfg, skills_for=lambda agent_id: None)


# ── Society turn review ──────────────────────────────────────────────────────


async def test_review_runs_on_the_agents_api_key_and_model(registry):
    result = await _ask(_runtime(), _agent("gemini", "gemini-chosen"), "Evidence")
    assert result == {"memories": [], "skill": None}
    assert [(b.name, b.kwargs) for b in registry.built] == [
        ("gemini", {"model": "gemini-chosen"})
    ]
    [answer] = registry.answered
    assert answer.secrets == {"gemini": "agent-key-gemini"}
    assert answer.caller == "society-review"


async def test_review_on_a_subscription_seat_uses_that_cli_in_structured_mode(registry):
    await _ask(_runtime(), _agent("openai-codex", "gpt-5.5"), "Evidence")
    [built] = registry.built
    assert built.name == "codex"
    assert built.kwargs["model"] == "gpt-5.5"
    assert built.kwargs["structured_prompts"] is True
    assert built.kwargs["prefer_subscription"] is True
    # A subscription seat is not billed to a key: no key override is set.
    assert registry.answered[0].secrets == {}


@pytest.mark.parametrize(
    ("cli_installed", "expected"),
    [(True, "claude-cli"), (False, "claude-api")],
)
async def test_the_claude_row_reviews_where_its_chat_runs(
    registry, monkeypatch, cli_installed, expected
):
    from jarvis.agent_chat import service

    monkeypatch.setattr(service, "_claude_cli_installed", lambda: cli_installed)
    await _ask(_runtime(), _agent("claude-api", "claude-sonnet-5-5"), "Evidence")
    assert [(b.name, b.kwargs.get("model")) for b in registry.built] == [
        (expected, "claude-sonnet-5-5")
    ]


async def test_a_failing_seat_never_reaches_another_provider(registry, caplog):
    registry.failing = frozenset({"openai"})
    with caplog.at_level(logging.DEBUG):
        assert await _ask(_runtime(), _agent("openai", "gpt-chosen"), "Evidence") is None
    assert registry.built_names() == ["openai"]
    assert [a.name for a in registry.answered] == ["openai"]
    # AP-34: the provider's error body never reaches a log line.
    assert "private provider body" not in caplog.text
    assert "sk-leak" not in caplog.text


async def test_an_agent_on_a_cli_without_a_background_brain_asks_nothing(registry):
    assert await _ask(_runtime(), _agent("cursor"), "Evidence") is None
    assert registry.built == []


async def test_an_unset_agent_follows_the_agents_tier_it_chats_on(registry, monkeypatch):
    from jarvis.local_models import assistant_session

    tier = SimpleNamespace(provider="vertex", model="tier-model", ready=True, reason="")
    monkeypatch.setattr(assistant_session, "agents_tier", lambda cfg: tier)
    await _ask(_runtime(), _agent(""), "Evidence")
    assert [(b.name, b.kwargs) for b in registry.built] == [("vertex", {"model": "tier-model"})]


async def test_a_seat_without_a_model_uses_the_chats_router_tier_model(registry):
    from jarvis.brain.manager import get_tier_default_model

    cfg = SimpleNamespace(brain=SimpleNamespace(providers={}))
    await _ask(_runtime(cfg), _agent("gemini"), "Evidence")
    [built] = registry.built
    assert built.kwargs == {"model": get_tier_default_model("router", "gemini")}


# ── the bounded review queue ────────────────────────────────────────────────


@pytest.fixture
async def rt(tmp_path):
    from jarvis.society.runtime import SocietyRuntime

    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    runtime = SocietyRuntime(tmp_path, cfg=lambda: cfg, seed_starter_team=False)
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout", description="Draft only.")
    try:
        yield runtime
    finally:
        await runtime.close()


def _queue(rt, turn_id: str = "turn-1") -> None:
    events = [
        {"kind": "user_message", "payload": {"text": "A normal completed task."}},
        {"kind": "assistant_text", "payload": {"text": "Done."}},
        {"kind": "turn_finished", "payload": {"status": "done"}},
    ]
    rt.conversations.queue_review("society:scout", turn_id, events, direct_user=True)


async def _settle(rt) -> None:
    """Follow the armed wake-ups until none is left."""
    for _ in range(20):
        retry = getattr(rt, "_memory_review_retry", None)
        if retry is None or retry.done():
            return
        await asyncio.wait_for(retry, 5)
    raise AssertionError("the review queue kept waking up")


async def test_a_seat_that_keeps_failing_drops_the_review_after_bounded_retries(
    rt, monkeypatch, caplog
):
    from jarvis.society import review_queue

    attempts = []

    async def seat_down(*args):
        attempts.append(True)
        return None

    rt.turn_reviewer = seat_down
    monkeypatch.setattr(review_queue.random, "uniform", lambda *args: 0)
    _queue(rt)
    with caplog.at_level(logging.WARNING, logger="jarvis.society.review_queue"):
        await rt.recover_reviews()
        await _settle(rt)
    assert len(attempts) == review_queue.MAX_ATTEMPTS
    assert rt.conversations.review_counts("scout") == {"pending": 0, "done": 0, "dropped": 1}
    assert [r for r in caplog.records if "dropped" in r.getMessage()]
    # A dropped review is never retried again.
    await rt.recover_reviews()
    assert len(attempts) == review_queue.MAX_ATTEMPTS


async def test_a_burst_of_turns_calls_the_seat_only_for_due_reviews(rt, monkeypatch):
    from jarvis.society import review_queue

    calls = []

    async def seat_down(*args):
        calls.append(True)
        return None

    rt.turn_reviewer = seat_down
    # The backoff is long: only turn-triggered drains run during the test.
    monkeypatch.setattr(review_queue.random, "uniform", lambda *args: 3600)
    _queue(rt)
    for _ in range(10):
        await rt.recover_reviews()
    assert len(calls) == 1  # The failed review waits out its backoff.
    [pending] = rt.conversations.pending_reviews()
    assert pending["attempts"] == 1


async def test_concurrent_drain_requests_are_coalesced(rt, monkeypatch):
    from jarvis.society import review_queue

    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def slow_seat(*args):
        calls.append(True)
        started.set()
        await release.wait()
        return None

    rt.turn_reviewer = slow_seat
    monkeypatch.setattr(review_queue.random, "uniform", lambda *args: 3600)
    for index in range(3):
        _queue(rt, f"turn-{index}")
    first = asyncio.create_task(rt.recover_reviews())
    await asyncio.wait_for(started.wait(), 5)
    # Ten more finished turns while the seat is still thinking.
    await asyncio.gather(*(rt.recover_reviews() for _ in range(10)))
    release.set()
    await asyncio.wait_for(first, 5)
    # One call per review: the queued requests became one extra pass, and
    # that pass found every review waiting out its backoff.
    assert len(calls) == 3
    assert [p["attempts"] for p in rt.conversations.pending_reviews()] == [1, 1, 1]


async def test_the_attempt_budget_survives_a_restart(tmp_path, monkeypatch):
    from jarvis.society import review_queue
    from jarvis.society.runtime import SocietyRuntime

    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    # A previous run: the agent exists and three attempts are already spent.
    first = SocietyRuntime(tmp_path, cfg=lambda: cfg, seed_starter_team=False)
    await first.ensure_started()
    try:
        await first.roster.create(name="Scout", description="Draft only.")
        _queue(first)
        for _ in range(review_queue.MAX_ATTEMPTS - 1):
            first.conversations.fail_review("society:scout", "turn-1", retry_after_ms=0)
    finally:
        await first.close()

    calls = []

    async def seat_down(*args):
        calls.append(True)
        return None

    monkeypatch.setattr(review_queue.random, "uniform", lambda *args: 0)
    runtime = SocietyRuntime(tmp_path, cfg=lambda: cfg, seed_starter_team=False)
    runtime.turn_reviewer = seat_down
    await runtime.ensure_started()  # Its startup drain runs the review.
    try:
        await runtime.recover_reviews()
        for _ in range(100):
            if not runtime.conversations.pending_reviews():
                break
            await asyncio.sleep(0.02)
        await _settle(runtime)
        assert len(calls) == 1  # Only the last attempt of the budget was left.
        assert runtime.conversations.review_counts("scout") == {
            "pending": 0,
            "done": 0,
            "dropped": 1,
        }
    finally:
        await runtime.close()


def test_an_old_archive_gains_the_retry_columns(tmp_path):
    import sqlite3

    from jarvis.society.conversation import ConversationArchive

    path = tmp_path / "society-conversations.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE reviews (session TEXT NOT NULL, turn_id TEXT NOT NULL, "
            "events TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
            "PRIMARY KEY(session,turn_id))"
        )
        db.execute(
            "INSERT INTO reviews(session,turn_id,events) VALUES(?,?,?)",
            ("society:scout", "old-turn", json.dumps({"events": [], "direct_user": True})),
        )
    archive = ConversationArchive(path)
    try:
        [pending] = archive.pending_reviews()
        assert (pending["turn_id"], pending["attempts"], pending["retry_after_ms"]) == (
            "old-turn",
            0,
            0,
        )
        assert archive.fail_review("society:scout", "old-turn", retry_after_ms=5) == 1
    finally:
        archive.close()
    # Reopening is idempotent.
    ConversationArchive(path).close()


# ── Society skill learning ──────────────────────────────────────────────────


SKILL_DRAFT = json.dumps(
    {
        "name": "Source check",
        "description": "Check the dates of sources before comparing them.",
        "category": "general",
        "tags": ["research", "sources", "dates"],
        "triggers": [{"type": "voice", "pattern": "(source check)", "language": ["en"]}],
        "requires_tools": [],
        "risk_policy": {"default_tier": "monitor"},
        "body": "Check dates.\n\n## Steps\n1. Read each source's date.\n2. Compare.",
    }
)


class _Skills:
    def __init__(self, root) -> None:
        from jarvis.skills.registry import SkillRegistry

        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.registry = SkillRegistry(root, bus=None)
        self.registry.reload_sync()


async def test_skill_learning_authors_on_the_agents_seat_only(registry, monkeypatch, tmp_path):
    from jarvis.agent_chat import runner_brain
    from jarvis.core.model_selection import ModelSelection, use_operation_model
    from jarvis.skills.creator_service import SkillCreatorInput
    from jarvis.society.learning import default_creator_factory

    def jarvis_brain(*args, **kwargs):
        raise AssertionError("society skill learning asked Jarvis' own provider")

    manager = SimpleNamespace(
        _tools={},
        _get_brain=jarvis_brain,
        _get_or_create=jarvis_brain,
        active_provider="openai",
    )
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: manager)
    registry.replies["antigravity"] = SKILL_DRAFT
    cfg = SimpleNamespace(brain=SimpleNamespace(providers={}))
    creator = await default_creator_factory(lambda: cfg)(
        _agent("antigravity", "agy-chosen"), _Skills(tmp_path / "skills")
    )
    # A pinned operation model (a GPT-Live call) must not outrank the seat.
    with use_operation_model(ModelSelection("openai", "pinned", "")):
        result = await creator.draft(SkillCreatorInput(intent="Check source dates"))
    assert result.brain_used and result.brain_source == "seat"
    assert [(b.name, b.kwargs["model"]) for b in registry.built] == [("antigravity", "agy-chosen")]
    assert registry.answered[0].caller == "society-skill"


async def test_a_failing_skill_seat_asks_no_other_provider(registry, monkeypatch, tmp_path):
    from jarvis.agent_chat import runner_brain
    from jarvis.brain import resolver as brain_resolver
    from jarvis.skills.creator_service import SkillCreatorInput
    from jarvis.society.learning import default_creator_factory

    def ladder(*args, **kwargs):
        raise AssertionError("society skill learning climbed the resolver ladder")

    for name in ("resolve_tool_model_brain", "resolve_quality_brain", "resolve_frontier_brain"):
        monkeypatch.setattr(brain_resolver, name, ladder)
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: None)
    registry.failing = frozenset({"gemini"})
    creator = await default_creator_factory(lambda: None)(
        _agent("gemini", "gemini-chosen"), _Skills(tmp_path / "skills")
    )
    result = await creator.draft(SkillCreatorInput(intent="Check source dates"))
    assert not result.brain_used
    assert registry.built_names() == ["gemini"]


async def test_the_learning_pass_awaits_the_seat_factory(tmp_path):
    from jarvis.society.learning import LearningPass, TurnDigest
    from jarvis.society.runtime import SocietyRuntime

    runtime = SocietyRuntime(tmp_path, cfg=lambda: None, seed_starter_team=False)
    await runtime.ensure_started()
    try:
        agent, _ = await runtime.roster.create(name="Scout")
        seen = []

        async def factory(agent, skills):
            seen.append(agent.agent_id)
            return None  # The seat could not be built: nothing is learned.

        learner = LearningPass(runtime, creator_factory=factory)
        digest = TurnDigest(task="t", final_text="f", tool_steps=["a", "b"])
        assert await learner.run(agent, digest) is None
        assert seen == [agent.agent_id]
    finally:
        await runtime.close()
