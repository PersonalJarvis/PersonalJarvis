"""Pins for the errand REST surface — CLI and UI see what voice sees (§5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.errands.runner import ErrandRunner
from jarvis.errands.store import ErrandStore
from jarvis.ui.web.errands_routes import router

from .test_errand_runner import ScriptedLegs


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: ErrandRunner | None) -> None:
    monkeypatch.setattr("jarvis.ui.web.errands_routes.get_runner", lambda: runner)


def test_unwired_app_answers_503_not_a_fake_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_runner(monkeypatch, None)
    assert client.get("/api/errands").status_code == 503


@pytest.mark.asyncio
async def test_list_answer_and_inspect_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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

    listed = client.get("/api/errands").json()["errands"]
    assert [e["id"] for e in listed] == [opened.id]
    assert listed[0]["open_questions"] == ["Which cabin class?"]

    answered = client.post(f"/api/errands/{opened.id}/answer", json={"answers": "economy"})
    assert answered.status_code == 200
    await runner.join()

    detail = client.get(f"/api/errands/{opened.id}").json()
    assert detail["state"] == "completed"
    assert any("BB22" in e["detail"] for s in detail["steps"] for e in s["evidence"])

    assert client.get("/api/errands/nope").status_code == 404


@pytest.mark.asyncio
async def test_cancel_round_trip(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = ErrandStore(tmp_path / "e.db")
    runner = ErrandRunner(
        store=store, execute_leg=ScriptedLegs(questions=["Which airport?"])
    )
    opened = await runner.start("book a flight")
    _patch_runner(monkeypatch, runner)

    cancelled = client.post(f"/api/errands/{opened.id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
