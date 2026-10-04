"""Focused contracts for external routine webhook connection metadata."""
from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from uuid import uuid4

from fastapi import Response

from jarvis.tasks.schema import SpeakAction, TaskSpec, TriggerWebhook
from jarvis.tasks.webhook_auth import verify_signature
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
    return SimpleNamespace(app=SimpleNamespace(state=state)), row


async def test_github_connection_returns_hmac_signing_secret(monkeypatch) -> None:
    task_id = uuid4()
    spec = TaskSpec(
        id=task_id,
        title="PR merged",
        trigger=TriggerWebhook(provider="github"),
        action=SpeakAction(text="Merged"),
    )
    request, row = _request(spec)
    secrets: dict[str, str] = {}
    monkeypatch.setattr("jarvis.tasks.webhook_auth.get_secret", secrets.get)
    monkeypatch.setattr(
        "jarvis.tasks.webhook_auth.set_secret",
        lambda slot, value: not secrets.__setitem__(slot, value),
    )

    response = Response()
    body = await get_webhook_connection(task_id, request, response)

    assert body["token"]
    assert body["path"] == f"/api/tasks/hooks/{task_id}"
    assert response.headers["Cache-Control"] == "no-store"

    raw = b'{"action":"closed","pull_request":{"merged":true}}'
    digest = hmac.new(body["token"].encode(), raw, hashlib.sha256).hexdigest()
    assert verify_signature(row, raw, "sha256=" + digest)


async def test_github_connection_rotation_revokes_old_signing_secret(monkeypatch) -> None:
    task_id = uuid4()
    spec = TaskSpec(
        id=task_id,
        title="PR merged",
        trigger=TriggerWebhook(provider="github"),
        action=SpeakAction(text="Merged"),
    )
    request, row = _request(spec)
    secrets: dict[str, str] = {}
    monkeypatch.setattr("jarvis.tasks.webhook_auth.get_secret", secrets.get)
    monkeypatch.setattr(
        "jarvis.tasks.webhook_auth.set_secret",
        lambda slot, value: not secrets.__setitem__(slot, value),
    )

    old = await get_webhook_connection(task_id, request, Response())
    new = await rotate_webhook_connection(task_id, request, Response())

    assert old["token"] != new["token"]
    raw = b'{"action":"closed","pull_request":{"merged":true}}'
    old_digest = hmac.new(old["token"].encode(), raw, hashlib.sha256).hexdigest()
    new_digest = hmac.new(new["token"].encode(), raw, hashlib.sha256).hexdigest()
    assert not verify_signature(row, raw, "sha256=" + old_digest)
    assert verify_signature(row, raw, "sha256=" + new_digest)
