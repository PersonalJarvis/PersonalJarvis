"""An MCP token must pass the CSRF guard — on the MCP path, and nowhere else.

This is a regression test for a bug that every other test missed, because
every other test mounts the ASGI app directly and never sees the guard.

What happened live: the guard treats cookie- and open-access requests as
browser-shaped and demands an Origin header on POST, exempting Bearer
credentials it recognises. It recognised the control key and session tokens —
not MCP tokens. So a paired client, which sends a Bearer and never an Origin,
was rejected 403 "Untrusted Origin header" on every call. All unit tests were
green; the feature did not work at all.

The fix had to stay narrow. The desktop UI routes (`/api/settings/*`) are NOT
key-gated — they rely on this guard for authentication — so a token accepted
app-wide would have opened them. The second test below is the one that keeps
it narrow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.mcp_server_routes import build_mcp_asgi_app
from jarvis.ui.web.surface_security import SurfaceSecurity

_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


@pytest.fixture
def guarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The MCP surface behind the real guard, plus a token store of our own."""
    from jarvis.mcp.agents import tokens

    store = tokens.TokenStore(tmp_path / "mcp_tokens.json")
    monkeypatch.setattr(tokens, "_store", store)

    app = FastAPI()
    app.mount("/api/control/mcp", build_mcp_asgi_app())

    @app.post("/api/settings/danger")
    def _settings() -> dict[str, str]:
        """Stands in for a same-origin UI route: no key gate of its own."""
        return {"changed": "yes"}

    # A control key this process will not accept, so only the token is in play.
    guard = SurfaceSecurity(app, control_key_validator=lambda token: False)
    return TestClient(guard), store


def test_a_token_passes_the_guard_without_an_origin(guarded) -> None:
    """A CLI client sends a Bearer and no Origin — that must be enough."""
    client, store = guarded
    _token, secret = store.issue(name="Claude Desktop", scope="work")

    answer = client.post(
        "/api/control/mcp/agents",
        headers={**_MCP_HEADERS, "Authorization": f"Bearer {secret}"},
        json=_INIT,
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["result"]["serverInfo"]["name"] == "jarvis-agents"


def test_a_token_opens_nothing_but_the_mcp_path(guarded) -> None:
    """The narrow part of the fix: UI routes rely on this guard, not on a key."""
    client, store = guarded
    _token, secret = store.issue(name="Nosy client", scope="full")

    answer = client.post(
        "/api/settings/danger",
        headers={"Authorization": f"Bearer {secret}"},
        json={},
    )

    assert answer.status_code in (401, 403), (
        "an MCP token must not authenticate anything outside the agent surface"
    )
    assert answer.json().get("changed") != "yes"


def test_a_revoked_token_stops_passing_the_guard(guarded) -> None:
    client, store = guarded
    token, secret = store.issue(name="Retired laptop", scope="work")
    store.revoke(token.token_id)

    answer = client.post(
        "/api/control/mcp/agents",
        headers={**_MCP_HEADERS, "Authorization": f"Bearer {secret}"},
        json=_INIT,
    )

    assert answer.status_code in (401, 403)


def test_a_made_up_token_does_not_pass(guarded) -> None:
    client, _store = guarded

    answer = client.post(
        "/api/control/mcp/agents",
        headers={**_MCP_HEADERS, "Authorization": "Bearer jarvis_mcp_deadbeef_nonsense"},
        json=_INIT,
    )

    assert answer.status_code in (401, 403)


def test_a_foreign_origin_is_still_refused_with_a_valid_token(guarded) -> None:
    """The token exempts a request from NEEDING an Origin, not from having a bad one."""
    client, store = guarded
    _token, secret = store.issue(name="Attacked client", scope="work")

    answer = client.post(
        "/api/control/mcp/agents",
        headers={
            **_MCP_HEADERS,
            "Authorization": f"Bearer {secret}",
            "Origin": "http://evil.example",
        },
        json=_INIT,
    )

    assert answer.status_code == 403
