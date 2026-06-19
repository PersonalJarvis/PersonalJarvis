"""Integration-Tests fuer ScreenSnapshotTool (Phase 5, CL-9).

Skippen wenn `mss` nicht installiert ist oder kein Display verfuegbar ist
(Headless-CI). Lokal auf Windows mit GUI muessen sie durchgruenen.
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
    """Heuristik: auf Linux/Mac braucht mss ein Display; Windows hat immer eins."""
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
    """Tool muss Protocol-Shape erfuellen: name, description, schema, risk_tier, async execute."""
    tool = ScreenSnapshotTool()
    assert tool.name == "screenshot"
    assert tool.risk_tier == "monitor"
    assert isinstance(tool.description, str) and tool.description
    assert isinstance(tool.schema, dict)
    assert tool.schema.get("type") == "object"
    assert "reason" in tool.schema.get("properties", {})
    # schema.required sollte vorhanden sein (hier leer)
    assert isinstance(tool.schema.get("required", []), list)
    # execute ist Coroutine
    import inspect

    assert inspect.iscoroutinefunction(tool.execute)


@needs_capture
@pytest.mark.asyncio
async def test_screenshot_returns_valid_jpeg():
    """Artifact-Data muss dekodiert mit JPEG-Magic-Bytes starten (SOI-Marker)."""
    tool = ScreenSnapshotTool()
    result = await tool.execute({}, _ctx())
    assert isinstance(result, ToolResult)
    assert result.success, f"Tool fehlgeschlagen: {result.error}"
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    raw = base64.b64decode(artifact["data"])
    # JPEG SOI: 0xFF 0xD8, dann meist 0xFF 0xE0 (JFIF) oder 0xFF 0xE1 (Exif)
    assert raw[:2] == b"\xff\xd8", "Artifact-Data ist kein gueltiges JPEG"


@needs_capture
@pytest.mark.asyncio
async def test_screenshot_size_under_500kb():
    """Auch bei 4K-Monitor muss der Artifact-Blob <= 500KB sein."""
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
