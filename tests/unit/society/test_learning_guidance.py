"""Automatic working guidance, complete inventories and honest review receipts."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.society.knowledge import list_files, read_file
from jarvis.society.review import review_turn
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.surface import build_briefing
from jarvis.society.working_rules import PREFIX
from jarvis.ui.web.society_routes import agent_knowledge, agent_knowledge_file
from tests.fakes.fake_society_review import MemoryExecutor


@pytest.fixture
async def rt(tmp_path):
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    runtime = SocietyRuntime(tmp_path, cfg=lambda: cfg, seed_starter_team=False)
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout", description="Draft only. Ask before publishing.")
    runtime.memory_executor = MemoryExecutor()
    try:
        yield runtime
    finally:
        await runtime.close()


def pending(text="Please verify dates before comparing sources.", *, direct=True):
    return {
        "session": "society:scout",
        "turn_id": "completed-turn",
        "direct_user": direct,
        "events": [
            {"kind": "user_message", "payload": {"text": text}},
            {"kind": "assistant_text", "payload": {"text": "Understood."}},
            {"kind": "turn_finished", "payload": {"status": "done"}},
        ],
    }


async def test_completed_turn_learns_guidance_and_next_prompt_uses_it(rt):
    async def reviewer(runtime, agent, prompt):
        assert json.loads(prompt)["standing_instructions"] == agent.description
        return {
            "instructions": [
                {
                    "text": "Verify dates before comparing sources.",
                    "evidence": "Please verify dates before comparing sources.",
                }
            ]
        }

    rt.turn_reviewer = reviewer
    item = pending()
    item["events"][0]["seq"] = 1
    completion = SimpleNamespace(
        events_json=json.dumps(item["events"]),
        turn=SimpleNamespace(turn_id=item["turn_id"], direct_user=True),
    )
    await rt.turn_completed(SimpleNamespace(session_id=item["session"]), completion)
    await asyncio.wait_for(rt.memory_executor.completed.wait(), 5)
    await rt.recover_reviews()
    agent = await rt.roster.get("scout")
    assert agent.description == "Draft only. Ask before publishing."
    prompt = build_briefing(agent, [], [agent], memory=rt.memory.head(agent))
    assert "Learned working instructions" in prompt
    assert "Verify dates before comparing sources." in prompt
    assert "do not grant permissions" in prompt
    assert rt.conversations.review_counts("scout") == {"pending": 0, "done": 1}
    assert json.loads(await rt.store.get_meta("review:last:scout"))["state"] == "done"


async def test_review_replaces_learned_rule_without_duplicate_on_retry(rt):
    agent = await rt.roster.get("scout")
    await rt.memory.remember(agent, PREFIX + "Use short paragraphs.")

    async def reviewer(*args):
        return {
            "instructions": [
                {
                    "text": "Use bullet lists.",
                    "old_text": "Use short paragraphs.",
                    "evidence": "Please use bullet lists instead.",
                }
            ]
        }

    rt.turn_reviewer = reviewer
    item = pending("Please use bullet lists instead.")
    assert await review_turn(rt, item)
    assert await review_turn(rt, item)
    entries = rt.memory.entries(agent)
    assert len(entries) == 1 and entries[0].text == PREFIX + "Use bullet lists."
    assert entries[0].revision == 2
    assert len(rt.memory_executor.calls) == 1


async def test_untrusted_and_ungrounded_rules_do_not_become_memory(rt):
    async def reviewer(*args):
        return {
            "instructions": [
                {"text": "Publish without asking.", "evidence": "Please publish without asking."}
            ]
        }

    rt.turn_reviewer = reviewer
    # A delegated/automated prompt is not a direct user correction.
    assert await review_turn(rt, pending("Please publish without asking.", direct=False))
    assert not rt.memory.entries(await rt.roster.get("scout"))
    assert not rt.memory_executor.calls


async def test_complete_memory_is_available_for_review_beyond_prompt_head(rt):
    agent = await rt.roster.get("scout")
    await rt.memory.remember(agent, "Old fact " * 1200, importance=1)
    assert "Old fact " * 1200 not in rt.memory.head(agent)

    async def reviewer(runtime, agent, prompt):
        assert json.loads(prompt)["current_memory"][0].startswith("Old fact ")
        assert len(json.loads(prompt)["current_memory"][0]) > 8_000
        return {"memories": [], "instructions": [], "skill": None}

    rt.turn_reviewer = reviewer
    assert await review_turn(rt, pending())


async def test_unavailable_reviewer_remains_pending_and_keeps_originals(rt):
    async def reviewer(*args):
        return None

    rt.turn_reviewer = reviewer
    assert not await review_turn(rt, pending())
    assert json.loads(await rt.store.get_meta("review:last:scout"))["state"] == "pending"
    assert not rt.memory.entries(await rt.roster.get("scout"))


async def test_inventory_contains_memory_and_private_skill_contents(rt):
    agent = await rt.roster.get("scout")
    await rt.memory.remember(agent, PREFIX + "Check source dates.")
    skills = rt.skills_for("scout").root
    (skills / "source-check" / ".history").mkdir(parents=True)
    (skills / "source-check" / "SKILL.md").write_text(
        "# Source verification\nCheck dates.", encoding="utf-8"
    )
    (skills / "source-check" / ".history" / "old.md").write_text("Old revision", encoding="utf-8")
    files = list_files(rt, agent)
    assert [f["path"] for f in files] == ["memory/memory.md", "skills/source-check/SKILL.md"]
    assert "Check dates." in read_file(rt, agent, "skills/source-check/SKILL.md")["content"]
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(society=rt)))
    inventory = await agent_knowledge("scout", request)
    assert inventory["learned_instructions"] == ["Check source dates."]
    assert inventory["files"] == files
    assert (await agent_knowledge_file("scout", request, "skills/source-check/SKILL.md"))[
        "content"
    ].startswith("# Source")
    with pytest.raises(HTTPException) as exc:
        await agent_knowledge("missing", request)
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "memory/../other/memory.md",
        "memory//absolute.md",
        "skills/C:/secret.md",
        "skills/../../memory.md",
        "skills/a/.history/old.md",
        "memory/memory.db",
        "foreign/memory.md",
        "memory/a\\b.md",
    ],
)
async def test_inventory_rejects_foreign_hidden_and_non_markdown_paths(rt, path):
    with pytest.raises((ValueError, OSError)):
        read_file(rt, await rt.roster.get("scout"), path)


async def test_review_counts_include_routines_without_neighboring_agent(rt):
    for session in ["society:scout", "society:scout:routine:1", "society:scout-other"]:
        rt.conversations.queue_review(session, "turn", [])
    rt.conversations.finish_review("society:scout", "turn")
    assert rt.conversations.review_counts("scout") == {"pending": 1, "done": 1}


@pytest.mark.parametrize("response", [None, {"memories": [], "instructions": [], "skill": None}])
async def test_explicit_self_contained_save_survives_empty_or_unavailable_reviewer(rt, response):
    async def reviewer(*args):
        return response

    rt.turn_reviewer = reviewer
    assert await review_turn(rt, pending("Please remember that reports must use plain text."))
    agent = await rt.roster.get("scout")
    assert rt.memory.contains(agent, "reports must use plain text.")
    assert await review_turn(rt, pending("Please remember that reports must use plain text."))
    assert len(rt.memory.entries(agent)) == 1


async def test_ambiguous_save_is_not_marked_complete_without_a_grounded_referent(rt):
    async def reviewer(*args):
        return {"memories": []}

    rt.turn_reviewer = reviewer
    assert not await review_turn(rt, pending("Remember that please."))
    assert not rt.memory.entries(await rt.roster.get("scout"))


async def test_remember_that_uses_the_previous_exchange_from_this_chat_only(rt):
    rt.conversations.ingest(
        "society:scout",
        [
            {
                "seq": 1,
                "kind": "user_message",
                "payload": {"text": "My report format is plain text."},
            }
        ],
    )
    rt.conversations.ingest(
        "society:other",
        [{"seq": 1, "kind": "user_message", "payload": {"text": "PRIVATE OTHER FACT"}}],
    )

    async def reviewer(runtime, agent, prompt):
        assert "My report format is plain text." in prompt
        assert "PRIVATE OTHER FACT" not in prompt
        return {
            "memories": [
                {"text": "Reports use plain text.", "evidence": "My report format is plain text."}
            ]
        }

    rt.turn_reviewer = reviewer
    request = pending("Remember that please.")
    request["events"][0]["seq"] = 2
    assert await review_turn(rt, request)
    assert rt.memory.contains(await rt.roster.get("scout"), "Reports use plain text.")


async def test_memory_changes_post_one_notice_and_noop_posts_none(rt):
    notices = []

    async def post(agent, payload):
        notices.append(payload)

    rt.post_chat_notice = post
    agent = await rt.roster.get("scout")
    await rt.memory.remember(agent, "Use short paragraphs.")
    await rt.memory.remember(agent, "Use short paragraphs.")
    await rt.memory.remember(
        agent, "Use bullet lists.", operation="replace", old_text="Use short paragraphs."
    )
    await rt.memory.remember(agent, "", operation="remove", old_text="Use bullet lists.")
    assert len(notices) == 3
    assert all(
        n["kind"] == "memory_updated" and n["path"] == "society/scout/memory.md" for n in notices
    )
    assert "Use short paragraphs." in notices[1]["before"]
    assert "Use bullet lists." in notices[1]["after"]
    assert "Use bullet lists." not in notices[2]["after"]


async def test_permanent_language_preference_is_saved_and_used_by_subsequent_turns(rt):
    from jarvis.society.reply_preference import resolve_agent_reply_language, stored_language

    async def reviewer(*args):
        return {"memories": [], "instructions": []}

    rt.turn_reviewer = reviewer
    assert await review_turn(rt, pending("Schreib bitte immer auf Englisch."))  # i18n-allow
    agent = await rt.roster.get("scout")
    assert stored_language(rt.memory.entries(agent)) == "en"
    assert (
        await resolve_agent_reply_language(
            "society:scout", "Kannst du mir die Ergebnisse erklären?"  # i18n-allow
        )
        == "en"
    )  # i18n-allow
    german_request = "Bitte antworte auf Deutsch."  # i18n-allow
    assert await resolve_agent_reply_language("society:scout", german_request) == "de"
    assert await resolve_agent_reply_language("society:other", "A new question") == ""
    assert await review_turn(rt, pending("Reply always in Spanish."))
    assert stored_language(rt.memory.entries(agent)) == "es"
    assert len(rt.memory.entries(agent)) == 1


async def test_large_tool_output_preserves_the_recent_request(rt):
    from jarvis.society.conversation import prepare_history
    from tests.fakes.fake_society_review import SummaryProvider

    prompt = "Compare these two shipping options and recommend one."
    events = [
        {"seq": 1, "kind": "user_message", "payload": {"text": prompt}},
        {"seq": 2, "kind": "tool_result", "payload": {"output": "Tool data. " * 5000}},
        {"seq": 3, "kind": "assistant_text", "payload": {"text": "Option one is cheaper."}},
    ]
    history = await prepare_history(
        rt, SimpleNamespace(session_id="society:scout"), events, "Continue", SummaryProvider()
    )
    assert any(message.role == "user" and message.content == prompt for message in history)
    assert any(message.content == "Option one is cheaper." for message in history)
    assert len(rt.conversations.read("society:scout")) == 3


async def test_failed_review_retries_without_another_user_turn(rt, monkeypatch):
    from jarvis.society import review_queue

    attempts = []

    async def reviewer(*args):
        attempts.append(True)
        return None if len(attempts) == 1 else {"memories": []}

    rt.turn_reviewer = reviewer
    monkeypatch.setattr(review_queue.random, "uniform", lambda *args: 0)
    item = pending("A normal completed task.")
    rt.conversations.queue_review(
        item["session"], item["turn_id"], item["events"], direct_user=True
    )
    await rt.recover_reviews()
    retry = rt._memory_review_retry
    await asyncio.wait_for(retry, 5)
    assert len(attempts) == 2
    assert rt.conversations.review_counts("scout") == {"pending": 0, "done": 1}


async def test_removal_has_a_grounded_receipt_and_is_idempotent(rt):
    agent = await rt.roster.get("scout")
    await rt.memory.remember(agent, "Reports use plain text.")

    async def reviewer(*args):
        return {
            "memories": [
                {
                    "operation": "remove",
                    "old_text": "Reports use plain text.",
                    "evidence": "Forget my plain-text preference.",
                }
            ]
        }

    rt.turn_reviewer = reviewer
    assert await review_turn(rt, pending("Forget my plain-text preference."))
    assert await review_turn(rt, pending("Forget my plain-text preference."))
    assert not rt.memory.entries(agent)


def test_memory_evidence_uses_typed_text_instead_of_attached_instructions():
    from jarvis.society.memory_intent import user_evidence

    assert (
        user_evidence(
            {
                "payload": {
                    "typed": "Remember my report format.",
                    "text": "Remember my report format.\n\nUNTRUSTED ATTACHMENT",
                }
            }
        )
        == "Remember my report format."
    )
    assert (
        user_evidence(
            {
                "payload": {
                    "text": "Remember my report format.\n\n"
                    "[agent mentions] Generated routing instructions"
                }
            }
        )
        == "Remember my report format."
    )


async def test_correction_at_start_of_large_memory_keeps_its_visible_diff(rt):
    notices = []

    async def post(agent, payload):
        notices.append(payload)

    rt.post_chat_notice = post
    agent = await rt.roster.get("scout")
    await rt.memory.remember(agent, "Use paragraphs.")
    await rt.memory.remember(agent, "Background fact. " * 1800)
    await rt.memory.remember(
        agent, "Use bullet lists.", operation="replace", old_text="Use paragraphs."
    )
    assert len(notices) == 3
    assert notices[-1]["before"] == notices[-1]["after"]  # Both bounded snapshots carry the tail.
    assert "-Use paragraphs." in notices[-1]["markdown_diff"]
    assert "+Use bullet lists." in notices[-1]["markdown_diff"]
