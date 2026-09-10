"""Deterministic tests for browser contracts and policy boundaries."""
from types import SimpleNamespace
from pathlib import Path
import pytest
from jarvis.society.browser.bridge import brain_messages
from jarvis.society.capabilities import capability_id_for_tool, tool_name_for_capability, build_catalog
from jarvis.ui.web.society_browser_routes import validate_control

def test_browser_capability_roundtrip():
    assert capability_id_for_tool("society_browser") == "core:browser"
    assert tool_name_for_capability("core:browser") == "society_browser"
    rows = build_catalog({"society_browser": SimpleNamespace(description="Browse", risk_tier="monitor")})
    assert "browser-use" in rows[0].aliases

@pytest.mark.parametrize("value", [
    {"op": "evaluate", "args": {}},
    {"op": "click", "args": {"x": float("nan"), "y": 1}},
    {"op": "click", "args": {"x": 1}},
    {"op": "text", "args": {"text": "x" * 8193}},
])
def test_invalid_remote_controls_are_rejected(value):
    with pytest.raises(ValueError):
        validate_control(value)

def test_browser_multimodal_messages_preserve_images():
    messages = brain_messages([{"role": "user", "content": [
        {"type": "text", "text": "Look at this"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}},
    ]}])
    assert messages[0].content == "Look at this"
    assert messages[0].images[0].data_b64 == "YQ=="

async def test_slow_viewer_keeps_approval_and_only_latest_pixels():
    from jarvis.society.browser.live import LiveUpdates
    buffer = LiveUpdates()
    buffer.put_nowait({"kind": "approval", "id": "one"})
    for sequence in range(100):
        buffer.put_nowait({"kind": "frame", "sequence": sequence})
    assert (await buffer.get())["id"] == "one"
    assert (await buffer.get())["sequence"] == 99

def test_browser_children_do_not_inherit_provider_credentials(monkeypatch, tmp_path):
    from jarvis.society.browser.install import worker_env
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.setenv("EXAMPLE_ACCESS_TOKEN", "test-placeholder")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-placeholder")
    monkeypatch.setenv("DATABASE_PASSWORD", "test-placeholder")
    monkeypatch.setenv("PIP_INDEX_URL", "https://example.invalid/simple")
    env = worker_env(tmp_path)
    assert "OPENAI_API_KEY" not in env
    assert "EXAMPLE_ACCESS_TOKEN" not in env
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "DATABASE_PASSWORD" not in env
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert "PIP_INDEX_URL" not in env
    assert env["ANONYMIZED_TELEMETRY"] == "false"

def test_managed_python_uses_windows_emulation_only_where_needed():
    from jarvis.society.browser.install import managed_python_request
    assert managed_python_request("win32", "ARM64") == "cpython-3.12-windows-x86_64-none"
    assert managed_python_request("win32", "AMD64") == "3.12"
    assert managed_python_request("linux", "aarch64") == "3.12"
    assert managed_python_request("darwin", "arm64") == "3.12"


