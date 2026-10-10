"""Clear requests execute; real questions keep the same turn alive until answered."""

from __future__ import annotations

import asyncio
import json

import pytest

from jarvis.agent_chat import service as service_module
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.questions import parse_questions
from jarvis.agent_chat.runner_api import TurnHandle
from jarvis.agent_chat.service import AgentChatService, _Running
from jarvis.agent_chat.store import AgentChatStore
from jarvis.agent_chat.turn_completion import TurnCompletion, offer_instead_of_action
from jarvis.core.protocols import current_chat_turn

QUESTION = {"question": "Which project?", "options": ["Reading tracker", "Budget tracker"]}


@pytest.mark.parametrize(
    "user_request,reply",
    [
        ("Please create a README file.", "I can create that for you. Shall I start?"),
        (
            "Erstelle bitte eine Datei.",
            "Ich schlage vor, zuerst einen Entwurf zu machen.",
        ),  # i18n-allow
        ("Por favor crea el archivo.", "Si quieres, puedo crearlo."),  # i18n-allow
        ("Cria o ficheiro, por favor.", "Se quiseres, posso criá-lo."),  # i18n-allow
        ("Podes criar o relatório?", "Qual projeto devo usar?"),  # i18n-allow
        ("Please create a project overview.", "Which project should I use?"),
    ],
)
def test_offer_and_unstructured_blocking_question_are_not_execution(user_request, reply):
    assert offer_instead_of_action(user_request, reply)


@pytest.mark.parametrize(
    "user_request,reply",
    [
        ("Give me suggestions for the design.", "I suggest a simple layout."),
        ("Please write a poem.", "I can fly beyond the sky."),
        ("Translate this example.", "If you want, I can help."),
        ("Please create a file.", "Created the file. Would you like anything else?"),
        ("Please check the file.", "I can confirm the file contains the requested sections."),
        ("Please create a plan.", "I suggest these steps."),
        ("Did you create the file?", "I can create it if you want."),
        ("Summarize this text: please create a file.", "I can create it if you want."),
        ("Cria um plano com exemplo.", "Posso sugerir estes passos."),  # i18n-allow
        ("Cria o ficheiro.", "Criei o ficheiro. Queres mais alguma coisa?"),  # i18n-allow
    ],
)
def test_advice_plans_and_actual_results_do_not_start_unrequested_work(user_request, reply):
    assert not offer_instead_of_action(user_request, reply)


@pytest.fixture
async def card_world(tmp_path):
    svc = AgentChatService(AgentChatStore(":memory:"))
    session = svc.store.create_session(
        provider="openai", model="fake", effort="", cwd=str(tmp_path)
    )
    run = _Running("t", asyncio.Event())
    run.task = asyncio.create_task(asyncio.sleep(3600))
    svc._running[session.session_id] = run

    async def emit(event):
        await svc._emit(session.session_id, event)

    async def ask(*args):
        return "deny"

    handle = TurnHandle(session, "t", emit, ask, run.cancel)
    gate = TurnCompletion(svc, handle, "Please create my project overview.", allow_correction=True)
    try:
        yield svc, handle, gate
    finally:
        svc.signal_cancel(session.session_id)
        run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
        for task in list(svc._question_tasks):
            task.cancel()
        await asyncio.gather(*svc._question_tasks, return_exceptions=True)
        svc.store.close()


async def waiting(gate, svc, handle):
    qid = await svc.open_questions(handle.session.session_id, parse_questions(QUESTION))
    await gate.emit(
        make_event(
            "tool_call",
            {
                "call_id": "q",
                "name": "mcp__jarvis__society_ask_user",
                "input": {"questions": [QUESTION]},
            },
        )
    )
    await gate.emit(
        make_event(
            "tool_result",
            {
                "call_id": "q",
                "output": json.dumps({"status": "waiting", "question_id": qid}),
                "is_error": False,
            },
        )
    )
    await gate.emit(
        make_event(
            "turn_finished",
            {
                "turn_id": "t",
                "status": "done",
                "usage": {"output_tokens": 7},
                "cost_usd": 0.01,
            },
        )
    )
    return qid


async def test_card_stays_open_after_runner_exit_and_resumes_with_the_answer(card_world):
    svc, handle, gate = card_world
    sid = handle.session.session_id
    qid = await waiting(gate, svc, handle)
    continuation = asyncio.create_task(gate.next_prompt())
    await asyncio.sleep(0)
    assert not continuation.done() and svc.pending_questions(sid) == [qid]
    assert not any(e["kind"] == "turn_finished" for e in svc.store.list_events(sid))
    assert svc.resolve_question(sid, qid, option_index=1)
    prompt = await asyncio.wait_for(continuation, 2)
    assert "Budget tracker" in prompt and '"source": "person"' in prompt
    await gate.emit(
        make_event(
            "turn_finished",
            {
                "turn_id": "t",
                "status": "done",
                "usage": {"output_tokens": 5},
                "cost_usd": 0.02,
            },
        )
    )
    assert await gate.next_prompt() is None
    await gate.publish()
    finish = [e for e in svc.store.list_events(sid) if e["kind"] == "turn_finished"]
    assert len(finish) == 1 and finish[0]["payload"]["usage"]["output_tokens"] == 12
    assert finish[0]["payload"]["cost_usd"] == pytest.approx(0.03)


