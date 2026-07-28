"""Cross-layer parity guard for the sidebar section ids (AP-4).

A section id crosses more layers than it looks: the ``SectionId`` union, the
``SECTION_IDS`` array, the ``SECTION_LABELS`` record (all three in
``store/events.ts``), the ``MainView`` switch that decides which view is
mounted, the ``Sidebar`` row that has to stay highlighted for it, the tab bar
of the merged voice section, and a ``nav.*`` string in every locale.

TypeScript catches almost none of that. ``as const satisfies readonly
SectionId[]`` only rejects an array entry that is missing from the union —
never a union member missing from the array (docs/BUGS.md, the recurring
enum-drift class). A tab id that never reaches ``MainView`` renders a blank
pane. An id missing from ``matchIds`` un-highlights the sidebar row while the
user is standing on it. A missing locale key prints "nav.voice_language" at
the user.

The set of ids parsed out of each layer is asserted NON-EMPTY before it is
compared, so a reformat that breaks a regex fails loudly instead of passing
against an empty set.

Not covered here on purpose: ``SECTION_IDS`` <-> ``navigate.KNOWN`` set
equality, which ``tests/unit/plugins/tool/test_navigate.py`` already derives
from both sides.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FRONTEND = _REPO_ROOT / "jarvis" / "ui" / "web" / "frontend" / "src"
_EVENTS_TS = _FRONTEND / "store" / "events.ts"
_MAIN_VIEW_TSX = _FRONTEND / "components" / "layout" / "MainView.tsx"
_SIDEBAR_TSX = _FRONTEND / "components" / "layout" / "Sidebar.tsx"
_VOICE_HUB_TSX = _FRONTEND / "views" / "VoiceHubView.tsx"
_LOCALES = _FRONTEND / "i18n" / "locales"

SUPPORTED_LOCALES = ("de", "en", "es")

#: The five tabs behind the merged voice section. "dictation" and "dictionary"
#: keep their original ids on purpose — renaming them would break the
#: Command-Registry ``ui_section`` binding and every existing voice deep-link.
EXPECTED_VOICE_TABS = (
    "dictation",
    "dictionary",
    "voice-shortcuts",
    "voice-language",
    "voice-api-keys",
)


def _read(path: Path) -> str:
    assert path.exists(), f"frontend file missing: {path}"
    return path.read_text(encoding="utf-8")


def _strip_comments(source: str) -> str:
    """Drop ``//`` and ``/* */`` comments.

    The union and the label record are heavily commented, and those comments
    quote ids ("dictation", "{name} Voice"). Parsing them as members would make
    the test agree with prose instead of with code.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def _union_members() -> set[str]:
    body = re.search(
        r"export type SectionId\s*=\s*(.+?);", _strip_comments(_read(_EVENTS_TS)), re.DOTALL
    )
    assert body is not None, "SectionId union not found in events.ts"
    return set(re.findall(r'"([a-z0-9_-]+)"', body.group(1)))


def _section_ids() -> list[str]:
    body = re.search(
        r"export const SECTION_IDS\s*=\s*\[(.*?)\]\s*as const",
        _strip_comments(_read(_EVENTS_TS)),
        re.DOTALL,
    )
    assert body is not None, "SECTION_IDS array not found in events.ts"
    return re.findall(r'"([a-z0-9_-]+)"', body.group(1))


def _section_labels() -> dict[str, str]:
    body = re.search(
        r"export const SECTION_LABELS[^=]*=\s*\{(.*?)\n\};",
        _strip_comments(_read(_EVENTS_TS)),
        re.DOTALL,
    )
    assert body is not None, "SECTION_LABELS record not found in events.ts"
    pairs = re.findall(r'^\s*"?([a-z0-9_-]+)"?\s*:\s*"([^"]*)"', body.group(1), re.M)
    return dict(pairs)


def _voice_tabs() -> list[tuple[str, str]]:
    """``(section id, i18n label key)`` for every tab of the voice hub."""
    body = re.search(
        r"const TABS\s*=\s*\[(.*?)\]\s*as const", _read(_VOICE_HUB_TSX), re.DOTALL
    )
    assert body is not None, "TABS array not found in VoiceHubView.tsx"
    return re.findall(
        r'id:\s*"([a-z0-9_-]+)"\s*,\s*labelKey:\s*"([a-z0-9_.]+)"', body.group(1)
    )


def _voice_hub_routed_ids() -> set[str]:
    """Every ``case "…":`` that falls through to ``<VoiceHubView />``."""
    body = re.search(
        r'((?:\s*case\s+"[a-z0-9_-]+":)+)\s*return\s*<VoiceHubView\s*/>;',
        _read(_MAIN_VIEW_TSX),
    )
    assert body is not None, "VoiceHubView switch arm not found in MainView.tsx"
    return set(re.findall(r'case\s+"([a-z0-9_-]+)":', body.group(1)))


