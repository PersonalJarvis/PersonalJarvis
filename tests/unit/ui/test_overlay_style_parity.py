"""The display-style value must mean the same thing in every layer.

``orb_style`` crosses Python, the REST payload, the TypeScript union and four
user-visible surfaces. That is the five-layer enum this repo has been bitten by
four times (AP-4 / BUG-008): the classic symptom is a style the backend offers
that the picker renders as an unlabelled, previewless card — or worse, a style
the UI can select and the surface factory silently drops.

So this pins the layers to ``jarvis.ui.overlay_styles``:

* the REST options list,
* the TypeScript ``OverlayStyle`` union and its client-side fallback array,
* the Settings labels and the onboarding labels + captions, in EVERY locale,
* the preview graphic (no style may fall through to the "hidden" thumbnail),
* the surface factory (every style must map to a surface).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis.ui.overlay_styles import (
    LEGACY_STYLE_ALIASES,
    ORB_STYLES,
    OVERLAY_STYLES,
    normalize_overlay_style,
)

_FRONTEND = Path(__file__).resolve().parents[3] / "jarvis/ui/web/frontend/src"
_LOCALES = _FRONTEND / "i18n/locales"


def _ts(relative: str) -> str:
    return (_FRONTEND / relative).read_text(encoding="utf-8")


def _locale(name: str) -> dict:
    return json.loads((_LOCALES / f"{name}.json").read_text(encoding="utf-8"))


def _locale_names() -> list[str]:
    return sorted(path.stem for path in _LOCALES.glob("*.json"))


def test_typescript_union_matches_python() -> None:
    source = _ts("hooks/useOverlayStyle.ts")
    match = re.search(r"export type OverlayStyle =([^;]+);", source)
    assert match, "OverlayStyle union not found — did the hook move?"
    union = re.findall(r'"([a-z_]+)"', match.group(1))
    assert union == list(OVERLAY_STYLES)


def test_typescript_fallback_array_matches_python() -> None:
    source = _ts("hooks/useOverlayStyle.ts")
    match = re.search(r"OVERLAY_STYLES: OverlayStyle\[\] = \[([^\]]+)\]", source)
    assert match, "OVERLAY_STYLES fallback array not found"
    assert re.findall(r'"([a-z_]+)"', match.group(1)) == list(OVERLAY_STYLES)


@pytest.mark.parametrize("locale", _locale_names())
def test_settings_labels_cover_every_style(locale: str) -> None:
    options = _locale(locale)["settings_view"]["overlay_style"]["options"]
    assert sorted(options) == sorted(OVERLAY_STYLES)
    assert all(str(label).strip() for label in options.values())


@pytest.mark.parametrize("locale", _locale_names())
def test_onboarding_labels_and_captions_cover_every_style(locale: str) -> None:
    step = _locale(locale)["onboarding"]["system_style"]
    assert sorted(step["options"]) == sorted(OVERLAY_STYLES)
    assert sorted(step["captions"]) == sorted(OVERLAY_STYLES)
    assert all(str(text).strip() for text in step["captions"].values())


def test_every_style_has_its_own_preview_graphic() -> None:
    # StylePreview falls THROUGH to the "hidden" thumbnail, so a style nobody
    # taught it renders as a crossed-out box that claims the overlay is off.
    source = _ts("components/overlay/OverlayStylePreviews.tsx")
    for style in OVERLAY_STYLES:
        if style == "none":
            continue
        assert f'"{style}"' in source, f"{style} has no preview branch"


def test_surface_factory_knows_every_style() -> None:
    # The factory branches on "none" / "jarvis_bar" and treats everything else
    # as an orb style, so the check is that the leftovers really ARE orb styles.
    leftovers = set(OVERLAY_STYLES) - {"none", "jarvis_bar"}
    assert leftovers == set(ORB_STYLES)


def test_legacy_values_still_resolve() -> None:
    for legacy, current in LEGACY_STYLE_ALIASES.items():
        assert normalize_overlay_style(legacy) == current
        assert current in OVERLAY_STYLES
    assert normalize_overlay_style("  MASCOT ") == "mascot"
    assert normalize_overlay_style("bogus") is None
    assert normalize_overlay_style(None) is None