async def test_answer_arriving_before_runner_exit_is_not_lost(card_world):
    svc, handle, gate = card_world
    qid = await waiting(gate, svc, handle)
    svc.resolve_question(handle.session.session_id, qid, option_index=0)
    await svc._questions[qid].result
    assert svc.pending_questions(handle.session.session_id) == []
    assert "Reading tracker" in await gate.next_prompt()


async def test_answers_already_returned_to_the_model_do_not_trigger_a_second_run(card_world):
    svc, handle, gate = card_world
    qid = await waiting(gate, svc, handle)
    svc.resolve_question(handle.session.session_id, qid, option_index=0)
    await svc.wait_questions(handle.session.session_id, qid)
    assert await gate.next_prompt() is None


async def test_stop_during_question_wait_never_continues(card_world):
    svc, handle, gate = card_world
    await waiting(gate, svc, handle)
    continuation = asyncio.create_task(gate.next_prompt())
    await asyncio.sleep(0)
    svc.signal_cancel(handle.session.session_id)
    with pytest.raises(asyncio.CancelledError):
        await continuation


async def test_plain_offer_is_corrected_once_without_reauthorizing_the_task(card_world):
    _, _, gate = card_world
    gate.context = gate.request + "\nAttached source: input.md"
    offer = make_event("assistant_text", {"text": "I can write that. Shall I start?"})
    finish = make_event("turn_finished", {"status": "done", "usage": {}})
    await gate.emit(offer)
    await gate.emit(finish)
    prompt = await gate.next_prompt()
    assert "society_ask_user" in prompt and "input.md" in prompt
    assert gate.request in prompt
    await gate.emit(offer)
    await gate.emit(finish)
    assert await gate.next_prompt() is None


async def test_denial_prevents_plain_offer_correction(card_world):
    _, _, gate = card_world
    await gate.ask("c", "Write", {}, "Write file")
    await gate.emit(make_event("assistant_text", {"text": "I can write that. Shall I start?"}))
    await gate.emit(make_event("turn_finished", {"status": "done", "usage": {}}))
    assert await gate.next_prompt() is None


async def test_work_already_done_does_not_trigger_optional_offer_execution(card_world):
    _, _, gate = card_world
    await gate.emit(make_event("tool_call", {"call_id": "w", "name": "Write", "input": {}}))
    await gate.emit(make_event("tool_result", {"call_id": "w", "output": "Saved"}))
    await gate.emit(make_event("assistant_text", {"text": "I can also create a second file."}))
    await gate.emit(make_event("turn_finished", {"status": "done", "usage": {}}))
    assert await gate.next_prompt() is None


