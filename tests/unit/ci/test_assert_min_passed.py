"""Fail-closed tests for the JUnit minimum-passed gate."""

from __future__ import annotations

from pathlib import Path

from scripts.ci import assert_min_passed


def _write_report(path: Path, *, tests: int, failures: int = 0, errors: int = 0) -> None:
    path.write_text(
        f'<testsuites><testsuite tests="{tests}" failures="{failures}" '
        f'errors="{errors}" skipped="0" /></testsuites>',
        encoding="utf-8",
    )


def test_strict_flag_accepts_report_after_option(tmp_path: Path, monkeypatch) -> None:
    report = tmp_path / "report.xml"
    _write_report(report, tests=2)
    monkeypatch.setattr(assert_min_passed, "FLOOR", 1)

    assert assert_min_passed.main(["gate", "--strict", str(report)]) == 0


def test_strict_flag_blocks_behavioral_failure(tmp_path: Path, monkeypatch) -> None:
    report = tmp_path / "report.xml"
    _write_report(report, tests=2, failures=1)
    monkeypatch.setattr(assert_min_passed, "FLOOR", 1)

    assert assert_min_passed.main(["gate", "--strict", str(report)]) == 1
