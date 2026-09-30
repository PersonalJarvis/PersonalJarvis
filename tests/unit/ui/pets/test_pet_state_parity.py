"""The pet state vocabulary must mean the same thing in every layer.

A pet state is one animation row of a sprite sheet. It crosses Python
(``jarvis.ui.pets.states``), the manifest on disk, the REST payload, the
TypeScript mirror and the ``pets.states.*`` labels — the five-layer enum this
repo has been bitten by four times (AP-4 / BUG-008). The classic symptom here
is a preview that cycles through a state the backend never sends, or a state
label that renders its raw key on the settings page.

So this pins the layers to the Python module:

* the TS ``PET_STATES`` tuple, in the SAME order (it is the sheet's row order),
* the TS ``STATE_FALLBACKS`` table and the "none" pet id,
* the frame-size and frames-per-row limits the create dialog checks against,
* a ``pets.states.<state>`` label in every locale.

Every parsed set is asserted non-empty first, so a reformat that breaks a
regex fails loudly instead of passing against nothing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis.ui.pets.states import (
    FRAME_SIZES,
    MAX_FRAMES_PER_STATE,
    NO_PET_ID,
    PET_STATES,
    STATE_FALLBACKS,
)

_FRONTEND = Path(__file__).resolve().parents[4] / "jarvis/ui/web/frontend/src"
_TS_FILE = _FRONTEND / "lib/petStates.ts"
_LOCALES = _FRONTEND / "i18n/locales"


def _ts() -> str:
    assert _TS_FILE.exists(), f"TS mirror missing: {_TS_FILE}"
    return _TS_FILE.read_text(encoding="utf-8")


def _locale_names() -> list[str]:
    return sorted(path.stem for path in _LOCALES.glob("*.json"))


def test_typescript_states_match_python_in_order() -> None:
    match = re.search(r"export const PET_STATES = \[([^\]]*)\] as const;", _ts())
    assert match, "PET_STATES const tuple not found in petStates.ts"
    states = re.findall(r'"([a-z_]+)"', match.group(1))
    assert states, "parsed no states from petStates.ts"
    assert states == list(PET_STATES)


def test_typescript_fallbacks_match_python() -> None:
    match = re.search(r"export const STATE_FALLBACKS[^=]*=\s*\{([^}]*)\};", _ts())
    assert match, "STATE_FALLBACKS table not found in petStates.ts"
    pairs = dict(re.findall(r'([a-z_]+):\s*"([a-z_]+)"', match.group(1)))
    assert pairs, "parsed no fallbacks from petStates.ts"
    assert pairs == STATE_FALLBACKS


def test_typescript_constants_match_python() -> None:
    source = _ts()
    none_id = re.search(r'export const NO_PET_ID = "([^"]+)";', source)
    assert none_id and none_id.group(1) == NO_PET_ID
    sizes = re.search(r"export const FRAME_SIZES = \[([^\]]*)\] as const;", source)
    assert sizes, "FRAME_SIZES not found in petStates.ts"
    assert tuple(int(n) for n in re.findall(r"\d+", sizes.group(1))) == FRAME_SIZES
    max_frames = re.search(r"export const MAX_FRAMES_PER_STATE = (\d+);", source)
    assert max_frames and int(max_frames.group(1)) == MAX_FRAMES_PER_STATE


def test_every_fallback_chain_ends_at_idle() -> None:
    for state in PET_STATES:
        seen: set[str] = set()
        current = state
        while current != "idle":
            assert current not in seen, f"fallback loop at {current}"
            seen.add(current)
            current = STATE_FALLBACKS[current]


@pytest.mark.parametrize("locale", _locale_names())
def test_every_state_is_labelled_in_every_locale(locale: str) -> None:
    data = json.loads((_LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
    labels = data["pets"]["states"]
    assert sorted(labels) == sorted(PET_STATES)
    assert all(str(label).strip() for label in labels.values())
