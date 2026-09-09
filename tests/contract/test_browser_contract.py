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

