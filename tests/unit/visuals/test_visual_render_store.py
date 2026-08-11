"""Tests for the visualisation renderer and its place in the run archive.

The renderer's hard constraint is the serving CSP: the page is delivered inside
the app origin with ``default-src 'none'``, so a document that reaches for
JavaScript, a font, or a remote image renders broken — and one that echoes a
model-authored label unescaped is an injection into the app's own origin. Both
are pinned here rather than left to review.

The store's hard constraint is the read side: two path conventions that
``outputs_routes`` enforces and this module has to match exactly, or the
picture is written successfully and is invisible forever.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarvis.visuals.render import render_visual_html
from jarvis.visuals.spec import VISUAL_KINDS, parse_spec
from jarvis.visuals.store import TASK_DIR_NAME, store_visual


def _spec(kind: str = "flow"):
    return parse_spec(
        {
            "title": "How a turn is answered",
            "kind": kind,
            "items": [
                {"label": "Listen", "detail": "Speech becomes text.", "value": 3},
                {"label": "Decide", "detail": "The router picks a tool.", "value": 8},
            ],
            "caption": "Drawn on request.",
        },
        source_utterance="visualisier mir den ablauf",
    )


# --- Renderer ----------------------------------------------------------------


@pytest.mark.parametrize("kind", VISUAL_KINDS)
def test_every_kind_renders_a_complete_document(kind: str) -> None:
    """One branch per declared kind — a new kind without a branch fails here."""
    html = render_visual_html(_spec(kind))
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert "How a turn is answered" in html
    assert "Listen" in html


@pytest.mark.parametrize("kind", VISUAL_KINDS)
def test_no_kind_reaches_for_anything_the_csp_forbids(kind: str) -> None:
    """default-src 'none': no script, no network, no external asset."""
    html = render_visual_html(_spec(kind)).lower()
    for forbidden in ("<script", "javascript:", "http://", "https://", "@import", "<img"):
        assert forbidden not in html, forbidden


def test_model_authored_text_is_escaped() -> None:
    """A label is untrusted input rendered into the app's own origin."""
    spec = parse_spec(
        {
            "title": "<script>alert(1)</script>",
            "kind": "comparison",
            "items": [{"label": "a\"b<c>", "detail": "<b>bold</b>"}],
        }
    )
    html = render_visual_html(spec)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>bold</b>" not in html


def test_bar_widths_are_proportional_from_zero() -> None:
    """A bar axis that starts somewhere convenient overstates every difference."""
    spec = parse_spec(
        {
            "title": "Runs per day",
            "kind": "bars",
            "items": [{"label": "Mon", "value": 5}, {"label": "Tue", "value": 10}],
        }
    )
    html = render_visual_html(spec)
    widths = [float(w) for w in re.findall(r'bar-fill" style="width:([\d.]+)%', html)]
    assert widths == [50.0, 100.0]


def test_a_bar_without_a_value_renders_as_an_honest_gap() -> None:
    spec = parse_spec(
        {
            "title": "Runs per day",
            "kind": "bars",
            "items": [{"label": "Mon", "value": 5}, {"label": "Tue"}],
        }
    )
    html = render_visual_html(spec)
    assert "—" in html


def test_rendering_is_deterministic() -> None:
    """No clock, no randomness — which is what makes the archive diffable."""
    assert render_visual_html(_spec()) == render_visual_html(_spec())


def test_the_utterance_is_shown_but_the_caption_owns_the_footer() -> None:
    html = render_visual_html(_spec())
    assert "visualisier mir den ablauf" in html
    assert "Drawn on request." in html


# --- Store -------------------------------------------------------------------


def test_the_file_lands_where_the_gallery_looks(tmp_path: Path) -> None:
    """Both conventions at once: the run slug and the deliverable subtree.

    ``outputs_routes`` parses the first and lists only the second; getting
    either wrong writes a picture nobody can ever see.
    """
    stored = store_visual(
        "<!doctype html><html></html>",
        title="How a turn is answered",
        utterance="visualisier mir den ablauf",
        outputs_root=tmp_path,
    )

    # The slug shape outputs_routes._SLUG_RE parses.
    assert re.fullmatch(r"\d{8}T\d{6}__[a-z0-9-]+__[0-9a-f]{8}", stored.slug), stored.slug
    # The only subtree outputs_routes will list or serve.
    assert stored.artifact_path == (
        f"tasks/{TASK_DIR_NAME}/artifacts/files/how-a-turn-is-answered.html"
    )
    assert stored.file.read_text(encoding="utf-8").startswith("<!doctype html>")
    # The absolute path and the listed path describe the same file.
    assert stored.file == tmp_path.joinpath(stored.slug, *stored.artifact_path.split("/"))


def test_no_partial_file_is_left_behind(tmp_path: Path) -> None:
    """The gallery polls this tree; a half-written page renders as a blank frame."""
    stored = store_visual("<!doctype html><html></html>", title="T", outputs_root=tmp_path)
    leftovers = [p.name for p in stored.file.parent.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_two_pictures_of_the_same_thing_do_not_collide(tmp_path: Path) -> None:
    first = store_visual("<html>1</html>", title="Same", outputs_root=tmp_path)
    second = store_visual("<html>2</html>", title="Same", outputs_root=tmp_path)
    assert first.slug != second.slug
    assert first.file.read_text(encoding="utf-8") == "<html>1</html>"
    assert second.file.read_text(encoding="utf-8") == "<html>2</html>"


def test_a_title_with_nothing_sluggable_still_produces_a_filename(tmp_path: Path) -> None:
    stored = store_visual("<html></html>", title="???", outputs_root=tmp_path)
    assert stored.file.name == "visualization.html"
