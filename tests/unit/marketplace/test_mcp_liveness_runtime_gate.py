"""M2 (honest status): a stdio MCP plugin (GitHub=docker, Supabase=npx) must NOT
report "Live" when its launcher binary is absent — connecting saves the token but
the tools never appear, and a green "Connected · Live" badge is a lie. Gate stdio
liveness on the launcher being on PATH and surface ``runtime_missing``.
"""
from __future__ import annotations

from jarvis.ui.web.marketplace_routes import _mcp_live


def test_http_transport_is_always_live():
    assert _mcp_live({"transport": "http"}) == (True, None)


def test_stdio_with_present_launcher_is_live():
    # "python" is guaranteed on PATH in the test runner.
    assert _mcp_live({"transport": "stdio", "install": ["python", "-m", "x"]}) == (True, None)


def test_stdio_with_missing_launcher_is_not_live_and_flags_runtime():
    live, missing = _mcp_live({"transport": "stdio", "install": ["definitely-not-a-binary-xyz", "run"]})
    assert live is False
    assert missing == "definitely-not-a-binary-xyz"


def test_no_transport_is_not_live():
    assert _mcp_live({}) == (False, None)


def test_http_connected_with_zero_live_tools_is_not_live(monkeypatch):
    from jarvis.ui.web import marketplace_routes as mr

    class _Reg:
        def live_tool_count(self, pid): return 0
        def last_connect_error(self, pid): return "HTTP 401 unauthorized"

    monkeypatch.setattr(mr, "_live_plugin_registry", lambda: _Reg())
    live, hint = mr._mcp_live({"transport": "http"}, plugin_id="notion", status="connected")
    assert live is False
    assert "401" in hint


def test_http_without_registry_stays_live(monkeypatch):
    from jarvis.ui.web import marketplace_routes as mr

    monkeypatch.setattr(mr, "_live_plugin_registry", lambda: None)
    live, hint = mr._mcp_live({"transport": "http"}, plugin_id="notion", status="connected")
    assert live is True and hint is None
