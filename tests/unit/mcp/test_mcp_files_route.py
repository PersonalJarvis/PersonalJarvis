"""The MCP detail page's file card: what defines one server, as readable files.

``mcp.json`` is the user's own block for that server, ``definition.json`` the
spec the registry runs. A built-in server that was never written to mcp.json
still has a definition to read, and no secret ever appears in either file.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from jarvis.mcp import state as mcp_state
from jarvis.mcp.registry import MCPServerSpec
from jarvis.ui.web.mcp_routes import router


@pytest.fixture()
def mcp_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "mcp.json"
    monkeypatch.setenv("JARVIS_MCP_CONFIG", str(path))
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "notes-mcp": {
                        "command": "uvx",
                        "args": ["notes-mcp"],
                        "env": {"NOTES_TOKEN": "literal-secret-value", "NOTES_REF": "$NOTES"},
                        "enabled": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _client() -> httpx.AsyncClient:
    app = FastAPI()  # no registry on app.state — the route must still answer
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_user_server_block_is_returned_with_env_literals_masked(mcp_json: Path) -> None:
    async with _client() as client:
        resp = await client.get("/api/mcps/notes-mcp/files")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [f["path"] for f in body["files"]] == ["mcp.json"]
    block = json.loads(body["files"][0]["text"])["mcpServers"]["notes-mcp"]
    assert block["command"] == "uvx"
    assert block["env"]["NOTES_REF"] == "$NOTES"
    assert "literal-secret-value" not in resp.text
    assert body["config_path"].endswith("mcp.json")


class _FakeRegistry:
    """Only what the route asks of a registry: the spec by name."""

    def __init__(self, spec: MCPServerSpec) -> None:
        self._spec = spec

    def get_spec(self, name: str) -> MCPServerSpec | None:
        return self._spec if name == self._spec.name else None


@pytest.mark.asyncio
async def test_registry_server_has_a_definition_even_without_an_mcp_json_entry(
    mcp_json: Path,
) -> None:
    spec = MCPServerSpec(
        name="files-mcp",
        display="Files",
        description="Read files.",
        install_command=["uvx", "files-mcp"],
    )
    app = FastAPI()
    app.state.mcp_registry = _FakeRegistry(spec)
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcps/files-mcp/files")
    assert resp.status_code == 200, resp.text
    files = {f["path"]: f for f in resp.json()["files"]}
    assert list(files) == ["definition.json"]  # nothing of it in mcp.json yet
    definition = json.loads(files["definition.json"]["text"])
    assert definition["name"] == "files-mcp"
    assert definition["install_command"] == ["uvx", "files-mcp"]


@pytest.mark.asyncio
async def test_unknown_server_is_404(mcp_json: Path) -> None:
    assert mcp_state.get_server_entry("nope") is None
    async with _client() as client:
        resp = await client.get("/api/mcps/nope/files")
    assert resp.status_code == 404
