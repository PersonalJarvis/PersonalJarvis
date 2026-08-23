"""Brand marks in the artifact brief — the original or plain text, never a tile.

What is pinned: a request that names a brand gets that brand's ORIGINAL mark
inlined (in order of first mention, capped), a mono mark follows the page's
ink, a request that names nothing adds only the rule, a missing asset folder
degrades to the rule — and the brief carries the section.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.artifacts.brand_marks import (
    BRAND_FAMILIES,
    BRAND_MARKS_RULE,
    BrandMark,
    assets_root,
    brand_marks_section,
    find_brand_marks,
    mentioned_families,
)
from jarvis.artifacts.brief import build_artifact_brief


def test_every_family_points_at_a_bundled_file_with_a_ledger() -> None:
    root = assets_root()
    for fam in BRAND_FAMILIES:
        assert (root / fam.folder / fam.file).is_file(), fam.family
        assert (root / fam.folder / "LOGOS.md").is_file(), fam.folder


def test_mentions_come_back_in_order_of_first_appearance() -> None:
    text = "Put Slack first, then the OpenAI API, then Claude and the ChatGPT app."
    assert [f.family for f in mentioned_families(text)] == ["slack", "openai", "claude"]


def test_aliases_are_word_bounded() -> None:
    assert mentioned_families("GPTQ quantisation and foo3 bars") == []
    assert [f.family for f in mentioned_families("the gpt-5 launch")] == ["openai"]
    assert [f.family for f in mentioned_families("Vertex AI hosts Gemini")] == [
        "google-cloud",
        "gemini",
    ]


def test_marks_are_clean_inline_svg_and_mono_follows_the_ink() -> None:
    marks = find_brand_marks("Compare OpenAI and Gemini; link the GitHub repo.")
    by_family = {m.family: m for m in marks}
    assert set(by_family) == {"openai", "gemini", "github"}
    for mark in marks:
        assert mark.svg.startswith("<svg")
        assert "<?xml" not in mark.svg and "<title>" not in mark.svg
        assert ' width="' not in mark.svg.split(">", 1)[0]
    # Mono marks: the ink fill is currentColor so the page's --ink drives it.
    assert by_family["openai"].render == "mono" and 'fill="currentColor"' in by_family["openai"].svg
    assert "#fff" not in by_family["github"].svg.lower()
    assert 'fill="currentColor"' in by_family["github"].svg
    # A colour mark is untouched — its colours are the brand.
    assert by_family["gemini"].render == "colour"


def test_limit_and_budget_bound_the_section() -> None:
    text = " ".join(f.aliases[0] for f in BRAND_FAMILIES)
    assert len(find_brand_marks(text, limit=3)) == 3
    assert find_brand_marks(text, max_total_chars=10) == []


def test_missing_assets_degrade_to_the_rule(tmp_path: Path) -> None:
    assert find_brand_marks("OpenAI and Slack", root=tmp_path) == []
    section = brand_marks_section([])
    assert section.startswith(BRAND_MARKS_RULE)
    assert "names no brand" in section


def test_section_lists_each_mark_once_with_its_render_note() -> None:
    marks = [
        BrandMark(family="openai", label="OpenAI", render="mono", svg="<svg>o</svg>"),
        BrandMark(family="gemini", label="Gemini", render="colour", svg="<svg>g</svg>"),
    ]
    section = brand_marks_section(marks)
    assert section.count("<svg>o</svg>") == 1
    assert "mono — inherits `color`" in section
    assert "colour mark — do not recolour" in section


def test_brief_carries_the_rule_and_the_supplied_marks() -> None:
    marks = find_brand_marks("A comparison of Claude and GPT-5 pricing.")
    brief = build_artifact_brief(
        "A comparison of Claude and GPT-5 pricing.",
        title="Model pricing",
        language="en",
        brand_marks=marks,
    )
    assert "## Brand marks — the original or none" in brief.prompt
    assert "no lettered tile" in brief.prompt
    for mark in marks:
        assert mark.svg in brief.prompt
    # Without marks the brief still states the rule — text, never a stand-in.
    bare = build_artifact_brief("A weather page.", title="Weather", language="en")
    assert "## Brand marks — the original or none" in bare.prompt
    assert "names no brand" in bare.prompt
