"""Five-layer anti-drift guard: the TS wake-engine mirror == the Python SoT.

``engine`` is a string that crosses Python -> TOML -> Pydantic -> TypeScript ->
UI dropdown. Per docs/anti-drift-three-layer.md the frontend constant
(``wakeEngines.ts``) must list exactly the same values as
``jarvis/speech/wake_constants.WAKE_ENGINES``, or the Settings dropdown silently
drifts from what the backend accepts (BUG-008 class).
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.speech.wake_constants import WAKE_ENGINES

_TS = (
    Path(__file__).resolve().parents[3]
    / "jarvis"
    / "ui"
    / "web"
    / "frontend"
    / "src"
    / "constants"
    / "wakeEngines.ts"
)


def _parse_ts_engines(text: str) -> list[str]:
    m = re.search(r"WAKE_ENGINES\s*=\s*\[(.*?)\]", text, re.DOTALL)
    assert m, "WAKE_ENGINES array not found in wakeEngines.ts"
    return re.findall(r"[\"']([a-z_]+)[\"']", m.group(1))


def test_ts_mirror_file_exists() -> None:
    assert _TS.is_file(), f"missing TS mirror: {_TS}"


def test_ts_engines_match_python_sot_exactly() -> None:
    engines = _parse_ts_engines(_TS.read_text(encoding="utf-8"))
    assert tuple(engines) == WAKE_ENGINES, (
        f"TS {engines} != Python {WAKE_ENGINES} — update wakeEngines.ts"
    )
