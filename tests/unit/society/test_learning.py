"""Automatic learning: a finished turn becomes the agent's own active skill."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.society.events import MsgType
from jarvis.society.learning import (
    AgentSkills,
    LearningPass,
    RunLearnedSkillTool,
    TurnDigest,
    should_learn,
)
from jarvis.society.runtime import SocietyRuntime

CTX = SimpleNamespace(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)

SKILL_MD = """---
schema_version: 1
name: {name}
version: 0.1.0
description: {description}
category: learned
state: draft
when_to_use: when the user asks for a thumbnail
---

# {name}

1. Look at the last five thumbnails.
2. Note the shared style.
3. Produce a new one in that style.
"""


class FakeCreator:
    """Writes a real SKILL.md into the agent's registry root like the creator does."""

    def __init__(self, skills: AgentSkills, *, fail: bool = False) -> None:
        self.skills = skills
        self.fail = fail
        self.calls: list[str] = []

    async def author(self, inp):
        self.calls.append(inp.intent)
        if self.fail:
            raise RuntimeError("no brain")
        slug = "thumbnail-style"
        folder = self.skills.root / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(
            SKILL_MD.format(name="Thumbnail style", description="Reproduce the channel look"),
            encoding="utf-8",
        )
        skill = SimpleNamespace(path=folder / "SKILL.md")
        return SimpleNamespace(skill=skill, name="Thumbnail style", slug=slug)


DIGEST = TurnDigest(
    task="Analyze my last five YouTube thumbnails and describe the style.",
    final_text="They share a red accent, big face, three words. Done.",
    tool_steps=["society_browser: youtube.com/@me", "society_wiki_note: style notes"],
)


def test_should_learn_filter():
    assert should_learn(DIGEST)
    assert not should_learn(TurnDigest(task="x", final_text="y", tool_steps=["a"]))
    assert not should_learn(
        TurnDigest(task="x", final_text="y", tool_steps=["a", "b"], status="blocked")
    )
    assert not should_learn(TurnDigest(task="", final_text="y", tool_steps=["a", "b"]))


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = SocietyRuntime(
        tmp_path,
        seed_starter_team=False,
        cfg=lambda: SimpleNamespace(
            memory=SimpleNamespace(data_dir=str(tmp_path)),
            wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")),
        ),
    )
    await runtime.ensure_started()
    await runtime.roster.create(name="Tuber", title="YouTube analyst")
    try:
        yield runtime
    finally:
        await runtime.close()


async def test_learned_skill_is_active_for_the_agent_only(rt: SocietyRuntime, tmp_path: Path):
    notices: list[dict] = []
    creators: list[FakeCreator] = []

    def factory(agent, skills):
        creator = FakeCreator(skills)
        creators.append(creator)
        return creator

    async def notify(agent, payload):
        notices.append(payload)

    learner = LearningPass(rt, creator_factory=factory, notify=notify)
    tuber = await rt.roster.get("tuber")
    slug = await learner.run(tuber, DIGEST)
    assert slug == "thumbnail-style"
    assert "Standing instructions" not in creators[0].calls[0]  # empty description → omitted
    assert "Do not copy page content" in creators[0].calls[0]

    skills = rt.skills_for("tuber")
    summaries = skills.summaries()
    assert [s["slug"] for s in summaries] == ["thumbnail-style"]
    text = (skills.root / "thumbnail-style" / "SKILL.md").read_text(encoding="utf-8")
    assert "state: active" in text
    # Board + chat notice + memory line.
    digest = [e for e in await rt.store.events_since(0) if e.msg_type is MsgType.DIGEST]
    assert [e.payload["kind"] for e in digest if e.payload["kind"] != "memory"][-1] == (
        "learned_skill"
    )
    assert notices == [{"kind": "learned_skill", "slug": slug, "name": "Thumbnail style"}]
    memory = tmp_path / "vault" / "society" / "tuber" / "memory.md"
    assert memory.is_file() and "thumbnail-style" in memory.read_text(encoding="utf-8")
    # The global user skills dir is untouched until promotion.
    from jarvis.skills.bootstrap import ensure_user_skills_dir

    assert not any(p.name.startswith("tuber-") for p in ensure_user_skills_dir().iterdir())


