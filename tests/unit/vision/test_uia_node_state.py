"""Accessibility state on the UIA node model (audit foundation for #5/#16/#1B).

UIANode/RawNode now carry ``is_password`` (a secure/password edit -> redact before
upload, never read its value) and ``focused`` (holds keyboard focus -> a
click_element that focuses a field is verifiable post-hoc). These pin the
defaults, the RawNode->UIANode copy, and the best-effort populate in the Windows
traversal (no real UIA needed — a fake element exposes the COM-style properties).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from jarvis.core.protocols import UIANode
from jarvis.vision.pruning import RawNode
from jarvis.vision.uia_tree import UIATreeSource, _flatten


# --- defaults + copy ---------------------------------------------------------


def test_uianode_state_defaults_false():
    n = UIANode(role="Edit", name="x")
    assert n.is_password is False
    assert n.focused is False


def test_to_uia_nodes_copies_state():
    raw = [
        RawNode(role="Edit", name="pw", is_password=True, focused=True),
        RawNode(role="Button", name="ok"),
    ]
    nodes = UIATreeSource._to_uia_nodes(raw)
    assert nodes[0].is_password is True
    assert nodes[0].focused is True
    assert nodes[1].is_password is False
    assert nodes[1].focused is False


# --- best-effort populate in the Windows traversal ---------------------------


def _fake_element(*, is_password: Any = False, focused: Any = False,
                  with_element: bool = True) -> Any:
    rect = SimpleNamespace(left=10, top=20, right=110, bottom=60)
    raw_el = (
        SimpleNamespace(CurrentIsPassword=is_password, CurrentHasKeyboardFocus=focused)
        if with_element else None
    )
    return SimpleNamespace(
        control_type="Edit", name="field", automation_id="f1",
        rectangle=rect, enabled=True, is_offscreen=False,
        iface_value=None, element=raw_el,
    )


def test_flatten_populates_password_and_focus():
    out: list[RawNode] = []
    _flatten(_fake_element(is_password=True, focused=True),
             depth=0, max_depth=0, parent_index=-1, out=out)
    assert len(out) == 1
    assert out[0].is_password is True
    assert out[0].focused is True


def test_flatten_defaults_false_for_plain_control():
    out: list[RawNode] = []
    _flatten(_fake_element(is_password=False, focused=False),
             depth=0, max_depth=0, parent_index=-1, out=out)
    assert out[0].is_password is False
    assert out[0].focused is False


def test_flatten_defaults_false_when_no_underlying_element():
    out: list[RawNode] = []
    _flatten(_fake_element(with_element=False),
             depth=0, max_depth=0, parent_index=-1, out=out)
    assert out[0].is_password is False
    assert out[0].focused is False


def test_flatten_state_read_never_skips_node_on_error():
    # A COM access that raises must not drop the node — it just defaults to False.
    class _Boom:
        @property
        def CurrentIsPassword(self):  # noqa: N802 — mirror the COM property name
            raise OSError("COM gone")

    el = _fake_element()
    el.element = _Boom()
    out: list[RawNode] = []
    _flatten(el, depth=0, max_depth=0, parent_index=-1, out=out)
    assert len(out) == 1
    assert out[0].is_password is False
