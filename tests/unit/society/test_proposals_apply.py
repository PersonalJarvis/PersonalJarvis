"""Confirming a proposal applies it: rules, focus, approval rules, routines, skills.
Rejecting changes nothing. A resolved proposal cannot be applied twice."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.society import proposals
from jarvis.society.events import ApprovalState
from jarvis.society.learning import LearningPass
from jarvis.society.runtime import SocietyRuntime
from tests.fakes.fake_agent_chat import FakeChatService
from tests.unit.society.test_learning import SKILL_MD
from tests.unit.society.test_routines import FakeScheduler, FakeTaskStore


def _tool(name: str, desc: str = "does things.") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, risk_tier="monitor")


TOOLS = {
    "gmail": _tool("gmail", "Read and send mail."),
    "cli_gh": _tool("cli_gh", "GitHub CLI."),
    "search-web": _tool("search-web", "Web search."),
}


class _Store:
    def get_session(self, session_id: str):
        return SimpleNamespace(session_id=session_id)


@pytest.fixture
async def world(tmp_path: Path):
    svc = FakeChatService(_Store())
    runtime = SocietyRuntime(
        tmp_path,
        seed_starter_team=False,
        brain_tools=lambda: TOOLS,
        chat_service=lambda: svc,
        cfg=lambda: SimpleNamespace(
            memory=SimpleNamespace(data_dir=str(tmp_path)),
            wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")),
        ),
    )
    await runtime.ensure_started()
    await runtime.roster.create(
        name="Mailbox",
        title="Gmail agent",
        description="Handle my mail.",
        focus=["plugin:gmail"],
        approval_rules={"require_approval": ["plugin:gmail:send"], "always_allow": []},
    )
    try:
        yield runtime, svc
    finally:
        await runtime.close()


async def _propose(rt, kind: str, payload: dict):
    agent = await rt.roster.get("mailbox")
    return await proposals.propose(
        rt, agent, kind=kind, payload=payload, reason="test", session_id="society:mailbox"
    )


async def test_confirming_a_rule_appends_to_the_description_and_leaves_focus_alone(world):
    rt, svc = world
    item = await _propose(rt, "rule", {"text": "Always answer in English."})
    out = await proposals.resolve(rt, item.id, approve=True)
    assert out["applied"] is True and out["kind"] == "rule"
    agent = await rt.roster.get("mailbox")
    assert agent.description == "Handle my mail.\n\nAlways answer in English."
    assert agent.focus == ["plugin:gmail"]
    assert agent.approval_rules["require_approval"] == ["plugin:gmail:send"]
    assert (await rt.approvals.get(item.id)).state is ApprovalState.APPROVED
    kinds = [n["kind"] for _, n in svc.notices]
    assert kinds == ["proposal", "proposal_resolved"]
    assert svc.notices[-1][1]["status"] == "applied"


async def test_confirming_an_approval_rule_merges_and_dedupes_the_patterns(world):
    rt, _ = world
    item = await _propose(
        rt,
        "approval_rule",
        {"require_approval": ["plugin:gmail:send", "cli:gh:*"], "always_allow": ["plugin:gmail"]},
    )
    out = await proposals.resolve(rt, item.id, approve=True)
    assert out["applied"] is True
    agent = await rt.roster.get("mailbox")
    assert agent.approval_rules == {
        "require_approval": ["cli:gh:*", "plugin:gmail:send"],
        "always_allow": ["plugin:gmail"],
    }
    assert agent.description == "Handle my mail."


async def test_confirming_a_focus_change_keeps_the_order_the_agent_asked_for(world):
    rt, _ = world
    item = await _propose(rt, "focus", {"focus": ["cli:gh", "plugin:gmail", "core:search-web"]})
    await proposals.resolve(rt, item.id, approve=True)
    agent = await rt.roster.get("mailbox")
    assert agent.focus == ["cli:gh", "plugin:gmail", "core:search-web"]


async def test_confirming_a_routine_creates_a_tagged_task(world):
    rt, _ = world
    store = FakeTaskStore()
    scheduler = FakeScheduler(store)
    item = await _propose(
        rt,
        "routine",
        {
            "title": "Inbox sweep",
            "prompt": "Sort the inbox and draft replies.",
            "schedule": {"kind": "every", "interval_seconds": 3600},
        },
    )
    out = await proposals.resolve(rt, item.id, approve=True, task_store=store, scheduler=scheduler)
    assert out["applied"] is True, out
    assert scheduler.scheduled == ["task-1"]
    assert store.rows[0]["title"] == "[agent:Mailbox] Inbox sweep"
    assert "agent:mailbox" in store.rows[0]["spec_json"]
    # Without a task store the outcome is honest, never a silent success.
    again = await _propose(
        rt, "routine", {"title": "B", "prompt": "b", "schedule": {"kind": "every"}}
    )
    out2 = await proposals.resolve(rt, again.id, approve=True)
    assert out2["applied"] is False and "task store" in out2["detail"]


async def test_confirming_a_skill_authors_it_in_the_background(world):
    rt, svc = world

    class FakeCreator:
        def __init__(self, skills) -> None:
            self.skills = skills
            self.inputs = []

        async def author(self, inp):
            self.inputs.append(inp)
            slug = "weekly-digest"
            folder = self.skills.root / slug
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(
                SKILL_MD.format(name="Weekly digest", description="Digest the week"),
                encoding="utf-8",
            )
            return SimpleNamespace(
                skill=SimpleNamespace(path=folder / "SKILL.md"), name="Weekly digest", slug=slug
            )

    creators: list[FakeCreator] = []

    def factory(agent, skills):
        creator = FakeCreator(skills)
        creators.append(creator)
        return creator

    rt.learning = LearningPass(rt, creator_factory=factory)
    item = await _propose(
        rt,
        "skill",
        {"name": "Weekly digest", "goal": "Summarise the week", "steps": [], "outcome": "ok"},
    )
    out = await proposals.resolve(rt, item.id, approve=True)
    assert out["applied"] is True and out["detail"] == "authoring the skill"
    await asyncio.gather(*list(rt._watchers))  # noqa: SLF001 — drain the background task
    assert creators and creators[0].inputs[0].name_hint == "Weekly digest"
    assert [s["slug"] for s in rt.skills_for("mailbox").summaries()] == ["weekly-digest"]
    last = svc.notices[-1][1]
    assert last["kind"] == "proposal_resolved" and last["status"] == "applied"
    assert "weekly-digest" in last["text"]


async def test_rejecting_a_proposal_changes_nothing_and_posts_a_notice(world):
    rt, svc = world
    item = await _propose(rt, "rule", {"text": "Be terse."})
    out = await proposals.resolve(rt, item.id, approve=False)
    assert out["applied"] is False
    agent = await rt.roster.get("mailbox")
    assert agent.description == "Handle my mail."
    assert (await rt.approvals.get(item.id)).state is ApprovalState.DENIED
    assert svc.notices[-1][1]["status"] == "rejected"


async def test_a_resolved_proposal_cannot_be_applied_twice(world):
    rt, _ = world
    item = await _propose(rt, "rule", {"text": "Be terse."})
    await proposals.resolve(rt, item.id, approve=True)
    with pytest.raises(ValueError, match="already resolved"):
        await proposals.resolve(rt, item.id, approve=True)
    with pytest.raises(KeyError):
        await proposals.resolve(rt, "nope", approve=True)
    agent = await rt.roster.get("mailbox")
    assert agent.description.count("Be terse.") == 1


async def test_a_plain_approval_is_not_a_proposal(world):
    rt, _ = world
    plain = await rt.approvals.enqueue(
        agent_id="mailbox", trace_id="t", capability="core:shell", action={}, summary="rm -rf"
    )
    with pytest.raises(ValueError, match="not a configuration proposal"):
        await proposals.resolve(rt, plain.id, approve=True)
