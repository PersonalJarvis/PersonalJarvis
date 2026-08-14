"""The in-app publish module: rule mirror, identity, submit — no network."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from jarvis.marketplace import publish
from jarvis.marketplace.token_store import Tokens


def _skill_draft(**overrides: Any) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "kind": "skill",
        "name": "three-point-check",
        "version": "1.0.0",
        "title": "Three Point Check",
        "description": "Summarize any topic in three bullets",
        "categories": ["writing"],
        "skill_md": "---\nname: three-point-check\n---\n\nDo the thing.",
    }
    draft.update(overrides)
    return draft


def _plugin_draft(**overrides: Any) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "kind": "plugin",
        "name": "todo-fox",
        "version": "1.2.0",
        "plugin_json": {"name": "todo-fox", "description": "Tasks from TodoFox"},
        "mcp_json": {"mcpServers": {"todo-fox": {"url": "https://mcp.todofox.example/mcp"}}},
        "usage_card": None,
    }
    draft.update(overrides)
    return draft


class FakeStore:
    def __init__(self, tokens: Tokens | None = None) -> None:
        self._tokens = tokens
        self.deleted = False

    def load(self, key: str) -> Tokens | None:
        return self._tokens

    def save(self, key: str, tokens: Tokens) -> None:
        self._tokens = tokens

    def delete(self, key: str) -> None:
        self._tokens = None
        self.deleted = True


# --- validate_draft --------------------------------------------------------


def test_valid_skill_normalizes() -> None:
    value, errors = publish.validate_draft(_skill_draft())
    assert errors == []
    assert value is not None
    assert value["kind"] == "skill" and value["title"] == "Three Point Check"


def test_valid_plugin_with_pinned_stdio() -> None:
    draft = _plugin_draft(
        mcp_json={
            "mcpServers": {
                "todo-fox": {"command": "uvx", "args": ["todo-fox-mcp@1.2.0"]}
            }
        }
    )
    value, errors = publish.validate_draft(draft)
    assert errors == [] and value is not None


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"kind": "wat"}, "kind"),
        ({"name": "UPPER"}, "name"),
        ({"name": "double--dash"}, "name"),
        ({"version": "1.0"}, "version"),
        ({"title": ""}, "title"),
        ({"description": ""}, "description"),
        ({"description": "x" * 501}, "description"),
        ({"skill_md": "no frontmatter"}, "skill_md"),
        ({"skill_md": "---\n" + "x" * (publish.MAX_SKILL_BYTES + 1)}, "skill_md"),
    ],
)
def test_skill_rejections_carry_the_field(overrides: dict[str, Any], field: str) -> None:
    value, errors = publish.validate_draft(_skill_draft(**overrides))
    assert value is None
    assert any(e["field"] == field for e in errors)


@pytest.mark.parametrize(
    "mcp",
    [
        {"mcpServers": {}},
        {"mcpServers": {"a": {"url": "http://plain.example"}}},
        {"mcpServers": {"a": {"command": "bash", "args": ["x@1.0.0"]}}},
        {"mcpServers": {"a": {"command": "npx", "args": ["unpinned-thing"]}}},
        {"mcpServers": {"a": {}, "b": {}}},
    ],
)
def test_mcp_rejections(mcp: dict[str, Any]) -> None:
    value, errors = publish.validate_draft(_plugin_draft(mcp_json=mcp))
    assert value is None
    assert any(e["field"] == "mcp_json" for e in errors)


def test_secret_pattern_rejected() -> None:
    draft = _skill_draft(skill_md="---\nname: x\n---\ntoken: ghp_" + "a" * 24)
    value, errors = publish.validate_draft(draft)
    assert value is None
    assert any("credential" in e["error"] for e in errors)


# --- submit ---------------------------------------------------------------


def _transport(status: int, body: dict[str, Any]) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body))


@pytest.mark.asyncio
async def test_submit_posts_bearer_and_maps_the_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "publish_endpoint", lambda: "https://pj.example/api/submit")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            201,
            json={"prUrl": "https://github.com/pr/1", "submissionPath": "submissions/x.json"},
        )

    value, _ = publish.validate_draft(_skill_draft())
    assert value is not None
    result = await publish.submit(
        value,
        store=FakeStore(Tokens(access="tok")),  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
    )
    assert result == {"pr_url": "https://github.com/pr/1", "submission_path": "submissions/x.json"}
    assert seen["auth"] == "Bearer tok"


@pytest.mark.asyncio
async def test_submit_without_token_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "publish_endpoint", lambda: "https://pj.example/api/submit")
    value, _ = publish.validate_draft(_skill_draft())
    assert value is not None
    with pytest.raises(publish.SubmitError) as exc:
        await publish.submit(value, store=FakeStore(None))  # type: ignore[arg-type]
    assert exc.value.status == 401


@pytest.mark.asyncio
async def test_submit_passes_endpoint_refusal_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "publish_endpoint", lambda: "https://pj.example/api/submit")
    value, _ = publish.validate_draft(_skill_draft())
    assert value is not None
    with pytest.raises(publish.SubmitError) as exc:
        await publish.submit(
            value,
            store=FakeStore(Tokens(access="tok")),  # type: ignore[arg-type]
            transport=_transport(409, {"error": "name is owned by octocat", "field": "name"}),
        )
    assert exc.value.status == 409
    assert exc.value.field == "name"


@pytest.mark.asyncio
async def test_submit_disabled_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(publish, "publish_endpoint", lambda: "")
    value, _ = publish.validate_draft(_skill_draft())
    assert value is not None
    with pytest.raises(publish.SubmitError) as exc:
        await publish.submit(value, store=FakeStore(Tokens(access="tok")))  # type: ignore[arg-type]
    assert exc.value.status == 503


# --- live status ----------------------------------------------------------


@pytest.mark.asyncio
async def test_live_status_reports_the_feed_truthfully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.marketplace import community_source

    index = community_source.CommunityIndex.model_validate(
        {"skills": [{"name": "three-point-check", "version": "1.0.0"}]}
    )

    async def fake_get_index(force: bool = False) -> tuple[Any, str]:
        return index, "fresh"

    monkeypatch.setattr(community_source, "get_index", fake_get_index)
    assert (await publish.live_status("three-point-check", "1.0.0"))["live"] is True
    assert (await publish.live_status("three-point-check", "2.0.0"))["live"] is False
    assert (await publish.live_status("absent-name", "1.0.0"))["live"] is False


# --- identity -------------------------------------------------------------


@pytest.mark.asyncio
async def test_identity_signed_in() -> None:
    state = await publish.current_identity(
        store=FakeStore(Tokens(access="tok")),  # type: ignore[arg-type]
        transport=_transport(200, {"login": "octocat", "avatar_url": "https://a"}),
    )
    assert state == {"signed_in": True, "login": "octocat", "avatar_url": "https://a"}


@pytest.mark.asyncio
async def test_identity_dead_token_signs_out(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoRefreshHandler:
        async def refresh(self, tokens: Tokens) -> Tokens:
            raise RuntimeError("revoked")

    monkeypatch.setattr(publish, "make_device_handler", lambda: NoRefreshHandler())
    store = FakeStore(Tokens(access="dead"))
    state = await publish.current_identity(
        store=store,  # type: ignore[arg-type]
        transport=_transport(401, {}),
    )
    assert state == {"signed_in": False}
    assert store.deleted is True