async def test_run_learned_skill_tool_and_briefing(rt: SocietyRuntime, tmp_path: Path):
    learner = LearningPass(rt, creator_factory=lambda a, s: FakeCreator(s))
    tuber = await rt.roster.get("tuber")
    await learner.run(tuber, DIGEST)
    tool = RunLearnedSkillTool(rt, "tuber")
    res = await tool.execute({"skill": "thumbnail-style"}, CTX)
    assert res.success, res.error
    assert "Look at the last five thumbnails" in res.output["instructions"]
    assert res.artifacts and "learned skill" in res.artifacts[0]
    missing = await tool.execute({"skill": "nope"}, CTX)
    assert missing.success is False and "thumbnail-style" in missing.output["known"]

    from jarvis.society.surface import society_system_extra

    session = SimpleNamespace(
        session_id="society:tuber", cwd=str(tmp_path / "ws"), permission_mode="ask"
    )
    briefing = await society_system_extra(rt._get_cfg(), None, session)  # noqa: SLF001
    assert "## Your learned skills" in briefing
    assert "- thumbnail-style: Reproduce the channel look (use when: when the user asks" in briefing


async def test_daily_cap_and_creator_failure(rt: SocietyRuntime):
    tuber = await rt.roster.get("tuber")
    failing = LearningPass(rt, creator_factory=lambda a, s: FakeCreator(s, fail=True))
    assert await failing.run(tuber, DIGEST) is None
    capped = LearningPass(rt, creator_factory=lambda a, s: FakeCreator(s), daily_cap=1)
    assert await capped.run(tuber, DIGEST) == "thumbnail-style"
    assert await capped.run(tuber, DIGEST) is None  # cap reached for today
    none = LearningPass(rt, creator_factory=lambda a, s: None)
    assert await none.run(tuber, DIGEST) is None


async def test_promote_copies_as_draft(rt: SocietyRuntime, tmp_path: Path, monkeypatch):
    import jarvis.skills.bootstrap as bootstrap

    user_dir = tmp_path / "user-skills"
    user_dir.mkdir()
    monkeypatch.setattr(bootstrap, "ensure_user_skills_dir", lambda: user_dir)
    learner = LearningPass(rt, creator_factory=lambda a, s: FakeCreator(s))
    tuber = await rt.roster.get("tuber")
    await learner.run(tuber, DIGEST)
    target = rt.skills_for("tuber").promote_to_global("thumbnail-style")
    assert target == user_dir / "tuber-thumbnail-style"
    assert "state: draft" in (target / "SKILL.md").read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        rt.skills_for("tuber").promote_to_global("thumbnail-style")
    with pytest.raises(KeyError):
        rt.skills_for("tuber").promote_to_global("ghost")


async def test_watch_turn_feeds_the_learner(tmp_path: Path):
    """The end of a chat-run assignment digests the turn and learns off-path."""
    import asyncio

    from jarvis.agent_chat.store import AgentChatStore
    from jarvis.society.events import SocietyEnvelope
    from tests.unit.society.test_chat_binding import FakeTurnService

    svc = FakeTurnService(AgentChatStore(tmp_path / "agent_chat.db"))
    cfg = SimpleNamespace(
        memory=SimpleNamespace(data_dir=str(tmp_path)),
        wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")),
    )
    rt = SocietyRuntime(
        tmp_path, seed_starter_team=False, chat_service=lambda: svc, cfg=lambda: cfg
    )
    await rt.ensure_started()
    seen: list[TurnDigest] = []

    class Spy:
        async def run(self, agent, digest):
            seen.append(digest)
            return None

    rt.learning = Spy()  # type: ignore[assignment]
    try:
        await rt.roster.create(name="Tuber", provider="openai")
        await rt.say(
            from_agent="user", to_agent="tuber", text="Study my thumbnails", msg_type=MsgType.ASSIGN
        )
        for q in list(svc.queues.get("society:tuber", [])):
            q.put_nowait(
                {
                    "kind": "tool_call",
                    "payload": {
                        "turn_id": "turn-1",
                        "name": "society_browser",
                        "summary": "youtube",
                    },
                }
            )
            q.put_nowait({"kind": "tool_call", "payload": {"turn_id": "turn-1", "name": "Read"}})
        await svc.finish("society:tuber", "Red accent, big face.")
        for _ in range(40):
            await asyncio.sleep(0.05)
            if seen:
                break
        assert len(seen) == 1
        assert seen[0].tool_steps == ["society_browser: youtube", "Read"]
        assert seen[0].origin == "web" and seen[0].status == "done"
        assert seen[0].task == "Study my thumbnails"
        assert isinstance((await rt.store.events_since(0))[-1], SocietyEnvelope)
    finally:
        await rt.close()
