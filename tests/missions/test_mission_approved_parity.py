"""Python <-> TypeScript parity for mission languages and ``MissionApproved`` (AP-4).

``summary_local`` carries the es/pt approval summary; the frontend type must
declare every payload field the backend emits so the outcome view can read it.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.missions.events import MISSION_LANGUAGES, MissionApproved

_REPO = Path(__file__).resolve().parents[2]
_TS = _REPO / "jarvis" / "ui" / "web" / "frontend" / "src" / "types" / "missions.ts"


def _ts_fields(interface: str) -> set[str]:
    text = _TS.read_text(encoding="utf-8")
    m = re.search(rf"export interface {interface} extends BasePayload \{{(.*?)\n\}}", text, re.S)
    assert m, f"{interface} interface not found in missions.ts"
    return set(re.findall(r"^\s*([a-z_]+)\??:", m.group(1), re.M))


def _py_fields(model: type) -> set[str]:
    return set(model.model_fields)


def test_mission_approved_fields_python_ts_parity() -> None:
    py = _py_fields(MissionApproved)
    ts = _ts_fields("MissionApproved")
    assert py - ts == set(), f"fields missing in missions.ts: {py - ts}"
    assert ts - py == set(), f"fields missing in events.py: {ts - py}"
    assert "summary_local" in ts


def test_mission_dispatch_languages_python_ts_parity() -> None:
    text = _TS.read_text(encoding="utf-8")
    m = re.search(
        r"export interface MissionDispatched extends BasePayload \{.*?language: ([^;]+);",
        text,
        re.S,
    )
    assert m, "MissionDispatched.language not found in missions.ts"
    assert set(re.findall(r'"([^"]+)"', m.group(1))) == set(MISSION_LANGUAGES)
