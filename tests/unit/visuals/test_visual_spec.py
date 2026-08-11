"""Tests for the validation in front of the visualisation renderer.

The spec layer is the only thing standing between model-authored JSON and a
page served inside the app origin, so its job is to be strict and to say why.
Two properties matter beyond "valid input parses":

* every rejection names what to fix, because the message goes back to the model
  as the tool error and a precise one turns a bad call into a corrected retry;
* the caps actually bind — they are the reason "visualise the whole codebase"
  cannot turn into a multi-megabyte document.
"""
from __future__ import annotations

import pytest

from jarvis.visuals.spec import (
    MAX_DEPTH,
    MAX_ITEMS,
    MAX_LABEL_CHARS,
    VISUAL_KINDS,
    VisualSpecError,
    parse_spec,
)


def _flow(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "How a turn is answered",
        "kind": "flow",
        "items": [{"label": "Listen"}, {"label": "Decide"}, {"label": "Answer"}],
    }
    payload.update(overrides)
    return payload


def test_a_plain_spec_parses() -> None:
    spec = parse_spec(_flow(caption="Three steps."), source_utterance="visualisier das")
    assert spec.kind == "flow"
    assert [item.label for item in spec.items] == ["Listen", "Decide", "Answer"]
    assert spec.caption == "Three steps."
    assert spec.source_utterance == "visualisier das"


def test_a_bare_string_is_a_valid_item() -> None:
    """The cheapest thing a model can emit still describes the picture."""
    spec = parse_spec(_flow(items=["Listen", "Decide"]))
    assert [item.label for item in spec.items] == ["Listen", "Decide"]


def test_whitespace_is_collapsed() -> None:
    spec = parse_spec(_flow(title="  How   a\n turn  is answered "))
    assert spec.title == "How a turn is answered"


@pytest.mark.parametrize("kind", VISUAL_KINDS)
def test_every_declared_kind_is_accepted(kind: str) -> None:
    """VISUAL_KINDS is the contract the tool schema advertises."""
    items = [{"label": "A", "value": 1}, {"label": "B", "value": 2}]
    assert parse_spec({"title": "T", "kind": kind, "items": items}).kind == kind


def test_an_unknown_kind_names_the_valid_ones() -> None:
    with pytest.raises(VisualSpecError) as excinfo:
        parse_spec(_flow(kind="piechart"))
    message = str(excinfo.value)
    assert "piechart" in message
    for kind in VISUAL_KINDS:
        assert kind in message


def test_a_missing_title_is_rejected() -> None:
    with pytest.raises(VisualSpecError, match="title"):
        parse_spec(_flow(title="   "))


def test_an_empty_item_list_is_rejected() -> None:
    with pytest.raises(VisualSpecError, match="at least one"):
        parse_spec(_flow(items=[]))


def test_a_missing_label_is_rejected_with_its_position() -> None:
    with pytest.raises(VisualSpecError, match=r"items'\[1\]\.label"):
        parse_spec(_flow(items=[{"label": "Listen"}, {"detail": "no label"}]))


def test_too_many_items_is_rejected_not_truncated() -> None:
    """Silently dropping items would draw a picture that is quietly wrong."""
    with pytest.raises(VisualSpecError, match=str(MAX_ITEMS)):
        parse_spec(_flow(items=[{"label": f"Step {n}"} for n in range(MAX_ITEMS + 1)]))


def test_an_over_long_label_is_clipped_rather_than_rejected() -> None:
    """The model described the right picture; it just wrote too much."""
    spec = parse_spec(_flow(items=[{"label": "x" * (MAX_LABEL_CHARS + 50)}]))
    assert len(spec.items[0].label) == MAX_LABEL_CHARS
    assert spec.items[0].label.endswith("…")


def test_nesting_is_capped() -> None:
    deepest: dict[str, object] = {"label": "leaf"}
    for level in range(MAX_DEPTH + 1):
        deepest = {"label": f"level {level}", "children": [deepest]}
    with pytest.raises(VisualSpecError, match=str(MAX_DEPTH)):
        parse_spec({"title": "T", "kind": "hierarchy", "items": [deepest]})


def test_nesting_within_the_cap_is_kept() -> None:
    spec = parse_spec(
        {
            "title": "T",
            "kind": "hierarchy",
            "items": [{"label": "Brain", "children": [{"label": "Router"}]}],
        }
    )
    assert spec.items[0].children[0].label == "Router"


def test_bars_without_a_single_number_are_rejected() -> None:
    """A bar chart with no numbers is a bullet list wearing a costume."""
    with pytest.raises(VisualSpecError, match="comparison"):
        parse_spec({"title": "T", "kind": "bars", "items": [{"label": "A"}]})


def test_a_non_numeric_bar_value_is_rejected() -> None:
    with pytest.raises(VisualSpecError, match="value"):
        parse_spec({"title": "T", "kind": "bars", "items": [{"label": "A", "value": "lots"}]})


def test_an_extra_field_for_the_wrong_kind_is_ignored_not_rejected() -> None:
    """A field too many still describes the picture correctly."""
    spec = parse_spec(_flow(items=[{"label": "Listen", "value": 7}]))
    assert spec.items[0].value == 7
