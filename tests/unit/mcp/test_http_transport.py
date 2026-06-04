"""Unit tests for the remote streamable-HTTP MCP transport (Wave 0).

Covers the additions that unblock OAuth/remote MCP connectors:
- ``MCPServerSpec`` gains ``url`` + ``headers`` fields.
- ``MCPRegistry.load_from_mcp_json`` understands ``transport="http"`` entries
  (which carry a ``url`` instead of a ``command``).
- ``MCPClient`` resolves ``$SECRET`` placeholders inside header values via
  ``get_secret`` so a Bearer token never lives in ``mcp.json`` in clear text.
- ``MCPClient.start()`` wires the official ``streamablehttp_client`` (a
  3-tuple transport, unlike the 2-tuple stdio/sse transports).
"""
from __future__ import annotations

import pytest

from jarvis.mcp.client import MCPClient
from jarvis.mcp.registry import MCPRegistry, MCPServerSpec


def _http_spec(
    url: str = "https://mcp.example.test/api",
    headers: dict[str, str] | None = None,
) -> MCPServerSpec:
    return MCPServerSpec(
        name="zapier",
        display="Zapier",
        description="remote http mcp",
        install_command=[],
        transport="http",
        url=url,
        headers=headers or {},
    )


# ---------------------------------------------------------------- spec fields

def test_spec_accepts_url_and_headers() -> None:
    spec = _http_spec(headers={"Authorization": "Bearer x"})
    assert spec.url == "https://mcp.example.test/api"
    assert spec.headers == {"Authorization": "Bearer x"}


def test_spec_url_and_headers_default_empty() -> None:
    spec = MCPServerSpec(
        name="s", display="S", description="", install_command=["echo"]
    )
    assert spec.url is None
    assert spec.headers == {}


# -------------------------------------------------------- load_from_mcp_json

def test_load_from_mcp_json_http_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_cfg = {
        "mcpServers": {
            "zapier": {
                "transport": "http",
                "url": "https://mcp.zapier.com/api/mcp/s/abc/mcp",
                "headers": {"Authorization": "Bearer $ZAPIER_TOKEN"},
                "enabled": True,
            }
        }
    }
    monkeypatch.setattr("jarvis.mcp.state.load_config", lambda: fake_cfg)
    reg = MCPRegistry()
    reg.load_from_mcp_json()
    spec = reg.get_spec("zapier")
    assert spec is not None
    assert spec.transport == "http"
    assert spec.url == "https://mcp.zapier.com/api/mcp/s/abc/mcp"
    assert spec.headers == {"Authorization": "Bearer $ZAPIER_TOKEN"}


def test_load_from_mcp_json_http_without_url_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cfg = {"mcpServers": {"broken": {"transport": "http", "enabled": True}}}
    monkeypatch.setattr("jarvis.mcp.state.load_config", lambda: fake_cfg)
    reg = MCPRegistry()
    reg.load_from_mcp_json()
    assert reg.get_spec("broken") is None


def test_load_from_mcp_json_stdio_entry_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: the stdio path must be unchanged by the http branch."""
    fake_cfg = {
        "mcpServers": {
            "fs": {
                "command": "uvx",
                "args": ["mcp-server-filesystem"],
                "enabled": True,
            }
        }
    }
    monkeypatch.setattr("jarvis.mcp.state.load_config", lambda: fake_cfg)
    reg = MCPRegistry()
    reg.load_from_mcp_json()
    spec = reg.get_spec("fs")
    assert spec is not None
    assert spec.transport == "stdio"
    assert spec.install_command == ["uvx", "mcp-server-filesystem"]


# --------------------------------------------------- header secret resolution

def test_resolve_headers_substitutes_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.mcp.client.get_secret",
        lambda name, env_fallback=None: "SEKRET" if name == "ZAPIER_TOKEN" else None,
    )
    spec = _http_spec(
        headers={"Authorization": "Bearer $ZAPIER_TOKEN", "X-Plain": "static"}
    )
    client = MCPClient(spec)
    resolved = client._resolve_headers()
    assert resolved == {"Authorization": "Bearer SEKRET", "X-Plain": "static"}


def test_resolve_headers_keeps_placeholder_when_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.mcp.client.get_secret", lambda name, env_fallback=None: None
    )
    spec = _http_spec(headers={"Authorization": "Bearer $MISSING"})
    client = MCPClient(spec)
    assert client._resolve_headers() == {"Authorization": "Bearer $MISSING"}


# ------------------------------------------------------------ http start wiring

@pytest.mark.asyncio
async def test_start_http_transport_wires_streamablehttp_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class _CM:
        def __init__(self, url: str, headers: dict | None = None, **kw: object) -> None:
            calls["url"] = url
            calls["headers"] = headers

        async def __aenter__(self):  # noqa: ANN204
            return ("READ", "WRITE", lambda: "session-id")

        async def __aexit__(self, *a: object) -> bool:
            return False

    def _fake_http(url: str, headers: dict | None = None, **kw: object) -> _CM:
        return _CM(url, headers, **kw)

    class _Tool:
        name = "run_zap"
        description = "run a zap"
        inputSchema: dict = {}

    class _ListResult:
        tools = [_Tool()]

    class _Session:
        def __init__(self, read: object, write: object) -> None:
            calls["read"] = read
            calls["write"] = write

        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def initialize(self) -> None:
            calls["initialized"] = True

        async def list_tools(self) -> _ListResult:
            return _ListResult()

    monkeypatch.setattr(
        "mcp.client.streamable_http.streamablehttp_client", _fake_http, raising=False
    )
    monkeypatch.setattr("mcp.ClientSession", _Session, raising=False)
    monkeypatch.setattr(
        "jarvis.mcp.client.get_secret",
        lambda name, env_fallback=None: "TOK" if name == "ZAPIER_TOKEN" else None,
    )

    spec = _http_spec(headers={"Authorization": "Bearer $ZAPIER_TOKEN"})
    client = MCPClient(spec)
    await client.start()
    try:
        assert calls["url"] == "https://mcp.example.test/api"
        assert calls["headers"] == {"Authorization": "Bearer TOK"}
        assert calls["initialized"] is True
        assert client.is_healthy is True
        tools = await client.list_tools()
        assert any(t["name"] == "run_zap" for t in tools)
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_start_http_without_url_raises() -> None:
    spec = MCPServerSpec(
        name="x",
        display="X",
        description="",
        install_command=[],
        transport="http",
        url=None,
    )
    client = MCPClient(spec)
    with pytest.raises(ValueError, match="url"):
        await client.start()
