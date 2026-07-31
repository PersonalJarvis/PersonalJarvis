"""Privacy filtering: block, black out, scrub — in that order.

The image-region cases carry the most weight. A misplaced black box does not
fail loudly; it leaves the secret visible while the report claims it was
removed, which is the worst possible outcome for a privacy feature. The HiDPI
scaling case exists because that is exactly how it would happen in practice.
"""
from __future__ import annotations

from dataclasses import dataclass

from jarvis.screen_context.models import RedactionRule, WindowFacts
from jarvis.screen_context.redaction import (
    blocked_by_denylist,
    build_patterns,
    merge_reports,
    regions_to_redact,
    scrub_text,
    validate_pattern_source,
)


@dataclass
class FakeNode:
    """Shaped like ``jarvis.core.protocols.UIANode`` — only what we read."""

    name: str = ""
    value: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    is_password: bool = False
    role: str = "Text"


# --------------------------------------------------------------------------
# Denylist
# --------------------------------------------------------------------------


def test_denylist_matches_app_name_case_insensitively() -> None:
    facts = WindowFacts(app_name="1Password.exe", title="Vault")
    assert blocked_by_denylist(facts, ["1password"]) == "1password"


def test_denylist_matches_the_window_title_too() -> None:
    """Browsers expose the sensitive part in the title, not the app name."""
    facts = WindowFacts(app_name="chrome.exe", title="Online Banking — Private")
    assert blocked_by_denylist(facts, ["online banking"]) == "online banking"


def test_denylist_survives_a_version_bump() -> None:
    """Substring, not exact: an exact list stops protecting after an update."""
    assert blocked_by_denylist(WindowFacts(app_name="1Password 8"), ["1Password"])


def test_unknown_window_is_not_blocked() -> None:
    """No facts is not a match — the capture is governed by the other layers."""
    assert blocked_by_denylist(WindowFacts(), ["1password"]) is None


def test_empty_denylist_entries_are_ignored() -> None:
    """A blank line in the config must not block every window."""
    assert blocked_by_denylist(WindowFacts(app_name="editor"), ["", "   "]) is None


# --------------------------------------------------------------------------
# Image regions
# --------------------------------------------------------------------------


def test_password_field_is_always_a_region() -> None:
    nodes = [FakeNode(name="Password", bounds=(100, 200, 300, 40), is_password=True)]
    regions = regions_to_redact(
        nodes, target_bbox=(0, 0, 1920, 1080), patterns=build_patterns()
    )
    assert len(regions) == 1
    rect, rule, _label = regions[0]
    assert rule is RedactionRule.PASSWORD_FIELD
    assert rect == (100, 200, 300, 40)


def test_node_text_matching_a_pattern_is_blacked_out() -> None:
    nodes = [FakeNode(value="4111 1111 1111 1111", bounds=(10, 20, 200, 30))]
    regions = regions_to_redact(
        nodes, target_bbox=(0, 0, 1920, 1080), patterns=build_patterns()
    )
    assert [r[2] for r in regions] == ["card"]


def test_regions_are_translated_into_image_local_coordinates() -> None:
    """A capture of the LEFT monitor starts at negative virtual X.

    Forgetting the translation puts every box off-image — silently, because
    Pillow happily draws outside the canvas.
    """
    nodes = [FakeNode(bounds=(-1800, 100, 200, 50), is_password=True)]
    regions = regions_to_redact(
        nodes, target_bbox=(-1920, 0, 1920, 1080), patterns=build_patterns()
    )
    assert regions[0][0] == (120, 100, 200, 50)


def test_regions_scale_to_backing_pixels() -> None:
    """macOS: geometry in points, capture in backing pixels (2x on Retina).

    Without the scale the box covers a quarter of the field and the secret
    stays visible next to it.
    """
    nodes = [FakeNode(bounds=(100, 100, 200, 40), is_password=True)]
    regions = regions_to_redact(
        nodes, target_bbox=(0, 0, 1440, 900), patterns=build_patterns(), scale=2.0
    )
    assert regions[0][0] == (200, 200, 400, 80)


def test_node_outside_the_captured_area_is_dropped() -> None:
    """A field on the OTHER monitor is not in this picture."""
    nodes = [FakeNode(bounds=(3000, 100, 200, 40), is_password=True)]
    regions = regions_to_redact(
        nodes, target_bbox=(0, 0, 1920, 1080), patterns=build_patterns()
    )
    assert regions == ()


def test_node_straddling_the_edge_is_clipped_not_dropped() -> None:
    """Half a password field is still half a password field."""
    nodes = [FakeNode(bounds=(1800, 100, 400, 40), is_password=True)]
    regions = regions_to_redact(
        nodes, target_bbox=(0, 0, 1920, 1080), patterns=build_patterns()
    )
    assert regions[0][0] == (1800, 100, 120, 40)


