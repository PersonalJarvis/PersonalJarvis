"""Cross-platform overlay DropTarget seam (files/text dropped on the bar/mascot).

GUI-free tests: the TkDND path parser is pure, and the factory must degrade to a
no-op (never raise) when ``tkinterdnd2`` / the ``tkdnd`` binary is absent — the
web dock still carries the feature on every OS, so the overlay extra is gated.
See docs/superpowers/specs/2026-06-21-dragdrop-files-into-context-design.md.
"""
from __future__ import annotations

from jarvis.overlay.drop_target import (
    NullDropTarget,
    _parse_dnd_files,
    make_drop_target,
)


def test_parse_single_plain_path() -> None:
    assert _parse_dnd_files("C:/Users/a/file.txt") == ["C:/Users/a/file.txt"]


def test_parse_brace_wrapped_path_with_spaces() -> None:
    # TkDND wraps a path containing spaces in braces.
    assert _parse_dnd_files("{C:/Users/a b/file name.txt}") == [
        "C:/Users/a b/file name.txt"
    ]


def test_parse_mixed_multiple_paths() -> None:
    data = "{C:/a b/one.txt} C:/c/two.png {D:/x y/three.pdf}"
    assert _parse_dnd_files(data) == [
        "C:/a b/one.txt",
        "C:/c/two.png",
        "D:/x y/three.pdf",
    ]


def test_parse_empty_is_empty_list() -> None:
    assert _parse_dnd_files("") == []
    assert _parse_dnd_files("   ") == []


def test_factory_returns_a_drop_target_and_never_raises() -> None:
    dt = make_drop_target()
    assert hasattr(dt, "register")


def test_null_drop_target_register_is_a_safe_noop() -> None:
    null = NullDropTarget()
    # Registering against a dummy "widget" must not raise and reports no-op.
    assert null.register(object(), lambda paths, text: None) is False
