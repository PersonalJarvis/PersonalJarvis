"""Unit tests for CU v2 conventions (coordinate space + action grammar),
the idempotency ledger, and the pure verification helpers."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.cu import conventions as conv
from jarvis.cu.ledger import ActionLedger, action_key
from jarvis.cu.verify import (
    click_point_in_focused_element,
    clickable_labels,
    crop_raw,
    element_is_focused,
    field_values_hint,
    human_handoff_reason,
    regions_equal,
    typed_text_landed,
)

# ---------------------------------------------------------------------------
# Coordinate convention resolution
# ---------------------------------------------------------------------------

def test_config_override_wins():
    assert conv.resolve_convention(
        "gemini", None, config_override="image_pixels",
    ) == "image_pixels"


def test_brain_capability_beats_family_default():
    brain = SimpleNamespace(coordinate_convention="image_pixels")
    assert conv.resolve_convention("gemini", brain) == "image_pixels"


def test_family_defaults():
    assert conv.resolve_convention("gemini", None) == "normalized_1000"
    assert conv.resolve_convention("claude-api", None) == "image_pixels"
    assert conv.resolve_convention("openai", None) == "image_pixels"
    assert conv.resolve_convention("some-new-provider", None) == "normalized_1000"


def test_prompt_block_mentions_the_right_space():
    norm = conv.coordinate_prompt_block("normalized_1000", 1366, 768)
    assert "0-1000" in norm and "1366" not in norm
    pix = conv.coordinate_prompt_block("image_pixels", 1366, 768)
    assert "1366x768" in pix and "0-1000" not in pix


# ---------------------------------------------------------------------------
# Action grammar
# ---------------------------------------------------------------------------

def test_parse_single_click():
    raw = '{"action": "click", "x": 512, "y": 300, "target": "Send button"}'
    actions = conv.parse_actions(raw)
    assert actions == [{
        "action": "click", "x": 512.0, "y": 300.0,
        "button": "left", "double": False, "target": "Send button",
    }]


def test_parse_batch_with_fences_and_prose():
    raw = (
        "Here is my plan:\n```json\n"
        '[{"action":"click","x":10,"y":20},'
        '{"action":"type","text":"hello"},'
        '{"action":"key","keys":["enter"]}]\n```'
    )
    actions = conv.parse_actions(raw)
    assert [a["action"] for a in actions] == ["click", "type", "key"]


def test_terminal_action_truncates_batch():
    raw = (
        '[{"action":"done","reason":"visible"},'
        '{"action":"click","x":1,"y":2}]'
    )
    actions = conv.parse_actions(raw)
    assert len(actions) == 1 and actions[0]["action"] == "done"


def test_numeric_strings_are_tolerated():
    actions = conv.parse_actions('{"action":"click","x":"512","y":"300.5"}')
    assert actions[0]["x"] == 512.0 and actions[0]["y"] == 300.5


def test_action_aliases_map_to_canonical_actions():
    # "double" (live model drift 2026-07-02) -> click with double=true.
    a = conv.parse_actions('{"action":"double","x":10,"y":20}')[0]
    assert a["action"] == "click" and a["double"] is True
    a = conv.parse_actions('{"action":"right_click","x":10,"y":20}')[0]
    assert a["action"] == "click" and a["button"] == "right"
    a = conv.parse_actions('{"action":"type_text","text":"hi"}')[0]
    assert a["action"] == "type" and a["text"] == "hi"
    a = conv.parse_actions('{"action":"hotkey","keys":["ctrl","t"]}')[0]
    assert a["action"] == "key" and a["keys"] == ["ctrl", "t"]
    # An explicit field always beats the alias override.
    a = conv.parse_actions('{"action":"double","x":1,"y":2,"double":false}')[0]
    assert a["action"] == "click" and a["double"] is False


def test_wait_is_capped():
    actions = conv.parse_actions('{"action":"wait","ms":3600000}')
    assert actions[0]["ms"] == conv.MAX_WAIT_MS


def test_batch_is_capped():
    raw = "[" + ",".join(
        '{"action":"key","keys":["tab"]}' for _ in range(12)
    ) + "]"
    assert len(conv.parse_actions(raw)) == conv.MAX_BATCH


@pytest.mark.parametrize("bad", [
    "", "no json here", '{"action":"launch_missiles"}',
    '{"action":"click","x":"abc","y":2}',
    '{"action":"type","text":""}',
    '{"action":"key","keys":[]}',
    '{"action":"scroll","direction":"diagonal"}',
])
def test_invalid_replies_raise(bad):
    with pytest.raises(conv.ActionParseError):
        conv.parse_actions(bad)


# ---------------------------------------------------------------------------
# Idempotency ledger
# ---------------------------------------------------------------------------

def test_click_duplicate_within_tolerance_same_frame():
    ledger = ActionLedger()
    a = {"action": "click", "button": "left", "double": False}
    ledger.record(a, "sha1", resolved_xy=(100, 100))
    assert ledger.is_duplicate(a, "sha1", resolved_xy=(108, 95))
    # Different frame -> the same click is legitimate again.
    assert not ledger.is_duplicate(a, "sha2", resolved_xy=(100, 100))
    # Far-away click on the same frame is a different target.
    assert not ledger.is_duplicate(a, "sha1", resolved_xy=(300, 100))


def test_type_duplicate_is_text_normalized():
    ledger = ActionLedger()
    ledger.record({"action": "type", "text": "Hello World"}, "sha1")
    assert ledger.is_duplicate({"action": "type", "text": "  hello   world "}, "sha1")
    assert not ledger.is_duplicate({"action": "type", "text": "hello world"}, "sha2")
    assert not ledger.is_duplicate({"action": "type", "text": "goodbye"}, "sha1")


def test_open_app_and_key_dedupe_on_same_frame_only():
    ledger = ActionLedger()
    ledger.record({"action": "open_app", "name": "Spotify"}, "sha1")
    assert ledger.is_duplicate({"action": "open_app", "name": "spotify"}, "sha1")
    assert not ledger.is_duplicate({"action": "open_app", "name": "spotify"}, "sha2")
    ledger.record({"action": "key", "keys": ["Enter"]}, "sha1")
    assert ledger.is_duplicate({"action": "key", "keys": ["enter"]}, "sha1")


def test_wait_done_fail_are_exempt():
    ledger = ActionLedger()
    for kind in ("wait", "done", "fail"):
        assert action_key({"action": kind}) is None
        ledger.record({"action": kind}, "sha1")
        assert not ledger.is_duplicate({"action": kind}, "sha1")


# ---------------------------------------------------------------------------
# Pure verification helpers
# ---------------------------------------------------------------------------

def _node(**kw):
    defaults = dict(role="Edit", name="", value="", focused=False,
                    enabled=True, is_password=False)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_typed_text_landed_tristate():
    # A value match is positive evidence regardless of focus.
    nodes = (_node(value="https://example.com"),)
    assert typed_text_landed(nodes, "example.com") is True
    assert typed_text_landed((), "example.com") is None       # nothing editable
    assert typed_text_landed(nodes, "ab") is None              # too short


def test_typed_text_landed_false_needs_focused_editable_evidence():
    # A confirmed MISS requires having looked at the RECEIVING surface: a
    # focused editable that does not hold the text. Editables without focus
    # mean the enumeration covered the wrong surface (start-menu flyout,
    # live incident 2026-07-02 18:00) — that is "cannot tell", never False.
    unfocused = (_node(value="something else"),)
    assert typed_text_landed(unfocused, "spotify") is None
    focused = (_node(value="something else", focused=True),)
    assert typed_text_landed(focused, "spotify") is False


def test_element_is_focused_positive_only():
    nodes = (_node(role="Button", name="Save", focused=True),)
    assert element_is_focused(nodes, "Save") is True
    assert element_is_focused(nodes, "Cancel") is None         # never False


def test_click_point_in_focused_element_tristate():
    focused_bar = _node(role="Edit", name="Address",
                        focused=True, bounds=(100, 40, 800, 36))
    other = _node(role="Button", name="Go", bounds=(950, 40, 60, 36))
    nodes = (other, focused_bar)
    assert click_point_in_focused_element(nodes, 400, 58) is True
    assert click_point_in_focused_element(nodes, 970, 58) is False
    assert click_point_in_focused_element((), 400, 58) is None
    # Broken bounds never crash and never count as a hit.
    broken = (_node(focused=True, bounds=None),)
    assert click_point_in_focused_element(broken, 400, 58) is False


def test_focused_container_is_never_click_evidence():
    # Chrome reports keyboard focus on its top-level WINDOW node — accepting
    # that would rescue EVERY in-window miss and neuter the effect-check.
    window = _node(role="Window", name="Browser",
                   focused=True, bounds=(0, 0, 2560, 1400))
    assert click_point_in_focused_element((window,), 400, 58) is False
    # A real focused control inside the focused window still qualifies.
    bar = _node(role="Edit", name="Address",
                focused=True, bounds=(100, 40, 800, 36))
    assert click_point_in_focused_element((window, bar), 400, 58) is True


def test_field_values_hint_lists_filled_fields_only():
    nodes = (
        _node(name="Address", value="https://example.com"),
        _node(name="Empty", value=""),
        _node(role="Button", name="Go", value="x"),
    )
    hint = field_values_hint(nodes)
    assert "Address" in hint and "example.com" in hint
    assert "Empty" not in hint and "Go" not in hint
    assert field_values_hint(()) == ""


def test_human_handoff_reasons():
    assert human_handoff_reason((_node(role="Text", name="Complete the reCAPTCHA"),)) == "captcha challenge"
    assert human_handoff_reason((_node(role="Text", name="Enter the verification code"),)) == "two-factor / one-time code"
    assert human_handoff_reason((_node(role="Edit", name="Password"),)) == "login / password entry"
    assert human_handoff_reason((_node(role="Edit", name="", is_password=True),)) == "login / password entry"
    # A "Change password" BUTTON alone must not trip the handoff.
    assert human_handoff_reason((_node(role="Button", name="Change password"),)) is None
    assert human_handoff_reason(()) is None


def test_clickable_labels_filters_roles_and_dedupes():
    nodes = (
        _node(role="Button", name="OK"),
        _node(role="Button", name="OK"),
        _node(role="Slider", name="Volume"),
        _node(role="Button", name="", enabled=True),
        _node(role="MenuItem", name="File"),
        _node(role="Button", name="Disabled", enabled=False),
    )
    assert clickable_labels(nodes) == ["OK", "File"]


def test_snap_point_to_element_smallest_containing_rect_wins():
    from jarvis.cu.verify import snap_point_to_element

    clickables = [
        ("Panel", "Button", (0, 0, 800, 600)),      # container-sized: capped
        ("Row", "ListItem", (100, 100, 400, 40)),
        ("Star", "Button", (480, 110, 24, 24)),     # smallest leaf
    ]
    # Point inside BOTH the row and the star -> the star's center wins.
    hit = snap_point_to_element(490, 120, clickables, capture_area=1920 * 1080)
    assert hit == (492, 122, "Star")
    # Point only inside the row -> row center.
    hit = snap_point_to_element(150, 120, clickables, capture_area=1920 * 1080)
    assert hit == (300, 120, "Row")
    # Point in dead space -> no snap.
    assert snap_point_to_element(700, 500, clickables, capture_area=1920 * 1080) is None
    # Container trap: the panel is > 15% of a small capture -> never snaps.
    assert snap_point_to_element(700, 500, [("Panel", "Button", (0, 0, 800, 600))],
                                 capture_area=800 * 600) is None


def test_clickable_rects_keeps_nameless_elements_and_valid_bounds():
    from jarvis.cu.verify import clickable_rects

    nodes = (
        _node(role="Button", name="", enabled=True, bounds=(10, 10, 30, 20)),
        _node(role="Button", name="Zero", bounds=(0, 0, 0, 0)),      # degenerate
        _node(role="Slider", name="Vol", bounds=(5, 5, 50, 10)),     # not clickable role
        _node(role="MenuItem", name="File", bounds=(50, 0, 40, 18)),
        _node(role="Button", name="Off", enabled=False, bounds=(1, 1, 5, 5)),
    )
    rects = clickable_rects(nodes)
    assert ("", "Button", (10, 10, 30, 20)) in rects
    assert ("File", "MenuItem", (50, 0, 40, 18)) in rects
    assert all(name != "Zero" for name, _, _ in rects)
    assert all(name != "Off" for name, _, _ in rects)


def test_regions_equal_tristate():
    a = ((4, 4), b"\x01" * 48)
    b = ((4, 4), b"\x01" * 48)
    c = ((4, 4), b"\x02" * 48)
    assert regions_equal(a, b) is True
    assert regions_equal(a, c) is False
    assert regions_equal(None, b) is None
    assert regions_equal(a, None) is None


def test_crop_raw_scales_points_to_grab_pixels():
    pytest.importorskip("PIL", reason="pillow required")
    # Retina-style: rect is 100x50 points, grab is 200x100 pixels.
    rgb = bytearray(200 * 100 * 3)
    # Paint a white pixel at grab position (100, 50) = point (50, 25).
    idx = (50 * 200 + 100) * 3
    rgb[idx:idx + 3] = b"\xff\xff\xff"
    raw = ((200, 100), bytes(rgb))
    crop = crop_raw(raw, (0, 0, 100, 50), 50, 25, radius=2)
    assert crop is not None
    (w, h), data = crop
    assert w >= 4 and h >= 4          # radius scaled 2x -> >= 4px square
    assert b"\xff\xff\xff" in data     # the white pixel is inside the crop
    # A point outside the rect -> None.
    assert crop_raw(raw, (0, 0, 100, 50), 999, 999, radius=2) is None