def test_ordinary_nodes_are_not_redacted() -> None:
    nodes = [FakeNode(name="Save", bounds=(10, 10, 60, 20))]
    assert regions_to_redact(
        nodes, target_bbox=(0, 0, 1920, 1080), patterns=build_patterns()
    ) == ()


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------


def test_sensitive_text_becomes_a_typed_placeholder() -> None:
    """A placeholder, not a hole: a model fills holes, it reads placeholders."""
    scrubbed, hits = scrub_text("pay with 4111 1111 1111 1111 now", build_patterns())
    assert "4111" not in scrubbed
    assert "[redacted:card]" in scrubbed
    assert hits[0].label == "card"
    assert hits[0].region is None


def test_multiple_pattern_families_are_all_applied() -> None:
    text = "Authorization: Bearer abc123xyz and key sk-abcdefghijklmnopqrst"
    scrubbed, hits = scrub_text(text, build_patterns())
    assert "abc123xyz" not in scrubbed
    assert "sk-abcdefghijklmnopqrst" not in scrubbed
    assert len(hits) >= 2


def test_private_key_header_is_caught() -> None:
    scrubbed, _ = scrub_text("-----BEGIN OPENSSH PRIVATE KEY-----", build_patterns())
    assert "PRIVATE KEY" not in scrubbed


def test_ordinary_text_is_untouched() -> None:
    text = "Build failed: 3 errors in main.py at line 42"
    scrubbed, hits = scrub_text(text, build_patterns())
    assert scrubbed == text
    assert hits == ()


def test_custom_labelled_pattern_reaches_the_report() -> None:
    patterns = build_patterns(["employee_id:EMP-\\d{5}"])
    scrubbed, hits = scrub_text("ticket for EMP-12345", patterns)
    assert "[redacted:employee_id]" in scrubbed
    assert hits[0].label == "employee_id"


def test_invalid_custom_pattern_is_skipped_not_fatal() -> None:
    """A bad regex must not brick capture — but it must not be silent either.

    (The WARNING log is the other half of this contract; see redaction._compile.)
    """
    patterns = build_patterns(["broken:([unclosed"])
    scrubbed, _ = scrub_text("4111 1111 1111 1111", patterns)
    assert "[redacted:card]" in scrubbed, "defaults must still apply"


def test_custom_pattern_rejects_nested_repetition() -> None:
    source = r"(a+)+$"

    assert validate_pattern_source(source) is not None
    assert build_patterns([f"unsafe:{source}"], include_defaults=False) == ()


def test_bounded_custom_pattern_remains_available() -> None:
    assert validate_pattern_source(r"CUST-[0-9]+") is None


def test_defaults_can_be_replaced_entirely() -> None:
    patterns = build_patterns(["only:SECRET"], include_defaults=False)
    scrubbed, _ = scrub_text("4111 1111 1111 1111 SECRET", patterns)
    assert "4111" in scrubbed
    assert "[redacted:only]" in scrubbed


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def test_report_summary_distinguishes_image_from_text() -> None:
    _, text_hits = scrub_text("4111 1111 1111 1111", build_patterns())
    regions = regions_to_redact(
        [FakeNode(bounds=(0, 0, 10, 10), is_password=True)],
        target_bbox=(0, 0, 100, 100),
        patterns=build_patterns(),
    )
    from PIL import Image

    from jarvis.screen_context.redaction import apply_image_redactions

    _, region_hits = apply_image_redactions(
        Image.new("RGB", (100, 100), (255, 255, 255)), regions
    )
    report = merge_reports(region_hits, text_hits)
    assert report.region_count == 1
    assert report.text_count == 1
    summary = report.summary()
    assert "image region" in summary and "text match" in summary


def test_redaction_actually_blackens_the_pixels() -> None:
    """The promise is pixels, not bookkeeping.

    A report that says "redacted" over an unchanged image is the failure mode
    that matters here, so this asserts on the canvas itself.
    """
    from PIL import Image

    from jarvis.screen_context.redaction import apply_image_redactions

    image = Image.new("RGB", (100, 100), (255, 255, 255))
    image, hits = apply_image_redactions(
        image, (((10, 10, 20, 20), RedactionRule.PASSWORD_FIELD, "password_field"),)
    )
    assert image.getpixel((15, 15)) == (0, 0, 0), "the region must be opaque black"
    assert image.getpixel((90, 90)) == (255, 255, 255), "the rest must be untouched"
    assert hits[0].region == (10, 10, 20, 20)


def test_apply_image_redactions_is_a_noop_without_regions() -> None:
    """No regions must not touch a drawing backend at all."""
    from jarvis.screen_context.redaction import apply_image_redactions

    image = object()
    result, hits = apply_image_redactions(image, ())
    assert result is image
    assert hits == ()
