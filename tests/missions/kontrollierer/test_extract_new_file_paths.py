"""Tests for ``_extract_new_file_paths_from_diff`` — recover deliverable
filenames from a captured unified diff so ``artifacts/files/`` is populated.
"""
from __future__ import annotations

from jarvis.missions.kontrollierer.orchestrator import (
    _extract_new_file_paths_from_diff,
)


def test_empty_diff_returns_empty_list() -> None:
    assert _extract_new_file_paths_from_diff("") == []


def test_single_new_file() -> None:
    diff = (
        "diff --git a/test.html b/test.html\n"
        "new file mode 100644\n"
        "index 0000000..159c84f\n"
        "--- /dev/null\n"
        "+++ b/test.html\n"
    )
    assert _extract_new_file_paths_from_diff(diff) == ["test.html"]


def test_multiple_new_files_preserves_order() -> None:
    diff = (
        "diff --git a/a.html b/a.html\nnew file mode 100644\n"
        "diff --git a/b.md b/b.md\nnew file mode 100644\n"
    )
    assert _extract_new_file_paths_from_diff(diff) == ["a.html", "b.md"]


def test_modified_file_is_not_counted() -> None:
    diff = (
        "diff --git a/x.py b/x.py\n"
        "index 1234567..abcdef0 100644\n"
        "--- a/x.py\n+++ b/x.py\n"
    )
    assert _extract_new_file_paths_from_diff(diff) == []


def test_deletion_is_not_counted() -> None:
    diff = (
        "diff --git a/g.txt b/g.txt\n"
        "deleted file mode 100644\n"
    )
    assert _extract_new_file_paths_from_diff(diff) == []


def test_real_live_mission_diff() -> None:
    """The exact diff captured for mission_019e6858-ab9a today."""
    diff = (
        "diff --git a/test.html b/test.html\n"
        "new file mode 100644\n"
        "index 0000000..159c84f\n"
        "--- /dev/null\n"
        "+++ b/test.html\n"
        "@@ -0,0 +1,10 @@\n"
        "+<!DOCTYPE html>\n"
    )
    assert _extract_new_file_paths_from_diff(diff) == ["test.html"]


# --- HIGH finding (2026-05-27 hardening audit): non-ASCII deliverable names
#     dropped because git core.quotepath=true (default) octal-escapes the
#     path in the diff header (ä -> \303\244). The extractor must decode the
#     escape back to the real on-disk name so the artifacts/files/ copy loop
#     can find the file. A German/bilingual assistant produces umlaut
#     filenames routinely (Werbungä.html, Lebenslauf-Müller.pdf).


def test_octal_escaped_umlaut_path_is_decoded() -> None:
    """git quotepath=true emits `"a/Werbung\\303\\244.html"`; the extractor
    must return the decoded UTF-8 name `Werbungä.html`, not the literal
    backslash-octal string."""
    diff = (
        'diff --git "a/Werbung\\303\\244.html" "b/Werbung\\303\\244.html"\n'
        "new file mode 100644\n"
        "index 0000000..159c84f\n"
        "--- /dev/null\n"
        '+++ "b/Werbung\\303\\244.html"\n'
    )
    assert _extract_new_file_paths_from_diff(diff) == ["Werbungä.html"]


def test_octal_escaped_path_with_subdir_is_decoded() -> None:
    """Multi-segment umlaut path round-trips (Lebenslauf-Müller in a subdir)."""
    diff = (
        'diff --git "a/out/Lebenslauf-M\\303\\274ller.pdf"'
        ' "b/out/Lebenslauf-M\\303\\274ller.pdf"\n'
        "new file mode 100644\n"
    )
    assert _extract_new_file_paths_from_diff(diff) == [
        "out/Lebenslauf-Müller.pdf"
    ]


def test_plain_ascii_path_is_unchanged_by_decoder() -> None:
    """Regression guard: the octal decoder must be a no-op for ASCII paths
    that carry no backslash escapes (the common case)."""
    diff = "diff --git a/report.md b/report.md\nnew file mode 100644\n"
    assert _extract_new_file_paths_from_diff(diff) == ["report.md"]
