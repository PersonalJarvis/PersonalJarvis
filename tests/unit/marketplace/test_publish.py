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
        "skill_md": (
            "---\nname: three-point-check\ndescription: Three bullets, done.\n---\n\nDo the thing."
        ),
    }
    draft.update(overrides)
    return draft


def _plugin_draft(**overrides: Any) -> dict[str, Any]:
    draft: dict[str, Any] = {
        "kind": "plugin",
        "name": "todo-fox",
        "version": "1.2.0",
        "plugin_json": {"name": "todo-fox", "description": "Tasks from TodoFox"},
        "mcp_json": {
            "mcpServers": {
                "todo-fox": {"type": "streamable-http", "url": "https://mcp.todofox.example/mcp"}
            }
        },
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
                "todo-fox": {"type": "stdio", "command": "uvx", "args": ["todo-fox-mcp@1.2.0"]}
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
        {"mcpServers": {"a": {"type": "streamable-http", "url": "http://plain.example"}}},
        {"mcpServers": {"a": {"type": "stdio", "command": "bash", "args": ["x@1.0.0"]}}},
        # Ends in no version marker at all — the authority only forbids a
        # literal "@latest" suffix, so this one is caught by the local
        # supplementary pin check on top of the delegated rules, not by
        # agent_plugins_loader.validate_mcp_server itself.
        {"mcpServers": {"a": {"type": "stdio", "command": "npx", "args": ["unpinned-thing"]}}},
        {"mcpServers": {"a": {}, "b": {}}},
    ],
)
def test_mcp_rejections(mcp: dict[str, Any]) -> None:
    value, errors = publish.validate_draft(_plugin_draft(mcp_json=mcp))
    assert value is None
    assert any(e["field"] == "mcp_json" for e in errors)


# --- mcp.json now delegates to agent_plugins_loader.validate_mcp_server ----
#
# validator-parity.md rows 12, 13 and 15, plus the "type" field itself: the
# rules below were NOT enforced by the old hand-rolled _validate_mcp at all,
# so these are net-new rejections, not just refactored ones.


def test_mcp_missing_type_is_rejected() -> None:
    draft = _plugin_draft(
        mcp_json={"mcpServers": {"todo-fox": {"url": "https://mcp.todofox.example/mcp"}}}
    )
    value, errors = publish.validate_draft(draft)
    assert value is None
    assert any(e["field"] == "mcp_json" and "type" in e["error"] for e in errors)


def test_mcp_sse_transport_is_rejected() -> None:
    draft = _plugin_draft(
        mcp_json={
            "mcpServers": {"todo-fox": {"type": "sse", "url": "https://mcp.todofox.example/sse"}}
        }
    )
    value, errors = publish.validate_draft(draft)
    assert value is None
    assert any(e["field"] == "mcp_json" and "sse" in e["error"] for e in errors)


def test_mcp_headers_on_hosted_server_is_rejected() -> None:
    draft = _plugin_draft(
        mcp_json={
            "mcpServers": {
                "todo-fox": {
                    "type": "streamable-http",
                    "url": "https://mcp.todofox.example/mcp",
                    "headers": {"Authorization": "Bearer x"},
                }
            }
        }
    )
    value, errors = publish.validate_draft(draft)
    assert value is None
    assert any(e["field"] == "mcp_json" for e in errors)


def test_mcp_stdio_env_literal_is_rejected() -> None:
    draft = _plugin_draft(
        mcp_json={
            "mcpServers": {
                "todo-fox": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@todofox/mcp-server@1.2.0"],
                    "env": {"TODOFOX_TOKEN": "a-literal-value-not-a-placeholder-at-all"},
                }
            }
        }
    )
    value, errors = publish.validate_draft(draft)
    assert value is None
    assert any(e["field"] == "mcp_json" for e in errors)


def test_mcp_stdio_env_placeholder_is_accepted() -> None:
    draft = _plugin_draft(
        mcp_json={
            "mcpServers": {
                "todo-fox": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@todofox/mcp-server@1.2.0"],
                    "env": {"TODOFOX_TOKEN": "$plugin_todo-fox_access_token"},
                }
            }
        }
    )
    value, errors = publish.validate_draft(draft)
    assert errors == [] and value is not None


def test_mcp_and_install_authority_agree_on_acceptance() -> None:
    """W3 "done when" (publishing-plan.md): the same mcp.json fixture is
    judged identically by the app's pre-check and by the install-time
    authority (agent_plugins_loader.validate_mcp_server) it delegates to."""
    from jarvis.marketplace import agent_plugins_loader

    server = {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@todofox/mcp-server@1.2.0"],
        "env": {"TODOFOX_TOKEN": "$plugin_todo-fox_access_token"},
    }
    mcp_json = {"mcpServers": {"todo-fox": server}}
    value, errors = publish.validate_draft(_plugin_draft(mcp_json=mcp_json))
    assert errors == [] and value is not None

    assert agent_plugins_loader.validate_mcp_server("todo-fox", server) == {
        "transport": "stdio",
        "install": ["npx", "-y", "@todofox/mcp-server@1.2.0"],
        "env_template": {"TODOFOX_TOKEN": "$plugin_todo-fox_access_token"},
    }


def test_mcp_and_install_authority_agree_on_rejection() -> None:
    from jarvis.marketplace import agent_plugins_loader

    server = {"type": "sse", "url": "https://mcp.todofox.example/sse"}
    mcp_json = {"mcpServers": {"todo-fox": server}}
    value, errors = publish.validate_draft(_plugin_draft(mcp_json=mcp_json))
    assert value is None
    assert any(e["field"] == "mcp_json" and "sse" in e["error"] for e in errors)

    with pytest.raises(agent_plugins_loader.AgentPluginError, match="sse"):
        agent_plugins_loader.validate_mcp_server("todo-fox", server)


