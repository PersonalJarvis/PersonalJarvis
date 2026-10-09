"""The desktop registry accepts each window offered by the frontend."""

import re
from pathlib import Path

from jarvis.ui.desktop_app import DETACHABLE_VIEWS
from jarvis.ui.section_windows import WINDOW_GROUPS

FRONTEND = Path(__file__).parents[3] / "jarvis/ui/web/frontend/src"


def _ids(source: str, name: str) -> set[str]:
    block = re.search(rf"{name}[^=]*=\s*\[(.*?)\];", source, re.S)
    assert block, name
    return set(re.findall(r'"([a-z-]+)"', block[1]))


def test_every_offered_section_can_open_natively():
    source = (FRONTEND / "lib/sectionWindows.ts").read_text(encoding="utf-8")
    offered = _ids(source, "DETACHABLE_SECTIONS")
    assert offered <= DETACHABLE_VIEWS.keys()
    assert "chats" not in offered
    assert "dictation" not in offered


def test_settings_tabs_share_exactly_one_window_on_both_sides():
    source = (FRONTEND / "components/layout/navGroups.ts").read_text(encoding="utf-8")
    assert _ids(source, "SETTINGS_HUB_IDS") == set(WINDOW_GROUPS["settings"])
