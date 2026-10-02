"""The Local voice vocabulary agrees across Python, TypeScript and the locales (AP-4).

Layers under test:

1. ``jarvis/realtime/local_voice_setup.py`` — ``PHASES``, ``MACHINE_CLASSES``,
   ``LATENCY_BASES``, ``SETUP_STAGES`` (source of truth).
2. ``jarvis/core/config.py`` — ``VOICE_ENGINE_VOICES`` (the Pydantic config).
3. ``src/lib/localVoice.ts`` — the ``LOCAL_VOICE_*`` const tuples the card uses.
4. ``src/i18n/locales/{en,de,es}.json`` — one label per value the card renders.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis.core.config import VOICE_ENGINE_VOICES
from jarvis.realtime import local_voice_setup as setup

REPO = Path(__file__).resolve().parents[3]
FRONTEND = REPO / "jarvis" / "ui" / "web" / "frontend" / "src"
TS = FRONTEND / "lib" / "localVoice.ts"
LOCALES = FRONTEND / "i18n" / "locales"


def _ts_tuple(name: str) -> tuple[str, ...]:
    text = TS.read_text(encoding="utf-8")
    block = re.search(rf"export const {name} = \[([\s\S]+?)\] as const", text)
    assert block, f"{name} not found in localVoice.ts"
    return tuple(re.findall(r'"([^"]+)"', block.group(1)))


@pytest.mark.parametrize(
    ("ts_name", "python_values"),
    [
        ("LOCAL_VOICE_PHASES", setup.PHASES),
        ("LOCAL_VOICE_MACHINE_CLASSES", setup.MACHINE_CLASSES),
        ("LOCAL_VOICE_LATENCY_BASES", setup.LATENCY_BASES),
        ("LOCAL_VOICE_SETUP_STAGES", setup.SETUP_STAGES),
        ("LOCAL_VOICE_VOICES", VOICE_ENGINE_VOICES),
    ],
)
def test_typescript_mirrors_python(ts_name: str, python_values: tuple[str, ...]) -> None:
    assert _ts_tuple(ts_name) == tuple(python_values)


@pytest.mark.parametrize("locale", ["en", "de", "es"])
def test_every_rendered_value_has_a_label(locale: str) -> None:
    view = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))["apikeys_view"]
    needed = (
        [f"local_voice_phase_{v}" for v in setup.PHASES]
        + [f"local_voice_machine_{v}" for v in setup.MACHINE_CLASSES]
        + [f"local_voice_basis_{v}" for v in setup.LATENCY_BASES]
        + [f"local_voice_stage_{v}" for v in (*setup.SETUP_STAGES, "process", "speech")]
        + [f"local_voice_voice_{v}" for v in VOICE_ENGINE_VOICES]
    )
    missing = [key for key in needed if key not in view]
    assert missing == []


def test_locales_carry_the_same_local_voice_keys() -> None:
    keys = {
        locale: {
            k for k in json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
            ["apikeys_view"] if k.startswith("local_voice_")
        }
        for locale in ("en", "de", "es")
    }
    assert keys["de"] == keys["en"] == keys["es"]
