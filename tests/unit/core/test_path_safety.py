"""Tests for jarvis.core.path_safety containment helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.path_safety import UnsafePathError, contained_path, is_safe_name, safe_child


@pytest.mark.parametrize("name", ["a.md", "u0123abcd.webp", "My File-1_x.json", "figure-ab12.glb"])
def test_plain_names_pass(name: str) -> None:
    assert is_safe_name(name)


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", ".hidden", "../x", "..\\x", "a/b", "a\\b", "C:x", "/etc/passwd", "a\nb"],
)
def test_unsafe_names_rejected(tmp_path: Path, name: str) -> None:
    assert not is_safe_name(name)
    with pytest.raises(UnsafePathError):
        safe_child(tmp_path, name)


def test_safe_child_stays_inside(tmp_path: Path) -> None:
    target = safe_child(tmp_path, "note.md")
    assert target.parent == tmp_path.resolve()


def test_contained_path_allows_nested(tmp_path: Path) -> None:
    assert contained_path(tmp_path, "a/b/c.txt") == (tmp_path / "a" / "b" / "c.txt").resolve()


@pytest.mark.parametrize("rel", ["../escape.txt", "a/../../escape.txt"])
def test_contained_path_rejects_traversal(tmp_path: Path, rel: str) -> None:
    with pytest.raises(UnsafePathError):
        contained_path(tmp_path / "root", rel)


def test_contained_path_rejects_absolute_elsewhere(tmp_path: Path) -> None:
    (tmp_path / "root").mkdir()
    with pytest.raises(UnsafePathError):
        contained_path(tmp_path / "root", str(tmp_path / "other.txt"))


def test_contained_path_rejects_sibling_prefix(tmp_path: Path) -> None:
    (tmp_path / "root").mkdir()
    with pytest.raises(UnsafePathError):
        contained_path(tmp_path / "root", str(tmp_path / "root-evil" / "x"))
