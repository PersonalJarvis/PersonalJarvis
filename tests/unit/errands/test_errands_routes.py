"""Pins for the errand REST surface — CLI and UI see what voice sees (§5).

The async tests call the route coroutines directly instead of going through
TestClient: TestClient is a blocking portal, and driving it from inside a
running event loop while the runner detaches background tasks onto the
portal's OTHER loop is a deadlock, not a test. The sync 503 pin keeps one
real HTTP round-trip to prove the router mounts.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from jarvis.errands.runner import ErrandRunner
from jarvis.errands.store import ErrandStore
from jarvis.ui.web.errands_routes import (
    AnswerBody,
    answer_errand,
    cancel_errand,
    get_errand,
    list_errands,
    router,
)

from .test_errand_runner import ScriptedLegs


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: ErrandRunner | None) -> None:
    monkeypatch.setattr("jarvis.ui.web.errands_routes.get_runner", lambda: runner)


def test_unwired_app_answers_503_not_a_fake_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_runner(monkeypatch, None)
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get("/api/errands").status_code == 503


@pytest.mark.asyncio
async def test_list_answer_and_inspect_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = ErrandStore(tmp_path / "e.db")
    legs = ScriptedLegs(
        questions=["Which cabin class?"],
        work=["Booked economy. EVIDENCE: ref BB22"],
        verdicts=[{"done": True, "proof": "ref BB22"}],
    )
    runner = ErrandRunner(store=store, execute_leg=legs)
    opened = await runner.start("book a flight")
    _patch_runner(monkeypatch, runner)

    listed = (await list_errands())["errands"]
    assert [e["id"] for e in listed] == [opened.id]
    assert listed[0]["open_questions"] == ["Which cabin class?"]

    answered = await answer_errand(opened.id, AnswerBody(answers="economy"))
    assert answered["state"] == "running"
    await runner.join()

    detail = await get_errand(opened.id)
    assert detail["state"] == "completed"
    assert any("BB22" in e["detail"] for s in detail["steps"] for e in s["evidence"])

    with pytest.raises(HTTPException) as missing:
        await get_errand("nope")
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_cancel_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = ErrandStore(tmp_path / "e.db")
    runner = ErrandRunner(
        store=store, execute_leg=ScriptedLegs(questions=["Which airport?"])
    )
    opened = await runner.start("book a flight")
    _patch_runner(monkeypatch, runner)

    cancelled = await cancel_errand(opened.id)
    assert cancelled["state"] == "cancelled"