@pytest.mark.parametrize("runner", ["brain", "claude-cli"])
@pytest.mark.parametrize("cancel_wait", [False, True])
async def test_service_continues_to_artifact_after_question_without_another_user_turn(
    tmp_path,
    monkeypatch,
    runner,
    cancel_wait,
):
    from jarvis.society.runtime import SocietyRuntime

    svc = AgentChatService(AgentChatStore(":memory:"))
    rt = SocietyRuntime(tmp_path, seed_starter_team=False, chat_service=lambda: svc)
    await rt.ensure_started()
    agent, _ = await rt.roster.create(name="Completion tester", provider="openai", model="fake")
    session = svc.store.create_session(
        session_id=agent.session_id,
        provider="openai",
        model="fake",
        effort="",
        surface="society",
        cwd=str(tmp_path),
        permission_mode="bypass",
    )
    monkeypatch.setattr(service_module, "resolve_runner", lambda *args, **kwargs: runner)
    attempts = []
    q = svc.subscribe(session.session_id)

    async def run(handle, prompt, *args, **kwargs):
        attempts.append((handle.turn_id, handle.session.vendor_session))
        assert current_chat_turn.get().user_text == "Please create my project overview."
        if len(attempts) == 1:
            qid = await svc.open_questions(session.session_id, parse_questions(QUESTION))
            await handle.emit(
                make_event(
                    "tool_call",
                    {
                        "call_id": "q",
                        "name": "society_ask_user",
                        "input": {},
                    },
                )
            )
            await handle.emit(
                make_event(
                    "tool_result",
                    {
                        "call_id": "q",
                        "output": {"status": "waiting", "question_id": qid},
                    },
                )
            )
        else:
            assert handle.continuation and "Budget tracker" in prompt
            if runner == "claude-cli":
                assert handle.session.vendor_session == "vendor-thread"
            (tmp_path / "overview.md").write_text("# Budget tracker", encoding="utf-8")
            await handle.emit(
                make_event(
                    "assistant_text", {"turn_id": handle.turn_id, "text": "Saved overview.md."}
                )
            )
        await handle.emit(
            make_event(
                "turn_finished",
                {
                    "turn_id": handle.turn_id,
                    "status": "done",
                    "usage": {"output_tokens": 3},
                    "cost_usd": 0.01,
                },
            )
        )
        return "vendor-thread"

    monkeypatch.setattr(service_module, "run_brain_turn", run)
    monkeypatch.setattr(service_module, "run_cli_turn", run)
    try:
        turn_id = await svc.send(session.session_id, "Please create my project overview.")
        async with asyncio.timeout(5):
            while (event := await q.get())["kind"] != "question_required":
                assert event["kind"] != "turn_finished"
            await asyncio.sleep(0)
            assert svc.is_running(session.session_id)
            if cancel_wait:
                svc.signal_cancel(session.session_id)
            else:
                svc.resolve_question(
                    session.session_id, event["payload"]["question_id"], option_index=1
                )
            while (event := await q.get())["kind"] != "turn_finished":
                pass
        if cancel_wait:
            assert event["payload"]["status"] == "cancelled"
            assert event["payload"]["usage"]["output_tokens"] == 3
            assert event["payload"]["cost_usd"] == pytest.approx(0.01)
            assert len(attempts) == 1 and not (tmp_path / "overview.md").exists()
        else:
            assert event["payload"]["status"] == "done"
            assert event["payload"]["usage"]["output_tokens"] == 6
            assert event["payload"]["cost_usd"] == pytest.approx(0.02)
            assert [a[0] for a in attempts] == [turn_id, turn_id]
            assert (tmp_path / "overview.md").read_text() == "# Budget tracker"
    finally:
        if svc.is_running(session.session_id):
            await svc.cancel(session.session_id)
        await rt.close()
        svc.store.close()


# ------------------------------------------------------------------ credential guard


def stored(names):
    async def fake(_session_id):
        return names

    return fake


async def finish_with(gate, *events):
    for event in events:
        await gate.emit(event)
    await gate.emit(make_event("turn_finished", {"status": "done", "usage": {}}))


async def test_a_secret_asked_for_in_the_reply_is_steered_to_the_secure_field_once(
    card_world, monkeypatch
):
    from jarvis.agent_chat import turn_completion

    monkeypatch.setattr(turn_completion, "_stored_credentials", stored([]))
    _, _, gate = card_world
    ask = make_event(
        "assistant_text", {"text": "Please paste your Discord bot token here so I can connect."}
    )
    await finish_with(gate, ask)
    prompt = await gate.next_prompt()
    assert "society_request_credential" in prompt and "Stored credentials right now: none" in prompt
    assert gate.request in prompt
    await finish_with(gate, ask)
    assert await gate.next_prompt() is None


async def test_a_turn_that_opened_the_field_is_left_alone(card_world, monkeypatch):
    from jarvis.agent_chat import turn_completion

    monkeypatch.setattr(turn_completion, "_stored_credentials", stored([]))
    _, _, gate = card_world
    call = make_event(
        "tool_call",
        {"call_id": "c", "name": "mcp__jarvis__society_request_credential", "input": {}},
    )
    await finish_with(
        gate, call, make_event("assistant_text", {"text": "Please enter the token in the field."})
    )
    assert await gate.next_prompt() is None


async def test_a_claimed_token_that_is_not_stored_is_corrected(card_world, monkeypatch):
    from jarvis.agent_chat import turn_completion

    _, _, gate = card_world
    claim = make_event(
        "assistant_text", {"text": "Der Bot-Token ist bereits sicher gespeichert."}  # i18n-allow
    )
    monkeypatch.setattr(turn_completion, "_stored_credentials", stored(["DISCORD_BOT_TOKEN"]))
    await finish_with(gate, claim)
    assert await gate.next_prompt() is None  # it really is stored
    gate.credential_corrected = False
    monkeypatch.setattr(turn_completion, "_stored_credentials", stored([]))
    assert "society_request_credential" in await gate.next_prompt()


async def test_chats_without_the_credential_tool_are_never_steered(card_world, monkeypatch):
    from jarvis.agent_chat import turn_completion

    monkeypatch.setattr(turn_completion, "_stored_credentials", stored(None))
    _, _, gate = card_world
    await finish_with(gate, make_event("assistant_text", {"text": "I need your API key."}))
    prompt = await gate.next_prompt()
    assert prompt is None or "society_request_credential" not in prompt
    assert not gate.credential_corrected
