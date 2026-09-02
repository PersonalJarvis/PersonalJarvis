"""The Agent MCP surface's mount and its connect-flow routes.

The mount is the silent failure this file exists to catch. A ``Mount`` on
``/api/control/mcp/agents`` builds the path regex
``^/api/control/mcp/agents(?P<path>/.*)$`` — it requires something after the
mount path, so a plain ``POST /api/control/mcp/agents`` never matches it and
falls through to the shorter ``/api/control/mcp``. The client would get the
tools catalog on the agents URL: same transport, same auth, wrong tools, and
nothing anywhere to notice it by. One mount dispatching on the rest of the path
is what makes that impossible, so both URLs are exercised for real here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.agent_mcp_routes import router
from jarvis.ui.web.mcp_server_routes import build_mcp_asgi_app

#: A stateless Streamable HTTP request needs both content types offered.
_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _mounted_app() -> FastAPI:
    """The surfaces mounted exactly as ``server.py`` mounts them."""
    app = FastAPI()
    app.mount("/api/control/mcp", build_mcp_asgi_app())
    return app


@pytest.fixture
def keyed(monkeypatch: pytest.MonkeyPatch) -> str:
    """A control key this process will accept, without touching the real one."""
    from jarvis.core import control_key as ck

    key = "not-a-real-key-only-this-test-accepts-it"
    monkeypatch.setattr(ck, "verify_control_key", lambda presented: presented == key)
    return key


def _tool_names(client: TestClient, url: str, key: str) -> set[str]:
    """The tool catalog one surface serves, over a real MCP handshake."""
    headers = {**_MCP_HEADERS, "Authorization": f"Bearer {key}"}
    init = client.post(
        url,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
    )
    assert init.status_code == 200, init.text
    listed = client.post(
        url,
        headers=headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert listed.status_code == 200, listed.text
    body: Any = listed.json()
    return {t["name"] for t in body["result"]["tools"]}


def test_the_agents_url_serves_the_agent_catalog(keyed: str) -> None:
    """The exact URL a client is configured with, with no trailing slash."""
    with TestClient(_mounted_app()) as client:
        names = _tool_names(client, "/api/control/mcp/agents", keyed)

    assert {"agent_chat", "agent_assign", "ecosystem_status", "kill_switch"} <= names
    assert "open-app" not in names, "the agents URL must not serve the tools surface"


def test_the_tools_url_still_serves_the_tools_surface(keyed: str) -> None:
    """The pre-existing surface keeps its URL and its (possibly empty) catalog."""
    with TestClient(_mounted_app()) as client:
        names = _tool_names(client, "/api/control/mcp", keyed)

    # No supervisor gateway is wired in a unit test, so the catalog is empty —
    # what matters is that it is NOT the agent catalog.
    assert "agent_chat" not in names


def test_an_unknown_surface_is_a_404_not_a_wrong_catalog(keyed: str) -> None:
    with TestClient(_mounted_app()) as client:
        answer = client.post(
            "/api/control/mcp/nonsense",
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {keyed}"},
            json={},
        )
    assert answer.status_code == 404
    assert "/api/control/mcp/agents" in answer.text


def test_both_surfaces_demand_the_control_key() -> None:
    """Loopback does NOT bypass auth — anything on the box could open a socket."""
    with TestClient(_mounted_app()) as client:
        for url in ("/api/control/mcp", "/api/control/mcp/agents"):
            answer = client.post(url, headers=_MCP_HEADERS, json={})
            assert answer.status_code == 401, url
            assert "control api key" in answer.text.lower()


@pytest.fixture
def connect_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def test_status_publishes_the_tool_catalog(connect_app: FastAPI) -> None:
    with TestClient(connect_app) as client:
        body = client.get("/api/agent-mcp/status").json()

    assert body["server_name"] == "jarvis-agents"
    assert body["url"].endswith("/api/control/mcp/agents")
    assert body["tool_count"] == len(body["tools"])
    names = {t["name"] for t in body["tools"]}
    assert {"agent_chat", "agent_assign", "ecosystem_status"} <= names
    # A client must be able to see which tools spend money BEFORE calling one.
    assert next(t for t in body["tools"] if t["name"] == "agent_chat")["dangerous"] is True
    assert next(t for t in body["tools"] if t["name"] == "agents_list")["dangerous"] is False


def test_clients_route_reports_installability(connect_app: FastAPI) -> None:
    with TestClient(connect_app) as client:
        body = client.get("/api/agent-mcp/clients").json()

    rows = {c["key"]: c for c in body["clients"]}
    assert {"claude-desktop", "cursor", "claude-code", "codex"} <= set(rows)
    assert rows["codex"]["writable"] is False, "a hand-formatted TOML is never written"
    assert rows["cursor"]["writable"] is True


def test_snippet_route_returns_pasteable_config(connect_app: FastAPI) -> None:
    with TestClient(connect_app) as client:
        body = client.get("/api/agent-mcp/snippet", params={"client": "claude-desktop"}).json()
        parsed = json.loads(body["snippet"])
        assert list(parsed["mcpServers"]) == ["jarvis-agents"]

        toml = client.get("/api/agent-mcp/snippet", params={"client": "codex"}).json()
        assert toml["snippet"].startswith("[mcp_servers.jarvis-agents]")

        bad = client.get("/api/agent-mcp/snippet", params={"client": "emacs"})
        assert bad.status_code == 404


def test_connect_writes_into_a_clients_config(
    connect_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.mcp.agents import export

    monkeypatch.setattr(export, "_home", lambda: tmp_path)
    (tmp_path / ".cursor").mkdir()

    with TestClient(connect_app) as client:
        body = client.post("/api/agent-mcp/connect", json={"client": "cursor"}).json()

    assert body["ok"] is True
    assert body["restart_required"] is True
    written = json.loads((tmp_path / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "jarvis-agents" in written["mcpServers"]


# ------------------------------------------------------- per-client tokens


@pytest.fixture
def token_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A token store of our own — never the real one on this machine."""
    from jarvis.mcp.agents import tokens

    store = tokens.TokenStore(tmp_path / "mcp_tokens.json")
    monkeypatch.setattr(tokens, "_store", store)
    return store


