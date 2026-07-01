"""Integration tests for ScreenSnapshotTool (Phase 5, CL-9).

Skip when `mss` isn't installed or no display is available
(headless CI). Locally on Windows with a GUI they must pass green.
"""
from __future__ import annotations

import base64
import os
from uuid import uuid4

import pytest

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.plugins.tool.screen_snapshot import ScreenSnapshotTool


def _have_mss() -> bool:
    try:
        import mss  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _has_display() -> bool:
    """Heuristic: on Linux/Mac mss needs a display; Windows always has one."""
    if os.name == "nt":
        return True
    return bool(os.environ.get("DISPLAY"))


needs_capture = pytest.mark.skipif(
    not (_have_mss() and _has_display()),
    reason="Screenshot braucht mss + Display",
)


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="test",
        config={},
        memory_read=None,
        approved_by="auto",
    )


def test_tool_contract_compliance():
    """Tool must satisfy the Protocol shape: name, description, schema, risk_tier, async execute."""
    tool = ScreenSnapshotTool()
    assert tool.name == "screenshot"
    assert tool.risk_tier == "monitor"
    assert isinstance(tool.description, str) and tool.description
    assert isinstance(tool.schema, dict)
    assert tool.schema.get("type") == "object"
    assert "reason" in tool.schema.get("properties", {})
    # schema.required should be present (empty here)
    assert isinstance(tool.schema.get("required", []), list)
    # execute is a coroutine
    import inspect

    assert inspect.iscoroutinefunction(tool.execute)


@needs_capture
@pytest.mark.asyncio
async def test_screenshot_returns_valid_jpeg():
    """Decoded artifact data must start with the JPEG magic bytes (SOI marker)."""
    tool = ScreenSnapshotTool()
    result = await tool.execute({}, _ctx())
    assert isinstance(result, ToolResult)
    assert result.success, f"Tool failed: {result.error}"
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    raw = base64.b64decode(artifact["data"])
    # JPEG SOI: 0xFF 0xD8, then usually 0xFF 0xE0 (JFIF) or 0xFF 0xE1 (Exif)
    assert raw[:2] == b"\xff\xd8", "Artifact data is not a valid JPEG"


@needs_capture
@pytest.mark.asyncio
async def test_screenshot_size_under_500kb():
    """Even with a 4K monitor, the artifact blob must be <= 500KB."""
    tool = ScreenSnapshotTool()
    result = await tool.execute({"reason": "size-check"}, _ctx())
    assert result.success, result.error
    raw = base64.b64decode(result.artifacts[0]["data"])
    assert len(raw) <= 500_000, f"Screenshot {len(raw)} bytes > 500KB"


@needs_capture
@pytest.mark.asyncio
async def test_screenshot_artifacts_schema():
    """Artifact hat exakt die erwarteten Keys: type, mime, data."""
    tool = ScreenSnapshotTool()
    result = await tool.execute({}, _ctx())
    assert result.success, result.error
    artifact = result.artifacts[0]
    assert set(artifact.keys()) == {"type", "mime", "data"}
    assert artifact["type"] == "image"
    assert artifact["mime"] == "image/jpeg"
    assert isinstance(artifact["data"], str)
    # valides base64
    base64.b64decode(artifact["data"], validate=True)


@needs_capture
@pytest.mark.asyncio
async def test_screenshot_with_reason_in_output():
    """Der optionale `reason`-Arg landet in ToolResult.output."""
    tool = ScreenSnapshotTool()
    result = await tool.execute({"reason": "debug sidebar"}, _ctx())
    assert result.success, result.error
    assert "debug sidebar" in str(result.output)
