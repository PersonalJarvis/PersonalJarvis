"""The ``visualize`` tool is offered only on a turn that asked for a picture.

Maintainer mandate (2026-08-11): a visualisation is something the user asks
for — "visualisier mir das", "zeig mir das bildlich" — never something the
assistant decides an answer would benefit from. Prompt wording does not hold
that line reliably, so the enforcement is structural: on any other turn the
tool is removed from the surface and the model cannot call what it cannot see.

This pins the gate's placement in ``BrainManager``. The vocabulary itself is
tested in ``test_visualize_gate.py``; here the questions are narrower — does
the manager actually strip it, does it leave every other tool alone, and does a
fault in the gate fail OPEN (tools unchanged) rather than blinding the brain.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager


def _mgr() -> BrainManager:
    return BrainManager.__new__(BrainManager)  # bypass heavy __init__


def _surface() -> dict:
    return {
        "visualize": object(),  # the gated one
        "navigate": object(),  # opens the EXISTING gallery — never gated
        "search_web": object(),
        "screenshot": object(),
        "spawn_worker": object(),
    }


def test_an_explicit_request_keeps_the_tool():
    out = _mgr()._hide_visualize_tool_without_request(
        _surface(), "visualisier mir das mal"  # i18n-allow: DE test vocabulary
    )
    assert "visualize" in out


def test_an_ordinary_turn_loses_it():
    out = _mgr()._hide_visualize_tool_without_request(
        _surface(), "was haben wir gerade besprochen"  # i18n-allow: DE test vocabulary
    )
    assert "visualize" not in out


def test_nothing_else_is_touched():
    """The gate owns exactly one name."""
    out = _mgr()._hide_visualize_tool_without_request(_surface(), "wie spät ist es")
    assert set(out) == {"navigate", "search_web", "screenshot", "spawn_worker"}


def test_opening_the_gallery_does_not_draw_a_new_picture():
    """"Zeig mir die Visualisierungen" is navigate's turn, not visualize's.

    The regression that makes this worth its own test: both features answer to
    the same word, and a visualize tool left on the surface here would produce
    a brand-new picture instead of showing the ones already there.
    """
    out = _mgr()._hide_visualize_tool_without_request(
        _surface(), "zeig mir die visualisierungen"  # i18n-allow: DE test vocabulary
    )
    assert "visualize" not in out
    assert "navigate" in out


def test_a_surface_without_the_tool_is_returned_untouched():
    surface = {"search_web": object()}
    assert _mgr()._hide_visualize_tool_without_request(surface, "hallo") is surface


def test_a_gate_fault_fails_open(monkeypatch):
    """A gate bug must never cost the brain a tool it was supposed to have."""
    import jarvis.brain.visualize_gate as gate

    def _boom(_text: str) -> bool:
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(gate, "wants_visualization", _boom)
    out = _mgr()._hide_visualize_tool_without_request(_surface(), "anything")
    assert "visualize" in out


def test_a_non_dict_surface_is_passed_through():
    sentinel = ["not", "a", "dict"]
    assert _mgr()._hide_visualize_tool_without_request(sentinel, "visualisier das") is sentinel