def test_secret_pattern_rejected() -> None:
    draft = _skill_draft(skill_md="---\nname: x\n---\ntoken: ghp_" + "a" * 24)
    value, errors = publish.validate_draft(draft)
    assert value is None
    assert any("credential" in e["error"] for e in errors)


# --- skill_md is judged by the same rule the install path uses -------------
#
# community_install.install_community_skill calls
# agent_plugins_loader.validate_bundled_skills for every standalone skill
# install; validate_draft must reject exactly what that call would reject,
# not a hand-rolled subset of it.


def test_skill_frontmatter_missing_description_is_rejected() -> None:
    draft = _skill_draft(skill_md="---\nname: three-point-check\n---\n\nDo the thing.")
    value, errors = publish.validate_draft(draft)
    assert value is None
    assert any(e["field"] == "skill_md" for e in errors)


def test_skill_frontmatter_risk_policy_is_rejected() -> None:
    draft = _skill_draft(
        skill_md=(
            "---\nname: three-point-check\ndescription: d\nrisk_policy: safe\n---\n\nDo the thing."
        )
    )
    value, errors = publish.validate_draft(draft)
    assert value is None
    assert any(e["field"] == "skill_md" and "risk_policy" in e["error"] for e in errors)


def test_invalid_skill_name_does_not_also_report_skill_md() -> None:
    # A bad top-level name must not be reused as `plugin_name` for the
    # skill_md check — that would attribute a confusing second error to the
    # wrong field for a skill_md that was otherwise fine.
    draft = _skill_draft(name="UPPER")
    value, errors = publish.validate_draft(draft)
    assert value is None
    assert any(e["field"] == "name" for e in errors)
    assert not any(e["field"] == "skill_md" for e in errors)


# --- bundled skills on a plugin submission ----------------------------------


def _bundled_skill(name: str = "todo-fox-tips", **overrides: Any) -> dict[str, Any]:
    skill: dict[str, Any] = {
        "name": name,
        "skill_md": f"---\nname: {name}\ndescription: Tips for todo-fox.\n---\n\nDo the thing.",
    }
    skill.update(overrides)
    return skill


def test_valid_bundled_skill_normalizes() -> None:
    value, errors = publish.validate_draft(_plugin_draft(skills=[_bundled_skill()]))
    assert errors == []
    assert value is not None
    assert value["skills"] == [_bundled_skill()]


def test_bundled_skill_missing_description_is_rejected() -> None:
    bad = _bundled_skill(skill_md="---\nname: todo-fox-tips\n---\n\nDo the thing.")
    value, errors = publish.validate_draft(_plugin_draft(skills=[bad]))
    assert value is None
    assert any(e["field"] == "skills" for e in errors)


def test_bundled_skill_risk_policy_is_rejected() -> None:
    bad = _bundled_skill(
        skill_md=(
            "---\nname: todo-fox-tips\ndescription: d\nrisk_policy: safe\n---\n\nDo the thing."
        )
    )
    value, errors = publish.validate_draft(_plugin_draft(skills=[bad]))
    assert value is None
    assert any(e["field"] == "skills" and "risk_policy" in e["error"] for e in errors)


def test_bundled_skill_duplicate_name_is_rejected() -> None:
    skill = _bundled_skill()
    value, errors = publish.validate_draft(_plugin_draft(skills=[skill, skill]))
    assert value is None
    assert any(e["field"] == "skills" for e in errors)


def test_bundled_skill_sharing_plugin_name_alone_is_allowed() -> None:
    skill = _bundled_skill(
        name="todo-fox",
        skill_md="---\nname: todo-fox\ndescription: d\n---\n\nDo the thing.",
    )
    value, errors = publish.validate_draft(_plugin_draft(skills=[skill]))
    assert errors == []
    assert value is not None


def test_bundled_skill_sharing_plugin_name_with_others_is_rejected() -> None:
    same_name = _bundled_skill(
        name="todo-fox",
        skill_md="---\nname: todo-fox\ndescription: d\n---\n\nDo the thing.",
    )
    value, errors = publish.validate_draft(
        _plugin_draft(skills=[same_name, _bundled_skill(name="todo-fox-extra")])
    )
    assert value is None
    assert any(e["field"] == "skills" for e in errors)


def test_too_many_bundled_skills_is_rejected() -> None:
    skills = [_bundled_skill(name=f"todo-fox-{i}") for i in range(11)]
    value, errors = publish.validate_draft(_plugin_draft(skills=skills))
    assert value is None
    assert any(e["field"] == "skills" for e in errors)


# --- mcp.json: the two divergences closed in publish.py --------------------


def test_mcp_rejects_latest_even_when_another_arg_looks_pinned() -> None:
    draft = _plugin_draft(
        mcp_json={
            "mcpServers": {"todo-fox": {"command": "npx", "args": ["foo@1.2.0", "bar@latest"]}}
        }
    )
    value, errors = publish.validate_draft(draft)
    assert value is None
    assert any(e["field"] == "mcp_json" for e in errors)


def test_mcp_rejects_the_servers_alias() -> None:
    draft = _plugin_draft(
        mcp_json={"servers": {"todo-fox": {"url": "https://mcp.todofox.example/mcp"}}}
    )
    value, errors = publish.validate_draft(draft)
    assert value is None
    assert any(e["field"] == "mcp_json" for e in errors)


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
