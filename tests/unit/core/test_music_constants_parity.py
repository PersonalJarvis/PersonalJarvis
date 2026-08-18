"""Parity test for the layers that share the music-settings vocabulary
(five-layer pattern, BUG-008 class).

1. ``jarvis/core/music_constants.py``                — the Python tuples (source of truth)
2. ``jarvis/core/config.py::MusicConfig``           — the Pydantic Literals (asserted at import)
3. ``jarvis/ui/web/settings_routes.py``             — the accepted set the route answers
4. ``jarvis/ui/web/frontend/src/lib/musicSettings.ts`` — the TS const tuples
5. ``jarvis/ui/web/frontend/src/i18n/locales/{en,de,es}.json``
                                                     — a label + description per value

A drift shows up here as one failing test instead of a Pydantic
``literal_error`` on load, a 400 on a value the UI offers, or a row that
renders its raw i18n key.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

from jarvis.core.config import MusicConfig
from jarvis.core.music_constants import MUSIC_PLAYBACK_MODES, MUSIC_SERVICES

REPO_ROOT = Path(__file__).resolve().parents[3]
TS_FILE = REPO_ROOT / "jarvis/ui/web/frontend/src/lib/musicSettings.ts"
LOCALES = REPO_ROOT / "jarvis/ui/web/frontend/src/i18n/locales"


def _ts_tuple(name: str) -> set[str]:
    src = TS_FILE.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[([^\]]*)\] as const;", src)
    assert match, f"{name} const tuple missing from {TS_FILE.name}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_python_literals_match_the_tuples() -> None:
    fields = MusicConfig.model_fields
    assert set(get_args(fields["preferred_service"].annotation)) == set(MUSIC_SERVICES)
    assert set(get_args(fields["playback"].annotation)) == set(MUSIC_PLAYBACK_MODES)


def test_typescript_mirror_matches_the_tuples() -> None:
    assert _ts_tuple("MUSIC_SERVICES") == set(MUSIC_SERVICES)
    assert _ts_tuple("MUSIC_PLAYBACK_MODES") == set(MUSIC_PLAYBACK_MODES)


def test_every_locale_labels_every_value() -> None:
    for lang in ("en", "de", "es"):
        data = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))
        music = data["settings_view"]["music"]
        assert set(music["service_labels"]) == set(MUSIC_SERVICES), lang
        assert set(music["service_options"]) == set(MUSIC_SERVICES), lang
        assert set(music["playback_labels"]) == set(MUSIC_PLAYBACK_MODES), lang
        assert set(music["playback_options"]) == set(MUSIC_PLAYBACK_MODES), lang
        for group in ("service_labels", "service_options", "playback_labels", "playback_options"):
            assert all(str(v).strip() for v in music[group].values()), (lang, group)


def test_route_accepts_exactly_the_tuples() -> None:
    from jarvis.ui.web import settings_routes

    assert set(settings_routes.MUSIC_SERVICES) == set(MUSIC_SERVICES)
    assert set(settings_routes.MUSIC_PLAYBACK_MODES) == set(MUSIC_PLAYBACK_MODES)
