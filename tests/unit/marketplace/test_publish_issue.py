"""The issue intake path of in-app publishing (no server, no network in tests)."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from jarvis.marketplace import publish
from jarvis.marketplace.token_store import Tokens


class FakeStore:
    def __init__(self, tokens: Tokens | None) -> None:
        self.tokens = tokens

    def load(self, plugin_id: str) -> Tokens | None:
        return self.tokens


def _agent_submission(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "kind": "agent",
        "name": "inbox-butler",
        "version": "1.0.0",
        "title": "Inbox Butler",
        "description": "Sorts the inbox and drafts the answers.",
        "categories": ["email"],
        "agent": {
            "schema": 1,
            "name": "Inbox Butler",
            "title": "Keeps the inbox at zero",
            "instructions": "Sort new mail. Never send without approval.",
            "tier": "specialist",
            "focus": ["plugin:gmail"],
        },
    }
    doc.update(overrides)
    return doc


def test_agent_draft_validates() -> None:
    value, errors = publish.validate_draft(_agent_submission())
    assert errors == []
    assert value is not None and value["agent"]["name"] == "Inbox Butler"


def test_agent_draft_refuses_a_permission_widening() -> None:
    doc = _agent_submission()
    doc["agent"] = {**doc["agent"], "approval_mode": "bypass"}
    value, errors = publish.validate_draft(doc)
    assert value is None
    assert any(e["field"] == "agent" for e in errors)


def test_agent_draft_needs_a_summary() -> None:
    value, errors = publish.validate_draft(_agent_submission(description=""))
    assert value is None
    assert any(e["field"] == "description" for e in errors)


def test_issue_body_is_what_the_intake_reads() -> None:
    value, _ = publish.validate_draft(_agent_submission())
    assert value is not None
    body = publish.issue_body(value)
    assert publish.INTAKE_BODY_MARKER in body
    # The registry intake's own fence pattern (scripts/intake.py).
    fence = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.DOTALL)
    match = fence.search(body)
    assert match is not None and json.loads(match.group(1)) == value
    assert publish.issue_title(value).startswith(publish.INTAKE_TITLE_TAG)


@pytest.mark.asyncio
async def test_submit_opens_the_issue_as_the_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "publish_endpoint", lambda: "")
    monkeypatch.setattr(publish, "registry_repo", lambda: "PersonalJarvis/marketplace")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["json"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={"html_url": "https://github.com/PersonalJarvis/marketplace/issues/9",
                  "number": 9},
        )

    value, _ = publish.validate_draft(_agent_submission())
    assert value is not None
    result = await publish.submit(
        value,
        store=FakeStore(Tokens(access="tok")),  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
    )
    assert seen["url"] == "https://api.github.com/repos/PersonalJarvis/marketplace/issues"
    assert seen["auth"] == "Bearer tok"
    assert seen["json"]["title"] == "[publish] agent: inbox-butler 1.0.0"
    assert "publisher" not in seen["json"]["body"]
    assert result == {
        "issue_url": "https://github.com/PersonalJarvis/marketplace/issues/9",
        "issue_number": 9,
        "submission_path": "submissions/inbox-butler.json",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 410])
async def test_a_refused_issue_points_to_the_browser(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setattr(publish, "publish_endpoint", lambda: "")
    monkeypatch.setattr(publish, "registry_repo", lambda: "PersonalJarvis/marketplace")
    value, _ = publish.validate_draft(_agent_submission())
    assert value is not None
    refusal = {"message": "Resource not accessible by integration"}
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=refusal))
    with pytest.raises(publish.SubmitError) as exc:
        await publish.submit(value, store=FakeStore(Tokens(access="tok")), transport=transport)  # type: ignore[arg-type]
    assert exc.value.field == "browser_fallback"
    assert "issues/new?template=publish.yml" in exc.value.error
    # GitHub's own body never reaches the person (AP-34).
    assert "integration" not in exc.value.error


@pytest.mark.asyncio
async def test_an_expired_sign_in_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "publish_endpoint", lambda: "")
    monkeypatch.setattr(publish, "registry_repo", lambda: "PersonalJarvis/marketplace")
    value, _ = publish.validate_draft(_agent_submission())
    assert value is not None
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={}))
    with pytest.raises(publish.SubmitError) as exc:
        await publish.submit(value, store=FakeStore(Tokens(access="tok")), transport=transport)  # type: ignore[arg-type]
    assert exc.value.status == 401


def test_registry_repo_must_look_like_owner_slash_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import jarvis.core.config as config

    for raw, expected in (
        ("PersonalJarvis/marketplace", "PersonalJarvis/marketplace"),
        ("https://evil.example/x", ""),
        ("", ""),
    ):
        monkeypatch.setattr(
            config,
            "load_config",
            lambda raw=raw: SimpleNamespace(
                marketplace=SimpleNamespace(publish_registry_repo=raw)
            ),
        )
        assert publish.registry_repo() == expected