def test_a_scoped_token_opens_the_agent_surface(token_store, keyed: str) -> None:
    """A paired client authenticates as ITSELF, not with the master key."""
    _token, secret = token_store.issue(name="Cursor - laptop", scope="work")

    with TestClient(_mounted_app()) as client:
        names = _tool_names(client, "/api/control/mcp/agents", secret)

    assert "agent_chat" in names
    assert "quest_post" in names


def test_a_read_token_is_never_offered_a_spending_tool(token_store, keyed: str) -> None:
    """Scope is enforced by NOT LISTING. A tool a model can see, it will call."""
    _token, secret = token_store.issue(name="Dashboard", scope="read")

    with TestClient(_mounted_app()) as client:
        names = _tool_names(client, "/api/control/mcp/agents", secret)

    assert "agents_list" in names and "ecosystem_status" in names
    for spender in ("agent_chat", "agent_assign", "quest_post", "kill_switch"):
        assert spender not in names, f"a read token must not even see {spender}"


def test_a_work_token_may_spend_but_not_govern(token_store, keyed: str) -> None:
    _token, secret = token_store.issue(name="Phone", scope="work")

    with TestClient(_mounted_app()) as client:
        names = _tool_names(client, "/api/control/mcp/agents", secret)

    assert {"agent_chat", "quest_post", "agent_assign"} <= names
    assert "kill_switch" not in names, "stopping the house is the owner's call"
    assert "approval_resolve" not in names


def test_the_control_key_still_sees_everything(token_store, keyed: str) -> None:
    with TestClient(_mounted_app()) as client:
        names = _tool_names(client, "/api/control/mcp/agents", keyed)

    assert {"kill_switch", "approval_resolve", "agent_chat"} <= names


def test_a_revoked_token_is_dead_at_once(token_store, keyed: str) -> None:
    token, secret = token_store.issue(name="Stolen laptop", scope="full")
    token_store.revoke(token.token_id)

    with TestClient(_mounted_app()) as client:
        answer = client.post(
            "/api/control/mcp/agents",
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {secret}"},
            json={},
        )

    assert answer.status_code == 401


def test_a_token_does_not_open_the_tools_surface(token_store, keyed: str) -> None:
    """A scoped token is for the ecosystem; Jarvis' own hands need the key."""
    _token, secret = token_store.issue(name="Anything", scope="full")

    with TestClient(_mounted_app()) as client:
        answer = client.post(
            "/api/control/mcp/",
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {secret}"},
            json={},
        )

    assert answer.status_code == 401


# --------------------------------------------------------- the pairing flow


def test_pair_returns_a_finished_config_with_its_own_credential(
    connect_app: FastAPI, token_store
) -> None:
    with TestClient(connect_app) as client:
        body = client.post(
            "/api/agent-mcp/pair",
            json={"name": "Claude Desktop - MacBook", "scope": "work", "client": "claude-desktop"},
        ).json()

    assert body["secret_shown_once"] is True
    secret = body["secret"]
    assert secret.startswith("jarvis_mcp_")

    # The config is finished: paste it, restart the client, done.
    entry = body["config"]["mcpServers"]["jarvis-agents"]
    assert entry["args"] == ["-m", "jarvis.mcp.agents.bridge"]
    assert entry["env"]["JARVIS_MCP_TOKEN"] == secret
    assert "JARVIS_CONTROL_KEY" not in entry["env"], "pairing must not hand out the master key"
    assert json.loads(body["config_text"])["mcpServers"]["jarvis-agents"] == entry

    # And it really works as a credential.
    assert token_store.verify(secret) is not None


def test_pair_for_another_machine_uses_that_address(connect_app: FastAPI, token_store) -> None:
    with TestClient(connect_app) as client:
        body = client.post(
            "/api/agent-mcp/pair",
            json={
                "name": "Work laptop",
                "transport": "http",
                "base_url": "http://192.168.1.50:47821",
            },
        ).json()

    entry = body["config"]["mcpServers"]["jarvis-agents"]
    assert entry["url"] == "http://192.168.1.50:47821/api/control/mcp/agents"
    assert entry["headers"]["Authorization"] == f"Bearer {body['secret']}"


def test_paired_clients_are_listed_and_revocable(connect_app: FastAPI, token_store) -> None:
    with TestClient(connect_app) as client:
        first = client.post("/api/agent-mcp/pair", json={"name": "One"}).json()
        client.post("/api/agent-mcp/pair", json={"name": "Two"}).json()

        listed = client.get("/api/agent-mcp/tokens").json()
        assert {t["name"] for t in listed["tokens"]} == {"One", "Two"}
        assert all("secret" not in t for t in listed["tokens"])
        assert listed["scopes"] == ["read", "work", "full"]

        token_id = first["token"]["token_id"]
        gone = client.request("DELETE", f"/api/agent-mcp/tokens/{token_id}").json()
        assert gone["revoked"] is True

        left = client.get("/api/agent-mcp/tokens").json()
        assert {t["name"] for t in left["tokens"]} == {"Two"}


def test_pair_refuses_a_nameless_client(connect_app: FastAPI, token_store) -> None:
    with TestClient(connect_app) as client:
        answer = client.post("/api/agent-mcp/pair", json={"name": "   "})
    assert answer.status_code == 422
