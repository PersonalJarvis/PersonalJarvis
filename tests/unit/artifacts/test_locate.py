"""Finding an existing artifact for a revision — by title, or the newest one.

The archive layout is the real one (``<run>/tasks/<id>/artifacts/files/``), so
the test writes that shape into a temp root rather than faking the walker.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from jarvis.artifacts.locate import locate_artifact


def _page(root: Path, run: str, name: str, title: str, *, age_s: float = 0.0) -> Path:
    directory = root / run / "tasks" / "019e0001" / "artifacts" / "files"
    directory.mkdir(parents=True, exist_ok=True)
    file = directory / name
    file.write_text(
        f"<!doctype html><html><head><title>{title}</title></head><body>x</body></html>",
        encoding="utf-8",
    )
    stamp = time.time() - age_s
    os.utime(file, (stamp, stamp))
    os.utime(root / run, (stamp, stamp))
    return file


def test_latest_is_the_newest_page_across_runs(tmp_path: Path) -> None:
    _page(tmp_path, "mission_old", "old.html", "Old dashboard", age_s=600)
    newest = _page(tmp_path, "mission_new", "new.html", "New dashboard", age_s=10)
    found = locate_artifact("latest", outputs_roots=(tmp_path,))
    assert found is not None
    assert found.file == newest
    assert found.slug == "mission_new"
    assert found.artifact_path == "tasks/019e0001/artifacts/files/new.html"
    assert found.title == "New dashboard"
    assert "<title>New dashboard</title>" in found.html


def test_a_title_fragment_matches_case_and_punctuation_insensitively(tmp_path: Path) -> None:
    _page(tmp_path, "mission_a", "plans.html", "Plan comparison — Q3", age_s=100)
    _page(tmp_path, "mission_b", "roster.html", "Team roster", age_s=50)
    found = locate_artifact("PLAN COMPARISON", outputs_roots=(tmp_path,))
    assert found is not None and found.title == "Plan comparison — Q3"
    by_stem = locate_artifact("roster", outputs_roots=(tmp_path,))
    assert by_stem is not None and by_stem.file.name == "roster.html"


def test_no_match_is_none_not_a_guess(tmp_path: Path) -> None:
    _page(tmp_path, "mission_a", "plans.html", "Plan comparison", age_s=100)
    assert locate_artifact("budget forecast", outputs_roots=(tmp_path,)) is None
    assert locate_artifact("", outputs_roots=(tmp_path,)) is None
    assert locate_artifact("latest", outputs_roots=(tmp_path / "missing",)) is None


def test_only_html_deliverables_count(tmp_path: Path) -> None:
    directory = tmp_path / "mission_a" / "tasks" / "019e0001" / "artifacts" / "files"
    directory.mkdir(parents=True)
    (directory / "notes.md").write_text("# not a page", encoding="utf-8")
    assert locate_artifact("latest", outputs_roots=(tmp_path,)) is None
