"""Tests for the public fallback documentation privacy scanner."""

from __future__ import annotations

import sys
from pathlib import Path

from scripts.ci import docs_privacy_scan


def test_generic_rules_block_windows_macos_and_linux_home_paths() -> None:
    rules, emails = docs_privacy_scan._generic_manifest()

    for value in (
        r"C:\Users\alice\Documents\private.md",
        "/Users/alice/Documents/private.md",
        "/home/alice/Documents/private.md",
    ):
        assert docs_privacy_scan.scan_text(value, rules, emails)


def test_generic_rules_allow_neutral_home_placeholders() -> None:
    rules, emails = docs_privacy_scan._generic_manifest()

    for value in (
        r"C:\Users\<username>\Documents\example.md",
        "/Users/<username>/Documents/example.md",
        "/home/user/Documents/example.md",
        "`/home/<name>`",
        "/home/<service-user>/state",
    ):
        assert docs_privacy_scan.scan_text(value, rules, emails) == []


def test_generic_rules_do_not_mistake_url_paths_for_home_directories() -> None:
    rules, emails = docs_privacy_scan._generic_manifest()

    assert (
        docs_privacy_scan.scan_text(
            "https://app.example.com/api/v1/users/me",
            rules,
            emails,
        )
        == []
    )


def test_unreadable_document_fails_closed(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    document = tmp_path / "docs" / "invalid.md"
    document.parent.mkdir()
    document.write_bytes(b"\xff\xfe")
    monkeypatch.setattr(sys, "argv", ["docs_privacy_scan.py", str(document)])

    assert docs_privacy_scan.main() == 1
    assert "unreadable documentation file" in capsys.readouterr().err