def _sidebar_voice_match_ids() -> set[str]:
    """The ``matchIds`` of the single sidebar row labelled ``nav.voice``."""
    body = re.search(
        r'labelKey:\s*"nav\.voice"\s*,[\s\S]{0,400}?matchIds:\s*\[([^\]]*)\]',
        _read(_SIDEBAR_TSX),
    )
    assert body is not None, "voice NavItem with matchIds not found in Sidebar.tsx"
    return set(re.findall(r'"([a-z0-9_-]+)"', body.group(1)))


def _locale(name: str) -> dict:
    path = _LOCALES / f"{name}.json"
    assert path.exists(), f"locale file missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _nested(data: dict, dotted: str) -> object | None:
    node: object = data
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)  # type: ignore[assignment]
        if node is None:
            return None
    return node


def test_the_three_section_id_layers_are_one_set() -> None:
    """`satisfies` only guards one direction; this guards both."""
    union = _union_members()
    array = _section_ids()
    labels = _section_labels()
    # Guard against a trivially-green empty/partial parse of any layer.
    assert union, "parsed no SectionId union members"
    assert array, "parsed no SECTION_IDS entries"
    assert labels, "parsed no SECTION_LABELS entries"
    assert len(array) == len(set(array)), array
    assert set(array) == union, set(array).symmetric_difference(union)
    assert set(labels) == union, set(labels).symmetric_difference(union)


def test_every_section_label_is_a_readable_non_empty_string() -> None:
    """These labels are read back verbatim by the voice-navigation toast.

    They are deliberately plain English and NOT interpolated, so a ``{name}``
    token here would be spoken and rendered literally.
    """
    labels = _section_labels()
    assert labels, "parsed no SECTION_LABELS entries"
    for section, label in labels.items():
        assert label.strip(), section
        assert "{name}" not in label, f"{section}: {label!r} is never interpolated"


def test_the_voice_hub_fronts_exactly_the_expected_tabs() -> None:
    tabs = _voice_tabs()
    assert tabs, "parsed no tabs from VoiceHubView.tsx"
    assert [section for section, _ in tabs] == list(EXPECTED_VOICE_TABS)


def test_every_voice_tab_is_a_real_section_with_a_label() -> None:
    tabs = _voice_tabs()
    assert tabs, "parsed no tabs from VoiceHubView.tsx"
    known = set(_section_ids())
    labels = _section_labels()
    assert known and labels
    for section, _ in tabs:
        assert section in known, f"tab {section} is not a SectionId"
        assert labels.get(section, "").strip(), f"tab {section} has no SECTION_LABELS entry"


def test_every_voice_tab_is_routed_and_stays_highlighted() -> None:
    """A tab id that never reaches MainView renders a blank pane."""
    tabs = {section for section, _ in _voice_tabs()}
    routed = _voice_hub_routed_ids()
    matched = _sidebar_voice_match_ids()
    assert tabs, "parsed no tabs from VoiceHubView.tsx"
    assert routed, "parsed no case labels from the MainView VoiceHubView arm"
    assert matched, "parsed no matchIds from the Sidebar voice row"
    assert routed == tabs, routed.symmetric_difference(tabs)
    assert matched == tabs, matched.symmetric_difference(tabs)


def test_every_voice_tab_label_key_resolves_in_every_locale() -> None:
    tabs = _voice_tabs()
    assert tabs, "parsed no tabs from VoiceHubView.tsx"
    for name in SUPPORTED_LOCALES:
        data = _locale(name)
        for section, label_key in tabs:
            value = _nested(data, label_key)
            assert isinstance(value, str) and value.strip(), (
                f"{name}.json: {label_key} (tab {section})"
            )


def test_every_voice_section_id_has_a_nav_key_in_every_locale() -> None:
    """The naming convention that keeps a new voice tab from shipping unlabelled.

    Section ids are hyphenated ("voice-shortcuts"); the locale keys under
    ``nav`` are the same value with underscores.
    """
    voice_ids = [s for s in _section_ids() if s.startswith("voice-")]
    assert voice_ids, "parsed no voice-* section ids"
    for name in SUPPORTED_LOCALES:
        data = _locale(name)
        for section in voice_ids:
            key = f"nav.{section.replace('-', '_')}"
            value = _nested(data, key)
            assert isinstance(value, str) and value.strip(), f"{name}.json: {key}"


def test_the_merged_sidebar_row_carries_the_brand_token_not_a_name() -> None:
    """CLAUDE.md §4 — the user-visible brand is derived from the wake word.

    ``nav.voice`` is the one label of this section that reaches the sidebar, so
    a hardcoded product name here would show up for every user regardless of
    the wake word they chose.
    """
    for name in SUPPORTED_LOCALES:
        value = _nested(_locale(name), "nav.voice")
        assert isinstance(value, str) and value.strip(), f"{name}.json: nav.voice"
        assert "{name}" in value, f"{name}.json: nav.voice lost the brand token"
