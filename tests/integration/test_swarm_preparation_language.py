"""The host resolves one preparation-turn language and reuses it on retries."""

import json
from types import SimpleNamespace

import pytest

from jarvis.core.swarm_preparation import PreparationAnswers
from jarvis.core.turn_language import resolve_output_language
from jarvis.swarm import preparation
from tests.integration.test_swarm_preparation import answers, prep_runtime, spec


@pytest.mark.asyncio
@pytest.mark.parametrize("pin", ["de", "en", "es"])
async def test_reply_pin_is_resolved_once_persisted_and_forwarded_to_both_prompts(
    tmp_path, monkeypatch, pin
):
    service = prep_runtime(tmp_path)
    service.brain_factory.cfg = SimpleNamespace(
        brain=SimpleNamespace(reply_language=pin), ui=SimpleNamespace(language="en")
    )
    resolutions = []

    def resolve(*args, **kwargs):
        resolutions.append((args, kwargs))
        return resolve_output_language(*args, **kwargs)

    monkeypatch.setattr(preparation, "resolve_output_language", resolve)
    try:
        original = spec().model_copy(
            update={"goal": "Please calculate the exact result for my report."}
        )
        questions = await service.create_preparation(original)
        assert len(resolutions) == 1
        # Config changes cannot rewrite a replayed operation's language or input.
        service.brain_factory.cfg.brain.reply_language = "en" if pin != "en" else "de"
        replay = await service.create_preparation(original)
        assert replay["questions"] == questions["questions"]
        assert len(resolutions) == 1
        service.brain_factory.cfg.brain.reply_language = pin
        ready = await service.answer_preparation(questions["team"]["id"], answers(questions))
        assert ready["state"] == "ready" and len(resolutions) == 2
        await service.answer_preparation(questions["team"]["id"], answers(questions))
        assert len(resolutions) == 2
        assert len(service.brain_factory.requests) == 2
        for request in service.brain_factory.requests:
            assert json.loads(request.messages[0].content)["output_language"] == pin
            assert f"Resolved output language: {pin}." in request.system
            assert "Use the owner's language" not in request.system
        operations = [
            item
            for item in await service.records(ready["team"]["id"], "decisions")
            if item["kind"] == "preparation_operation"
        ]
        assert len(operations) == 2
        assert {item["output_language"] for item in operations} == {pin}
        assert ready["team"]["limits"] == original.limits.model_dump(mode="json")
        assert ready["team"]["policy"] == original.policy.model_dump(mode="json")
        assert service.brain_factory.cfg.brain.reply_language == pin
    finally:
        await service.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("goal", "locale"),
    [
        ("Bitte berechne das Ergebnis und schreibe einen Bericht.", "de"),  # i18n-allow
        ("Please calculate the result and write a report.", "en"),
        ("Por favor, calcula el resultado y escribe un informe.", "es"),
    ],
)
async def test_short_answers_keep_the_preparation_language_in_auto_mode(tmp_path, goal, locale):
    service = prep_runtime(tmp_path)
    service.brain_factory.cfg = SimpleNamespace(
        brain=SimpleNamespace(reply_language="auto"), ui=SimpleNamespace(language="en")
    )
    try:
        questions = await service.create_preparation(spec().model_copy(update={"goal": goal}))
        body = PreparationAnswers(
            expected_revision=questions["revision"],
            expected_storage_generation="",
            request_key="short-answer",
            answers={"deliverable": "OK"},
        )
        ready = await service.answer_preparation(questions["team"]["id"], body)
        assert ready["state"] == "ready"
        assert [
            json.loads(request.messages[0].content)["output_language"]
            for request in service.brain_factory.requests
        ] == [locale, locale]
        assert service.brain_factory.cfg.brain.reply_language == "auto"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_clarification_requests_real_missing_inputs_without_technical_choices(tmp_path):
    service = prep_runtime(tmp_path)
    goal = "Calculate a few summary statistics for a list of numbers and save the result."
    try:
        view = await service.create_preparation(spec().model_copy(update={"goal": goal}))
        request = service.brain_factory.requests[0]
        payload = json.loads(request.messages[0].content)
        assert payload["original_goal"] == goal
        assert payload["answers"] == {}
        assert not request.tools and view["team"]["state"] == "created"
        assert await service.records(view["team"]["id"], "tasks") == []
        for rule in (
            "person with no technical background",
            "paste or provide that exact data",
            "Never invent missing inputs",
            "factual input requests free-form",
            "Never suggest reading a local file",
            "Do not ask the owner to choose an implementation",
        ):
            assert rule in request.system
        assert "one to three" in request.system
        assert "Resolved output language: en." in request.system
    finally:
        await service.stop()
