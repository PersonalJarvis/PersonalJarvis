"""Tests for the ``navigate`` router tool.

The tool lets a spoken/typed command move the desktop UI to a sidebar section by
publishing a ``NavigateSidebar`` event (the frontend listener in
``useWebSocket.ts`` already switches the section on it). Contract:

- a canonical section id ("socials") → publishes NavigateSidebar(section="socials"),
- a natural-language alias ("Einstellungen", "social media") → normalized to the id,
- an unknown section → success=False, nothing published,
- the tool's known sections stay in parity with the frontend SECTION_IDS
  (the wire-format enum guard from docs/anti-drift-three-layer.md).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarvis.core.events import NavigateSidebar
from jarvis.plugins.tool.navigate import NavigateTool


class RecordingBus:
    """Minimal fake bus that records published events (no unittest.mock)."""

    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


@pytest.fixture()
def bus() -> RecordingBus:
    return RecordingBus()


@pytest.fixture()
def tool(bus: RecordingBus) -> NavigateTool:
    return NavigateTool(bus=bus)


async def test_canonical_id_publishes_navigate(tool: NavigateTool, bus: RecordingBus) -> None:
    res = await tool.execute({"section": "socials"}, ctx=None)
    assert res.success is True
    assert len(bus.events) == 1
    ev = bus.events[0]
    assert isinstance(ev, NavigateSidebar)
    assert ev.section == "socials"


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("Socials", "socials"),
        ("social media", "socials"),
        ("Einstellungen", "settings"),
        ("settings", "settings"),
        ("agenten", "agents"),
        ("sub-agents", "agents"),
        ("Aufgaben", "tasks"),
        ("notizen", "memory"),
        ("Kontakte", "contacts"),
        ("kontakt", "contacts"),
        ("contacts", "contacts"),
    ],
)
async def test_alias_normalizes_to_id(
    bus: RecordingBus, tool: NavigateTool, spoken: str, expected: str
) -> None:
    res = await tool.execute({"section": spoken}, ctx=None)
    assert res.success is True, res
    assert isinstance(bus.events[0], NavigateSidebar)
    assert bus.events[0].section == expected


async def test_unknown_section_fails_and_publishes_nothing(
    tool: NavigateTool, bus: RecordingBus
) -> None:
    res = await tool.execute({"section": "weather"}, ctx=None)
    assert res.success is False
    assert bus.events == []


async def test_missing_section_fails(tool: NavigateTool, bus: RecordingBus) -> None:
    res = await tool.execute({}, ctx=None)
    assert res.success is False
    assert bus.events == []


def test_tool_metadata() -> None:
    assert NavigateTool.name == "navigate"
    assert NavigateTool.risk_tier == "safe"
    # The schema enumerates valid sections so the router picks a real one.
    enum = NavigateTool.schema["properties"]["section"]["enum"]
    assert "socials" in enum and "settings" in enum


def test_contacts_section_is_known() -> None:
    """The Contacts section (Chunk A) is a navigable target."""
    assert "contacts" in NavigateTool.known_sections()
    assert "contacts" in NavigateTool.schema["properties"]["section"]["enum"]


def test_known_sections_match_frontend_section_ids() -> None:
    """Drift guard: the tool's sections must equal the frontend SECTION_IDS."""
    events_ts = (
        Path(__file__).resolve().parents[4]
        / "jarvis/ui/web/frontend/src/store/events.ts"
    )
    text = events_ts.read_text(encoding="utf-8")
    block = re.search(r"SECTION_IDS\s*=\s*\[(.*?)\]\s*as const", text, re.DOTALL)
    assert block, "could not find SECTION_IDS array in store/events.ts"
    # [a-z0-9_-]+ — underscore added to match "run_inspector" (hyphen-only
    # pattern silently dropped it, so KNOWN had run_inspector but ts_ids didn't).
    ts_ids = set(re.findall(r'"([a-z0-9_-]+)"', block.group(1)))
    assert ts_ids, "no ids parsed from SECTION_IDS"
    assert NavigateTool.known_sections() == ts_ids
