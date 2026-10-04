"""Focused contracts for external routine webhook connection metadata."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from jarvis.tasks.schema import SpeakAction, TaskSpec, TriggerWebhook
from jarvis.ui.web.routine_hooks_routes import (
    get_webhook_connection,
    rotate_webhook_connection,
)


class _Store:
    def __init__(self, spec: TaskSpec, row: dict[str, object]) -> None:
        self.spec = spec
        self.row = row

    async def get(self, task_id: str):
        return self.row if task_id == str(self.spec.id) else None

    async def get_spec(self, task_id: str):
        return self.spec if task_id == str(self.spec.id) else None


def _request(spec: TaskSpec):
    row = {
        "id": str(spec.id),
        "trigger_type": "webhook",
        "created_at_ns": 123,
    }
    state = SimpleNamespace(task_store=_Store(spec, row), task_scheduler=object())
    return SimpleNamespace(app=SimpleNamespace(state=state))


async def test_github_connection_reports_provider_secret_not_bearer_token(monkeypatch) -> None:
    task_id = uuid4()
    spec = TaskSpec(
        id=task_id,
        title="PR merged",
        trigger=TriggerWebhook(provider="github"),
        action=SpeakAction(text="Merged"),
    )
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda _slot: "configured-secret")

    response = Response()
    body = await get_webhook_connection(task_id, _request(spec), response)

    assert body["provider"] == "github"
    assert body["configured"] is True
    assert body["token"] == ""
    assert body["path"] == f"/api/tasks/hooks/{task_id}"
    assert response.headers["Cache-Control"] == "no-store"


async def test_github_connection_does_not_offer_generic_token_rotation() -> None:
    task_id = uuid4()
    spec = TaskSpec(
        id=task_id,
        title="PR merged",
        trigger=TriggerWebhook(provider="github"),
        action=SpeakAction(text="Merged"),
    )

    with pytest.raises(HTTPException) as raised:
        await rotate_webhook_connection(task_id, _request(spec), Response())

    assert raised.value.status_code == 409
    assert "provider" in str(raised.value.detail).lower()
