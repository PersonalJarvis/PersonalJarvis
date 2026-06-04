"""Tests for the Set-of-Marks renderer (jarvis/vision/set_of_marks.py)."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

from jarvis.core.protocols import UIANode
from jarvis.vision.set_of_marks import Mark, render_set_of_marks

pytest.importorskip("PIL")


def _nodes() -> tuple[UIANode, ...]:
    return (
        UIANode(role="Button", name="Send", automation_id="send_btn",
                bounds=(100, 200, 80, 30), enabled=True),
        UIANode(role="Edit", name="Message", automation_id="msg_input",
                bounds=(100, 100, 300, 24), enabled=True),
        UIANode(role="Button", name="Disabled", automation_id="x",
                bounds=(0, 0, 50, 20), enabled=False),       # excluded: disabled
        UIANode(role="Text", name="Zero", automation_id="",
                bounds=(10, 10, 0, 0), enabled=True),         # excluded: zero area
        UIANode(role="Pane", name="Container", automation_id="",
                bounds=(0, 0, 500, 500), enabled=True),       # excluded: non-interactable role, no id
    )


def _make_png(tmp_path, w: int = 640, h: int = 480) -> str:
    from PIL import Image
    p = os.path.join(str(tmp_path), "shot.png")
    Image.new("RGB", (w, h), (20, 20, 20)).save(p)
    return p


def test_marks_select_only_interactable_nodes(tmp_path) -> None:
    shot = _make_png(tmp_path)
    res = render_set_of_marks(shot, _nodes(), viewport_origin=(0, 0), scale=1.0)
    # Only Send + Message survive the filter, numbered 1..2 in tree order.
    assert [m.index for m in res.marks] == [1, 2]
    assert res.marks[0].name == "Send"
    assert res.marks[1].name == "Message"


def test_center_screen_is_exact_click_point(tmp_path) -> None:
    shot = _make_png(tmp_path)
    res = render_set_of_marks(shot, _nodes(), viewport_origin=(0, 0), scale=1.0)
    send = res.mark_by_index(1)
    assert isinstance(send, Mark)
    # center = (x + w//2, y + h//2) in absolute screen coords
    assert send.center_screen == (100 + 40, 200 + 15)


def test_legend_lists_indices_and_names(tmp_path) -> None:
    shot = _make_png(tmp_path)
    res = render_set_of_marks(shot, _nodes(), viewport_origin=(0, 0), scale=1.0)
    assert "[1] Button 'Send'" in res.legend_text
    assert "id=send_btn" in res.legend_text
    assert "[2] Edit 'Message'" in res.legend_text


def test_annotated_image_is_written_and_differs(tmp_path) -> None:
    from PIL import Image
    shot = _make_png(tmp_path)
    res = render_set_of_marks(shot, _nodes(), viewport_origin=(0, 0), scale=1.0)
    assert res.annotated_path is not None
    assert os.path.exists(res.annotated_path)
    # The box for "Send" is red around (100,200); sample a pixel on its top edge.
    img = Image.open(res.annotated_path).convert("RGB")
    # Scan the top border row of the Send box for a reddish pixel.
    found_red = any(
        img.getpixel((px, 200))[0] > 150 and img.getpixel((px, 200))[1] < 100
        for px in range(100, 181)
    )
    assert found_red, "no red mark drawn where the Send button should be"


def test_legend_only_mode_without_screenshot(tmp_path) -> None:
    res = render_set_of_marks(None, _nodes(), viewport_origin=(0, 0), scale=1.0)
    assert res.annotated_path is None
    assert len(res.marks) == 2  # still usable for click-by-index / click_element


def test_scale_and_origin_mapping(tmp_path) -> None:
    # A secondary-monitor capture: origin offset + 1.5x DPI scale.
    from PIL import Image
    shot = _make_png(tmp_path, w=900, h=600)
    nodes = (UIANode(role="Button", name="Far", automation_id="f",
                     bounds=(1920 + 100, 50, 80, 40), enabled=True),)
    res = render_set_of_marks(shot, nodes, viewport_origin=(1920, 0), scale=1.5)
    # Mark stays in screen coords; the drawn box maps to image space.
    assert res.marks[0].center_screen == (1920 + 140, 70)
    img = Image.open(res.annotated_path).convert("RGB")
    # image_x = (2020 - 1920) * 1.5 = 150 ; verify a red pixel near there at top edge (y=75).
    found = any(
        img.getpixel((px, 75))[0] > 150 and img.getpixel((px, 75))[1] < 100
        for px in range(150, 271)
    )
    assert found
